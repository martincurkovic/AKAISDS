# Save/Load Program (.p1/.p3) in ProgramEditorWindow, end to end against
# FakeS1000 (real adapter + real BridgeWorker thread). Dialogs are patched.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox

from core import akai_program_file as apf
from core.program_editor_bridge import LoggingBridge
from core.s1000_bridge import S1000Bridge
from test_program_editor_window_s1000 import (  # noqa: F401 - fixtures/helpers
    _dispose,
    _make_editor,
    _pump_until,
    qapp,
)
from test_akai_program_file import PointerFake, _blocks


@pytest.fixture
def s1000(qapp):
    fake = PointerFake()
    editor = _make_editor(
        qapp, "akai_s1000", LoggingBridge(S1000Bridge(fake.bridge(timeout=0.3)))
    )
    yield fake, editor
    _dispose(editor)


def _idle(qapp, editor):
    editor._worker.wait_until_idle()
    for _ in range(10):
        qapp.processEvents()


def test_buttons_follow_the_selection(s1000):
    _fake, editor = s1000
    assert editor.save_program_button.isEnabled()
    assert editor.load_program_button.isEnabled()


def test_save_writes_a_p1_file_a_load_can_read_back(qapp, s1000, tmp_path, monkeypatch):
    fake, editor = s1000
    target = tmp_path / "DRUMS.p1"
    monkeypatch.setattr(
        "ui.program_editor_window.QFileDialog.getSaveFileName",
        lambda *a, **k: (str(target), ""),
    )
    editor._save_program_to_file()
    _pump_until(qapp, lambda: target.exists())

    parsed = apf.parse_file(target.read_bytes())
    assert parsed.name == "DRUMS" and parsed.family == "s1000"
    assert len(parsed.keygroups) == 2
    assert apf.zone_sample_names(parsed) == ["KICK", "SNARE"]


def test_cancelling_the_save_dialog_writes_nothing(qapp, s1000, tmp_path, monkeypatch):
    _fake, editor = s1000
    monkeypatch.setattr(
        "ui.program_editor_window.QFileDialog.getSaveFileName", lambda *a, **k: ("", "")
    )
    editor._save_program_to_file()
    _idle(qapp, editor)
    assert list(tmp_path.iterdir()) == []
    assert "cancelled" in editor.status_bar.currentMessage()


def test_load_with_a_clashing_name_asks_for_another_and_adds_a_program(
    qapp, s1000, tmp_path, monkeypatch
):
    fake, editor = s1000
    path = tmp_path / "DRUMS.p1"
    program, keygroups = fake.programs[0]["block"], fake.programs[0]["keygroups"]
    path.write_bytes(apf.build_file(bytes(program), [bytes(k) for k in keygroups]))

    monkeypatch.setattr(
        "ui.program_editor_window.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(path), ""),
    )
    prompts = []
    monkeypatch.setattr(
        editor, "_prompt_akai_name", lambda *a: prompts.append(a) or "DRUMS NEW"
    )
    asked = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes,
    )

    editor._load_program_from_file()
    _pump_until(qapp, lambda: editor.program_list.count() == 3)

    # the follow-up sample-list reload must not snap the selection back to row 0
    _idle(qapp, editor)
    editor._worker.wait_until_idle()
    for _ in range(20):
        qapp.processEvents()
    assert editor.program_list.currentRow() == 2

    assert len(prompts) == 1  # DRUMS is resident, so it had to be renamed
    assert editor.program_list.item(2).text() == "DRUMS NEW"
    assert fake.deleted_by_name_clash == []  # nothing was deleted by the load
    assert "All of them are on the sampler" in asked[0] or "Not on the sampler" in asked[0]


def test_load_refuses_a_file_for_the_other_sampler_family(qapp, s1000, tmp_path, monkeypatch):
    fake, editor = s1000
    path = tmp_path / "BIG.p3"
    path.write_bytes(apf.build_file(*_blocks(192)))
    monkeypatch.setattr(
        "ui.program_editor_window.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(path), ""),
    )
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))

    editor._load_program_from_file()
    _idle(qapp, editor)

    assert warnings and "S2000/S3000" in warnings[0]
    assert len(fake.programs) == 2  # nothing sent


def _s2000_editor_loading_a_p1(s1000, tmp_path, monkeypatch, answers):
    """The window in S2000/S3000 mode (only the model flag differs from the fixture) loading a .p1; `answers` = what each question() returns."""
    fake, editor = s1000
    editor._is_s1000 = False
    path = tmp_path / "OLD.p1"
    path.write_bytes(apf.build_file(*_blocks(150, groups=2, name="OLD S1000")))
    monkeypatch.setattr("ui.program_editor_window.QFileDialog.getOpenFileName", lambda *a, **k: (str(path), ""))
    asked, submitted = [], []

    def question(*a, **k):
        asked.append(a[2])
        return answers[len(asked) - 1]

    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: asked.append("WARNING: " + a[2]))
    monkeypatch.setattr(editor._worker, "submit_import_program", lambda pf, name: submitted.append((pf, name)))
    editor._load_program_from_file()
    return asked, submitted


def test_an_s1000_program_loaded_on_an_s2000_is_converted_after_a_confirmation(qapp, s1000, tmp_path, monkeypatch):
    yes = QMessageBox.StandardButton.Yes
    asked, submitted = _s2000_editor_loading_a_p1(s1000, tmp_path, monkeypatch, [yes, yes])
    assert "S1000 (.p1) program" in asked[0] and "convert" in asked[0].lower() and "tweaking" in asked[0]
    assert len(submitted) == 1
    program_file, name = submitted[0]
    assert program_file.block_size == 192 and program_file.family == "s2000_s3000" and len(program_file.keygroups) == 2
    assert program_file.program[72:] == apf.S3000_PROGRAM_TAIL
    assert name == "OLD S1000"


def test_declining_the_conversion_sends_nothing(qapp, s1000, tmp_path, monkeypatch):
    no = QMessageBox.StandardButton.No
    asked, submitted = _s2000_editor_loading_a_p1(s1000, tmp_path, monkeypatch, [no])
    assert len(asked) == 1 and submitted == []


def test_load_reports_an_unreadable_file(qapp, s1000, tmp_path, monkeypatch):
    fake, editor = s1000
    path = tmp_path / "junk.p1"
    path.write_bytes(b"not a program")
    monkeypatch.setattr(
        "ui.program_editor_window.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(path), ""),
    )
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    editor._load_program_from_file()
    assert warnings and "Couldn't read this file" in warnings[0]


# --- drag and drop: a program file dropped on the window loads it; a program dragged out of the list becomes a file -----------------


from PySide6.QtCore import QMimeData, QPointF, QUrl, Qt
from PySide6.QtGui import QDropEvent


_KEEP_ALIVE = []  # a drop event only POINTS at its QMimeData: if Python frees it first, the next read segfaults


def _mime(*paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    _KEEP_ALIVE.append(mime)
    return mime


def _drop_event(*paths):
    return QDropEvent(
        QPointF(20, 20), Qt.DropAction.CopyAction, _mime(*paths), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    )


def _program_file(fake, tmp_path, name="DRUMS.p1"):
    path = tmp_path / name
    program, keygroups = fake.programs[0]["block"], fake.programs[0]["keygroups"]
    path.write_bytes(apf.build_file(bytes(program), [bytes(k) for k in keygroups]))
    return path


def test_dropping_a_program_file_loads_it_like_the_load_button(qapp, s1000, tmp_path, monkeypatch):
    fake, editor = s1000
    path = _program_file(fake, tmp_path)
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda *a: "DRUMS DROP")
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes
    )
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))

    event = _drop_event(path)
    editor.dropEvent(event)
    assert event.isAccepted()
    # answered after the drop has returned (the dialogs must not hold the dragging app open)
    assert asked == []
    _pump_until(qapp, lambda: editor.program_list.count() == 3 or warnings)

    assert warnings == []
    assert editor.program_list.item(2).text() == "DRUMS DROP"
    assert asked and "keygroups" in asked[0]  # it asked before loading, like the Load button


def test_only_program_files_are_accepted_for_a_drop(qapp, s1000, tmp_path):
    _fake, editor = s1000
    from PySide6.QtGui import QDragEnterEvent

    def enter(*paths):
        event = QDragEnterEvent(
            QPointF(5, 5).toPoint(), Qt.DropAction.CopyAction, _mime(*paths), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
        )
        event.setAccepted(False)
        editor.dragEnterEvent(event)
        return event.isAccepted()

    assert enter(tmp_path / "A.p1") and enter(tmp_path / "B.P3")
    assert not enter(tmp_path / "loop.wav")


def test_dropping_two_program_files_loads_neither(qapp, s1000, tmp_path, monkeypatch):
    fake, editor = s1000
    first, second = _program_file(fake, tmp_path, "A.p1"), _program_file(fake, tmp_path, "B.p1")
    loaded = []
    monkeypatch.setattr(editor, "_load_program_path", loaded.append)
    editor.dropEvent(_drop_event(first, second))
    _pump_until(qapp, lambda: "one program file" in editor.status_bar.currentMessage())
    assert loaded == []


def test_a_drop_is_refused_while_the_window_is_busy_or_in_demo_mode(qapp, s1000, tmp_path, monkeypatch):
    fake, editor = s1000
    path = _program_file(fake, tmp_path)
    loaded = []
    monkeypatch.setattr(editor, "_load_program_path", loaded.append)

    editor.main_tabs.setEnabled(False)  # what a long hardware operation does
    editor.dropEvent(_drop_event(path))
    _pump_until(qapp, lambda: "try again" in editor.status_bar.currentMessage())
    editor.main_tabs.setEnabled(True)

    editor._load_program_action.setEnabled(False)  # demo mode
    editor.dropEvent(_drop_event(path))
    _pump_until(qapp, lambda: "demo mode" in editor.status_bar.currentMessage())
    assert loaded == []


def test_a_program_read_for_dragging_becomes_a_file_a_load_can_read(qapp, s1000):
    _fake, editor = s1000
    path = editor._export_program_to_temp_file(0)
    assert path and path.endswith("DRUMS.p1")
    parsed = apf.parse_file(open(path, "rb").read())
    assert parsed.name == "DRUMS" and len(parsed.keygroups) == 2


def test_dragging_a_program_out_starts_a_copy_drag_of_that_file(qapp, s1000, monkeypatch):
    _fake, editor = s1000
    seen = {}

    def fake_exec(drag, actions=None):
        seen["urls"] = [u.toLocalFile() for u in drag.mimeData().urls()]
        return Qt.DropAction.CopyAction

    monkeypatch.setattr("ui.program_editor_window.QDrag.exec", fake_exec)
    editor._drag_program_out(0)
    assert len(seen["urls"]) == 1 and seen["urls"][0].endswith("DRUMS.p1") and os.path.exists(seen["urls"][0])
    assert editor.status_bar.currentMessage() == "Saved DRUMS.p1"
    assert editor._program_drag_active is False


def test_a_drag_dropped_nowhere_says_nothing_was_saved(qapp, s1000, monkeypatch):
    _fake, editor = s1000
    monkeypatch.setattr("ui.program_editor_window.QDrag.exec", lambda drag, actions=None: Qt.DropAction.IgnoreAction)
    editor._drag_program_out(0)
    assert "nothing saved" in editor.status_bar.currentMessage()


def test_the_program_list_reports_which_row_was_dragged(qapp, s1000):
    _fake, editor = s1000
    rows = []
    editor.program_list.drag_out_requested.connect(rows.append)
    editor.program_list.setCurrentRow(1)
    editor.program_list.startDrag(Qt.DropAction.CopyAction)
    assert rows == [1]


def test_drag_and_drop_decisions_are_logged(qapp, s1000, tmp_path, monkeypatch):
    import logging
    from core import debug_log

    fake, editor = s1000
    lines = []

    class _Capture(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    logger = debug_log.get_logger()
    handler = _Capture(level=logging.DEBUG)
    logger.addHandler(handler)
    try:
        path = _program_file(fake, tmp_path)
        monkeypatch.setattr(editor, "_load_program_path", lambda p: None)
        editor.main_tabs.setEnabled(False)
        editor.dropEvent(_drop_event(path))
        _pump_until(qapp, lambda: any("drop refused - the window is busy" in l for l in lines))
        editor.main_tabs.setEnabled(True)
        monkeypatch.setattr("ui.program_editor_window.QDrag.exec", lambda drag, actions=None: Qt.DropAction.CopyAction)
        editor._drag_program_out(0)
    finally:
        logger.removeHandler(handler)
    assert any("program file(s) dropped" in l for l in lines)
    assert any("read for a drag" in l and "DRUMS" in l for l in lines)
    assert any("drag of DRUMS.p1 ended with CopyAction" in l for l in lines)
