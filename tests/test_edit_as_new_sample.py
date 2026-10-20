# "Send as New Sample" in the Filter and Stretch / Pitch dialogs: the same edit, sent through the Duplicate Sample flow as a NEW
# sample - the original is never touched. Plus the Stretch / Pitch dialog keeping one size across its two modes.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QDialogButtonBox

from test_program_editor_window import (  # noqa: F401
    FakeSamplerController,
    _select_sample_with_full_audio,
    editor,
    qapp,
)
from ui.filter_sample_dialog import FilterSampleDialog
from ui.time_stretch_dialog import PITCH, STRETCH, TimeStretchDialog

_MARKERS = {"start": 10, "loop_start": 30, "loop_end": 60, "end": 90}


def _choose_new_sample(monkeypatch, dialog_class, **setup):
    """Stand-in for exec(): set the controls (`setup`: attribute -> knob value) and click Send as New Sample."""

    def fake_exec(self):
        for name, value in setup.items():
            getattr(self, name).setValue(value)
        self.new_sample_button.click()
        return True

    monkeypatch.setattr(dialog_class, "exec", fake_exec)


@pytest.fixture
def real_mode(editor, qapp, monkeypatch):
    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    fake = FakeSamplerController(editor._bridge)
    editor._main_window.sampler_controller = fake
    original = [int(8000 * ((i % 20) / 10 - 1)) for i in range(100)]
    _select_sample_with_full_audio(editor, qapp, 0, original, dict(_MARKERS))  # sample 0 == "SQUARE"
    editor._test_original = original
    editor._test_fake = fake
    return editor


def _header_calls(editor, name):
    index = editor._bridge.sample_list().index(name)
    return {c[0]: c[2] for c in editor._bridge.set_parameter_calls if c[1] == index}


def test_filter_as_new_sample_sends_the_filtered_audio_under_a_new_name_and_leaves_the_original(real_mode, monkeypatch):
    editor = real_mode
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda *a, **k: "SQUARE FILT")
    _choose_new_sample(monkeypatch, FilterSampleDialog)

    editor._confirm_filter_sample()

    sent = editor._test_fake.sent_entries
    assert len(sent) == 1 and sent[0]["name"] == "SQUARE FILT"
    assert sent[0]["samples"] != editor._test_original and len(sent[0]["samples"]) == 100  # filtered, same length
    assert "SQUARE" in editor._bridge.sample_list() and "SQUARE FILT" in editor._bridge.sample_list()
    assert not any(c[0] == "SHNAME" for c in editor._bridge.set_parameter_calls)  # nothing renamed, nothing replaced
    header = _header_calls(editor, "SQUARE FILT")
    assert (header["SSTART"], header["SMPEND"], header["LOOPAT1"]) == (10, 90, 60)
    assert "Filter complete" in editor.status_bar.currentMessage()
    # the original's audio is still what the cache holds for it
    assert editor._sample_name_at_row(editor.sample_list_widget.currentRow()) == "SQUARE FILT"


def test_stretch_as_new_sample_sends_the_longer_audio_with_scaled_markers(real_mode, monkeypatch):
    editor = real_mode
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda *a, **k: "SQUARE STR")
    _choose_new_sample(monkeypatch, TimeStretchDialog, length_knob=200, grain_knob=10)

    editor._confirm_time_stretch()

    sent = editor._test_fake.sent_entries
    assert len(sent) == 1 and sent[0]["name"] == "SQUARE STR" and len(sent[0]["samples"]) == 200
    header = _header_calls(editor, "SQUARE STR")
    scale = 199 / 99
    assert header["SSTART"] == round(10 * scale) and header["SMPEND"] == round(90 * scale)
    assert header["LLNGTH1"] == (round(60 * scale) - round(30 * scale)) * 65536
    assert "SQUARE" in editor._bridge.sample_list()
    assert "Time Stretch complete" in editor.status_bar.currentMessage()


def test_pitch_shift_as_new_sample_keeps_the_length_and_markers(real_mode, monkeypatch):
    editor = real_mode
    seen = {}

    def prompt(self_or_title, title=None, label=None, default=None, *a):
        seen["default"] = default
        return "SQUARE PITCH"

    monkeypatch.setattr(editor, "_prompt_akai_name", lambda title, label, default: prompt(None, title, label, default))

    def fake_exec(self):
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(PITCH))
        self.semitones_knob.setValue(7)
        self.new_sample_button.click()
        return True

    monkeypatch.setattr(TimeStretchDialog, "exec", fake_exec)
    editor._confirm_time_stretch()
    sent = editor._test_fake.sent_entries
    assert len(sent) == 1 and sent[0]["name"] == "SQUARE PITCH" and len(sent[0]["samples"]) == 100
    assert seen["default"] == "SQUARE PITCH"  # the prompt starts from the original's name + the edit's suffix
    assert _header_calls(editor, "SQUARE PITCH")["SMPEND"] == 90


def test_a_name_already_in_use_is_refused_and_nothing_is_sent(real_mode, monkeypatch):
    import ui.program_editor_window as pew

    editor = real_mode
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda *a, **k: "SQUARE")  # the original's own name
    warned = []
    monkeypatch.setattr(pew.QMessageBox, "warning", lambda *a, **k: warned.append(a))
    _choose_new_sample(monkeypatch, FilterSampleDialog)
    editor._confirm_filter_sample()
    assert warned and editor._test_fake.sent_entries == []


def test_cancelling_the_name_prompt_sends_nothing(real_mode, monkeypatch):
    editor = real_mode
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda *a, **k: None)
    _choose_new_sample(monkeypatch, FilterSampleDialog)
    editor._confirm_filter_sample()
    assert editor._test_fake.sent_entries == []
    assert editor._sample_menu.isEnabled()  # (the window isn't left frozen)


def test_the_dialogs_only_offer_a_new_sample_where_the_editor_can_make_one(real_mode, monkeypatch):
    editor = real_mode
    offered = {}
    for dialog_class in (FilterSampleDialog, TimeStretchDialog):
        monkeypatch.setattr(
            dialog_class, "exec", lambda self, c=dialog_class: offered.setdefault(c, self.new_sample_button is not None) and False
        )
    editor._confirm_filter_sample()
    editor._confirm_time_stretch()
    assert offered == {FilterSampleDialog: True, TimeStretchDialog: True}
    # demo mode (like Duplicate Sample): no add-sample primitive, so no button
    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    offered.clear()
    editor._confirm_filter_sample()
    editor._confirm_time_stretch()
    assert offered == {FilterSampleDialog: False, TimeStretchDialog: False}


# --- the dialogs -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("dialog_class", [FilterSampleDialog, TimeStretchDialog])
def test_the_new_sample_button_is_optional_and_off_by_default(qapp, dialog_class):
    assert dialog_class(None, "KICK", [0] * 1000, 44100).new_sample_button is None
    dialog = dialog_class(None, "KICK", [0] * 1000, 44100, allow_new_sample=True)
    assert dialog.new_sample_button is not None and not dialog.send_as_new()
    assert "Send as New Sample" in dialog.new_sample_button.text()


def test_the_new_sample_button_accepts_the_dialog_and_follows_ok_s_enabled_state(qapp):
    dialog = TimeStretchDialog(None, "KICK", [0] * 1000, 44100, allow_new_sample=True)
    ok = dialog._button_box.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled() and not dialog.new_sample_button.isEnabled()  # 100%: nothing to send
    dialog.length_knob.setValue(150)
    assert ok.isEnabled() and dialog.new_sample_button.isEnabled()
    dialog.new_sample_button.click()
    assert dialog.send_as_new() and dialog.result() == dialog.DialogCode.Accepted

    plain = TimeStretchDialog(None, "KICK", [0] * 1000, 44100, allow_new_sample=True)
    plain.length_knob.setValue(150)
    plain._button_box.button(QDialogButtonBox.StandardButton.Ok).click()
    assert not plain.send_as_new()  # OK is still "replace"


def test_the_filter_dialogs_new_sample_button_follows_its_filters(qapp):
    dialog = FilterSampleDialog(None, "KICK", [0] * 1000, 44100, allow_new_sample=True)
    assert dialog.new_sample_button.isEnabled()  # the lowpass starts on
    dialog.lp_group.setChecked(False)
    assert not dialog.new_sample_button.isEnabled()
    dialog.hp_group.setChecked(True)
    dialog.new_sample_button.click()
    assert dialog.send_as_new()


@pytest.mark.parametrize("allow", [False, True])
def test_the_stretch_dialog_keeps_one_size_when_the_mode_is_switched_back_and_forth(qapp, allow):
    # it used to grow a little each time it was switched to Pitch Shift and back
    dialog = TimeStretchDialog(None, "KICK", [0] * 44100, 44100, allow_new_sample=allow)
    dialog.show()
    qapp.processEvents()
    opened = dialog.size()
    for mode in (PITCH, STRETCH, PITCH, STRETCH):
        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData(mode))
        qapp.processEvents()
        assert dialog.size() == opened, mode
    dialog.hide()
