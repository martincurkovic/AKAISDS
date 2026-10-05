"""A fake Akai S900/S950 that speaks real SysEx, for tests and demo mode.

Same idea as `core/demo_s1000.py`: it fakes the MIDI PORTS, not any Python
API. `FakeS950.out`/`.inp` have the rtmidi surface (`send_message(frame)` /
`get_message()`, frames INCLUDING the F0/F7) so whatever transport code ends
up driving an S950 can run against it with no hardware. Everything it says
and accepts is built and parsed with `core.s950_sysex` / `core.s950_program`,
so a wire-format bug there shows up as a failed conversation here.

Supported: RCAT, RSPRM/SPRM, RPRGM/PRGM, RSD and an incoming sample dump.
Anything else (drum/overall settings, SECRE/SECRD, standard-SDS requests...)
is answered with silence like the real unit and lands in `ignored_ops`.
Not modelled at all: delete (there is no opcode), timing (the fake has no
clock, but a real unit is slower and stays busy after an upload - s950tools
uses an RCAT reply as its "device ready" barrier for that), and the
front-panel "active edit buffer" that SPRM writes don't refresh.

Behaviour that comes from s950tools' hardware-verified comments:
 - the 4-byte handshake (F0 7E code F7) is the only ACK it understands; the
   6-byte standard SDS ACK is silently ignored, so a dump stays stalled
 - a dump the fake SENDS is one SysEx (header + all blocks + one F7), held
   back until it has been ACKed (see `dump_acks_required`)
 - it streams one ACK per block it RECEIVES during an upload, so an ACK left
   over from an upload can satisfy a naive "got any reply" ping
 - overwriting a sample slot keeps stale start/end/loop values
   (`stale_params_on_overwrite`)
 - no reply to SPRM / PRGM writes

Behaviour that is GUESSED (nothing measured - the first real S950 report
will confirm or refute each): the order of the catalog (programs first),
the default name of an uploaded sample (the dump carries none), a NAK for an
RSD of an empty slot (s950tools handles one, but not what provokes it),
silence for a write to a slot that doesn't exist (SPRM) and for RSPRM/RPRGM
of an empty one, the number of ACKs a dump needs (one per block), that
a NAK'd upload stores nothing, and how the header's loop fields map to
SPRM start/end/loop length.
"""

from collections import deque

from core import s950_program as prog
from core import s950_sysex as s

SOX = 0xF0
EOX = 0xF7
DEFAULT_NAME = "SAMPLE"


def make_sample(name, words=None, *, rate=22050, frames=2000):
    """A resident sample: SPRM params plus its 12-bit audio words.

    With no `words`, a quiet triangle wave `frames` long, so a fixture is
    cheap to make but never silent (silence would hide a dropped block).
    """
    if words is None:
        words = [
            s.SILENCE_WORD + (abs((i % 100) - 50) - 25) * 8 for i in range(frames)
        ]
    params = s.SampleParams(
        name=name,
        total_words=len(words),
        sample_rate_hz=rate,
        end=len(words) - 1,
        start=0,
        replay_mode=s.REPLAY_ONE_SHOT,
    )
    return {"params": params, "words": list(words)}


class _FakeOut:
    # stands in for the rtmidi MidiOut / SharedMidiOutput
    def __init__(self, fake):
        self._fake = fake

    def send_message(self, message, *, write=False):
        self._fake.handle(list(message))

    def close_port(self):
        pass


class _FakeIn:
    # stands in for the rtmidi MidiIn / SharedMidiInput
    def __init__(self, fake):
        self._fake = fake

    def get_message(self):
        if not self._fake._outbox:
            return None
        return (list(self._fake._outbox.popleft()), 0.0)

    def close_port(self):
        pass


class FakeS950:
    def __init__(
        self,
        *,
        samples=None,
        programs=None,
        channel=0,
        dump_acks_required=None,
        stale_params_on_overwrite=True,
    ):
        self.channel = channel
        #: ACK handshakes needed before a requested dump is released. None =
        #: one per block (a guess - s950tools only knows the unit stalls
        #: between blocks without them). 0 = send immediately.
        self.dump_acks_required = dump_acks_required
        #: s950tools: overwriting a slot via a dump "sometimes leaves"
        #: SSTART/SEND/SLOOP stale, so a sender must write SPRM afterwards
        self.stale_params_on_overwrite = stale_params_on_overwrite
        #: slot -> {"params": SampleParams, "words": [int]}
        self.samples = {}
        #: slot -> Program
        self.programs = {}
        self._outbox = deque()
        self.out = _FakeOut(self)
        self.inp = _FakeIn(self)
        self._pending_dump = None  # {"frame": [...], "acks_left": int}

        #: function codes (or "sds:<hex>" for F0 7E ...) of every frame that
        #: was received and not answered because this fake doesn't know it
        self.ignored_ops = []
        #: every frame received, F0/F7 included
        self.received = []
        #: frames dropped for a bad checksum / wrong device byte / structure
        self.rejected = []
        self.sprm_writes = 0
        self.prgm_writes = 0
        #: slots a complete, clean dump was stored into
        self.uploads = []
        #: how many 4-byte ACKs the fake has been given
        self.acks_received = 0

        if samples is None and programs is None:
            self._populate_default()
        else:
            self.samples.update(samples or {})
            self.programs.update(programs or {})

    def _populate_default(self):
        self.samples[0] = make_sample("KICK", rate=22050, frames=3000)
        self.samples[1] = make_sample("SNARE", rate=22050, frames=4000)
        self.samples[2] = make_sample("PAD", rate=44100, frames=9000)
        drums = prog.Program(
            name="DRUMS",
            keygroups=[
                prog.Keygroup(lower_key=24, upper_key=59, soft_sample="KICK"),
                prog.Keygroup(lower_key=60, upper_key=127, soft_sample="SNARE"),
            ],
        )
        self.programs[0] = drums
        self.programs[1] = prog.Program(
            name="PAD PROG", keygroups=[prog.Keygroup(soft_sample="PAD")]
        )

    # -- the S950 side of the wire --------------------------------------------------

    def _send(self, data):
        self._outbox.append([SOX, *data, EOX])

    def _reply_akai(self, function, num, payload):
        self._send(s.build_akai_data(function, num, payload, channel=self.channel))

    def handle(self, frame):
        if not frame or frame[0] != SOX or frame[-1] != EOX:
            return  # plain MIDI, or a fragment - nothing to answer
        self.received.append(frame)
        data = frame[1:-1]
        if not data:
            return
        if data[0] == s.MANUFACTURER_AKAI:
            self._handle_akai(data)
        elif data[0] == s.UNIVERSAL_NRT:
            self._handle_common(data)
        # any other manufacturer: not ours, no answer

    def _handle_akai(self, data):
        # requests are 6 bytes (no checksum); data messages carry one
        if len(data) < 6 or data[3] != s.DEVICE_ID_S950:
            self.rejected.append(data)
            return
        if data[1] & 0x0F != self.channel:
            return
        function, num = data[2] & 0x7F, data[4] & 0x7F
        requests = {
            s.FUNC_RCAT: self._rcat,
            s.FUNC_RSPRM: self._rsprm,
            s.FUNC_RPRGM: self._rprgm,
        }
        writes = {s.FUNC_SPRM: self._sprm, s.FUNC_PRGM: self._prgm}
        if function in requests and len(data) == 6:
            requests[function](num)
        elif function in writes:
            try:
                message = s.parse_akai(data)
            except ValueError:
                self.rejected.append(data)
                return
            writes[function](num, message.payload)
        else:
            self.ignored_ops.append(function)

    def _handle_common(self, data):
        if len(data) == 2:
            code = s.handshake_code(data)
            if code == s.CODE_ACKS:
                self._on_ack()
            elif code == s.CODE_ASD:
                self._pending_dump = None
            elif code is None:
                self.ignored_ops.append(f"sds:{data[1]:02X}")
            return  # a NAK is accepted and, here, has no effect
        if data[1] == s.CODE_RSD and len(data) == 4 and data[3] == 0x00:
            self._rsd(data[2] & 0x7F)
        elif data[1] == s.CODE_SD:
            self._receive_dump(data)
        else:
            # standard-SDS framing (a channel byte, the 6-byte ACK, a request
            # with sub-ID 03...) means nothing to this unit
            self.ignored_ops.append(f"sds:{data[1]:02X}")

    # -- catalog / parameters / programs --------------------------------------------

    def _rcat(self, _num):
        payload = bytearray()
        for kind, table in (("P", self.programs), ("S", self.samples)):
            for slot in sorted(table):
                name = table[slot].name if kind == "P" else table[slot]["params"].name
                payload += bytes([ord(kind), slot])
                payload += name[:10].ljust(10).encode("ascii", "replace")
        self._reply_akai(s.FUNC_CAT, 0, payload)

    def _rsprm(self, slot):
        if slot in self.samples:
            self._reply_akai(s.FUNC_SPRM, slot, self.samples[slot]["params"].to_payload())

    def _sprm(self, slot, payload):
        # no reply. Only a resident sample can take new parameters.
        if slot not in self.samples:
            self.ignored_ops.append(f"sprm-empty-slot:{slot}")
            return
        try:
            self.samples[slot]["params"] = s.SampleParams.from_payload(payload)
        except ValueError:
            self.rejected.append(payload)
            return
        self.sprm_writes += 1

    def _rprgm(self, slot):
        if slot in self.programs:
            self._reply_akai(s.FUNC_PRGM, slot, self.programs[slot].to_payload())

    def _prgm(self, slot, payload):
        # no reply. Writing to a free slot creates the program.
        try:
            self.programs[slot] = prog.Program.from_payload(payload)
        except ValueError:
            self.rejected.append(payload)
            return
        self.prgm_writes += 1

    # -- sample dumps -----------------------------------------------------------------

    def _dump_header(self, params, total):
        # how SPRM start/end/loop map onto the dump header is inferred from
        # s950tools' comments, not measured
        if params.replay_mode == s.REPLAY_ONE_SHOT:
            loop_start, loop_end = max(0, total - 5), max(0, total - 1)
        else:
            loop_end = params.end
            loop_start = max(0, loop_end - params.loop_length)
        return s.SampleDumpHeader(
            num=0,
            period_ns=s.hz_to_period_ns(params.sample_rate_hz),
            total_words=total,
            loop_start=loop_start,
            loop_end=loop_end,
            mode=1 if params.replay_mode == s.REPLAY_ALTERNATING else 0,
        )

    def _rsd(self, slot):
        sample = self.samples.get(slot)
        if sample is None:
            self._send(s.build_handshake(s.CODE_NAKS))
            return
        header = self._dump_header(sample["params"], len(sample["words"]))
        header.num = slot
        frame = [SOX, *s.build_sample_dump(header, sample["words"]), EOX]
        acks = self.dump_acks_required
        if acks is None:
            acks = s.num_blocks(len(sample["words"]))
        if acks <= 0:
            self._outbox.append(frame)
            self._pending_dump = None
        else:
            self._pending_dump = {"frame": frame, "acks_left": acks}

    def _on_ack(self):
        self.acks_received += 1
        pending = self._pending_dump
        if pending is None:
            return
        pending["acks_left"] -= 1
        if pending["acks_left"] <= 0:
            self._outbox.append(pending["frame"])
            self._pending_dump = None

    def _receive_dump(self, data):
        try:
            header = s.SampleDumpHeader.from_bytes(data)
        except ValueError:
            self.rejected.append(data)
            return
        body = data[s.DUMP_HEADER_SIZE :]
        words, clean = [], True
        # one ACK (or NAK) per block, in order, as the unit streams them
        for cursor in range(0, len(body), s.BLOCK_SIZE):
            block = body[cursor : cursor + s.BLOCK_SIZE]
            try:
                words += s.decode_block(block)[1]
                self._send(s.build_handshake(s.CODE_ACKS))
            except ValueError:
                clean = False
                self._send(s.build_handshake(s.CODE_NAKS))
        if not clean or len(words) < header.total_words:
            return  # guess: a dump that NAK'd stores nothing
        self._store_upload(header, words[: header.total_words])

    def _store_upload(self, header, words):
        slot = header.num
        existing = self.samples.get(slot)
        params = s.SampleParams(
            name=f"{DEFAULT_NAME} {slot:02d}",
            total_words=len(words),
            sample_rate_hz=s.period_ns_to_hz(header.period_ns),
            start=0,
            end=header.loop_end,
            loop_length=max(0, header.loop_end - header.loop_start),
            replay_mode=(
                s.REPLAY_ALTERNATING
                if header.mode == 1
                else (
                    s.REPLAY_ONE_SHOT
                    if header.loop_start >= len(words) - 5
                    else s.REPLAY_LOOP
                )
            ),
        )
        if existing is not None:
            # a slot being overwritten keeps its name and, per s950tools,
            # sometimes its stale start/end/loop values
            params.name = existing["params"].name
            if self.stale_params_on_overwrite:
                old = existing["params"]
                params.start, params.end, params.loop_length = (
                    old.start,
                    old.end,
                    old.loop_length,
                )
        self.samples[slot] = {"params": params, "words": list(words)}
        self.uploads.append(slot)
