# tests for program_editor_window.py
# needs a real (offscreen) QApplication since these are actual widgets,
# not pure logic - same pattern used to verify the waveform renderer earlier

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget
from ui.program_editor_window import ProgramEditorWindow


@pytest.fixture(scope="session")
def qapp():
    # Qt allows exactly one QApplication per process - share it across
    # every test in this file rather than creating a new one each time
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def editor(qapp):
    fake_main_window = (
        QWidget()
    )  # just needs a .show() method - a plain QWidget has one
    return ProgramEditorWindow(fake_main_window)


def test_first_program_is_preselected_with_its_keygroups_shown(editor):
    assert editor.program_list.currentItem().text() == "Bass stab"
    assert editor.keygroup_list.count() == 3
    assert editor.keygroup_list.item(0).text() == "C1 - F2"


def test_switching_program_replaces_keygroup_list_without_crashing(editor):
    # this is the direct regression test for the "current=None" guard -
    # clearing keygroup_list mid-switch fires currentItemChanged with
    # current=None, and without the guard this crashes on current.text()
    editor.program_list.setCurrentRow(1)  # "EPiano warm"

    assert editor.keygroup_list.count() == 2
    assert editor.keygroup_list.item(0).text() == "A0 - G3"
    assert editor.detail_label.text() == "Select a keygroup"


def test_selecting_a_keygroup_updates_the_detail_label(editor):
    editor.keygroup_list.setCurrentRow(1)
    assert editor.detail_label.text() == "Keygroup range: F#2 - C4"
