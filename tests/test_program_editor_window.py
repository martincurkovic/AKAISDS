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
        self._keygroups = {
            0: [(24, 60), (61, 96)],  # (LONOTE, HINOTE) per keygroup
            1: [(24, 96)],
        }

    def program_list(self):
        return self._programs

    def get_parameter(self, param, program_index, keygroup=0, **kwargs):
        keygroups = self._keygroups[program_index]
        if keygroup >= len(keygroups):
            raise ValueError(
                f"program {program_index} has {len(keygroups)} keygroup(s)"
            )
        lo, hi = keygroups[keygroup]
        return lo if param.name == "LONOTE" else hi


@pytest.fixture(scope="session")
def qapp():
    # Qt allows exactly one QApplication per process - share it across
    # every test in this file rather than creating a new one each time
    app = QApplication.instance() or QApplication([])
    yield app


def _wait_for_load(editor, qapp):
    editor._loader.wait()
    qapp.processEvents()


@pytest.fixture
def editor(qapp):
    fake_main_window = (
        QWidget()
    )  # just needs a .show() method - a plain QWidget has one
    editor = ProgramEditorWindow(fake_main_window, bridge=FakeBridge())
    _wait_for_load(editor, qapp)
    return editor


def test_first_program_is_preselected_with_its_keygroups_shown(editor):
    assert editor.program_list.currentItem().text() == "Bass stab"
    assert editor.keygroup_list.count() == 2
    assert editor.keygroup_list.item(0).text() == "24 - 60"


def test_switching_program_replaces_keygroup_list_without_crashing(editor, qapp):
    editor.program_list.setCurrentRow(1)
    _wait_for_load(editor, qapp)

    assert editor.keygroup_list.count() == 1
    assert editor.keygroup_list.item(0).text() == "24 - 96"


def test_selecting_a_keygroup_updates_the_detail_label(editor):
    editor.keygroup_list.setCurrentRow(1)
    assert editor.detail_label.text() == "Keygroup range: 61 - 96"
