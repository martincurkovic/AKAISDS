from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtGui import QPainter, QPen, QColor, QPolygonF
from PySide6.QtCore import Qt, QRectF, QPointF, Signal

from ui import theme

_BORDER_RADIUS = 4  # matches envelope_graph.py's own card-edge radius
_HIT_RADIUS_PX = 6  # how close a click has to land to a marker to grab it
_HANDLE_SIZE = 5  # the little triangle at the top of each marker line

# order matters: this is also the neighbour-clamping order - each marker can
# only move between the ones on either side of it in this list
_MARKER_ORDER = ("start", "loop_start", "loop_end", "end")

_PLACEHOLDER_TEXT = (
    "Double-click to load waveform\n\n"
    "Loading is slow and will freeze the interface"
)
_LOADING_TEXT = "Loading…"


def build_envelope(samples, width):
    """(min, max) per pixel column across *samples*, *width* columns wide.

    Plain function, independent of Qt/QWidget, same reasoning as
    envelope_graph.py's _adsr_points - testable without a QApplication, and
    it's the part that actually matters for a huge sample (hundreds of
    thousands of frames): painting one drawLine() per raw sample was fast
    enough for the original WaveformRenderer prototype's static display, but
    not for a widget that repaints on every mouse-move while dragging a
    marker. This reduces every repaint to one min/max pair per pixel,
    computed once per load/resize instead of once per paint.
    """
    n = len(samples)
    width = max(1, int(width))
    if n == 0:
        return [(0, 0)] * width
    envelope = []
    for x in range(width):
        lo_i = (x * n) // width
        hi_i = max(lo_i + 1, ((x + 1) * n) // width)
        chunk = samples[lo_i:hi_i]
        envelope.append((min(chunk), max(chunk)))
    return envelope


def frame_for_x(x, width, frame_count):
    """Inverse of the x-position a marker at *frame* would be drawn at."""
    if width <= 1 or frame_count <= 1:
        return 0
    frac = min(1.0, max(0.0, x / (width - 1)))
    return int(round(frac * (frame_count - 1)))


def x_for_frame(frame, width, frame_count):
    if frame_count <= 1:
        return 0.0
    return (frame / (frame_count - 1)) * (width - 1)


def clamp_marker(order, index, frame, values, frame_count):
    """Clamp *frame* between this marker's neighbours (and the sample's own
    bounds at the two ends) - start <= loop_start <= loop_end <= end always
    holds, so a drag can never cross a neighbouring marker or the sample's
    own extent.
    """
    frame = max(0, min(frame_count - 1, frame))
    lo_bound = values[order[index - 1]] if index > 0 else 0
    hi_bound = values[order[index + 1]] if index < len(order) - 1 else frame_count - 1
    return max(lo_bound, min(hi_bound, frame))


class WaveformView(QWidget):
    # Loop-point editor for one sample: a fast min/max envelope plus four
    # draggable markers (start/loop start/loop end/end). Has no sample
    # loaded until told to (see set_waveform/clear) - in that placeholder
    # state it shows instructional text instead of an empty box, and a
    # double-click there emits load_requested rather than doing anything
    # itself. Loading real sample audio means a live SDS transfer over a
    # SEPARATE MIDI connection (the Transfer Dashboard's own
    # SamplerController - see program_editor_window.py's
    # _load_sample_waveform) that can legitimately take minutes and, by
    # design, freezes the rest of the app while it runs - this widget only
    # ever asks for that to happen and displays the result; it has no idea
    # how the data actually arrives.
    load_requested = Signal()
    # which marker moved ("start"/"loop_start"/"loop_end"/"end"), then the
    # four current frame positions - emitted once per drag, on release, not
    # continuously while dragging (same "redraw live, write on release"
    # split envelope_graph.py's own knobs use for their hardware writes)
    marker_committed = Signal(str, int, int, int, int)
    # start, loop_start, loop_end, end - emitted on load and on every drag
    # step (unlike marker_committed, continuously, not just on release) so
    # a numeric readout can track a drag live. The canvas has no room for
    # per-marker text without markers overlapping when they're close
    # together (or, in demo mode with an all-zero sample header, sitting
    # exactly on top of each other) - see program_editor_window.py's
    # marker value labels, which are what this actually feeds.
    markers_changed = Signal(int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        # fixed height, not "fill whatever space the tab gives it" - a
        # waveform doesn't get more useful taller past a certain point, and
        # letting it expand made it eat the entire tab, mockup's own
        # svg was a similar fixed 140px for the same reason
        self.setFixedHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self._samples = None  # None = placeholder state, nothing loaded yet
        self._envelope = []
        self._frame_count = 0
        self._markers = {name: 0 for name in _MARKER_ORDER}
        self._dragging = None
        self._loading = False

    def has_waveform(self):
        return self._samples is not None

    def markers(self):
        return dict(self._markers)

    def set_loading(self, loading):
        self._loading = loading
        self.update()

    def clear(self):
        # back to the placeholder state - used when the user selects a
        # sample this session hasn't loaded audio for yet (see
        # program_editor_window.py's _on_sample_selected)
        self._samples = None
        self._envelope = []
        self._frame_count = 0
        self._dragging = None
        self.update()

    def set_waveform(self, samples, start, loop_start, loop_end, end):
        self._samples = samples
        self._frame_count = len(samples)
        self._markers = {
            "start": start,
            "loop_start": loop_start,
            "loop_end": loop_end,
            "end": end,
        }
        self._rebuild_envelope()
        self.update()
        self._emit_markers_changed()

    def _emit_markers_changed(self):
        m = self._markers
        self.markers_changed.emit(m["start"], m["loop_start"], m["loop_end"], m["end"])

    def resizeEvent(self, event):
        if self._samples is not None:
            self._rebuild_envelope()
        super().resizeEvent(event)

    def _rebuild_envelope(self):
        self._envelope = build_envelope(self._samples, self.width())

    def _x_for(self, name):
        return x_for_frame(self._markers[name], self.width(), self._frame_count)

    def _marker_colors(self, palette):
        # start/end are the sample's own boundaries (matches the mockup's
        # neutral "start"/"end" legend colour); loop start/end share a
        # colour since they're one region's two edges, not two independent
        # things - reuses the keygroup range bar's teal rather than
        # inventing a new theme token for it (see keygroup_range_bar.py's
        # own keygroup_color(2), a validated categorical teal in both themes)
        boundary = QColor(palette["text_disabled"])
        loop = QColor(palette["keygroup_color_3"])
        return {
            "start": boundary,
            "end": boundary,
            "loop_start": loop,
            "loop_end": loop,
        }

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = theme.current_palette()

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillRect(rect, QColor(palette["bg_input"]))
        painter.setPen(QColor(palette["border"]))
        painter.drawRoundedRect(rect, _BORDER_RADIUS, _BORDER_RADIUS)

        if self._samples is None:
            painter.setPen(QColor(palette["text_disabled"]))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                _LOADING_TEXT if self._loading else _PLACEHOLDER_TEXT,
            )
            return

        mid_y = self.height() / 2
        half = self.height() / 2 - 6
        pen = QPen(QColor(palette["accent"]))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        for x, (lo, hi) in enumerate(self._envelope):
            y_lo = mid_y - (hi / 32768) * half
            y_hi = mid_y - (lo / 32768) * half
            painter.drawLine(int(x), int(y_lo), int(x), int(y_hi) + 1)

        colors = self._marker_colors(palette)
        for name in _MARKER_ORDER:
            x = self._x_for(name)
            pen = QPen(colors[name])
            pen.setWidthF(1.5)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            painter.setBrush(colors[name])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPolygon(QPolygonF([
                QPointF(x - _HANDLE_SIZE, 0),
                QPointF(x + _HANDLE_SIZE, 0),
                QPointF(x, _HANDLE_SIZE * 1.6),
            ]))

    def _marker_near(self, x):
        best = None
        best_dist = None
        for name in _MARKER_ORDER:
            dist = abs(self._x_for(name) - x)
            if best_dist is None or dist < best_dist:
                best, best_dist = name, dist
        if best_dist is not None and best_dist <= _HIT_RADIUS_PX:
            return best
        return None

    def mouseDoubleClickEvent(self, event):
        if self._samples is None and not self._loading:
            self.load_requested.emit()
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if self._samples is None:
            return
        self._dragging = self._marker_near(event.position().x())

    def mouseMoveEvent(self, event):
        if self._dragging is None:
            return
        index = _MARKER_ORDER.index(self._dragging)
        frame = frame_for_x(event.position().x(), self.width(), self._frame_count)
        self._markers[self._dragging] = clamp_marker(
            _MARKER_ORDER, index, frame, self._markers, self._frame_count
        )
        self.update()
        self._emit_markers_changed()

    def mouseReleaseEvent(self, event):
        if self._dragging is None:
            return
        which = self._dragging
        self._dragging = None
        m = self._markers
        self.marker_committed.emit(
            which, m["start"], m["loop_start"], m["loop_end"], m["end"]
        )
