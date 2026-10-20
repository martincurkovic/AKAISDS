# core/ui_lag_probe.py: the heartbeat summary a sample load logs, and the opt-in stack sampler.

import logging
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from core import debug_log, ui_lag_probe
from core.ui_lag_probe import LagProbe, summarize


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def log_lines():
    handler = _Capture()
    debug_log.get_logger().addHandler(handler)
    yield handler.lines
    debug_log.get_logger().removeHandler(handler)


def pump(qapp, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.002)


def test_summarize_gives_median_p95_worst_and_the_slow_counts():
    delays = [1.0] * 90 + [60.0] * 8 + [250.0] * 2
    median, p95, worst, (noticeable, frozen) = summarize(delays)
    assert median == 1.0 and p95 == 60.0 and worst == 250.0
    assert (noticeable, frozen) == (10, 2)
    assert summarize([]) is None


def test_a_probe_logs_one_summary_line(qapp, log_lines):
    probe = LagProbe("a test load", sample_stacks=False)
    probe.start()
    pump(qapp, 0.15)
    probe.stop()
    lines = [line for line in log_lines if line.startswith("LagProbe:")]
    assert len(lines) == 1 and "a test load" in lines[0] and "GUI heartbeats" in lines[0] and "worst" in lines[0]


def test_a_blocked_gui_thread_shows_as_a_long_delay(qapp, log_lines):
    probe = LagProbe("blocked", sample_stacks=False)
    probe.start()
    pump(qapp, 0.05)
    time.sleep(0.3)  # (a slot that hogs the thread)
    pump(qapp, 0.05)
    probe.stop()
    assert max(probe._delays) >= 250
    assert "over 200 ms" in [line for line in log_lines if line.startswith("LagProbe:")][0]


def test_stopping_twice_logs_once(qapp, log_lines):
    probe = LagProbe("twice", sample_stacks=False)
    probe.start()
    pump(qapp, 0.05)
    probe.stop()
    probe.stop()
    assert len([line for line in log_lines if line.startswith("LagProbe:")]) == 1


def test_the_stack_sampler_reports_where_the_gui_thread_was(qapp, log_lines):
    probe = LagProbe("sampled", sample_stacks=True)
    probe.start()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.3:
        _busy_here()
    probe.stop()
    text = "\n".join(line for line in log_lines if line.startswith("LagProbe:"))
    assert "where the GUI thread's Python was" in text and "_busy_here" in text


def _busy_here():
    sum(i * i for i in range(2000))


def test_the_sampler_is_off_unless_asked_for(qapp, monkeypatch):
    monkeypatch.delenv("AKAISDS_PROFILE_LOAD", raising=False)
    assert LagProbe("x")._sample is False
    monkeypatch.setenv("AKAISDS_PROFILE_LOAD", "1")
    assert LagProbe("x")._sample is True
