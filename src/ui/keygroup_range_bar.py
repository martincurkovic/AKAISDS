from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtGui import QPainter, QColor, QPainterPath, QPen
from PySide6.QtCore import Qt, QRectF, QPointF
from ui import theme

_NOTE_COUNT = 128  # MIDI notes 0-127 - the full addressable keyboard range
_SEGMENT_GAP = 2  # px of surface-color gap separating touching segments/runs
_CORNER_RADIUS = 3
_HATCH_SPACING = 6  # px between diagonal overlap-hatch lines
_HATCH_WIDTH = 2  # px stroke width of each hatch line


def keygroup_color(index):
    # shared with the keygroup list rows (program_editor_window.py) so a
    # keygroup's swatch always matches its segment on the bar
    palette = theme.current_palette()
    colors = [palette[f"keygroup_color_{n}"] for n in range(1, 9)]
    return QColor(colors[index % len(colors)])


class KeygroupRangeBar(QWidget):
    # Horizontal strip showing where each keygroup's note range sits across
    # the full keyboard, one colored segment per keygroup in order - mirrors
    # the "range-bar" in test_scripts/akaisds_mockup_v2.html. Segment colors
    # are a fixed, dataviz-validated categorical order (theme.py's
    # keygroup_color_1..8) - identity also comes from position/order and the
    # note-range text in the list beside it, never from hue alone.
    #
    # Keygroups CAN legitimately overlap in note range (e.g. layering two
    # sounds across the same keys), so overlapping runs are drawn as a
    # diagonal hatch blending every color involved, rather than one keygroup
    # silently painting over another.
    def __init__(self, parent=None):
        super().__init__(parent)
        self._ranges = []  # list of (lo, hi) inclusive MIDI note numbers
        self.setFixedHeight(16)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

    def set_ranges(self, ranges):
        self._ranges = list(ranges)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        palette = theme.current_palette()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        track_path = QPainterPath()
        track_path.addRoundedRect(rect, _CORNER_RADIUS, _CORNER_RADIUS)
        painter.fillPath(track_path, QColor(palette["bg_input"]))
        painter.setPen(QColor(palette["border"]))
        painter.drawPath(track_path)

        if not self._ranges:
            return

        # clip to the track's rounded silhouette so only the bar's true outer
        # ends read as rounded - runs stay square where they meet a
        # neighbour, per the usual stacked-segment convention
        painter.setClipPath(track_path)
        width = rect.width()

        spans = []  # (x_start, x_end, keygroup index)
        for index, (lo, hi) in enumerate(self._ranges):
            lo = max(0, min(int(lo), _NOTE_COUNT - 1))
            hi = max(lo, min(int(hi), _NOTE_COUNT - 1))
            x_start = rect.left() + (lo / _NOTE_COUNT) * width
            x_end = rect.left() + ((hi + 1) / _NOTE_COUNT) * width
            spans.append((x_start, x_end, index))

        # sweep-line: split the bar at every span boundary, then paint each
        # resulting run once, according to which keygroup(s) are active
        # across it (one -> flat fill, two+ -> overlap hatch)
        boundaries = sorted({round(x, 3) for s in spans for x in (s[0], s[1])})
        for left_x, right_x in zip(boundaries, boundaries[1:]):
            mid_x = (left_x + right_x) / 2
            active = [idx for (x0, x1, idx) in spans if x0 <= mid_x < x1]
            if not active:
                continue
            run_rect = QRectF(
                left_x + _SEGMENT_GAP / 2,
                rect.top(),
                max(0.0, (right_x - left_x) - _SEGMENT_GAP),
                rect.height(),
            )
            if len(active) == 1:
                painter.fillRect(run_rect, keygroup_color(active[0]))
            else:
                self._paint_overlap(painter, run_rect, active)

    def _paint_overlap(self, painter, rect, active_indices):
        painter.fillRect(rect, keygroup_color(active_indices[0]))
        stripe_colors = [keygroup_color(i) for i in active_indices[1:]]

        painter.save()
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
        # 45-degree lines, offset to start before the rect so the first
        # stripe's corner is still a full diagonal rather than a sliver
        x = rect.left() - rect.height()
        stripe_index = 0
        while x < rect.right():
            pen = QPen(stripe_colors[stripe_index % len(stripe_colors)])
            pen.setWidthF(_HATCH_WIDTH)
            painter.setPen(pen)
            painter.drawLine(
                QPointF(x, rect.bottom()), QPointF(x + rect.height(), rect.top())
            )
            x += _HATCH_SPACING
            stripe_index += 1
        painter.restore()
