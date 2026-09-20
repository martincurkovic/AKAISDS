# tests for program_editor_window.py
# needs a real (offscreen) QApplication since these are actual widgets,
# not pure logic - same pattern used to verify the waveform renderer earlier

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from ui.program_editor_window import ProgramEditorWindow
from core.program_editor_bridge import MULTI_PART_COUNT


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
        self.multipart_channels = list(range(MULTI_PART_COUNT))
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
        if param.name == "PMCHAN":
            return 3
        if param.name == "PTUNO":
            return 256  # +1.00 semitone, see _tune_offset_to_semitones
        if param.name == "PRIORT":
            return 2  # high
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
    # the worker is a single persistent background thread now (see
    # BridgeWorker) rather than a fresh QThread per request, so
    # wait_until_idle() replaces waiting on a specific loader's own
    # .wait() - it blocks until every job submitted so far has actually
    # been processed and its result signal emitted. Delivery of that
    # (queued, cross-thread) signal to the slot that reacts to it still
    # needs a pump, same as before.
    editor._worker.wait_until_idle()  # program list loaded -> submits sample list
    _pump_until(qapp, lambda: editor.program_list.count() > 0)
    editor._worker.wait_until_idle()  # sample list loaded -> selects row 0
    _pump_until(qapp, lambda: editor.program_list.currentRow() == 0)


def _wait_for_keygroup_load(editor, qapp, expected_count):
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.keygroup_list.count() == expected_count)


@pytest.fixture
def editor(qapp):
    fake_main_window = QWidget()
    editor = ProgramEditorWindow(fake_main_window, bridge=FakeBridge())
    _wait_for_program_load(editor, qapp)
    _wait_for_keygroup_load(editor, qapp, expected_count=2)  # program 0 has 2
    yield editor
    # BridgeWorker is a persistent thread that only exits once told to (see
    # its stop()) - unlike the old per-action QThreads, it's still running
    # long after every test assertion above is done, so it must be shut
    # down explicitly or it gets garbage-collected mid-run, which is
    # exactly the real-hardware crash this class exists to prevent
    editor._worker.stop()
    editor._worker.wait()


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
    assert _keygroup_row_text(editor, 0) == "Keygroup 1: C0 - C3"


def test_program_tab_loads_channel_tune_and_priority_from_hardware(editor):
    # FakeBridge.get_parameter reports PMCHAN=3 (channel 4), PTUNO=256
    # (+1.00 semitone, see _tune_offset_to_semitones), PRIORT=2 (High)
    assert editor.midi_channel_combo.currentData() == 3
    assert editor.midi_channel_combo.currentText() == "4"
    assert editor.program_tune_spinbox.value() == pytest.approx(1.0)
    assert editor.note_priority_combo.currentText() == "High"


def test_changing_midi_channel_writes_pmchan(editor, qapp):
    bridge = editor._bridge

    editor.midi_channel_combo.setCurrentIndex(
        editor.midi_channel_combo.findData(255)  # Omni
    )
    editor._flush_write("PMCHAN")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert bridge.set_parameter_calls[-1] == ("PMCHAN", 0, 255, 0)


def test_changing_note_priority_writes_priort(editor, qapp):
    bridge = editor._bridge

    editor.note_priority_combo.setCurrentIndex(3)  # Hold

    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert bridge.set_parameter_calls[-1] == ("PRIORT", 0, 3, 0)


def test_changing_program_tune_writes_ptuno_in_raw_units(editor, qapp):
    # same raw encoding as the keygroup zone's Tune spinbox - 2.56 raw
    # units per cent, so -2.00 semitones is raw -512
    bridge = editor._bridge

    editor.program_tune_spinbox.setValue(-2.0)
    editor._flush_write("PTUNO")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert bridge.set_parameter_calls[-1] == ("PTUNO", 0, -512, 0)


def test_refresh_picks_up_a_program_created_on_the_hardware(editor, qapp):
    # regression test for a real bug: Refresh re-loaded the current
    # program's keygroups but never re-fetched the program list itself, so
    # a program created on the hardware after the editor opened only ever
    # showed up after closing and reopening the window
    bridge = editor._bridge
    bridge._programs.append("New Program")

    editor._refresh_from_hardware()
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.program_list.count() == 3)

    assert [
        editor.program_list.item(i).text() for i in range(editor.program_list.count())
    ] == ["Bass stab", "EPiano warm", "New Program"]
    # the previously-selected program stays selected across the refresh,
    # rather than resetting to the top of the list
    assert editor.program_list.currentItem().text() == "Bass stab"


def test_switching_program_replaces_keygroup_list_without_crashing(editor, qapp):
    editor.program_list.setCurrentRow(1)
    _wait_for_keygroup_load(editor, qapp, expected_count=1)  # program 1 has 1

    assert editor.keygroup_list.count() == 1
    assert _keygroup_row_text(editor, 0) == "Keygroup 1: C0 - C6"


def test_selecting_a_keygroup_shows_the_keygroup_panel_with_real_values(editor, qapp):
    editor.keygroup_list.setCurrentRow(1)
    editor._worker.wait_until_idle()
    # detail_stack switches synchronously on selection, before detail_loaded
    # is even delivered, so pump until the loader's actual effect lands
    _pump_until(qapp, lambda: editor.cutoff_knob.value() == 72)

    assert editor.detail_stack.currentIndex() == 1  # switched to the keygroup panel
    assert editor.cutoff_knob.value() == 72
    assert editor.resonance_knob.value() == 8
    assert editor.note_lo_spinbox.value() == 61
    assert editor.note_hi_spinbox.value() == 96


def _wait_for_multi_parts_load(editor, qapp):
    editor._worker.wait_until_idle()
    # part 0's channel combo already defaults to channel 0 before any load
    # (see _build_multis_tab), so it can't tell "loaded" from "not loaded
    # yet" - the program combo can: it starts on the blank "-" placeholder
    # and only shows a real program once _on_multi_parts_loaded runs
    _pump_until(qapp, lambda: editor._multi_program_combos[0].currentText() != "-")


def test_multi_parts_loaded_populates_combos_from_hardware(editor, qapp):
    _wait_for_multi_parts_load(editor, qapp)

    # FakeBridge's get_header cycles ["Bass stab", "EPiano warm"] by part
    # index and reports multipart_channels[index] == index
    assert editor._multi_program_combos[0].currentText() == "Bass stab"
    assert editor._multi_channel_combos[0].currentData() == 0
    assert editor._multi_program_combos[1].currentText() == "EPiano warm"
    assert editor._multi_channel_combos[1].currentData() == 1


def test_selecting_a_multi_part_program_targets_the_right_program_index(editor, qapp):
    # regression test for an off-by-one: the program combo has a blank "-"
    # placeholder at index 0 (see _build_multis_tab), so its currentIndex
    # is always one more than the program's actual position in
    # program_list. A part assigned "Bass stab" (program_list index 0)
    # must send a Program Change for program_index 0, not 1.
    _wait_for_multi_parts_load(editor, qapp)
    sent = []
    editor._worker.submit_program_change = lambda *a: sent.append(a)

    program_combo = editor._multi_program_combos[0]
    program_combo.setCurrentIndex(program_combo.findText("EPiano warm"))

    assert sent == [(0, 1, "EPiano warm", 0)]


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

    editor._worker.wait_until_idle()
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
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert bridge.set_parameter_calls[-1] == ("PMCHAN", 7, 11, 0)
