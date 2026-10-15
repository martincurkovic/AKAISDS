"""A fake Ensoniq EPS/ASR that speaks real SysEx, for tests and demo mode.

Same idea as `core/demo_s950.py`: it fakes the MIDI PORTS, not any Python API. `FakeEnsoniq.out`/`.inp` have the rtmidi surface
(`send_message(frame)` / `get_message()`, frames INCLUDING the F0/F7), so the real engine (controller/ensoniq_transfers.py) runs on top of it
with no hardware. Everything it says and accepts is built and parsed with `core.ensoniq_sysex`, so a wire-format bug there shows up as a
failed conversation here.

**It is built from the same reading of the spec as the codec, so it can only catch our own inconsistencies, not a misreading of the spec or
a real unit's quirks.** Where the spec is silent the fake follows the engine's assumptions and says so below; the first real report from an
Ensoniq owner is the only thing that confirms or refutes them.

Supported (the read side): GET INSTRUMENT / LAYER / WAVESAMPLE PARAMETERS / WAVESAMPLE DATA / PARAMETER (free system blocks only). A GET
is answered the way the spec's section 8 describes: RESPONSE(ACK), then the PUT's first message (command + context [+ range]), then, once
that is ACKed, the data-only message, then a final ACK from the client. One conversation at a time: a command that arrives in the middle
of one is answered with NAK (section 3.1). Anything else is answered with silence and lands in `ignored_ops`.

Behaviour the spec states: errors are RESPONSE messages with a status code (05 invalid instrument for an instrument that isn't loaded, 06
invalid layer, 08 invalid wavesample, 0A invalid data range, 11 wavesample is a copy); a bad first-part ACK ends the conversation; the
unit never retransmits the second part.

GUESSED (nothing measured): that a GET of an absent instrument/layer/wavesample answers with those statuses at all (the spec defines the
codes, not what provokes them), whether a data range's END offset is inclusive (`inclusive_end`, so the engine is tested against both),
that the first RESPONSE(ACK) is sent before the first PUT message (`skip_initial_ack` drops it, in case a real unit doesn't),
what the layer map holds for a key with no wavesample (0), and the instrument size in blocks.
"""

from collections import deque

from core import ensoniq_sysex as e

SOX = 0xF0
EOX = 0xF7


def make_wavesample(name, *, frames=2000, rate_code=20, key_range=(0, 127), root_key=60, samples=None):
    """A resident wavesample: its parameter block plus its audio (signed 16-bit words). With no `samples`, a triangle wave `frames` long,
    so a fixture is cheap to make but never silent (silence would hide a dropped chunk)."""
    if samples is None:
        samples = [(abs((i % 200) - 100) - 50) * 300 for i in range(frames)]
    params = e.WavesampleParams.blank()
    params.name = name
    params.root_key = root_key
    params.sample_rate_code = rate_code
    params.key_range = key_range
    params.loop_mode = e.LOOP_FORWARD_NO_LOOP
    params.start = 0
    params.end = len(samples)
    params.loop_start = 0
    params.set_loop_end(len(samples), 0)
    return {"params": params, "samples": list(samples)}


def make_layer(name, wavesamples):
    return {"name": name, "wavesamples": dict(wavesamples)}


def make_instrument(name, layers):
    return {"name": name, "layers": dict(layers)}


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


class FakeEnsoniq:
    def __init__(
        self,
        *,
        instruments=None,
        channel=0,
        inclusive_end=False,
        skip_initial_ack=False,
        free_blocks=3000,
        copies=None,
        wait_on_first_get=False,
        corrupt_data_messages=0,
        corrupt_block_messages=0,
    ):
        self.channel = channel
        #: False: a data range (a, b) covers words a..b-1; True: a..b (one more). The spec doesn't say which; the engine accepts both.
        self.inclusive_end = inclusive_end
        #: True: a GET's first reply is the PUT message with no RESPONSE(ACK) before it (a real unit might do this)
        self.skip_initial_ack = skip_initial_ack
        self.free_blocks = free_blocks
        #: {(instrument, layer, wavesample)} that answer a data GET with status 11 ("wavesample is a copy")
        self.copies = set(copies or ())
        #: True: the first GET is answered with WAIT (section 3.2); the unit then waits for the client's ACK and carries on
        self.wait_on_first_get = wait_on_first_get
        #: the next this-many AUDIO data messages (GET WAVESAMPLE DATA) / parameter-block messages are sent one word short (a damaged
        #: transfer: the engine must NAK and ask again - the unit never retransmits a second part)
        self.corrupt_data_messages = corrupt_data_messages
        self.corrupt_block_messages = corrupt_block_messages
        #: instrument number -> {"name", "layers": {layer: {"name", "wavesamples": {ws: {"params", "samples"}}}}}
        self.instruments = {}
        self._outbox = deque()
        self.out = _FakeOut(self)
        self.inp = _FakeIn(self)
        self._conv = None  # {"phase", "data": [frame]} while a GET is being answered
        self._waited = False

        #: command codes (or "data") of every frame that was received and not answered because this fake doesn't know it
        self.ignored_ops = []
        #: every frame received, F0/F7 included
        self.received = []
        #: frames dropped for another channel or a bad structure
        self.rejected = []
        #: commands refused with NAK because a conversation was already open
        self.naks_sent = 0
        #: how many data messages were completely delivered and ACKed
        self.data_messages_delivered = 0

        if instruments is None:
            self._populate_default()
        else:
            self.instruments.update(instruments)

    def _populate_default(self):
        self.instruments[0] = make_instrument(
            "DRUMS",
            {
                0: make_layer(
                    "KIT",
                    {
                        1: make_wavesample("KICK", frames=3000, key_range=(0, 59), root_key=48),
                        2: make_wavesample("SNARE", frames=4000, key_range=(60, 127), root_key=72),
                    },
                )
            },
        )
        self.instruments[2] = make_instrument(
            "PAD",
            {0: make_layer("PAD A", {1: make_wavesample("PAD C3", frames=9000, rate_code=14)})},
        )

    # -- the unit's side of the wire ------------------------------------------------------

    def _send(self, data):
        self._outbox.append([SOX, *data, EOX])

    def _respond(self, status):
        self._send(e.build_response(status, self.channel))

    def handle(self, frame):
        if not frame or frame[0] != SOX or frame[-1] != EOX:
            return  # plain MIDI, or a fragment - nothing to answer
        self.received.append(frame)
        data = frame[1:-1]
        if not e.is_ensoniq(data) or len(data) < 4:
            self.rejected.append(data)
            return
        if data[2] & 0x0F != self.channel:
            return
        try:
            message = e.parse_message(data)
        except ValueError:
            self.rejected.append(data)
            return
        if self._conv is not None:
            self._handle_mid_conversation(message)
            return
        handlers = {
            e.CMD_GET_INSTRUMENT: self._get_instrument,
            e.CMD_GET_LAYER: self._get_layer,
            e.CMD_GET_WAVESAMPLE_PARAMETERS: self._get_wavesample_parameters,
            e.CMD_GET_WAVESAMPLE_DATA: self._get_wavesample_data,
            e.CMD_GET_PARAMETER: self._get_parameter,
        }
        handler = handlers.get(message.command)
        if handler is None:
            if message.command != e.CMD_RESPONSE:  # a stray ACK while idle is harmless
                self.ignored_ops.append(message.command)
            return
        if self.wait_on_first_get and not self._waited:
            # the unit needs longer: WAIT, and carry on only once the client has ACKed it
            self._waited = True
            self._conv = {"phase": "await_wait_ack", "handler": handler, "message": message}
            self._respond(e.STATUS_WAIT)
            return
        handler(message)

    def _handle_mid_conversation(self, message):
        conv = self._conv
        if message.command != e.CMD_RESPONSE:
            # section 3.1: "send a negative acknowledge if another message is received during processing"
            self.naks_sent += 1
            self._respond(e.STATUS_NAK)
            return
        status = e.parse_response(message) if len(message.body) >= 2 else e.STATUS_NAK
        if status != e.STATUS_ACK:
            self._conv = None  # the unit never retransmits the second part
            return
        if conv["phase"] == "await_wait_ack":
            self._conv = None
            conv["handler"](conv["message"])
        elif conv["phase"] == "await_first_ack":
            self._send(conv["data"])
            conv["phase"] = "await_data_ack"
        else:
            self.data_messages_delivered += 1
            self._conv = None

    def _converse(self, first, data, audio=False):
        """Reply to a GET: RESPONSE(ACK), the PUT's first message, and (after the client's ACK) the data-only message."""
        if not self.skip_initial_ack:
            self._respond(e.STATUS_ACK)
        self._send(first)
        if audio and self.corrupt_data_messages > 0 and len(data) > 6:
            self.corrupt_data_messages -= 1
            data = data[:-3]  # one word short
        elif not audio and self.corrupt_block_messages > 0:
            self.corrupt_block_messages -= 1
            data = data[:-3]
        self._conv = {"phase": "await_first_ack", "data": data}

    # -- lookups ------------------------------------------------------------------------------

    def _context(self, message):
        if len(message.body) < e.CONTEXT_SIZE:
            raise ValueError("context too short")
        return e.decode_context(message.body)

    def _find(self, instrument, layer=None, wavesample=None):
        """(object, status): the instrument / layer / wavesample dict, or the status the unit answers an absent one with."""
        inst = self.instruments.get(instrument)
        if inst is None:
            return None, e.STATUS_INVALID_INSTRUMENT
        if layer is None:
            return inst, e.STATUS_ACK
        lyr = inst["layers"].get(layer)
        if lyr is None:
            return None, e.STATUS_INVALID_LAYER
        if wavesample is None:
            return lyr, e.STATUS_ACK
        ws = lyr["wavesamples"].get(wavesample)
        if ws is None:
            return None, e.STATUS_INVALID_WAVESAMPLE
        return ws, e.STATUS_ACK

    # -- blocks ---------------------------------------------------------------------------------

    def instrument_block(self, inst):
        values = [0] * e.INSTRUMENT_BLOCK_WORDS
        values[: e.NAME_WORDS] = e.encode_name(inst["name"])
        words = sum(
            len(ws["samples"]) for lyr in inst["layers"].values() for ws in lyr["wavesamples"].values()
        )
        values[15] = min(255, (words + e.BLOCK_WORDS - 1) // e.BLOCK_WORDS + 2)
        return values

    def layer_block(self, lyr):
        values = [0] * e.LAYER_BLOCK_WORDS
        values[: e.NAME_WORDS] = e.encode_name(lyr["name"])
        # words 19-106: one wavesample number per key (88 keys; index 0 is MIDI note 21)
        for index in range(88):
            note = 21 + index
            for number, ws in lyr["wavesamples"].items():
                lo, hi = ws["params"].key_range
                if lo <= note <= hi:
                    values[19 + index] = number
                    break
        return values

    # -- GET handlers ------------------------------------------------------------------------------

    def _get_instrument(self, message):
        instrument, _layer, _ws = self._context(message)
        inst, status = self._find(instrument)
        if status != e.STATUS_ACK:
            self._respond(status)
            return
        first = e.build_message(e.CMD_PUT_INSTRUMENT, message.body[: e.CONTEXT_SIZE], self.channel)
        self._converse(first, e.build_block_message(self.instrument_block(inst), self.channel))

    def _get_layer(self, message):
        instrument, layer, _ws = self._context(message)
        lyr, status = self._find(instrument, layer)
        if status != e.STATUS_ACK:
            self._respond(status)
            return
        first = e.build_message(e.CMD_PUT_LAYER, message.body[: e.CONTEXT_SIZE], self.channel)
        self._converse(first, e.build_block_message(self.layer_block(lyr), self.channel))

    def _get_wavesample_parameters(self, message):
        instrument, layer, wavesample = self._context(message)
        ws, status = self._find(instrument, layer, wavesample)
        if status != e.STATUS_ACK:
            self._respond(status)
            return
        first = e.build_message(e.CMD_PUT_WAVESAMPLE_PARAMETERS, message.body[: e.CONTEXT_SIZE], self.channel)
        block = e.WavesampleParams.from_block(ws["params"].to_block())
        if self.inclusive_end:
            # a unit whose data ranges are inclusive describes its sample the same way: `end` is the LAST word
            block.end = block.end - 1
        self._converse(first, e.build_block_message(block.to_block(), self.channel))

    def _get_wavesample_data(self, message):
        try:
            instrument, layer, wavesample, start, end = e.parse_context_and_range(message.body)
        except ValueError:
            self.rejected.append(message.body)
            return
        ws, status = self._find(instrument, layer, wavesample)
        if status != e.STATUS_ACK:
            self._respond(status)
            return
        if (instrument, layer, wavesample) in self.copies:
            self._respond(e.STATUS_WAVESAMPLE_IS_A_COPY)
            return
        samples = ws["samples"]
        last = end if self.inclusive_end else end - 1  # the last word index the range covers
        if start > last or last >= len(samples):
            self._respond(e.STATUS_INVALID_DATA_RANGE)
            return
        first = e.build_message(e.CMD_PUT_WAVESAMPLE_DATA, message.body[: e.CONTEXT_SIZE + 8], self.channel)
        self._converse(first, e.build_data_message(samples[start : last + 1], self.channel), audio=True)

    def _get_parameter(self, message):
        # the unit answers with ACK and then ONE PUT PARAMETER message (no second part, no response needed)
        body = message.body
        if len(body) < e.CONTEXT_SIZE + 2:
            self.rejected.append(body)
            return
        page, item = e.decode_param_number(body[e.CONTEXT_SIZE], body[e.CONTEXT_SIZE + 1])
        if (page, item) != (e.PAGE_SYSTEM, e.SYSTEM_ITEM_FREE_BLOCKS):
            self._respond(e.STATUS_INVALID_PARAMETER_NUMBER)
            return
        if not self.skip_initial_ack:
            self._respond(e.STATUS_ACK)
        self._send(
            e.build_message(
                e.CMD_PUT_PARAMETER,
                list(body[: e.CONTEXT_SIZE + 2]) + e.encode_param_value(self.free_blocks),
                self.channel,
            )
        )
