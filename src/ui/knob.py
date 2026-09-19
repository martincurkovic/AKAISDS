import math
from PySide6.QtWidgets import QDial
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtCore import Qt, QPointF

START_ANGLE_DEG = 240
SWEEP_DEG = 300

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

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(4, 4, -4, -4)

        track_pen = QPen(QColor("#3d3f56"))
        track_pen.setWidth(4)
        painter.setPen(track_pen)
        painter.drawArc(rect, START_ANGLE_DEG * 16, -SWEEP_DEG * 16)

        value_range = self.maximum() - self.minimum()
        fraction = (self.value() - self.minimum()) / value_range if value_range else 0

        value_pen = QPen(QColor("#3aa88a"))
        value_pen.setWidth(4)
        value_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
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
        pointer_pen = QPen(QColor("#e8e6e1"))
        pointer_pen.setWidth(3)
        painter.setPen(pointer_pen)
        inner = QPointF(
            cx + r * 0.2 * math.cos(angle_rad), cy - r * 0.2 * math.sin(angle_rad)
        )
        outer = QPointF(
            cx + r * 0.85 * math.cos(angle_rad), cy - r * 0.85 * math.sin(angle_rad)
        )
        painter.drawLine(inner, outer)
