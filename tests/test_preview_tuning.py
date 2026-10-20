# Every preview plays a sample as the sampler would: shifted by its root note and tune (core/preview_pitch.py). The Program Editor's
# click-to-preview and its Filter / Stretch / Pitch dialogs were covered in test_preview_pitch.py; this file covers the Slice Editor,
# the Yamaha editor (its click-to-preview, dialogs and slicer), the shared formulas and the Stretch / Pitch dialog's result line.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core import audio_preview, preview_pitch
from core import yamaha_params as yp
from test_program_editor_window import _pump_until, _stub_demo_audio, editor, qapp  # noqa: F401
from test_slice_editor_window import _build_window
from test_yamaha_markers_ui import Recorder, put, user_sample
from test_yamaha_program_editor import warning_acknowledged  # noqa: F401
from test_yamaha_waveform import window  # noqa: F401
from ui import yamaha_sample_edit as edit_module
from ui.filter_sample_dialog import FilterSampleDialog
from ui.slice_editor_window import SliceEditorWindow
from ui.time_stretch_dialog import TimeStretchDialog


# --- the formulas ---------------------------------------------------------------------------------------------------------


def test_the_yamaha_shift_goes_from_original_key_to_the_preview_note_plus_the_tuning():
    assert preview_pitch.yamaha_shift_semitones(60, 0, 0) == 0
    assert preview_pitch.yamaha_shift_semitones(48, 0, 0) == 12  # an octave below the preview note: shifted up
    assert preview_pitch.yamaha_shift_semitones(72, 0, 0) == -12
    assert preview_pitch.yamaha_shift_semitones(60, 2, 0) == 2
    assert preview_pitch.yamaha_shift_semitones(60, 0, 50) == pytest.approx(0.5)  # 50 fine steps = 50 cents at the default step size
    assert preview_pitch.yamaha_shift_semitones(60, 0, 50, fine_steps_per_cent=2.0) == pytest.approx(0.25)
    assert preview_pitch.yamaha_shift_semitones(48, 2, -25) == pytest.approx(13.75)


def test_played_seconds_gets_shorter_when_the_pitch_goes_up():
    assert preview_pitch.played_seconds(44100, 44100, 0) == pytest.approx(1.0)
    assert preview_pitch.played_seconds(44100, 44100, 12) == pytest.approx(0.5)
    assert preview_pitch.played_seconds(44100, 44100, -12) == pytest.approx(2.0)
    assert preview_pitch.played_seconds(10, 0, 0) == 0.0


# --- the result line --------------------------------------------------------------------------------------------------------


def test_the_result_line_says_how_long_the_preview_lasts_when_the_pitch_is_shifted(qapp):
    plain = TimeStretchDialog(None, "KICK", [0] * 44100, 44100)
    assert plain.result_label.text() == "44,100 frames, 1.00 s"
    up = TimeStretchDialog(None, "KICK", [0] * 44100, 44100, pitch_shift_semitones=12)
    assert up.result_label.text() == "44,100 frames, 1.00 s (0.50 s as previewed)"
    up.length_knob.setValue(200)
    assert up.result_label.text() == "88,200 frames, 2.00 s (1.00 s as previewed)"
    down = TimeStretchDialog(None, "KICK", [0] * 44100, 44100, pitch_shift_semitones=-12)
    assert "(2.00 s as previewed)" in down.result_label.text()
    nearly = TimeStretchDialog(None, "KICK", [0] * 44100, 44100, pitch_shift_semitones=0.001)
    assert "previewed" not in nearly.result_label.text()  # too small to matter


# --- the Slice Editor -------------------------------------------------------------------------------------------------------


@pytest.fixture
def played(monkeypatch):
    calls = []

    def fake_play(self, samples, start, end, framerate, pitch_shift_semitones=0, right_samples=None):
        calls.append((start, end, framerate, pitch_shift_semitones))

    monkeypatch.setattr(audio_preview.SlicePreviewPlayer, "play", fake_play)
    return calls


def test_the_slice_editor_previews_a_slice_shifted_like_the_sampler_plays_it(qapp, played):
    window = _build_window()
    window._preview_pitch_shift_semitones = 4.5
    window._preview_slice(0, 99)
    assert played == [(0, 99, 44100, 4.5)]


def test_the_slice_editor_has_no_shift_unless_it_is_given_one(qapp, played):
    window = _build_window()  # audio from a file: no tune, no root note
    window._preview_slice(0, 99)
    assert played[-1][3] == 0
    shifted = SliceEditorWindow(
        None, "SQUARE", [0] * 200, 44100, 60, 0, 0, lambda: [], lambda *a: True, preview_pitch_shift_semitones=-3
    )
    shifted._preview_slice(0, 99)
    assert played[-1][3] == -3


def test_the_program_editor_opens_the_slice_editor_with_the_samples_shift(editor, qapp, monkeypatch):
    import ui.program_editor_window as pew

    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    monkeypatch.setattr(pew, "_DEMO_MS_PER_WORD", 0.001)
    _stub_demo_audio(editor, monkeypatch, list(range(500)))
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    editor._load_sample_waveform()
    entry = editor._sample_waveform_cache[0]
    entry["spitch"], entry["stuno"] = 48, 0
    seen = {}
    run_slice_editor = editor._run_slice_editor
    monkeypatch.setattr(editor, "_run_slice_editor", lambda *a, **k: seen.update(k))
    editor._open_slice_editor()
    assert seen["preview_pitch_shift"] == pytest.approx(editor._preview_pitch_shift_semitones(entry))
    assert seen["preview_pitch_shift"] != 0
    # ...and passes it on to the dialog it builds (audio imported from a file passes none: the default is 0)
    captured = {}

    class Stub:
        export_succeeded = False

        def __init__(self, *a, **k):
            captured.update(k)

        def exec(self):
            return 0

    monkeypatch.setattr(pew, "SliceEditorWindow", Stub)
    editor._main_window.sampler_controller = type(
        "C", (), {"is_transfer_busy": lambda self: False, "cancel_transfer": lambda self: None}
    )()
    run_slice_editor("X", [0] * 10, 44100, 60, 0, 0, resident_source=False, preview_pitch_shift=2.5)
    assert captured["preview_pitch_shift_semitones"] == 2.5
    run_slice_editor("X", [0] * 10, 44100, 60, 0, 0, resident_source=False)
    assert captured["preview_pitch_shift_semitones"] == 0.0


# --- the Yamaha editor ---------------------------------------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def tuned(window):
    """A sample whose original key is an octave below the preview note, with +2 coarse and +50 fine tune: 12 + 2 + 0.5 semitones."""

    def make(mode):
        tab = user_sample(window, mode=mode, end=3500, loop=(1500, 2500))
        put(window.fake, "UM", original_key_l=48, original_key_r=48, coarse_tune=2, fine_tune_l=50, fine_tune_r=50)
        tab.reload_sample("UM")
        assert _wait(lambda: yp.extract(yp.get("sample", "fine_tune_l"), tab._cache["UM"]) == 50)
        return tab

    return make


def _wait(predicate, timeout=5):
    from test_s950_transfers import wait_until

    return wait_until(predicate, timeout=timeout)


def test_the_yamaha_shift_comes_from_the_samples_original_key_and_tuning(window, tuned):
    tab = tuned(4)
    assert tab.preview_pitch_shift_semitones(tab._cache["UM"]) == pytest.approx(14.5)


@pytest.mark.parametrize("mode, kind", [(1, "play_loop"), (2, "play_loop"), (4, "play"), (3, "play")])
def test_the_yamaha_click_to_preview_plays_shifted_in_every_loop_mode(window, tuned, mode, kind):
    tab = tuned(mode)
    rec = Recorder(tab)
    tab.waveform_view.preview_requested.emit()
    called, _args, kwargs = rec.calls[-1]
    assert called == kind and kwargs["pitch_shift_semitones"] == pytest.approx(14.5)


def test_the_yamaha_dialogs_and_slicer_preview_with_the_samples_shift(window, tuned, monkeypatch):
    tab = tuned(4)
    seen = {}
    for dialog_class, key in ((FilterSampleDialog, "filter"), (TimeStretchDialog, "stretch"), (SliceEditorWindow, "slicer")):
        attribute = "_preview_pitch_shift_semitones" if dialog_class is SliceEditorWindow else "_pitch_shift_semitones"
        monkeypatch.setattr(
            dialog_class, "exec", lambda self, key=key, attribute=attribute: seen.setdefault(key, getattr(self, attribute)) and 0
        )
    monkeypatch.setattr(edit_module, "FilterSampleDialog", FilterSampleDialog)
    tab.edit_buttons["filter"].click()
    tab.time_stretch_button.click()
    tab.slice_button.click()
    assert seen == {"filter": pytest.approx(14.5), "stretch": pytest.approx(14.5), "slicer": pytest.approx(14.5)}


def test_an_imported_file_in_the_yamaha_slicer_has_no_shift(window, monkeypatch):
    seen = {}
    monkeypatch.setattr(SliceEditorWindow, "exec", lambda self: seen.setdefault("shift", self._preview_pitch_shift_semitones) and 0)
    editor = window.samples_tab._edits
    editor._run_slicer("LOOP", [[0] * 100], 44100, {"original_key_l": 60, "original_key_r": 60})
    assert seen["shift"] == 0.0
