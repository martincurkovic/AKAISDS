# Trim / Reverse / Fade / Normalise / Filter / Stretch-Pitch edit the sample's WORKING COPY and send nothing; Send to Sampler is what
# writes. They chain, and Undo steps back through them (the first Undo goes back to what is on the sampler).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core import sample_editing
from test_program_editor_window import (  # noqa: F401
    FakeSamplerController,
    _pump_until,
    _select_sample_with_full_audio,
    editor,
    qapp,
)
from test_staged_edits import _MARKERS, _ORIGINAL, staged_editor  # noqa: F401
from ui.filter_sample_dialog import FilterSampleDialog
from ui.time_stretch_dialog import PITCH, TimeStretchDialog


def _sent(editor):
    return editor._test_fake.sent_entries


def test_every_edit_changes_the_working_copy_and_sends_nothing(staged_editor):
    editor = staged_editor
    for action in (
        editor._confirm_reverse_sample,
        editor._confirm_fade_sample,
        editor._confirm_normalize_sample,
        editor._confirm_trim_sample,
    ):
        action()
    assert _sent(editor) == []
    assert all(
        c[0] not in ("SSTART", "SMPEND", "LOOPAT1", "LLNGTH1", "SHNAME") for c in editor._bridge.set_parameter_calls
    )
    staged = editor._staged_edits["SQUARE"]
    assert staged.original_samples == _ORIGINAL  # the sampler's version is untouched, here and there
    assert editor._sample_waveform_cache[0]["samples"] is staged.samples
    assert "SQUARE" in editor._bridge.sample_list() and "SQUARE-TMP" not in editor._bridge.sample_list()
    assert "applied" in editor.status_bar.currentMessage() and "not on the sampler" in editor.status_bar.currentMessage()
    assert bool(editor._staged_banner.property("unsent"))


def test_edits_chain_on_the_working_copy(staged_editor):
    editor = staged_editor
    editor._confirm_reverse_sample()
    once = list(editor._staged_edits["SQUARE"].samples)
    assert once == list(reversed(_ORIGINAL))
    editor._confirm_reverse_sample()  # reversing the REVERSED copy gives the original back
    assert editor._sample_waveform_cache[0]["samples"] == _ORIGINAL
    assert len(editor._staged_edits["SQUARE"].history) == 1


def test_trim_moves_the_markers_with_the_audio_and_stays_local(staged_editor):
    editor = staged_editor
    editor._confirm_trim_sample()  # to Start 10 .. End 90
    entry = editor._sample_waveform_cache[0]
    assert entry["frame_count"] == 81 and (entry["start"], entry["end"]) == (0, 80)
    assert (entry["loop_start"], entry["loop_end"]) == (20, 50)
    assert editor.waveform_view.frame_count() == 81
    assert all(c[0] != "SMPEND" for c in editor._bridge.set_parameter_calls)


def test_the_nothing_to_do_guards_still_apply(staged_editor):
    editor = staged_editor
    editor.waveform_view.set_marker("start", 0)
    editor.waveform_view.set_marker("end", 99)
    editor._confirm_trim_sample()
    assert "Nothing to trim" in editor.status_bar.currentMessage() and editor._staged_edits == {}


def test_undo_steps_back_through_the_edits_and_the_first_goes_back_to_the_sampler(staged_editor):
    editor = staged_editor
    editor._confirm_reverse_sample()
    editor._confirm_normalize_sample()
    after_normalise = list(editor._staged_edits["SQUARE"].samples)
    assert after_normalise != list(reversed(_ORIGINAL))

    editor.undo_edits_button.click()
    assert editor._staged_edits["SQUARE"].samples == list(reversed(_ORIGINAL))  # normalise undone, reverse kept
    assert editor._sample_waveform_cache[0]["samples"] == list(reversed(_ORIGINAL))
    editor.undo_edits_button.click()  # the first edit: back to what the sampler holds
    assert editor._staged_edits == {}
    entry = editor._sample_waveform_cache[0]
    assert entry["samples"] == _ORIGINAL and {k: entry[k] for k in _MARKERS} == _MARKERS
    assert not bool(editor._staged_banner.property("unsent")) and not editor.undo_edits_button.isEnabled()


def test_undo_brings_back_the_earlier_markers_too(staged_editor):
    editor = staged_editor
    editor._confirm_trim_sample()
    editor.undo_edits_button.click()
    assert editor._sample_waveform_cache[0]["frame_count"] == 100 and editor.waveform_view.frame_count() == 100


def test_undo_history_is_limited(staged_editor):
    import ui.program_editor_window as pew

    editor = staged_editor
    for _ in range(pew._UNDO_LIMIT + 5):
        editor._confirm_reverse_sample()
    assert len(editor._staged_edits["SQUARE"].history) == pew._UNDO_LIMIT


def test_send_to_sampler_sends_the_chained_result_in_one_replace(staged_editor):
    editor = staged_editor
    editor._confirm_reverse_sample()
    editor._confirm_normalize_sample()
    expected = list(editor._staged_edits["SQUARE"].samples)
    assert _sent(editor) == []
    editor.send_edits_button.click()
    assert len(_sent(editor)) == 1 and _sent(editor)[0]["samples"] == expected  # one send for the whole chain
    assert editor._staged_edits == {}


def test_a_failed_send_keeps_the_working_copy_so_it_can_be_sent_again(staged_editor):
    editor = staged_editor
    editor._test_fake.next_result = False
    editor._confirm_reverse_sample()
    editor.send_edits_button.click()
    assert "SQUARE" in editor._staged_edits and "failed" in editor.status_bar.currentMessage().lower()
    editor._test_fake.next_result = True
    editor.send_edits_button.click()
    assert editor._staged_edits == {}


# --- the dialogs -----------------------------------------------------------------------------------------------------------


def test_filter_ok_applies_to_the_working_copy_and_sends_nothing(staged_editor, monkeypatch):
    editor = staged_editor
    monkeypatch.setattr(FilterSampleDialog, "exec", lambda self: True)
    editor._confirm_filter_sample()
    expected = sample_editing.filter_samples(
        _ORIGINAL, 10, 30, 60, 90, 44100,
        highpass_enabled=False, highpass_cutoff_hz=200, highpass_slope_db_per_octave=12,
        lowpass_enabled=True, lowpass_cutoff_hz=1000, lowpass_slope_db_per_octave=12,
    )[0]
    assert editor._staged_edits["SQUARE"].samples == expected and _sent(editor) == []
    assert "Filter applied" in editor.status_bar.currentMessage()


def test_stretch_ok_applies_to_the_working_copy_and_sends_nothing(staged_editor, monkeypatch):
    editor = staged_editor

    def fake_exec(self):
        self.length_knob.setValue(200)
        self.grain_knob.setValue(10)
        return True

    monkeypatch.setattr(TimeStretchDialog, "exec", fake_exec)
    editor._confirm_time_stretch()
    assert len(editor._staged_edits["SQUARE"].samples) == 200 and _sent(editor) == []
    assert editor._sample_waveform_cache[0]["frame_count"] == 200


def test_pitch_shift_ok_applies_to_the_working_copy_with_the_same_length(staged_editor, monkeypatch):
    editor = staged_editor

    def fake_exec(self):
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(PITCH))
        self.semitones_knob.setValue(5)
        return True

    monkeypatch.setattr(TimeStretchDialog, "exec", fake_exec)
    editor._confirm_time_stretch()
    staged = editor._staged_edits["SQUARE"]
    assert len(staged.samples) == 100 and staged.samples != _ORIGINAL and _sent(editor) == []
    assert "Pitch Shift applied" in editor.status_bar.currentMessage()


def test_send_as_new_from_a_dialog_still_sends_at_once_and_leaves_the_original(staged_editor, monkeypatch):
    editor = staged_editor
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda *a, **k: "SQUARE FILT")

    def fake_exec(self):
        self.new_sample_button.click()
        return True

    monkeypatch.setattr(FilterSampleDialog, "exec", fake_exec)
    editor._confirm_filter_sample()
    assert len(_sent(editor)) == 1 and _sent(editor)[0]["name"] == "SQUARE FILT"
    assert editor._staged_edits == {}  # the original was never staged by it


def test_the_dialogs_say_what_ok_does_in_the_program_editor(qapp):
    filt = FilterSampleDialog(None, "K", [0] * 100, 44100, allow_new_sample=True, applies_to_working_copy=True)
    labels = [w.text() for w in filt.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)]
    assert any("OK applies the filter to the sample here" in t and "Send to Sampler" in t for t in labels)
    stretch = TimeStretchDialog(None, "K", [0] * 100, 44100, allow_new_sample=True, applies_to_working_copy=True)
    assert "OK applies it to the sample here" in stretch.warning_label.text()
    # the Yamaha editor's wording is unchanged (its OK still makes a new sample)
    yamaha = TimeStretchDialog(None, "K", [0] * 100, 44100)
    assert "cannot be undone" in yamaha.warning_label.text()
    assert "applies" not in yamaha.warning_label.text()


def test_the_edit_in_external_app_chain_works_with_a_filter_in_between(staged_editor, monkeypatch, tmp_path):
    from core import dropped_files, external_edit as ee, sds_encoder

    editor = staged_editor
    monkeypatch.setattr(dropped_files, "_session_dir", tmp_path)
    opened = []
    monkeypatch.setattr(ee, "launch", lambda path, editor=None: opened.append(path))
    editor.external_edit_button.click()
    sds_encoder.write_wav_file(opened[0], [1000] * 100, 44100, bit_depth=16)
    stat = os.stat(opened[0])
    os.utime(opened[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    editor._refresh_external_edit_buttons()
    editor.external_load_button.click()
    assert editor._staged_edits["SQUARE"].samples == [1000] * 100
    monkeypatch.setattr(FilterSampleDialog, "exec", lambda self: True)
    editor._confirm_filter_sample()  # used to send here
    assert _sent(editor) == [] and editor._staged_edits["SQUARE"].samples != [1000] * 100
    editor.undo_edits_button.click()  # the filter
    assert editor._staged_edits["SQUARE"].samples == [1000] * 100
    editor.undo_edits_button.click()  # the external edit: back to the sampler's version
    assert editor._staged_edits == {} and editor._sample_waveform_cache[0]["samples"] == _ORIGINAL
