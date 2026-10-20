# A sample SENT from another one's audio (a replace, a new sample, slices) must keep that sample's pitch. The sent sample lands on the
# bandwidth bucket the send chose, which need not be the source's, and STUNO holds the baseline for its bucket: copying the source's
# STUNO across as it was played an 11025 Hz sample back an OCTAVE LOW on a real S2000 (2026-10-13).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core import external_edit as ee
from test_program_editor_window import (  # noqa: F401
    FakeSamplerController,
    _pump_until,
    _select_sample_with_full_audio,
    editor,
    qapp,
)

_MARKERS = {"start": 10, "loop_start": 30, "loop_end": 60, "end": 90}
_AUDIO = [int(8000 * ((i % 20) / 10 - 1)) for i in range(100)]


def _stuno(editor, semitones):
    return editor._semitones_to_sample_tune_offset(semitones)


@pytest.fixture
def eleven_khz(editor, qapp, monkeypatch, tmp_path):
    """Sample 0 as the sampler imported it itself at 11025 Hz: SBANDW 1 and a baseline of -24 semitones in STUNO (+ `extra` dialled in)."""
    import ui.program_editor_window as pew

    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    monkeypatch.setattr(ee, "BACKUP_DIR", tmp_path / "bk")
    monkeypatch.setattr(pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.Yes)
    fake = FakeSamplerController(editor._bridge)
    editor._main_window.sampler_controller = fake
    _select_sample_with_full_audio(editor, qapp, 0, list(_AUDIO), dict(_MARKERS))
    entry = editor._sample_waveform_cache[0]
    entry.update(framerate=11025, sbandw=1, stuno=_stuno(editor, -24.0))
    editor._set_sample_edit_buttons_enabled(True)
    editor._test_fake = fake
    return editor


def _header(editor, name):
    index = editor._bridge.sample_list().index(name)
    return {c[0]: c[2] for c in editor._bridge.set_parameter_calls if c[1] == index}


def test_a_replace_keeps_the_pitch_when_the_send_lands_on_a_different_bandwidth(eleven_khz):
    editor = eleven_khz
    editor._test_fake.new_sample_sbandw = 0  # the 16-bit Akai send picks the nearest bucket to 11025 Hz: 22050 Hz
    editor._confirm_reverse_sample()
    editor._send_staged_to_sampler()
    # the source's -24 was baseline(1) = -24 plus nothing; the new sample's baseline (bucket 0) is -12
    assert _header(editor, "SQUARE")["STUNO"] == _stuno(editor, -12.0)


def test_the_tuning_the_user_dialled_in_survives_the_move_to_another_bandwidth(eleven_khz):
    editor = eleven_khz
    editor._sample_waveform_cache[0]["stuno"] = _stuno(editor, -24.0 + 3.25)  # +3.25 semitones on top of the baseline
    editor._test_fake.new_sample_sbandw = 0
    editor._confirm_reverse_sample()
    editor._send_staged_to_sampler()
    assert _header(editor, "SQUARE")["STUNO"] == _stuno(editor, -12.0 + 3.25)


def test_nothing_changes_when_the_send_lands_on_the_same_bandwidth(eleven_khz):
    editor = eleven_khz
    editor._test_fake.new_sample_sbandw = 1
    editor._confirm_reverse_sample()
    editor._send_staged_to_sampler()
    assert _header(editor, "SQUARE")["STUNO"] == _stuno(editor, -24.0)


def test_a_sample_the_app_sent_itself_on_the_nearest_bucket_stays_as_it_was(eleven_khz):
    editor = eleven_khz
    editor._sample_waveform_cache[0].update(sbandw=0, stuno=_stuno(editor, -12.0))
    editor._test_fake.new_sample_sbandw = 0
    editor._confirm_reverse_sample()
    editor._send_staged_to_sampler()
    assert _header(editor, "SQUARE")["STUNO"] == _stuno(editor, -12.0)


def test_a_new_sample_from_an_edit_gets_the_same_correction(eleven_khz, monkeypatch):
    editor = eleven_khz
    editor._test_fake.new_sample_sbandw = 0
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda *a, **k: "SQUARE COPY")
    editor._confirm_duplicate_sample()
    assert _header(editor, "SQUARE COPY")["STUNO"] == _stuno(editor, -12.0)


def test_slices_of_a_resident_sample_get_the_same_correction(eleven_khz):
    editor = eleven_khz
    editor._test_fake.new_sample_sbandw = 0
    ok, _msg = editor._export_slices(
        ["CUT-1"], [list(range(40))], 11025, 16, None, 60, _stuno(editor, -24.0), 0,
        lambda *a: None, lambda *a: None, source_sbandw=1, source_rate=11025,
    )
    assert ok and _header(editor, "CUT-1")["STUNO"] == _stuno(editor, -12.0)


def test_slices_of_audio_from_a_file_leave_the_samplers_own_tuning_alone(eleven_khz):
    editor = eleven_khz
    ok, _msg = editor._export_slices(
        ["CUT-1"], [list(range(40))], 11025, 16, None, 60, 0, 0, lambda *a: None, lambda *a: None
    )
    assert ok and "STUNO" not in _header(editor, "CUT-1")  # the baseline the send baked in is not overwritten with 0


def test_a_failed_header_read_falls_back_to_the_sources_tuning(eleven_khz, monkeypatch):
    editor = eleven_khz
    monkeypatch.setattr(editor, "_read_sample_header_blocking", lambda index: None)
    editor._confirm_reverse_sample()
    editor._send_staged_to_sampler()
    assert _header(editor, "SQUARE")["STUNO"] == _stuno(editor, -24.0)


# --- after a replace the sample is live at once -------------------------------------------------------------------------------


def test_the_replaced_sample_is_shown_with_working_controls_the_moment_the_send_ends(eleven_khz):
    editor = eleven_khz
    editor._confirm_reverse_sample()
    editor._send_staged_to_sampler()
    # no waiting for a header fetch: everything is live straight away
    row = editor.sample_list_widget.currentRow()
    assert editor._sample_name_at_row(row) == "SQUARE"
    assert editor.waveform_view.has_waveform() and editor.trim_sample_button.isEnabled()
    assert editor.sample_root_note_spinbox.isEnabled() and editor.sample_loop_type_combo.isEnabled()
    entry = editor._sample_waveform_cache[row]
    assert entry["samples"] == list(reversed(_AUDIO)) and entry["frame_count"] == 100
    assert (entry["start"], entry["end"]) == (100 - 1 - 90, 100 - 1 - 10)  # the reversed markers
    assert not bool(editor._staged_banner.property("unsent"))


def test_a_sample_that_was_already_selected_is_shown_too(eleven_khz):
    # the selection does not change when the replaced sample is the one already current: _on_sample_selected would never fire
    editor = eleven_khz
    editor._confirm_reverse_sample()
    editor._send_staged_to_sampler()
    editor._confirm_normalize_sample()
    editor._send_staged_to_sampler()
    assert editor.waveform_view.has_waveform() and editor.trim_sample_button.isEnabled()
