# tests for ui/filter_sample_dialog.py - the Samples tab's Filter dialog
# (the only one of Trim/Reverse/Fade/Normalise/Filter with an actual
# parameter, hence its own small dialog rather than a plain
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
from PySide6.QtWidgets import QApplication, QDialog

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


# --- construction / defaults -------------------------------------------------


def test_defaults_to_lowpass(qapp):
    dialog = _build_dialog()
    assert dialog.filter_type() == "lowpass"


def test_cutoff_spinbox_range_stays_strictly_inside_nyquist(qapp):
    dialog = _build_dialog(framerate=44100)
    assert dialog.cutoff_spin.minimum() == 20
    assert dialog.cutoff_spin.maximum() == 44100 // 2 - 1


def test_cutoff_default_clamps_to_a_low_nyquist(qapp):
    # a low framerate sample's own Nyquist can sit below the usual 1000Hz
    # default - the spinbox must never open with a value above its own max
    dialog = _build_dialog(samples=_tone(200), framerate=1500)
    assert dialog.cutoff_spin.value() <= dialog.cutoff_spin.maximum()


def test_filter_type_reflects_the_combo_selection(qapp):
    dialog = _build_dialog()
    dialog.type_combo.setCurrentIndex(1)  # Highpass
    assert dialog.filter_type() == "highpass"


def test_cutoff_hz_reflects_the_spinbox(qapp):
    dialog = _build_dialog()
    dialog.cutoff_spin.setValue(2500)
    assert dialog.cutoff_hz() == 2500


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
    dialog.cutoff_spin.setValue(2000)
    assert calls == []


def test_changing_cutoff_while_playing_retriggers_preview(qapp, monkeypatch):
    dialog = _build_dialog()
    monkeypatch.setattr(dialog._preview_player, "is_playing", lambda: True)
    calls = []
    monkeypatch.setattr(dialog, "_preview", lambda: calls.append(True))
    dialog.cutoff_spin.setValue(2000)
    assert calls == [True]


def test_changing_filter_type_while_playing_retriggers_preview(qapp, monkeypatch):
    dialog = _build_dialog()
    monkeypatch.setattr(dialog._preview_player, "is_playing", lambda: True)
    calls = []
    monkeypatch.setattr(dialog, "_preview", lambda: calls.append(True))
    dialog.type_combo.setCurrentIndex(1)
    assert calls == [True]


# --- Preview actually plays filtered audio (real device) --------------------


@_requires_audio_device
def test_preview_starts_playback(qapp):
    dialog = _build_dialog()
    dialog._preview()
    assert dialog._preview_player.is_playing() is True
    dialog._preview_player.stop()
