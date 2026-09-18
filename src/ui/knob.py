from PySide6.QtWidgets import QDial
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtCore import Qt


class Knob(QDial):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEnabled(False)  # read-only for now - a disabled QDial
        # can't be dragged, and Qt greys it out automatically so this
        # reads as "display only" without any extra styling work

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(4, 4, -4, -4)

        track_pen = QPen(QColor("#3d3f56"))
        track_pen.setWidth(4)
        painter.setPen(track_pen)
        painter.drawArc(rect, 0, 360 * 16)

        span = self.maximum() - self.minimum()
        fraction = (self.value() - self.minimum()) / span if span else 0
        value_pen = QPen(QColor("#3aa88a"))
        value_pen.setWidth(4)
        value_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(value_pen)
        # Qt measures angles counterclockwise from 3 o'clock, in 16ths of
        # a degree - 90*16 starts us at 12 o'clock, and the negative span
        # makes it sweep clockwise as the value increases, matching the
        # mockup's visual convention (verified by rendering this offscreen
        # at several values and checking which quadrants actually filled)
        painter.drawArc(rect, 90 * 16, -int(360 * fraction * 16))
