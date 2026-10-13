# What the Akai Program Editor writes to ~/.akaisds/akaisds.log for the actions built in 1.4.0: one INFO line per user action, enough to follow a bug report.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from test_envelope_editing import move, press, release
from test_program_editor_window import _pump_until, _select_keygroup_1, _select_sample_with_full_audio, editor, qapp  # noqa: F401
from test_memory_status import editor as status_editor  # noqa: F401  (a bridge that answers RSTAT)
from test_staged_edits import _stage, staged_editor  # noqa: F401


@pytest.fixture
def log(caplog):
    caplog.set_level(logging.INFO, logger="akaisds")
    return lambda: [r.getMessage() for r in caplog.records if r.name == "akaisds"]


def test_the_samplers_os_and_memory_are_logged_once_per_window(status_editor, qapp, log):
    _pump_until(qapp, lambda: any("sampler OS" in m for m in log()))
    lines = [m for m in log() if "sampler OS" in m]
    assert len(lines) == 1 and "sampler OS 2.01" in lines[0] and "750 of 1,000 words used" in lines[0]
    status_editor._worker.submit_sample_list()  # every sample list load re-reads it: not logged again
    status_editor._worker.wait_until_idle()
    qapp.processEvents()
    assert len([m for m in log() if "sampler OS" in m]) == 1


def test_an_envelope_graph_edit_is_logged_once_on_release_with_what_was_written(editor, qapp, log):
    _select_keygroup_1(editor, qapp)
    graph = editor.env1_graph
    graph.show()
    start = graph.handle_positions()[0]
    press(graph, start)
    move(graph, start + QPointF(15, 0))
    move(graph, start + QPointF(40, 0))
    assert not [m for m in log() if "envelope graph" in m]  # nothing while the pointer is down
    release(graph, start + QPointF(40, 0))
    lines = [m for m in log() if "envelope graph" in m]
    assert lines == [f"ProgramEditor: envelope graph edit on keygroup 1: ATTAK1={graph.values()['attack']}"]


def test_undo_and_revert_are_logged(staged_editor, log, monkeypatch):  # noqa: F811
    import ui.program_editor_window as pew

    editor = staged_editor
    monkeypatch.setattr(pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.Yes)
    _stage(editor, [1] * 100)
    _stage(editor, [2] * 100)
    editor._undo_staged()
    assert any("undo on 'SQUARE': 0 more step(s)" in m for m in log())
    editor._undo_staged()
    assert any("undo on 'SQUARE': back to the sampler's version" in m for m in log())
    _stage(editor, [3] * 100)
    editor._revert_staged()
    assert any("reverted 'SQUARE' to the sampler's version" in m for m in log())


@pytest.mark.parametrize(
    "result, answer, expected",
    [(None, "Yes", "no reliable pitch"), ((60, 0.0, 0.1), "Yes", "no reliable pitch"), ((72, 15.0, 0.9), "No", "declined"), ((72, 15.0, 0.9), "Yes", "applied")],
)
def test_detect_root_note_logs_what_it_found_and_what_the_user_did(editor, qapp, monkeypatch, log, result, answer, expected):
    import ui.program_editor_window as pew

    monkeypatch.setattr(pew.root_note_detection, "detect_root_note", lambda *a, **k: result)
    monkeypatch.setattr(pew.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(pew.QMessageBox, "question", lambda *a, **k: getattr(pew.QMessageBox.StandardButton, answer))
    _select_sample_with_full_audio(editor, qapp, 0, list(range(-5000, 5000)), {"start": 0, "loop_start": 100, "loop_end": 900, "end": 999})
    editor._confirm_detect_root_note()
    lines = [m for m in log() if "root note detection" in m]
    assert len(lines) == 1 and expected in lines[0] and "'SQUARE'" in lines[0]
    if result and result[2] >= 0.5:
        assert "C5" in lines[0] or "+15 cents" in lines[0]  # the note and the residual
