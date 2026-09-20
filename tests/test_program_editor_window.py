# tests for program_editor_window.py
# needs a real (offscreen) QApplication since these are actual widgets,
# not pure logic - same pattern used to verify the waveform renderer earlier

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from ui.program_editor_window import ProgramEditorWindow
from core.program_editor_bridge import MultiPartsLoader


class FakeBridge:
    def __init__(self):
        self._programs = ["Bass stab", "EPiano warm"]
        self._pan = {0: -10, 1: 25}
        self._samples = ["SQUARE", "SAWTOOTH", "PULSE", "SINE"]
        self._keygroups = {
            0: [(24, 60), (61, 96)],  # (LONOTE, HINOTE) per keygroup
            1: [(24, 96)],
        }
        self._details = {0: 72, 1: 8, 2: 30, 3: 60, 4: 90}
        # multipart channel per part - distinct values so a test can catch
        # one part's write landing on the wrong part
        self.multipart_channels = list(range(MultiPartsLoader.PART_COUNT))
        self.set_parameter_calls = []

    def sample_list(self):
        return self._samples

    def program_list(self):
        return self._programs

    def get_header(self, region, index, **kwargs):
        if region == "multipart":
            return {
                "PRNAME": self._programs[index % len(self._programs)],
                "PMCHAN": self.multipart_channels[index],
            }
        raise AssertionError(f"unexpected region {region!r}")

    def set_parameter(self, param, program_index, value, *, keygroup=0):
        self.set_parameter_calls.append((param.name, program_index, value, keygroup))
        if param.name == "PMCHAN":
            self.multipart_channels[program_index] = value

    def get_parameter(self, param, program_index, keygroup=0, **kwargs):
        if param.name == "PANPOS":
            return self._pan[program_index]
        if param.name == "POLYPH":
            return 8
        if param.name in ("LFORAT", "LFODEP", "LFODEL", "LFO1WAVE"):
            return 0
        if param.name in ("SNAME1", "SNAME2", "SNAME3", "SNAME4"):
            return "SQUARE"  # a real sample name that matches _samples below
        keygroups = self._keygroups[program_index]
        if param.name == "GROUPS":
            return len(keygroups)
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


def _pump_until(qapp, predicate, timeout=2.0):
    # a queued cross-thread signal isn't always delivered by a single
    # processEvents() call - measured to sometimes need a second pass before
    # the slot it triggers (which sets the attribute a caller is waiting on)
    # actually runs, so this polls instead of assuming one pump is enough
    deadline = time.monotonic() + timeout
    while not predicate():
        qapp.processEvents()
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met before timeout")


def _wait_for_program_load(editor, qapp):
    editor._program_loader.wait()
    # delivers programs_loaded, which starts _sample_loader
    _pump_until(qapp, lambda: hasattr(editor, "_sample_loader"))
    editor._sample_loader.wait()
    # delivers samples_loaded, which selects row 0 and starts _keygroup_loader
    _pump_until(qapp, lambda: hasattr(editor, "_keygroup_loader"))


def _wait_for_keygroup_load(editor, qapp, expected_count):
    editor._keygroup_loader.wait()
    _pump_until(qapp, lambda: editor.keygroup_list.count() == expected_count)


@pytest.fixture
def editor(qapp):
    fake_main_window = QWidget()
    editor = ProgramEditorWindow(fake_main_window, bridge=FakeBridge())
    _wait_for_program_load(editor, qapp)
    _wait_for_keygroup_load(editor, qapp, expected_count=2)  # program 0 has 2
    return editor


def _keygroup_row_text(editor, row):
    # keygroup rows are a swatch + QLabel row widget (program_editor_window's
    # _add_keygroup_row), not plain text items - the item's own .text() is
    # always empty, the displayed text lives in the row widget's label
    item = editor.keygroup_list.item(row)
    label = editor.keygroup_list.itemWidget(item).findChild(QLabel, "keygroupRangeLabel")
    return label.text()


def test_first_program_is_preselected_with_its_keygroups_shown(editor):
    assert editor.program_list.currentItem().text() == "Bass stab"
    assert editor.keygroup_list.count() == 2
    assert _keygroup_row_text(editor, 0) == "Keygroup 1: C1 - C4"


def test_switching_program_replaces_keygroup_list_without_crashing(editor, qapp):
    editor.program_list.setCurrentRow(1)
    _wait_for_keygroup_load(editor, qapp, expected_count=1)  # program 1 has 1

    assert editor.keygroup_list.count() == 1
    assert _keygroup_row_text(editor, 0) == "Keygroup 1: C1 - C7"


def test_selecting_a_keygroup_shows_the_keygroup_panel_with_real_values(editor, qapp):
    editor.keygroup_list.setCurrentRow(1)
    editor._detail_loader.wait()
    # detail_stack switches synchronously on selection, before detail_loaded
    # is even delivered, so pump until the loader's actual effect lands
    _pump_until(qapp, lambda: editor.cutoff_knob.value() == 72)

    assert editor.detail_stack.currentIndex() == 1  # switched to the keygroup panel
    assert editor.cutoff_knob.value() == 72
    assert editor.resonance_knob.value() == 8
    assert editor.note_lo_spinbox.value() == 61
    assert editor.note_hi_spinbox.value() == 96


def _wait_for_multi_parts_load(editor, qapp):
    editor._multi_parts_loader.wait()
    # the loader's initial run sets part 0's channel combo from FakeBridge's
    # multipart_channels[0] == 0 ("1") - a real, if arbitrary, landmark to
    # pump until, since nothing else about the combos changes on its own
    _pump_until(qapp, lambda: editor._multi_channel_combos[0].currentData() == 0)


def test_changing_two_multi_part_channels_writes_both_independently(editor, qapp):
    # regression test for a real bug: _schedule_write/_flush_write/
    # _write_knob_value used to key every pending write by bare param_name
    # alone, which was safe when only one keygroup could ever be "current"
    # at a time. All 16 Multis-tab parts share the field name PMCHAN but are
    # all independently editable at once, so changing two parts' channels in
    # quick succession used to let the second write silently clobber the
    # first's pending value (and risk its in-flight QThread being garbage
    # collected before it finished).
    _wait_for_multi_parts_load(editor, qapp)
    bridge = editor._bridge

    editor._multi_channel_combos[0].setCurrentIndex(
        editor._multi_channel_combos[0].findData(5)
    )
    editor._multi_channel_combos[1].setCurrentIndex(
        editor._multi_channel_combos[1].findData(9)
    )

    # flush both pending debounced writes immediately rather than waiting
    # out the real 500ms timer
    editor._flush_write("multipart_channel_0")
    editor._flush_write("multipart_channel_1")

    for writer in list(editor._active_writers.values()):
        writer.wait()
    _pump_until(qapp, lambda: len(bridge.set_parameter_calls) >= 2)

    pmchan_calls = [c for c in bridge.set_parameter_calls if c[0] == "PMCHAN"]
    assert ("PMCHAN", 0, 5, 0) in pmchan_calls
    assert ("PMCHAN", 1, 9, 0) in pmchan_calls


def test_multi_part_channel_write_targets_the_part_not_the_selected_program(
    editor, qapp
):
    # _write_knob_value's index= override is what makes the write above
    # target part_index rather than program_list.currentRow() (the usual
    # target for every other field) - pin that down directly, since a
    # regression here would silently redirect every multipart write to
    # whatever program happens to be selected on the Programs tab
    _wait_for_multi_parts_load(editor, qapp)
    assert editor.program_list.currentRow() == 0  # from the editor fixture
    bridge = editor._bridge

    editor._schedule_write(
        "PMCHAN", "multipart", 11, index=7, debounce_key="multipart_channel_7"
    )
    editor._flush_write("multipart_channel_7")
    editor._active_writers["multipart_channel_7"].wait()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert bridge.set_parameter_calls[-1] == ("PMCHAN", 7, 11, 0)
