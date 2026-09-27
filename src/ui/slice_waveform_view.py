from PySide6.QtWidgets import QApplication, QMenu, QSizePolicy, QWidget
from PySide6.QtGui import QCursor, QPainter, QPen, QColor, QPolygonF
from PySide6.QtCore import Qt, QPointF, QRectF, Signal

from core.sample_slicing import find_nearest_zero_crossing
from core.sample_slicing import slice_bounds as _slice_bounds_fn
from ui import theme
from ui.waveform_view import _MIN_ZOOM, build_envelope, frame_for_x, x_for_frame

# Loop-editor look-and-feel constants mirrored from waveform_view.py (kept
# separate rather than imported - this widget's marker model is genuinely
# different, see the class docstring below, and these are small enough that
# duplicating them costs less than coupling the two together)
_BORDER_RADIUS = 4
_HIT_RADIUS_PX = 6
_HANDLE_SIZE = 5
_FINE_DRAG_DIVISOR = 8
_ZOOM_STEP = 1.6

# how far outward (in frames) to search for a zero crossing when snapping a
# newly-placed or just-dragged marker/edge - generous, since a search that
# comes up empty just falls back to the unsnapped frame (see
# core.sample_slicing.find_nearest_zero_crossing)
_ZERO_CROSSING_SEARCH_RADIUS = 4000

_LABEL_Y = 16  # baseline for the per-slice number label, below the marker
# handle triangles (which occupy roughly the first _HANDLE_SIZE * 1.6 px)

_PLACEHOLDER_TEXT = "No audio loaded"


class SliceWaveformView(QWidget):
    # The Slice Editor's own waveform canvas - deliberately NOT a reuse of
    # WaveformView (Samples tab): that widget's whole marker model is a
    # FIXED set of four named markers (start/loop_start/loop_end/end) that
    # push each other on drag (see its own push_marker), one persistent
    # widget for the Samples tab's whole lifetime, and it also owns a lot
    # of Samples-tab-specific stuff (header-only mode, progressive live-
    # capture fill, loop-enabled greying) that has no meaning here. This
    # widget's model is two edge handles (start/end - "shave off dead
    # space") plus an ARBITRARY-LENGTH, user-managed list of interior slice
    # markers, added/removed one at a time, never pushed past a neighbour -
    # a drag just clamps at whichever neighbour (marker or edge) is
    # nearest, since there's no meaningful "shove the next slice marker
    # along" behaviour the way loop-marker pushing has. Reuses
    # waveform_view.py's free, Qt-independent helpers (build_envelope,
    # frame_for_x, x_for_frame, _MIN_ZOOM) rather than re-deriving that
    # math, but owns its own paintEvent/mouse handling entirely.
    #
    # Always constructed with the sample's audio ALREADY fully loaded
    # (see slice_editor_window.py - the "Slice Editor" button itself is
    # only enabled once has_waveform() is true on the Samples tab), so
    # unlike WaveformView there's no header-only or progressive-fill mode
    # to support here at all.
    markers_changed = Signal()  # start, end, or the marker list itself changed
    view_changed = Signal(int, int, int)  # view_start, view_length, frame_count

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self._samples = []
        self._envelope = []
        self._frame_count = 0
        self._start = 0
        self._end = 0
        self._markers = []  # sorted, strictly within (start, end)
        self._zoom = _MIN_ZOOM
        self._view_start = 0
        self._dragging = None  # "start" / "end" / ("marker", index) / None
        self._drag_anchor_x = 0.0
        self._drag_value = 0.0  # float accumulator, same reasoning as WaveformView
        self._fine_active = False
        self._warp_anchor_global = None

    # --- state -----------------------------------------------------------

    def has_waveform(self):
        return self._frame_count > 0

    def frame_count(self):
        return self._frame_count

    def start(self):
        return self._start

    def end(self):
        return self._end

    def slice_markers(self):
        return list(self._markers)

    def slice_count(self):
        return len(self._markers) + 1

    def set_waveform(self, samples, start, end, markers=None):
        """Full (re)load - resets zoom/pan, same as WaveformView.set_waveform
        for a genuinely new sample (there's no "preserve the view" case
        here, since a SliceWaveformView is only ever shown one sample for
        its whole lifetime - the window that owns it gets recreated per
        sample instead of reused).
        """
        self._samples = samples
        self._frame_count = len(samples)
        self._start = max(0, min(self._frame_count - 1, start))
        self._end = max(self._start, min(self._frame_count - 1, end))
        self._markers = []
        if markers:
            self._set_markers_raw(markers)
        self._zoom = _MIN_ZOOM
        self._view_start = 0
        self._dragging = None
        self._rebuild_envelope()
        self.update()
        self._emit_markers_changed()
        self._emit_view_changed()

    def _set_markers_raw(self, markers):
        # sorts/dedupes/filters to strictly within (start, end) - the same
        # defensive filter core.sample_slicing.slice_bounds applies, kept
        # in sync deliberately (see set_markers' own docstring)
        self._markers = sorted({m for m in markers if self._start < m < self._end})

    def set_markers(self, markers):
        """Replace every interior marker at once - used by the "Equal
        Slices" quick-start action (see slice_editor_window.py). Silently
        drops anything outside (start, end), same rule add_marker enforces
        for a single marker, so an equal-slice request computed against a
        stale start/end can't leave a bogus marker behind.
        """
        self._set_markers_raw(markers)
        self.update()
        self._emit_markers_changed()

    def add_marker(self, frame):
        """Snaps *frame* to the nearest zero crossing, then adds it as a
        new interior marker if that's still strictly within (start, end)
        and isn't already present. Returns the actual frame added, or None
        if the request was rejected (out of range, or landed exactly on an
        existing marker/edge after snapping).
        """
        snapped = find_nearest_zero_crossing(
            self._samples, frame, _ZERO_CROSSING_SEARCH_RADIUS
        )
        if not (self._start < snapped < self._end) or snapped in self._markers:
            return None
        self._markers.append(snapped)
        self._markers.sort()
        self.update()
        self._emit_markers_changed()
        return snapped

    def remove_marker(self, frame):
        if frame in self._markers:
            self._markers.remove(frame)
            self.update()
            self._emit_markers_changed()

    def _emit_markers_changed(self):
        self.markers_changed.emit()

    # --- zoom / pan (same shape as WaveformView's own) --------------------

    def _view_length(self):
        if self._frame_count == 0:
            return 0
        return max(
            1, min(self._frame_count, int(round(self._frame_count / self._zoom)))
        )

    def _max_zoom(self):
        return max(_MIN_ZOOM, float(self._frame_count))

    def _clamp_view_start(self):
        max_start = max(0, self._frame_count - self._view_length())
        self._view_start = max(0, min(max_start, self._view_start))

    def _emit_view_changed(self):
        self.view_changed.emit(self._view_start, self._view_length(), self._frame_count)

    def set_view_start(self, start):
        if self._frame_count == 0:
            return
        self._view_start = start
        self._clamp_view_start()
        self._rebuild_envelope()
        self.update()
        self._emit_view_changed()

    def set_zoom(self, zoom, anchor_frame=None):
        if self._frame_count == 0:
            return
        old_view_length = self._view_length()
        if anchor_frame is None:
            anchor_frame = self._view_start + old_view_length / 2
        anchor_fraction = (
            (anchor_frame - self._view_start) / (old_view_length - 1)
            if old_view_length > 1
            else 0.0
        )
        self._zoom = max(_MIN_ZOOM, min(self._max_zoom(), zoom))
        new_view_length = self._view_length()
        self._view_start = int(
            round(anchor_frame - anchor_fraction * (new_view_length - 1))
        )
        self._clamp_view_start()
        self._rebuild_envelope()
        self.update()
        self._emit_view_changed()

    def zoom_in(self, anchor_frame=None):
        self.set_zoom(self._zoom * _ZOOM_STEP, anchor_frame)

    def zoom_out(self, anchor_frame=None):
        self.set_zoom(self._zoom / _ZOOM_STEP, anchor_frame)

    def reset_zoom(self):
        self.set_zoom(_MIN_ZOOM)

    def resizeEvent(self, event):
        if self._frame_count:
            self._rebuild_envelope()
        super().resizeEvent(event)

    def _rebuild_envelope(self):
        if self._frame_count == 0:
            self._envelope = []
            return
        view_start = self._view_start
        view_length = self._view_length()
        visible = self._samples[view_start : view_start + view_length]
        self._envelope = build_envelope(visible, self.width())

    # --- coordinate helpers -------------------------------------------------

    def _x_for(self, frame):
        return x_for_frame(frame, self.width(), self._view_start, self._view_length())

    def _frame_for(self, x):
        return frame_for_x(x, self.width(), self._view_start, self._view_length())

    def _visible(self, frame):
        return self._view_start <= frame <= self._view_start + self._view_length() - 1

    # --- rendering -----------------------------------------------------------

    def _slice_index_of(self, frame):
        # which slice band [0, len(markers)] a frame falls in
        index = 0
        for m in self._markers:
            if frame < m:
                break
            index += 1
        return index

    def _zone_color(self, frame, palette):
        if frame < self._start or frame > self._end:
            return QColor(palette["text_disabled"])
        band = self._slice_index_of(frame)
        return QColor(
            palette["accent"] if band % 2 == 0 else palette["keygroup_color_3"]
        )

    def _draw_zero_crossing_line(self, painter, palette):
        mid_y = self.height() / 2
        pen = QPen(QColor(palette["border"]))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawLine(QPointF(0, mid_y), QPointF(self.width(), mid_y))

    def _draw_marker_line(self, painter, frame, color):
        if not self._visible(frame):
            return
        x = self._x_for(frame)
        pen = QPen(color)
        pen.setWidthF(1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(x - _HANDLE_SIZE, 0),
                    QPointF(x + _HANDLE_SIZE, 0),
                    QPointF(x, _HANDLE_SIZE * 1.6),
                ]
            )
        )

    def _draw_slice_labels(self, painter, palette):
        painter.setPen(QColor(palette["text"]))
        for slice_number, (slice_start, slice_end) in enumerate(
            self.slice_bounds(), start=1
        ):
            mid_frame = (slice_start + slice_end) // 2
            if not self._visible(mid_frame):
                continue
            x = self._x_for(mid_frame)
            painter.drawText(
                QRectF(x - 20, _LABEL_Y - 12, 40, 16),
                Qt.AlignmentFlag.AlignCenter,
                str(slice_number),
            )

    def slice_bounds(self):
        """[(slice_start, slice_end), ...] for the CURRENT start/end/marker
        state - what slice_editor_window.py actually reads at export time,
        rather than re-deriving the same boundaries a second way.
        """
        return _slice_bounds_fn(self._start, self._end, self._markers)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = theme.current_palette()

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillRect(rect, QColor(palette["bg_input"]))
        painter.setPen(QColor(palette["border"]))
        painter.drawRoundedRect(rect, _BORDER_RADIUS, _BORDER_RADIUS)

        if self._frame_count == 0:
            painter.setPen(QColor(palette["text_disabled"]))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, _PLACEHOLDER_TEXT
            )
            return

        self._draw_zero_crossing_line(painter, palette)

        view_start = self._view_start
        view_length = self._view_length()
        if 0 < view_length <= self.width():
            mid_y = self.height() / 2
            half = self.height() / 2 - 6
            pen = QPen()
            pen.setWidthF(1.0)
            current_color = None
            prev_point = None
            for frame in range(view_start, view_start + view_length):
                x = self._x_for(frame)
                y = mid_y - (self._samples[frame] / 32768) * half
                point = QPointF(x, y)
                color = self._zone_color(frame, palette)
                if color != current_color:
                    pen.setColor(color)
                    painter.setPen(pen)
                    current_color = color
                if prev_point is not None:
                    painter.drawLine(prev_point, point)
                prev_point = point
        else:
            mid_y = self.height() / 2
            half = self.height() / 2 - 6
            pen = QPen()
            pen.setWidthF(1.0)
            current_color = None
            for x, (lo, hi) in enumerate(self._envelope):
                frame = frame_for_x(x, self.width(), view_start, view_length)
                color = self._zone_color(frame, palette)
                if color != current_color:
                    pen.setColor(color)
                    painter.setPen(pen)
                    current_color = color
                y_lo = mid_y - (hi / 32768) * half
                y_hi = mid_y - (lo / 32768) * half
                painter.drawLine(int(x), int(y_lo), int(x), int(y_hi) + 1)

        boundary_color = QColor(palette["keygroup_color_2"])
        marker_color = QColor(palette["keygroup_color_7"])
        self._draw_marker_line(painter, self._start, boundary_color)
        self._draw_marker_line(painter, self._end, boundary_color)
        for m in self._markers:
            self._draw_marker_line(painter, m, marker_color)

        self._draw_slice_labels(painter, palette)

    # --- mouse handling ------------------------------------------------------

    def _hit_targets(self):
        # (kind, key, x) for every draggable/clickable thing currently
        # visible - key is None for "start"/"end", the marker's own frame
        # for an interior one (unique by construction: markers are always
        # kept strictly ordered and distinct, see _set_markers_raw/
        # add_marker)
        targets = []
        if self._visible(self._start):
            targets.append(("start", None, self._x_for(self._start)))
        if self._visible(self._end):
            targets.append(("end", None, self._x_for(self._end)))
        for m in self._markers:
            if self._visible(m):
                targets.append(("marker", m, self._x_for(m)))
        return targets

    def _nearest_hit_target(self, x):
        best = None
        best_dist = None
        for kind, key, target_x in self._hit_targets():
            dist = abs(target_x - x)
            if dist <= _HIT_RADIUS_PX and (best_dist is None or dist < best_dist):
                best = (kind, key)
                best_dist = dist
        return best

    def mouseDoubleClickEvent(self, event):
        if self._frame_count == 0:
            return
        x = event.position().x()
        if self._nearest_hit_target(x) is not None:
            return  # double-clicking an existing handle isn't "add a marker"
        frame = self._frame_for(x)
        if self._start < frame < self._end:
            self.add_marker(frame)
        # no super() call - unlike WaveformView (which forwards for a
        # placeholder-state load_requested to still propagate normally),
        # there's nothing else here that depends on QWidget's own default
        # double-click handling

    def mousePressEvent(self, event):
        if self._frame_count == 0:
            return
        x = event.position().x()
        target = self._nearest_hit_target(x)
        if target is None:
            return
        kind, key = target
        if event.button() == Qt.MouseButton.RightButton:
            if kind == "marker":
                self._show_marker_context_menu(key, event.globalPosition().toPoint())
            return
        self._dragging = target
        self._drag_anchor_x = x
        self._drag_value = float(self._start if kind == "start" else
                                  self._end if kind == "end" else key)

    def _show_marker_context_menu(self, frame, global_pos):
        menu = QMenu(self)
        delete_action = menu.addAction("Delete Slice Marker")
        chosen = menu.exec(global_pos)
        if chosen == delete_action:
            self.remove_marker(frame)

    def _drag_bounds(self):
        # (lower, upper) frame bounds for whatever's currently being
        # dragged - a single clamp, never a push: there's no meaningful
        # "shove the next slice marker along" behaviour here (see the
        # class docstring), a drag just can't cross its nearest neighbour
        kind, key = self._dragging
        if kind == "start":
            upper = (min(self._markers) if self._markers else self._end) - 1
            return 0, max(0, upper)
        if kind == "end":
            lower = (max(self._markers) if self._markers else self._start) + 1
            return min(self._frame_count - 1, lower), self._frame_count - 1
        # interior marker: strictly between its two neighbours (edges count)
        idx = self._markers.index(key)
        lower = (self._markers[idx - 1] if idx > 0 else self._start) + 1
        upper = (
            self._markers[idx + 1] if idx < len(self._markers) - 1 else self._end
        ) - 1
        return lower, upper

    def mouseMoveEvent(self, event):
        if self._dragging is None:
            return
        fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if fine and not self._fine_active:
            self._enter_fine_drag()
        elif not fine and self._fine_active:
            self._exit_fine_drag()

        if self._fine_active:
            current = event.globalPosition()
            anchor = self._warp_anchor_global
            if (round(current.x()), round(current.y())) == (
                round(anchor.x()),
                round(anchor.y()),
            ):
                return
            dx = (current.x() - anchor.x()) / _FINE_DRAG_DIVISOR
            QCursor.setPos(anchor)
        else:
            x = event.position().x()
            dx = x - self._drag_anchor_x
            self._drag_anchor_x = x

        view_length = self._view_length()
        frames_per_px = (view_length - 1) / max(1, self.width() - 1)
        self._drag_value += dx * frames_per_px
        frame = int(round(self._drag_value))

        lower, upper = self._drag_bounds()
        clamped = max(lower, min(upper, frame))
        if clamped != frame:
            self._drag_value = clamped  # resync accumulator only at the true bound

        kind, key = self._dragging
        if kind == "start":
            self._start = clamped
        elif kind == "end":
            self._end = clamped
        else:
            idx = self._markers.index(key)
            self._markers[idx] = clamped
            self._dragging = ("marker", clamped)  # key tracks the live position

        self.update()
        self._emit_markers_changed()

    def _enter_fine_drag(self):
        self._fine_active = True
        self._warp_anchor_global = QCursor.pos()
        QApplication.setOverrideCursor(Qt.CursorShape.BlankCursor)

    def _exit_fine_drag(self):
        self._fine_active = False
        self._warp_anchor_global = None
        QApplication.restoreOverrideCursor()

    def mouseReleaseEvent(self, event):
        if self._dragging is None:
            return
        if self._fine_active:
            self._exit_fine_drag()
        kind, key = self._dragging
        self._dragging = None
        # snap to the nearest zero crossing only on release (not live during
        # the drag) - continuous snapping would make the marker visibly
        # jump around mid-drag instead of tracking the cursor smoothly, the
        # same "redraw live, commit on release" split WaveformView's own
        # marker_committed already uses for its hardware writes
        if kind == "start":
            snapped = find_nearest_zero_crossing(
                self._samples, self._start, _ZERO_CROSSING_SEARCH_RADIUS
            )
            upper = (min(self._markers) if self._markers else self._end) - 1
            self._start = max(0, min(max(0, upper), snapped))
        elif kind == "end":
            snapped = find_nearest_zero_crossing(
                self._samples, self._end, _ZERO_CROSSING_SEARCH_RADIUS
            )
            lower = (max(self._markers) if self._markers else self._start) + 1
            self._end = min(self._frame_count - 1, max(lower, snapped))
        else:
            idx = self._markers.index(key)
            lower = (self._markers[idx - 1] if idx > 0 else self._start) + 1
            upper = (
                self._markers[idx + 1]
                if idx < len(self._markers) - 1
                else self._end
            ) - 1
            snapped = find_nearest_zero_crossing(
                self._samples, key, _ZERO_CROSSING_SEARCH_RADIUS
            )
            self._markers[idx] = max(lower, min(upper, snapped))
        self.update()
        self._emit_markers_changed()

    def hideEvent(self, event):
        if self._fine_active:
            self._exit_fine_drag()
        super().hideEvent(event)

    def wheelEvent(self, event):
        if self._frame_count == 0:
            return
        angle = event.angleDelta()

        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = angle.x() if angle.x() != 0 else angle.y()
            if delta == 0:
                return
            anchor_frame = self._frame_for(event.position().x())
            if delta > 0:
                self.zoom_in(anchor_frame)
            else:
                self.zoom_out(anchor_frame)
            event.accept()
            return

        if angle.x() != 0:
            delta = angle.x()
        elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier and angle.y() != 0:
            delta = angle.y()
        else:
            return

        view_length = self._view_length()
        pan_frames = int(-delta / 120 * max(1, view_length // 10))
        self.set_view_start(self._view_start + pan_frames)
        event.accept()
