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

import math

import ui.knob as knob_module
from ui.knob import (
    Knob,
    LogKnob,
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
        y,
        button=Qt.MouseButton.LeftButton,
        modifiers=Qt.KeyboardModifier.NoModifier,
        global_y=None,
    ):
        self._y = y
        self._button = button
        self._modifiers = modifiers
        # only meaningful for fine-drag events (see _drag_fine below) -
        # real global screen position, distinct from the widget-local one
        self._global_y = y if global_y is None else global_y

    def button(self):
        return self._button

    def position(self):
        return QPointF(0, self._y)

    def globalPosition(self):
        return QPointF(0, self._global_y)

    def modifiers(self):
        return self._modifiers


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


class _FakeCursor:
    # stands in for QCursor for fine-drag tests - the real one reflects
    # actual OS cursor state, which is unpredictable (and warping it is a
    # real side effect) under the offscreen QPA platform these tests run
    # under. Knob only ever calls .pos()/.setPos() on it.
    _pos = QPoint(0, 0)

    @classmethod
    def pos(cls):
        return cls._pos

    @classmethod
    def setPos(cls, point):
        cls._pos = point


def _drag_fine(knob, global_travel, *, anchor_y=1000):
    # mirrors _drag above, but holding Shift and driving the (faked) global
    # cursor position - fine mode reads global position (see mouseMoveEvent),
    # not the widget-local one, so the warp-back-to-anchor trick works
    # regardless of where the knob itself sits on screen. Widget-local y is
    # irrelevant here and left at a fixed dummy value throughout.
    _FakeCursor._pos = QPoint(0, anchor_y)
    knob.mousePressEvent(_FakeMouseEvent(0))
    shift = Qt.KeyboardModifier.ShiftModifier
    # first Shift-held move only ENTERS fine mode - its anchor is set from
    # (faked) QCursor.pos() inside _enter_fine_drag, so feeding this same
    # event's own global position back in as the anchor's starting point
    # keeps that first move a genuine no-op rather than an unpredictable
    # jump, matching what real hardware delivers (the OS cursor hasn't
    # actually moved anywhere yet the instant Shift goes down)
    knob.mouseMoveEvent(_FakeMouseEvent(0, modifiers=shift, global_y=anchor_y))
    knob.mouseMoveEvent(
        _FakeMouseEvent(0, modifiers=shift, global_y=anchor_y - global_travel)
    )
    # always finish the drag - leaving fine mode "active" would leave the
    # override cursor pushed on QApplication's shared stack forever,
    # bleeding into every later test in this (session-scoped qapp) file
    knob.mouseReleaseEvent(_FakeMouseEvent(0))


def test_fine_drag_needs_far_more_travel_for_the_same_change(qapp, monkeypatch):
    # forces the warp-based (non-macOS) code path regardless of the host
    # platform these tests actually run on - see the _MACOS-forced tests
    # below for the macOS no-warp path's own coverage
    monkeypatch.setattr(knob_module, "_MACOS", False)
    monkeypatch.setattr(knob_module, "QCursor", _FakeCursor)
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(50)

    # the same travel that covers the WHOLE range in normal mode (see
    # test_dragging_upward_increases_the_value) only covers ~1/_FINE_DRAG_
    # DIVISOR of it here - not "the whole range", not "barely moved"
    _drag_fine(knob, global_travel=_DRAG_SENSITIVITY_PX)

    assert 50 < knob.value() < 70


def test_fine_drag_divisor_times_the_travel_matches_a_normal_drag(qapp, monkeypatch):
    monkeypatch.setattr(knob_module, "_MACOS", False)
    monkeypatch.setattr(knob_module, "QCursor", _FakeCursor)
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(50)

    _drag_fine(knob, global_travel=_DRAG_SENSITIVITY_PX * _FINE_DRAG_DIVISOR)

    assert knob.value() == 100  # same distance a normal drag covers the whole range in


def test_entering_fine_drag_hides_the_cursor_and_releasing_restores_it(qapp, monkeypatch):
    monkeypatch.setattr(knob_module, "_MACOS", False)
    monkeypatch.setattr(knob_module, "QCursor", _FakeCursor)
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(50)

    _FakeCursor._pos = QPoint(0, 1000)
    knob.mousePressEvent(_FakeMouseEvent(100))
    knob.mouseMoveEvent(
        _FakeMouseEvent(
            100, modifiers=Qt.KeyboardModifier.ShiftModifier, global_y=1000
        )
    )

    assert QApplication.overrideCursor() is not None
    assert QApplication.overrideCursor().shape() == Qt.CursorShape.BlankCursor

    knob.mouseReleaseEvent(_FakeMouseEvent(0))

    assert QApplication.overrideCursor() is None


def test_releasing_shift_mid_drag_exits_fine_mode(qapp, monkeypatch):
    monkeypatch.setattr(knob_module, "_MACOS", False)
    monkeypatch.setattr(knob_module, "QCursor", _FakeCursor)
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(50)

    _FakeCursor._pos = QPoint(0, 1000)
    knob.mousePressEvent(_FakeMouseEvent(100))
    knob.mouseMoveEvent(
        _FakeMouseEvent(
            100, modifiers=Qt.KeyboardModifier.ShiftModifier, global_y=1000
        )
    )
    assert knob._fine_active is True

    # Shift released mid-drag - back to normal (non-warped) dragging
    knob.mouseMoveEvent(_FakeMouseEvent(90))

    assert knob._fine_active is False
    assert QApplication.overrideCursor() is None


def test_hiding_the_knob_mid_fine_drag_restores_the_cursor(qapp, monkeypatch):
    monkeypatch.setattr(knob_module, "_MACOS", False)
    monkeypatch.setattr(knob_module, "QCursor", _FakeCursor)
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(50)
    knob.show()

    _FakeCursor._pos = QPoint(0, 1000)
    knob.mousePressEvent(_FakeMouseEvent(100))
    knob.mouseMoveEvent(
        _FakeMouseEvent(
            100, modifiers=Qt.KeyboardModifier.ShiftModifier, global_y=1000
        )
    )
    assert QApplication.overrideCursor() is not None

    knob.hide()

    assert QApplication.overrideCursor() is None


def test_macos_fine_drag_needs_far_more_travel_for_the_same_change(qapp, monkeypatch):
    # macOS skips the warp-the-cursor-back trick entirely (see
    # ui/knob.py's own _MACOS comment) - forced here regardless of the
    # host platform these tests actually run on, same reasoning as the
    # warp-mode tests above forcing _MACOS False
    monkeypatch.setattr(knob_module, "_MACOS", True)
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(50)

    knob.mousePressEvent(_FakeMouseEvent(1000))
    shift = Qt.KeyboardModifier.ShiftModifier
    knob.mouseMoveEvent(_FakeMouseEvent(1000, modifiers=shift))
    knob.mouseMoveEvent(_FakeMouseEvent(1000 - _DRAG_SENSITIVITY_PX, modifiers=shift))
    knob.mouseReleaseEvent(_FakeMouseEvent(0))

    assert 50 < knob.value() < 70


def test_macos_fine_drag_divisor_times_the_travel_matches_a_normal_drag(qapp, monkeypatch):
    monkeypatch.setattr(knob_module, "_MACOS", True)
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(50)

    knob.mousePressEvent(_FakeMouseEvent(1000))
    shift = Qt.KeyboardModifier.ShiftModifier
    knob.mouseMoveEvent(_FakeMouseEvent(1000, modifiers=shift))
    knob.mouseMoveEvent(
        _FakeMouseEvent(1000 - _DRAG_SENSITIVITY_PX * _FINE_DRAG_DIVISOR_MACOS, modifiers=shift)
    )
    knob.mouseReleaseEvent(_FakeMouseEvent(0))

    assert knob.value() == 100


def test_macos_fine_drag_does_not_hide_or_warp_the_cursor(qapp, monkeypatch):
    monkeypatch.setattr(knob_module, "_MACOS", True)
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(50)

    knob.mousePressEvent(_FakeMouseEvent(100))
    knob.mouseMoveEvent(
        _FakeMouseEvent(100, modifiers=Qt.KeyboardModifier.ShiftModifier)
    )

    assert QApplication.overrideCursor() is None

    knob.mouseReleaseEvent(_FakeMouseEvent(0))

    assert QApplication.overrideCursor() is None


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


# --- LogKnob: logarithmic drag/paint, same value()/setValue() units --------


def test_log_knob_fraction_to_value_and_back_are_inverses(qapp):
    knob = LogKnob()
    knob.setRange(20, 20000)
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        value = knob._fraction_to_value(fraction)
        assert knob._value_to_fraction(value) == pytest.approx(fraction, abs=1e-9)


def test_log_knob_midpoint_fraction_is_the_geometric_mean(qapp):
    # halfway in LOG space is sqrt(min*max), not (min+max)/2 - the whole
    # point of a log knob: equal ROTATION covers equal RATIO
    knob = LogKnob()
    knob.setRange(20, 20000)
    assert knob._fraction_to_value(0.5) == pytest.approx(math.sqrt(20 * 20000))


def test_log_knob_endpoints_match_min_and_max(qapp):
    knob = LogKnob()
    knob.setRange(20, 20000)
    assert knob._fraction_to_value(0.0) == pytest.approx(20)
    assert knob._fraction_to_value(1.0) == pytest.approx(20000)


def test_log_knob_full_drag_from_minimum_reaches_maximum(qapp):
    knob = LogKnob()
    knob.setRange(20, 20000)
    knob.setValue(20)

    _drag(knob, start_y=100, end_y=100 - _DRAG_SENSITIVITY_PX)

    assert knob.value() == 20000


def test_log_knob_half_drag_from_minimum_lands_on_the_geometric_mean(qapp):
    knob = LogKnob()
    knob.setRange(20, 20000)
    knob.setValue(20)

    _drag(knob, start_y=100, end_y=100 - (_DRAG_SENSITIVITY_PX / 2))

    assert knob.value() == round(math.sqrt(20 * 20000))


def test_log_knob_dragging_past_the_range_clamps_rather_than_wrapping(qapp):
    knob = LogKnob()
    knob.setRange(20, 20000)
    knob.setValue(1000)

    _drag(knob, start_y=100, end_y=100 - _DRAG_SENSITIVITY_PX * 10)
    assert knob.value() == 20000

    _drag(knob, start_y=100, end_y=100 + _DRAG_SENSITIVITY_PX * 10)
    assert knob.value() == 20


def test_log_knob_rejects_a_non_positive_minimum(qapp):
    knob = LogKnob()
    knob.setRange(0, 20000)
    with pytest.raises(ValueError):
        knob._value_to_fraction(100)
    with pytest.raises(ValueError):
        knob._fraction_to_value(0.5)


def test_log_knob_value_and_type_entry_stay_in_plain_linear_units(qapp):
    # value()/setValue()/click-to-type are all UNCHANGED by LogKnob - only
    # drag/paint angle are logarithmic (see the class's own docstring) -
    # typing "300" must set exactly 300, not a log-transformed number
    knob = LogKnob()
    knob.setRange(20, 20000)
    knob._begin_type_edit("3")
    knob._type_edit.setText("300")
    knob._type_edit.returnPressed.emit()
    assert knob.value() == 300


# --- disabled-state painting -------------------------------------------------


def test_disabled_knob_paints_without_error(qapp):
    # regression guard for the disabled/greyed-out paint branch (palette
    # key typos etc wouldn't show up any other way, since nothing else
    # calls paintEvent directly)
    knob = Knob()
    knob.setRange(0, 100)
    knob.setValue(50)
    knob.setEnabled(False)
    knob.resize(56, 56)
    pixmap = knob.grab()
    assert not pixmap.isNull()


def test_disabled_log_knob_paints_without_error(qapp):
    knob = LogKnob()
    knob.setRange(20, 20000)
    knob.setValue(1000)
    knob.setEnabled(False)
    knob.resize(56, 56)
    pixmap = knob.grab()
    assert not pixmap.isNull()


# --- bipolar knobs (the Yamaha editor's pan / offset knobs) -----------------------------------------------------------


def test_the_value_arc_starts_at_the_minimum_by_default_and_at_zero_when_bipolar():
    from ui.knob import START_ANGLE_DEG, SWEEP_DEG, arc_start_and_span

    assert arc_start_and_span(0.25) == (START_ANGLE_DEG, -SWEEP_DEG * 0.25)  # unchanged: from the minimum
    start, span = arc_start_and_span(0.5, 0.5)  # a bipolar knob at its zero draws nothing
    assert span == 0 and start == START_ANGLE_DEG - SWEEP_DEG * 0.5
    start, span = arc_start_and_span(0.75, 0.5)  # above zero: clockwise from zero
    assert span < 0 and start == START_ANGLE_DEG - SWEEP_DEG * 0.5
    start, span = arc_start_and_span(0.25, 0.5)  # below zero: counter-clockwise from zero
    assert span > 0


def test_only_a_range_spanning_zero_can_be_bipolar(qapp):
    from ui.knob import Knob

    k = Knob()
    k.setRange(-63, 63)
    assert k._origin_fraction() == 0.0  # off until asked for
    k.setBipolar(True)
    assert abs(k._origin_fraction() - 0.5) < 1e-9
    k.setRange(0, 127)  # asked for, but zero is the minimum: nothing to centre
    assert k._origin_fraction() == 0.0
    k.setRange(-24, 96)
    assert abs(k._origin_fraction() - 24 / 120) < 1e-9
