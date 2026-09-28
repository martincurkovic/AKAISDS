import math
from PySide6.QtWidgets import QApplication, QDial, QLineEdit
from PySide6.QtGui import QCursor, QPainter, QPen, QColor, QIntValidator
from PySide6.QtCore import Qt, QPointF
from ui import theme

START_ANGLE_DEG = 240
SWEEP_DEG = 300
_RING_WIDTH = 4
_POINTER_WIDTH = 3

_DRAG_SENSITIVITY_PX = 150
# how much slower a Shift-held drag moves - same convention/value as
# WaveformView's own _FINE_DRAG_DIVISOR (ui/waveform_view.py), kept as an
# independent constant here rather than a shared import so this widget
# doesn't need to depend on that module for one number
_FINE_DRAG_DIVISOR = 8

_TYPE_EDIT_SIZE = (52, 22)


class _TypeEdit(QLineEdit):
    """The floating text box a Knob opens for click-then-type entry (see
    Knob._begin_type_edit). A real top-level window (Qt.WindowType.Popup),
    not a child of the knob - a child widget is clipped to its parent's own
    rect, and the smallest knobs on this page (28x28) are far too small to
    hold a legible text box themselves. Popup gets two behaviours for free
    that a plain child widget wouldn't: it grabs the keyboard immediately,
    and clicking anywhere outside it closes it automatically - which is
    exactly "click away to commit" (see hideEvent below).
    """

    def __init__(self, knob):
        super().__init__()
        self._knob = knob
        self._cancelled = False
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancelled = True
            self.close()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event):
        super().hideEvent(event)
        # closing a Popup (Escape, Enter via returnPressed->close, or Qt's
        # own click-outside dismissal) always ends up here - one path for
        # every way this can end, rather than duplicating the commit/cancel
        # decision at each trigger
        self._knob._finish_type_edit(self, self._cancelled)


class Knob(QDial):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEnabled(False)  # read-only for now
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._drag_anchor_y = None
        self._drag_value = None  # float accumulator - see mouseMoveEvent
        self._default_value = None
        self._type_edit = None
        # fine (Shift-held) mode warps the OS cursor back to a fixed point
        # every move event - see _enter_fine_drag/mouseMoveEvent, same
        # mechanism/reasoning as WaveformView's own fine-drag marker editing
        self._fine_active = False
        self._warp_anchor_global = None

    def defaultValue(self):
        if self._default_value is not None:
            return self._default_value
        return (self.minimum() + self.maximum()) // 2

    def setDefaultValue(self, value):
        self._default_value = value

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_anchor_y = event.position().y()
            self._drag_value = float(self.value())

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # the release that Qt sends right after this event does the
            # actual commit (sliderReleased), same as an ordinary drag
            self.setValue(self.defaultValue())

    def keyPressEvent(self, event):
        # click-to-type, Ableton-style: clicking a knob just focuses it (no
        # visual change) - typing a digit (or '-' on a signed range) right
        # after is what actually opens the entry box. Anything else falls
        # through to QDial's own key handling (arrow keys/PageUp/Home etc).
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
                    # as releasing a drag - callers wire their write-commit
                    # (e.g. _flush_write) to this signal, not to every
                    # valueChanged tick
                    self.sliderReleased.emit()
        edit.deleteLater()

    def mouseMoveEvent(self, event):
        if self._drag_anchor_y is None:
            return
        fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if fine and not self._fine_active:
            self._enter_fine_drag()
        elif not fine and self._fine_active:
            self._exit_fine_drag()

        if self._fine_active:
            # the cursor gets warped back to _warp_anchor_global at the end
            # of this branch, so THIS event's global position is already
            # the delta since the last one - not since drag start. The warp
            # itself generates its own synthetic move event on most
            # platforms, landing exactly on the anchor - skip it rather
            # than read it as a zero-length real move (harmless either way
            # since dy would be 0, but skips a redundant setValue/repaint)
            current = event.globalPosition()
            anchor = self._warp_anchor_global
            if (round(current.x()), round(current.y())) == (
                round(anchor.x()),
                round(anchor.y()),
            ):
                return
            dy = (anchor.y() - current.y()) / _FINE_DRAG_DIVISOR
            QCursor.setPos(anchor)  # QCursor.pos() is already a QPoint
        else:
            y = event.position().y()
            dy = self._drag_anchor_y - y
            self._drag_anchor_y = y

        value_range = self.maximum() - self.minimum()
        sensitivity = value_range / _DRAG_SENSITIVITY_PX
        # a float accumulator (_drag_value) carries the sub-unit remainder
        # between events instead of rounding it away each time, same reason
        # WaveformView's own marker dragging needs one - see its comment
        self._drag_value += dy * sensitivity
        new_value = int(round(self._drag_value))
        clamped_value = max(self.minimum(), min(self.maximum(), new_value))
        # only resync the accumulator when the value itself just got
        # clamped to the knob's own range - never unconditionally (see
        # WaveformView's identical comment on why: it would otherwise
        # silently throw away the sub-unit remainder on every move event,
        # making a slow, careful drag barely move at all)
        if clamped_value != new_value:
            self._drag_value = clamped_value
        self.setValue(clamped_value)

    def _enter_fine_drag(self):
        # Shift held mid-drag: the same physical mouse movement now covers
        # only 1/_FINE_DRAG_DIVISOR of the distance, for precise placement -
        # which means crawling across the same screen-height of physical
        # travel many times over for a large move. Hiding the cursor and
        # warping it back to a fixed point every event (see mouseMoveEvent)
        # is the same trick WaveformView's own fine-drag marker editing
        # uses, so the mouse never runs out of screen and stalls mid-drag.
        self._fine_active = True
        self._warp_anchor_global = QCursor.pos()
        QApplication.setOverrideCursor(Qt.CursorShape.BlankCursor)

    def _exit_fine_drag(self):
        # Shift released mid-drag, or the drag ending - always paired with
        # _enter_fine_drag's setOverrideCursor so the app is never left
        # with a permanently invisible cursor
        self._fine_active = False
        self._warp_anchor_global = None
        QApplication.restoreOverrideCursor()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._fine_active:
                self._exit_fine_drag()
            self._drag_anchor_y = None
            self._drag_value = None
            self.sliderReleased.emit()

    def hideEvent(self, event):
        # safety net: if this widget is hidden mid-drag (switching tabs,
        # the window closing) with no mouseReleaseEvent ever arriving, make
        # sure the app isn't left with a stuck invisible cursor - same
        # reasoning as WaveformView's own hideEvent
        if self._fine_active:
            self._exit_fine_drag()
        super().hideEvent(event)

    def wheelEvent(self, event):
        # QAbstractSlider's own wheelEvent (inherited via QDial) only
        # accepts the event while it's actually still changing the value -
        # once a knob is scrolled to its min/max, further ticks in the same
        # direction leave it ignored, so Qt walks it up to the nearest
        # ancestor that DOES want it: the section card's QScrollArea, which
        # then scrolls the whole page out from under the user's cursor.
        # Always accepting here (after letting the base class do its usual
        # value-stepping first) keeps every wheel tick over a knob local to
        # that knob, at every value, matching the drag-to-adjust behaviour
        # right above.
        super().wheelEvent(event)
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(4, 4, -4, -4)
        palette = theme.current_palette()

        # track color reacts to theme (was a fixed dark slate, which read as
        # too dark against the light theme's near-white background)
        # flat caps on both arcs - a round cap is a full circle as wide as
        # the arc's own stroke (4px), which is wider than the 3px pointer
        # line now that the pointer reaches all the way to the ring: the
        # round cap at the value arc's current-value end would peek out past
        # both edges of the pointer right where they cross
        track_pen = QPen(QColor(palette["border_hover"]))
        track_pen.setWidth(_RING_WIDTH)
        track_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(track_pen)
        painter.drawArc(rect, START_ANGLE_DEG * 16, -SWEEP_DEG * 16)

        value_range = self.maximum() - self.minimum()
        fraction = (self.value() - self.minimum()) / value_range if value_range else 0

        value_pen = QPen(QColor("#3aa88a"))
        value_pen.setWidth(_RING_WIDTH)
        value_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(value_pen)
        painter.drawArc(rect, START_ANGLE_DEG * 16, -int(SWEEP_DEG * fraction * 16))

        # pointer line - same underlying angle as the arc's current
        # endpoint, converted to an (x, y) point via trig. Screen y grows
        # downward, so the y term is negated to keep this pointing the
        # same visual direction as the arc itself (verified offscreen:
        # lands at 7 o'clock at value=0, 5 o'clock at value=max)
        angle_rad = math.radians(START_ANGLE_DEG - (SWEEP_DEG * fraction))
        cx, cy = rect.center().x(), rect.center().y()
        r = rect.width() / 2
        # pointer color reacts to theme too (was a fixed near-white, all but
        # invisible against the light theme's near-white background) - using
        # the app's brightest/darkest ink token guarantees strong contrast
        # in both themes
        pointer_pen = QPen(QColor(palette["text_bright"]))
        pointer_pen.setWidth(_POINTER_WIDTH)
        pointer_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pointer_pen)
        inner = QPointF(
            cx + r * 0.2 * math.cos(angle_rad), cy - r * 0.2 * math.sin(angle_rad)
        )
        # outer end lands exactly on the ring's own outer edge (r + half its
        # stroke width) rather than a fixed fraction of r - a fraction of r
        # left an absolute gap that grew with the knob's size, so small
        # knobs looked connected to the ring while large ones didn't. The
        # round cap then carries it very slightly past that edge, same as
        # every other knob size, for a small deliberate overlap instead of
        # an exact (and easy to misjudge) tangent.
        outer_radius = r + _RING_WIDTH / 2
        outer = QPointF(
            cx + outer_radius * math.cos(angle_rad),
            cy - outer_radius * math.sin(angle_rad),
        )
        painter.drawLine(inner, outer)
