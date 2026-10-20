# ui/spinbox_drag.py: every spin box drags like a knob and takes a click-then-type replacement. Events are sent as QMouseEvents with explicit global positions
# (QTest's mouse helpers do not carry the held button on moves).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QSpinBox, QVBoxLayout, QWidget

from ui import spinbox_drag
from ui.note_spinbox import NoteSpinBox


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def installed(qapp):
    spinbox_drag.install(qapp)
    yield
    spinbox_drag.uninstall(qapp)
    while QApplication.overrideCursor() is not None:
        QApplication.restoreOverrideCursor()


def window_with(qapp, *spins):
    window = QWidget()
    layout = QVBoxLayout(window)
    for spin in spins:
        layout.addWidget(spin)
    window.show()
    qapp.processEvents()
    return window


def mouse(kind, edit, y, *, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier):
    """A mouse event for `edit` at screen height y (x is fixed)."""
    if kind == QEvent.Type.MouseButtonRelease:
        buttons = Qt.MouseButton.NoButton
    local = QPointF(5, 5)
    event = QMouseEvent(kind, local, QPointF(100, y), button, buttons, modifiers)
    QApplication.sendEvent(edit, event)


def press(edit, y=300):
    mouse(QEvent.Type.MouseButtonPress, edit, y)


def move(edit, y, **kw):
    mouse(QEvent.Type.MouseMove, edit, y, button=Qt.MouseButton.NoButton, **kw)


def release(edit, y):
    mouse(QEvent.Type.MouseButtonRelease, edit, y)


def drag(qapp, spin, dy, **kw):
    edit = spin.lineEdit()
    press(edit)
    move(edit, 296, **kw)  # (the first few pixels only START the drag: it does not jump)
    move(edit, 296 - dy, **kw)  # dy > 0 = up
    release(edit, 296 - dy)
    qapp.processEvents()


def test_dragging_up_raises_and_down_lowers_the_value(qapp, installed):
    spin = QSpinBox()
    spin.setRange(0, 99)
    spin.setValue(50)
    window = window_with(qapp, spin)
    drag(qapp, spin, 20)  # 0..99 over 200 px = 0.495 steps/px
    assert spin.value() == 59
    drag(qapp, spin, -40)
    assert spin.value() == 40  # -19.8 steps: a drag moves whole steps toward zero, the fraction carries within one drag only
    window.close()


def test_the_value_stops_at_the_ends_of_the_range(qapp, installed):
    spin = QSpinBox()
    spin.setRange(0, 99)
    spin.setValue(95)
    window = window_with(qapp, spin)
    drag(qapp, spin, 500)
    assert spin.value() == 99
    drag(qapp, spin, -2000)
    assert spin.value() == 0
    window.close()


def test_a_click_selects_everything_so_typing_replaces_the_value(qapp, installed):
    spin = QSpinBox()
    spin.setRange(0, 999)
    spin.setValue(123)
    window = window_with(qapp, spin)
    edit = spin.lineEdit()
    press(edit)
    release(edit, 300)
    qapp.processEvents()
    assert edit.hasFocus() and edit.selectedText() == "123" and spin.value() == 123  # a click alone changes nothing
    QTest.keyClicks(edit, "42")
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert spin.value() == 42
    window.close()


def test_a_drag_leaves_the_text_selected_for_typing(qapp, installed):
    spin = QSpinBox()
    spin.setRange(0, 999)
    spin.setValue(100)
    window = window_with(qapp, spin)
    drag(qapp, spin, 10)
    edit = spin.lineEdit()
    assert edit.hasFocus() and edit.selectedText() == str(spin.value()) and spin.value() != 100
    QTest.keyClicks(edit, "7")
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert spin.value() == 7
    window.close()


def test_a_movement_under_the_threshold_is_still_a_click(qapp, installed):
    spin = QSpinBox()
    spin.setRange(0, 99)
    spin.setValue(50)
    window = window_with(qapp, spin)
    edit = spin.lineEdit()
    press(edit)
    move(edit, 298)  # 2 px
    release(edit, 298)
    qapp.processEvents()
    assert spin.value() == 50 and edit.selectedText() == "50" and QApplication.overrideCursor() is None
    window.close()


def test_a_box_that_is_being_edited_keeps_normal_clicks(qapp, installed):
    spin = QSpinBox()
    spin.setRange(0, 999)
    spin.setValue(123)
    window = window_with(qapp, spin)
    edit = spin.lineEdit()
    press(edit)
    release(edit, 300)
    qapp.processEvents()
    assert edit.selectedText() == "123"
    QTest.keyClick(edit, Qt.Key.Key_Left)  # the user puts the text cursor in the text: now they are editing
    assert edit.selectedText() == ""
    press(edit)
    move(edit, 200)  # a big movement: text behaviour, not a value drag
    release(edit, 200)
    qapp.processEvents()
    assert spin.value() == 123
    window.close()


def test_a_box_that_just_happens_to_have_the_focus_still_drags(qapp, installed):
    # (the first box in a window or dialog starts focused, with no one having edited it)
    spin = QSpinBox()
    spin.setRange(0, 99)
    spin.setValue(50)
    window = window_with(qapp, spin)
    spin.lineEdit().setFocus()
    qapp.processEvents()
    drag(qapp, spin, 20)
    assert spin.value() == 59
    window.close()


def test_shift_makes_the_drag_finer(qapp, installed):
    coarse, fine = QSpinBox(), QSpinBox()
    for spin in (coarse, fine):
        spin.setRange(0, 1000)
        spin.setValue(500)
    window = window_with(qapp, coarse, fine)
    drag(qapp, coarse, 40)
    drag(qapp, fine, 40, modifiers=Qt.KeyboardModifier.ShiftModifier)
    assert coarse.value() - 500 == 200  # 5 steps per pixel
    assert 0 < fine.value() - 500 <= 25  # 8 times finer
    window.close()


def test_a_wide_range_moves_faster_than_a_narrow_one(qapp, installed):
    narrow, wide = QSpinBox(), QSpinBox()
    narrow.setRange(0, 99)
    wide.setRange(0, 100000)
    window = window_with(qapp, narrow, wide)
    drag(qapp, narrow, 40)
    drag(qapp, wide, 40)
    assert wide.value() > 100 * narrow.value()
    window.close()


def test_a_slow_drag_adds_up_instead_of_losing_the_fractions(qapp, installed):
    spin = QSpinBox()
    spin.setRange(0, 99)
    spin.setValue(10)
    window = window_with(qapp, spin)
    edit = spin.lineEdit()
    press(edit)
    for y in range(299, 250, -1):  # one pixel at a time, 49 px = about 24 steps
        move(edit, y)
    release(edit, 251)
    assert 30 <= spin.value() <= 36
    window.close()


def test_a_double_spin_box_moves_by_its_single_step(qapp, installed):
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 20.0)
    spin.setSingleStep(0.5)
    spin.setValue(10.0)
    window = window_with(qapp, spin)
    drag(qapp, spin, 30)  # 40 steps over 200 px = 0.2 steps/px -> floor at 1 step / 3 px: 10 steps
    assert spin.value() == pytest.approx(10.0 + 0.5 * 10)
    window.close()


def test_the_note_spin_box_drags_and_takes_a_typed_note_name(qapp, installed):
    spin = NoteSpinBox()
    spin.setRange(21, 127)
    spin.setValue(60)
    window = window_with(qapp, spin)
    drag(qapp, spin, 10)
    assert spin.value() > 60
    edit = spin.lineEdit()
    press(edit, 300)
    release(edit, 300)
    qapp.processEvents()
    QTest.keyClicks(edit, "C4")
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert spin.value() == 72  # (C4 on the S3000 panel's scale)
    window.close()


def test_the_cursor_is_a_vertical_arrow_only_while_dragging(qapp, installed):
    spin = QSpinBox()
    spin.setRange(0, 99)
    window = window_with(qapp, spin)
    edit = spin.lineEdit()
    press(edit)
    assert QApplication.overrideCursor() is None
    move(edit, 250)
    assert QApplication.overrideCursor().shape() == Qt.CursorShape.SizeVerCursor
    release(edit, 250)
    assert QApplication.overrideCursor() is None
    window.close()


def test_a_lost_release_does_not_leave_the_drag_running(qapp, installed):
    spin = QSpinBox()
    spin.setRange(0, 99)
    spin.setValue(50)
    window = window_with(qapp, spin)
    edit = spin.lineEdit()
    press(edit)
    move(edit, 250)
    value = spin.value()
    mouse(QEvent.Type.MouseMove, edit, 200, button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.NoButton)  # no button held any more
    assert QApplication.overrideCursor() is None and spin.value() == value
    window.close()


def test_disabled_and_read_only_boxes_do_not_drag(qapp, installed):
    off, readonly = QSpinBox(), QSpinBox()
    for spin in (off, readonly):
        spin.setRange(0, 99)
        spin.setValue(50)
    off.setEnabled(False)
    readonly.setReadOnly(True)
    window = window_with(qapp, off, readonly)
    drag(qapp, off, 50)
    drag(qapp, readonly, 50)
    assert off.value() == 50 and readonly.value() == 50
    window.close()


def test_other_mouse_buttons_are_left_alone(qapp, installed):
    spin = QSpinBox()
    spin.setRange(0, 99)
    spin.setValue(50)
    window = window_with(qapp, spin)
    edit = spin.lineEdit()
    mouse(QEvent.Type.MouseButtonPress, edit, 300, button=Qt.MouseButton.RightButton, buttons=Qt.MouseButton.RightButton)
    move(edit, 200)
    assert spin.value() == 50
    window.close()


def test_steps_per_pixel_is_bounded_both_ways(qapp):
    tiny, huge = QSpinBox(), QSpinBox()
    tiny.setRange(0, 3)
    huge.setRange(0, 2_000_000_000)
    assert spinbox_drag.steps_per_pixel(tiny) == pytest.approx(1 / spinbox_drag.MIN_PX_PER_STEP)
    assert spinbox_drag.steps_per_pixel(huge) == spinbox_drag.MAX_STEPS_PER_PX
    assert spinbox_drag.steps_per_pixel(huge, fine=True) == spinbox_drag.MAX_STEPS_PER_PX / spinbox_drag.FINE_DIVISOR


def test_installing_twice_gives_one_filter(qapp):
    first = spinbox_drag.install(qapp)
    assert spinbox_drag.install(qapp) is first
    spinbox_drag.uninstall(qapp)
