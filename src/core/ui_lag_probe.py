"""Measures how responsive the GUI thread stays during a long blocking job (a sample load over SDS), and says so in the log.

A heartbeat QTimer on the GUI thread records how late each tick is: a thread that is busy (in a slot, a paint, a layout) delivers ticks late.
`stop()` logs one INFO line: the median, 95th percentile and worst delay and how many ticks were over 50 and 200 ms. That is the number to ask a user
for when "the window lags while loading" (the log is `~/.akaisds/akaisds.log`).

With the environment variable `AKAISDS_PROFILE_LOAD=1` a second thread also samples the GUI thread's Python stack every few ms and `stop()` logs the
busiest places. It only sees Python frames (time inside Qt shows as the Python line that called in, e.g. the nested event loop), and it costs a few
percent of CPU, so it is off unless asked for.
"""

import collections
import os
import sys
import threading
import time

from PySide6.QtCore import QObject, QTimer

from core import debug_log

HEARTBEAT_MS = 10
SAMPLE_MS = 5
SLOW_MS = (50, 200)  # the delays a log line counts: "noticeable" and "frozen"
TOP_STACKS = 12


def summarize(delays_ms):
    """(median, p95, worst, ticks over each of SLOW_MS) of the heartbeat delays, in ms; None for no ticks."""
    if not delays_ms:
        return None
    ordered = sorted(delays_ms)
    median = ordered[len(ordered) // 2]
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return median, p95, ordered[-1], tuple(sum(1 for d in ordered if d > limit) for limit in SLOW_MS)


class LagProbe(QObject):
    def __init__(self, label, parent=None, sample_stacks=None):
        super().__init__(parent)
        self._label = label
        self._sample = bool(os.environ.get("AKAISDS_PROFILE_LOAD")) if sample_stacks is None else sample_stacks
        self._timer = QTimer(self)
        self._timer.setInterval(HEARTBEAT_MS)
        self._timer.timeout.connect(self._tick)
        self._delays = []
        self._last = None
        self._started = None
        self._stacks = collections.Counter()
        self._sampler = None
        self._stop_sampling = threading.Event()
        self._main_ident = threading.get_ident()

    def start(self):
        self._delays = []
        self._started = self._last = time.perf_counter()
        self._timer.start()
        if self._sample:
            self._stacks.clear()
            self._stop_sampling.clear()
            self._sampler = threading.Thread(target=self._sample_loop, name="LagProbeSampler", daemon=True)
            self._sampler.start()

    def _tick(self):
        now = time.perf_counter()
        self._delays.append(max(0.0, (now - self._last) * 1000 - HEARTBEAT_MS))
        self._last = now

    def _sample_loop(self):
        while not self._stop_sampling.wait(SAMPLE_MS / 1000):
            frame = sys._current_frames().get(self._main_ident)
            if frame is None:
                continue
            places = []
            while frame is not None and len(places) < 3:
                code = frame.f_code
                places.append(f"{os.path.basename(code.co_filename)}:{frame.f_lineno} {code.co_name}")
                frame = frame.f_back
            self._stacks[" <- ".join(places)] += 1

    def stop(self):
        """Stop and log. Safe to call twice (the second does nothing)."""
        if self._started is None:
            return
        self._timer.stop()
        elapsed = time.perf_counter() - self._started
        self._started = None
        log = debug_log.get_logger()
        stats = summarize(self._delays)
        if stats is None:
            log.info(f"LagProbe: {self._label}: no GUI heartbeat in {elapsed:.1f} s")
        else:
            median, p95, worst, (noticeable, frozen) = stats
            log.info(
                f"LagProbe: {self._label}: {len(self._delays)} GUI heartbeats in {elapsed:.1f} s, delay median {median:.0f} ms, p95 {p95:.0f} ms, "
                f"worst {worst:.0f} ms; {noticeable} over {SLOW_MS[0]} ms, {frozen} over {SLOW_MS[1]} ms"
            )
        if self._sampler is not None:
            self._stop_sampling.set()
            self._sampler.join(timeout=1)
            self._sampler = None
            total = sum(self._stacks.values()) or 1
            log.info(f"LagProbe: {self._label}: where the GUI thread's Python was, {total} samples:")
            for place, count in self._stacks.most_common(TOP_STACKS):
                log.info(f"LagProbe:   {100 * count / total:5.1f}%  {place}")
