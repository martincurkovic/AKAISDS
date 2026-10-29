"""The envelope graphs: a drawing of an envelope's SHAPE (no calibrated timing) that the user can also edit by dragging its handles.

Dragging is shared by every graph (`_EditableGraph`): each graph supplies its curve, which curve points are handles and what dragging one means (the
`*_values_for_handle` functions below - pure, Qt-free, the inverse of the drawing maths). A graph never writes anything itself. It emits
- `values_changed({name: value})` live while dragging (and on a reset or a cancel), so the owner can move its knobs and readouts at once;
- `edit_committed({name: value})` once, on release (or a reset), with only the values that differ from before: the moment a window may send them to a sampler;
- `edit_cancelled()` when Esc (or hiding the graph) abandoned a drag - the values are already back and nothing was committed.
A graph is INERT until its owner calls `set_editable(True)` (values read, writable, not frozen), so an unwired graph can never edit anything.
Shift slows a drag, a double-click on a handle resets what it controls to `set_defaults`, Esc cancels. While a drag is in progress `set_values` is ignored:
a reload must not fight the pointer.
"""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ui import theme

#: the size every editor gives its envelope graphs (big enough to grab a handle; they were 200 x 80-90 when they only drew)
GRAPH_SIZE = (280, 110)

SUSTAIN_HOLD_FRACTION = 0.15  # sustain is a LEVEL, not a duration - this
# reserves a fixed-width segment to show it

_BORDER_RADIUS = 4  # matches QWidget#sectionCard/#zoneCard's own radius
_INSET = 8  # room between the border and the curve, so a handle on the edge (attack 0, level 99) is whole and easy to grab
_HANDLE_RADIUS = 4.5
_HIT_RADIUS = 11  # how close the pointer must be to a handle to take it
_FINE_DIVISOR = 6  # how much slower a Shift-held drag moves
_OVERLAP_PX = 2.5  # handles this close to the nearest one are "on top of" it (a stage of rate 0, an instant attack...)
_DECIDE_PX = 3  # how far the pointer moves before a drag over overlapping handles picks one
_SEGMENT_HIT = 6  # how close the pointer must be to a draggable segment (the sustain line) to take it


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


def _clamp_int(value, low, high):
    return max(low, min(high, int(round(value))))


def adsr_values_for_handle(index, x, y, values, w, h):
    """`values` (attack, decay, sustain, release; 0-99) after handle `index` is dragged to (x, y) in a w x h graph - the inverse of `_adsr_points`.
    Handle 0 is the attack peak (only x matters: attack), 1 the end of the decay (x: decay, y: sustain), 2 the end of the release (x: release).
    A stage's width depends only on its own value, so each x is measured from where the stage before it ends NOW."""
    attack, decay, sustain, release = values
    if w <= 0 or h <= 0:
        return tuple(values)
    stage_w = (1 - SUSTAIN_HOLD_FRACTION) / 3 * w
    if index == 0:
        attack = _clamp_int(x / stage_w * 99, 0, 99)
    elif index == 1:
        decay = _clamp_int((x - attack / 99 * stage_w) / stage_w * 99, 0, 99)
        sustain = _clamp_int((h - y) / h * 99, 0, 99)
    else:
        start = attack / 99 * stage_w + decay / 99 * stage_w + SUSTAIN_HOLD_FRACTION * w
        release = _clamp_int((x - start) / stage_w * 99, 0, 99)
    return (attack, decay, sustain, release)


def adsr_values_for_sustain(y, values, h):
    """`values` (attack, decay, sustain, release) after the sustain LINE is dragged to height y: only the sustain level changes."""
    attack, decay, sustain, release = values
    if h > 0:
        sustain = _clamp_int((h - y) / h * 99, 0, 99)
    return (attack, decay, sustain, release)


def env2_values_for_handle(index, x, y, stages, w, h):
    """`stages` [(rate, level) x 4; 0-99] after handle `index` (the end of stage index + 1) is dragged to (x, y) - the inverse of `_env2_points`."""
    stages = [tuple(stage) for stage in stages]
    if w <= 0 or h <= 0:
        return stages
    stage_w = w / len(stages)
    start = sum(rate for rate, _level in stages[:index]) / 99 * stage_w
    stages[index] = (_clamp_int((x - start) / stage_w * 99, 0, 99), _clamp_int((h - y) / h * 99, 0, 99))
    return stages


class _Drag:
    # one drag in progress: the handle, the values before it, and where the handle is (in the graph's inner coordinates)
    def __init__(self, index, start, offset, pos, candidates=None, press=None):
        self.index = index
        self.candidates = candidates  # several handles under the pointer: which one is chosen by the first move (see _choose)
        self.press = press
        self.start = start
        self.offset = offset  # pointer - handle, so the handle doesn't jump to the pointer
        self.pos = pos
        self.fine = False
        self.fine_pointer = None
        self.fine_pos = None


class _EditableGraph(QWidget):
    """What every envelope graph shares: the frame, the curve, the handles and the dragging. Subclasses give the values and the geometry."""

    #: live, while dragging (also a reset or a cancel): {name: value} of the values that just changed
    values_changed = Signal(dict)
    #: once, on release (or a reset): {name: value} of the values that differ from before the drag
    edit_committed = Signal(dict)
    #: Esc or hiding the graph abandoned a drag (the values are already back)
    edit_cancelled = Signal()

    NAMES = ()  # the value names, in the order of `_values`
    HANDLE_NAMES = ()  # per handle: the value names dragging it changes
    HANDLE_AXES = ()  # per handle: "x", "y" or "xy" - which way it moves (the pointer shape)
    CURVE_INDEX_OF_HANDLE = ()  # per handle: which point of the curve it sits on
    # Draggable SEGMENTS (the flat sustain line): (curve point, curve point) pairs. A segment is a target after the handles, so its names and axes are the
    # tail of HANDLE_NAMES / HANDLE_AXES and its index is len(CURVE_INDEX_OF_HANDLE) + its position here. It always moves up/down.
    SEGMENT_POINTS = ()

    def __init__(self, color, parent=None):
        super().__init__(parent)
        self._color = color
        self._values = tuple(0 for _ in self.NAMES)
        self._editable = False
        self._defaults = {}
        self._drag = None
        self._hover = -1
        self.setMouseTracking(True)

    # -- subclass hooks ----------------------------------------------------------------------------------

    def _curve_points(self, w, h):
        raise NotImplementedError

    def _values_for_handle(self, index, x, y, w, h):
        raise NotImplementedError

    def _paint_background(self, painter, w, h):
        pass

    # -- values --------------------------------------------------------------------------------------------

    def _store(self, values):
        self._values = tuple(values)

    def values(self):
        return dict(zip(self.NAMES, self._values))

    def is_dragging(self):
        return self._drag is not None

    def set_defaults(self, defaults):
        """{name: value} a double-click on a handle resets to (the knobs' own defaults)."""
        self._defaults = dict(defaults)

    def set_editable(self, editable):
        editable = bool(editable)
        if not editable and self._drag is not None:
            self._cancel_drag()
        if editable != self._editable:
            self._editable = editable
            self._hover = -1
            self.unsetCursor()
            self.update()

    def is_editable(self):
        return self._editable

    # -- geometry --------------------------------------------------------------------------------------------

    def _inner_size(self):
        return max(self.width() - 2 * _INSET, 1), max(self.height() - 2 * _INSET, 1)

    def handle_positions(self):
        """The handles' centres in WIDGET coordinates (what a mouse event reports)."""
        w, h = self._inner_size()
        points = self._curve_points(w, h)
        return [QPointF(points[i][0] + _INSET, points[i][1] + _INSET) for i in self.CURVE_INDEX_OF_HANDLE]

    def _handles_at(self, position):
        """The handle under `position` - or, when several sit on top of one another, all of them (earliest first). Empty if none is close enough."""
        centres = self.handle_positions()
        distances = [((c.x() - position.x()) ** 2 + (c.y() - position.y()) ** 2) ** 0.5 for c in centres]
        if not distances or min(distances) > _HIT_RADIUS:
            return []
        nearest = centres[distances.index(min(distances))]
        return [
            i for i, c in enumerate(centres) if ((c.x() - nearest.x()) ** 2 + (c.y() - nearest.y()) ** 2) ** 0.5 <= _OVERLAP_PX and distances[i] <= _HIT_RADIUS
        ]

    def _segment_at(self, position):
        """The index (as a drag target) of the draggable segment under `position`, or -1."""
        w, h = self._inner_size()
        points = self._curve_points(w, h)
        for k, (a, b) in enumerate(self.SEGMENT_POINTS):
            ax, ay = points[a][0] + _INSET, points[a][1] + _INSET
            bx, by = points[b][0] + _INSET, points[b][1] + _INSET
            dx, dy = bx - ax, by - ay
            length_sq = dx * dx + dy * dy
            t = 0 if length_sq == 0 else max(0.0, min(1.0, ((position.x() - ax) * dx + (position.y() - ay) * dy) / length_sq))
            nearest_x, nearest_y = ax + t * dx, ay + t * dy
            if ((position.x() - nearest_x) ** 2 + (position.y() - nearest_y) ** 2) ** 0.5 <= _SEGMENT_HIT:
                return len(self.CURVE_INDEX_OF_HANDLE) + k
        return -1

    def _targets_at(self, position):
        """What a press at `position` takes: the handle(s) under it, else the segment under it (handles win where they meet), else nothing."""
        handles = self._handles_at(position)
        if handles:
            return handles
        segment = self._segment_at(position)
        return [segment] if segment >= 0 else []

    def _is_segment(self, index):
        return index >= len(self.CURVE_INDEX_OF_HANDLE)

    def _target_position(self, index, pointer):
        """Where a drag target is, in WIDGET coordinates: a handle's centre; for a segment the point of it level with the pointer (it only moves up/down)."""
        if not self._is_segment(index):
            return self.handle_positions()[index]
        w, h = self._inner_size()
        a = self.SEGMENT_POINTS[index - len(self.CURVE_INDEX_OF_HANDLE)][0]
        return QPointF(pointer.x(), self._curve_points(w, h)[a][1] + _INSET)

    def _choose(self, candidates, dx, dy):
        """Which of several overlapping handles a drag means: a sideways move takes the first that can move sideways, an up/down move the first that can
        move up/down (a handle at x 0 and one at x 0 are the attack and the decay of an instant envelope: dragging right is the attack, down the sustain)."""
        wanted = "x" if abs(dx) >= abs(dy) else "y"
        for index in candidates:
            if wanted in self.HANDLE_AXES[index]:
                return index
        return candidates[0]

    def _interactive(self):
        return self._editable and self.isEnabled()

    # -- painting --------------------------------------------------------------------------------------------

    def _line_color(self):
        # a theme without colour (System 6) draws every graph in its own ink
        return QColor(theme.current_palette().get("graph_line", self._color))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        _draw_border(painter, self.width(), self.height())
        w, h = self._inner_size()
        painter.translate(_INSET, _INSET)
        self._paint_background(painter, w, h)
        pen = QPen(self._line_color())
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in self._curve_points(w, h)]))
        if not self._interactive():
            return
        palette = theme.current_palette()
        points = self._curve_points(w, h)
        for k, (a, b) in enumerate(self.SEGMENT_POINTS):
            index = len(self.CURVE_INDEX_OF_HANDLE) + k
            if index == self._hover or (self._drag is not None and self._drag.index == index):
                # the line that is about to move (or is moving) shows it: thicker and brighter
                bright = QPen(self._line_color().lighter(135))
                bright.setWidth(4)
                bright.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(bright)
                painter.drawLine(QPointF(*points[a]), QPointF(*points[b]))
        for index, point_index in enumerate(self.CURVE_INDEX_OF_HANDLE):
            x, y = points[point_index]
            active = index == self._hover or (self._drag is not None and self._drag.index == index)
            radius = _HANDLE_RADIUS + (1.5 if active else 0)
            painter.setBrush(self._line_color())
            ring = QPen(QColor(palette["text_bright"] if active else palette["bg"]))
            ring.setWidthF(1.5)
            painter.setPen(ring)
            painter.drawEllipse(QPointF(x, y), radius, radius)

    # -- mouse -----------------------------------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self._interactive():
            return super().mousePressEvent(event)
        candidates = self._targets_at(event.position())
        if not candidates:
            return super().mousePressEvent(event)
        index = candidates[0]
        centre = self._target_position(index, event.position())
        inner = QPointF(centre.x() - _INSET, centre.y() - _INSET)
        self._drag = _Drag(
            index, self._values, event.position() - centre, inner, candidates if len(candidates) > 1 else None, event.position()
        )
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # Shift already held at the press: the whole drag is fine, not just what follows the first move
            self._drag.fine, self._drag.fine_pointer, self._drag.fine_pos = True, event.position(), inner
        self.grabKeyboard()  # Esc reaches this widget without taking focus (a focus change can scroll the page)
        self.update()
        event.accept()

    def mouseMoveEvent(self, event):
        drag = self._drag
        if drag is None:
            self._update_hover(event.position())
            return
        pointer = event.position()
        if drag.candidates:
            # several handles under the pointer: nothing moves until it has gone far enough to say which one is meant
            moved = pointer - drag.press
            if (moved.x() ** 2 + moved.y() ** 2) ** 0.5 < _DECIDE_PX:
                return
            drag.index = self._choose(drag.candidates, moved.x(), moved.y())
            drag.candidates = None
        fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if fine and not drag.fine:
            drag.fine, drag.fine_pointer, drag.fine_pos = True, pointer, drag.pos
        elif not fine and drag.fine:
            # leaving fine mode: the handle stays where it is and follows the pointer from there
            drag.fine = False
            drag.offset = pointer - (drag.pos + QPointF(_INSET, _INSET))
        if drag.fine:
            drag.pos = drag.fine_pos + (pointer - drag.fine_pointer) / _FINE_DIVISOR
        else:
            drag.pos = pointer - drag.offset - QPointF(_INSET, _INSET)
        w, h = self._inner_size()
        new = tuple(self._values_for_handle(drag.index, drag.pos.x(), drag.pos.y(), w, h))
        if new != self._values:
            changed = {name: v for name, v, old in zip(self.NAMES, new, self._values) if v != old}
            self._store(new)
            self.update()
            self.values_changed.emit(changed)
        event.accept()

    def mouseReleaseEvent(self, event):
        drag = self._drag
        if drag is None or event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        self._end_drag()  # before the signal: the owner's reaction may call set_values
        changed = {name: v for name, v, start in zip(self.NAMES, self._values, drag.start) if v != start}
        self._update_hover(event.position())
        if changed:
            self.edit_committed.emit(changed)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self._interactive():
            return super().mouseDoubleClickEvent(event)
        targets = self._targets_at(event.position())
        if not targets:
            return super().mouseDoubleClickEvent(event)
        index = targets[0]
        values = list(self._values)
        for name in self.HANDLE_NAMES[index]:
            if name in self._defaults:
                values[self.NAMES.index(name)] = self._defaults[name]
        changed = {name: v for name, v, old in zip(self.NAMES, values, self._values) if v != old}
        if changed:
            self._store(values)
            self.update()
            self.values_changed.emit(changed)
            self.edit_committed.emit(changed)
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._drag is not None:
            self._cancel_drag()
            event.accept()
            return
        super().keyPressEvent(event)

    def leaveEvent(self, event):
        if self._drag is None and self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    def hideEvent(self, event):
        # a drag whose release can never arrive (a tab switched by the keyboard, the window closing) must not leave the keyboard grabbed
        if self._drag is not None:
            self._cancel_drag()
        super().hideEvent(event)

    # -- drag bookkeeping ------------------------------------------------------------------------------------

    def _end_drag(self):
        self._drag = None
        self.releaseKeyboard()
        self.update()

    def _cancel_drag(self):
        drag = self._drag
        self._end_drag()
        changed = {name: v for name, v, start in zip(self.NAMES, drag.start, self._values) if v != start}
        self._store(drag.start)
        if changed:
            self.values_changed.emit(changed)
        self.edit_cancelled.emit()

    def _update_hover(self, position):
        targets = self._targets_at(position) if self._interactive() else []
        index = targets[0] if targets else -1
        if index != self._hover:
            self._hover = index
            if index < 0:
                self.unsetCursor()
            else:
                axes = self.HANDLE_AXES[index]
                self.setCursor(
                    Qt.CursorShape.SizeAllCursor if axes == "xy" else Qt.CursorShape.SizeHorCursor if axes == "x" else Qt.CursorShape.SizeVerCursor
                )
            self.update()


class ADSREnvelopeGraph(_EditableGraph):
    NAMES = ("attack", "decay", "sustain", "release")
    HANDLE_NAMES = (("attack",), ("decay", "sustain"), ("release",), ("sustain",))
    HANDLE_AXES = ("x", "xy", "x", "y")
    CURVE_INDEX_OF_HANDLE = (1, 2, 4)
    SEGMENT_POINTS = ((2, 3),)  # the flat sustain line

    def __init__(self, parent=None):
        super().__init__("#3aa88a", parent)

    def set_values(self, attack, decay, sustain, release):
        if self._drag is not None:
            return  # a reload must not fight the pointer
        self._store((attack, decay, sustain, release))
        self.update()

    def _curve_points(self, w, h):
        return _adsr_points(*self._values, w, h)

    def _values_for_handle(self, index, x, y, w, h):
        if self._is_segment(index):
            return adsr_values_for_sustain(y, self._values, h)
        return adsr_values_for_handle(index, x, y, self._values, w, h)


class Envelope2Graph(_EditableGraph):
    # ENV2 is a 4-stage rate/level generator, NOT an ADSR shape - levels
    # can rise or fall freely between stages (confirmed against the S2000
    # manual's own example envelope shapes). Verified offscreen: correctly
    # renders a non-monotonic dip-rise-dip shape, which EnvelopeGraph
    # (built for ENV1's genuine ADSR shape) cannot represent.
    NAMES = tuple(name for i in range(1, 5) for name in (f"rate{i}", f"level{i}"))
    HANDLE_NAMES = tuple((f"rate{i}", f"level{i}") for i in range(1, 5))  # (no segment: its stages have no flat part)
    HANDLE_AXES = ("xy",) * 4
    CURVE_INDEX_OF_HANDLE = (1, 2, 3, 4)

    def __init__(self, parent=None):
        super().__init__("#d97757", parent)

    def set_values(self, rate1, level1, rate2, level2, rate3, level3, rate4, level4):
        if self._drag is not None:
            return
        self._store((rate1, level1, rate2, level2, rate3, level3, rate4, level4))
        self.update()

    def _stages(self):
        return [(self._values[i], self._values[i + 1]) for i in range(0, 8, 2)]

    def _curve_points(self, w, h):
        return _env2_points(self._stages(), w, h)

    def _values_for_handle(self, index, x, y, w, h):
        return tuple(v for stage in env2_values_for_handle(index, x, y, self._stages(), w, h) for v in stage)


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


def yamaha_adsr_rates(attack, decay, sustain, release):
    """The inverse of `yamaha_adsr_values`: the graph's (attack, decay, sustain, release) on 0-99 back to the unit's (attack rate, decay rate, sustain level,
    release rate) on 0-127."""

    def rate(time):
        return min(max(YAMAHA_RATE_MAX - round(time * YAMAHA_RATE_MAX / 99), 0), YAMAHA_RATE_MAX)

    return rate(attack), rate(decay), min(max(round(sustain * YAMAHA_LEVEL_MAX / 99), 0), YAMAHA_LEVEL_MAX), rate(release)


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


def level_values_for_handle(index, x, y, values, w, h, margin=3):
    """`values` (init, attack_level, sustain_level, release_level, attack_rate, decay_rate, release_rate) after handle `index` is dragged to (x, y) - the
    inverse of `_level_envelope_points`. Handle 0 is the start (only y: init level), 1 the attack peak (x: attack rate, y: attack level), 2 the end of the
    decay (decay rate, sustain level), 3 the end of the release (release rate, release level). A HIGHER rate is a NARROWER stage."""
    init, attack_level, sustain_level, release_level, attack_rate, decay_rate, release_rate = values
    if w <= 0 or h <= 0:
        return tuple(values)
    stage_w = (1 - SUSTAIN_HOLD_FRACTION) / 3 * w

    def level(y_):
        return _clamp_int((h / 2 - y_) / max(h / 2 - margin, 1) * YAMAHA_LEVEL_MAX, -YAMAHA_LEVEL_MAX, YAMAHA_LEVEL_MAX)

    def rate(width):
        return _clamp_int(YAMAHA_RATE_MAX - width / stage_w * YAMAHA_RATE_MAX, 0, YAMAHA_RATE_MAX)

    def width(rate_):
        return (YAMAHA_RATE_MAX - min(max(rate_, 0), YAMAHA_RATE_MAX)) / YAMAHA_RATE_MAX * stage_w

    if index == 0:
        init = level(y)
    elif index == 1:
        attack_rate, attack_level = rate(x), level(y)
    elif index == 2:
        decay_rate, sustain_level = rate(x - width(attack_rate)), level(y)
    else:
        start = width(attack_rate) + width(decay_rate) + SUSTAIN_HOLD_FRACTION * w
        release_rate, release_level = rate(x - start), level(y)
    return (init, attack_level, sustain_level, release_level, attack_rate, decay_rate, release_rate)


def level_values_for_sustain(y, values, h, margin=3):
    """`values` of a level envelope after the sustain LINE is dragged to height y: only the sustain level changes."""
    values = list(values)
    if h > 0:
        values[2] = _clamp_int((h / 2 - y) / max(h / 2 - margin, 1) * YAMAHA_LEVEL_MAX, -YAMAHA_LEVEL_MAX, YAMAHA_LEVEL_MAX)
    return tuple(values)


class LevelEnvelopeGraph(_EditableGraph):
    # A 4-level envelope (init -> attack -> sustain, then release) drawn around a faint zero line - the Yamaha filter and pitch EGs.
    NAMES = ("init", "attack_level", "sustain_level", "release_level", "attack_rate", "decay_rate", "release_rate")
    HANDLE_NAMES = (("init",), ("attack_rate", "attack_level"), ("decay_rate", "sustain_level"), ("release_rate", "release_level"), ("sustain_level",))
    HANDLE_AXES = ("y", "xy", "xy", "xy", "y")
    CURVE_INDEX_OF_HANDLE = (0, 1, 2, 4)
    SEGMENT_POINTS = ((2, 3),)  # the flat sustain line

    def __init__(self, color="#d97757", parent=None):
        super().__init__(color, parent)
        self._values = (0, 0, 0, 0, YAMAHA_RATE_MAX, YAMAHA_RATE_MAX, YAMAHA_RATE_MAX)

    def set_values(self, init, attack_level, sustain_level, release_level, attack_rate, decay_rate, release_rate):
        if self._drag is not None:
            return
        self._store((init, attack_level, sustain_level, release_level, attack_rate, decay_rate, release_rate))
        self.update()

    def _paint_background(self, painter, w, h):
        zero = QPen(QColor(theme.current_palette()["border"]))
        zero.setWidth(1)
        painter.setPen(zero)
        painter.drawLine(QPointF(0, h / 2), QPointF(w, h / 2))

    def _curve_points(self, w, h):
        # the inset around the graph already keeps a handle on the edge whole: no extra margin here
        return _level_envelope_points(*self._values, w, h, margin=0)

    def _values_for_handle(self, index, x, y, w, h):
        if self._is_segment(index):
            return level_values_for_sustain(y, self._values, h, margin=0)
        return level_values_for_handle(index, x, y, self._values, w, h, margin=0)
