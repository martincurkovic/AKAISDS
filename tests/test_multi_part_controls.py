# The Multi tab's per-part priority, key range, transpose and tune controls (ui/program_editor_window.py _build_multis_tab).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_program_editor_window import _pump_until, _wait_for_multi_parts_load, editor, qapp  # noqa: F401

EXTRA_FIELDS = ("PRIORT", "PLAYLO", "PLAYHI", "TRANSPOSE", "PTUNOCM")


def extra_calls(bridge):
    return [c for c in bridge.set_parameter_calls if c[0] in EXTRA_FIELDS]


def wait_for_calls(editor, qapp, count):  # noqa: F811
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: len(extra_calls(editor._bridge)) >= count)
    return extra_calls(editor._bridge)


def test_the_new_controls_show_what_the_sampler_holds(editor, qapp):  # noqa: F811
    _wait_for_multi_parts_load(editor, qapp)
    # FakeBridge: priority index % 4, low 24 + index, high 127 - index, transpose index - 8, tune 2 * index - 16
    assert editor._multi_priority_combos[3].currentText() == "Hold" and editor._multi_priority_combos[0].currentText() == "Low"
    assert (editor._multi_low_spins[3].value(), editor._multi_high_spins[3].value()) == (27, 124)
    assert editor._multi_low_spins[3].text() == "D#0" and editor._multi_high_spins[3].text() == "E8"  # note names (60 = C3), not numbers
    assert editor._multi_transpose_knobs[2].value() == -6 and editor._multi_transpose_value_labels[2].text() == "-6"
    assert editor._multi_transpose_knobs[8].value() == 0 and editor._multi_transpose_value_labels[8].text() == "0"
    assert editor._multi_tune_knobs[10].value() == 4 and editor._multi_tune_value_labels[10].text() == "+4"


def test_showing_the_values_writes_nothing(editor, qapp):  # noqa: F811
    _wait_for_multi_parts_load(editor, qapp)
    editor._worker.wait_until_idle()
    qapp.processEvents()
    assert extra_calls(editor._bridge) == []  # a load must not echo what it just read back to the sampler


def test_choosing_a_priority_writes_the_raw_value_to_that_part(editor, qapp):  # noqa: F811
    _wait_for_multi_parts_load(editor, qapp)
    combo = editor._multi_priority_combos[5]
    combo.setCurrentIndex(combo.findText("High"))
    editor._flush_write("multipart_priority_5")
    assert wait_for_calls(editor, qapp, 1) == [("PRIORT", 5, 2, 0)]


def test_the_transpose_and_tune_knobs_write_and_show_a_signed_value(editor, qapp):  # noqa: F811
    _wait_for_multi_parts_load(editor, qapp)
    editor._multi_transpose_knobs[2].setValue(7)
    editor._multi_tune_knobs[2].setValue(-15)
    assert editor._multi_transpose_value_labels[2].text() == "+7" and editor._multi_tune_value_labels[2].text() == "-15"
    editor._flush_write("multipart_transpose_2")
    editor._flush_write("multipart_tune_2")
    assert wait_for_calls(editor, qapp, 2) == [("TRANSPOSE", 2, 7, 0), ("PTUNOCM", 2, -15, 0)]


def test_a_typed_key_range_is_written_on_enter_not_per_keystroke(editor, qapp):  # noqa: F811
    _wait_for_multi_parts_load(editor, qapp)
    spin = editor._multi_high_spins[1]  # 126
    assert not spin.keyboardTracking()
    spin.setValue(90)
    editor._flush_write("multipart_playhi_1")
    assert wait_for_calls(editor, qapp, 1) == [("PLAYHI", 1, 90, 0)]


def test_raising_low_above_high_pushes_high_up_and_writes_it_first(editor, qapp):  # noqa: F811
    _wait_for_multi_parts_load(editor, qapp)
    low, high = editor._multi_low_spins[3], editor._multi_high_spins[3]  # 27..124
    high.setValue(40)
    editor._flush_write("multipart_playhi_3")
    low.setValue(60)  # above High
    editor._flush_write("multipart_playlo_3")
    assert high.value() == 60 and low.value() == 60
    assert wait_for_calls(editor, qapp, 3) == [("PLAYHI", 3, 40, 0), ("PLAYHI", 3, 60, 0), ("PLAYLO", 3, 60, 0)]  # never Low above High on the sampler


def test_lowering_high_below_low_pushes_low_down_and_writes_it_first(editor, qapp):  # noqa: F811
    _wait_for_multi_parts_load(editor, qapp)
    low, high = editor._multi_low_spins[3], editor._multi_high_spins[3]  # 27..124
    low.setValue(80)
    editor._flush_write("multipart_playlo_3")
    high.setValue(50)  # below Low
    editor._flush_write("multipart_playhi_3")
    assert low.value() == 50 and high.value() == 50
    assert wait_for_calls(editor, qapp, 3) == [("PLAYLO", 3, 80, 0), ("PLAYLO", 3, 50, 0), ("PLAYHI", 3, 50, 0)]


def test_parts_are_independent(editor, qapp):  # noqa: F811
    _wait_for_multi_parts_load(editor, qapp)
    editor._multi_transpose_knobs[0].setValue(3)
    editor._multi_transpose_knobs[1].setValue(4)
    editor._flush_write("multipart_transpose_0")
    editor._flush_write("multipart_transpose_1")
    assert wait_for_calls(editor, qapp, 2) == [("TRANSPOSE", 0, 3, 0), ("TRANSPOSE", 1, 4, 0)]


def test_the_row_still_fits_the_window_without_a_horizontal_scroll_bar(editor):  # noqa: F811
    scroll = editor.main_tabs.widget(0)
    assert scroll.widget().minimumSizeHint().width() < editor.minimumWidth() - 40


def test_the_columns_run_program_level_pan_channel_range_semi_cents_priority(editor, qapp):  # noqa: F811
    from PySide6.QtCore import QPoint

    editor.main_tabs.setCurrentIndex(0)
    editor.show()
    qapp.processEvents()
    x = lambda w: w.mapTo(editor, QPoint(0, 0)).x()  # noqa: E731
    order = [
        editor._multi_program_combos[0],
        editor._multi_level_knobs[0],
        editor._multi_pan_knobs[0],
        editor._multi_channel_combos[0],
        editor._multi_low_spins[0],
        editor._multi_high_spins[0],
        editor._multi_transpose_knobs[0],
        editor._multi_tune_knobs[0],
        editor._multi_priority_combos[0],
    ]
    positions = [x(w) for w in order]
    assert positions == sorted(positions) and len(set(positions)) == len(positions), positions
