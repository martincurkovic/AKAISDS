# A sample's WORKING COPY: audio edited in the app (or loaded back from an external editor) that is not on the sampler yet. It shows in the
# waveform, survives sample-list reloads, keeps marker edits local, and only Send to Sampler / Send as New Sample write it.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QCloseEvent

from core import external_edit as ee
from test_program_editor_window import (  # noqa: F401
    FakeSamplerController,
    _pump_until,
    _select_sample_with_full_audio,
    editor,
    qapp,
)

_MARKERS = {"start": 10, "loop_start": 30, "loop_end": 60, "end": 90}
_ORIGINAL = [int(8000 * ((i % 20) / 10 - 1)) for i in range(100)]


@pytest.fixture
def staged_editor(editor, qapp, monkeypatch, tmp_path):
    """The editor on sample 0 ("SQUARE", 100 frames) in real mode, against a fake sampler, with the audio loaded."""
    import ui.program_editor_window as pew

    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    monkeypatch.setattr(ee, "BACKUP_DIR", tmp_path / "bk")
    monkeypatch.setattr(pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.Yes)
    editor._test_fake = FakeSamplerController(editor._bridge)
    editor._main_window.sampler_controller = editor._test_fake
    _select_sample_with_full_audio(editor, qapp, 0, list(_ORIGINAL), dict(_MARKERS))
    editor._set_sample_edit_buttons_enabled(True)
    return editor


def _stage(editor, samples, **markers):
    new = {**_MARKERS, **markers}
    assert editor._stage_working_copy(0, samples, new)


def _marker_writes(editor):
    editor._worker.wait_until_idle()
    return [c for c in editor._bridge.set_parameter_calls if c[0] in ("SSTART", "SMPEND", "LOOPAT1", "LLNGTH1")]


def test_staging_shows_the_edited_audio_and_remembers_what_the_sampler_has(staged_editor):
    editor = staged_editor
    edited = [5] * 100
    _stage(editor, edited)
    entry = editor._sample_waveform_cache[0]
    assert entry["samples"] == edited and entry["frame_count"] == 100
    staged = editor._staged_edits["SQUARE"]
    assert staged.original_samples == _ORIGINAL and staged.original_markers == _MARKERS
    assert not editor._staged_banner.isHidden()
    assert "●" in editor._sample_row_labels[0][1].text()
    for key in ("stage_send", "stage_revert"):
        assert editor._sample_edit_controls[key][0].isEnabled(), key
    # staging again keeps the FIRST original
    _stage(editor, [6] * 80, end=79, loop_end=60)
    assert editor._staged_edits["SQUARE"].original_samples == _ORIGINAL


def test_marker_edits_stay_local_while_a_sample_has_unsent_edits(staged_editor):
    editor = staged_editor
    # with no unsent edits a marker edit writes to the sampler straight away (unchanged behaviour)
    editor.waveform_view.set_marker("start", 20)
    editor._on_waveform_marker_committed("start", 20, 30, 60, 90)
    assert [c[0] for c in _marker_writes(editor)] == ["SSTART"]
    editor._bridge.set_parameter_calls.clear()

    _stage(editor, [5] * 100)
    editor.waveform_view.set_marker("end", 80)
    editor._on_waveform_marker_committed("end", 20, 30, 60, 80)
    editor._on_marker_spinbox_changed("loop_start", 35)
    assert _marker_writes(editor) == []  # nothing for the live sample
    staged = editor._staged_edits["SQUARE"]
    assert staged.markers["end"] == 80 and staged.markers["loop_start"] == 35
    assert editor._sample_waveform_cache[0]["end"] == 80


def test_the_working_copy_survives_a_sample_list_reload(staged_editor, qapp):
    editor = staged_editor
    edited = [5] * 100
    _stage(editor, edited, end=70)
    editor._worker.submit_sample_list()
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor._sample_waveform_cache.get(0, {}).get("samples") is not None)
    entry = editor._sample_waveform_cache[0]
    assert entry["samples"] == edited and entry["end"] == 70  # the cache was wiped and rebuilt from the header + the working copy
    assert editor.waveform_view.has_waveform()
    assert "●" in editor._sample_row_labels[0][1].text()


def test_revert_goes_back_to_the_samplers_version_after_a_confirmation(staged_editor, monkeypatch):
    import ui.program_editor_window as pew

    editor = staged_editor
    _stage(editor, [5] * 60, end=59, loop_end=50)
    monkeypatch.setattr(pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.No)
    editor.revert_edits_button.click()
    assert "SQUARE" in editor._staged_edits  # declined
    monkeypatch.setattr(pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.Yes)
    editor.revert_edits_button.click()
    entry = editor._sample_waveform_cache[0]
    assert editor._staged_edits == {} and entry["samples"] == _ORIGINAL and entry["frame_count"] == 100
    assert {k: entry[k] for k in _MARKERS} == _MARKERS
    assert editor._staged_banner.isHidden() and "●" not in editor._sample_row_labels[0][1].text()
    assert editor.waveform_view.frame_count() == 100


def test_send_to_sampler_replaces_the_sample_with_the_working_copy_after_saving_a_backup(staged_editor, tmp_path):
    editor = staged_editor
    edited = [int(v / 2) for v in _ORIGINAL]
    _stage(editor, edited)

    editor.send_edits_button.click()

    sent = editor._test_fake.sent_entries
    assert len(sent) == 1 and sent[0]["samples"] == edited and sent[0]["name"].startswith("SQUARE")
    assert editor._staged_edits == {}  # on the sampler now: nothing unsent
    backups = list((tmp_path / "bk").glob("*.wav"))
    assert len(backups) == 1
    from core import sds_encoder

    assert sds_encoder.read_wav_samples(str(backups[0]))[0] == _ORIGINAL  # the SAMPLER's audio, not the edit
    assert editor._staged_banner.isHidden()
    assert "SQUARE" in editor._bridge.sample_list()


def test_send_to_sampler_declined_changes_nothing(staged_editor, monkeypatch, tmp_path):
    import ui.program_editor_window as pew

    editor = staged_editor
    _stage(editor, [5] * 100)
    monkeypatch.setattr(pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.No)
    editor.send_edits_button.click()
    assert editor._test_fake.sent_entries == [] and "SQUARE" in editor._staged_edits and not (tmp_path / "bk").exists()


def test_send_as_new_sample_leaves_the_original_and_drops_its_unsent_edits(staged_editor, monkeypatch):
    editor = staged_editor
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda *a, **k: "SQUARE EDIT")
    edited = [5] * 100
    _stage(editor, edited)

    editor.send_edits_as_new_button.click()

    sent = editor._test_fake.sent_entries
    assert len(sent) == 1 and sent[0]["name"] == "SQUARE EDIT" and sent[0]["samples"] == edited
    assert "SQUARE" in editor._bridge.sample_list() and "SQUARE EDIT" in editor._bridge.sample_list()
    assert editor._staged_edits == {}  # the work lives on as the new sample
    # the original's retained audio is the SAMPLER's, not the edit (it would otherwise come back as the original's waveform)
    assert editor._retained_sample_audio["SQUARE"][0] == _ORIGINAL


def test_duplicate_of_an_edited_sample_keeps_the_samplers_audio_for_the_original(staged_editor, monkeypatch):
    editor = staged_editor
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda *a, **k: "SQUARE COPY")
    _stage(editor, [5] * 100)
    editor._confirm_duplicate_sample()
    assert editor._test_fake.sent_entries[0]["samples"] == [5] * 100  # the copy is of what is shown
    assert editor._retained_sample_audio["SQUARE"][0] == _ORIGINAL


def test_renaming_a_sample_takes_its_unsent_edits_along(staged_editor, monkeypatch):
    editor = staged_editor
    _stage(editor, [5] * 100)
    monkeypatch.setattr(editor, "_prompt_sample_name", lambda current: "NEW NAME")
    editor._confirm_rename_sample()
    assert "NEW NAME" in editor._staged_edits and "SQUARE" not in editor._staged_edits
    assert "●" in editor._sample_row_labels[0][1].text()


def test_deleting_a_sample_drops_its_unsent_edits(staged_editor):
    editor = staged_editor
    _stage(editor, [5] * 100)
    editor._confirm_delete_sample()
    assert editor._staged_edits == {}


def test_a_sample_that_vanishes_from_the_list_loses_its_unsent_edits_but_not_mid_operation(staged_editor):
    editor = staged_editor
    _stage(editor, [5] * 100)
    editor._hardware_busy_ui = True  # a replace deletes the original for a moment and renames its copy back
    editor._on_samples_loaded([n for n in editor._sample_list if n != "SQUARE"])
    assert "SQUARE" in editor._staged_edits
    editor._hardware_busy_ui = False
    editor._on_samples_loaded([n for n in editor._sample_list if n != "SQUARE"])
    assert editor._staged_edits == {} and "no longer on the sampler" in editor.status_bar.currentMessage()


def test_closing_the_editor_with_unsent_edits_asks_first(staged_editor, monkeypatch):
    import ui.program_editor_window as pew

    editor = staged_editor
    _stage(editor, [5] * 100)
    asked = []
    monkeypatch.setattr(
        pew.QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or pew.QMessageBox.StandardButton.No
    )
    event = QCloseEvent()
    editor.closeEvent(event)
    assert not event.isAccepted() and "SQUARE" in asked[0]
    # nothing unsent: closes without asking (the worker's shutdown is left to the fixture)
    editor._staged_edits.clear()
    asked.clear()
    monkeypatch.setattr(editor._worker, "stop", lambda: None)
    monkeypatch.setattr(editor._worker, "wait", lambda: None)
    event = QCloseEvent()
    editor.closeEvent(event)
    assert event.isAccepted() and asked == []


def test_the_working_copy_controls_are_hidden_and_off_without_unsent_edits(staged_editor):
    editor = staged_editor
    assert editor._staged_banner.isHidden()
    for key in ("stage_send", "stage_new", "stage_revert"):
        button, action = editor._sample_edit_controls[key]
        assert not button.isEnabled() and not action.isEnabled(), key


def test_the_slice_editor_retains_the_samplers_audio_not_the_working_copy(staged_editor, monkeypatch):
    import ui.program_editor_window as pew

    editor = staged_editor
    _stage(editor, [5] * 100)

    class Stub:
        export_succeeded = False

        def __init__(self, *a, **k):
            pass

        def exec(self):
            # while the dialog is open the sampler's audio is what is retained for the sample
            Stub.retained = editor._retained_sample_audio["SQUARE"][0]
            return 0

    monkeypatch.setattr(pew, "SliceEditorWindow", Stub)
    editor._open_slice_editor()
    assert Stub.retained == _ORIGINAL
