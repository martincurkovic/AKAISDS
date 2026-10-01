# tests for ui/fine_slider.py's FineSlider - mirrors tests/test_knob.py's
# own coverage of Knob (this widget is a horizontal, non-inverted, non-
# hand-painted translation of the exact same drag/type-edit/reset logic -
# see FineSlider's own class docstring for why it's a standalone widget
# rather than sharing a base class with Knob).
#
# Uses a tiny duck-typed fake mouse event rather than a real QMouseEvent -
# FineSlider only ever calls .button()/.position().x()/.globalPosition()/
# .modifiers() on what it's given, same reasoning test_knob.py's own fake
# gives for Knob.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
import pytest

import ui.fine_slider as fine_slider_module
from ui.fine_slider import (
    FineSlider,
    _DRAG_SENSITIVITY_PX,
    _FINE_DRAG_DIVISOR,
    _FINE_DRAG_DIVISOR_MACOS,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeMouseEvent:
    def __init__(
        self,
        x,
        button=Qt.MouseButton.LeftButton,
        modifiers=Qt.KeyboardModifier.NoModifier,
        global_x=None,
    ):
        self._x = x
        self._button = button
        self._modifiers = modifiers
        self._global_x = x if global_x is None else global_x

    def button(self):
        return self._button

    def position(self):
        return QPointF(self._x, 0)

    def globalPosition(self):
        return QPointF(self._global_x, 0)

    def modifiers(self):
        return self._modifiers


def _drag(slider, start_x, end_x):
    slider.mousePressEvent(_FakeMouseEvent(start_x))
    slider.mouseMoveEvent(_FakeMouseEvent(end_x))


def test_dragging_right_increases_the_value(qapp):
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setValue(50)

    # right is the natural "more" direction for a horizontal slider - no
    # inversion needed, unlike Knob's own vertical (up=more) convention
    _drag(slider, start_x=100, end_x=100 + _DRAG_SENSITIVITY_PX)

    assert slider.value() == 100


def test_dragging_left_decreases_the_value(qapp):
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setValue(50)

    _drag(slider, start_x=100, end_x=100 - _DRAG_SENSITIVITY_PX)

    assert slider.value() == 0


def test_drag_is_proportional_to_the_configured_range(qapp):
    slider = FineSlider()
    slider.setRange(0, 30)
    slider.setValue(15)

    _drag(slider, start_x=100, end_x=100 + (_DRAG_SENSITIVITY_PX / 2))

    assert slider.value() == 30


def test_dragging_past_the_range_clamps_rather_than_wrapping(qapp):
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setValue(50)

    _drag(slider, start_x=100, end_x=100 + _DRAG_SENSITIVITY_PX * 10)
    assert slider.value() == 100

    _drag(slider, start_x=100, end_x=100 - _DRAG_SENSITIVITY_PX * 10)
    assert slider.value() == 0


def test_mouse_move_without_a_preceding_press_is_a_no_op(qapp):
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setValue(50)

    slider.mouseMoveEvent(_FakeMouseEvent(1000))

    assert slider.value() == 50


class _FakeCursor:
    # same reasoning as test_knob.py's own _FakeCursor - the real QCursor
    # reflects actual OS state, unpredictable under the offscreen QPA
    # platform these tests run under
    _pos = QPoint(0, 0)

    @classmethod
    def pos(cls):
        return cls._pos

    @classmethod
    def setPos(cls, point):
        cls._pos = point


def _drag_fine(slider, global_travel, *, anchor_x=1000):
    _FakeCursor._pos = QPoint(anchor_x, 0)
    slider.mousePressEvent(_FakeMouseEvent(0))
    shift = Qt.KeyboardModifier.ShiftModifier
    slider.mouseMoveEvent(_FakeMouseEvent(0, modifiers=shift, global_x=anchor_x))
    slider.mouseMoveEvent(
        _FakeMouseEvent(0, modifiers=shift, global_x=anchor_x + global_travel)
    )
    slider.mouseReleaseEvent(_FakeMouseEvent(0))


def test_fine_drag_needs_far_more_travel_for_the_same_change(qapp, monkeypatch):
    monkeypatch.setattr(fine_slider_module, "_MACOS", False)
    monkeypatch.setattr(fine_slider_module, "QCursor", _FakeCursor)
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setValue(50)

    _drag_fine(slider, global_travel=_DRAG_SENSITIVITY_PX)

    assert 50 < slider.value() < 70


def test_fine_drag_divisor_times_the_travel_matches_a_normal_drag(qapp, monkeypatch):
    monkeypatch.setattr(fine_slider_module, "_MACOS", False)
    monkeypatch.setattr(fine_slider_module, "QCursor", _FakeCursor)
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setValue(50)

    _drag_fine(slider, global_travel=_DRAG_SENSITIVITY_PX * _FINE_DRAG_DIVISOR)

    assert slider.value() == 100


def test_entering_fine_drag_hides_the_cursor_and_releasing_restores_it(
    qapp, monkeypatch
):
    monkeypatch.setattr(fine_slider_module, "_MACOS", False)
    monkeypatch.setattr(fine_slider_module, "QCursor", _FakeCursor)
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setValue(50)

    _FakeCursor._pos = QPoint(1000, 0)
    slider.mousePressEvent(_FakeMouseEvent(100))
    slider.mouseMoveEvent(
        _FakeMouseEvent(100, modifiers=Qt.KeyboardModifier.ShiftModifier, global_x=1000)
    )

    assert QApplication.overrideCursor() is not None
    assert QApplication.overrideCursor().shape() == Qt.CursorShape.BlankCursor

    slider.mouseReleaseEvent(_FakeMouseEvent(0))

    assert QApplication.overrideCursor() is None


def test_releasing_shift_mid_drag_exits_fine_mode(qapp, monkeypatch):
    monkeypatch.setattr(fine_slider_module, "_MACOS", False)
    monkeypatch.setattr(fine_slider_module, "QCursor", _FakeCursor)
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setValue(50)

    _FakeCursor._pos = QPoint(1000, 0)
    slider.mousePressEvent(_FakeMouseEvent(100))
    slider.mouseMoveEvent(
        _FakeMouseEvent(100, modifiers=Qt.KeyboardModifier.ShiftModifier, global_x=1000)
    )
    assert slider._fine_active is True

    slider.mouseMoveEvent(_FakeMouseEvent(110))

    assert slider._fine_active is False
    assert QApplication.overrideCursor() is None


def test_hiding_the_slider_mid_fine_drag_restores_the_cursor(qapp, monkeypatch):
    monkeypatch.setattr(fine_slider_module, "_MACOS", False)
    monkeypatch.setattr(fine_slider_module, "QCursor", _FakeCursor)
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setValue(50)
    slider.show()

    _FakeCursor._pos = QPoint(1000, 0)
    slider.mousePressEvent(_FakeMouseEvent(100))
    slider.mouseMoveEvent(
        _FakeMouseEvent(100, modifiers=Qt.KeyboardModifier.ShiftModifier, global_x=1000)
    )
    assert QApplication.overrideCursor() is not None

    slider.hide()

    assert QApplication.overrideCursor() is None


def test_macos_fine_drag_needs_far_more_travel_for_the_same_change(qapp, monkeypatch):
    monkeypatch.setattr(fine_slider_module, "_MACOS", True)
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setValue(50)

    slider.mousePressEvent(_FakeMouseEvent(1000))
    shift = Qt.KeyboardModifier.ShiftModifier
    slider.mouseMoveEvent(_FakeMouseEvent(1000, modifiers=shift))
    slider.mouseMoveEvent(_FakeMouseEvent(1000 + _DRAG_SENSITIVITY_PX, modifiers=shift))
    slider.mouseReleaseEvent(_FakeMouseEvent(0))

    assert 50 < slider.value() < 70


def test_macos_fine_drag_divisor_times_the_travel_matches_a_normal_drag(
    qapp, monkeypatch
):
    monkeypatch.setattr(fine_slider_module, "_MACOS", True)
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setValue(50)

    slider.mousePressEvent(_FakeMouseEvent(1000))
    shift = Qt.KeyboardModifier.ShiftModifier
    slider.mouseMoveEvent(_FakeMouseEvent(1000, modifiers=shift))
    slider.mouseMoveEvent(
        _FakeMouseEvent(
            1000 + _DRAG_SENSITIVITY_PX * _FINE_DRAG_DIVISOR_MACOS, modifiers=shift
        )
    )
    slider.mouseReleaseEvent(_FakeMouseEvent(0))

    assert slider.value() == 100


def test_macos_fine_drag_does_not_hide_or_warp_the_cursor(qapp, monkeypatch):
    monkeypatch.setattr(fine_slider_module, "_MACOS", True)
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setValue(50)

    slider.mousePressEvent(_FakeMouseEvent(100))
    slider.mouseMoveEvent(
        _FakeMouseEvent(100, modifiers=Qt.KeyboardModifier.ShiftModifier)
    )

    assert QApplication.overrideCursor() is None

    slider.mouseReleaseEvent(_FakeMouseEvent(0))

    assert QApplication.overrideCursor() is None


def test_default_value_falls_back_to_the_range_midpoint(qapp):
    slider = FineSlider()
    slider.setRange(0, 100)

    assert slider.defaultValue() == 50


def test_default_value_uses_an_explicitly_set_value_instead_of_the_midpoint(qapp):
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setDefaultValue(0)

    assert slider.defaultValue() == 0


def test_double_click_resets_to_the_default_value(qapp):
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setValue(70)
    slider.setDefaultValue(0)

    slider.mouseDoubleClickEvent(_FakeMouseEvent(0))

    assert slider.value() == 0


class _FakeKeyEvent:
    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text


def test_typing_a_digit_opens_the_type_edit_box(qapp):
    slider = FineSlider()
    slider.setRange(0, 100)

    slider.keyPressEvent(_FakeKeyEvent("7"))

    assert slider._type_edit is not None
    assert slider._type_edit.text() == "7"
    slider._type_edit.close()


def test_minus_only_opens_the_type_edit_box_on_a_signed_range(qapp):
    real_event = QKeyEvent(
        QKeyEvent.Type.KeyPress, Qt.Key.Key_Minus, Qt.KeyboardModifier.NoModifier, "-"
    )
    unsigned = FineSlider()
    unsigned.setRange(0, 100)
    unsigned.keyPressEvent(real_event)
    assert unsigned._type_edit is None


def test_finishing_type_edit_commits_the_typed_value(qapp):
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setValue(0)

    slider.keyPressEvent(_FakeKeyEvent("8"))
    edit = slider._type_edit
    edit.setText("85")
    slider._finish_type_edit(edit, cancelled=False)

    assert slider.value() == 85
    assert slider._type_edit is None


def test_cancelling_type_edit_leaves_the_value_unchanged(qapp):
    slider = FineSlider()
    slider.setRange(0, 100)
    slider.setValue(42)

    slider.keyPressEvent(_FakeKeyEvent("9"))
    edit = slider._type_edit
    slider._finish_type_edit(edit, cancelled=True)

    assert slider.value() == 42
