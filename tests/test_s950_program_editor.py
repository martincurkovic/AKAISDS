# tests for ui/s950_program_editor.py - the S900/S950 program editor (view + staged edits + write).
#
# The real window, real SamplerController and real S950Transfers run against
# core/demo_s950.FakeS950 over the same fake MidiManager test_s950_transfers.py
# uses. Offscreen Qt; every wire wait is shrunk.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import dataclasses

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QMainWindow

from core import s950_program as prog
from core.demo_s950 import FakeS950
from PySide6.QtWidgets import QLabel, QMessageBox

from ui import s950_program_editor as viewer
from ui.s950_program_editor import S950ProgramEditorWindow

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


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    # never touch the real config or the real ~/.akaisds/s950_backups
    state = {"ack": False}
    monkeypatch.setattr(
        viewer.app_config, "get_s950_program_write_warning_acknowledged", lambda: state["ack"]
    )
    monkeypatch.setattr(
        viewer.app_config,
        "save_s950_program_write_warning_acknowledged",
        lambda acknowledged=True: state.update(ack=acknowledged),
    )
    monkeypatch.setattr(viewer.QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(viewer.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    return state


@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeS950())
    rig.engine.backup_dir = tmp_path / "backups"
    main = _Main()
    window = S950ProgramEditorWindow(main, rig.controller)
    window.rig = rig
    window.main = main
    yield window
    rig.engine.cancel()
    window._busy_timer.stop()
    window._working = None  # nothing to confirm discarding at teardown
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def _wait_listed(win):
    assert wait_until(lambda: win.program_list.count() == 2)


def _show(win, slot=0):
    _wait_listed(win)
    win.program_list.setCurrentRow(slot)
    assert wait_until(lambda: win._working is not None and win._slot == slot)


def _kg(win, attr):
    return win._editors[("kg", attr)]


def _write_and_wait(win):
    win.write_button.click()
    assert wait_until(lambda: not win._writing)


# --- viewing -----------------------------------------------------------------------------------


def test_it_lists_the_programs_on_construction(win):
    _wait_listed(win)
    assert [win.program_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(2)] == [0, 1]
    assert "DRUMS" in win.program_list.item(0).text()
    assert win.placeholder.isHidden() is False  # nothing selected yet
    assert win.detail_stack.isHidden()


def test_selecting_a_program_shows_it(win):
    _show(win, 0)
    assert win.detail_stack.isHidden() is False
    assert win.detail_stack.currentIndex() == 0  # the program page first, as in the S3000 editor
    assert win._editors[("program", "name")].text() == "DRUMS"
    assert win.keygroup_list.count() == 2
    assert win.keygroup_row_text(0) == "Keygroup 1: C0 - B2"
    assert win.keygroup_row_text(1) == "Keygroup 2: C3 - G8"
    assert _kg(win, "lower_key").value() == 24
    assert _kg(win, "soft_sample").currentData() == "KICK"
    assert win.is_dirty() is False


def test_selecting_a_keygroup_updates_the_detail(win):
    _show(win, 0)
    win.keygroup_list.setCurrentRow(1)
    assert _kg(win, "lower_key").value() == 60
    assert _kg(win, "soft_sample").currentData() == "SNARE"


def test_clicking_a_keygroup_opens_the_keygroup_page_and_a_program_the_program_page(win):
    _show(win, 0)
    win.keygroup_list.itemClicked.emit(win.keygroup_list.item(1))
    assert win.detail_stack.currentIndex() == 1
    win.program_list.itemClicked.emit(win.program_list.item(0))
    assert win.detail_stack.currentIndex() == 0


def test_keygroup_rows_have_colored_swatches_like_the_s3000_editor(win):
    _show(win, 0)
    swatches = [l for l in win.keygroup_list.findChildren(QLabel) if l.property("swatchKind")]
    assert [s.property("keygroupIndex") for s in swatches] == [0, 1]
    assert "background-color" in swatches[0].styleSheet()
    assert swatches[0].styleSheet() != swatches[1].styleSheet()


def test_the_zone_card_switches_between_the_soft_and_loud_sample(win):
    _show(win, 0)
    assert win._zone_stack.currentIndex() == 0
    win._zone_buttons.button(1).click()
    assert win._zone_stack.currentIndex() == 1


def test_the_sample_picker_offers_the_units_samples_and_none(win):
    _show(win, 0)
    combo = _kg(win, "soft_sample")
    names = [combo.itemData(i) for i in range(combo.count())]
    assert names == ["", "KICK", "SNARE", "PAD"]


def test_a_sample_the_unit_does_not_have_is_kept_and_flagged(win):
    win.rig.fake.programs[1] = prog.Program(
        name="ORPHAN", keygroups=[prog.Keygroup(soft_sample="NOPE", loud_sample="PAD")]
    )
    win._refresh()
    _show(win, 1)
    combo = _kg(win, "soft_sample")
    assert combo.currentData() == "NOPE" and "not on sampler" in combo.currentText()
    assert "Not on the sampler" in win._sample_warnings["soft"].text()
    assert win._sample_warnings["loud"].text() == ""  # PAD exists


def test_the_range_bar_and_graphs_follow_the_program(win):
    _show(win, 0)
    assert win.keygroup_range_bar._ranges == [(24, 59), (60, 127)]
    kg = win._working.keygroups[0]
    assert win.amp_graph._attack == kg.attack
    assert win.filter_graph._release == kg.filter_release


def test_control_bits_are_shown_but_not_editable(win):
    _show(win, 0)
    assert ("kg", "control_bits") not in win._editors
    assert "vibrato desync" in win._readonly["control_bits"].text()


def test_a_value_beyond_our_assumed_range_is_shown_truthfully(win):
    odd = prog.Program(name="ODD", keygroups=[prog.Keygroup(filter_key_track=120)])
    win.rig.fake.programs[1] = odd
    win._refresh()
    _show(win, 1)
    assert _kg(win, "filter_key_track").value() == 120
    assert win.is_dirty() is False


def test_a_failed_read_shows_a_message_and_does_not_retry_forever(win):
    win.rig.engine.reply_timeout_ms = 30
    _wait_listed(win)
    win.rig.fake.programs.pop(1)  # listed, then gone: the fake stays silent
    win.program_list.setCurrentRow(1)
    assert wait_until(lambda: win._shown_slot == 1)
    assert "Couldn't read program 1" in win.placeholder.text()
    assert win.detail_stack.isHidden() and win.keygroup_list.count() == 0
    assert not win.write_button.isEnabled()
    sent = len(win.rig.midi.sent)
    QCoreApplication.processEvents()
    assert len(win.rig.midi.sent) == sent  # no retry loop


def test_refresh_after_a_failure_tries_again(win):
    win.rig.engine.reply_timeout_ms = 30
    _wait_listed(win)
    fake_program = win.rig.fake.programs.pop(1)
    win.program_list.setCurrentRow(1)
    assert wait_until(lambda: win._shown_slot == 1 and win._working is None)
    win.rig.fake.programs[1] = fake_program
    win._refresh()
    assert wait_until(lambda: win._working is not None)
    assert win._editors[("program", "name")].text() == "PAD PROG"


def test_clicking_faster_than_the_unit_answers_ends_on_the_last_choice(win):
    _wait_listed(win)
    win.program_list.setCurrentRow(0)
    win.program_list.setCurrentRow(1)
    assert wait_until(lambda: win._working is not None and win._slot == 1)
    assert win._editors[("program", "name")].text() == "PAD PROG"
    assert win._inflight_slot is None


def test_reading_alone_never_writes(win):
    _show(win, 0)
    win.keygroup_list.setCurrentRow(1)
    from core import s950_sysex as s

    functions = {m[2] for m in win.rig.midi.sent}
    assert functions <= {s.FUNC_RCAT, s.FUNC_RPRGM}
    assert win.rig.fake.prgm_writes == 0 and win.rig.fake.sprm_writes == 0


def test_the_old_program_cannot_be_written_while_the_next_one_loads(win):
    _show(win, 0)
    _kg(win, "attack").setValue(5)
    win.rig.engine.reply_timeout_ms = 30
    win.rig.fake.programs.pop(1)
    win.program_list.setCurrentRow(1)  # discard confirmed (stubbed), then reading
    assert win._working is None
    assert not win.write_button.isEnabled() and win.detail_stack.isHidden()


# --- editing -----------------------------------------------------------------------------------


def test_editing_a_field_stages_it_without_writing(win):
    _show(win, 0)
    _kg(win, "attack").setValue(40)
    assert win.is_dirty()
    assert win._working.keygroups[0].attack == 40
    assert win._baseline.keygroups[0].attack != 40
    assert win.write_button.isEnabled()
    assert "1 unsaved change" in win.dirty_label.text()
    assert win.rig.fake.prgm_writes == 0
    assert win.amp_graph._attack == 40  # the graph follows live


def test_edits_apply_to_the_selected_keygroup_only(win):
    _show(win, 0)
    win.keygroup_list.setCurrentRow(1)
    _kg(win, "decay").setValue(7)
    assert win._working.keygroups[1].decay == 7
    assert win._working.keygroups[0].decay != 7


def test_editing_the_note_range_updates_the_list_row_and_range_bar(win):
    _show(win, 0)
    _kg(win, "upper_key").setValue(48)
    assert win.keygroup_row_text(0) == "Keygroup 1: C0 - C2"
    assert win.keygroup_range_bar._ranges[0] == (24, 48)


def test_transpose_is_edited_in_semitones_and_stored_in_sixteenths(win):
    _show(win, 0)
    _kg(win, "soft_transpose").setValue(1.5)
    assert win._working.keygroups[0].soft_transpose == 24


def test_picking_a_sample_by_name(win):
    _show(win, 0)
    combo = _kg(win, "soft_sample")
    combo.setCurrentIndex(combo.findData("PAD"))
    combo.activated.emit(combo.currentIndex())
    assert win._working.keygroups[0].soft_sample == "PAD"
    assert win._sample_warnings["soft"].text() == ""


def test_program_name_is_uppercased_and_stripped(win):
    _show(win, 0)
    edit = win._editors[("program", "name")]
    edit.setText("  my kit ")
    edit.editingFinished.emit()
    assert win._working.name == "MY KIT" and edit.text() == "MY KIT"


def test_editing_back_to_the_original_value_is_not_a_change(win):
    _show(win, 0)
    original = _kg(win, "attack").value()
    _kg(win, "attack").setValue(original + 1)
    assert win.is_dirty()
    _kg(win, "attack").setValue(original)
    assert not win.is_dirty()
    assert not win.write_button.isEnabled()


def test_discard_restores_the_loaded_program(win):
    _show(win, 0)
    _kg(win, "attack").setValue(40)
    win.discard_button.click()
    assert not win.is_dirty()
    assert _kg(win, "attack").value() == win._baseline.keygroups[0].attack


def test_changing_program_with_unsaved_edits_asks_first(win, monkeypatch):
    _show(win, 0)
    _kg(win, "attack").setValue(40)
    monkeypatch.setattr(viewer.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    win.program_list.setCurrentRow(1)
    assert win.program_list.currentRow() == 0  # selection put back
    assert win._working is not None and win._slot == 0 and win.is_dirty()
    assert win.rig.fake.prgm_writes == 0


# --- writing -----------------------------------------------------------------------------------


def test_write_sends_only_the_edit_then_shows_what_the_unit_holds(win):
    _show(win, 0)
    _kg(win, "attack").setValue(40)
    _write_and_wait(win)
    stored = win.rig.fake.programs[0]
    assert stored.keygroups[0].attack == 40
    assert stored.keygroups[1] == win._baseline.keygroups[1]
    assert not win.is_dirty()
    assert win._baseline.keygroups[0].attack == 40  # the unit's own read-back
    assert "verified" in win.status_bar.currentMessage()
    assert list(win.rig.engine.backup_dir.glob("prog00-*.syx"))


def test_the_first_write_warns_once_and_remembers(win, _isolated, monkeypatch):
    shown = []
    monkeypatch.setattr(
        viewer.QMessageBox,
        "warning",
        lambda parent, title, *a, **k: shown.append(title) or QMessageBox.StandardButton.Ok,
    )
    _show(win, 0)
    _kg(win, "attack").setValue(40)
    _write_and_wait(win)
    assert shown == ["Akai S900/S950 - experimental"] and _isolated["ack"] is True
    _kg(win, "attack").setValue(41)
    _write_and_wait(win)
    assert shown == ["Akai S900/S950 - experimental"]  # not asked again


def test_cancelling_the_warning_writes_nothing(win, monkeypatch):
    monkeypatch.setattr(viewer.QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    _show(win, 0)
    _kg(win, "attack").setValue(40)
    win.write_button.click()
    assert win._writing is False and win.rig.fake.prgm_writes == 0
    assert win.is_dirty()


def test_write_unchanged_is_a_no_op_that_verifies(win):
    _show(win, 0)
    before = win.rig.fake.programs[0].to_payload()
    assert win._write_unchanged_action.isEnabled()
    assert not win.write_button.isEnabled()
    win._write_unchanged_action.trigger()
    assert wait_until(lambda: not win._writing)
    assert win.rig.fake.prgm_writes == 1
    assert win.rig.fake.programs[0].to_payload() == before
    assert "verified" in win.status_bar.currentMessage()


def test_a_name_another_program_already_has_is_refused(win, monkeypatch):
    warned = []
    monkeypatch.setattr(
        viewer.QMessageBox, "warning", lambda parent, title, text, *a: warned.append(text) or QMessageBox.StandardButton.Ok
    )
    _show(win, 0)
    edit = win._editors[("program", "name")]
    edit.setText("pad prog")
    edit.editingFinished.emit()
    win.write_button.click()
    assert win.rig.fake.prgm_writes == 0
    assert any("already called" in text for text in warned)


def test_a_write_that_reads_back_differently_shows_the_units_truth(win, monkeypatch):
    warned = []
    monkeypatch.setattr(
        viewer.QMessageBox, "warning", lambda parent, title, text, *a: warned.append((title, text)) or QMessageBox.StandardButton.Ok
    )
    real = win.rig.fake._prgm

    def clamp(slot, payload):
        real(slot, payload)
        kg = win.rig.fake.programs[slot].keygroups[0]
        win.rig.fake.programs[slot].keygroups[0] = dataclasses.replace(kg, attack=kg.attack - 1)

    win.rig.fake._prgm = clamp
    _show(win, 0)
    _kg(win, "attack").setValue(40)
    _write_and_wait(win)
    assert any("not written cleanly" in title for title, _ in warned)
    assert _kg(win, "attack").value() == 39  # what the sampler says
    assert not win.is_dirty()


def test_a_failed_write_keeps_the_edits_on_screen(win, monkeypatch):
    win.rig.engine.reply_timeout_ms = 30
    _show(win, 0)
    _kg(win, "attack").setValue(40)
    win.rig.fake.programs.pop(0)  # gone: the pre-read gets no answer
    _write_and_wait(win)
    assert win.is_dirty() and win._working.keygroups[0].attack == 40
    assert win.rig.fake.prgm_writes == 0


def test_controls_are_locked_while_a_write_is_in_flight(win):
    _show(win, 0)
    _kg(win, "attack").setValue(40)
    win.write_button.click()
    assert win._writing
    assert win.detail_stack.isEnabled() is False
    assert win.write_button.isEnabled() is False
    assert win._dashboard_action.isEnabled() is False
    assert wait_until(lambda: not win._writing)
    assert win.detail_stack.isEnabled()


def test_restore_previous_writes_back_what_the_program_held(win, monkeypatch):
    monkeypatch.setattr(viewer.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Ok)
    _show(win, 0)
    original_attack = _kg(win, "attack").value()
    assert not win.restore_button.isEnabled()
    _kg(win, "attack").setValue(40)
    _write_and_wait(win)
    assert win.restore_button.isEnabled()
    win.restore_button.click()
    assert wait_until(lambda: not win._writing)
    assert win.rig.fake.programs[0].keygroups[0].attack == original_attack
    assert not win.restore_button.isEnabled()  # nothing further to restore


def test_restore_is_offered_only_for_the_program_it_belongs_to(win):
    _show(win, 0)
    _kg(win, "attack").setValue(40)
    _write_and_wait(win)
    win.program_list.setCurrentRow(1)
    assert wait_until(lambda: win._slot == 1 and win._working is not None)
    assert not win.restore_button.isEnabled()


# --- window plumbing -------------------------------------------------------------------------------


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


def test_closing_with_unsaved_edits_asks_and_can_be_cancelled(win, monkeypatch):
    _show(win, 0)
    _kg(win, "attack").setValue(40)
    shown = []
    win.main.show = lambda: shown.append(True)
    monkeypatch.setattr(viewer.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    win.close()
    assert shown == []
    monkeypatch.setattr(viewer.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    win.close()
    assert shown == [True]


def test_closing_disconnects_from_the_controller(win):
    win.main.show = lambda: None
    win.close()
    # a late catalog after close must not touch the closed window
    win.program_list.clear()
    win.rig.controller.program_slots_updated.emit([(0, "X")])
    assert win.program_list.count() == 0


def test_the_amounts_are_knobs_like_the_s3000_editor_and_the_rest_are_not(win):
    from ui.knob import Knob

    _show(win, 0)
    for attr in ("attack", "release", "filter_vel", "adsr_to_vcf", "lfo_rate", "soft_loudness"):
        assert isinstance(_kg(win, attr), Knob), attr
    for attr in ("lower_key", "velocity_switch", "midi_offset"):
        assert not isinstance(_kg(win, attr), Knob), attr
    assert isinstance(win._editors[("program", "key_tilt")], Knob)


def test_a_knob_at_zero_still_shows_its_value(win):
    _show(win, 0)
    readout = _kg(win, "attack").property("valueReadout")
    assert readout.text() == "0"  # setValue(0) on a fresh knob emits nothing


def test_editing_through_a_knob_stages_the_change(win):
    _show(win, 0)
    _kg(win, "decay").setValue(12)
    assert win._working.keygroups[0].decay == 12 and win.is_dirty()


def test_the_bottom_bar_has_refresh_on_the_left_and_close_on_the_right(win):
    buttons = [b for b in win.centralWidget().findChildren(type(win.refresh_button))]
    texts = [b.text() for b in buttons if b.parent() is win.centralWidget()]
    assert texts[0] == "Refresh" and texts[-1] == "Close"
