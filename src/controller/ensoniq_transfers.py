"""Ensoniq EPS / EPS-16 PLUS / ASR-10 transfers: the sample list and receiving samples (read only).

Owned by `SamplerController` (which delegates here whenever the Sampler Type's protocol family is "ensoniq") and reporting through the
controller's own signals, so the Dashboard needs no Ensoniq-specific plumbing beyond the sparse sample list (`sample_slots_updated`).
The wire format lives in `core/ensoniq_sysex.py`; this module is only the event-driven choreography, written the way
`controller/s950_transfers.py` is (Qt timers + `sysex_received`, one GUI thread).

**NOTHING HERE HAS BEEN RUN AGAINST AN ENSONIQ.** It follows Ensoniq's External Command Specification (see
dev_docs/ensoniq-support-plan.md and ensoniq-spec-crosscheck.md); where the spec is silent the choice is made here and logged.

The conversation (spec sections 3 and 8):
 - ONE command in flight. A 2 s timer runs after every command; a WAIT status restarts it at 30 s and is answered with ACK.
 - A GET is answered by RESPONSE(ACK), then the unit sends the matching PUT's FIRST message (command + context [+ range]); we ACK it
   within the 2 s limit, then the unit sends the data as a SECOND message (no command byte), and we ACK that. The unit never
   retransmits the second part, so a bad data message is NAKed and the whole GET is asked for again.
 - An error is a RESPONSE with a status code instead of the ACK (05 = instrument not loaded, 06 = no such layer, 08 = no such
   wavesample, 0A = bad data range, 11 = wavesample is a copy).
 - There is no list command. The list is probed: GET INSTRUMENT 0-7, GET LAYER 0-7 of each instrument that exists, then GET
   WAVESAMPLE PARAMETERS for every wavesample number in the layer's key map (layer block words 19-106).

Receiving a sample reads its parameter block (start, end, sample-rate code), then the audio in chunks (`chunk_words`). The unit's own end
offset is treated as exclusive; the answer may carry one word more (an inclusive end - the spec doesn't say), which is trimmed, and a
final chunk the unit rejects as out of range (status 0A) is asked for once more one word shorter. Whatever the unit does is logged.

Sending (`send_file_queue`) is the part with the most guesses - the spec doesn't describe a whole upload. Each file becomes a NEW instrument in
the first unused slot (found by probing GET INSTRUMENT; nothing is ever overwritten), one layer per channel (a stereo file is two layers,
panned hard left and hard right), one wavesample in each:
   GET free blocks (param 34 00) -> CREATE INSTRUMENT -> per layer: CREATE LAYER (which brings one placeholder wavesample) -> GET its
   parameters -> PUT them back with name / root key / rate code / one-shot -> TRUNCATE -> PUT WAVESAMPLE DATA in `put_chunk_words` pieces
   at rising offsets (the wavesample grows - how is not stated by the spec) -> GET/PUT the parameters again to set start / end / loop ->
   [stereo: PUT PARAMETER pan] -> read back the parameters and a stretch of audio and compare.
The instrument itself is never rewritten (PUT INSTRUMENT and PUT LAYER carry RAM pointers - the S1000 lesson), so it keeps the unit's default
name; the wavesample carries the name. If anything fails the message says which instrument may be left incomplete: nothing here can delete it.

Never sent from here: delete instrument/layer/wavesample and SAVE INSTRUMENT (`ensoniq_sysex.NEVER_SEND`).
"""

import math
import os

from PySide6.QtCore import QObject, QTimer

from core import debug_log, sds_encoder
from core import ensoniq_sysex as e

MIDI_BYTES_PER_SECOND = 3125
#: sample numbers the Dashboard list carries: instrument, layer and wavesample packed into one int (ws 1-127 fits 7 bits)
NUMBER_LAYER_SHIFT = 7
NUMBER_INSTRUMENT_SHIFT = 10

_BUSY_OPS = ("receive", "send")

#: a sample is never sent shorter than this (the CREATE WAVESAMPLE placeholder is a short single cycle)
MIN_SEND_WORDS = 64
PAGE_VOLUME = 6  # EPS spec section 9.9: "Pan" is item 2 (Table 5: 1 = far left ... 8 = far right)
PAN_ITEM = 2
PAN_LEFT, PAN_RIGHT = 1, 8
DEFAULT_ROOT_KEY = 60


def pack_number(instrument, layer, wavesample):
    return (instrument << NUMBER_INSTRUMENT_SHIFT) | (layer << NUMBER_LAYER_SHIFT) | wavesample


def unpack_number(number):
    return (
        (number >> NUMBER_INSTRUMENT_SHIFT) & 0x7,
        (number >> NUMBER_LAYER_SHIFT) & 0x7,
        number & 0x7F,
    )


def wavesample_numbers_in_layer(layer_block):
    """The distinct wavesample numbers a layer block's key map (words 19-106) points at, in ascending order."""
    return sorted({v for v in layer_block[19:107] if e.WAVESAMPLE_MIN <= v <= e.WAVESAMPLE_MAX})


def prepare_channels(channels, framerate, *, target_rate=None, force_mono=False):
    """WAV channels -> (list of word lists, rate code, exact rate in Hz). Raises ValueError if unusable.

    The unit's rate is a code (period = code x 1.6 microseconds), so a rate it can't represent exactly is resampled to the nearest code's
    EXACT rate (44.1 kHz becomes 44,643 Hz) - otherwise the sample would play about 1.2% off pitch. A stereo file stays two channels
    unless `force_mono` (then the left channel, as the S950 path does). Longer than one data range can address (1,048,575 words) is
    refused, never truncated; shorter than MIN_SEND_WORDS is padded with silence.
    """
    if len(channels) == 2 and not force_mono:
        chosen = [list(channels[0]), list(channels[1])]
    else:
        chosen = [list(channels[0])]
    target = target_rate or framerate
    code = e.hz_to_rate_code(target)
    rate = round(e.rate_code_to_hz(code))
    if rate != framerate:
        chosen = [sds_encoder.resample_to_target_rate(ch, framerate, rate)[0] for ch in chosen]
    longest = max(len(ch) for ch in chosen)
    if longest > e.MAX_OFFSET:
        raise ValueError(
            f"too long for the Ensoniq ({longest:,} words, {longest / rate:.1f} s at {rate} Hz - one wavesample can be at most "
            f"{e.MAX_OFFSET:,} words)"
        )
    out = []
    for ch in chosen:
        words = [max(-32768, min(32767, int(v))) for v in ch]
        if len(words) < MIN_SEND_WORDS:
            words += [0] * (MIN_SEND_WORDS - len(words))
        out.append(words)
    return out, code, rate


class _Result:
    """What a GET conversation ended with: `status` (an error RESPONSE), `error` (our own text) or `values` (the block / samples)."""

    def __init__(self, status=None, error=None, values=None):
        self.status, self.error, self.values = status, error, values

    @property
    def ok(self):
        return self.status is None and self.error is None


class _Exchange:
    """One conversation. `kind`: "block" / "samples" (a GET), "cmd" (a command answered by one RESPONSE), "put" (a PUT of two messages),
    "param" (GET PARAMETER: ACK then one PUT PARAMETER back), "silent" (a PUT PARAMETER: no answer unless it is wrong)."""

    def __init__(self, put_command, context, kind, counts, callback, label, second=None, param_bytes=None, optional=False):
        self.optional = optional  # True: a timeout is a result (error), not the end of the whole operation
        self.second = second  # the data-only message of a "put", sent once the first is ACKed
        self.param_bytes = param_bytes
        self.put_command = put_command
        self.context = list(context)  # the leading context bytes the first PUT message must echo (instrument; + layer; + wavesample)
        self.kind = kind  # "block" or "samples"
        self.counts = set(counts)  # acceptable word counts in the data message
        self.callback = callback
        self.label = label
        self.phase = "ack1" if kind == "put" else "ack"  # then "put", then "data" (a GET); a PUT: "ack1" then "ack2"


class EnsoniqTransfers(QObject):
    # a message the transfer did not ask for (see _dispatch): a few are normal (a late reply after a timeout); a steady stream is another
    # MIDI program echoing the sampler's replies back to it (AGENTS.md's incident notes)
    _UNEXPECTED_WARNING_COUNT = 10

    def __init__(self, controller):
        super().__init__()
        self._c = controller

        # timing, in ms - attributes so tests can shrink them
        self.reply_timeout_ms = int(e.COMMAND_TIMEOUT_S * 1000)
        self.wait_timeout_ms = int(e.WAIT_TIMEOUT_S * 1000)
        #: audio words asked for per GET. The EPS-16+ community tool sends 5000 per message (its own choice, the spec states no maximum)
        self.chunk_words = 4096
        self.chunk_retries = 2
        # sending
        #: audio words per PUT WAVESAMPLE DATA. The EPS-16+ community tool sends 5000 per message and pauses 700 ms after each (its own
        #: choices, the spec states no maximum); a smaller piece means a smaller thing to lose to one damaged message
        self.put_chunk_words = 2048
        #: pause after every acknowledged step, ms (the unit is slow; nothing in the spec says how slow)
        self.pace_ms = 50
        #: a command with no answer (PUT PARAMETER) still has to wait out the 2 s command timer (spec section 3.1)
        self.silent_wait_ms = int(e.COMMAND_TIMEOUT_S * 1000)
        #: TRUNCATE after the first parameter write, before the audio (the sequence the EPS-16+ community tool uses)
        self.truncate_placeholder = True
        #: the unit's sample END offset is written as words - 1 + this (GUESS: whether `end` is the last word or one past it is not
        #: stated; -1 can only cost a final sample, 0 could point one word past the data)
        self.end_adjust = -1
        #: PUT ranges are (a, b) with b one past the last word; the first chunk flips this once if the unit refuses that
        self.put_end_inclusive = False
        self.verify_words = 64
        self.memory_margin_blocks = 8

        #: last list: [{"instrument", "layer", "wavesample", "name", "label", "rate_code", "length"}]
        self.entries = []
        self.instruments = {}  # instrument number -> name

        self._op = None
        self._x = None
        self._silent = False
        self._unexpected = 0
        self._step_fn = None
        # list scan
        self._scan_instruments = []
        self._scan_layers = []
        self._scan_wavesamples = []
        self._scan_entries = []
        self._scan_instrument_names = {}
        self._layer_names = {}
        self._mem = None
        # receive
        self._queue = []
        self._queue_total = 0
        self._receive_total = 0
        self._rx = None
        # send
        self._tx = None
        self._sent = 0
        self._skipped = 0
        self._unverified = 0

        self._reply_timer = self._make_timer(self._on_reply_timeout)
        self._step_timer = self._make_timer(self._run_step)

    def _make_timer(self, slot):
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(slot)
        return timer

    # -- public surface (what SamplerController delegates to) ------------------------------------

    @property
    def busy(self):
        return self._op in _BUSY_OPS

    @property
    def idle(self):
        return self._op is None

    def refresh_catalog(self, silent=False):
        if not self._begin("catalog", "Listing the samples", silent):
            return
        self._silent = silent
        if not silent:
            self._c.status_changed.emit("Reading the Ensoniq's instruments...")
        self._scan_instruments = list(range(e.INSTRUMENT_MAX + 1))
        self._scan_layers = []
        self._scan_wavesamples = []
        self._scan_entries = []
        self._scan_instrument_names = {}
        self._layer_names = {}
        self._scan_next()

    def send_file_queue(self, entries):
        """Send each file as a new instrument. Returns True if it started. Reports through the controller's transfer signals."""
        if not entries:
            return False
        if not self._begin("send", "Sending samples"):
            return False
        self._queue = list(entries)
        self._queue_total = len(self._queue)
        self._sent = self._skipped = self._unverified = 0
        self._tx = None
        self._next_file()
        return True

    def receive_samples(self, requests):
        if not requests:
            return
        if not self._begin("receive", "Receiving samples"):
            return
        self._queue = list(requests)
        self._receive_total = len(self._queue)
        self._next_receive()

    def cancel(self):
        """Abort whatever is running. Returns True if something was. The unit can't be told to stop (CANCEL is only valid in WAIT mode),
        so a message already on its way is simply ignored when it lands."""
        if self._op is None:
            return False
        op = self._op
        partial = self._partial_note()
        self._stop_timers()
        self._x = None
        self._queue = []
        self._reset()
        if op == "receive":
            self._c.status_changed.emit("Transfer cancelled")
            self._c.receive_finished.emit(False)
        elif op == "send":
            self._c.status_changed.emit("Transfer cancelled" + (f".{partial}" if partial else ""))
            self._c.transfer_finished.emit(False)
        return True

    def handle_sysex(self, data):
        """Every SysEx the controller receives while the Ensoniq family is selected."""
        try:
            self._dispatch(bytes(data))
        except Exception as ex:
            debug_log.get_logger().error("EnsoniqTransfers: unhandled exception while handling SysEx", exc_info=True)
            self._fail(f"Unexpected error: {ex}")

    # -- plumbing ----------------------------------------------------------------------------------------

    def _wire_channel(self):
        # the byte after the product ID is "base MIDI channel minus 1" (0-15) in the ASR-10 spec; the ASR-X manual has a separate SysEx
        # Device ID instead. Which one a unit wants is unknown, so the Device ID the user set is sent as is, if it fits
        channel = self._c.channel
        return channel if 0 <= channel <= 15 else None

    def _begin(self, op, what, silent=False):
        if self._op is not None:
            if not silent:
                self._c.status_changed.emit("A transfer is already in progress - please wait for it to finish")
            return False
        if self._c.is_open_loop():
            if not silent:
                self._c.status_changed.emit(
                    f"{what} needs a MIDI input - the Ensoniq answers on it. Select one in Settings."
                )
            return False
        if self._wire_channel() is None:
            if not silent:
                self._c.status_changed.emit(
                    "The Ensoniq needs a SysEx channel of 0-15 (its base MIDI channel minus 1) - set it in Settings"
                )
            return False
        self._op = op
        debug_log.get_logger().info(f"EnsoniqTransfers: begin {op} ({what}) on wire channel {self._wire_channel()}")
        return True

    def _reset(self):
        self._op = None
        self._x = None
        self._rx = None
        self._tx = None
        self._mem = None
        self._step_fn = None
        self._scan_instruments = []
        self._scan_layers = []
        self._scan_wavesamples = []

    def _stop_timers(self):
        self._reply_timer.stop()
        self._step_timer.stop()

    def _after(self, ms, fn):
        self._step_fn = fn
        self._step_timer.start(max(0, int(ms)))

    def _run_step(self):
        fn, self._step_fn = self._step_fn, None
        if fn is None:
            return
        try:
            fn()
        except Exception as ex:
            debug_log.get_logger().error("EnsoniqTransfers: unhandled exception in a transfer step", exc_info=True)
            self._fail(f"Unexpected error: {ex}")

    def _send(self, data):
        try:
            self._c.midi_manager.send_sysex(data)
        except Exception as ex:
            debug_log.get_logger().error(f"EnsoniqTransfers: MIDI send failed ({len(data)} bytes)", exc_info=True)
            self._fail(f"Couldn't send to the Ensoniq: {ex}")
            return False
        debug_log.get_logger().debug(f"EnsoniqTransfers: sent {bytes(data[:24]).hex(' ')}{' ...' if len(data) > 24 else ''} ({len(data)} bytes)")
        return True

    def _send_response(self, status):
        return self._send(e.build_response(status, self._wire_channel()))

    def _fail(self, message):
        message += self._partial_note()
        debug_log.get_logger().warning(f"EnsoniqTransfers: {self._op} failed: {message}")
        op = self._op
        silent = self._silent
        self._stop_timers()
        self._x = None
        self._queue = []
        self._reset()
        self._silent = False
        if op == "catalog" and silent:
            return
        self._c.status_changed.emit(message)
        if op == "receive":
            self._c.receive_finished.emit(False)
        elif op == "send":
            self._c.transfer_finished.emit(False)

    def _partial_note(self):
        # a failed send can leave a half-built instrument on the unit, and nothing here deletes anything
        tx = self._tx
        if self._op == "send" and tx and tx.get("created"):
            return f" Instrument {tx['inst'] + 1} on the Ensoniq may be incomplete - delete it on the unit."
        return ""

    def _on_reply_timeout(self):
        x = self._x
        if x is not None and x.kind == "silent":
            # no answer within the command timer is how a PUT PARAMETER says "fine"
            self._finish(x, _Result(values=[]))
            return
        if x is not None and x.optional:
            debug_log.get_logger().info(f"EnsoniqTransfers: no answer for {x.label} (phase {x.phase}) - carrying on without it")
            self._finish(x, _Result(error="no answer"))
            return
        self._x = None
        label = x.label if x else "its last request"
        phase = x.phase if x else "ack"
        if phase == "ack":
            self._fail(
                f"No reply from the Ensoniq for {label}. Check it's on, MIDI SysEx is enabled in its MIDI settings, both MIDI cables are "
                "connected and the channel matches (a SCSI cable on the unit can also make it ignore MIDI)"
            )
        else:
            self._fail(f"The Ensoniq stopped answering while sending {label} - try again")

    # -- one GET conversation ----------------------------------------------------------------------------

    def _get(self, request, put_command, context, kind, counts, callback, label):
        """Send `request` and run the GET conversation; `callback(_Result)` is called when it ends (never on a timeout: that fails the op)."""
        self._x = _Exchange(put_command, context, kind, counts, callback, label)
        self._reply_timer.start(self.reply_timeout_ms)
        if not self._send(request):
            self._reply_timer.stop()

    def _get_instrument(self, instrument, callback):
        ch = self._wire_channel()
        self._get(
            e.build_get_instrument(instrument, ch),
            e.CMD_PUT_INSTRUMENT,
            e.encode_context(instrument, 0, 1)[:2],  # only the instrument word: what a unit puts in the other fields is its own affair
            "block",
            {e.INSTRUMENT_BLOCK_WORDS},
            callback,
            f"instrument {instrument + 1}",
        )

    def _get_layer(self, instrument, layer, callback):
        ch = self._wire_channel()
        self._get(
            e.build_get_layer(instrument, layer, ch),
            e.CMD_PUT_LAYER,
            e.encode_context(instrument, layer, 1)[:4],
            "block",
            {e.LAYER_BLOCK_WORDS},
            callback,
            f"layer {layer + 1} of instrument {instrument + 1}",
        )

    def _get_wavesample_parameters(self, instrument, layer, wavesample, callback):
        ch = self._wire_channel()
        self._get(
            e.build_get_wavesample_parameters(instrument, layer, wavesample, ch),
            e.CMD_PUT_WAVESAMPLE_PARAMETERS,
            e.encode_context(instrument, layer, wavesample),
            "block",
            {e.WAVESAMPLE_BLOCK_WORDS},
            callback,
            f"wavesample {wavesample} (instrument {instrument + 1}, layer {layer + 1})",
        )

    def _get_data(self, instrument, layer, wavesample, start, end, callback):
        ch = self._wire_channel()
        self._get(
            e.build_get_wavesample_data(instrument, layer, wavesample, start, end, ch),
            e.CMD_PUT_WAVESAMPLE_DATA,
            e.encode_context(instrument, layer, wavesample),
            "samples",
            {end - start, end - start + 1},  # exclusive end, or an inclusive one (one word more) - the spec doesn't say
            callback,
            f"audio words {start}-{end} of wavesample {wavesample}",
        )

    def _start(self, x, message, timeout_ms=None):
        self._x = x
        self._reply_timer.start(self.reply_timeout_ms if timeout_ms is None else timeout_ms)
        if not self._send(message):
            self._reply_timer.stop()

    def _command(self, message, callback, label):
        """A command answered by one RESPONSE (CREATE ..., TRUNCATE). callback(_Result)."""
        self._start(_Exchange(None, [], "cmd", (), callback, label), message)

    def _put(self, first, second, callback, label):
        """A PUT of two messages: `first` (command + context), then - once the unit ACKs it - `second` (the data alone), then a final ACK."""
        self._start(_Exchange(None, [], "put", (), callback, label, second=second), first)

    def _get_parameter_value(self, page, item, callback, instrument=0, optional=False):
        ch = self._wire_channel()
        number = e.encode_param_number(page, item)
        x = _Exchange(
            e.CMD_PUT_PARAMETER, [], "param", (), callback, f"parameter {page}/{item:02X}", param_bytes=number, optional=optional
        )
        self._start(x, e.build_get_parameter(instrument, 0, 1, page, item, ch))

    def _put_parameter_value(self, instrument, layer, wavesample, page, item, value, callback, label):
        """PUT PARAMETER: no answer unless the number or value is wrong, but the 2 s command timer is still waited out."""
        ch = self._wire_channel()
        x = _Exchange(None, [], "silent", (), callback, label)
        self._start(x, e.build_put_parameter(instrument, layer, wavesample, page, item, value, ch), self.silent_wait_ms)

    def _dispatch(self, data):
        if not data:
            return
        if not e.is_ensoniq(data):
            debug_log.get_logger().debug(f"EnsoniqTransfers: ignoring non-Ensoniq SysEx {data[:8].hex(' ')}")
            return
        x = self._x
        if x is None:
            self._note_unexpected(data, "no request is waiting")
            return
        if x.phase == "data":
            self._on_data(x, data)
            return
        try:
            message = e.parse_message(data)
        except ValueError as ex:
            debug_log.get_logger().warning(f"EnsoniqTransfers: unreadable message ({len(data)} bytes, {ex}): {data[:32].hex(' ')}")
            return
        if message.command == e.CMD_RESPONSE:
            self._on_response(x, message)
        elif message.command == x.put_command:
            self._on_put_first(x, message)
        else:
            self._note_unexpected(data, f"command 0x{message.command:02X} while waiting for {x.label}")

    def _note_unexpected(self, data, why):
        self._unexpected += 1
        debug_log.get_logger().debug(
            f"EnsoniqTransfers: ignoring unexpected message ({why}): {data[:16].hex(' ')} (#{self._unexpected})"
        )
        if self._unexpected == self._UNEXPECTED_WARNING_COUNT:
            debug_log.get_logger().warning(
                f"EnsoniqTransfers: {self._unexpected} unexpected messages this session - late replies do this, but so does another "
                "MIDI program sending the sampler's replies back to it (see AGENTS.md's incident notes)"
            )

    def _on_response(self, x, message):
        try:
            status = e.parse_response(message)
        except ValueError as ex:
            debug_log.get_logger().warning(f"EnsoniqTransfers: unreadable RESPONSE: {ex}")
            return
        if status == e.STATUS_ACK:
            if x.kind == "cmd":
                self._finish(x, _Result(values=[]))
            elif x.kind == "put":
                if x.phase == "ack1":
                    # the first message is ACKed: now the data alone, and the unit ACKs again once it has all of it
                    x.phase = "ack2"
                    second = x.second
                    wire_ms = len(second) * 1000 / MIDI_BYTES_PER_SECOND
                    self._reply_timer.start(int(self.reply_timeout_ms + wire_ms * 1.5))
                    self._send(second)
                else:
                    self._finish(x, _Result(values=[]))
            elif x.kind == "silent":
                pass  # a stray ACK: the answer that matters is an error, or none at all
            elif x.phase == "ack":
                x.phase = "put"
                self._reply_timer.start(self.reply_timeout_ms)
            return  # (a GET or parameter read) an ACK while waiting for the PUT's first message is a stray one
        if status == e.STATUS_WAIT:
            # section 3.2: restart the timer at 30 s and answer ACK (or CANCEL) so the unit goes on
            debug_log.get_logger().info(f"EnsoniqTransfers: WAIT from the unit for {x.label}")
            self._reply_timer.start(self.wait_timeout_ms)
            self._send_response(e.STATUS_ACK)
            return
        debug_log.get_logger().info(f"EnsoniqTransfers: {x.label}: status 0x{status:02X} ({e.status_name(status)})")
        self._finish(x, _Result(status=status))

    def _on_put_first(self, x, message):
        if x.kind == "param":
            # GET PARAMETER is answered by ONE PUT PARAMETER message (no second part, no response needed): context, number, value
            body = message.body
            if len(body) < e.CONTEXT_SIZE + 6 or list(body[e.CONTEXT_SIZE : e.CONTEXT_SIZE + 2]) != x.param_bytes:
                self._note_unexpected(bytes(body[:16]), f"PUT PARAMETER for another parameter while waiting for {x.label}")
                return
            value = e.decode_param_value(body[e.CONTEXT_SIZE + 2 : e.CONTEXT_SIZE + 6])
            self._finish(x, _Result(values=[value]))
            return
        if list(message.body[: len(x.context)]) != x.context:
            self._note_unexpected(bytes(message.body[:16]), f"PUT for another object while waiting for {x.label}")
            return
        # ACK within the unit's 2 s limit (the ASR-10 spec, p.8), before anything else
        if not self._send_response(e.STATUS_ACK):
            return
        words = max(x.counts)
        wire_ms = 3 * words * 1000 / MIDI_BYTES_PER_SECOND
        x.phase = "data"
        self._reply_timer.start(int(self.reply_timeout_ms + wire_ms * 1.5))

    def _on_data(self, x, data):
        # a data-only message has no command byte, so a 6-byte RESPONSE could in principle be one sample word; it is only
        # treated as a RESPONSE when a single word isn't what we are waiting for
        if len(data) == 6 and data[3] == e.CMD_RESPONSE and 1 not in x.counts:
            try:
                self._on_response(x, e.parse_message(data))
            except ValueError:
                pass
            return
        try:
            values = e.decode_block(data[3:]) if x.kind == "block" else e.decode_samples(data[3:])
            if len(values) not in x.counts:
                raise ValueError(f"{len(values)} words, expected {' or '.join(str(c) for c in sorted(x.counts))}")
        except ValueError as ex:
            # the unit never retransmits the second part: NAK it and let the caller ask again
            debug_log.get_logger().warning(f"EnsoniqTransfers: bad data message for {x.label} ({len(data)} bytes): {ex}")
            self._send_response(e.STATUS_NAK)
            self._finish(x, _Result(error=str(ex)))
            return
        if not self._send_response(e.STATUS_ACK):
            return
        self._finish(x, _Result(values=values))

    def _finish(self, x, result):
        self._reply_timer.stop()
        if self._x is x:
            self._x = None
        try:
            x.callback(result)
        except Exception as ex:
            debug_log.get_logger().error(f"EnsoniqTransfers: unhandled exception finishing {x.label}", exc_info=True)
            self._fail(f"Unexpected error: {ex}")

    # -- the list ----------------------------------------------------------------------------------------------

    def _scan_next(self):
        if self._op != "catalog":
            return
        if self._scan_wavesamples:
            instrument, layer, wavesample = self._scan_wavesamples.pop(0)
            self._get_wavesample_parameters(
                instrument, layer, wavesample, lambda r, k=(instrument, layer, wavesample): self._scan_got_wavesample(k, r)
            )
        elif self._scan_layers:
            instrument, layer = self._scan_layers.pop(0)
            self._get_layer(instrument, layer, lambda r, k=(instrument, layer): self._scan_got_layer(k, r))
        elif self._scan_instruments:
            instrument = self._scan_instruments.pop(0)
            self._get_instrument(instrument, lambda r, k=instrument: self._scan_got_instrument(k, r))
        else:
            self._publish_catalog()

    def _scan_error(self, r, absent_status, what):
        """True if `r` ended the object's conversation in a way the scan carries on from (absent, or bad data worth skipping)."""
        if r.status == absent_status:
            return True
        if r.status is not None:
            self._fail(f"The Ensoniq answered {what}: {e.status_name(r.status)}. {e.status_text(r.status)}")
            return True
        if r.error is not None:
            debug_log.get_logger().warning(f"EnsoniqTransfers: skipping {what}: {r.error}")
            return True
        return False

    def _scan_got_instrument(self, instrument, r):
        if self._scan_error(r, e.STATUS_INVALID_INSTRUMENT, f"for instrument {instrument + 1}"):
            if self._op == "catalog":
                self._after(0, self._scan_next)
            return
        name = e.instrument_name(r.values)
        self._scan_instrument_names[instrument] = name
        debug_log.get_logger().info(
            f"EnsoniqTransfers: instrument {instrument + 1} {name!r}, {e.instrument_size_blocks(r.values)} blocks"
        )
        self._scan_layers = [(instrument, layer) for layer in range(e.LAYER_MAX + 1)] + self._scan_layers
        self._after(0, self._scan_next)

    def _scan_got_layer(self, key, r):
        instrument, layer = key
        if self._scan_error(r, e.STATUS_INVALID_LAYER, f"for layer {layer + 1} of instrument {instrument + 1}"):
            if self._op == "catalog":
                self._after(0, self._scan_next)
            return
        self._layer_names[key] = e.decode_name(r.values[: e.NAME_WORDS])
        numbers = wavesample_numbers_in_layer(r.values)
        debug_log.get_logger().info(
            f"EnsoniqTransfers: instrument {instrument + 1} layer {layer + 1} {self._layer_names[key]!r}: wavesamples {numbers}"
        )
        self._scan_wavesamples = [(instrument, layer, n) for n in numbers] + self._scan_wavesamples
        self._after(0, self._scan_next)

    def _scan_got_wavesample(self, key, r):
        instrument, layer, wavesample = key
        if self._scan_error(r, e.STATUS_INVALID_WAVESAMPLE, f"for wavesample {wavesample}"):
            if self._op == "catalog":
                self._after(0, self._scan_next)
            return
        try:
            params = e.WavesampleParams.from_block(r.values)
        except ValueError as ex:
            debug_log.get_logger().warning(f"EnsoniqTransfers: skipping wavesample {key}: {ex}")
            self._after(0, self._scan_next)
            return
        self._scan_entries.append(
            {
                "instrument": instrument,
                "layer": layer,
                "wavesample": wavesample,
                "name": params.name or f"Wavesample {wavesample}",
                "rate_code": params.sample_rate_code,
                "length": params.length,
            }
        )
        self._after(0, self._scan_next)

    def _publish_catalog(self):
        silent = self._silent
        entries = self._scan_entries
        names = [entry["name"].upper() for entry in entries]
        for entry in entries:
            # a name another wavesample shares gets where it lives, so the rows (and the files received from them) can be told apart
            entry["label"] = (
                entry["name"]
                if names.count(entry["name"].upper()) == 1
                else f"{entry['name']} ({entry['instrument'] + 1}.{entry['layer'] + 1}.{entry['wavesample']})"
            )
        self.entries = entries
        self.instruments = dict(self._scan_instrument_names)
        self._c.sample_slots_updated.emit(
            [(pack_number(x["instrument"], x["layer"], x["wavesample"]), x["label"]) for x in entries]
        )
        if not silent:
            n = len(entries)
            self._c.status_changed.emit(f"{n} sample{'' if n == 1 else 's'} on the Ensoniq")
        # then the memory figure for the Dashboard's bar: the list is already on screen, and nothing below can fail the refresh
        self._mem = {"todo": sorted(self.instruments), "used": 0, "free": None}
        self._get_parameter_value(e.PAGE_SYSTEM, e.SYSTEM_ITEM_FREE_BLOCKS, self._mem_got_free, optional=True)

    # memory ---------------------------------------------------------------------------------------------------------
    # free blocks (system parameter 0) + the size of every instrument (instrument parameter 7, read only) = the total. The unit's
    # own total isn't readable, and the sizes use the same "blocks" as the free count (GUESS: 256 words each).

    def _mem_finish(self, ok):
        mem, self._mem = self._mem, None
        silent = self._silent
        self._silent = False
        self._reset()
        if ok:
            free, used = mem["free"], mem["used"]
            info = {
                "max_num_samp_words": (free + used) * e.BLOCK_WORDS,
                "num_words_free": free * e.BLOCK_WORDS,
                "num_blocks_free": free,
            }
            debug_log.get_logger().info(
                f"EnsoniqTransfers: memory: {free} blocks free, {used} used by {len(self.instruments)} instrument(s)"
            )
        else:
            # a zero total tells the Dashboard to say "unknown" rather than keep a stale figure
            info = {"max_num_samp_words": 0, "num_words_free": 0, "num_blocks_free": 0}
            debug_log.get_logger().info("EnsoniqTransfers: couldn't read the memory figures - the bar stays unknown")
        self._c.memory_status_updated.emit(info)

    def _mem_got_free(self, r):
        if not (r.ok and r.values):
            self._mem_finish(False)
            return
        self._mem["free"] = r.values[0]
        self._after(0, self._mem_next_instrument)

    def _mem_next_instrument(self):
        if not self._mem["todo"]:
            self._mem_finish(True)
            return
        n = self._mem["todo"].pop(0)
        self._get_parameter_value(
            e.PAGE_INSTRUMENT, e.INSTRUMENT_ITEM_SIZE, lambda r, k=n: self._mem_got_size(k, r), instrument=n, optional=True
        )

    def _mem_got_size(self, instrument, r):
        if not (r.ok and r.values):
            self._mem_finish(False)
            return
        self._mem["used"] += r.values[0]
        self._after(0, self._mem_next_instrument)

    # -- receiving ---------------------------------------------------------------------------------------------

    def _entry_for(self, instrument, layer, wavesample):
        for entry in self.entries:
            if (entry["instrument"], entry["layer"], entry["wavesample"]) == (instrument, layer, wavesample):
                return entry
        return None

    def _next_receive(self):
        if not self._queue:
            self._reset()
            self._c.status_changed.emit("All samples received")
            self._c.receive_finished.emit(True)
            return
        number, path = self._queue.pop(0)
        instrument, layer, wavesample = unpack_number(number)
        entry = self._entry_for(instrument, layer, wavesample)
        name = entry["label"] if entry else f"wavesample {wavesample}"
        index = self._receive_total - len(self._queue)
        self._c.status_changed.emit(f"Receiving {name} ({index}/{self._receive_total})...")
        self._rx = {
            "instrument": instrument,
            "layer": layer,
            "wavesample": wavesample,
            "path": path,
            "name": name,
            "samples": [],
            "chunks": [],
            "retries": 0,
        }
        self._get_wavesample_parameters(instrument, layer, wavesample, self._rx_got_parameters)

    def _rx_failed(self, r, what):
        if r.status is not None:
            if r.status == e.STATUS_INVALID_WAVESAMPLE:
                self._fail(f"{what} isn't on the Ensoniq any more - refresh the list")
            elif r.status == e.STATUS_WAVESAMPLE_IS_A_COPY:
                self._fail(f"{what} is a copy of another wavesample - receive the original instead")
            else:
                self._fail(f"The Ensoniq answered {what}: {e.status_name(r.status)}. {e.status_text(r.status)}")
        else:
            self._fail(f"Couldn't read {what}: {r.error}")

    def _rx_got_parameters(self, r):
        rx = self._rx
        if r.error is not None and rx.get("param_retries", 0) < self.chunk_retries:
            rx["param_retries"] = rx.get("param_retries", 0) + 1
            debug_log.get_logger().info(f"EnsoniqTransfers: asking for {rx['name']}'s parameters again ({r.error})")
            self._get_wavesample_parameters(rx["instrument"], rx["layer"], rx["wavesample"], self._rx_got_parameters)
            return
        if not r.ok:
            self._rx_failed(r, f"the parameters of {rx['name']}")
            return
        params = e.WavesampleParams.from_block(r.values)
        start, end, code = params.start, params.end, params.sample_rate_code
        debug_log.get_logger().info(
            f"EnsoniqTransfers: {rx['name']!r} (instrument {rx['instrument'] + 1}, layer {rx['layer'] + 1}, wavesample "
            f"{rx['wavesample']}): start={start} end={end} loop={params.loop_start}..{params.loop_end} mode={params.loop_mode} "
            f"rate code={code} root={params.root_key} copy={r.values[e.WS_COPY_NUMBER]}/{r.values[e.WS_COPY_LAYER]}"
        )
        if not e.RATE_CODE_MIN <= code <= e.RATE_CODE_MAX:
            self._fail(f"{rx['name']} has a sample rate code the app doesn't understand ({code}) - please report it with the log")
            return
        if not 0 <= start < end <= e.MAX_OFFSET + 1:
            self._fail(
                f"Couldn't work out how long {rx['name']} is (start {start}, end {end}) - please report it with ~/.akaisds/akaisds.log"
            )
            return
        rx["rate"] = round(e.rate_code_to_hz(code))
        rx["start"], rx["end"] = start, end
        rx["total"] = end - start
        rx["chunks"] = [(start + a, start + b) for a, b in e.chunk_ranges(end - start, self.chunk_words)]
        self._rx_next_chunk()

    def _rx_next_chunk(self):
        rx = self._rx
        if not rx["chunks"]:
            self._rx_finish()
            return
        rx["chunk"] = rx["chunks"].pop(0)
        rx["retries"] = 0
        self._rx_request_chunk(*rx["chunk"])

    def _rx_request_chunk(self, a, b):
        rx = self._rx
        self._get_data(rx["instrument"], rx["layer"], rx["wavesample"], a, b, self._rx_got_chunk)

    def _rx_got_chunk(self, r):
        rx = self._rx
        a, b = rx["chunk"]
        if r.status == e.STATUS_INVALID_DATA_RANGE and not rx["chunks"] and b - a > 1 and not rx.get("shortened"):
            # the final chunk reached past the data: if the unit's end offset is inclusive, `end` itself is one too far
            rx["shortened"] = True
            rx["chunk"] = (a, b - 1)
            debug_log.get_logger().info(f"EnsoniqTransfers: range {a}-{b} refused (0A); asking for {a}-{b - 1}")
            self._rx_request_chunk(a, b - 1)
            return
        if r.error is not None and rx["retries"] < self.chunk_retries:
            rx["retries"] += 1
            debug_log.get_logger().info(f"EnsoniqTransfers: asking for {a}-{b} again ({r.error}), try {rx['retries'] + 1}")
            self._rx_request_chunk(a, b)
            return
        if not r.ok:
            self._rx_failed(r, f"the audio of {rx['name']}")
            return
        need = rx["chunk"][1] - rx["chunk"][0]
        keep = need
        if len(r.values) == need + 1:
            # an inclusive end: each answer runs one word past the range, and the next chunk starts on that word. The unit describes
            # the sample's own end the same way, so the final chunk's extra word is the sample's last one and is kept
            if not rx.get("inclusive_logged"):
                rx["inclusive_logged"] = True
                debug_log.get_logger().info("EnsoniqTransfers: the unit's data range end looks INCLUSIVE (one word more than asked for)")
            if not rx["chunks"]:
                keep = need + 1
        rx["samples"].extend(r.values[:keep])
        self._c.receive_progress.emit(len(rx["samples"]), rx["total"])
        self._after(0, self._rx_next_chunk)

    def _rx_finish(self):
        rx = self._rx
        samples = rx["samples"]
        try:
            sds_encoder.write_wav_file(rx["path"], samples, rx["rate"], 16)
        except (OSError, ValueError) as ex:
            self._fail(f"Couldn't save {rx['name']}: {ex}")
            return
        debug_log.get_logger().info(
            f"EnsoniqTransfers: received {rx['name']!r}: {len(samples)} words @ {rx['rate']} Hz -> {rx['path']}"
        )
        self._c.receive_progress.emit(len(samples), len(samples))
        self._c.status_changed.emit(f"Saved {rx['name']} to {rx['path']}")
        self._c.sample_received.emit(rx["path"])
        self._rx = None
        self._after(0, self._next_receive)

    # -- sending -----------------------------------------------------------------------------------------------
    # Per file: memory check -> first free instrument -> CREATE INSTRUMENT -> for each channel (layer): CREATE LAYER, parameters, TRUNCATE,
    # audio in chunks, final parameters, [pan], verify. State lives in self._tx.

    def _next_file(self):
        if not self._queue:
            self._finish_send()
            return
        entry = self._queue.pop(0)
        path = entry["filepath"]
        base = os.path.splitext(os.path.basename(path))[0]
        index = self._queue_total - len(self._queue)
        self._c.status_changed.emit(f"Sending file {index}/{self._queue_total}: {base}...")
        try:
            channels, framerate = sds_encoder.read_wav_channels(path)
            words, code, rate = prepare_channels(
                channels, framerate, target_rate=entry.get("sample_rate"), force_mono=entry.get("mono", False)
            )
        except (OSError, ValueError) as ex:
            # skipped, not failed: the rest of the queue is unaffected, and the file stays in the Dashboard's queue so it can be fixed
            debug_log.get_logger().warning(f"EnsoniqTransfers: skipping {path!r}: {ex!r}")
            self._skip_file(f"Skipping {os.path.basename(path)}: {ex}")
            return
        name = e.decode_name(e.encode_name((entry.get("name") or base).strip())) or "SAMPLE"
        self._tx = {
            "entry": entry,
            "name": name,
            "channels": words,
            "code": code,
            "rate": rate,
            "inst": None,
            "created": False,
            "layer": 0,
            "total_words": sum(len(ch) for ch in words),
            "done_words": 0,
            "mismatch": False,
        }
        debug_log.get_logger().info(
            f"EnsoniqTransfers: sending {name!r}: {len(words)} channel(s), {len(words[0])} words @ {rate} Hz (rate code {code}) from {path!r}"
        )
        self._tx_check_memory()

    def _skip_file(self, message):
        self._skipped += 1
        self._tx = None
        self._c.status_changed.emit(message)
        self._after(0, self._next_file)

    def _status_message(self, r, what):
        if r.status is not None:
            return f"The Ensoniq refused {what}: {e.status_name(r.status)}. {e.status_text(r.status)}"
        return f"Couldn't finish {what}: {r.error}"

    # memory ---------------------------------------------------------------------------------------------------

    def _tx_check_memory(self):
        self._get_parameter_value(e.PAGE_SYSTEM, e.SYSTEM_ITEM_FREE_BLOCKS, self._tx_got_free_blocks)

    def _tx_got_free_blocks(self, r):
        tx = self._tx
        if r.ok and r.values:
            free = r.values[0]
            need = sum(math.ceil(len(ch) / e.BLOCK_WORDS) for ch in tx["channels"]) + self.memory_margin_blocks
            debug_log.get_logger().info(f"EnsoniqTransfers: free system blocks {free}, this sample needs about {need}")
            if free < need:
                self._skip_file(
                    f"Not enough sample memory on the Ensoniq for {tx['name']} (needs about {need} blocks, {free} free)"
                )
                return
        else:
            # not fatal: the parameter is documented but a unit may not answer it; the unit still refuses with 0C if it really is full
            debug_log.get_logger().info(f"EnsoniqTransfers: couldn't read the free memory ({r.status}, {r.error}) - sending anyway")
        tx["probe"] = 0
        self._tx_probe()

    # the instrument --------------------------------------------------------------------------------------------

    def _tx_probe(self):
        n = self._tx["probe"]
        if n > e.INSTRUMENT_MAX:
            self._fail("All 8 instrument slots on the Ensoniq are in use - delete one on the unit first.")
            return
        self._get_instrument(n, self._tx_probed)

    def _tx_probed(self, r):
        tx = self._tx
        n = tx["probe"]
        if r.status == e.STATUS_INVALID_INSTRUMENT:
            tx["inst"] = n
            debug_log.get_logger().info(f"EnsoniqTransfers: instrument {n + 1} is free")
            self._command(
                e.build_create_instrument(n, self._wire_channel()),
                self._tx_instrument_created,
                f"creating instrument {n + 1}",
            )
            return
        if r.status is not None:
            self._fail(self._status_message(r, f"instrument {n + 1}"))
            return
        tx["probe"] += 1  # it exists (even if its block came damaged): never touch it
        self._after(0, self._tx_probe)

    def _tx_instrument_created(self, r):
        if not r.ok:
            self._fail(self._status_message(r, "the new instrument"))
            return
        self._tx["created"] = True
        self._tx["layer"] = 0
        self._after(self.pace_ms, self._tx_layer_start)

    # one layer per channel ----------------------------------------------------------------------------------------

    def _tx_layer_name(self):
        tx = self._tx
        if len(tx["channels"]) == 1:
            return tx["name"]
        side = "L" if tx["layer"] == 0 else "R"
        return f"{tx['name'][: e.NAME_WORDS - 2]} {side}"

    def _tx_layer_start(self):
        tx = self._tx
        if tx["layer"] >= len(tx["channels"]):
            self._tx_file_done()
            return
        self._command(
            e.build_create_layer(tx["inst"], tx["layer"], self._wire_channel()),
            self._tx_layer_created,
            f"creating layer {tx['layer'] + 1}",
        )

    def _tx_layer_created(self, r):
        if not r.ok:
            self._fail(self._status_message(r, "the new layer"))
            return
        tx = self._tx
        self._get_wavesample_parameters(tx["inst"], tx["layer"], 1, self._tx_got_default_parameters)

    def _tx_put_parameters(self, params, callback, label):
        tx = self._tx
        ch = self._wire_channel()
        self._put(
            e.build_put_wavesample_parameters(tx["inst"], tx["layer"], 1, ch),
            e.build_block_message(params.to_block(), ch),
            callback,
            label,
        )

    def _tx_got_default_parameters(self, r):
        tx = self._tx
        if not r.ok:
            self._fail(self._status_message(r, "the new wavesample's parameters"))
            return
        p = e.WavesampleParams.from_block(r.values)
        debug_log.get_logger().info(
            f"EnsoniqTransfers: new wavesample before the upload: start={p.start} end={p.end} rate code={p.sample_rate_code} "
            f"loop mode={p.loop_mode} key range={p.key_range} name={p.name!r}"
        )
        p.name = self._tx_layer_name()
        p.root_key = DEFAULT_ROOT_KEY
        p.sample_rate_code = tx["code"]
        p.loop_mode = e.LOOP_FORWARD_NO_LOOP
        self._tx_put_parameters(p, self._tx_parameters_put, "the wavesample's name and rate")

    def _tx_parameters_put(self, r):
        if not r.ok:
            self._fail(self._status_message(r, "the wavesample's name and rate"))
            return
        if self.truncate_placeholder:
            tx = self._tx
            self._command(
                e.build_truncate_wavesample(tx["inst"], tx["layer"], 1, self._wire_channel()),
                self._tx_truncated,
                "trimming the placeholder wave",
            )
        else:
            self._after(self.pace_ms, self._tx_data_start)

    def _tx_truncated(self, r):
        if not r.ok:
            self._fail(self._status_message(r, "trimming the placeholder wave"))
            return
        self._after(self.pace_ms, self._tx_data_start)

    # the audio --------------------------------------------------------------------------------------------------

    def _tx_data_start(self):
        tx = self._tx
        words = tx["channels"][tx["layer"]]
        tx["chunks"] = e.chunk_ranges(len(words), self.put_chunk_words)
        tx["first_chunk"] = True
        self._tx_next_chunk()

    def _tx_next_chunk(self):
        tx = self._tx
        if not tx["chunks"]:
            self._after(self.pace_ms, self._tx_final_parameters)
            return
        tx["chunk"] = tx["chunks"].pop(0)
        self._tx_send_chunk()

    def _tx_send_chunk(self):
        tx = self._tx
        a, b = tx["chunk"]
        words = tx["channels"][tx["layer"]][a:b]
        ch = self._wire_channel()
        # a range's end is one past the last word, unless the unit turns out to want an inclusive one (see _tx_chunk_done)
        end = b - 1 if self.put_end_inclusive else b
        self._put(
            e.build_put_wavesample_data(tx["inst"], tx["layer"], 1, a, end, ch),
            e.build_data_message(words, ch),
            self._tx_chunk_done,
            f"audio words {a}-{b}",
        )

    def _tx_chunk_done(self, r):
        tx = self._tx
        a, b = tx["chunk"]
        if not r.ok:
            if tx.get("first_chunk") and not tx.get("flipped") and (r.status in (e.STATUS_NAK, e.STATUS_INVALID_DATA_RANGE) or r.error):
                # the very first chunk refused: the unit may count its range end inclusively (the spec doesn't say) - try that once
                tx["flipped"] = True
                self.put_end_inclusive = not self.put_end_inclusive
                debug_log.get_logger().info(
                    f"EnsoniqTransfers: first audio chunk refused ({r.status}, {r.error}); retrying with an "
                    f"{'inclusive' if self.put_end_inclusive else 'exclusive'} range end"
                )
                self._after(self.pace_ms, self._tx_send_chunk)
                return
            self._fail(self._status_message(r, f"audio words {a}-{b}"))
            return
        tx["first_chunk"] = False
        tx["done_words"] += b - a
        self._c.transfer_progress.emit(tx["done_words"], tx["total_words"])
        self._c.unit_progress.emit(tx["done_words"] / tx["total_words"])
        self._after(self.pace_ms, self._tx_next_chunk)

    # the final parameters, pan, verify ----------------------------------------------------------------------------------

    def _tx_final_parameters(self):
        tx = self._tx
        self._get_wavesample_parameters(tx["inst"], tx["layer"], 1, self._tx_got_parameters_after_audio)

    def _tx_got_parameters_after_audio(self, r):
        tx = self._tx
        if not r.ok:
            self._fail(self._status_message(r, "the wavesample's parameters after the audio"))
            return
        n = len(tx["channels"][tx["layer"]])
        p = e.WavesampleParams.from_block(r.values)
        debug_log.get_logger().info(f"EnsoniqTransfers: after the audio the unit says start={p.start} end={p.end} (we sent {n} words)")
        last = max(1, n + self.end_adjust)
        p.name = self._tx_layer_name()
        p.root_key = DEFAULT_ROOT_KEY
        p.sample_rate_code = tx["code"]
        p.loop_mode = e.LOOP_FORWARD_NO_LOOP
        p.start = 0
        p.end = last
        p.loop_start = 0
        p.set_loop_end(last, 0)
        self._tx_put_parameters(p, self._tx_final_parameters_put, "the wavesample's start and end")

    def _tx_final_parameters_put(self, r):
        if not r.ok:
            self._fail(self._status_message(r, "the wavesample's start and end"))
            return
        tx = self._tx
        if len(tx["channels"]) == 2:
            pan = PAN_LEFT if tx["layer"] == 0 else PAN_RIGHT
            self._put_parameter_value(
                tx["inst"], tx["layer"], 1, PAGE_VOLUME, PAN_ITEM, pan, self._tx_pan_done, f"panning layer {tx['layer'] + 1}"
            )
        else:
            self._after(self.pace_ms, self._tx_verify)

    def _tx_pan_done(self, r):
        if not r.ok:
            self._fail(self._status_message(r, "the pan setting"))
            return
        self._after(self.pace_ms, self._tx_verify)

    def _tx_verify(self):
        tx = self._tx
        self._get_wavesample_parameters(tx["inst"], tx["layer"], 1, self._tx_verify_parameters)

    def _tx_verify_parameters(self, r):
        tx = self._tx
        if r.ok:
            p = e.WavesampleParams.from_block(r.values)
            n = len(tx["channels"][tx["layer"]])
            wanted = (self._tx_layer_name(), tx["code"], DEFAULT_ROOT_KEY, 0)
            got = (p.name, p.sample_rate_code, p.root_key, p.start)
            debug_log.get_logger().info(
                f"EnsoniqTransfers: read back name/rate code/root/start {got}, end={p.end}, loop={p.loop_start}..{p.loop_end} "
                f"(wanted {wanted}, {n} words)"
            )
            if got != wanted:
                tx["mismatch"] = True
        else:
            tx["mismatch"] = True
            debug_log.get_logger().warning(f"EnsoniqTransfers: couldn't read the parameters back ({r.status}, {r.error})")
        words = tx["channels"][tx["layer"]]
        n = len(words)
        v = min(self.verify_words, n - 1)
        # two stretches that exist whatever the unit's end convention is: the first v words, and the v words ending one before the last
        tx["verify"] = [(0, v), (n - 1 - v, n - 1)]
        self._tx_verify_next()

    def _tx_verify_next(self):
        tx = self._tx
        if not tx["verify"]:
            self._tx_layer_done()
            return
        tx["verify_range"] = tx["verify"].pop(0)
        a, b = tx["verify_range"]
        self._get_data(tx["inst"], tx["layer"], 1, a, b, self._tx_verify_data)

    def _tx_verify_data(self, r):
        tx = self._tx
        a, b = tx["verify_range"]
        want = tx["channels"][tx["layer"]][a:b]
        if r.ok and list(r.values[: b - a]) == want:
            self._after(self.pace_ms, self._tx_verify_next)
            return
        tx["mismatch"] = True
        debug_log.get_logger().warning(
            f"EnsoniqTransfers: audio words {a}-{b} read back different or not at all ({r.status}, {r.error})"
        )
        tx["verify"] = []  # one mismatch says it; don't keep reading
        self._after(self.pace_ms, self._tx_layer_done)

    def _tx_layer_done(self):
        tx = self._tx
        tx["layer"] += 1
        self._after(self.pace_ms, self._tx_layer_start)

    def _tx_file_done(self):
        tx, self._tx = self._tx, None
        self._sent += 1
        if tx["mismatch"]:
            self._unverified += 1
            self._c.status_changed.emit(
                f"Sent {tx['name']} to instrument {tx['inst'] + 1}, but it doesn't read back the same - please report it with ~/.akaisds/akaisds.log"
            )
        else:
            self._c.status_changed.emit(f"Sent {tx['name']} to instrument {tx['inst'] + 1} on the Ensoniq")
        self._c.unit_progress.emit(1.0)
        self._c.file_transferred.emit(tx["entry"]["filepath"])
        self._after(self.pace_ms, self._next_file)

    def _finish_send(self):
        sent, skipped, unverified = self._sent, self._skipped, self._unverified
        self._reset()
        parts = [f"Sent {sent} file{'' if sent == 1 else 's'}"]
        if skipped:
            parts.append(f"skipped {skipped}")
        if unverified:
            parts.append(f"{unverified} didn't read back the same")
        self._c.status_changed.emit(", ".join(parts))
        self._c.transfer_finished.emit(sent > 0 or not skipped)
        # show what is on the unit now (same as the other families' post-send refresh)
        self._after(300, lambda: self.refresh_catalog(silent=True))
