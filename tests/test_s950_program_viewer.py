# tests for ui/s950_program_viewer.py - the read-only S900/S950 program viewer.
#
# The real window, real SamplerController and real S950Transfers run against
# core/demo_s950.FakeS950 over the same fake MidiManager test_s950_transfers.py
# uses. Offscreen Qt; every wire wait is shrunk.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QMainWindow

from core import s950_program as prog
from core.demo_s950 import FakeS950
from ui import s950_program_viewer as viewer
from ui.s950_program_viewer import S950ProgramViewerWindow

from test_s950_transfers import _build, qapp, wait_until  # noqa: F401


# --- pure formatting ---------------------------------------------------------------------------


def test_key_range_uses_the_s3000xl_note_names():
    assert viewer.format_key_range(60, 72) == "C3 - C4"


def test_velocity_switch_text():
    assert "soft sample only" in viewer.format_velocity_switch(128)
    assert viewer.format_velocity_switch(90) == "loud sample above velocity 90"


def test_transpose_is_sixteenths_of_a_semitone():
    assert viewer.format_transpose(0) == "+0.00 st"
    assert viewer.format_transpose(16) == "+1.00 st"
    assert viewer.format_transpose(-8) == "-0.50 st"


def test_loudness_shows_units_and_decibels():
    assert viewer.format_loudness(0) == "+0 (+0.0 dB)"
    assert viewer.format_loudness(-8) == "-8 (-3.0 dB)"


def test_voice_out_text():
    assert viewer.format_voice_out(255) == "all outputs"
    assert viewer.format_voice_out(3) == "output 3"
    assert viewer.format_voice_out(8) == "left group"
    assert viewer.format_voice_out(9) == "right group"


def test_control_bits_text():
    assert viewer.format_control_bits(0) == "none"
    assert viewer.format_control_bits(0b101) == "transpose off, vibrato desync"


def test_names_compare_case_and_padding_insensitively():
    assert viewer.normalise_name(" kick ") == "KICK"
    assert viewer.sample_label("   ") == "-"


# --- the window ----------------------------------------------------------------------------------


class _Main(QMainWindow):
    pass


@pytest.fixture
def win(qapp, monkeypatch):
    rig = _build(qapp, monkeypatch, FakeS950())
    main = _Main()
    window = S950ProgramViewerWindow(main, rig.controller)
    window.rig = rig
    window.main = main
    yield window
    rig.engine.cancel()
    window._busy_timer.stop()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def _wait_listed(win):
    assert wait_until(lambda: win.program_list.count() == 2)


def _show(win, slot=0):
    _wait_listed(win)
    win.program_list.setCurrentRow(slot)
    assert wait_until(lambda: win._program is not None and win._program_slot == slot)


def test_it_lists_the_programs_on_construction(win):
    _wait_listed(win)
    assert [win.program_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(2)] == [0, 1]
    assert "DRUMS" in win.program_list.item(0).text()
    assert win.placeholder.isHidden() is False  # nothing selected yet


def test_selecting_a_program_shows_it(win):
    _show(win, 0)
    assert win.detail_scroll.isHidden() is False
    assert win._values["name"].text() == "DRUMS"
    assert win._values["keygroups"].text() == "2"
    assert win.keygroup_table.rowCount() == 2
    assert win.keygroup_table.item(0, 1).text() == viewer.format_key_range(24, 59)
    assert win.keygroup_table.item(1, 2).text() == "SNARE"
    # the first keygroup is shown
    assert win._values["range"].text() == viewer.format_key_range(24, 59)
    assert win._values["soft_sample"].text() == "KICK"


def test_selecting_a_keygroup_updates_the_detail(win):
    _show(win, 0)
    win.keygroup_table.selectRow(1)
    assert win._values["range"].text() == viewer.format_key_range(60, 127)
    assert win._values["soft_sample"].text() == "SNARE"
    assert "Keygroup 2 of 2" in win.keygroup_title.text()


def test_a_sample_the_unit_does_not_have_is_flagged(win):
    win.rig.fake.programs[1] = prog.Program(
        name="ORPHAN", keygroups=[prog.Keygroup(soft_sample="NOPE", loud_sample="PAD")]
    )
    win._refresh()
    _show(win, 1)
    assert "not on the sampler" in win._values["soft_sample"].text()
    assert "not on the sampler" not in win._values["loud_sample"].text()  # PAD exists
    assert "not on sampler" in win.keygroup_table.item(0, 2).text()


def test_an_empty_loud_sample_is_not_flagged(win):
    _show(win, 0)
    assert win._values["loud_sample"].text() == "-"


def test_the_range_bar_and_graphs_follow_the_program(win):
    _show(win, 0)
    assert win.range_bar._ranges == [(24, 59), (60, 127)]
    kg = win._program.keygroups[0]
    assert win.amp_graph._attack == kg.attack
    assert win.filter_graph._release == kg.filter_release


def test_a_failed_read_shows_a_message_and_does_not_retry_forever(win):
    win.rig.engine.reply_timeout_ms = 30
    _wait_listed(win)
    win.rig.fake.programs.pop(1)  # listed, then gone: the fake stays silent
    win.program_list.setCurrentRow(1)
    assert wait_until(lambda: win._shown_slot == 1)
    assert "Couldn't read program 1" in win.placeholder.text()
    sent = len(win.rig.midi.sent)
    QCoreApplication.processEvents()
    assert len(win.rig.midi.sent) == sent  # no retry loop


def test_refresh_after_a_failure_tries_again(win):
    win.rig.engine.reply_timeout_ms = 30
    _wait_listed(win)
    fake_program = win.rig.fake.programs.pop(1)
    win.program_list.setCurrentRow(1)
    assert wait_until(lambda: win._shown_slot == 1 and win._program is None)
    win.rig.fake.programs[1] = fake_program
    win._refresh()
    assert wait_until(lambda: win._program is not None)
    assert win._values["name"].text() == "PAD PROG"


def test_clicking_faster_than_the_unit_answers_ends_on_the_last_choice(win):
    _wait_listed(win)
    win.program_list.setCurrentRow(0)
    win.program_list.setCurrentRow(1)
    assert wait_until(lambda: win._program is not None and win._program_slot == 1)
    assert win._values["name"].text() == "PAD PROG"
    assert win._inflight_slot is None


def test_it_never_writes_to_the_sampler(win):
    _show(win, 0)
    win.keygroup_table.selectRow(1)
    from core import s950_sysex as s

    functions = {m[2] for m in win.rig.midi.sent}
    assert functions <= {s.FUNC_RCAT, s.FUNC_RPRGM}
    assert win.rig.fake.prgm_writes == 0 and win.rig.fake.sprm_writes == 0


def test_closing_returns_to_the_dashboard(win):
    shown = []
    win.main.show = lambda: shown.append(True)
    win.close()
    assert shown == [True]


def test_closing_is_refused_while_a_read_is_in_flight(win):
    _wait_listed(win)
    shown = []
    win.main.show = lambda: shown.append(True)
    win.rig.controller.request_program(0)  # now on the wire
    assert win.rig.controller.is_transfer_busy()
    win.close()
    assert shown == []
    assert "in progress" in win.status_bar.currentMessage()
    assert wait_until(lambda: not win.rig.controller.is_transfer_busy())


def test_closing_disconnects_from_the_controller(win):
    win.main.show = lambda: None
    win.close()
    # a late catalog after close must not touch the closed window
    win.program_list.clear()
    win.rig.controller.program_slots_updated.emit([(0, "X")])
    assert win.program_list.count() == 0
