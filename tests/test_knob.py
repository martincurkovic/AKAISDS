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
from PySide6.QtCore import QPointF, Qt
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
