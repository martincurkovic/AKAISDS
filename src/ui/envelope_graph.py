from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QPen, QColor, QPolygonF
from PySide6.QtCore import QPointF

SUSTAIN_HOLD_FRACTION = 0.15  # sustain is a LEVEL, not a duration - this
# reserves a fixed-width segment to show it


class ADSREnvelopeGraph(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._attack = 0
        self._decay = 0
        self._sustain = 0
        self._release = 0

    def set_values(self, attack, decay, sustain, release):
        self._attack = attack
        self._decay = decay
        self._sustain = sustain
        self._release = release
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # attack/decay/release are 0-99 "speed" values with no confirmed
        # real-world millisecond mapping - this shows relative SHAPE only,
        # not calibrated timing
        time_total = self._attack + self._decay + self._release
        if time_total == 0:
            time_total = 1

        attack_frac = (self._attack / time_total) * (1 - SUSTAIN_HOLD_FRACTION)
        decay_frac = (self._decay / time_total) * (1 - SUSTAIN_HOLD_FRACTION)
        release_frac = (self._release / time_total) * (1 - SUSTAIN_HOLD_FRACTION)
        sustain_height_frac = self._sustain / 99

        x0, y0 = 0, h
        x1, y1 = attack_frac * w, 0
        x2, y2 = x1 + decay_frac * w, h - (sustain_height_frac * h)
        x3, y3 = x2 + SUSTAIN_HOLD_FRACTION * w, y2
        x4, y4 = x3 + release_frac * w, h

        points = [
            QPointF(x0, y0),
            QPointF(x1, y1),
            QPointF(x2, y2),
            QPointF(x3, y3),
            QPointF(x4, y4),
        ]
        pen = QPen(QColor("#3aa88a"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF(points))


class Envelope2Graph(QWidget):
    # ENV2 is a 4-stage rate/level generator, NOT an ADSR shape - levels
    # can rise or fall freely between stages (confirmed against the S2000
    # manual's own example envelope shapes). Verified offscreen: correctly
    # renders a non-monotonic dip-rise-dip shape, which EnvelopeGraph
    # (built for ENV1's genuine ADSR shape) cannot represent.
    def __init__(self, parent=None):
        super().__init__(parent)
        self._stages = [(0, 0)] * 4  # (rate, level) per stage, in order

    def set_values(self, rate1, level1, rate2, level2, rate3, level3, rate4, level4):
        self._stages = [
            (rate1, level1),
            (rate2, level2),
            (rate3, level3),
            (rate4, level4),
        ]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        total_rate = sum(r for r, l in self._stages) or 1
        points = [QPointF(0, h)]  # starts at silence
        x = 0
        for rate, level in self._stages:
            x += (rate / total_rate) * w
            y = h - (level / 99) * h
            points.append(QPointF(x, y))

        pen = QPen(QColor("#d97757"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF(points))
