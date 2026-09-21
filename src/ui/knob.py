import math
from PySide6.QtWidgets import QDial
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtCore import Qt, QPointF
from ui import theme

START_ANGLE_DEG = 240
SWEEP_DEG = 300
_RING_WIDTH = 4
_POINTER_WIDTH = 3

_DRAG_SENSITIVITY_PX = 150


class Knob(QDial):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEnabled(False)  # read-only for now
        self._drag_start_y = None
        self._drag_start_value = None
        self._default_value = None

    def defaultValue(self):
        if self._default_value is not None:
            return self._default_value
        return (self.minimum() + self.maximum()) // 2

    def setDefaultValue(self, value):
        self._default_value = value

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_y = event.position().y()
            self._drag_start_value = self.value()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # the release that Qt sends right after this event does the
            # actual commit (sliderReleased), same as an ordinary drag
            self.setValue(self.defaultValue())

    def mouseMoveEvent(self, event):
        if self._drag_start_y is None:
            return
        delta_y = self._drag_start_y - event.position().y()
        value_range = self.maximum() - self.minimum()
        sensitivity = value_range / _DRAG_SENSITIVITY_PX
        new_value = int(self._drag_start_value + delta_y * sensitivity)
        new_value = max(self.minimum(), min(self.maximum(), new_value))
        self.setValue(new_value)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_y = None
            self._drag_start_value = None
            self.sliderReleased.emit()

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
