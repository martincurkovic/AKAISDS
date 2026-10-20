"""Scrolling a page never changes a control it passes over: a spin box, combo, slider or knob answers the mouse wheel only after the user CLICKED it.

Until then a wheel tick is ignored by the control, so Qt hands it to the next widget up (the page's scroll area). A mouse press on a control arms it and it stays
armed, with an accent outline, until a press lands anywhere else (Ableton's way); one control is armed at a time. The outline is the `armed` dynamic property:
the style sheet's `[armed="true"]` rules, and Knob's own paint. Scrolling over an armed control changes it, so the outline is the cue that it will.

One application-level filter, installed once at start-up (`install`), covers every control without touching the places that create them. The filter
sees the wheel event before the control does and, for an unarmed control, offers it to the control's ancestors itself. Keyboard focus is NOT touched: typing into a spin
box still works when the mouse drifts away.

Scroll bars are left alone (they must always scroll), and so are the views that own the wheel themselves (waveforms, the loop preview).
"""

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractSlider, QAbstractSpinBox, QApplication, QComboBox, QScrollBar, QWidget

_GUARDED = (QAbstractSpinBox, QComboBox, QAbstractSlider)

_guard = None


def guarded_control(widget):
    """The control a click on `widget` belongs to (the widget itself, or the spin box / combo it is the inner part of), or None."""
    while widget is not None:
        if isinstance(widget, QScrollBar):
            return None  # (also the scroll bar inside a combo's drop-down list)
        if isinstance(widget, _GUARDED):
            return widget
        widget = widget.parentWidget()
    return None


class WheelGuard(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._armed = None

    def armed_control(self):
        if self._armed is not None and not shiboken6.isValid(self._armed):
            self._armed = None
        return self._armed

    def _arm(self, control):
        previous = self.armed_control()
        if control is previous:
            return
        self._armed = control
        if previous is not None:
            self._show(previous, False)
        if control is not None:
            self._show(control, True)

    @staticmethod
    def _show(control, armed):
        control.setProperty("armed", armed)
        style = control.style()
        style.unpolish(control)  # (a style sheet rule keyed on a dynamic property only re-applies after an unpolish/polish)
        style.polish(control)
        control.update()

    @staticmethod
    def _pass_to_parent(control, event):
        """Hand a wheel event the control won't use up the parent chain, as if the control weren't there, until a widget accepts it (the page's scroll area).

        Done by hand: ignoring the event in an application filter does not make Qt offer it to the parent, and an event sent with sendEvent() is delivered to its
        receiver only, so each ancestor gets its own copy."""
        global_position = event.globalPosition()
        widget = control.parentWidget()
        while widget is not None:
            forwarded = QWheelEvent(
                QPointF(widget.mapFromGlobal(global_position.toPoint())),
                global_position,
                event.pixelDelta(),
                event.angleDelta(),
                event.buttons(),
                event.modifiers(),
                event.phase(),
                event.inverted(),
                event.source(),
            )
            QApplication.sendEvent(widget, forwarded)
            if forwarded.isAccepted():
                event.accept()
                return
            if widget.isWindow():
                break
            widget = widget.parentWidget()
        event.ignore()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.Wheel:
            if isinstance(watched, _GUARDED) and not isinstance(watched, QScrollBar) and watched is not self.armed_control():
                self._pass_to_parent(watched, event)
                return True
            return False
        if kind == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
            if isinstance(watched, _GUARDED) and not isinstance(watched, QScrollBar):
                self._arm(watched)
            elif watched.childAt(event.position().toPoint()) is None:
                # only the widget the click LANDED on decides: one that ignores the press hands it to each parent in turn, and those visits must not disarm
                self._arm(guarded_control(watched))
            return False
        if kind == QEvent.Type.Hide and watched is self.armed_control():
            self._arm(None)  # (a tab switched away from, a window closed: it comes back unarmed)
        return False


def install(app=None):
    """Install the guard on the application (once; a second call returns the first). Returns it."""
    global _guard
    app = app or QApplication.instance()
    if _guard is None or not shiboken6.isValid(_guard):
        _guard = WheelGuard(app)
        app.installEventFilter(_guard)
    return _guard


def uninstall(app=None):
    global _guard
    app = app or QApplication.instance()
    if _guard is not None and shiboken6.isValid(_guard):
        guard, _guard = _guard, None
        app.removeEventFilter(guard)
        guard.armed_control() and guard._arm(None)
        guard.deleteLater()
    _guard = None
