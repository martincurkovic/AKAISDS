"""Akai S900/S950 transfers: catalog, send, receive, rename, sample info.

Owned by `SamplerController` (which delegates here whenever the Sampler Type's
protocol family is "s950") and reporting through the controller's own signals,
so the Dashboard needs no S950-specific plumbing beyond the sparse slot list
(`sample_slots_updated`). The wire format lives in `core/s950_sysex.py`; this
module is only the event-driven choreography, written the way
`SamplerController` is (Qt timers + `sysex_received`, one GUI thread).

Everything here follows s950tools' hardware-verified findings (comments say
where) and has NOT been run against a real unit by this project:

 - ONE thing on the wire at a time. `_op` is the single in-flight operation.
 - A catalog request (RCAT) is the "device ready" barrier before every
   upload: the S950 only answers it once it has finished with the previous
   one, whereas "any reply" was satisfied by a stale ACK streamed during the
   last upload and green-lit a second dump mid-bookkeeping.
 - Uploads are open loop: the whole dump goes out as one SysEx, then we wait
   out the MIDI wire time (3125 B/s + 10%), then listen ~0.5 s for NAKs.
   It always goes to an EMPTY slot - overwriting an occupied one triggers a
   "NAK storm" - picked from a fresh catalog; the boot-time "TONE" placeholder
   counts as empty. The sample is named afterwards by read-modify-write of its
   SPRM (the dump itself carries no name).
 - Receiving: after RSD the unit stalls between blocks until ACKed, and
   `sysex_received` only surfaces complete F0..F7 messages, so there's nothing
   to react to per block: a steady 4-byte ACK every 50 ms is streamed from
   just after the RSD until the dump lands. The unit sometimes flushes the
   bare 18-byte header as a message of its own first - that one is ignored.
 - There's no way to delete a sample over MIDI (no opcode).

Known risk, unmeasured here: a receive is one message of up to ~1 MB. CoreMIDI
(macOS) hands rtmidi the whole assembled message; other backends may split a
long SysEx into chunks, and `MidiManager._on_raw_message` drops any fragment
that doesn't start with F0 - so on such a platform a big receive would time
out. The timeout message says so, and every request/reply is logged.
"""

import os
import re
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from core import debug_log, s950_params, s950_program, s950_sysex as s, sds_encoder

#: sample numbers the S950 has (its catalog uses one byte, the dump header 14 bits)
SLOT_COUNT = 100
#: the S950's own placeholder sample at boot - safe to overwrite
TONE_PLACEHOLDER = "TONE"
MIN_SAMPLE_RATE_HZ = round(1e9 / s.MAX_PERIOD_NS)  # 2 kHz - the dump header's limits,
MAX_SAMPLE_RATE_HZ = round(1e9 / s.MIN_PERIOD_NS)  # not what the hardware plays
MIDI_BYTES_PER_SECOND = 3125

_BUSY_OPS = ("send", "receive", "info", "rename", "program", "program_write")
#: where the original of every program is saved before it is overwritten
BACKUP_DIR = Path.home() / ".akaisds" / "s950_backups"


def clamp_sample_rate(hz):
    return max(MIN_SAMPLE_RATE_HZ, min(MAX_SAMPLE_RATE_HZ, int(hz)))


def sample_name_for(requested):
    # the S950's names are 10 characters; uppercase is the conservative choice
    # (s950tools uppercases the names it derives from file names)
    return requested.strip().upper()[: s.NAME_LENGTH]


def unique_name(name, taken):
    # keygroup zones find samples by NAME with no uniqueness enforced by the
    # unit, so two samples sharing one make every zone using it ambiguous
    name = sample_name_for(name) or "SAMPLE"
    taken = {t.upper() for t in taken}
    candidate, n = name, 2
    while candidate.upper() in taken:
        suffix = f"-{n}"
        candidate = name[: s.NAME_LENGTH - len(suffix)] + suffix
        n += 1
    return candidate


def prepare_words(channels, framerate, *, target_rate=None, force_mono=False):
    """WAV channels -> (12-bit words, rate). Raises ValueError if unusable.

    Stereo is folded to mono (left only when `force_mono`, otherwise the
    average); the rate is clamped into the dump header's range and resampled
    if it changed; shorter than the S950's 200-word minimum is padded with
    silence, longer than its 475,020-word maximum is refused (never silently
    truncated).
    """
    if len(channels) == 2 and not force_mono:
        mono = [(left + right) // 2 for left, right in zip(*channels)]
    else:
        mono = list(channels[0])
    rate = clamp_sample_rate(target_rate or framerate)
    if rate != framerate:
        mono, rate = sds_encoder.resample_to_target_rate(mono, framerate, rate)
    words = [s.pcm16_to_word(v) for v in mono]
    if len(words) > s.MAX_TOTAL_WORDS:
        seconds = len(words) / rate
        raise ValueError(
            f"too long for the S900/S950 ({len(words):,} words, {seconds:.1f} s at "
            f"{rate} Hz - the limit is {s.MAX_TOTAL_WORDS:,} words)"
        )
    if len(words) < s.MIN_TOTAL_WORDS:
        words += [s.SILENCE_WORD] * (s.MIN_TOTAL_WORDS - len(words))
    return words, rate


class _Pending:
    def __init__(self, function, num, callback, label):
        self.function, self.num, self.callback, self.label = (
            function,
            num,
            callback,
            label,
        )


class S950Transfers(QObject):
    def __init__(self, controller):
        super().__init__()
        self._c = controller

        # timing, in ms - attributes so tests can shrink them
        self.reply_timeout_ms = 3000
        self.ack_interval_ms = 50
        self.nak_window_ms = 500
        self.sprm_settle_ms = 200
        self.progress_tick_ms = 250
        self.drain_margin = 1.1
        self.drain_floor_ms = 200
        self.dump_timeout_slack_ms = 10000

        #: last catalog: [(slot, name)] for samples and programs
        self.samples = []
        self.programs = []

        self._op = None
        self._pending = None
        self._step_fn = None
        self._silent = False
        # send queue
        self._queue = []
        self._queue_total = 0
        self._skipped = 0
        self._sent = 0
        self._current = None
        self._phase = None
        self._naks = 0
        self._words = None
        self._rate = 0
        self._slot = None
        self._pending_name = ""
        self._catalog_then = None
        # receive
        self._rx_slot = None
        self._rx_path = None
        self._rx_words = 0
        self._rx_rate_hint = 0
        self._receive_total = 0
        # progress
        self._progress_started = 0.0
        self._progress_total_ms = 1
        self._progress_emit = None
        # info / rename
        self._rename_to = None
        # program read / write
        self._program_slot = None
        self._write_changes = []
        self._write_merged = None
        self._write_backup_path = None
        self.backup_dir = BACKUP_DIR

        self._reply_timer = self._make_timer(self._on_reply_timeout)
        self._step_timer = self._make_timer(self._run_step)
        self._pump_timer = self._make_timer(self._pump_ack)
        self._progress_timer = self._make_timer(self._on_progress_tick)
        self._dump_timer = self._make_timer(self._on_dump_timeout)

    def _make_timer(self, slot):
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(slot)
        return timer

    # -- public surface (what SamplerController delegates to) ----------------------

    @property
    def busy(self):
        return self._op in _BUSY_OPS

    @property
    def idle(self):
        # stricter than `busy`: also false during a plain catalog read, which
        # is not a "transfer" (the Dashboard stays usable) but still owns the wire
        return self._op is None

    def refresh_catalog(self, silent=False):
        if not self._begin("catalog", "Refreshing the sample list", silent):
            return
        self._silent = silent
        if not silent:
            self._c.status_changed.emit("Requesting the sample list...")
        self._request_catalog(self._publish_catalog)

    def send_file_queue(self, entries):
        if not self._begin("send", "Sending samples"):
            return False
        self._queue = list(entries)
        self._queue_total = len(self._queue)
        self._skipped = 0
        self._sent = 0
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

    def request_sample_info(self, slot):
        if not self._begin("info", "Sample info"):
            return
        self._c.status_changed.emit(f"Requesting info for sample {slot}...")
        self._request(
            s.FUNC_RSPRM, slot, s.FUNC_SPRM, self._publish_info, f"sample {slot}'s parameters"
        )

    def rename_sample(self, slot, new_name):
        name = sample_name_for(new_name)
        if not name:
            self._c.status_changed.emit("A sample name can't be empty")
            return
        taken = [n for num, n in self.samples if num != slot and n.upper() == name]
        if taken:
            self._c.status_changed.emit(
                f"Not renamed: another sample is already called '{name}' (programs "
                "find samples by name, so two with the same name clash)"
            )
            return
        if not self._begin("rename", "Renaming a sample"):
            return
        self._rename_to = name
        self._request(
            s.FUNC_RSPRM, slot, s.FUNC_SPRM, self._apply_rename, f"sample {slot}'s parameters"
        )

    def request_program(self, slot):
        """Read program `slot` (RPRGM -> PRGM) and report it on the controller's
        `s950_program_received` - `(slot, Program)`, or `(slot, None)` on any failure."""
        if not self._begin("program", "Reading a program"):
            # busy (or no MIDI input): the caller is waiting for an answer
            self._c.s950_program_received.emit(slot, None)
            return
        self._program_slot = slot
        self._c.status_changed.emit(f"Reading program {slot}...")
        self._request(
            s.FUNC_RPRGM, slot, s.FUNC_PRGM, self._publish_program, f"program {slot}"
        )

    def write_program(self, slot, baseline, edited):
        """Write the editable fields of `edited` that differ from `baseline` onto the
        program in `slot`; reports on the controller's `s950_program_written`.

        Never creates a program (the slot must already hold one) and never changes the
        keygroup count. The order, each step logged: read the program fresh, refuse if it
        can't be backed up, apply ONLY the changed fields to that fresh copy, send it, wait
        out the wire time and the NAK window, read it back and compare. The unit doesn't
        answer a PRGM write, so the read-back is the only evidence it landed.
        """
        try:
            changes = s950_params.diff_programs(baseline, edited)
        except ValueError as e:
            self._write_refused(slot, f"Not written: {e}")
            return
        problems = s950_params.program_problems(edited, changes)
        if problems:
            self._write_refused(slot, "Not written: " + "; ".join(problems))
            return
        if not self._begin("program_write", "Writing a program"):
            self._c.s950_program_written.emit(
                slot, False, None, "Not written: another operation is in progress (or no MIDI input)"
            )
            return
        self._program_slot = slot
        self._write_changes = changes
        self._phase = "preread"
        self._c.status_changed.emit(f"Writing program {slot}...")
        self._request(
            s.FUNC_RPRGM,
            slot,
            s.FUNC_PRGM,
            self._on_write_preread,
            f"program {slot} (before writing it)",
        )

    def _write_refused(self, slot, message):
        debug_log.get_logger().info(f"S950Transfers: {message}")
        self._c.status_changed.emit(message)
        self._c.s950_program_written.emit(slot, False, None, message)

    def cancel(self):
        """Abort whatever is running. Returns True if something was."""
        if self._op is None:
            return False
        op, phase = self._op, self._phase
        program_slot = self._program_slot
        if op == "receive" and phase == "dump":
            # tell the unit to stop streaming (the standard "abort dump")
            self._send_quietly(s.build_handshake(s.CODE_ASD))
        self._stop_timers()
        self._pending = None
        self._queue = []
        self._reset()
        if op == "send":
            self._c.status_changed.emit(
                "Transfer cancelled - the S900/S950 may still be receiving the "
                "sample that was already on its way"
                if phase in ("draining", "naks")
                else "Transfer cancelled"
            )
            self._c.transfer_finished.emit(False)
        elif op == "receive":
            self._c.status_changed.emit("Transfer cancelled")
            self._c.receive_finished.emit(False)
        else:
            self._c.status_changed.emit("Cancelled")
            if op == "program":
                self._c.s950_program_received.emit(program_slot, None)
            if op == "program_write":
                self._c.s950_program_written.emit(
                    program_slot,
                    False,
                    None,
                    "Cancelled - the sampler may already have received the program"
                    if phase in ("draining", "naks")
                    else "Cancelled - nothing was written",
                )
        return True

    def handle_sysex(self, data):
        """Every SysEx the controller receives while the S900/S950 family is selected."""
        try:
            self._dispatch(bytes(data))
        except Exception as e:
            debug_log.get_logger().error(
                "S950Transfers: unhandled exception while handling SysEx", exc_info=True
            )
            self._fail(f"Unexpected error: {e}")

    # -- plumbing -------------------------------------------------------------------

    def _begin(self, op, what, silent=False):
        if self._op is not None:
            if not silent:
                self._c.status_changed.emit(
                    "A transfer is already in progress - please wait for it to finish"
                )
            return False
        if self._c.is_open_loop():
            if not silent:
                self._c.status_changed.emit(
                    f"{what} needs a MIDI input - the S900/S950 answers on it. "
                    "Select one in Settings."
                )
            return False
        self._op = op
        self._phase = None
        return True

    def _reset(self):
        self._op = None
        self._phase = None
        self._current = None
        self._words = None
        self._slot = None
        self._rx_slot = None
        self._rx_path = None
        self._catalog_then = None
        self._rename_to = None
        self._program_slot = None
        self._write_changes = []
        self._write_merged = None
        self._write_backup_path = None
        self._step_fn = None

    def _stop_timers(self):
        for timer in (
            self._reply_timer,
            self._step_timer,
            self._pump_timer,
            self._progress_timer,
            self._dump_timer,
        ):
            timer.stop()

    def _after(self, ms, fn):
        # the next step of the current operation, `ms` from now
        self._step_fn = fn
        self._step_timer.start(max(0, int(ms)))

    def _run_step(self):
        fn, self._step_fn = self._step_fn, None
        if fn is None:
            return
        try:
            fn()
        except Exception as e:
            debug_log.get_logger().error(
                "S950Transfers: unhandled exception in a transfer step", exc_info=True
            )
            self._fail(f"Unexpected error: {e}")

    def _send(self, data):
        try:
            self._c.midi_manager.send_sysex(data)
        except Exception as e:
            debug_log.get_logger().error(
                f"S950Transfers: MIDI send failed ({len(data)} bytes)", exc_info=True
            )
            self._fail(f"Couldn't send to the S900/S950: {e}")
            return False
        if len(data) <= 32:
            debug_log.get_logger().debug(f"S950Transfers: sent {bytes(data).hex(' ')}")
        else:
            debug_log.get_logger().debug(f"S950Transfers: sent {len(data)} bytes")
        return True

    def _send_quietly(self, data):
        try:
            self._c.midi_manager.send_sysex(data)
        except Exception:
            debug_log.get_logger().warning("S950Transfers: send failed", exc_info=True)

    def _request(self, function, num, expect, callback, label):
        self._pending = _Pending(expect, num, callback, label)
        self._reply_timer.start(self.reply_timeout_ms)
        if not self._send(s.build_akai_request(function, num, self._c.channel)):
            self._reply_timer.stop()

    def _request_catalog(self, then):
        self._catalog_then = then
        self._request(s.FUNC_RCAT, 0, s.FUNC_CAT, self._on_catalog, "the sample catalog")

    def _on_reply_timeout(self):
        pending, self._pending = self._pending, None
        label = pending.label if pending else "its last request"
        self._fail(
            f"No reply from the S900/S950 for {label} - check it's on, MIDI is "
            "connected in both directions and the channel matches"
        )

    def _fail(self, message):
        debug_log.get_logger().warning(f"S950Transfers: {self._op} failed: {message}")
        op = self._op
        program_slot = self._program_slot
        self._stop_timers()
        self._pending = None
        self._queue = []
        self._reset()
        self._c.status_changed.emit(message)
        if op == "program":
            self._c.s950_program_received.emit(program_slot, None)
        if op == "program_write":
            self._c.s950_program_written.emit(program_slot, False, None, message)
        if op == "send":
            self._c.transfer_finished.emit(False)
        elif op == "receive":
            self._c.receive_finished.emit(False)

    def _finish_simple(self):
        self._reset()

    # -- incoming messages ---------------------------------------------------------

    def _dispatch(self, data):
        if not data:
            return
        if data[0] == s.MANUFACTURER_AKAI:
            try:
                message = s.parse_akai(data)
            except ValueError as e:
                debug_log.get_logger().warning(
                    f"S950Transfers: unreadable Akai message ({len(data)} bytes, {e}): "
                    f"{data[:48].hex(' ')}"
                )
                return
            self._on_akai(message)
        elif data[0] == s.UNIVERSAL_NRT:
            code = s.handshake_code(data)
            if code is not None:
                self._on_handshake(code)
            elif len(data) > 1 and data[1] == s.CODE_SD:
                self._on_dump(data)
            else:
                debug_log.get_logger().debug(
                    f"S950Transfers: ignoring F0 7E message {data[:8].hex(' ')}"
                )

    def _on_akai(self, message):
        pending = self._pending
        if (
            pending is None
            or message.function != pending.function
            or (pending.num is not None and message.num != pending.num)
        ):
            debug_log.get_logger().debug(
                f"S950Transfers: ignoring unexpected Akai message "
                f"(function {message.function}, num {message.num})"
            )
            return
        self._pending = None
        self._reply_timer.stop()
        pending.callback(message)

    def _on_handshake(self, code):
        if self._op in ("send", "program_write") and self._phase in ("draining", "naks"):
            if code == s.CODE_NAKS:
                self._naks += 1
            return
        if self._op == "receive" and self._phase == "dump":
            if code == s.CODE_NAKS:
                self._fail(
                    f"The S900/S950 refused the request for sample {self._rx_slot} "
                    "(is there a sample in that slot?)"
                )
            elif code == s.CODE_ASD:
                self._fail("The S900/S950 aborted the sample dump")
        # anything else is a leftover ACK from an earlier dump - harmless

    # -- catalog -------------------------------------------------------------------

    def _on_catalog(self, message):
        try:
            entries = s.parse_catalog(message.payload)
        except ValueError as e:
            self._fail(f"Unreadable catalog from the S900/S950 ({e})")
            return
        self.programs = [(e.num, e.name) for e in entries if e.kind == "P"]
        self.samples = [(e.num, e.name) for e in entries if e.kind == "S"]
        then, self._catalog_then = self._catalog_then, None
        then()

    def _publish_catalog(self):
        silent = self._silent
        self._silent = False
        self._reset()
        self._c.sample_slots_updated.emit(list(self.samples))
        self._c.program_slots_updated.emit(list(self.programs))
        if not silent:
            n = len(self.samples)
            self._c.status_changed.emit(f"{n} sample{'' if n == 1 else 's'} on the S900/S950")

    # -- sending -------------------------------------------------------------------

    def _start_progress(self, total_ms, emit):
        self._progress_started = time.monotonic()
        self._progress_total_ms = max(1, total_ms)
        self._progress_emit = emit
        self._progress_timer.start(self.progress_tick_ms)

    def _on_progress_tick(self):
        fraction = (time.monotonic() - self._progress_started) * 1000 / self._progress_total_ms
        if self._progress_emit is not None:
            self._progress_emit(min(0.99, fraction))
        self._progress_timer.start(self.progress_tick_ms)

    def _stop_progress(self):
        self._progress_timer.stop()
        self._progress_emit = None

    def _next_file(self):
        if not self._queue:
            self._finish_send()
            return
        entry = self._queue.pop(0)
        self._current = entry
        index = self._queue_total - len(self._queue)
        path = entry["filepath"]
        base = os.path.splitext(os.path.basename(path))[0]
        self._c.status_changed.emit(f"Sending file {index}/{self._queue_total}: {base}...")
        try:
            channels, framerate = sds_encoder.read_wav_channels(path)
            self._words, self._rate = prepare_words(
                channels,
                framerate,
                target_rate=entry.get("sample_rate"),
                force_mono=entry.get("mono", False),
            )
        except (OSError, ValueError) as e:
            # skipped, not failed: the rest of the queue is unaffected, and
            # the file stays in the Dashboard's queue so it can be fixed
            debug_log.get_logger().warning(f"S950Transfers: skipping {path!r}: {e!r}")
            self._c.status_changed.emit(f"Skipping {os.path.basename(path)}: {e}")
            self._skipped += 1
            self._after(0, self._next_file)
            return
        self._phase = "barrier"
        self._request_catalog(self._pick_slot_and_send)

    def _pick_slot_and_send(self):
        occupied = {num for num, name in self.samples if name.strip().upper() != TONE_PLACEHOLDER}
        free = [slot for slot in range(SLOT_COUNT) if slot not in occupied]
        if not free:
            self._fail(f"All {SLOT_COUNT} sample slots on the S900/S950 are occupied")
            return
        self._slot = free[0]
        entry = self._current
        base = os.path.splitext(os.path.basename(entry["filepath"]))[0]
        taken = [name for num, name in self.samples if num != self._slot]
        self._pending_name = unique_name(entry.get("name") or base, taken)

        n = len(self._words)
        header = s.SampleDumpHeader(
            num=self._slot,
            period_ns=s.hz_to_period_ns(self._rate),
            total_words=n,
            # one-shot, as the spec encodes it: loop start within 5 words of the end
            loop_start=n - 5,
            loop_end=n - 1,
        )
        data = s.build_sample_dump(header, self._words)
        debug_log.get_logger().info(
            f"S950Transfers: uploading {n} words @ {self._rate} Hz to slot {self._slot} "
            f"as {self._pending_name!r} ({len(data) + 2} bytes on the wire)"
        )
        self._naks = 0
        self._phase = "draining"
        if not self._send(data):
            return
        wire_ms = (len(data) + 2) * 1000 / MIDI_BYTES_PER_SECOND
        drain_ms = max(self.drain_floor_ms, wire_ms * self.drain_margin)
        blocks = s.num_blocks(n)
        self._start_progress(
            drain_ms,
            lambda f: (
                self._c.transfer_progress.emit(int(f * blocks), blocks),
                self._c.unit_progress.emit(f),
            ),
        )
        self._after(drain_ms, self._after_drain)

    def _after_drain(self):
        self._stop_progress()
        # always land the bar on 100%, even if the wire wait was shorter than
        # one progress tick
        blocks = s.num_blocks(len(self._words))
        self._c.transfer_progress.emit(blocks, blocks)
        self._phase = "naks"
        self._after(self.nak_window_ms, self._after_nak_window)

    def _after_nak_window(self):
        if self._naks:
            self._fail(
                f"{self._naks} NAK(s) from the S900/S950 - sample '{self._pending_name}' "
                f"in slot {self._slot} may be truncated; please retry"
            )
            return
        self._phase = "naming"
        self._after(self.sprm_settle_ms, self._read_sprm_for_name)

    def _read_sprm_for_name(self):
        self._request(
            s.FUNC_RSPRM,
            self._slot,
            s.FUNC_SPRM,
            self._write_name,
            f"slot {self._slot}'s parameters after the upload",
        )

    def _write_name(self, message):
        params = s.SampleParams.from_payload(message.payload)
        params.name = self._pending_name
        data = s.build_akai_data(s.FUNC_SPRM, self._slot, params.to_payload(), self._c.channel)
        if not self._send(data):
            return
        # the unit doesn't answer a write; let its 129 bytes clear the wire
        self._after(len(data) * 1000 / MIDI_BYTES_PER_SECOND + 100, self._file_done)

    def _file_done(self):
        entry, self._current = self._current, None
        self._sent += 1
        self._c.unit_progress.emit(1.0)
        self._c.file_transferred.emit(entry["filepath"])
        self._after(0, self._next_file)

    def _finish_send(self):
        sent, skipped = self._sent, self._skipped
        self._reset()
        parts = [f"Sent {sent} file{'' if sent == 1 else 's'}"]
        if skipped:
            parts.append(f"skipped {skipped}")
        self._c.status_changed.emit(", ".join(parts))
        self._c.transfer_finished.emit(True)
        # show what's on the unit now (same as the Akai path's post-send refresh)
        self._after(300, lambda: self.refresh_catalog(silent=True))

    # -- receiving -----------------------------------------------------------------

    def _next_receive(self):
        if not self._queue:
            self._reset()
            self._c.status_changed.emit("All samples received")
            self._c.receive_finished.emit(True)
            return
        self._rx_slot, self._rx_path = self._queue.pop(0)
        index = self._receive_total - len(self._queue)
        self._c.status_changed.emit(
            f"Receiving sample {self._rx_slot} ({index}/{self._receive_total})..."
        )
        self._phase = "params"
        self._request(
            s.FUNC_RSPRM,
            self._rx_slot,
            s.FUNC_SPRM,
            self._start_dump,
            f"sample {self._rx_slot}'s parameters",
        )

    def _start_dump(self, message):
        params = s.SampleParams.from_payload(message.payload)
        if params.total_words <= 0:
            self._fail(f"Sample {self._rx_slot} on the S900/S950 is empty")
            return
        self._rx_words = params.total_words
        self._rx_rate_hint = params.sample_rate_hz
        wire_bytes = s.DUMP_HEADER_SIZE + s.num_blocks(self._rx_words) * s.BLOCK_SIZE + 2
        est_ms = wire_bytes * 1000 / MIDI_BYTES_PER_SECOND * self.drain_margin
        self._phase = "dump"
        if not self._send(s.build_request_sample_dump(self._rx_slot)):
            return
        self._pump_ack()  # one ACK straight away, then every ack_interval_ms
        self._dump_timer.start(int(est_ms * 1.5 + self.dump_timeout_slack_ms))
        self._start_progress(
            est_ms,
            lambda f: self._c.receive_progress.emit(int(f * self._rx_words), self._rx_words),
        )

    def _pump_ack(self):
        if self._op != "receive" or self._phase != "dump":
            return
        self._send_quietly(s.build_handshake(s.CODE_ACKS))
        self._pump_timer.start(self.ack_interval_ms)

    def _on_dump_timeout(self):
        self._fail(
            f"Timed out waiting for sample {self._rx_slot} from the S900/S950. Large "
            "dumps arrive as one long SysEx message; if this keeps happening on a "
            "sample that is small, your MIDI backend may be splitting long SysEx "
            "messages - please report it with ~/.akaisds/akaisds.log"
        )

    def _on_dump(self, data):
        if self._op != "receive" or self._phase != "dump":
            debug_log.get_logger().debug(
                f"S950Transfers: ignoring a sample dump ({len(data)} bytes) nobody asked for"
            )
            return
        if len(data) <= s.DUMP_HEADER_SIZE:
            # the header flushed as a message of its own, before the blocks
            debug_log.get_logger().debug("S950Transfers: ignoring a header-only dump message")
            return
        slot, path = self._rx_slot, self._rx_path
        self._pump_timer.stop()
        self._dump_timer.stop()
        self._stop_progress()
        try:
            header, words = s.parse_sample_dump(data, expected_slot=slot)
            rate = s.period_ns_to_hz(header.period_ns)
            if rate <= 0:
                raise ValueError("the dump's sample period is zero")
            # the header holds a period in whole nanoseconds, so 44100 Hz
            # comes back as 44099; the SPRM has the exact rate - use it when
            # the two agree
            hint = self._rx_rate_hint
            if hint > 0 and abs(hint - rate) / rate < 0.005:
                rate = hint
            sds_encoder.write_wav_file(path, [s.word_to_pcm16(w) for w in words], rate, 16)
        except (ValueError, OSError) as e:
            debug_log.get_logger().warning(
                f"S950Transfers: bad dump for slot {slot} ({len(data)} bytes): {e}"
            )
            self._fail(f"Couldn't receive sample {slot}: {e}")
            return
        debug_log.get_logger().info(
            f"S950Transfers: received slot {slot}: {len(words)} words @ {rate} Hz -> {path}"
        )
        self._c.receive_progress.emit(len(words), len(words))
        self._c.status_changed.emit(f"Saved sample {slot} to {path}")
        self._c.sample_received.emit(path)
        self._phase = "between"
        self._after(300, self._next_receive)

    # -- program read ----------------------------------------------------------------

    def _publish_program(self, message):
        slot = message.num
        try:
            program = s950_program.Program.from_payload(message.payload)
        except ValueError as e:
            debug_log.get_logger().warning(
                f"S950Transfers: unreadable program {slot} ({len(message.payload)} bytes, "
                f"{e}): {bytes(message.payload).hex(' ')}"
            )
            self._fail(f"Couldn't read program {slot}: {e}")
            return
        debug_log.get_logger().info(
            f"S950Transfers: read program {slot} {program.name!r}: "
            f"{program.num_keygroups} keygroup(s), {len(message.payload)} payload bytes"
        )
        self._reset()
        self._c.s950_program_received.emit(slot, program)
        self._c.status_changed.emit(f"Read program {slot}: {program.name.strip()}")

    # -- program write -----------------------------------------------------------------

    def _on_write_preread(self, message):
        slot = self._program_slot
        try:
            current = s950_program.Program.from_payload(message.payload)
        except ValueError as e:
            self._fail(f"Not written: couldn't read program {slot} first ({e})")
            return
        try:
            merged = s950_params.apply_changes(current, self._write_changes)
        except ValueError as e:
            self._fail(f"Not written: {e}")
            return
        problems = s950_params.program_problems(merged)
        if problems:
            self._fail("Not written: " + "; ".join(problems))
            return
        try:
            self._write_backup_path = self._save_backup(slot, current.name, message)
        except OSError as e:
            self._fail(
                f"Not written: couldn't save a backup of program {slot} first ({e}) - "
                "nothing was changed on the sampler"
            )
            return
        self._write_merged = merged
        data = s.build_akai_data(s.FUNC_PRGM, slot, merged.to_payload(), self._c.channel)
        log = debug_log.get_logger()
        log.info(
            f"S950Transfers: writing program {slot} {merged.name!r}: "
            f"{len(self._write_changes)} change(s), {merged.num_keygroups} keygroup(s), "
            f"{len(data) + 2} bytes on the wire, backup {self._write_backup_path}"
        )
        for change in self._write_changes:
            log.info(f"S950Transfers:   {change.describe()}")
        self._naks = 0
        self._phase = "draining"
        if not self._send(data):
            return
        wire_ms = (len(data) + 2) * 1000 / MIDI_BYTES_PER_SECOND
        self._after(max(self.drain_floor_ms, wire_ms * self.drain_margin), self._after_write_drain)

    def _save_backup(self, slot, name, message):
        # the complete PRGM message exactly as the unit sent it, as a .syx file any
        # SysEx librarian can send straight back
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()) or "unnamed"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.backup_dir / f"prog{slot:02d}-{safe}-{stamp}.syx"
        raw = s.build_akai_data(s.FUNC_PRGM, slot, message.payload, message.channel)
        path.write_bytes(bytes([0xF0, *raw, 0xF7]))
        return path

    def _after_write_drain(self):
        self._phase = "naks"
        self._after(self.nak_window_ms, self._after_write_nak_window)

    def _after_write_nak_window(self):
        if self._naks:
            self._fail(
                f"{self._naks} NAK(s) from the S900/S950 while writing program "
                f"{self._program_slot} - it may not have taken the whole program. "
                f"The original is saved at {self._write_backup_path}"
            )
            return
        self._phase = "verify"
        self._after(self.sprm_settle_ms, self._read_back_program)

    def _read_back_program(self):
        self._request(
            s.FUNC_RPRGM,
            self._program_slot,
            s.FUNC_PRGM,
            self._on_write_readback,
            f"program {self._program_slot} (to check the write)",
        )

    def _on_write_readback(self, message):
        slot, sent, backup = self._program_slot, self._write_merged, self._write_backup_path
        try:
            held = s950_program.Program.from_payload(message.payload)
        except ValueError as e:
            self._fail(
                f"Program {slot} was sent, but the read-back is unreadable ({e}). "
                f"The original is saved at {backup}"
            )
            return
        log = debug_log.get_logger()
        self._reset()
        if held == sent:
            if held.to_payload() != sent.to_payload():
                # same modelled fields, different bytes elsewhere: the unit normalised
                # something we don't model - not a failure, but worth a log line
                log.warning(
                    f"S950Transfers: program {slot} read back with different unmodelled bytes "
                    f"than were sent (sent {len(sent.to_payload())}, got {len(held.to_payload())})"
                )
            log.info(f"S950Transfers: program {slot} written and verified")
            text = f"Program {slot} written and verified. Original saved to {backup}"
            self._c.status_changed.emit(text)
            self._c.s950_program_written.emit(slot, True, held, text)
            return
        try:
            differing = [c.describe() for c in s950_params.diff_programs(sent, held)]
        except ValueError:
            differing = ["the number of keygroups"]
        if not differing:
            differing = ["fields the editor doesn't offer"]
        log.warning(
            f"S950Transfers: program {slot} read back DIFFERENT from what was sent: "
            + "; ".join(differing)
        )
        text = (
            f"Program {slot} was sent, but the sampler now reports different values "
            f"({'; '.join(differing[:3])}{'...' if len(differing) > 3 else ''}). "
            f"Original saved to {backup}"
        )
        self._c.status_changed.emit(text)
        self._c.s950_program_written.emit(slot, False, held, text)

    # -- info / rename ---------------------------------------------------------------

    def _publish_info(self, message):
        params = s.SampleParams.from_payload(message.payload)
        self._reset()
        self._c.sample_info_received.emit(
            {
                "name": params.name,
                "sample_number": message.num,
                "bit_depth": 12,
                "sample_rate": params.sample_rate_hz,
                "sample_length": params.total_words,
                # SNOMP's unit and direction are only inferred from s950tools'
                # notes - not shown rather than shown wrong
                "root_key": None,
                "detune": None,
            }
        )

    def _apply_rename(self, message):
        params = s.SampleParams.from_payload(message.payload)
        params.name = self._rename_to
        data = s.build_akai_data(s.FUNC_SPRM, message.num, params.to_payload(), self._c.channel)
        if not self._send(data):
            return
        name = self._rename_to
        self._c.status_changed.emit(f"Renamed sample {message.num} to '{name}'")
        self._after(
            len(data) * 1000 / MIDI_BYTES_PER_SECOND + 100,
            self._after_rename,
        )

    def _after_rename(self):
        self._reset()
        self.refresh_catalog(silent=True)
