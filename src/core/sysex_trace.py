"""
A bounded wire trace of the SysEx messages that pass through `core/midi_manager.MidiManager` (no Qt, no MIDI of its own).

Every editor that is not the S3000-family Program Editor (the Transfer Dashboard, the S900/S950 editor, the Yamaha A4000/A5000 editor) sends through
`MidiManager.send_sysex` and receives through `MidiManager.sysex_received`, so this one trace gives their bug reports a wire timeline. (The S3000 Program Editor logs through
`LoggingBridge` instead.) It exists because on 2026-10-10 the only reason an echo on the MIDI interface - another program sending the sampler's replies back to it - could be
diagnosed at all was that the S3000 path logged raw frames; nothing else in the app did.

DEBUG lines of the form `MIDI out SysEx 47 00 06 48 ... (131 B)` / `MIDI in SysEx ...` (the bytes between F0 and F7: the first 16 and the total length - never the payload, so a
sample dump can't fill the log). Volume is bounded per direction: at most `MAX_LINES` lines per `WINDOW_S` seconds, then ONE line says how many were not logged - a sample
transfer sends tens of thousands of packets and the log rotates at 8 MB x 3 (see core/debug_log.py).
"""

import time

PREVIEW_BYTES = 16
WINDOW_S = 10.0
MAX_LINES = 300


def describe(data, limit=PREVIEW_BYTES):
    """`47 00 06 48 00 ... (131 B)` - the first `limit` bytes in hex and the total length."""
    data = bytes(data)
    more = " ..." if len(data) > limit else ""
    return f"{data[:limit].hex(' ')}{more} ({len(data)} B)"


class LineBudget:
    """At most `max_lines` log lines per `window_s`; `allow()` says whether to log this one. When a window that suppressed lines ends, the next `allow()` also hands back
    how many were dropped (`take_suppressed()`) so the caller can write ONE summary line."""

    def __init__(self, max_lines=MAX_LINES, window_s=WINDOW_S, clock=time.monotonic):
        self.max_lines, self.window_s, self._clock = max_lines, window_s, clock
        self._start = None
        self._count = 0
        self._suppressed = 0
        self._dropped_last_window = 0

    def allow(self):
        now = self._clock()
        if self._start is None or now - self._start >= self.window_s:
            self._dropped_last_window = self._suppressed
            self._start, self._count, self._suppressed = now, 0, 0
        if self._count < self.max_lines:
            self._count += 1
            return True
        self._suppressed += 1
        return False

    def take_suppressed(self):
        """Lines dropped in the window that just ended (0 if none); reading it resets it."""
        dropped, self._dropped_last_window = self._dropped_last_window, 0
        return dropped


class SysexTrace:
    def __init__(self, logger, clock=time.monotonic):
        self._logger = logger
        self._budgets = {"out": LineBudget(clock=clock), "in": LineBudget(clock=clock)}

    def out(self, data):
        self._record("out", data)

    def inbound(self, data):
        self._record("in", data)

    def _record(self, direction, data):
        budget = self._budgets[direction]
        allowed = budget.allow()
        dropped = budget.take_suppressed()
        if dropped:
            self._logger.debug(f"MIDI {direction}: {dropped} more SysEx message(s) not logged in the last {WINDOW_S:.0f} s (log volume cap)")
        if allowed:
            self._logger.debug(f"MIDI {direction} SysEx {describe(data)}")
