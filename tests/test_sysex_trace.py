# tests for core/sysex_trace.py and its hook in core/midi_manager.MidiManager - the bounded wire trace the Dashboard and the S950/Yamaha editors share

import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core import sysex_trace
from core.sysex_trace import SysexTrace, describe


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _Log:
    def __init__(self):
        self.lines = []

    def debug(self, text):
        self.lines.append(text)


def test_describe_shows_the_first_bytes_and_the_total_length():
    assert describe(b"\x47\x00\x06\x48") == "47 00 06 48 (4 B)"
    long = bytes(range(40))
    assert describe(long) == " ".join(f"{b:02x}" for b in range(16)) + " ... (40 B)"
    assert "10" not in describe(long).split("(")[0].split()  # byte 16 (0x10) is NOT shown - never the payload


def test_each_message_is_one_debug_line_with_its_direction():
    log = _Log()
    trace = SysexTrace(log, _Clock())
    trace.out(b"\x47\x00\x06\x48")
    trace.inbound(b"\x47\x00\x07\x48\x01")
    assert log.lines == ["MIDI out SysEx 47 00 06 48 (4 B)", "MIDI in SysEx 47 00 07 48 01 (5 B)"]


def test_volume_is_capped_per_window_then_one_summary_line():
    clock, log = _Clock(), _Log()
    trace = SysexTrace(log, clock)
    for _ in range(sysex_trace.MAX_LINES + 50):
        trace.inbound(b"\x01\x02")
    assert len(log.lines) == sysex_trace.MAX_LINES  # the 50 extra were not logged

    clock.now += sysex_trace.WINDOW_S + 0.1  # next window
    trace.inbound(b"\x01\x02")
    assert log.lines[-2] == f"MIDI in: 50 more SysEx message(s) not logged in the last {sysex_trace.WINDOW_S:.0f} s (log volume cap)"
    assert log.lines[-1].startswith("MIDI in SysEx")  # and logging resumes


def test_the_two_directions_are_capped_independently():
    log = _Log()
    trace = SysexTrace(log, _Clock())
    for _ in range(sysex_trace.MAX_LINES + 10):
        trace.inbound(b"\x01")
    trace.out(b"\x02")  # still logged: the outgoing window is its own
    assert log.lines[-1] == "MIDI out SysEx 02 (1 B)"


def test_a_quiet_window_logs_no_summary():
    clock, log = _Clock(), _Log()
    trace = SysexTrace(log, clock)
    trace.out(b"\x01")
    clock.now += 60
    trace.out(b"\x01")
    assert not [line for line in log.lines if "not logged" in line]


# --- the MidiManager hook ---------------------------------------------------------------------


@pytest.fixture
def manager(qapp_for_trace):
    from core import midi_manager as mm

    return mm.MidiManager()


@pytest.fixture
def qapp_for_trace():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_incoming_sysex_is_traced_and_still_delivered(manager, caplog):
    caplog.set_level(logging.DEBUG, logger="akaisds")
    got = []
    manager.sysex_received.connect(got.append)
    manager._on_raw_message([0xF0, 0x47, 0x00, 0x16, 0x48, 0x00, 0xF7])
    assert got == [bytes([0x47, 0x00, 0x16, 0x48, 0x00])]
    assert any("MIDI in SysEx 47 00 16 48 00 (5 B)" in r.getMessage() for r in caplog.records)


def test_non_sysex_input_is_not_traced(manager, caplog):
    caplog.set_level(logging.DEBUG, logger="akaisds")
    manager._on_raw_message([0x90, 60, 100])
    manager._on_raw_message([0xF8])
    assert not [r for r in caplog.records if "MIDI in SysEx" in r.getMessage()]


def test_outgoing_sysex_is_traced_on_the_shared_transport(manager, monkeypatch, caplog):
    from core import midi_manager as mm

    caplog.set_level(logging.DEBUG, logger="akaisds")
    sent = []

    class _Out:
        def send_message(self, message, **_kw):
            sent.append(list(message))

    monkeypatch.setattr(mm, "shared_transport_enabled", lambda: True)
    manager.raw_output = _Out()
    manager.send_sysex(bytes([0x47, 0x00, 0x00, 0x48]))
    assert sent == [[0xF0, 0x47, 0x00, 0x00, 0x48, 0xF7]]
    assert any("MIDI out SysEx 47 00 00 48 (4 B)" in r.getMessage() for r in caplog.records)


# --- LineBudget (shared by the wire trace and the controller's "unexpected SysEx" lines) --------------------


def test_line_budget_allows_max_lines_then_reports_the_drops_once_per_window():
    clock = _Clock()
    budget = sysex_trace.LineBudget(max_lines=3, window_s=10, clock=clock)
    assert [budget.allow() for _ in range(5)] == [True, True, True, False, False]
    assert budget.take_suppressed() == 0  # the window has not ended
    clock.now += 10.5
    assert budget.allow() is True
    assert budget.take_suppressed() == 2
    assert budget.take_suppressed() == 0  # reading it resets it
