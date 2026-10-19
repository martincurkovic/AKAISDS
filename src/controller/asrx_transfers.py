"""Ensoniq ASR-X transfers: list the samples, receive them as WAV files, send WAV files as new samples.

Owned by `SamplerController`, which delegates here when the Sampler Type is `ensoniq_asrx` (protocol family "ensoniq", this engine instead of
`controller/ensoniq_transfers.py`). It reports through the controller's own signals, so the Dashboard needs nothing ASR-X specific beyond the sparse sample list
(`sample_slots_updated`). The wire format is `core/asrx_sysex.py`; this module is the event-driven choreography (Qt timers + `sysex_received`, one GUI thread,
ONE request outstanding at a time, like `controller/ensoniq_transfers.py` and `controller/s950_transfers.py`).

**NOTHING HERE HAS BEEN RUN AGAINST AN ASR-X.** It is built from a static reading of the v2.67 ROM (dev_docs/asrx-rom-findings.md, tests/asrx_test_plan.md). It
is deliberately chatty in ~/.akaisds/akaisds.log (search for `AsrxTransfers`) so one tester log can settle what is wrong.

What it does:
 * LIST    `F0 7E <dev> 05 04 <slot> F7` for slot 0, 1, 2 ... (the standard SDS-extension "sample name request"). A name comes back for a sample, `7F 41` for an
           empty slot. The scan stops after `scan_empty_run` empty slots in a row. (Two internal recording buffers also answer `7F 41`; see _tx_find_slot.)
 * RECEIVE the sample object (`SMP1`: 52-byte header + signed 16-bit big-endian audio) read in pieces with command 04 (`receive_mode` "auto"). The first piece is the
           header, which says how long it is. If the unit stays silent to command 04 the whole object is asked for with command 01 instead. Audio is saved as a WAV at
           the rate the header's pitch word encodes.
 * SEND    each file (a stereo file becomes two samples, NAME-L and NAME-R) is built as an `SMP1` object and uploaded with command 02 into the first empty slot, in ONE
           SysEx message. The unit acknowledges (reply 42) after it has committed it. Afterwards the sample is read back (name, header, tail of the audio) and compared.
           Nothing is ever overwritten or deleted, and there is no way to delete over MIDI here: a failed send can leave a half-written slot.

Never sent from here: command 05 (partial write), commands 07/10/11/12/13/15 (they clear or replace whole banks, or jump into service code).
"""

import collections
import json
import os
import time

from PySide6.QtCore import QObject, QTimer

from core import asrx_sysex as a
from core import debug_log, sds_encoder, sysex_trace

MIDI_BYTES_PER_SECOND = 3125  # 31,250 baud, 10 bits a byte

_BUSY_OPS = ("receive", "send")

#: an upload or download is slower than this fraction of the nominal wire speed (USB interfaces are often faster, an old MIDI-USB cable is not)
WIRE_SLACK = 1.35


class _Res:
    """What a request ended with: `message` (the matching reply), `timeout` (nothing answered) or `error` (a 7F reply, as its Message)."""

    def __init__(self, message=None, timeout=False, error=None, elapsed=0.0):
        self.message, self.timeout, self.error, self.elapsed = message, timeout, error, elapsed

    @property
    def ok(self):
        return self.message is not None


class _Request:
    def __init__(self, label, expect, callback, *, accept_error=True, timeout_is_result=False):
        self.label = label
        self.expect = expect  # Message -> bool: does this reply answer us?
        self.callback = callback
        self.accept_error = accept_error  # a 7F error ends the request (as _Res.error)
        self.timeout_is_result = timeout_is_result  # False: a timeout ends the whole operation unless the callback handles it
        self.started = time.monotonic()


def _hex(data, limit=32):
    data = bytes(data)
    return data[:limit].hex(" ") + (" ..." if len(data) > limit else "")


def wire_seconds(message_bytes):
    return (message_bytes + 2) / MIDI_BYTES_PER_SECOND


class AsrxTransfers(QObject):
    IS_ASRX = True

    _UNEXPECTED_WARNING_COUNT = 10
    _UNEXPECTED_LOG_LIMIT = 40

    def __init__(self, controller):
        super().__init__()
        self._c = controller

        # tuning knobs - attributes so tests (and a tester with a debugger) can change them
        self.reply_timeout_ms = 2500
        #: a request whose "no reply" means "free" or "not supported" (the free-slot probe, the first range read)
        self.probe_timeout_ms = 1500
        #: a scan stops after this many empty slots in a row (the unit gives a new sample the lowest free slot, so holes are rare)
        self.scan_empty_run = 32
        self.scan_slot_limit = a.SLOT_LIMIT
        #: bytes per range read. 640 keeps a reply under 1024 bytes on the wire (python-rtmidi's default message buffer on some platforms)
        self.range_chunk_bytes = 640
        self.chunk_retries = 2
        #: "auto": range reads, falling back to one whole-object download if command 04 is never answered; "chunked": range reads only; "whole": command 01 only
        self.receive_mode = "auto"
        #: how long a whole-object download may take when its size isn't known (a header read failed), seconds
        self.whole_timeout_unknown_s = 900
        self.verify_reread = True
        self.send_verify = True
        #: seconds added to the estimated wire time before an upload is called unanswered
        self.ack_margin_s = 20.0
        #: free-slot candidates tried (each unanswered probe costs probe_timeout_ms)
        self.slot_candidates = 8
        self.root_key = a.DEFAULT_ROOT_KEY
        self.pace_ms = 20
        #: log every message in full (capped at 4 KB each). AKAISDS_ASRX_TRACE=1 turns it on; very noisy, for a debugging session
        self.trace_full = bool(os.environ.get("AKAISDS_ASRX_TRACE"))

        #: last list: [{"slot", "name", "label"}]
        self.entries = []
        self.occupied = None  # set of slots that answered with a name, None until the first scan
        self.hidden = set()  # slots that answered a range read but have no name (internal recording buffers)

        #: (Device ID in Settings, Device ID the unit really answered with) once the broadcast fallback found a unit set to another ID
        self._found_device = None

        self._op = None
        self._x = None
        self._silent = False
        self._step_fn = None
        self._unexpected = 0
        self._started = 0.0
        self._stats = {"messages_out": 0, "messages_in": 0, "bytes_out": 0, "bytes_in": 0, "timeouts": 0, "retries": 0}
        self._used_slots = set()

        self._scan = None
        self._scan_cont = None
        self._queue = []
        self._queue_total = 0
        self._rx = None
        self._tx = None
        self._sent = self._skipped = self._unverified = 0
        self._ignore_until = 0.0

        #: the last messages in both directions, dumped to the log when something fails
        self._recent = collections.deque(maxlen=12)
        self._budget = sysex_trace.LineBudget(max_lines=300)

        self._reply_timer = self._make_timer(self._on_reply_timeout)
        self._step_timer = self._make_timer(self._run_step)
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(500)
        self._progress_timer.timeout.connect(self._on_progress_tick)

        #: {offset: byte} written over the header of every upload (offsets 0x08-0x33 only). Empty unless ~/.akaisds/asrx.json says otherwise
        self.header_patch = {}
        self._load_overrides()

        debug_log.get_logger().info(
            f"AsrxTransfers: engine ready (receive_mode={self.receive_mode}, chunk={self.range_chunk_bytes} B, scan_empty_run={self.scan_empty_run}, "
            f"verify={self.send_verify}/{self.verify_reread}, root_key={self.root_key}, header_patch={self.header_patch}, trace_full={self.trace_full})"
        )

    #: knobs ~/.akaisds/asrx.json may set, with the type each must have. A tester can be told "create this file" to change behaviour without a new build.
    OVERRIDABLE = {
        "reply_timeout_ms": int,
        "probe_timeout_ms": int,
        "scan_empty_run": int,
        "range_chunk_bytes": int,
        "chunk_retries": int,
        "receive_mode": str,
        "whole_timeout_unknown_s": int,
        "verify_reread": bool,
        "send_verify": bool,
        "ack_margin_s": float,
        "slot_candidates": int,
        "root_key": int,
        "pace_ms": int,
        "trace_full": bool,
    }

    def _load_overrides(self):
        path = debug_log.LOG_PATH.parent / "asrx.json"
        log = self._log()
        try:
            if not path.exists():
                return
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError) as ex:
            log.warning(f"AsrxTransfers: couldn't read {path}: {ex}")
            return
        if not isinstance(raw, dict):
            log.warning(f"AsrxTransfers: {path} must hold a JSON object")
            return
        applied = {}
        for key, value in raw.items():
            if key == "header_patch" and isinstance(value, dict):
                patch = {}
                for offset, byte in value.items():
                    try:
                        o, b = int(str(offset), 0), int(byte)
                    except ValueError:
                        log.warning(f"AsrxTransfers: bad header_patch entry {offset!r}: {byte!r}")
                        continue
                    if 8 <= o < a.HEADER_BYTES and 0 <= b <= 255:
                        patch[o] = b
                    else:
                        log.warning(f"AsrxTransfers: header_patch offset {o} / value {b} refused (offsets 8-0x33, bytes 0-255)")
                self.header_patch = patch
                applied[key] = patch
                continue
            kind = self.OVERRIDABLE.get(key)
            if kind is None:
                log.warning(f"AsrxTransfers: unknown setting {key!r} in {path.name} ignored")
                continue
            if kind is bool:
                valid = isinstance(value, bool)
            elif kind is str:
                valid = isinstance(value, str)
            else:
                valid = isinstance(value, (int, float)) and not isinstance(value, bool)
            if not valid:
                log.warning(f"AsrxTransfers: setting {key!r} needs a {kind.__name__}, got {value!r}")
                continue
            try:
                value = kind(value)
            except (TypeError, ValueError):
                log.warning(f"AsrxTransfers: setting {key!r} needs a {kind.__name__}, got {value!r}")
                continue
            if key == "receive_mode" and value not in ("auto", "chunked", "whole"):
                log.warning(f"AsrxTransfers: receive_mode must be auto, chunked or whole, not {value!r}")
                continue
            setattr(self, key, value)
            applied[key] = value
        log.info(f"AsrxTransfers: settings taken from {path}: {applied}")

    def _make_timer(self, slot):
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(slot)
        return timer

    @staticmethod
    def _log():
        return debug_log.get_logger()

    # -- public surface (what SamplerController delegates to) ---------------------------------------------

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
            self._c.status_changed.emit("Reading the ASR-X's sample list...")
        self._scan_start(self._catalog_done)

    def receive_samples(self, requests):
        if not requests:
            return
        if not self._begin("receive", "Receiving samples"):
            return
        self._queue = list(requests)
        self._queue_total = len(self._queue)
        self._next_receive()

    def send_file_queue(self, entries):
        """Send each file as one new sample (two for a stereo file). Returns True if it started."""
        if not entries:
            return False
        if not self._begin("send", "Sending samples"):
            return False
        self._queue = list(entries)
        self._queue_total = len(self._queue)
        self._sent = self._skipped = self._unverified = 0
        self._tx = None
        self._used_slots = set()
        self._scan_start(self._send_scan_done)
        return True

    def cancel(self):
        """Abort whatever is running. Returns True if something was. A message already on its way to the unit can't be recalled."""
        if self._op is None:
            return False
        op = self._op
        pending_upload = op == "send" and self._tx is not None and self._tx.get("uploading")
        self._log().info(f"AsrxTransfers: {op} cancelled by the user (upload in flight: {pending_upload})")
        self._stop_timers()
        self._x = None
        self._queue = []
        self._reset()
        # the ASR-X goes on receiving what was already sent, and answers it: that late ACK must not be reported as stray
        self._ignore_until = time.monotonic() + 600
        if op == "receive":
            self._c.status_changed.emit("Transfer cancelled")
            self._c.receive_finished.emit(False)
        elif op == "send":
            self._c.status_changed.emit(
                "Transfer cancelled" + (" - the ASR-X is still taking in the sample already on its way; wait for it to finish" if pending_upload else "")
            )
            self._c.transfer_finished.emit(False)
        return True

    def handle_sysex(self, data):
        """Every SysEx the controller receives while the Ensoniq family is selected."""
        try:
            self._dispatch(bytes(data))
        except Exception as ex:
            self._log().error("AsrxTransfers: unhandled exception while handling SysEx", exc_info=True)
            self._fail(f"Unexpected error: {ex}")

    # -- plumbing -----------------------------------------------------------------------------------------------

    def _device(self):
        """The Device ID requests go to: the one in Settings, or the one the broadcast fallback found for that setting."""
        configured = self._c.channel & 0x7F
        if self._found_device is not None and self._found_device[0] == configured:
            return self._found_device[1]
        return configured

    def _begin(self, op, what, silent=False):
        if self._op is not None:
            if not silent:
                self._c.status_changed.emit("A transfer is already in progress - please wait for it to finish")
            return False
        if self._c.is_open_loop():
            if not silent:
                self._c.status_changed.emit(f"{what} needs a MIDI input - the ASR-X answers on it. Select one in Settings.")
            return False
        self._op = op
        self._started = time.monotonic()
        self._stats = {"messages_out": 0, "messages_in": 0, "bytes_out": 0, "bytes_in": 0, "timeouts": 0, "retries": 0}
        midi = self._c.midi_manager
        self._log().info(
            f"AsrxTransfers: begin {op} ({what}) device id {self._device()} in={getattr(midi, 'input_name', None)!r} out={getattr(midi, 'output_name', None)!r}"
        )
        return True

    def _reset(self):
        self._op = None
        self._x = None
        self._rx = None
        self._tx = None
        self._scan = None
        self._scan_cont = None
        self._step_fn = None
        self._progress_timer.stop()

    def _stop_timers(self):
        self._reply_timer.stop()
        self._step_timer.stop()
        self._progress_timer.stop()

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
            self._log().error("AsrxTransfers: unhandled exception in a transfer step", exc_info=True)
            self._fail(f"Unexpected error: {ex}")

    def _note(self, direction, data, extra=""):
        data = bytes(data)
        self._recent.append((time.monotonic(), direction, _hex(data, 40), len(data), extra))
        if self.trace_full:
            self._log().info(f"AsrxTransfers: TRACE {direction} {len(data)} B: {data[:4096].hex(' ')}")
        elif self._budget.allow():
            self._log().debug(f"AsrxTransfers: {direction} {_hex(data)} ({len(data)} B){' ' + extra if extra else ''}")

    def _dump_recent(self, why):
        log = self._log()
        log.warning(f"AsrxTransfers: last messages before: {why}")
        now = time.monotonic()
        for t, direction, hexed, n, extra in self._recent:
            log.warning(f"AsrxTransfers:   -{now - t:6.2f}s {direction:>3} {hexed} ({n} B) {extra}")

    def _send(self, data):
        data = bytes(data)
        self._note("out", data)
        try:
            self._c.midi_manager.send_sysex(data)
        except Exception as ex:
            self._log().error(f"AsrxTransfers: MIDI send failed ({len(data)} bytes)", exc_info=True)
            self._fail(f"Couldn't send to the ASR-X: {ex}")
            return False
        self._stats["messages_out"] += 1
        self._stats["bytes_out"] += len(data)
        return True

    def _fail(self, message):
        message += self._partial_note()
        log = self._log()
        log.warning(f"AsrxTransfers: {self._op} FAILED after {time.monotonic() - self._started:.1f}s: {message}")
        log.warning(f"AsrxTransfers: stats {self._stats}, occupied={sorted(self.occupied) if self.occupied is not None else None}, hidden={sorted(self.hidden)}")
        self._dump_recent(message)
        op, silent = self._op, self._silent
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
        tx = self._tx
        if self._op == "send" and tx and tx.get("uploading") and tx.get("slot") is not None:
            return f" Slot {tx['slot']} on the ASR-X may hold a partly written sample - check it on the unit."
        return ""

    # -- one request/reply --------------------------------------------------------------------------------------

    def _ask(self, label, message, expect, callback, *, timeout_ms=None, accept_error=True, timeout_is_result=False):
        self._x = _Request(label, expect, callback, accept_error=accept_error, timeout_is_result=timeout_is_result)
        self._reply_timer.start(int(self.reply_timeout_ms if timeout_ms is None else timeout_ms))
        if not self._send(message):
            self._reply_timer.stop()

    def _on_reply_timeout(self):
        x, self._x = self._x, None
        if x is None:
            return
        elapsed = time.monotonic() - x.started
        self._stats["timeouts"] += 1
        if x.timeout_is_result:
            self._log().info(f"AsrxTransfers: no answer to {x.label} after {elapsed:.1f}s (treated as a result)")
            x.callback(_Res(timeout=True, elapsed=elapsed))
            return
        self._log().warning(f"AsrxTransfers: no answer to {x.label} after {elapsed:.1f}s")
        x.callback(_Res(timeout=True, elapsed=elapsed))

    def _dispatch(self, data):
        if not data:
            return
        now = time.monotonic()
        try:
            message = a.parse_message(data)
        except a.AsrxError as ex:
            self._note("in", data, f"UNREADABLE: {ex}")
            self._log().warning(f"AsrxTransfers: unreadable message ({len(data)} B, {ex}): {_hex(data, 64)}")
            return
        self._stats["messages_in"] += 1
        self._stats["bytes_in"] += len(data)
        self._note("in", data, f"[{message.kind}]")
        x = self._x
        if x is None:
            self._note_unexpected(data, message, "no request is waiting", late=now < self._ignore_until)
            return
        if message.kind == "error" and x.accept_error:
            elapsed = now - x.started
            self._reply_timer.stop()
            self._x = None
            self._log().info(f"AsrxTransfers: {x.label} answered with error 7F {message.error_code:02X} after {elapsed:.2f}s")
            x.callback(_Res(error=message, elapsed=elapsed))
            return
        if x.expect(message):
            elapsed = now - x.started
            self._reply_timer.stop()
            self._x = None
            x.callback(_Res(message=message, elapsed=elapsed))
            return
        self._note_unexpected(data, message, f"while waiting for {x.label}")

    def _note_unexpected(self, data, message, why, late=False):
        self._unexpected += 1
        if self._unexpected <= self._UNEXPECTED_LOG_LIMIT:
            level = self._log().debug if late else self._log().info
            level(f"AsrxTransfers: ignoring unexpected message [{message.kind}] ({why}): {_hex(data, 48)} ({len(data)} B) (#{self._unexpected})")
        if self._unexpected == self._UNEXPECTED_WARNING_COUNT:
            self._log().warning(
                f"AsrxTransfers: {self._unexpected} unexpected messages this session - late replies do this, but so does another MIDI program echoing the sampler's replies back to it"
            )

    # -- requests, with the matching rule for each -----------------------------------------------------------------

    def _ask_name(self, slot, callback, *, label=None, device=None):
        self._ask(
            label or f"the name of slot {slot}",
            a.build_name_request(self._device() if device is None else device, slot),
            lambda m, s=slot: m.kind == "name" and m.slot == s,
            callback,
        )

    def _ask_range(self, slot, offset, length, callback, *, label=None, timeout_ms=None, timeout_is_result=True):
        def expect(m, s=slot, o=offset, n=length):
            return m.kind == "range" and m.slot == s and m.offset == o and m.length == n

        self._ask(
            label or f"{length} bytes at {offset} of slot {slot}",
            a.build_read_range(self._device(), slot, offset, length),
            expect,
            callback,
            timeout_ms=timeout_ms,
            timeout_is_result=timeout_is_result,
        )

    def _ask_object(self, slot, callback, *, timeout_ms=None):
        self._ask(
            f"the whole object of slot {slot}",
            a.build_get_object(self._device(), slot),
            lambda m, s=slot: m.kind == "object" and m.slot == s,
            callback,
            timeout_ms=timeout_ms,
        )

    # -- listing -----------------------------------------------------------------------------------------------

    def _scan_start(self, continuation):
        self._scan = {"slot": 0, "empty_run": 0, "found": [], "probes": 0, "timeouts": 0, "t0": time.monotonic(), "highest": -1}
        self._scan_cont = continuation
        self._scan_next()

    def _scan_next(self):
        s = self._scan
        if s["slot"] >= self.scan_slot_limit or s["empty_run"] >= self.scan_empty_run:
            self._scan_finish()
            return
        s["probes"] += 1
        self._ask_name(s["slot"], self._scan_got)

    def _scan_got(self, r):
        s = self._scan
        slot = s["slot"]
        if r.timeout:
            s["timeouts"] += 1
            self._stats["retries"] += 1
            if s["timeouts"] >= 2:
                if s["found"]:
                    self._fail(f"The ASR-X stopped answering while its sample list was being read (slot {slot})")
                elif self._device() != a.BROADCAST_DEVICE and not s.get("broadcast_tried"):
                    self._scan_try_broadcast(slot)
                else:
                    self._fail_silent_unit()
                return
            self._after(self.pace_ms, self._scan_next)  # one more try for the same slot
            return
        s["timeouts"] = 0
        if r.ok:
            s["found"].append((slot, r.message.name))
            s["empty_run"] = 0
            s["highest"] = slot
            if r.message.device != self._device() and not s.get("device_noted"):
                s["device_noted"] = True
                self._log().info(
                    f"AsrxTransfers: the unit answers with device id {r.message.device} (we used {self._device()})"
                    + (" - normal for a broadcast request" if self._device() == a.BROADCAST_DEVICE else " - the replies don't carry the id we sent")
                )
        else:
            s["empty_run"] += 1
            if r.error.error_code != a.ERROR_NO_SUCH_OBJECT:
                self._log().warning(f"AsrxTransfers: slot {slot} answered with an unexpected error code 0x{r.error.error_code:02X}")
        s["slot"] += 1
        self._after(0, self._scan_next)

    def _fail_silent_unit(self, tried_broadcast=False):
        self._fail(
            "No reply from the ASR-X. Check it's on, SysEx Recv is on and its SysEx Device ID matches the Device ID in Settings "
            "(System/MIDI page), both MIDI cables are connected, and no other MIDI program has the ports open"
            + (" (Device ID 127, all devices, got no reply either)" if tried_broadcast else "")
        )

    def _scan_try_broadcast(self, slot):
        """Nothing answered the Device ID in Settings: ask once more as Device ID 127 (all devices). Read only. A unit set to another ID answers it
        and its reply names the ID it really has, which is then used for the rest of the session."""
        self._scan["broadcast_tried"] = True
        self._log().warning(
            f"AsrxTransfers: no answer on Device ID {self._device()}; asking the name of slot {slot} as Device ID {a.BROADCAST_DEVICE} (all devices) in case the unit has another ID"
        )
        self._ask_name(slot, self._scan_got_broadcast, label=f"the name of slot {slot} (Device ID {a.BROADCAST_DEVICE})", device=a.BROADCAST_DEVICE)

    def _scan_got_broadcast(self, r):
        if r.timeout:
            self._stats["retries"] += 1
            self._fail_silent_unit(tried_broadcast=True)
            return
        configured = self._device()
        unit = r.message.device
        if unit != configured and unit != a.BROADCAST_DEVICE:
            self._found_device = (self._c.channel & 0x7F, unit)
            self._log().warning(
                f"AsrxTransfers: the unit answered Device ID {a.BROADCAST_DEVICE} with its own Device ID {unit}, not {configured} (Settings); using {unit} until the setting changes"
            )
            if not self._silent:
                self._c.status_changed.emit(
                    f"The ASR-X's SysEx Device ID is {unit}, not {configured}. Using {unit}; set it in Settings to keep it."
                )
        else:
            self._log().warning(f"AsrxTransfers: Device ID {a.BROADCAST_DEVICE} was answered with Device ID {unit}; the earlier requests were probably lost")
        self._scan["timeouts"] = 0
        self._scan_got(r)

    def _scan_finish(self):
        s, cont = self._scan, self._scan_cont
        elapsed = time.monotonic() - s["t0"]
        names = [name.upper() for _slot, name in s["found"]]
        entries = []
        for slot, name in s["found"]:
            label = name if names.count(name.upper()) == 1 else f"{name} ({slot})"
            entries.append({"slot": slot, "name": name, "label": label or f"Sample {slot}"})
        self.entries = entries
        self.occupied = {e["slot"] for e in entries}
        log = self._log()
        log.info(
            f"AsrxTransfers: scan done in {elapsed:.1f}s: {len(entries)} sample(s) in {s['probes']} probe(s), highest slot {s['highest']}, "
            f"stopped at slot {s['slot']} (empty run {s['empty_run']}/{self.scan_empty_run})"
        )
        for e in entries:
            log.info(f"AsrxTransfers:   slot {e['slot']}: {e['name']!r}")
        self._scan = None
        self._scan_cont = None
        self._c.sample_slots_updated.emit([(e["slot"], e["label"]) for e in entries])
        # nothing in the ROM reading says how to ask for memory: an empty total tells the Dashboard "unknown"
        self._c.memory_status_updated.emit({"max_num_samp_words": 0, "num_words_free": 0, "num_blocks_free": 0})
        cont(entries)

    def _catalog_done(self, entries):
        silent = self._silent
        self._silent = False
        self._reset()
        if not silent:
            n = len(entries)
            self._c.status_changed.emit(f"{n} sample{'' if n == 1 else 's'} on the ASR-X")

    # -- receiving ---------------------------------------------------------------------------------------------

    def _entry_for(self, slot):
        for e in self.entries:
            if e["slot"] == slot:
                return e
        return None

    def _next_receive(self):
        if not self._queue:
            self._finish_op_summary("receive")
            self._reset()
            self._c.status_changed.emit("All samples received")
            self._c.receive_finished.emit(True)
            return
        number, path = self._queue.pop(0)
        entry = self._entry_for(number)
        name = entry["label"] if entry else f"slot {number}"
        index = self._queue_total - len(self._queue)
        self._c.status_changed.emit(f"Receiving {name} ({index}/{self._queue_total})...")
        self._rx = {"slot": number, "path": path, "name": name, "t0": time.monotonic()}
        self._log().info(f"AsrxTransfers: receiving slot {number} ({name!r}) -> {path!r}")
        self._fetch(number, self._rx_got_object)

    def _rx_progress(self, done_bytes, total_bytes):
        if total_bytes:
            self._c.receive_progress.emit(max(0, (done_bytes - a.HEADER_BYTES) // 2), max(1, (total_bytes - a.HEADER_BYTES) // 2))

    def _rx_got_object(self, f):
        rx = self._rx
        if f.get("error"):
            self._fail(f"Couldn't read {rx['name']} from the ASR-X: {f['error']}")
            return
        data = f["data"]
        try:
            obj = a.parse_sample_object(data, slot=rx["slot"])
        except a.AsrxError as ex:
            self._log().warning(f"AsrxTransfers: slot {rx['slot']} isn't a readable sample: {ex}; first bytes {_hex(data, 64)}")
            self._fail(f"{rx['name']} came back in a form the app doesn't understand ({ex}) - please send the log")
            return
        log = self._log()
        log.info(f"AsrxTransfers: slot {rx['slot']} header: {_hex(obj.header, 52)}")
        log.info(f"AsrxTransfers: slot {rx['slot']} decoded: {a.describe_header(obj.header)}; {len(obj.samples)} samples; fetched {f['how']}")
        rx["obj"], rx["fetch"] = obj, f
        if not obj.samples and obj.shares_audio and obj.parent != rx["slot"]:
            log.info(f"AsrxTransfers: slot {rx['slot']} shares the audio of slot {obj.parent}; reading that")
            self._c.status_changed.emit(f"{rx['name']} shares another sample's audio - reading that...")
            self._fetch(obj.parent, self._rx_got_parent)
            return
        self._rx_save(obj.samples)

    def _rx_got_parent(self, f):
        rx = self._rx
        if f.get("error"):
            self._fail(f"Couldn't read the audio {rx['name']} shares: {f['error']}")
            return
        try:
            parent = a.parse_sample_object(f["data"], slot=rx["obj"].parent)
        except a.AsrxError as ex:
            self._fail(f"The audio {rx['name']} shares came back in a form the app doesn't understand ({ex})")
            return
        self._rx_save(parent.samples)

    def _rx_save(self, samples):
        rx = self._rx
        obj, f = rx["obj"], rx["fetch"]
        if not samples:
            self._fail(f"{rx['name']} holds no audio")
            return
        rate = max(1, round(obj.rate_hz))
        try:
            sds_encoder.write_wav_file(rx["path"], samples, rate, 16)
        except (OSError, ValueError) as ex:
            self._fail(f"Couldn't save {rx['name']}: {ex}")
            return
        elapsed = time.monotonic() - rx["t0"]
        notes = []
        if f.get("checksum_bad"):
            notes.append("the checksum did not match")
        if f.get("reread_mismatch"):
            notes.append("a second read of the first piece differed")
        if obj.loop_mode in (a.LOOP_FORWARD, a.LOOP_ALTERNATING):
            notes.append(f"loop {obj.loop_start:g}-{obj.end:g} not saved in the WAV")
        self._log().info(
            f"AsrxTransfers: received slot {rx['slot']} {rx['name']!r}: {len(samples)} samples @ {rate} Hz (pitch word 0x{obj.pitch_word:04X}, root {obj.root_key}) "
            f"in {elapsed:.1f}s via {f['how']} -> {rx['path']}" + (f" NOTES: {'; '.join(notes)}" if notes else "")
        )
        self._c.receive_progress.emit(len(samples), len(samples))
        self._c.status_changed.emit(f"Saved {rx['name']} to {rx['path']}" + (f" ({'; '.join(notes)})" if notes else ""))
        self._c.sample_received.emit(rx["path"])
        self._rx = None
        self._after(self.pace_ms, self._next_receive)

    # fetching one whole object --------------------------------------------------------------------------------
    # f = {"slot", "mode", "how", "size", "data" (bytearray), "offset", "retries", "chunks_ok", "callback", "t0"}

    def _fetch(self, slot, callback):
        f = {"slot": slot, "callback": callback, "t0": time.monotonic(), "retries": 0, "chunks_ok": 0, "data": bytearray(), "how": None, "size": None}
        mode = self.receive_mode
        if mode == "whole":
            self._fetch_whole(f)
        else:
            self._fetch_header(f)

    def _fetch_done(self, f, error=None):
        f["error"] = error
        f["data"] = bytes(f["data"])
        if not error:
            self._log().info(
                f"AsrxTransfers: fetched slot {f['slot']}: {len(f['data'])} B via {f['how']} in {time.monotonic() - f['t0']:.1f}s "
                f"({f['retries']} retried piece(s))"
            )
        f["callback"](f)

    def _fetch_header(self, f):
        # the header read doubles as a probe: if command 04 is answered at all, it also tells how big the object is
        self._ask_range(
            f["slot"], 0, a.HEADER_BYTES, lambda r, f=f: self._fetch_got_header(f, r), label=f"the header of slot {f['slot']}", timeout_ms=self.probe_timeout_ms
        )

    def _fetch_got_header(self, f, r):
        slot = f["slot"]
        if r.error is not None:
            self._fetch_done(f, f"slot {slot} is empty (7F {r.error.error_code:02X}) - refresh the list")
            return
        if r.timeout:
            if self.receive_mode == "auto":
                self._log().warning(
                    f"AsrxTransfers: command 04 (range read) got no answer for slot {slot}; falling back to a whole-object download (command 01)"
                )
                self._fetch_whole(f)
            else:
                self._fetch_done(f, "the ASR-X didn't answer the range read")
            return
        m = r.message
        header = m.payload
        try:
            info = a.parse_sample_object(header, header_only=True)
        except a.AsrxError as ex:
            self._log().warning(f"AsrxTransfers: slot {slot} header read isn't a sample header: {ex}; {_hex(header, 52)}")
            self._fetch_done(f, f"that isn't a sample the app understands ({ex})")
            return
        size = info.size
        if size > a.HEADER_BYTES + 2 * a.MAX_SAMPLES:
            self._fetch_done(f, f"it claims to be {size:,} bytes, which is more than the app will read")
            return
        f["size"], f["how"] = size, "range reads"
        f["data"] = bytearray(header)
        f["offset"] = a.HEADER_BYTES
        self._log().info(f"AsrxTransfers: slot {slot}: {size} bytes ({(size - a.HEADER_BYTES) // 2} samples), header answered in {r.elapsed:.2f}s")
        self._rx_progress(a.HEADER_BYTES, size)
        self._after(0, lambda: self._fetch_next_chunk(f))

    def _fetch_next_chunk(self, f):
        size, offset = f["size"], f["offset"]
        if offset >= size:
            self._fetch_verify(f)
            return
        n = min(self.range_chunk_bytes, size - offset)
        f["chunk"] = (offset, n)
        f["chunk_try"] = 0
        self._fetch_ask_chunk(f)

    def _fetch_ask_chunk(self, f):
        offset, n = f["chunk"]
        self._ask_range(
            f["slot"], offset, n, lambda r, f=f: self._fetch_got_chunk(f, r), label=f"bytes {offset}-{offset + n} of slot {f['slot']}", timeout_ms=self.reply_timeout_ms
        )

    def _fetch_got_chunk(self, f, r):
        offset, n = f["chunk"]
        if not r.ok:
            f["chunk_try"] += 1
            f["retries"] += 1
            self._stats["retries"] += 1
            why = "no answer" if r.timeout else f"error 7F {r.error.error_code:02X}"
            if f["chunk_try"] <= self.chunk_retries:
                self._log().info(f"AsrxTransfers: asking for bytes {offset}-{offset + n} of slot {f['slot']} again ({why}), try {f['chunk_try'] + 1}")
                self._after(self.pace_ms * 5, lambda: self._fetch_ask_chunk(f))
                return
            if f["chunks_ok"] == 0 and self.receive_mode == "auto":
                self._log().warning("AsrxTransfers: range reads of the audio never answered; falling back to a whole-object download")
                f["data"] = bytearray()
                self._fetch_whole(f)
                return
            self._fetch_done(f, f"the ASR-X stopped answering at byte {offset} of {f['size']}")
            return
        f["data"] += r.message.payload
        f["offset"] = offset + n
        f["chunks_ok"] += 1
        self._rx_progress(f["offset"], f["size"])
        self._after(0, lambda: self._fetch_next_chunk(f))

    def _fetch_verify(self, f):
        # read the first piece of audio again: a different answer means the offsets or the unit's data are not what we think
        if not self.verify_reread or f["size"] <= a.HEADER_BYTES:
            self._fetch_done(f)
            return
        n = min(self.range_chunk_bytes, f["size"] - a.HEADER_BYTES)
        self._ask_range(
            f["slot"],
            a.HEADER_BYTES,
            n,
            lambda r, f=f, n=n: self._fetch_got_verify(f, r, n),
            label=f"the first {n} audio bytes of slot {f['slot']} again",
            timeout_ms=self.reply_timeout_ms,
        )

    def _fetch_got_verify(self, f, r, n):
        if r.ok:
            first = bytes(f["data"][a.HEADER_BYTES : a.HEADER_BYTES + n])
            same = r.message.payload == first
            f["reread_mismatch"] = not same
            self._log().info(f"AsrxTransfers: re-read of the first audio piece of slot {f['slot']}: {'identical' if same else 'DIFFERENT'}")
        else:
            self._log().info("AsrxTransfers: re-read of the first audio piece got no answer (ignored)")
        self._fetch_done(f)

    def _fetch_whole(self, f):
        slot = f["slot"]
        size = f.get("size")
        if size:
            timeout_s = wire_seconds(a.payload_septet_count(size) + 24) * WIRE_SLACK + 15
        else:
            timeout_s = self.whole_timeout_unknown_s
        f["how"] = "whole-object download"
        self._log().info(f"AsrxTransfers: asking for the whole object of slot {slot} (command 01), waiting up to {timeout_s:.0f}s")
        self._c.status_changed.emit("Waiting for the ASR-X to send the whole sample (this can take several minutes)...")
        self._ask_object(slot, lambda r, f=f: self._fetch_got_whole(f, r), timeout_ms=int(timeout_s * 1000))

    def _fetch_got_whole(self, f, r):
        slot = f["slot"]
        if r.error is not None:
            self._fetch_done(f, f"slot {slot} is empty (7F {r.error.error_code:02X}) - refresh the list")
            return
        if r.timeout:
            self._fetch_done(f, "no answer to the whole-object request")
            return
        m = r.message
        f["data"] = bytearray(m.payload)
        f["size"] = m.size
        if not m.checksum_ok:
            f["checksum_bad"] = True
            self._log().warning(
                f"AsrxTransfers: checksum mismatch on slot {slot}: sent 0x{m.checksum_sent:04X}, computed 0x{a.checksum(a.pack_payload(m.payload)):04X} - "
                "kept anyway (the checksum rule is a reading of the ROM)"
            )
        self._fetch_done(f)

    # -- sending -----------------------------------------------------------------------------------------------

    def _send_scan_done(self, _entries):
        self._after(0, self._next_file)

    def _base_name(self, entry):
        base = os.path.splitext(os.path.basename(entry["filepath"]))[0]
        return (entry.get("name") or base).strip() or "SAMPLE"

    def _next_file(self):
        if not self._queue:
            self._finish_send()
            return
        entry = self._queue.pop(0)
        path = entry["filepath"]
        index = self._queue_total - len(self._queue)
        base = self._base_name(entry)
        self._c.status_changed.emit(f"Sending file {index}/{self._queue_total}: {base}...")
        try:
            channels, framerate = sds_encoder.read_wav_channels(path)
            rate = entry.get("sample_rate") or framerate
            if rate != framerate:
                channels = [sds_encoder.resample_to_target_rate(list(ch), framerate, rate)[0] for ch in channels]
            if entry.get("mono", False):
                channels = channels[:1]
            parts = self._parts(base, channels, rate)
        except (OSError, ValueError, a.AsrxError) as ex:
            self._log().warning(f"AsrxTransfers: skipping {path!r}: {ex!r}")
            self._skipped += 1
            self._tx = None
            self._c.status_changed.emit(f"Skipping {os.path.basename(path)}: {ex}")
            self._after(0, self._next_file)
            return
        total = sum(len(p["samples"]) for p in parts)
        self._tx = {"entry": entry, "parts": parts, "part": 0, "rate": rate, "unverified": False, "total_samples": total, "done_samples": 0, "slot": None}
        self._log().info(
            f"AsrxTransfers: sending {base!r} from {path!r}: {len(parts)} sample(s) {[p['name'] for p in parts]}, "
            f"{len(parts[0]['samples'])} samples each @ {rate} Hz"
        )
        self._tx_next_part()

    @staticmethod
    def _parts(base, channels, rate):
        stereo = len(channels) == 2
        parts = []
        for i, ch in enumerate(channels):
            name = (base[:14] + ("-L" if i == 0 else "-R")) if stereo else base[: a.NAME_LENGTH]
            samples = [max(-32768, min(32767, int(v))) for v in ch]
            if not samples:
                raise ValueError("the file holds no audio")
            if len(samples) > a.MAX_SAMPLES:
                raise ValueError(f"too long for the ASR-X ({len(samples):,} samples at {rate} Hz, at most {a.MAX_SAMPLES:,})")
            # check the rate fits the pitch word before anything is sent
            a.pitch_word(rate, a.DEFAULT_ROOT_KEY)
            parts.append({"name": name, "samples": samples})
        return parts

    def _tx_next_part(self):
        tx = self._tx
        if tx["part"] >= len(tx["parts"]):
            self._tx_file_done()
            return
        tx["slot"] = None
        tx["uploading"] = False
        tx["tried"] = 0
        tx["candidate"] = self._next_candidate(-1)
        if tx["candidate"] is None:
            self._fail("The ASR-X has no free sample slot the app can see")
            return
        self._tx_probe_candidate()

    def _next_candidate(self, after):
        taken = (self.occupied or set()) | self._used_slots | self.hidden
        for slot in range(after + 1, a.SLOT_LIMIT):
            if slot not in taken:
                return slot
        return None

    def _tx_probe_candidate(self):
        # A slot with no name can still be in use: the unit's two recording buffers answer a name request with "empty". A range read of that slot is
        # answered if something is there, and not at all if the slot is really free - so SILENCE is what we are hoping for here.
        tx = self._tx
        slot = tx["candidate"]
        tx["tried"] += 1
        self._ask_range(
            slot, 0, 4, lambda r: self._tx_probe_got(r), label=f"is slot {slot} really free", timeout_ms=self.probe_timeout_ms, timeout_is_result=True
        )

    def _tx_probe_got(self, r):
        tx = self._tx
        slot = tx["candidate"]
        if r.ok:
            self.hidden.add(slot)
            self._log().warning(f"AsrxTransfers: slot {slot} has no name but a range read answered: something is stored there; not using it")
            nxt = self._next_candidate(slot)
            if nxt is None or tx["tried"] >= self.slot_candidates:
                self._fail("Couldn't find a free sample slot on the ASR-X")
                return
            tx["candidate"] = nxt
            self._after(self.pace_ms, self._tx_probe_candidate)
            return
        self._log().info(f"AsrxTransfers: slot {slot} is free ({'no answer' if r.timeout else 'error ' + format(r.error.error_code, '02X')})")
        tx["slot"] = slot
        self._after(0, self._tx_upload)

    def _tx_upload(self):
        tx = self._tx
        part = tx["parts"][tx["part"]]
        slot = tx["slot"]
        try:
            obj = a.build_sample_object(slot, part["name"], part["samples"], tx["rate"], root_key=self.root_key)
            message = a.build_put_object(self._device(), slot, obj)
        except a.AsrxError as ex:
            self._fail(f"Couldn't build the sample: {ex}")
            return
        if self.header_patch:
            patched = bytearray(obj)
            for offset, byte in self.header_patch.items():
                patched[offset] = byte
            obj = bytes(patched)
            message = a.build_put_object(self._device(), slot, obj)
            self._log().warning(f"AsrxTransfers: header_patch applied to the upload: {{{', '.join(f'0x{o:02X}: 0x{b:02X}' for o, b in sorted(self.header_patch.items()))}}}")
        part["object"] = obj
        self._used_slots.add(slot)
        seconds = wire_seconds(len(message)) * WIRE_SLACK
        tx.update(uploading=True, upload_t0=time.monotonic(), upload_seconds=seconds, upload_bytes=len(message))
        log = self._log()
        log.info(f"AsrxTransfers: uploading {part['name']!r} to slot {slot}: object {len(obj)} B, message {len(message)} B, about {seconds:.0f}s on the wire")
        log.info(f"AsrxTransfers: header sent: {_hex(obj, 52)}")
        log.info(f"AsrxTransfers: header decoded: {a.describe_header(obj[:52])}")
        self._c.status_changed.emit(f"Sending {part['name']} to slot {slot} ({len(message) / 1024:.0f} KB, about {max(1, round(seconds))} s)...")
        self._progress_timer.start()
        self._ask(
            f"the acknowledgement of slot {slot}",
            message,
            lambda m: m.kind == "ack",
            self._tx_got_ack,
            timeout_ms=int((seconds + self.ack_margin_s) * 1000),
            timeout_is_result=True,
        )

    def _on_progress_tick(self):
        tx = self._tx
        if not tx or not tx.get("uploading"):
            self._progress_timer.stop()
            return
        frac = min(0.99, (time.monotonic() - tx["upload_t0"]) / max(0.1, tx["upload_seconds"]))
        done = tx["done_samples"] + int(len(tx["parts"][tx["part"]]["samples"]) * frac)
        total = max(1, tx["total_samples"])
        self._c.transfer_progress.emit(done, total)
        self._c.unit_progress.emit(done / total)

    def _tx_got_ack(self, r):
        tx = self._tx
        part = tx["parts"][tx["part"]]
        slot = tx["slot"]
        self._progress_timer.stop()
        if r.ok:
            self._log().info(f"AsrxTransfers: slot {slot} acknowledged after {r.elapsed:.1f}s (estimated {tx['upload_seconds']:.0f}s)")
            tx["acked"] = True
            self._tx_verify(part)
            return
        if r.error is not None:
            self._log().warning(f"AsrxTransfers: upload to slot {slot} answered with error 7F {r.error.error_code:02X}")
            self._fail(f"The ASR-X refused the sample (error 7F {r.error.error_code:02X})")
            return
        # silence: it may still have arrived (the unit's ACK is the last thing it does)
        self._log().warning(f"AsrxTransfers: no acknowledgement for slot {slot} after {r.elapsed:.1f}s; asking whether it arrived")
        tx["acked"] = False
        self._ask_name(slot, lambda rr: self._tx_got_arrival(rr, part), label=f"whether slot {slot} arrived")

    def _tx_got_arrival(self, r, part):
        tx = self._tx
        slot = tx["slot"]
        if r.ok:
            self._log().warning(f"AsrxTransfers: slot {slot} now holds {r.message.name!r} although no acknowledgement came")
            tx["unverified"] = True
            self._tx_verify(part, arrived_without_ack=True)
            return
        self._fail(
            f"No confirmation from the ASR-X for {part['name']} and nothing arrived in slot {slot}. "
            "Is its sample memory full, or did the transfer break off? (Check the unit's display)"
        )

    def _tx_verify(self, part, arrived_without_ack=False):
        tx = self._tx
        tx["uploading"] = False
        if not self.send_verify:
            self._tx_part_done()
            return
        slot = tx["slot"]
        self._ask_name(slot, lambda r: self._tx_verify_name(part, r), label=f"the name of slot {slot} (verify)")

    def _tx_verify_name(self, part, r):
        tx = self._tx
        slot = tx["slot"]
        if r.ok:
            want = a.decode_name(a.encode_name(part["name"]))
            same = r.message.name == want
            self._log().info(f"AsrxTransfers: verify slot {slot}: name {r.message.name!r} {'matches' if same else 'DIFFERS from ' + repr(want)}")
            if not same:
                tx["unverified"] = True
        else:
            self._log().warning(f"AsrxTransfers: verify slot {slot}: the unit does not list the sample ({'no answer' if r.timeout else 'error'})")
            tx["unverified"] = True
        self._ask_range(
            slot, 0, a.HEADER_BYTES, lambda rr: self._tx_verify_header(part, rr), label=f"the header of slot {slot} (verify)", timeout_ms=self.reply_timeout_ms
        )

    def _tx_verify_header(self, part, r):
        tx = self._tx
        slot = tx["slot"]
        sent = part["object"][: a.HEADER_BYTES]
        if r.ok:
            got = r.message.payload
            if got == sent:
                self._log().info(f"AsrxTransfers: verify slot {slot}: the header reads back identical")
            else:
                # informational: the unit may legitimately adjust fields (flags, a duplicate marker). What matters for "verified" is the name and the audio
                diffs = [f"+0x{i:02X}: sent {sent[i]:02X} got {got[i]:02X}" for i in range(a.HEADER_BYTES) if sent[i] != got[i]]
                self._log().warning(f"AsrxTransfers: verify slot {slot}: header differs at {len(diffs)} byte(s): {'; '.join(diffs[:20])}")
                self._log().warning(f"AsrxTransfers: header read back: {_hex(got, 52)}")
                self._log().warning(f"AsrxTransfers: header read back decoded: {a.describe_header(got)}")
        else:
            self._log().warning(f"AsrxTransfers: verify slot {slot}: header read got no answer; cannot compare")
            tx["unverified"] = True
        total = len(part["object"])
        n = min(self.range_chunk_bytes, total - a.HEADER_BYTES)
        offset = total - n
        part["tail"] = (offset, n)
        self._ask_range(
            slot, offset, n, lambda rr: self._tx_verify_tail(part, rr), label=f"the last {n} bytes of slot {slot} (verify)", timeout_ms=self.reply_timeout_ms
        )

    def _tx_verify_tail(self, part, r):
        tx = self._tx
        slot = tx["slot"]
        offset, n = part["tail"]
        if r.ok:
            same = r.message.payload == part["object"][offset : offset + n]
            self._log().info(f"AsrxTransfers: verify slot {slot}: the last {n} bytes read back {'identical' if same else 'DIFFERENT'}")
            if not same:
                tx["unverified"] = True
        else:
            self._log().warning(f"AsrxTransfers: verify slot {slot}: the tail read got no answer")
            tx["unverified"] = True
        self._tx_part_done()

    def _tx_part_done(self):
        tx = self._tx
        part = tx["parts"][tx["part"]]
        slot = tx["slot"]
        tx["done_samples"] += len(part["samples"])
        self._log().info(f"AsrxTransfers: {part['name']!r} is in slot {slot} ({'verified' if not tx['unverified'] else 'NOT fully verified'})")
        self.occupied = (self.occupied or set()) | {slot}
        tx["part"] += 1
        self._after(self.pace_ms, self._tx_next_part)

    def _tx_file_done(self):
        tx = self._tx
        self._sent += 1
        if tx["unverified"]:
            self._unverified += 1
        self._c.unit_progress.emit(1.0)
        self._c.file_transferred.emit(tx["entry"]["filepath"])
        self._tx = None
        self._after(self.pace_ms, self._next_file)

    def _finish_op_summary(self, op):
        elapsed = time.monotonic() - self._started
        s = self._stats
        rate = (s["bytes_in"] + s["bytes_out"]) / elapsed if elapsed > 0 else 0
        self._log().info(
            f"AsrxTransfers: {op} finished in {elapsed:.1f}s: {s['messages_out']} sent ({s['bytes_out']} B), {s['messages_in']} received ({s['bytes_in']} B), "
            f"{s['timeouts']} timeout(s), {s['retries']} retried, {rate:.0f} B/s overall"
        )

    def _finish_send(self):
        sent, skipped, unverified = self._sent, self._skipped, self._unverified
        self._finish_op_summary("send")
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
