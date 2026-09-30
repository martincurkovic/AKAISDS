import sys
from PySide6.QtWidgets import QApplication, QSlider
from PySide6.QtGui import QCursor, QIntValidator
from PySide6.QtCore import Qt

from ui.knob import _TYPE_EDIT_SIZE, _TypeEdit

# same convention/values as Knob's own (ui/knob.py) - kept as independent
# constants here rather than shared imports for the actual NUMBERS (Knob
# itself duplicates these from WaveformView for the same reason - see its
# own comment), but _TypeEdit itself IS imported directly rather than
# re-implemented: it's already fully generic (only needs the owning
# widget's own _finish_type_edit(edit, cancelled) method), so duplicating
# it would just be the same popup-positioning/Escape-handling logic typed
# out twice with nothing to actually differ.
_DRAG_SENSITIVITY_PX = 150
_FINE_DRAG_DIVISOR = 8
_MACOS = sys.platform == "darwin"
_FINE_DRAG_DIVISOR_MACOS = 12


class FineSlider(QSlider):
    """A horizontal QSlider with the same "nice-to-haves" every Knob (ui/
    knob.py) already has in this app - double-click to reset to a default
    value, Shift-held drag for finer control (with the same macOS-specific
    no-cursor-warp tweak Knob/WaveformView already use), and click-then-
    type exact entry. Built for the Slice Editor's live Sensitivity slider
    (ui/slice_editor_window.py) but has nothing slice-specific in it.

    Unlike Knob, this does NOT hand-paint itself - QSlider's own native
    groove/handle rendering already responds to this app's own QSS
    styling (style.qss.template's own QSlider rules), so there's nothing
    a custom paintEvent would need to do that plain Qt styling can't
    already (a QDial, which Knob wraps, doesn't skin nearly as well,
    which is the actual reason Knob paints itself by hand instead).

    Deliberately NOT a Knob subclass, or refactored to share a common base
    with it, despite the overlap - Knob is used throughout the Program
    Editor and is already covered by its own test suite; duplicating the
    drag/type-edit logic here (translated from a vertical, inverted axis
    to a plain horizontal one, and without the paint override) keeps this
    change's blast radius to exactly one new, independently testable
    widget, at the cost of some duplication with Knob's own mouseMoveEvent.

    A plain click-and-drag behaves like an ordinary QSlider (relative to
    wherever the drag STARTED, not jumping straight to the clicked
    position) - matching every Knob elsewhere in this app rather than a
    default QSlider's own "click the groove to jump there" convention, for
    a consistent feel across every draggable control in this codebase.
    """

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._drag_anchor_x = None
        self._drag_fraction = None  # float accumulator, in [0, 1] fraction space
        self._default_value = None
        self._type_edit = None
        # fine (Shift-held) mode warps the OS cursor back to a fixed point
        # every move event - see _enter_fine_drag/mouseMoveEvent, same
        # mechanism/reasoning as Knob's own and WaveformView's own
        # fine-drag marker editing
        self._fine_active = False
        self._warp_anchor_global = None
        # safety net alongside hideEvent's own below - see Knob's own
        # identical comment on why this exists
        QApplication.instance().applicationStateChanged.connect(
            self._on_application_state_changed
        )

    def _value_to_fraction(self, value):
        value_range = self.maximum() - self.minimum()
        if not value_range:
            return 0.0
        return (value - self.minimum()) / value_range

    def _fraction_to_value(self, fraction):
        return self.minimum() + fraction * (self.maximum() - self.minimum())

    def defaultValue(self):
        if self._default_value is not None:
            return self._default_value
        return (self.minimum() + self.maximum()) // 2

    def setDefaultValue(self, value):
        self._default_value = value

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_anchor_x = event.position().x()
            self._drag_fraction = self._value_to_fraction(self.value())

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # the release Qt sends right after this event does the actual
            # commit (sliderReleased), same as an ordinary drag - matches
            # Knob's own mouseDoubleClickEvent exactly
            self.setValue(self.defaultValue())

    def keyPressEvent(self, event):
        # click-to-type, Ableton-style - see Knob's own identical comment
        text = event.text()
        if self._type_edit is None and (
            text.isdigit() or (text == "-" and self.minimum() < 0)
        ):
            self._begin_type_edit(text)
            return
        super().keyPressEvent(event)

    def _begin_type_edit(self, initial_text):
        if self._type_edit is not None:
            return
        edit = _TypeEdit(self)
        edit.setValidator(QIntValidator(self.minimum(), self.maximum(), edit))
        edit.setText(initial_text)
        edit.returnPressed.connect(edit.close)
        width, height = _TYPE_EDIT_SIZE
        edit.resize(width, height)
        center = self.mapToGlobal(self.rect().center())
        edit.move(center.x() - width // 2, center.y() - height // 2)
        self._type_edit = edit
        edit.show()
        edit.setFocus(Qt.FocusReason.MouseFocusReason)

    def _finish_type_edit(self, edit, cancelled):
        if self._type_edit is not edit:
            return
        self._type_edit = None
        if not cancelled:
            text = edit.text().strip()
            if text not in ("", "-"):
                try:
                    value = max(self.minimum(), min(self.maximum(), int(text)))
                except ValueError:
                    value = None
                if value is not None:
                    self.setValue(value)
                    # typing a value is a discrete, deliberate commit, same
                    # as releasing a drag
                    self.sliderReleased.emit()
        edit.deleteLater()

    def mouseMoveEvent(self, event):
        if self._drag_anchor_x is None:
            return
        fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if fine and not self._fine_active:
            self._enter_fine_drag()
        elif not fine and self._fine_active:
            self._exit_fine_drag()

        if self._fine_active and _MACOS:
            # no warp on macOS - see _MACOS's own comment above
            x = event.position().x()
            dx = (x - self._drag_anchor_x) / _FINE_DRAG_DIVISOR_MACOS
            self._drag_anchor_x = x
        elif self._fine_active:
            current = event.globalPosition()
            anchor = self._warp_anchor_global
            if (round(current.x()), round(current.y())) == (
                round(anchor.x()),
                round(anchor.y()),
            ):
                return
            dx = (current.x() - anchor.x()) / _FINE_DRAG_DIVISOR
            QCursor.setPos(anchor)
        else:
            x = event.position().x()
            dx = x - self._drag_anchor_x
            self._drag_anchor_x = x

        # sensitivity is in FRACTION units (a full _DRAG_SENSITIVITY_PX drag
        # covers the whole 0..1 slider range), not raw value units - same
        # reasoning as Knob's own identical comment
        sensitivity = 1.0 / _DRAG_SENSITIVITY_PX
        self._drag_fraction += dx * sensitivity
        new_value = int(round(self._fraction_to_value(self._drag_fraction)))
        clamped_value = max(self.minimum(), min(self.maximum(), new_value))
        if clamped_value != new_value:
            self._drag_fraction = self._value_to_fraction(clamped_value)
        self.setValue(clamped_value)

    def _enter_fine_drag(self):
        self._fine_active = True
        if _MACOS:
            return
        self._warp_anchor_global = QCursor.pos()
        QApplication.setOverrideCursor(Qt.CursorShape.BlankCursor)

    def _exit_fine_drag(self):
        self._fine_active = False
        if _MACOS:
            return
        self._warp_anchor_global = None
        QApplication.restoreOverrideCursor()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._fine_active:
                self._exit_fine_drag()
            self._drag_anchor_x = None
            self._drag_fraction = None
            self.sliderReleased.emit()

    def hideEvent(self, event):
        if self._fine_active:
            self._exit_fine_drag()
        super().hideEvent(event)

    def _on_application_state_changed(self, state):
        if state != Qt.ApplicationState.ApplicationActive and self._fine_active:
            self._exit_fine_drag()
