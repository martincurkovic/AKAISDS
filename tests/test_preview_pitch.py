# The Preview buttons of the Filter and Stretch / Pitch dialogs play the sample the way the sampler would: shifted by its Tune and
# Root Note (the same shift the Samples tab's click-to-preview uses). They played the raw audio before.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core import audio_preview
from test_program_editor_window import (  # noqa: F401
    _pump_until,
    _stub_demo_audio,
    editor,
    qapp,
)
from ui.filter_sample_dialog import FilterSampleDialog
from ui.time_stretch_dialog import TimeStretchDialog


@pytest.fixture
def played(monkeypatch):
    """Every SlicePreviewPlayer.play call: (frames played, framerate, pitch_shift_semitones)."""
    calls = []

    def fake_play(self, samples, start, end, framerate, pitch_shift_semitones=0, right_samples=None):
        calls.append((len(samples), framerate, pitch_shift_semitones))

    monkeypatch.setattr(audio_preview.SlicePreviewPlayer, "play", fake_play)
    monkeypatch.setattr(audio_preview.SlicePreviewPlayer, "is_playing", lambda self: False)
    return calls


def test_the_filter_dialog_previews_with_the_samples_pitch_shift(qapp, played):
    dialog = FilterSampleDialog(None, "KICK", [100] * 2000, 44100, pitch_shift_semitones=-7.5)
    dialog._preview()
    assert played == [(2000, 44100, -7.5)]


def test_the_time_stretch_dialog_previews_with_the_samples_pitch_shift(qapp, played):
    dialog = TimeStretchDialog(None, "KICK", [100] * 2000, 44100, pitch_shift_semitones=3.25)
    dialog.length_knob.setValue(200)
    dialog._preview()
    assert played == [(4000, 44100, 3.25)]
    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("pitch"))
    dialog.semitones_knob.setValue(5)
    dialog._preview()
    assert played[-1] == (2000, 44100, 3.25)  # the chosen pitch change is in the audio; the sample's own shift still applies


def test_the_default_is_no_shift(qapp, played):
    FilterSampleDialog(None, "KICK", [1] * 100, 44100)._preview()
    assert played[-1][2] == 0


def _loaded_editor(editor, qapp, monkeypatch):
    import ui.program_editor_window as pew

    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    monkeypatch.setattr(pew, "_DEMO_MS_PER_WORD", 0.001)
    _stub_demo_audio(editor, monkeypatch, list(range(500)))
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    editor._load_sample_waveform()
    return editor._sample_waveform_cache[0]


def test_the_editor_shifts_by_root_note_and_tune_exactly_like_the_main_preview(editor, qapp, monkeypatch):
    entry = _loaded_editor(editor, qapp, monkeypatch)
    entry["spitch"], entry["stuno"] = 60, 0
    at_c3 = editor._preview_pitch_shift_semitones(entry)
    entry["spitch"] = 48  # the sample's root is an octave BELOW the preview note: it has to be shifted up an octave
    assert editor._preview_pitch_shift_semitones(entry) == pytest.approx(at_c3 + 12)
    entry["spitch"] = 60
    entry["stuno"] = 256 * 3  # +3 semitones of Tune (STUNO is signed 1/256 semitone)
    assert editor._preview_pitch_shift_semitones(entry) == pytest.approx(at_c3 + 3)


def test_the_editor_gives_both_dialogs_that_shift(editor, qapp, monkeypatch):
    entry = _loaded_editor(editor, qapp, monkeypatch)
    entry["spitch"], entry["stuno"] = 53, 128  # root F2, +0.5 semitone
    expected = editor._preview_pitch_shift_semitones(entry)
    assert expected != 0
    seen = []
    for dialog_class in (FilterSampleDialog, TimeStretchDialog):
        monkeypatch.setattr(dialog_class, "exec", lambda self: seen.append(self._pitch_shift_semitones) or False)
    editor.filter_sample_button.click()
    editor.time_stretch_button.click()
    assert seen == [pytest.approx(expected), pytest.approx(expected)]
