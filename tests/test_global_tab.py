# tests for ui/global_tab.py in isolation (the window wiring is in test_program_editor_window.py)

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from ui.global_tab import DISABLED_SETTINGS, GlobalSettingsTab


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp):
    tab = GlobalSettingsTab()
    chosen = []
    tab.setting_chosen.connect(lambda k, v: chosen.append((k, v)))
    tab.chosen = chosen
    yield tab
    tab.deleteLater()


ALL = {
    "external_controller": 1,
    "output_level": -6,
    "tune_semitones": -7,
    "tune_cents": 12,
    "program_change_channel": 3,
    "play_note": 72,
    "play_channel": 9,
    "play_velocity": 64,
    "scsi_disk_id": 2,
    "scsi_sector": 0,
    "scsi_local_id": 7,
}


def test_everything_starts_disabled_and_blank(tab):
    assert not any(w.isEnabled() for w in tab._widgets.values())
    assert tab.external_controller_combo.currentIndex() == -1


def test_set_values_shows_and_enables_without_emitting(tab):
    tab.set_values(ALL)
    w = tab._widgets
    assert w["output_level"].currentText() == "-6 dB"
    assert w["tune_semitones"].value() == -7 and w["tune_cents"].value() == 12
    assert w["program_change_channel"].currentText() == "3"
    assert w["play_note"].value() == 72
    assert w["scsi_sector"].currentText() == "512 B"
    assert w["scsi_local_id"].currentText() == "7"
    assert w["scsi_disk_id"].currentText() == "2" and w["play_channel"].currentText() == "9"
    assert tab._knob_labels["tune_semitones"].text() == "-7"
    assert tab._knob_labels["tune_cents"].text() == "12"
    assert all(x.isEnabled() for k, x in w.items() if k not in DISABLED_SETTINGS)
    for key in ("tune_semitones", "play_velocity"):
        assert not tab._timers[key].isActive()
    assert tab.chosen == []


def test_a_setting_that_was_not_read_stays_disabled(tab):
    tab.set_values({k: v for k, v in ALL.items() if k != "play_velocity"})
    assert not tab._widgets["play_velocity"].isEnabled()
    assert tab._widgets["play_note"].isEnabled()


def test_a_combo_choice_emits_its_value_not_its_index(tab):
    tab.set_values(ALL)
    combo = tab._widgets["output_level"]
    combo.setCurrentIndex(combo.findData(18))
    combo.activated.emit(combo.currentIndex())
    assert tab.chosen == [("output_level", 18)]

    pc = tab._widgets["program_change_channel"]
    pc.activated.emit(pc.findData(0))
    assert tab.chosen[-1] == ("program_change_channel", 0)


def test_the_external_controller_combo_emits_its_index(tab):
    tab.set_values(ALL)
    tab.external_controller_combo.activated.emit(2)
    assert tab.chosen == [("external_controller", 2)]


def test_tune_controls_are_small_bipolar_knobs(tab):
    from ui.knob import Knob

    for key in ("tune_semitones", "tune_cents"):
        knob = tab._widgets[key]
        assert isinstance(knob, Knob)
        assert (knob.width(), knob.height()) == (28, 28)
        assert (knob.minimum(), knob.maximum()) == (-50, 50)
        assert knob._bipolar and knob.defaultValue() == 0


def test_the_channel_and_scsi_id_controls_are_combos(tab):
    from PySide6.QtWidgets import QComboBox

    for key, count in (("play_channel", 16), ("scsi_disk_id", 8), ("scsi_local_id", 8)):
        assert isinstance(tab._widgets[key], QComboBox)
        assert tab._widgets[key].count() == count


def test_a_combo_choice_for_a_channel_or_id_emits_its_number(tab):
    tab.set_values(ALL)
    for key, value in (("play_channel", 16), ("scsi_disk_id", 0), ("scsi_local_id", 3)):
        combo = tab._widgets[key]
        combo.setCurrentIndex(combo.findData(value))
        combo.activated.emit(combo.currentIndex())
        assert tab.chosen[-1] == (key, value)


def test_tune_and_program_change_channel_are_shown_but_disabled(tab):
    # the sampler doesn't act on a written tune and ignores program-change-channel writes
    # (tools/s2000_tune_trigger_check.py) - the value still shows
    assert set(DISABLED_SETTINGS) == {"tune_semitones", "tune_cents", "program_change_channel"}
    tab.set_values(ALL)
    for key in DISABLED_SETTINGS:
        assert not tab._widgets[key].isEnabled()
        assert "Disabled for now" in tab._widgets[key].toolTip()
    assert tab._widgets["tune_semitones"].value() == -7
    assert tab._widgets["tune_cents"].value() == 12
    assert tab._widgets["program_change_channel"].currentText() == "3"
    # nothing else is locked
    for key, widget in tab._widgets.items():
        if key not in DISABLED_SETTINGS:
            assert widget.isEnabled() and "Disabled" not in widget.toolTip()


def test_a_knob_edit_is_debounced_and_a_release_commits_it(tab):
    tab.set_values(ALL)
    knob = tab._widgets["tune_cents"]
    knob.setValue(20)
    knob.setValue(25)
    assert tab.chosen == []
    assert tab._knob_labels["tune_cents"].text() == "25"  # live readout
    knob.sliderReleased.emit()
    assert tab.chosen == [("tune_cents", 25)]


def test_the_page_is_inset_from_the_tab_frame_on_every_side(tab):
    # the cards must not touch the tab widget's frame (the Multi tab's 14 px)
    page = tab.findChild(QWidget, "sectionCard").parentWidget()
    margins = page.layout().contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (14, 14, 14, 14)


def test_a_spinbox_edit_is_debounced_to_one_emit(tab):
    tab.set_values(ALL)
    box = tab._widgets["play_velocity"]
    box.setValue(10)
    box.setValue(11)
    box.setValue(12)
    assert tab.chosen == []
    tab.flush("play_velocity")
    assert tab.chosen == [("play_velocity", 12)]
    tab.flush("play_velocity")  # nothing pending any more
    assert len(tab.chosen) == 1


def test_set_unavailable_disables_everything_and_says_why(tab):
    tab.set_values(ALL)
    tab.set_unavailable("Not available in demo mode.")
    assert not any(w.isEnabled() for w in tab._widgets.values())
    assert tab._unavailable_label.text() == "Not available in demo mode."
    assert not tab._unavailable_label.isHidden()
    tab.set_values(ALL)
    assert tab._unavailable_label.isHidden()


def test_tooltips_exist_for_every_control(tab):
    assert all(w.toolTip() for w in tab._widgets.values())
