from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QPen, QColor, QPolygonF
from PySide6.QtCore import Qt, QPointF, QRectF

from ui import theme

SUSTAIN_HOLD_FRACTION = 0.15  # sustain is a LEVEL, not a duration - this
# reserves a fixed-width segment to show it

_BORDER_RADIUS = 4  # matches QWidget#sectionCard/#zoneCard's own radius


def _draw_border(painter, w, h):
    # the envelope curve is drawn edge-to-edge (0,0 to w,h) with nothing
    # else in the widget, so at rest - or at an extreme (attack=0,
    # sustain=99, ...) - it was often just a bare line with no visible
    # boundary at all, making the graph's actual extent a guess. A plain
    # rect, not a fancier frame, since this is a size reference for the
    # curve above it, not a card of its own.
    pen = QPen(QColor(theme.current_palette()["border"]))
    pen.setWidth(1)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # inset by half the pen width - drawn exactly on the edge, a 1px line
    # is split across the boundary by anti-aliasing and half of it is
    # clipped away, reading as thinner on the right/bottom than the top/left
    painter.drawRoundedRect(
        QRectF(0.5, 0.5, w - 1, h - 1), _BORDER_RADIUS, _BORDER_RADIUS
    )


def _adsr_points(attack, decay, sustain, release, w, h):
    # attack/decay/release are 0-99 "speed" values with no confirmed
    # real-world millisecond mapping - this shows relative SHAPE only,
    # not calibrated timing. Returns plain (x, y) tuples, in draw order,
    # rather than QPointF - kept independent of Qt so it can be unit
    # tested without a QApplication.
    time_total = attack + decay + release
    if time_total == 0:
        time_total = 1

    attack_frac = (attack / time_total) * (1 - SUSTAIN_HOLD_FRACTION)
    decay_frac = (decay / time_total) * (1 - SUSTAIN_HOLD_FRACTION)
    release_frac = (release / time_total) * (1 - SUSTAIN_HOLD_FRACTION)
    sustain_height_frac = sustain / 99

    x0, y0 = 0, h
    x1, y1 = attack_frac * w, 0
    x2, y2 = x1 + decay_frac * w, h - (sustain_height_frac * h)
    x3, y3 = x2 + SUSTAIN_HOLD_FRACTION * w, y2
    x4, y4 = x3 + release_frac * w, h

    return [(x0, y0), (x1, y1), (x2, y2), (x3, y3), (x4, y4)]


def _env2_points(stages, w, h):
    # stages: [(rate, level), ...] in order - see Envelope2Graph's own
    # comment for why this can't reuse _adsr_points. Returns plain (x, y)
    # tuples, same reasoning as _adsr_points above.
    total_rate = sum(rate for rate, _level in stages) or 1
    points = [(0, h)]  # starts at silence
    x = 0
    for rate, level in stages:
        x += (rate / total_rate) * w
        y = h - (level / 99) * h
        points.append((x, y))
    return points


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

        _draw_border(painter, w, h)

        points = _adsr_points(
            self._attack, self._decay, self._sustain, self._release, w, h
        )
        pen = QPen(QColor("#3aa88a"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in points]))


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

        _draw_border(painter, w, h)

        points = _env2_points(self._stages, w, h)
        pen = QPen(QColor("#d97757"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in points]))
