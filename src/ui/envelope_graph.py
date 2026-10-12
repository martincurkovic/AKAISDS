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
    #
    # Each of the three stages gets its own FIXED width budget - scaled
    # only by its own value against 99, never against the other two - so
    # turning one knob never moves a segment whose value didn't change.
    # This used to normalize against time_total = attack+decay+release,
    # which meant e.g. dragging Decay up also silently squeezed Attack's
    # on-screen width even though Attack's own value never moved (a real
    # regression a user noticed by comparing two screenshots side by side -
    # not how any synth's ADSR display behaves; each stage's footprint is
    # independent of its siblings' current values everywhere else).
    stage_max_frac = (1 - SUSTAIN_HOLD_FRACTION) / 3
    attack_frac = (attack / 99) * stage_max_frac
    decay_frac = (decay / 99) * stage_max_frac
    release_frac = (release / 99) * stage_max_frac
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
    #
    # Same fix as _adsr_points: each stage gets a fixed width budget
    # (w / stage count), scaled only by its own rate against 99 - not
    # normalized against the other stages' current rates, which used to
    # mean moving R1 would also silently resize R2/R3/R4's segments.
    stage_max_width = w / len(stages)
    points = [(0, h)]  # starts at silence
    x = 0
    for rate, level in stages:
        x += (rate / 99) * stage_max_width
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


# -- the Yamaha A4000/A5000's envelopes (ui/yamaha_samples_tab.py) ---------------------------------------------
#
# Its amplitude EG is an ADSR whose attack/decay/release are RATES - the HIGHER the value, the FASTER the stage (owner's
# manual, "Amplitude EG") - while the S3000's ADSR knobs are times. `yamaha_adsr_values` turns one into the other so the
# S3000's ADSREnvelopeGraph can draw it (shape only, no calibrated timing, like the originals). Its filter and pitch EGs are
# 4-LEVEL generators - init level, attack level, sustain level, release level, each -127..+127, joined by an attack, a decay
# and a release rate - so they get a bipolar graph of their own.

YAMAHA_RATE_MAX = 127
YAMAHA_LEVEL_MAX = 127


def yamaha_adsr_values(attack_rate, decay_rate, sustain_level, release_rate):
    """(attack, decay, sustain, release) on the 0-99 scale ADSREnvelopeGraph expects: rates become TIMES (a rate of 127 is an
    instant stage, 0 the slowest), the sustain level just rescales."""

    def time(rate):
        return (YAMAHA_RATE_MAX - min(max(rate, 0), YAMAHA_RATE_MAX)) * 99 / YAMAHA_RATE_MAX

    level = min(max(sustain_level, 0), YAMAHA_LEVEL_MAX) * 99 / YAMAHA_LEVEL_MAX
    return time(attack_rate), time(decay_rate), level, time(release_rate)


def _level_envelope_points(init, attack_level, sustain_level, release_level, attack_rate, decay_rate, release_rate, w, h, margin=3):
    # Plain (x, y) tuples in draw order, like _adsr_points. Levels are -127..+127 around a centre line; each stage has its own
    # fixed width budget scaled only by ITS rate (never by its siblings' - the same rule _adsr_points follows), the higher the
    # rate the narrower the stage.
    stage_max = (1 - SUSTAIN_HOLD_FRACTION) / 3

    def width(rate):
        return (YAMAHA_RATE_MAX - min(max(rate, 0), YAMAHA_RATE_MAX)) / YAMAHA_RATE_MAX * stage_max * w

    def y_for(level):
        level = min(max(level, -YAMAHA_LEVEL_MAX), YAMAHA_LEVEL_MAX)
        return h / 2 - level / YAMAHA_LEVEL_MAX * (h / 2 - margin)

    x1 = width(attack_rate)
    x2 = x1 + width(decay_rate)
    x3 = x2 + SUSTAIN_HOLD_FRACTION * w
    x4 = x3 + width(release_rate)
    return [(0, y_for(init)), (x1, y_for(attack_level)), (x2, y_for(sustain_level)), (x3, y_for(sustain_level)), (x4, y_for(release_level))]


class LevelEnvelopeGraph(QWidget):
    # A 4-level envelope (init -> attack -> sustain, then release) drawn around a faint zero line - the Yamaha filter and pitch EGs.
    def __init__(self, color="#d97757", parent=None):
        super().__init__(parent)
        self._color = color
        self._values = (0, 0, 0, 0, YAMAHA_RATE_MAX, YAMAHA_RATE_MAX, YAMAHA_RATE_MAX)

    def set_values(self, init, attack_level, sustain_level, release_level, attack_rate, decay_rate, release_rate):
        self._values = (init, attack_level, sustain_level, release_level, attack_rate, decay_rate, release_rate)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        _draw_border(painter, w, h)
        zero = QPen(QColor(theme.current_palette()["border"]))
        zero.setWidth(1)
        painter.setPen(zero)
        painter.drawLine(QPointF(1, h / 2), QPointF(w - 1, h / 2))
        points = _level_envelope_points(*self._values, w, h)
        pen = QPen(QColor(self._color))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in points]))
