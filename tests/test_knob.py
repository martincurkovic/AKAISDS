# tests for ui/knob.py's Knob - specifically the drag-to-value math in
# mouseMoveEvent (sensitivity scaling + clamping) and defaultValue(), since
# a regression in either (wrong sign, dropped clamp, wrong midpoint) would
# make dragging feel broken or let a knob's value escape its own range.
#
# Uses a tiny duck-typed fake mouse event rather than a real QMouseEvent -
# Knob only ever calls .button() and .position().y() on what it's given.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from ui.knob import Knob, _DRAG_SENSITIVITY_PX


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeMouseEvent:
    def __init__(self, y, button=Qt.MouseButton.LeftButton):
        self._y = y
        self._button = button

    def button(self):
        return self._button

    def position(self):
        return QPointF(0, self._y)


def _drag(knob, start_y, end_y):
    knob.mousePressEvent(_FakeMouseEvent(start_y))
    knob.mouseMoveEvent(_FakeMouseEvent(end_y))


def test_dragging_upward_increases_the_value(qapp):
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(50)

    # cursor moves UP (smaller y) - a knob physically turned clockwise/up
    _drag(knob, start_y=100, end_y=100 - _DRAG_SENSITIVITY_PX)

    # a full _DRAG_SENSITIVITY_PX of upward movement covers the whole range
    assert knob.value() == 100


def test_dragging_downward_decreases_the_value(qapp):
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(50)

    _drag(knob, start_y=100, end_y=100 + _DRAG_SENSITIVITY_PX)

    assert knob.value() == 0


def test_drag_is_proportional_to_the_configured_range(qapp):
    knob = Knob()
    knob.setRange(0, 30)
    knob.setValue(15)

    _drag(knob, start_y=100, end_y=100 - (_DRAG_SENSITIVITY_PX / 2))

    assert knob.value() == 30  # half the drag distance, but also half the range


def test_dragging_past_the_range_clamps_rather_than_wrapping(qapp):
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(50)

    _drag(knob, start_y=100, end_y=100 - _DRAG_SENSITIVITY_PX * 10)

    assert knob.value() == 100

    _drag(knob, start_y=100, end_y=100 + _DRAG_SENSITIVITY_PX * 10)

    assert knob.value() == 0


def test_mouse_move_without_a_preceding_press_is_a_no_op(qapp):
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(50)

    knob.mouseMoveEvent(_FakeMouseEvent(0))

    assert knob.value() == 50


def test_default_value_falls_back_to_the_range_midpoint(qapp):
    knob = Knob()
    knob.setRange(0, 100)

    assert knob.defaultValue() == 50


def test_default_value_uses_an_explicitly_set_value_instead_of_the_midpoint(qapp):
    knob = Knob()
    knob.setRange(0, 100)
    knob.setDefaultValue(75)

    assert knob.defaultValue() == 75


def test_double_click_resets_to_the_default_value(qapp):
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(10)
    knob.setDefaultValue(75)

    knob.mouseDoubleClickEvent(_FakeMouseEvent(0))

    assert knob.value() == 75


def _wheel_event(delta_y):
    # a real QWheelEvent, not a duck-typed fake - Knob.wheelEvent delegates
    # to QDial's own C++ implementation via super(), which needs the real
    # thing (angleDelta(), isAccepted(), etc)
    return QWheelEvent(
        QPointF(0, 0),
        QPointF(0, 0),
        QPoint(0, 0),
        QPoint(0, delta_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_wheel_at_max_value_is_still_accepted(qapp):
    # plain QDial leaves this kind of event un-accepted once already
    # clamped at its max - Qt then walks it up to the nearest ancestor
    # that DOES want it, which in the real app is the section card's
    # QScrollArea: scrolling a maxed-out knob further scrolled the whole
    # page instead of doing nothing. See Knob.wheelEvent's own comment.
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(100)

    event = _wheel_event(120)
    knob.wheelEvent(event)

    assert knob.value() == 100
    assert event.isAccepted()


def test_wheel_at_min_value_is_still_accepted(qapp):
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(0)

    event = _wheel_event(-120)
    knob.wheelEvent(event)

    assert knob.value() == 0
    assert event.isAccepted()


class _FakeKeyEvent:
    # Knob.keyPressEvent only calls .text() to decide whether to open the
    # type-to-enter box - no key code needed for that branch
    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text


def test_typing_a_digit_opens_the_type_edit_box(qapp):
    knob = Knob()
    knob.setRange(0, 9999)

    knob.keyPressEvent(_FakeKeyEvent("3"))

    assert knob._type_edit is not None
    assert knob._type_edit.text() == "3"


def test_minus_only_opens_the_type_edit_box_on_a_signed_range(qapp):
    # unsigned falls through to QDial's own keyPressEvent (real base-class
    # behaviour, e.g. arrow-key stepping) - needs a real QKeyEvent, unlike
    # the digit/signed-minus branch which returns before ever touching it
    real_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Minus, Qt.KeyboardModifier.NoModifier, "-")
    unsigned = Knob()
    unsigned.setRange(0, 100)
    unsigned.keyPressEvent(real_event)
    assert unsigned._type_edit is None

    signed = Knob()
    signed.setRange(-50, 50)
    signed.keyPressEvent(_FakeKeyEvent("-"))
    assert signed._type_edit is not None


def test_pressing_enter_in_the_type_edit_commits_the_typed_value(qapp):
    knob = Knob()
    knob.setRange(0, 9999)
    knob.setValue(10)
    released = []
    knob.sliderReleased.connect(lambda: released.append(knob.value()))

    knob._begin_type_edit("1")
    knob._type_edit.setText("1500")
    knob._type_edit.returnPressed.emit()

    assert knob.value() == 1500
    assert knob._type_edit is None  # cleaned up after commit
    assert released == [1500]  # write-commit wiring reacts the same as a drag


class _FakeEscapeEvent:
    # _TypeEdit.keyPressEvent only calls .key() for the Escape check
    def key(self):
        return Qt.Key.Key_Escape


def test_escape_in_the_type_edit_cancels_without_changing_the_value(qapp):
    knob = Knob()
    knob.setRange(0, 9999)
    knob.setValue(42)

    knob._begin_type_edit("9")
    knob._type_edit.setText("9999")
    knob._type_edit.keyPressEvent(_FakeEscapeEvent())

    assert knob.value() == 42
    assert knob._type_edit is None


def test_typed_value_out_of_range_clamps_rather_than_erroring(qapp):
    knob = Knob()
    knob.setRange(0, 9999)

    knob._begin_type_edit("9")
    knob._type_edit.setText("50000")
    knob._type_edit.returnPressed.emit()

    assert knob.value() == 9999


def test_committing_an_empty_type_edit_leaves_the_value_unchanged(qapp):
    knob = Knob()
    knob.setRange(0, 9999)
    knob.setValue(77)

    knob._begin_type_edit("-")
    knob._type_edit.setText("-")
    knob._type_edit.returnPressed.emit()

    assert knob.value() == 77


def test_wheel_within_range_still_changes_the_value(qapp):
    # the fix above must not turn wheel scrolling into a no-op - only the
    # at-the-boundary case changes behaviour. Not asserting the exact step
    # size - that's QDial/QAbstractSlider's own base behaviour, not
    # something Knob.wheelEvent controls.
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(50)

    event = _wheel_event(120)
    knob.wheelEvent(event)

    assert knob.value() > 50
    assert event.isAccepted()
