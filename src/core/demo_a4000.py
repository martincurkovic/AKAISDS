"""A fake Yamaha A4000 that speaks real SysEx, for tests and demo mode.

Same idea as `core/demo_s950.py` / `core/demo_s1000.py`: it fakes the MIDI PORTS, not any Python API.
`FakeA4000.out`/`.inp` have the rtmidi surface (`send_message(frame)` / `get_message()`, frames
INCLUDING F0/F7) so whatever drives an A4000 runs against it with no hardware. Everything it says and
accepts is built and parsed with `core.yamaha_sysex` / `core.yamaha_params`, so a wire-format or
table bug shows up as a failed conversation here.

Seeded from REAL bytes captured on 2026-10-06 (core/demo_a4000_data.py): 128 programs (all cold-boot
defaults), the factory single-cycle waveforms (as wave + sample pairs, like the real object list) and
a program's Easy Edit blocks. `assign()` fills an Easy Edit slot the way the unit did.

MEASURED on the real unit (the behaviours below are copied from it):
 - identity reply; bulk dump request/reply incl. the multi-block framing; the object list layout
 - a select gets NO reply; a parameter request is answered by the unit first ANNOUNCING its current
   object (a select-shaped message), then the value (a change-shaped message)
 - an object edit applies to the last selected object, gets no reply, and sets bit 0 of byte 1 of the
   object's common block (an "edited" flag)
 - rows flagged `write_ignored` in core/yamaha_params.py accept a write and change nothing
 - a Sample Dump Standard request (F0 7E ch 03 nn nn F7) is answered by the sample at POSITION nn in the
   sample list (0-based; the factory waveforms are 0-6 and a sample sent later is 7 - NOT the number it was
   sent as, and not the number in its name), as a standard SDS header + 120-byte packets, each sent after the
   previous one's ACK; a number with no sample gets a CANCEL (7D). The header's rate is rounded the way the
   unit's is (48000 -> 48001 Hz, the period in whole nanoseconds)

WAVE DATA: a wave object (named by its sample's payload @64 / @80) is served as the native "WD" bulk dump - several
complete messages per wave, laid out exactly as the real unit's (core/yamaha_wave.py). A stereo sample (`add_sample(...,
audio_right=...)`) has a right wave object. MEASURED layout; the pacing is not (every message is sent at once).

OBJECT LINK (MEASURED 2026-10-06 with tools/a4000_link_probe.py): an object link change (program upper, sample lower) never gets a
reply; linking APPENDS the sample as the next Easy Edit slot with every value at its default (receive channel -1 = "=sample", the
block's id bytes = the sample's own id + 0x18 - two samples seen), marks the program in the sample's "linked to program" map and sets
the edited flag; unlinking REMOVES that slot and shifts the later ones down (their values kept), clears the map bit; linking twice, or a
sample / program the unit doesn't have, changes nothing; an object link REQUEST is answered with an object link change carrying the
state. (The real unit leaves stale bytes in the vacated block's values; this fake does not model that.)

GUESSED (not measured - the first real conversation that disagrees wins): what a stereo sample or a sample bank
sends over SDS (one mono waveform here), the audio itself (a deterministic test tone unless a test sets
`audio[name]`), what SDS does after a deleted sample (positions shifting is assumed), that a wrong SDS
channel is silence, silence for a dump request
or a select of an object that doesn't exist, silence for a parameter request with no current object
or an unknown P-number, that a wrong device number (or "off") is simply silence, that Bulk Protect
makes edits silently ignored, that sample banks / system parameters / wave data / sequences are not
answered (they land in `ignored_ops`), and that an edit's out-of-range value is stored as sent.
NOT MODELLED: the unit's side effects of an edit (mirrored R bytes, derived EQ coefficients, control
blocks mirrored at the start of a sample's parameters, coupled wave/loop addresses) - an edit changes
exactly the table's bytes here - and timing.
"""

from collections import deque

import math

from core import demo_a4000_data as seed
from core import sds_encoder
from core import yamaha_params as yp
from core import yamaha_sysex as y
from core import yamaha_wave

SOX = 0xF0
EOX = 0xF7
PROGRAM_COUNT = 128
#: the factory waveforms every cold-booted A4000 holds (each is a wave object + a sample object)
FACTORY_SAMPLES = ("sine wave", "saw up", "triangle", "square", "pulse 1", "pulse 2", "pulse 3")


class _FakeOut:
    # stands in for the rtmidi MidiOut / SharedMidiOutput
    def __init__(self, fake):
        self._fake = fake

    def send_message(self, message, *, write=False):
        self._fake.handle(list(message))

    def close_port(self):
        pass


class _FakeIn:
    # stands in for the rtmidi MidiIn: get_message() -> (frame, delta) or None
    def __init__(self, fake):
        self._fake = fake

    def get_message(self):
        if self._fake._outbox:
            return (self._fake._outbox.popleft(), 0.0)
        return None

    def close_port(self):
        pass


def make_program_payload(number, assigned=()):
    """A program payload as a cold-booted unit holds it, optionally with samples assigned (names)."""
    data = bytearray(seed.PROGRAM_HEAD)
    for _ in range(max(seed.PROGRAM_EASY_EDIT_SLOTS, len(assigned))):
        data += seed.EASY_EDIT_EMPTY
    name = y.program_object_name(number)
    data[2:18] = y.pad_name(name)
    yp.store(yp.get("program", "program_name"), data, f"Pgm {name}")
    for slot, sample in enumerate(assigned):
        _assign(data, slot, sample)
    return data


def _assign(data, slot, sample_name):
    start = yp.PROGRAM_EASY_EDIT_BASE + yp.EASY_EDIT_BLOCK_SIZE * slot
    data[start : start + yp.EASY_EDIT_BLOCK_SIZE] = seed.EASY_EDIT_ASSIGNED
    data[start : start + 16] = y.pad_name(sample_name)
    count = yp.extract(yp.get("program", "assigned_samples"), data)
    yp.store(yp.get("program", "assigned_samples"), data, max(count, slot + 1))
    data[1] |= 1  # the unit flags an edited object (measured when a sample was assigned)


def make_sample_payload(name):
    """A sample payload (the built-in sine wave's parameters) under another name."""
    data = bytearray(seed.SAMPLE_PAYLOAD)
    data[2:18] = y.pad_name(name)
    data[64:80] = y.pad_name(name)  # linked wave object name L
    return data


class FakeA4000:
    def __init__(self, *, device=0, programs=None, samples=None, bulk_protect=False, device_number_off=False):
        self.device = device
        #: when True the unit never answers (its Device Number is "off")
        self.device_number_off = device_number_off
        #: when True edits are silently ignored (GUESS - see the module docstring)
        self.bulk_protect = bulk_protect
        #: when True an object select is lost (the previous object stays current) - for testing that nothing is
        #: written to the wrong object; the real unit has not been seen to do this
        self.drop_selects = False
        #: {program number 1..128: bytearray payload}
        self.programs = {n: make_program_payload(n) for n in range(1, PROGRAM_COUNT + 1)}
        #: {sample name: bytearray payload}, in object-list order
        self.samples = {name: make_sample_payload(name) for name in FACTORY_SAMPLES}
        for number, payload in (programs or {}).items():
            self.programs[number] = bytearray(payload)
        for name, payload in (samples or {}).items():
            self.samples[name] = bytearray(payload)
        self.out = _FakeOut(self)
        self.inp = _FakeIn(self)
        self._outbox = deque()
        #: {sample name: list of int16 words} - the audio an SDS dump sends (default: a tone, see `_audio_for`)
        self.audio = {}
        #: {sample name: list of int16 words} - a STEREO sample's right channel (see `add_sample`)
        self.audio_right = {}
        self._sds_packets = []  # the data packets still to send, each after an ACK
        #: the object edits and parameter requests apply to: (type, key) or None
        self.current = None
        #: every frame received, decoded as (label, message-without-F0/F7)
        self.received = []
        #: labels of frames this fake doesn't answer
        self.ignored_ops = []
        #: how many accepted object edits
        self.edits = 0
        self._program_rows = {p.p: p for p in yp.rows("program") if not p.bulk_only}
        self._sample_rows = {p.p: p for p in yp.rows("sample") if not p.bulk_only}
        self._easy_rows = {(p.p[3], p.p[4]): p for p in yp.rows("easy_edit") if not p.bulk_only}

    # -- helpers for tests ------------------------------------------------------------------------

    def assign(self, program, sample_name, slot=None):
        """Put a sample in a program's next free (or the given) Easy Edit slot, like the front panel does."""
        data = self.programs[program]
        if slot is None:
            slot = yp.extract(yp.get("program", "assigned_samples"), data)
        if yp.PROGRAM_EASY_EDIT_BASE + yp.EASY_EDIT_BLOCK_SIZE * (slot + 1) > len(data):
            data += seed.EASY_EDIT_EMPTY
        _assign(data, slot, sample_name)
        if sample_name in self.samples:
            # the unit also marks the program in the sample's "linked to program" map (measured: "sine wave"
            # assigned to program 1 reads back as word0 = 1)
            bits = bytearray(self.samples[sample_name])
            word_at = yp.SAMPLE_PARAMETER_BASE + yp.LINKED_PROGRAMS_OFFSET + 4 * ((program - 1) // 32)
            word = int.from_bytes(bits[word_at : word_at + 4], "big") | (1 << ((program - 1) % 32))
            bits[word_at : word_at + 4] = word.to_bytes(4, "big")
            self.samples[sample_name][:] = bits
        return slot

    def add_sample(self, name, payload=None, *, audio=None, audio_right=None):
        """Add a sample. `audio` (int16 words) sets its length; `audio_right` makes it STEREO (equal length)."""
        data = bytearray(payload) if payload is not None else make_sample_payload(name)
        if audio is not None:
            self.audio[name] = list(audio)
            for key in ("wave_length", "wave_end_address"):
                yp.store(yp.get("sample", key), data, len(audio))
        if audio_right is not None:
            self.audio_right[name] = list(audio_right)
            data[yp.WAVE_NAME_L_OFFSET : yp.WAVE_NAME_L_OFFSET + 16] = y.pad_name(f"{name}-L")
            data[yp.WAVE_NAME_R_OFFSET : yp.WAVE_NAME_R_OFFSET + 16] = y.pad_name(f"{name}-R")
        self.samples[name] = data

    def _wave_names(self, name):
        data = self.samples[name]
        names = [bytes(data[yp.WAVE_NAME_L_OFFSET : yp.WAVE_NAME_L_OFFSET + 16]).decode("ascii").strip(" \x00")]
        if yp.is_stereo(data):
            names.append(yp.wave_name_right(data))
        return names

    def _wave_frames(self, wave_name):
        """The int16 frames of a wave object (by ITS name), or None."""
        for sample in self.samples:
            names = self._wave_names(sample)
            if wave_name in names:
                if names.index(wave_name) == 0:
                    return self._audio_for(sample)
                return list(self.audio_right.get(sample) or [-w for w in self._audio_for(sample)])
        return None

    def object_list(self):
        entries = [bytes([y.OBJECT_TYPES["program"]]) + y.pad_name(y.program_object_name(n)) for n in sorted(self.programs)]
        for name in self.samples:
            for wave in self._wave_names(name):
                entries.append(bytes([y.OBJECT_TYPES["wave"]]) + y.pad_name(wave))
            entries.append(bytes([y.OBJECT_TYPES["sample"]]) + y.pad_name(name))
        return b"".join(entries)

    # -- the wire ---------------------------------------------------------------------------------------

    def _say(self, message):
        self._outbox.append([SOX, *message, EOX])

    def handle(self, frame):
        message = bytes(frame)
        if message[:1] == b"\xf0":
            message = message[1:]
        if message[-1:] == b"\xf7":
            message = message[:-1]
        kind = y.classify(message)
        self.received.append((kind, message))
        if len(message) >= 4 and message[0] == 0x7E and message[2:4] == b"\x06\x01":
            self._identity()
        elif len(message) >= 4 and message[0] == 0x7E:
            self._sds(message)
        elif message[:1] == b"\x43" and len(message) >= 4:
            if self.device_number_off or (message[1] & 0x0F) != self.device:
                self.ignored_ops.append("wrong device number")
                return
            self._yamaha(message)
        else:
            self.ignored_ops.append(kind)

    # -- Sample Dump Standard (the editor's waveform) -----------------------------------------------------

    def _audio_for(self, name):
        """The int16 words the unit would send for sample `name` (wave length frames)."""
        if name in self.audio:
            return list(self.audio[name])
        frames = yp.extract(yp.get("sample", "wave_length"), self.samples[name])
        cycles = 1 + list(self.samples).index(name)  # a different tone per sample, deterministic
        return [round(20000 * math.sin(2 * math.pi * cycles * i / max(frames, 1))) for i in range(frames)]

    def _sds(self, m):
        channel, kind = m[1], m[2]
        if channel not in (self.device, 0x7F):
            self.ignored_ops.append("SDS message for another channel")
            return
        if kind == 0x03:  # dump request
            self._sds_packets = []
            index = m[3] | (m[4] << 7)
            names = list(self.samples)
            if index >= len(names):
                self._say(bytes([0x7E, channel, sds_encoder.CANCEL, 0]))
                return
            name = names[index]
            words = self._audio_for(name)
            rate = yp.extract(yp.get("sample", "sampling_frequency_l"), self.samples[name])
            header, *packets = sds_encoder.build_sds_dump(words, rate, index, channel)
            self._sds_packets = [p[1:-1] for p in packets]
            self._say(header[1:-1])
        elif kind == sds_encoder.ACK:
            if self._sds_packets:
                self._say(self._sds_packets.pop(0))
        elif kind in (sds_encoder.CANCEL, sds_encoder.NAK):
            self._sds_packets = []
        else:
            self.ignored_ops.append(f"SDS message {kind:#04x}")

    def _identity(self):
        self._say(bytes([0x7E, 0x00, 0x06, 0x02, 0x43, 0x00, 0x41, 0x5A, 0x03, 0x16, 0x00, 0x00, 0x7F]))

    def _yamaha(self, m):
        kind, model, sub = m[1] >> 4, m[2], m[3]
        if kind == y.KIND_DUMP_REQUEST and model == y.MODEL_BULK:
            self._dump_request(m)
        elif model == y.MODEL_PARAM and kind == y.KIND_PARAMETER_CHANGE and sub == y.SUB_OBJECT_SELECT:
            self._select(m)
        elif model == y.MODEL_PARAM and kind == y.KIND_PARAMETER_CHANGE and sub == y.SUB_OBJECT_PARAMETER:
            self._edit(m)
        elif model == y.MODEL_PARAM and kind == y.KIND_PARAMETER_REQUEST and sub == y.SUB_OBJECT_PARAMETER:
            self._param_request(m)
        elif model == y.MODEL_PARAM and sub == y.SUB_OBJECT_LINK and kind in (y.KIND_PARAMETER_CHANGE, y.KIND_PARAMETER_REQUEST):
            self._link(m, kind)
        else:
            self.ignored_ops.append(f"yamaha kind {kind} model {model:#04x} sub {sub:#04x}")

    # -- bulk -------------------------------------------------------------------------------------------

    def _dump_request(self, m):
        fmt = m[11:13].decode("ascii", "replace")
        name = y.decode_name(m[13:29])
        if fmt == "OL":
            self._say(y.build_bulk_dump(self.device, "OL", y.pad_name("Object List"), self.object_list()))
        elif fmt == "PG":
            number = int(name) if name.isdigit() else None
            if number in self.programs:
                self._say(y.build_bulk_dump(self.device, "PG", y.pad_name(y.program_object_name(number)), bytes(self.programs[number])))
            else:
                self.ignored_ops.append(f"PG dump of unknown program {name!r}")
        elif fmt == "SP":
            if name in self.samples:
                self._say(y.build_bulk_dump(self.device, "SP", y.pad_name(name), bytes(self.samples[name])))
            else:
                self.ignored_ops.append(f"SP dump of unknown sample {name!r}")
        elif fmt == "WD":
            frames = self._wave_frames(name)
            if frames is not None:
                for message in yamaha_wave.build_wave_messages(self.device, name, frames):
                    self._say(message)
            else:
                self.ignored_ops.append(f"WD dump of unknown wave {name!r}")
        else:
            self.ignored_ops.append(f"{fmt} dump request")

    # -- object links (assigning samples to programs) ---------------------------------------------------------

    def _slot_range(self, slot):
        base = yp.PROGRAM_EASY_EDIT_BASE + yp.EASY_EDIT_BLOCK_SIZE * slot
        return base, base + yp.EASY_EDIT_BLOCK_SIZE

    def _link_slot(self, number, sample):
        data = self.programs[number]
        for slot in range(yp.extract(yp.get("program", "assigned_samples"), data)):
            if yp.extract(yp.get("easy_edit", "assigned_name"), data, slot) == sample:
                return slot
        return None

    def _set_link_bit(self, number, sample, on):
        data = self.samples[sample]
        word_at = yp.SAMPLE_PARAMETER_BASE + yp.LINKED_PROGRAMS_OFFSET + 4 * ((number - 1) // 32)
        word = int.from_bytes(data[word_at : word_at + 4], "big")
        bit = 1 << ((number - 1) % 32)
        data[word_at : word_at + 4] = ((word | bit) if on else (word & ~bit)).to_bytes(4, "big")

    def _link(self, m, kind):
        # a request has the same layout as a change (only the kind nibble differs): parse it as one
        msg = y.parse_parameter_message(bytes([m[0], (y.KIND_PARAMETER_CHANGE << 4) | (m[1] & 0x0F)]) + bytes(m[2:]))
        number = int(msg.object_name) if msg.object_name.isdigit() else None
        known = (
            msg.object_type == y.OBJECT_TYPES["program"] and number in self.programs
            and msg.lower_type == y.OBJECT_TYPES["sample"] and msg.lower_name in self.samples
        )
        if not known:
            self.ignored_ops.append(f"object link of an unknown {msg.object_name!r} / {msg.lower_name!r}")
            return
        if kind == y.KIND_PARAMETER_REQUEST:
            linked = self._link_slot(number, msg.lower_name) is not None
            self._say(y.build_object_link_change(self.device, msg.object_name, "program", msg.lower_name, "sample", linked))
            return
        if self.bulk_protect:
            self.ignored_ops.append("object link change while bulk protect is on")
            return
        data = self.programs[number]
        slot = self._link_slot(number, msg.lower_name)
        count = yp.extract(yp.get("program", "assigned_samples"), data)
        if msg.linked and slot is None:
            if self._slot_range(count)[1] > len(data):
                data += seed.EASY_EDIT_EMPTY
            start, end = self._slot_range(count)
            block = bytearray(seed.EASY_EDIT_EMPTY)
            block[0:16] = y.pad_name(msg.lower_name)
            ident = int.from_bytes(self.samples[msg.lower_name][60:64], "big") + 0x18
            block[16:20] = ident.to_bytes(4, "big")
            block[20] = y.OBJECT_TYPES["sample"]
            data[start:end] = block
            yp.store(yp.get("program", "assigned_samples"), data, count + 1)
            self._set_link_bit(number, msg.lower_name, True)
            data[1] |= 1
        elif not msg.linked and slot is not None:
            for later in range(slot + 1, count):  # the later slots shift down, keeping their values
                src, dst = self._slot_range(later), self._slot_range(later - 1)
                data[dst[0] : dst[1]] = data[src[0] : src[1]]
            last = self._slot_range(count - 1)
            data[last[0] : last[1]] = seed.EASY_EDIT_EMPTY
            yp.store(yp.get("program", "assigned_samples"), data, count - 1)
            self._set_link_bit(number, msg.lower_name, False)
            data[1] |= 1

    # -- parameters -------------------------------------------------------------------------------------

    def _lookup_object(self, name, otype):
        if otype == y.OBJECT_TYPES["program"] and name.isdigit() and int(name) in self.programs:
            return (otype, int(name))
        if otype == y.OBJECT_TYPES["sample"] and name in self.samples:
            return (otype, name)
        return None

    def _select(self, m):
        if self.drop_selects:
            self.ignored_ops.append("select dropped")
            return
        msg = y.parse_parameter_message(m)
        self.current = self._lookup_object(msg.object_name, msg.object_type)
        if self.current is None:
            self.ignored_ops.append(f"select of unknown object {msg.object_name!r}")

    def _payload_and_row(self, params):
        """(payload, Param, slot) for P-numbers on the current object, or None."""
        if self.current is None:
            return None
        otype, key = self.current
        if otype == y.OBJECT_TYPES["program"]:
            data = self.programs[key]
            if params[0] == 1:
                row = self._program_rows.get(tuple(params))
                return (data, row, None) if row else None
            if params[0] == 2:
                slot = params[1] * 100 + params[2]
                row = self._easy_rows.get((params[3], params[4]))
                blocks = (len(data) - yp.PROGRAM_EASY_EDIT_BASE) // yp.EASY_EDIT_BLOCK_SIZE
                return (data, row, slot) if row and slot < blocks else None
            return None
        data = self.samples[key]
        row = self._sample_rows.get(tuple(params))
        return (data, row, None) if row else None

    @staticmethod
    def _value_bytes(row, data, slot):
        if row.kind == "text":
            return yp.extract(row, data, slot).ljust(row.size).encode("ascii")
        value = yp.extract(row, data, slot)
        return y.encode_value(value, row.bulk_size if row.bits else row.size, signed=row.signed)

    def _param_request(self, m):
        params = tuple(m[4:10])
        found = self._payload_and_row(params)
        if found is None:
            self.ignored_ops.append(f"parameter request {params} with no matching object/parameter")
            return
        data, row, slot = found
        otype, key = self.current
        name = y.program_object_name(key) if otype == y.OBJECT_TYPES["program"] else key
        self._say(y.build_object_select(self.device, name, otype))  # the unit announces its object first
        self._say(y.build_object_edit(self.device, params, self._value_bytes(row, data, slot)))

    def _edit(self, m):
        if self.bulk_protect:
            self.ignored_ops.append("edit while bulk protect is on")
            return
        msg = y.parse_parameter_message(m)
        found = self._payload_and_row(msg.params)
        if found is None:
            self.ignored_ops.append(f"edit {msg.params} with no matching object/parameter")
            return
        data, row, slot = found
        if row.read_only or row.kind != "int" or len(msg.data) != (row.bulk_size if row.bits else row.size):
            self.ignored_ops.append(f"edit of {row.key} refused")
            return
        self.edits += 1
        data[1] |= 1  # edited flag (measured)
        if row.write_ignored:
            return  # accepted and ignored, like the real unit's sampling frequency / wave length
        yp.store(row, data, yp.decode_reply(row, msg.data), slot)
