# tests for program_editor_window.py
# needs a real (offscreen) QApplication since these are actual widgets,
# not pure logic - same pattern used to verify the waveform renderer earlier

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget
from ui.program_editor_window import ProgramEditorWindow


class FakeBridge:
    def __init__(self):
        self._programs = ["Bass stab", "EPiano warm"]
        self._pan = {0: -10, 1: 25}
        self._keygroups = {
            0: [(24, 60), (61, 96)],  # (LONOTE, HINOTE) per keygroup
            1: [(24, 96)],
        }
        self._details = {0: 72, 1: 8, 2: 30, 3: 60, 4: 90}

    def program_list(self):
        return self._programs

    def get_parameter(self, param, program_index, keygroup=0, **kwargs):
        if param.name == "PANPOS":
            return self._pan[program_index]
        keygroups = self._keygroups[program_index]
        if keygroup >= len(keygroups):
            raise ValueError(
                f"program {program_index} has {len(keygroups)} keygroup(s)"
            )
        if param.name == "LONOTE":
            return keygroups[keygroup][0]
        if param.name == "HINOTE":
            return keygroups[keygroup][1]
        if param.name == "FILFRQ":
            return 72
        if param.name == "FILQ":
            return 8
        return 30


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _wait_for_program_load(editor, qapp):
    editor._program_loader.wait()
    qapp.processEvents()


def _wait_for_keygroup_load(editor, qapp):
    editor._loader.wait()
    qapp.processEvents()


@pytest.fixture
def editor(qapp):
    fake_main_window = QWidget()
    editor = ProgramEditorWindow(fake_main_window, bridge=FakeBridge())
    _wait_for_program_load(editor, qapp)
    _wait_for_keygroup_load(editor, qapp)
    return editor


def test_first_program_is_preselected_with_its_keygroups_shown(editor):
    assert editor.program_list.currentItem().text() == "Bass stab"
    assert editor.keygroup_list.count() == 2
    assert editor.keygroup_list.item(0).text() == "24 - 60"


def test_switching_program_replaces_keygroup_list_without_crashing(editor, qapp):
    editor.program_list.setCurrentRow(1)
    _wait_for_keygroup_load(editor, qapp)

    assert editor.keygroup_list.count() == 1
    assert editor.keygroup_list.item(0).text() == "24 - 96"


def test_selecting_a_keygroup_shows_the_keygroup_panel_with_real_values(editor, qapp):
    editor.keygroup_list.setCurrentRow(1)
    editor._detail_loader.wait()
    qapp.processEvents()

    assert editor.detail_stack.currentIndex() == 1  # switched to the keygroup panel
    assert editor.cutoff_knob.value() == 72
    assert editor.resonance_knob.value() == 8
