from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtGui import QPainter, QColor, QPainterPath
from PySide6.QtCore import QRectF
from ui import theme

_NOTE_COUNT = 128  # MIDI notes 0-127 - the full addressable keyboard range
_SEGMENT_GAP = 2  # px of surface-color gap separating touching segments
_CORNER_RADIUS = 3


class KeygroupRangeBar(QWidget):
    # Horizontal strip showing where each keygroup's note range sits across
    # the full keyboard, one colored segment per keygroup in order - mirrors
    # the "range-bar" in test_scripts/akaisds_mockup_v2.html. Segment colors
    # are a fixed, dataviz-validated categorical order (theme.py's
    # keygroup_color_1..8) - identity also comes from position/order and the
    # note-range text in the list beside it, never from hue alone.
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

    def _color_for_index(self, index):
        palette = theme.current_palette()
        colors = [palette[f"keygroup_color_{n}"] for n in range(1, 9)]
        return QColor(colors[index % len(colors)])

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
        # ends read as rounded - segments stay square where they meet a
        # neighbour, per the usual stacked-segment convention
        painter.setClipPath(track_path)
        width = rect.width()
        for index, (lo, hi) in enumerate(self._ranges):
            lo = max(0, min(int(lo), _NOTE_COUNT - 1))
            hi = max(lo, min(int(hi), _NOTE_COUNT - 1))
            x_start = rect.left() + (lo / _NOTE_COUNT) * width
            x_end = rect.left() + ((hi + 1) / _NOTE_COUNT) * width
            seg_rect = QRectF(
                x_start + _SEGMENT_GAP / 2,
                rect.top(),
                max(0.0, (x_end - x_start) - _SEGMENT_GAP),
                rect.height(),
            )
            painter.fillRect(seg_rect, self._color_for_index(index))
