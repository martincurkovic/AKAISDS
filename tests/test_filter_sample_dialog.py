# tests for ui/filter_sample_dialog.py - the Samples tab's Filter dialog
# (the only one of Trim/Reverse/Fade/Normalise/Filter with actual
# parameters, hence its own small dialog rather than a plain
# QMessageBox.question - see program_editor_window.py's
# _confirm_filter_sample). Preview is exercised against a real
# SlicePreviewPlayer/miniaudio device where one's available, same
# reasoning tests/test_audio_preview.py already uses, skipped otherwise.

import os

# must be set BEFORE the first QApplication() call below - see
# test_slice_editor_window.py's own comment on this exact guard for why a
# file lacking it only runs offscreen by accident (whichever OTHER test
# module happens to get collected first)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from core import audio_preview
from ui.filter_sample_dialog import FilterSampleDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


_HAS_AUDIO_DEVICE = bool(audio_preview.list_output_devices())
_requires_audio_device = pytest.mark.skipif(
    not _HAS_AUDIO_DEVICE, reason="no audio output device available in this environment"
)


def _tone(n_frames, value=10000):
    return [value if i % 2 == 0 else -value for i in range(n_frames)]


def _build_dialog(samples=None, framerate=44100):
    samples = samples if samples is not None else _tone(4410)
    return FilterSampleDialog(None, "SQUARE", samples, framerate)


def _ok_button(dialog):
    return dialog._button_box.button(QDialogButtonBox.StandardButton.Ok)


# --- construction / defaults -------------------------------------------------


def test_lowpass_enabled_by_default_highpass_is_not(qapp):
    dialog = _build_dialog()
    assert dialog.lowpass_enabled() is True
    assert dialog.highpass_enabled() is False


def test_ok_button_enabled_by_default_since_lowpass_starts_enabled(qapp):
    dialog = _build_dialog()
    assert _ok_button(dialog).isEnabled() is True


def test_ok_button_disabled_when_both_filters_bypassed(qapp):
    dialog = _build_dialog()
    dialog.lp_group.setChecked(False)
    assert dialog.highpass_enabled() is False
    assert dialog.lowpass_enabled() is False
    assert _ok_button(dialog).isEnabled() is False


def test_ok_button_re_enabled_once_a_filter_is_turned_back_on(qapp):
    dialog = _build_dialog()
    dialog.lp_group.setChecked(False)
    assert _ok_button(dialog).isEnabled() is False
    dialog.hp_group.setChecked(True)
    assert _ok_button(dialog).isEnabled() is True


def test_knob_ranges_stay_strictly_inside_nyquist(qapp):
    dialog = _build_dialog(framerate=44100)
    nyquist = 44100 // 2
    assert dialog.hp_knob.maximum() == nyquist - 1
    assert dialog.lp_knob.maximum() == nyquist - 1


def test_knob_defaults_clamp_to_a_low_nyquist(qapp):
    # a low framerate sample's own Nyquist can sit below the usual
    # defaults - the knobs must never open above their own max
    dialog = _build_dialog(samples=_tone(200), framerate=1500)
    assert dialog.hp_knob.value() <= dialog.hp_knob.maximum()
    assert dialog.lp_knob.value() <= dialog.lp_knob.maximum()


def test_cutoff_accessors_reflect_the_knobs(qapp):
    dialog = _build_dialog()
    dialog.hp_knob.setValue(300)
    dialog.lp_knob.setValue(6000)
    assert dialog.highpass_cutoff_hz() == 300
    assert dialog.lowpass_cutoff_hz() == 6000


def test_slope_accessors_reflect_the_combos(qapp):
    dialog = _build_dialog()
    dialog.hp_slope_combo.setCurrentIndex(2)  # 36 dB/octave
    dialog.lp_slope_combo.setCurrentIndex(3)  # 48 dB/octave
    assert dialog.highpass_slope_db_per_octave() == 36
    assert dialog.lowpass_slope_db_per_octave() == 48


def test_slope_defaults_to_12db_per_octave_for_both(qapp):
    dialog = _build_dialog()
    assert dialog.highpass_slope_db_per_octave() == 12
    assert dialog.lowpass_slope_db_per_octave() == 12


# --- double-click resets to "fully open" (per direct user request) --------


def test_double_clicking_the_highpass_knob_resets_to_its_own_minimum(qapp):
    # "fully open" for a highpass is its own minimum (20Hz) - cutoff at
    # the very bottom, letting everything through
    dialog = _build_dialog()
    dialog.hp_knob.setValue(5000)
    assert dialog.hp_knob.defaultValue() == dialog.hp_knob.minimum() == 20
    dialog.hp_knob.setValue(dialog.hp_knob.defaultValue())  # simulates the double-click
    assert dialog.highpass_cutoff_hz() == 20


def test_double_clicking_the_lowpass_knob_resets_to_its_own_maximum(qapp):
    # "fully open" for a lowpass is its own maximum - cutoff at the very
    # top, letting everything through
    dialog = _build_dialog(framerate=44100)
    dialog.lp_knob.setValue(500)
    assert dialog.lp_knob.defaultValue() == dialog.lp_knob.maximum() == 44100 // 2 - 1
    dialog.lp_knob.setValue(dialog.lp_knob.defaultValue())  # simulates the double-click
    assert dialog.lowpass_cutoff_hz() == 44100 // 2 - 1


def test_knobs_open_at_a_sensible_value_not_already_fully_open(qapp):
    # defaultValue (the double-click target) must NOT be what the knob
    # opens showing - opening already "fully open" would make the filter
    # a no-op the instant the group box is checked
    dialog = _build_dialog()
    assert dialog.highpass_cutoff_hz() != dialog.hp_knob.defaultValue()
    assert dialog.lowpass_cutoff_hz() != dialog.lp_knob.defaultValue()


# --- accept/reject stop any in-flight preview -------------------------------


def test_reject_stops_the_preview_player(qapp, monkeypatch):
    dialog = _build_dialog()
    stopped = []
    monkeypatch.setattr(dialog._preview_player, "stop", lambda: stopped.append(True))
    dialog.reject()
    assert stopped == [True]


def test_accept_stops_the_preview_player(qapp, monkeypatch):
    dialog = _build_dialog()
    stopped = []
    monkeypatch.setattr(dialog._preview_player, "stop", lambda: stopped.append(True))
    dialog.accept()
    assert stopped == [True]


def test_reject_result_is_rejected(qapp):
    dialog = _build_dialog()
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected


# --- retriggering preview on setting changes --------------------------------


def test_changing_cutoff_while_idle_does_not_start_playback(qapp, monkeypatch):
    dialog = _build_dialog()
    calls = []
    monkeypatch.setattr(dialog, "_preview", lambda: calls.append(True))
    dialog.lp_knob.setValue(2000)
    assert calls == []


def test_changing_cutoff_while_playing_retriggers_preview(qapp, monkeypatch):
    dialog = _build_dialog()
    monkeypatch.setattr(dialog._preview_player, "is_playing", lambda: True)
    calls = []
    monkeypatch.setattr(dialog, "_preview", lambda: calls.append(True))
    dialog.lp_knob.setValue(2000)
    assert calls == [True]


def test_changing_highpass_settings_while_playing_retriggers_preview(qapp, monkeypatch):
    dialog = _build_dialog()
    monkeypatch.setattr(dialog._preview_player, "is_playing", lambda: True)
    calls = []
    monkeypatch.setattr(dialog, "_preview", lambda: calls.append(True))
    dialog.hp_group.setChecked(True)
    assert calls == [True]


def test_changing_slope_while_playing_retriggers_preview(qapp, monkeypatch):
    dialog = _build_dialog()
    monkeypatch.setattr(dialog._preview_player, "is_playing", lambda: True)
    calls = []
    monkeypatch.setattr(dialog, "_preview", lambda: calls.append(True))
    dialog.lp_slope_combo.setCurrentIndex(2)
    assert calls == [True]


def test_bypassing_both_filters_while_playing_stops_instead_of_retriggering(
    qapp, monkeypatch
):
    dialog = _build_dialog()
    monkeypatch.setattr(dialog._preview_player, "is_playing", lambda: True)
    preview_calls = []
    stop_calls = []
    monkeypatch.setattr(dialog, "_preview", lambda: preview_calls.append(True))
    monkeypatch.setattr(dialog._preview_player, "stop", lambda: stop_calls.append(True))

    dialog.lp_group.setChecked(False)  # both now bypassed (hp defaults off)

    assert preview_calls == []
    assert stop_calls == [True]


# --- Preview actually plays filtered audio (real device) --------------------


@_requires_audio_device
def test_preview_starts_playback(qapp):
    dialog = _build_dialog()
    dialog._preview()
    assert dialog._preview_player.is_playing() is True
    dialog._preview_player.stop()
