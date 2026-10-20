# ui/reverb_dialog.py and the Samples tab's Reverb button: the dialog's knobs, presets and limits, and OK / Send as New Sample on the working copy.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QLabel

from core import reverb
from test_program_editor_window import (  # noqa: F401
    FakeSamplerController,
    _pump_until,
    _select_sample_with_full_audio,
    editor,
    qapp,
)
from test_staged_edits import _MARKERS, _ORIGINAL, staged_editor  # noqa: F401
from ui.reverb_dialog import ReverbDialog


def make(samples=None, **kwargs):
    return ReverbDialog(None, "KICK", samples if samples is not None else [0] * 4410, 44100, **kwargs)


def _sent(editor):
    return editor._test_fake.sent_entries


# -- the dialog -------------------------------------------------------------------------------------------------------


def test_the_knobs_start_at_the_prototypes_defaults(qapp):
    dialog = make()
    decay, size, damping, mix = reverb.DEFAULTS
    assert (dialog.decay_knob.value(), dialog.size_knob.value(), dialog.damping_knob.value(), dialog.mix_knob.value()) == (decay, size, damping, mix)
    assert dialog.options() == dict(decay=decay, size=size, damping=damping, mix=mix, framerate=44100)
    assert dialog.preset_combo.currentText() == "Custom"


def test_a_preset_sets_the_knobs_and_a_moved_knob_goes_back_to_custom(qapp):
    dialog = make()
    index = dialog.preset_combo.findText("Hall")
    dialog.preset_combo.setCurrentIndex(index)
    dialog._on_preset_chosen(index)
    assert tuple(k.value() for k in dialog._knobs()) == reverb.PRESETS["Hall"]
    assert dialog.preset_combo.currentText() == "Hall"
    dialog.decay_knob.setValue(dialog.decay_knob.value() - 1)
    assert dialog.preset_combo.currentText() == "Custom"
    dialog.decay_knob.setValue(reverb.PRESETS["Hall"][0])
    assert dialog.preset_combo.currentText() == "Hall"  # back on the preset's numbers


def test_choosing_custom_leaves_the_knobs_alone(qapp):
    dialog = make()
    dialog.decay_knob.setValue(70)
    dialog._on_preset_chosen(0)
    assert dialog.decay_knob.value() == 70


def test_the_value_labels_show_seconds_a_scale_steps_and_percent(qapp):
    from ui import reverb_dialog

    assert reverb_dialog._decay_text(0) == "0.1 s" and reverb_dialog._decay_text(99) == "5.0 s"
    assert reverb_dialog._size_text(50) == "x1.2" and reverb_dialog._size_text(0) == "x0.4"
    assert reverb_dialog._damping_text(0) == "none" and reverb_dialog._damping_text(99) == "5 / 5"
    assert reverb_dialog._mix_text(0) == "0 %" and reverb_dialog._mix_text(99) == "100 %"
    dialog = make()
    labels = [w.text() for w in dialog.findChildren(QLabel)]
    assert "0.7 s" in labels and "30 %" in labels


def test_no_wet_means_nothing_to_apply(qapp):
    dialog = make(allow_new_sample=True)
    ok = dialog._button_box.button(dialog._button_box.StandardButton.Ok)
    assert ok.isEnabled() and dialog.new_sample_button.isEnabled()
    dialog.mix_knob.setValue(0)
    assert not ok.isEnabled() and not dialog.new_sample_button.isEnabled()


def test_a_result_that_could_not_fit_in_sample_memory_is_refused(qapp, monkeypatch):
    monkeypatch.setattr(reverb, "MAX_OUTPUT_FRAMES", 4410 + 1000)
    dialog = make()
    assert not dialog._button_box.button(dialog._button_box.StandardButton.Ok).isEnabled()
    assert not dialog.preview_button.isEnabled() and "Too long" in dialog.result_label.text()


def test_the_result_line_says_how_long_the_tail_can_be(qapp):
    dialog = make()
    assert "0.10 s, plus up to 0.7 s of tail" in dialog.result_label.text()
    dialog.decay_knob.setValue(99)
    assert "plus up to 5.0 s of tail" in dialog.result_label.text()


def test_the_wording_depends_on_what_ok_does(qapp):
    assert "OK applies it to the sample here" in make(applies_to_working_copy=True).warning_label.text()
    assert "Send as New Sample sends it now" in make(applies_to_working_copy=True, allow_new_sample=True).warning_label.text()
    assert "cannot be undone" in make().warning_label.text()


def test_preview_plays_the_reverberated_sample_through_the_player(qapp, monkeypatch):
    dialog = make(samples=[3000, -3000] * 500, pitch_shift_semitones=2)
    played = []
    monkeypatch.setattr(dialog._preview_player, "play", lambda *args, **kwargs: played.append((args, kwargs)))
    dialog._preview()
    (samples, start, end, rate), kwargs = played[0][0], played[0][1]
    assert len(samples) > 1000 and start == 0 and end == len(samples) - 1 and rate == 44100
    assert kwargs == {"pitch_shift_semitones": 2}


def test_preview_does_nothing_when_there_is_no_reverb_to_hear(qapp, monkeypatch):
    dialog = make()
    dialog.mix_knob.setValue(0)
    monkeypatch.setattr(dialog._preview_player, "play", lambda *a, **k: pytest.fail("played"))
    dialog._preview()


def test_send_as_new_is_reported(qapp):
    dialog = make(allow_new_sample=True)
    assert not dialog.send_as_new()
    dialog.new_sample_button.click()
    assert dialog.send_as_new()


# -- the Samples tab --------------------------------------------------------------------------------------------------


def test_reverb_ok_applies_to_the_working_copy_and_sends_nothing(staged_editor, monkeypatch):
    editor = staged_editor

    def fake_exec(self):
        self.decay_knob.setValue(40)
        self.mix_knob.setValue(60)
        return True

    monkeypatch.setattr(ReverbDialog, "exec", fake_exec)
    editor._confirm_reverb()
    staged = editor._staged_edits["SQUARE"]
    assert len(staged.samples) > 100 and _sent(editor) == []
    assert staged.original_samples == _ORIGINAL
    assert editor._sample_waveform_cache[0]["frame_count"] == len(staged.samples)
    assert "Reverb applied" in editor.status_bar.currentMessage() and "not on the sampler" in editor.status_bar.currentMessage()
    # the sample's own End (set earlier than the last frame) and the rest of the markers stay where the user put them
    entry = editor._sample_waveform_cache[0]
    assert {k: entry[k] for k in _MARKERS} == _MARKERS


def test_reverb_can_be_undone_back_to_the_samplers_version(staged_editor, monkeypatch):
    editor = staged_editor
    monkeypatch.setattr(ReverbDialog, "exec", lambda self: True)
    editor._confirm_reverb()
    assert len(editor._staged_edits["SQUARE"].samples) > 100
    editor._undo_staged()
    assert "SQUARE" not in editor._staged_edits
    assert editor._sample_waveform_cache[0]["samples"] == _ORIGINAL


def test_cancelling_the_dialog_changes_nothing(staged_editor, monkeypatch):
    editor = staged_editor
    monkeypatch.setattr(ReverbDialog, "exec", lambda self: False)
    editor._confirm_reverb()
    assert editor._staged_edits == {} and _sent(editor) == []


def test_reverb_send_as_new_sends_at_once_and_leaves_the_original(staged_editor, monkeypatch):
    editor = staged_editor
    asked = []
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda title, text, default: asked.append(default) or "SQUARE REV")

    def fake_exec(self):
        self.new_sample_button.click()
        return True

    monkeypatch.setattr(ReverbDialog, "exec", fake_exec)
    editor._confirm_reverb()
    assert asked == ["SQUARE REV"]
    assert len(_sent(editor)) == 1 and _sent(editor)[0]["name"] == "SQUARE REV"
    assert editor._staged_edits == {}


def test_the_reverb_button_is_beside_stretch_and_follows_the_audio(editor, qapp, monkeypatch):
    button, action = editor._sample_edit_controls["reverb"]
    assert button.text() == "Reverb…" and not button.isEnabled()
    keys = [k for group in __import__("ui.program_editor_window", fromlist=["x"])._EDIT_SAMPLE_ROWS[0][2] for k in group]
    assert keys.index("reverb") == keys.index("stretch") + 1
    editor._set_sample_edit_buttons_enabled(True)
    assert button.isEnabled()
