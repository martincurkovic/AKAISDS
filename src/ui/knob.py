from PySide6.QtWidgets import QDial
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtCore import Qt

# Qt measures angles counterclockwise from 3 o'clock, in 16ths of a degree.
# 240 degrees in that convention lands at 7 o'clock; sweeping 300 degrees
# clockwise from there passes through 12 o'clock and ends at 5 o'clock -
# verified by rendering offscreen at min/mid/max and checking which
# regions actually filled in, including that the bottom gap stays clear.
START_ANGLE_DEG = 240
SWEEP_DEG = 300


class Knob(QDial):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEnabled(False)  # read-only for now

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
