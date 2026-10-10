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


class SysexTrace:
    def __init__(self, logger, clock=time.monotonic):
        self._logger = logger
        self._clock = clock
        # direction -> [window start, lines logged in the window, lines suppressed in the window]
        self._windows = {"out": [None, 0, 0], "in": [None, 0, 0]}

    def out(self, data):
        self._record("out", data)

    def inbound(self, data):
        self._record("in", data)

    def _record(self, direction, data):
        window = self._windows[direction]
        now = self._clock()
        if window[0] is None or now - window[0] >= WINDOW_S:
            if window[2]:
                self._logger.debug(
                    f"MIDI {direction}: {window[2]} more SysEx message(s) not logged in the last {WINDOW_S:.0f} s (log volume cap)"
                )
            window[0], window[1], window[2] = now, 0, 0
        if window[1] < MAX_LINES:
            window[1] += 1
            self._logger.debug(f"MIDI {direction} SysEx {describe(data)}")
        else:
            window[2] += 1
