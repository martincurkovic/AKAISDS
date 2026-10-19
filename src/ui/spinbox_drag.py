"""Ableton-style spin boxes, for every spin box in the app: drag one up or down to change it like a knob, or click it once and type to replace the whole value.

A click selects the whole text, so what is typed overwrites it (Enter applies, Esc leaves), just as typing into a knob does. Pressing and moving the mouse up or down changes the value
(Shift for fine control): the cursor becomes a vertical arrow while it does, and the box is left with its text selected, ready to be typed over. A click on a box that is
being edited (its text cursor placed, nothing selected) behaves as usual, so text can still be corrected in place. The little arrows, the wheel (see wheel_guard), the arrow keys and Tab are untouched.

One application-level filter, installed once at start-up (`install`), like wheel_guard: it covers every QSpinBox, QDoubleSpinBox and subclass (the note spin boxes included) without
touching the places that create them. It never consumes a press (so a click arms the wheel guard and focuses the box as before); it consumes the moves and the release of a drag only.

How far a drag moves a value: the whole range takes `FULL_RANGE_PX` of travel, but never less than `MIN_PX_PER_STEP` per step (a small range does not fly) and never more than
`MAX_STEPS_PER_PX` steps per pixel; Shift divides that by `FINE_DIVISOR`.
"""

import shiboken6
from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QLineEdit

DRAG_THRESHOLD_PX = 3
FULL_RANGE_PX = 200
MIN_PX_PER_STEP = 3
MAX_STEPS_PER_PX = 200
FINE_DIVISOR = 8

_filter = None


def steps_per_pixel(spin, fine=False):
    """How many single-steps one pixel of vertical drag is worth for `spin`."""
    step = spin.singleStep() or 1
    span_steps = max(1.0, (spin.maximum() - spin.minimum()) / abs(step))
    rate = min(MAX_STEPS_PER_PX, max(1.0 / MIN_PX_PER_STEP, span_steps / FULL_RANGE_PX))
    return rate / FINE_DIVISOR if fine else rate


def spin_of(widget):
    """The spin box `widget` is the inner text box of, or None."""
    parent = widget.parentWidget() if isinstance(widget, QLineEdit) else None
    if isinstance(parent, QAbstractSpinBox) and parent.lineEdit() is widget:
        return parent
    return None


class _Press:
    def __init__(self, spin, line_edit, y):
        self.spin = spin
        self.line_edit = line_edit
        self.start_y = y
        self.last_y = y
        self.pending = 0.0  # fractional steps not yet applied (a slow drag adds up)
        self.dragging = False


class SpinBoxDrag(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._press = None
        self._cursor_set = False
        self._owned = None  # the spin box this filter last selected for typing: while it is focused with nothing selected, the user is editing its text

    # -- state --------------------------------------------------------------------------------------------------------

    def _end(self, select=True):
        press, self._press = self._press, None
        if self._cursor_set:
            QApplication.restoreOverrideCursor()
            self._cursor_set = False
        if press is not None and select and shiboken6.isValid(press.spin):
            self._owned = press.spin
            # after the line edit has handled the release (it can move the text cursor): select everything, so the next thing typed replaces the value
            QTimer.singleShot(0, lambda spin=press.spin: shiboken6.isValid(spin) and spin.selectAll())

    def _drag_to(self, press, y, fine):
        press.pending += (press.last_y - y) * steps_per_pixel(press.spin, fine)  # up is positive
        press.last_y = y
        steps = int(press.pending)  # toward zero
        if steps:
            press.pending -= steps
            press.spin.stepBy(steps)

    # -- the filter ---------------------------------------------------------------------------------------------------

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            if isinstance(watched, QLineEdit) and event.button() == Qt.MouseButton.LeftButton:
                spin = spin_of(watched)
                self._end(select=False)  # (a release that never arrived)
                if spin is not None and spin.isEnabled() and not watched.isReadOnly():
                    editing = spin is self._owned and watched.hasFocus() and not watched.hasSelectedText()
                    if not editing:
                        self._press = _Press(spin, watched, event.globalPosition().y())
            return False
        press = self._press
        if press is None or watched is not press.line_edit:
            return False
        if not shiboken6.isValid(press.spin):
            self._end(select=False)
            return False
        if kind == QEvent.Type.MouseMove:
            if not event.buttons() & Qt.MouseButton.LeftButton:
                self._end()  # the release was lost (a drag that ended outside the window)
                return False
            y = event.globalPosition().y()
            if not press.dragging:
                if abs(y - press.start_y) < DRAG_THRESHOLD_PX:
                    return False  # still a click
                press.dragging = True
                press.last_y = y  # (no jump when the drag starts)
                QApplication.setOverrideCursor(Qt.CursorShape.SizeVerCursor)
                self._cursor_set = True
                return True
            self._drag_to(press, y, bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            return True
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            dragged = press.dragging
            self._end()
            return dragged  # a drag's release is ours; a click's goes to the line edit as usual
        if kind == QEvent.Type.Hide:
            self._end(select=False)
        return False


def install(app=None):
    """Install the filter on the application (once; a second call returns the first). Returns it."""
    global _filter
    app = app or QApplication.instance()
    if _filter is None or not shiboken6.isValid(_filter):
        _filter = SpinBoxDrag(app)
        app.installEventFilter(_filter)
    return _filter


def uninstall(app=None):
    global _filter
    app = app or QApplication.instance()
    if _filter is not None and shiboken6.isValid(_filter):
        flt, _filter = _filter, None
        flt._end(select=False)
        app.removeEventFilter(flt)
        flt.deleteLater()
    _filter = None
