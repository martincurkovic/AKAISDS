# Save/Load Program (.p1/.p3) in ProgramEditorWindow, end to end against
# FakeS1000 (real adapter + real BridgeWorker thread). Dialogs are patched.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox

from core import akai_program_file as apf
from core.program_editor_bridge import LoggingBridge
from core.s1000_bridge import S1000Bridge
from tests.test_program_editor_window_s1000 import (  # noqa: F401 - fixtures/helpers
    _dispose,
    _make_editor,
    _pump_until,
    qapp,
)
from tests.test_akai_program_file import PointerFake, _blocks


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
