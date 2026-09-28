from PySide6.QtWidgets import QApplication, QMenu, QSizePolicy, QWidget
from PySide6.QtGui import QCursor, QPainter, QPen, QColor, QPolygonF
from PySide6.QtCore import Qt, QEvent, QPointF, QRectF, QTimer, Signal

from core import debug_log
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
    # a plain (non-handle) single click settled long enough to not be the
    # first half of a double-click - (slice_start, slice_end), inclusive
    slice_preview_requested = Signal(int, int)

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
        # click-to-preview: a plain click in empty space (no handle nearby)
        # schedules a preview instead of firing it immediately, deferred by
        # Qt's own double-click interval so that double-clicking to add a
        # marker (mouseDoubleClickEvent below) never also plays a stray
        # blip of audio from the single click that precedes it - the
        # standard single-vs-double-click disambiguation pattern
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._fire_preview)
        self._pending_preview_frame = None
        # driven by SlicePreviewPlayer.position_changed/finished (wired in
        # slice_editor_window.py) - None means nothing is currently playing
        self._playhead_frame = None

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

    # --- playhead (click-to-preview visual feedback) ------------------------

    def set_playhead(self, frame):
        self._playhead_frame = frame
        self.update()

    def clear_playhead(self):
        if self._playhead_frame is not None:
            self._playhead_frame = None
            self.update()

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

        self._draw_playhead_highlight(painter, palette)
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
        self._draw_playhead_line(painter, palette)

    def _draw_playhead_highlight(self, painter, palette):
        # a translucent tint across whichever slice is currently sounding -
        # drawn first (under the waveform/markers), see _draw_playhead_line
        # below for the moving line itself. No-op unless something's
        # actually playing (see clear_playhead)
        if self._playhead_frame is None:
            return
        bounds = self.slice_bounds()
        index = self._slice_index_of(self._playhead_frame)
        if not (0 <= index < len(bounds)):
            return
        slice_start, slice_end = bounds[index]
        view_start = self._view_start
        view_end = view_start + self._view_length() - 1
        visible_start = max(slice_start, view_start)
        visible_end = min(slice_end, view_end)
        if visible_start > visible_end:
            return  # the playing slice has scrolled/zoomed fully offscreen
        x1 = self._x_for(visible_start)
        x2 = self._x_for(visible_end)
        color = QColor(palette["accent"])
        color.setAlpha(50)
        painter.fillRect(QRectF(x1, 0, max(1.0, x2 - x1), self.height()), color)

    def _draw_playhead_line(self, painter, palette):
        # a solid, undashed line - deliberately distinct from the dashed
        # start/end/marker lines (_draw_marker_line) so "this is where
        # playback currently is" never reads as just another marker
        if self._playhead_frame is None or not self._visible(self._playhead_frame):
            return
        x = self._x_for(self._playhead_frame)
        pen = QPen(QColor(palette["text_bright"]))
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))

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
        # this whole widget is new/unshipped and does materially more
        # per-pixel hit-testing/index math than WaveformView's own longer-
        # proven equivalent - wrapped the same way AGENTS.md describes that
        # one being fixed after a real, hard-to-repro user bug (see its
        # "Diagnosing a load that silently does nothing" section): an
        # uncaught exception from a Qt mouse handler is otherwise invisible
        # in a packaged build with no console, not just here but for every
        # mouse handler below
        try:
            self._cancel_pending_preview()
            if self._frame_count == 0:
                return
            x = event.position().x()
            target = self._nearest_hit_target(x)
            if target is not None:
                kind, key = target
                # double-clicking a slice marker deletes it - a quicker
                # alternative to the right-click context menu
                # (_show_marker_context_menu), not a replacement for it.
                # start/end aren't markers and can't be deleted this way (or
                # any way - they're the region's own edges, not a slice
                # boundary), so double-clicking one does nothing, same as
                # before this existed.
                if kind == "marker":
                    self.remove_marker(key)
                return
            frame = self._frame_for(x)
            if self._start < frame < self._end:
                self.add_marker(frame)
            # no super() call - unlike WaveformView (which forwards for a
            # placeholder-state load_requested to still propagate normally),
            # there's nothing else here that depends on QWidget's own
            # default double-click handling
        except Exception:
            debug_log.get_logger().error(
                "SliceWaveformView.mouseDoubleClickEvent: unexpected error",
                exc_info=True,
            )

    def mousePressEvent(self, event):
        try:
            if self._frame_count == 0:
                return
            x = event.position().x()
            target = self._nearest_hit_target(x)
            if target is None:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._schedule_preview_click(self._frame_for(x))
                return
            kind, key = target
            if event.button() == Qt.MouseButton.RightButton:
                if kind == "marker":
                    self._show_marker_context_menu(
                        key, event.globalPosition().toPoint()
                    )
                return
            self._cancel_pending_preview()
            self._dragging = target
            self._drag_anchor_x = x
            self._drag_value = float(self._start if kind == "start" else
                                      self._end if kind == "end" else key)
        except Exception:
            debug_log.get_logger().error(
                "SliceWaveformView.mousePressEvent: unexpected error",
                exc_info=True,
            )
            self._dragging = None

    # --- click-to-preview --------------------------------------------------

    def _schedule_preview_click(self, frame):
        # only meaningful inside [start, end] - clicking the greyed-out
        # dead space either side isn't part of any slice
        if not (self._start <= frame <= self._end):
            return
        self._pending_preview_frame = frame
        # half of Qt's own doubleClickInterval() - a straight
        # doubleClickInterval() delay (the original, safest choice) felt
        # too sluggish for quickly scrubbing through slices, per the
        # user's own request. Still tied to the platform's own interval
        # rather than a made-up constant, just at half of it - narrows, but
        # doesn't eliminate, the window an unusually slow double-click
        # could land outside of and still trigger one stray preview blip
        # before its marker gets added (see this timer's own __init__
        # comment for the full reasoning on why this exists at all)
        self._preview_timer.start(QApplication.doubleClickInterval() // 2)

    def _cancel_pending_preview(self):
        self._preview_timer.stop()

    def _fire_preview(self):
        frame = self._pending_preview_frame
        bounds = self.slice_bounds()
        index = self._slice_index_of(frame)
        if 0 <= index < len(bounds):
            slice_start, slice_end = bounds[index]
            self.slice_preview_requested.emit(slice_start, slice_end)

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
        try:
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
        except Exception:
            # a stuck self._dragging would otherwise keep swallowing every
            # further mouse-move on this widget with no visible cause -
            # logging AND clearing it here means at worst a drag ends
            # early, not a widget that silently stops responding to the
            # mouse for the rest of the session
            debug_log.get_logger().error(
                "SliceWaveformView.mouseMoveEvent: unexpected error",
                exc_info=True,
            )
            if self._fine_active:
                self._exit_fine_drag()
            self._dragging = None

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
        try:
            # snap to the nearest zero crossing only on release (not live
            # during the drag) - continuous snapping would make the marker
            # visibly jump around mid-drag instead of tracking the cursor
            # smoothly, the same "redraw live, commit on release" split
            # WaveformView's own marker_committed already uses for its
            # hardware writes
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
        except Exception:
            # self._dragging is already cleared above regardless of this -
            # logged so a snap-on-release failure (e.g. a marker removed
            # out from under a drag) doesn't just vanish
            debug_log.get_logger().error(
                "SliceWaveformView.mouseReleaseEvent: unexpected error",
                exc_info=True,
            )

    def hideEvent(self, event):
        if self._fine_active:
            self._exit_fine_drag()
        self._cancel_pending_preview()
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

    def event(self, event):
        # real macOS trackpad pinch-to-zoom arrives as its own native
        # gesture event, not a modified wheel event - same reasoning and
        # same mechanism as WaveformView._handle_pinch_zoom/event() on the
        # Samples tab (Qt has no dedicated nativeGestureEvent() virtual to
        # override, so this is the documented way to catch it)
        if (
            event.type() == QEvent.Type.NativeGesture
            and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture
        ):
            self._handle_pinch_zoom(event)
            return True
        return super().event(event)

    def _handle_pinch_zoom(self, event):
        if self._frame_count == 0:
            return
        # value() is the incremental scale change for this one event, not
        # an absolute zoom level - multiplies into the current zoom rather
        # than replacing it, same as repeated zoom_in()/zoom_out() calls
        factor = max(0.1, 1.0 + event.value())
        anchor_frame = self._frame_for(event.position().x())
        self.set_zoom(self._zoom * factor, anchor_frame)
