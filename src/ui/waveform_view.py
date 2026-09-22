from PySide6.QtWidgets import QApplication, QWidget, QSizePolicy
from PySide6.QtGui import QCursor, QPainter, QPen, QColor, QPolygonF
from PySide6.QtCore import Qt, QEvent, QRectF, QPointF, Signal

from core import debug_log
from ui import theme

_BORDER_RADIUS = 4  # matches envelope_graph.py's own card-edge radius
_HIT_RADIUS_PX = 6  # how close a click has to land to a marker to grab it
_HANDLE_SIZE = 5  # the little triangle at the top of each marker line
_FINE_DRAG_DIVISOR = 8  # how much slower a Shift-held drag moves

_MIN_ZOOM = 1.0  # the whole sample visible at once
_ZOOM_STEP = 1.6  # multiplicative factor per wheel notch / zoom button click
# there used to be a flat _MAX_ZOOM = 500.0 ceiling here ("view can shrink
# to roughly 1/500th of the sample") - see WaveformView._max_zoom for why
# that's gone: for any sample bigger than about 500x the canvas's own
# width in frames, 500x zoom could never reach single-sample resolution
# at all, which was the actual reason dragging a marker couldn't get
# sample-accurate no matter how far zoomed in - frames_per_px in
# mouseMoveEvent never dropped below 1, so every drag pixel skipped
# several frames at once regardless of how many times you zoomed in.

# order matters: this is also the order push_marker cascades a push
# through - moving one marker past its neighbour pushes that neighbour
# (and, in turn, whichever one is next along) rather than stopping dead
# at it
_MARKER_ORDER = ("start", "loop_start", "loop_end", "end")

_PLACEHOLDER_TEXT = "Double-click to load audio waveform\n\nLoading is slow and will freeze the interface until finished"
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
    computed once per load/resize/zoom/pan instead of once per paint. The
    caller (WaveformView._rebuild_envelope) is responsible for slicing
    *samples* down to the currently visible zoom window first - this
    function has no idea zoom/pan exist.
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


def frame_for_x(x, width, view_start, view_length):
    """Inverse of x_for_frame - maps a widget-local x pixel to the absolute
    frame it represents, relative to the currently visible
    [view_start, view_start + view_length) window, not the whole sample.
    At full zoom (view_start=0, view_length=the sample's own frame count)
    this is the same mapping as before zoom/pan existed.
    """
    if width <= 1 or view_length <= 1:
        return view_start
    frac = min(1.0, max(0.0, x / (width - 1)))
    return view_start + int(round(frac * (view_length - 1)))


def x_for_frame(frame, width, view_start, view_length):
    if view_length <= 1:
        return 0.0
    return ((frame - view_start) / (view_length - 1)) * (width - 1)


def push_marker(order, index, frame, values, frame_count):
    """Moves the marker at order[index] to frame (clamped to the whole
    sample's own bounds, [0, frame_count - 1] - independent of the current
    zoom/pan window, same as a timeline editor: you can still drag a
    marker toward a position currently scrolled off-screen), pushing any
    neighbour(s) that would otherwise end up on the wrong side of it along
    too, rather than stopping dead the moment it touches one. Cascades:
    pushing one marker can in turn push the next one along too, the same
    as a Newton's cradle - dragging "end" far enough left first pushes
    loop_end out of the way, which can then push loop_start, which can
    then push start, all in one call. start <= loop_start <= loop_end <=
    end always holds in the result, the same invariant a plain "stop at
    the neighbour" clamp would also have guaranteed, just via shoving
    neighbours along instead of refusing to cross them - see AGENTS.md's
    own section on this for why a push, not a stop, is what a trim editor
    should do here.

    Returns a NEW {name: frame} dict covering every marker in *order* -
    the caller (WaveformView) is responsible for actually committing it to
    self._markers and, once the drag/edit is done, telling
    program_editor_window.py to write every field that actually changed,
    not just the one the user directly grabbed.
    """
    frame = max(0, min(frame_count - 1, frame))
    new_values = dict(values)
    new_values[order[index]] = frame

    for i in range(index - 1, -1, -1):
        if new_values[order[i]] > new_values[order[i + 1]]:
            new_values[order[i]] = new_values[order[i + 1]]
        else:
            break

    for i in range(index + 1, len(order)):
        if new_values[order[i]] < new_values[order[i - 1]]:
            new_values[order[i]] = new_values[order[i - 1]]
        else:
            break

    return new_values


class WaveformView(QWidget):
    # Loop-point editor for one sample: a fast min/max envelope plus four
    # draggable markers (start/loop start/loop end/end), horizontal
    # zoom+pan, and Shift-held fine dragging. Has nothing loaded until told
    # to (see set_waveform/set_header/clear) - in that placeholder state it
    # shows instructional text instead of an empty box, and a double-click
    # there emits load_requested rather than doing anything itself.
    #
    # Markers and audio arrive independently, and the markers alone are
    # what actually matter for editing: set_header shows and makes the
    # four markers draggable from the sample's HEADER alone (a handful of
    # fast get_parameter reads over the same connection every other tab on
    # this page uses), with no envelope trace - loading actual audio means
    # a live SDS transfer over a SEPARATE MIDI connection (the Transfer
    # Dashboard's own SamplerController - see program_editor_window.py's
    # _load_sample_waveform) that can legitimately take minutes and, by
    # design, freezes the rest of the app while it runs. A user shouldn't
    # have to sit through that just to nudge a loop point - set_waveform
    # layers the real envelope on top once (if ever) that audio arrives.
    # This widget only ever asks for that to happen (load_requested) and
    # displays whatever result shows up; it has no idea how either piece
    # of data actually arrives.
    load_requested = Signal()
    # which marker moved ("start"/"loop_start"/"loop_end"/"end"), then the
    # four current frame positions - emitted once per drag, on release, not
    # continuously while dragging (same "redraw live, write on release"
    # split envelope_graph.py's own knobs use for their hardware writes).
    # Also emitted by program_editor_window.py's marker spinboxes on commit
    # (Enter/focus-loss) - see set_marker.
    marker_committed = Signal(str, int, int, int, int)
    # start, loop_start, loop_end, end - emitted on load and on every drag/
    # spinbox step (unlike marker_committed, continuously, not just on
    # commit) so a numeric readout can track a drag live. The canvas has no
    # room for per-marker text without markers overlapping when they're
    # close together - see program_editor_window.py's marker spinboxes,
    # which are what this actually feeds.
    markers_changed = Signal(int, int, int, int)
    # view_start, view_length, frame_count - enough for an external
    # QScrollBar to size and position itself. Emitted whenever zoom or pan
    # changes, including indirectly (a new sample loading resets the view).
    view_changed = Signal(int, int, int)

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
        self._drag_anchor_x = 0.0
        self._drag_value = 0.0  # float accumulator - see mouseMoveEvent
        # click-to-cycle: which marker a press near this same stack of
        # overlapping candidates should pick NEXT - see
        # _markers_within_hit_radius/mousePressEvent. Reset whenever the
        # markers themselves get replaced wholesale (clear/set_header) so
        # a stale cycle position from a previous sample can't leak in.
        self._press_cycle_candidates = []
        self._press_cycle_index = 0
        # fine (Shift-held) mode warps the OS cursor back to a fixed point
        # every move event instead of letting it travel with the drag - see
        # mouseMoveEvent - so it never runs out of screen to move across
        # while crawling through a multi-thousand-frame fine adjustment.
        # _warp_anchor_global is that fixed point, in screen coordinates
        # (QCursor.setPos() needs global, not widget-local, coordinates).
        self._fine_active = False
        self._warp_anchor_global = None
        self._loading = False
        self._zoom = _MIN_ZOOM
        self._view_start = 0

    def has_waveform(self):
        return self._samples is not None

    def frame_count(self):
        return self._frame_count

    def markers(self):
        return dict(self._markers)

    def set_loading(self, loading):
        self._loading = loading
        self.update()

    def has_header(self):
        # markers are known (from the sample's header) and draggable, even
        # if there's no audio/envelope yet - see set_header
        return self._frame_count > 0

    def clear(self):
        # back to the placeholder state - used when the user selects a
        # sample this session has neither a header nor audio for yet (see
        # program_editor_window.py's _on_sample_selected)
        self._samples = None
        self._envelope = []
        self._frame_count = 0
        self._dragging = None
        self._press_cycle_candidates = []
        self._press_cycle_index = 0
        self._zoom = _MIN_ZOOM
        self._view_start = 0
        self.update()
        self._emit_view_changed()

    def set_header(self, frame_count, start, loop_start, loop_end, end):
        """Loop points known from the sample's header alone - no audio
        yet. Shows and makes the four markers draggable immediately
        instead of making the user wait through however long a full SDS
        dump takes (see program_editor_window.py's _load_sample_waveform)
        just to nudge a loop point. set_waveform (if/once real audio
        arrives) layers the envelope on top of whatever's already here.
        """
        self._samples = None
        self._envelope = []
        self._frame_count = frame_count
        self._markers = {
            "start": start,
            "loop_start": loop_start,
            "loop_end": loop_end,
            "end": end,
        }
        self._press_cycle_candidates = []
        self._press_cycle_index = 0
        self._zoom = _MIN_ZOOM
        self._view_start = 0
        self.update()
        self._emit_markers_changed()
        self._emit_view_changed()

    def set_waveform(self, samples, start, loop_start, loop_end, end):
        # if set_header already showed this sample's markers and the user
        # zoomed/panned while waiting for the audio to arrive, keep that
        # view instead of snapping back to fully zoomed out -
        # _load_sample_waveform only ever calls this for the sample that's
        # STILL selected (the whole window is frozen for the duration of
        # the fetch, so nothing else could have changed what this widget
        # was showing meanwhile). A genuinely new/different sample always
        # goes through clear()/set_header() first, which is what actually
        # resets the view - so preserving it here is never stale.
        # frame_count equality alone is enough proof of "still the same
        # sample" - it doesn't also require self._samples is None the way
        # it used to, now that begin_live_capture()/append_live_samples()
        # (a live SDS dump progressively filling this in - see their own
        # comments) can leave self._samples as a real, growing list by the
        # time this is called, not just None (header-only, no fetch
        # started yet).
        preserve_view = self._frame_count == len(samples)
        self._samples = samples
        self._frame_count = len(samples)
        self._markers = {
            "start": start,
            "loop_start": loop_start,
            "loop_end": loop_end,
            "end": end,
        }
        if not preserve_view:
            self._zoom = _MIN_ZOOM
            self._view_start = 0
        self._rebuild_envelope()
        self.update()
        self._emit_markers_changed()
        self._emit_view_changed()

    def begin_live_capture(self):
        """Switches from header-only (markers known, no envelope at all)
        to a progressively-filling waveform - called once right before a
        real or demo SDS dump actually starts (see
        program_editor_window.py's _load_sample_waveform), so
        append_live_samples has an empty list to grow into instead of the
        canvas showing nothing at all until the whole transfer finishes
        minutes later. Requires set_header to have already run
        (frame_count/markers known) - a no-op otherwise, same guard every
        other header-only-mode method here already uses.
        """
        if self._frame_count == 0:
            return
        self._samples = []
        self._rebuild_envelope()
        self.update()

    def append_live_samples(self, chunk):
        """Extends the in-progress waveform with newly-arrived sample
        words - see program_editor_window.py's _on_sample_chunk_received
        (real hardware, one SDS data packet's worth per call) and
        _fetch_demo_sample_audio (its own simulated progressive reveal in
        demo mode, paced the same way its progress bar already was). A
        no-op before begin_live_capture() has ever run (self._samples is
        still None then) - a stray call from a signal connection
        outliving its fetch can't corrupt a header-only display it has no
        business touching. Callers stop invoking this once set_waveform
        lands the final, complete list; nothing here enforces that on its
        own.
        """
        if self._samples is None or not chunk:
            return
        self._samples.extend(chunk)
        self._rebuild_envelope()
        self.update()

    def set_marker(self, name, frame):
        """Move one marker directly (not via a mouse drag) - what the
        marker spinboxes in program_editor_window.py call as the user
        types/steps a value. Pushes neighbours exactly like a drag (see
        push_marker); returns the moved marker's own final frame so the
        caller can snap its own displayed value to match (e.g. typing a
        value past the sample's own end) - markers_changed (emitted
        below) is what carries every OTHER marker's possibly-also-changed
        value back to the caller, since a push can move more than just
        the one named here.
        """
        if self._frame_count == 0:
            return None
        index = _MARKER_ORDER.index(name)
        self._markers = push_marker(
            _MARKER_ORDER, index, frame, self._markers, self._frame_count
        )
        self.update()
        self._emit_markers_changed()
        return self._markers[name]

    def _emit_markers_changed(self):
        m = self._markers
        self.markers_changed.emit(m["start"], m["loop_start"], m["loop_end"], m["end"])

    def _view_length(self):
        if self._frame_count == 0:
            return 0
        return max(
            1, min(self._frame_count, int(round(self._frame_count / self._zoom)))
        )

    def _max_zoom(self):
        # the zoom level at which _view_length() bottoms out at exactly 1
        # visible sample - always reachable, whatever the sample's own
        # size, unlike a flat constant (see the removed _MAX_ZOOM's own
        # comment for the bug this used to cause). frame_count itself is
        # the right ceiling: _view_length()'s own formula is
        # round(frame_count / zoom), and that reaches 1 exactly at
        # zoom == frame_count, with no benefit to allowing anything past
        # that (view_length is already floored at 1 regardless).
        return max(_MIN_ZOOM, float(self._frame_count))

    def _clamp_view_start(self):
        max_start = max(0, self._frame_count - self._view_length())
        self._view_start = max(0, min(max_start, self._view_start))

    def _emit_view_changed(self):
        self.view_changed.emit(self._view_start, self._view_length(), self._frame_count)

    def set_view_start(self, start):
        # driven by an external QScrollBar (program_editor_window.py) -
        # dragging/clicking the scrollbar pans the same way wheel-pan does.
        # Works in header-only mode too (no audio, just markers) - zooming
        # in still gives more precise dragging even with no envelope drawn.
        if self._frame_count == 0:
            return
        self._view_start = start
        self._clamp_view_start()
        self._rebuild_envelope()
        self.update()
        self._emit_view_changed()

    def set_zoom(self, zoom, anchor_frame=None):
        # anchor_frame is the frame that stays under the same x pixel after
        # zooming - defaults to the view's current center. Wheel-zoom
        # passes the frame under the cursor so zooming in/out feels like
        # it's happening "at the mouse", the same convention as every
        # pinch-to-zoom map/image viewer. Works in header-only mode too -
        # see set_view_start's own comment.
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
        if self._samples is not None:
            self._rebuild_envelope()
        super().resizeEvent(event)

    def _rebuild_envelope(self):
        # called unconditionally by set_view_start/set_zoom, which now also
        # run in header-only mode (no audio) - nothing to rebuild there
        if self._samples is None:
            self._envelope = []
            return
        view_start = self._view_start
        view_length = self._view_length()
        # clip to how much has actually arrived so far, not just what the
        # view window wants - during a live capture (begin_live_capture/
        # append_live_samples) self._samples is still growing, so this is
        # frequently short of view_start + view_length. Once the sample is
        # fully loaded len(self._samples) == frame_count, which can never
        # be less than view_start + view_length (the view is always
        # bounded within the sample), so loaded_end == view_start +
        # view_length there and this clip is a no-op - exactly the old
        # unconditional slice for every already-shipped, non-live case.
        loaded_end = min(view_start + view_length, len(self._samples))
        if loaded_end <= view_start:
            self._envelope = []
            return
        visible = self._samples[view_start:loaded_end]
        # only the fraction of the view actually loaded gets columns, so
        # the envelope visibly grows left-to-right in step with the
        # transfer instead of whatever prefix has arrived stretching to
        # fill the entire canvas width (build_envelope has no idea some of
        # its input is "not here yet" vs. genuinely the whole view).
        width = round(self.width() * len(visible) / view_length) if view_length else 0
        self._envelope = build_envelope(visible, width)

    def _x_for(self, name):
        return x_for_frame(
            self._markers[name], self.width(), self._view_start, self._view_length()
        )

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

    def _draw_zero_crossing_line(self, painter, palette):
        # a plain horizontal guide at zero amplitude - border colour, not
        # accent/text, since this is a background reference line, not
        # something meant to draw the eye the way the waveform or its
        # markers do. Drawn once the header/frame_count is known (same
        # gate as the markers below), not gated on has_waveform() - it's
        # just as useful a reference before real audio ever arrives.
        mid_y = self.height() / 2
        pen = QPen(QColor(palette["border"]))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawLine(QPointF(0, mid_y), QPointF(self.width(), mid_y))

    def _draw_connected_samples(self, painter, palette, view_start, view_length):
        # zoomed in enough that individual samples are more than a pixel
        # apart (view_length <= the canvas's own width, so every visible
        # sample gets its own distinguishable x) - build_envelope's own
        # one-min/max-pair-per-pixel-column approach degenerates here:
        # each column maps to at most one sample, so min == max and every
        # "bar" collapses to a lone dot, which is what actually read as a
        # row of disconnected dashes instead of a continuous waveform.
        # Drawing an actual point-to-point polyline through each visible
        # sample's own value instead is what every other waveform editor
        # does once you're zoomed in this far. Only ever called when
        # view_length <= self.width(), so this is never more per-paint
        # work than the bar-drawing loop it replaces here - it does NOT
        # replace that loop at lower zoom levels, where thousands of
        # samples can share a single column and a real per-sample
        # drawLine would be both pointless (sub-pixel) and slow (see
        # build_envelope's own comment on exactly that).
        mid_y = self.height() / 2
        half = self.height() / 2 - 6
        width = self.width()
        loaded_end = min(view_start + view_length, len(self._samples))
        pen = QPen()
        pen.setWidthF(1.0)
        current_color = None
        prev_point = None
        for frame in range(view_start, loaded_end):
            x = x_for_frame(frame, width, view_start, view_length)
            y = mid_y - (self._samples[frame] / 32768) * half
            point = QPointF(x, y)
            color = self._waveform_zone_color(frame, palette)
            if color != current_color:
                pen.setColor(color)
                painter.setPen(pen)
                current_color = color
            if prev_point is not None:
                painter.drawLine(prev_point, point)
            prev_point = point

    def _waveform_zone_color(self, frame, palette):
        # colors the envelope trace by which region a frame falls in:
        # dimmed ("greyed out") outside the Start/End markers - that
        # audio is on the sampler but never actually plays - the loop
        # region's own teal within [loop_start, loop_end] (the exact
        # same token the loop markers themselves already use - see
        # _marker_colors - so the coloured waveform band and its own
        # boundary markers read as one thing), and the ordinary accent
        # colour everywhere else within [start, end] (the ordinary,
        # non-looping lead-in/out around the loop).
        m = self._markers
        if frame < m["start"] or frame > m["end"]:
            return QColor(palette["text_disabled"])
        if m["loop_start"] <= frame <= m["loop_end"]:
            return QColor(palette["keygroup_color_3"])
        return QColor(palette["accent"])

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = theme.current_palette()

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillRect(rect, QColor(palette["bg_input"]))
        painter.setPen(QColor(palette["border"]))
        painter.drawRoundedRect(rect, _BORDER_RADIUS, _BORDER_RADIUS)

        if self._frame_count == 0:
            # nothing known at all yet (no header, no audio) - the big
            # centered placeholder
            painter.setPen(QColor(palette["text_disabled"]))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                _LOADING_TEXT if self._loading else _PLACEHOLDER_TEXT,
            )
            return

        self._draw_zero_crossing_line(painter, palette)

        if self._samples is not None:
            view_start = self._view_start
            view_length = self._view_length()
            if 0 < view_length <= self.width():
                # zoomed in far enough that samples are individually
                # distinguishable - connect them instead of a per-column
                # min/max bar (see _draw_connected_samples' own comment)
                self._draw_connected_samples(painter, palette, view_start, view_length)
            else:
                mid_y = self.height() / 2
                half = self.height() / 2 - 6
                pen = QPen()
                pen.setWidthF(1.0)
                current_color = None
                for x, (lo, hi) in enumerate(self._envelope):
                    # one min/max pair per pixel column already (see
                    # build_envelope's own comment on why) - frame_for_x maps
                    # that column back to roughly the frame it represents, the
                    # same mapping x_for_frame (marker positioning) already
                    # inverts, so the colour boundary lines up with where the
                    # markers themselves are drawn below
                    frame = frame_for_x(x, self.width(), view_start, view_length)
                    color = self._waveform_zone_color(frame, palette)
                    if color != current_color:
                        pen.setColor(color)
                        painter.setPen(pen)
                        current_color = color
                    y_lo = mid_y - (hi / 32768) * half
                    y_hi = mid_y - (lo / 32768) * half
                    painter.drawLine(int(x), int(y_lo), int(x), int(y_hi) + 1)

        # markers draw whenever the header is known, audio or not - the
        # whole point of set_header is editing before/without audio ever
        # arriving
        colors = self._marker_colors(palette)
        view_start = self._view_start
        view_length = self._view_length()
        for name in _MARKER_ORDER:
            frame = self._markers[name]
            # off the visible edge in either direction - draw nothing
            # rather than a marker pinned to x=0/width that looks real
            if frame < view_start or frame > view_start + view_length - 1:
                continue
            x = self._x_for(name)
            pen = QPen(colors[name])
            pen.setWidthF(1.5)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            painter.setBrush(colors[name])
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

        if not self._envelope:
            # nothing to look at yet where the envelope would otherwise
            # be - either genuinely header-only (self._samples is None,
            # no fetch started) or a live capture that's begun but hasn't
            # had its first packet/chunk land yet (self._samples == [],
            # see begin_live_capture/append_live_samples). Vertically
            # centered like the big placeholder above, not pinned to the
            # bottom - markers only occupy a few px at the very top (their
            # triangle handles), so a centered single line doesn't run
            # into them
            painter.setPen(QColor(palette["text_disabled"]))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                _LOADING_TEXT if self._loading else _PLACEHOLDER_TEXT,
            )

    def _markers_within_hit_radius(self, x):
        # every visible marker within _HIT_RADIUS_PX of x, closest first
        # (ties - genuinely equal distance, e.g. several markers pushed
        # onto the exact same frame - break in _MARKER_ORDER's own order,
        # since Python's sort is stable and that's the order this list
        # gets built in). The full candidate list, not just the single
        # closest one, is what mousePressEvent's click-to-cycle needs to
        # let a click pick something other than whichever marker happens
        # to win ties - otherwise, once several markers land on the same
        # frame (a push cascade can do this - see push_marker), only the
        # frontmost one could ever be grabbed again.
        view_start = self._view_start
        view_length = self._view_length()
        candidates = []
        for name in _MARKER_ORDER:
            frame = self._markers[name]
            if frame < view_start or frame > view_start + view_length - 1:
                continue  # not visible - can't be clicked
            dist = abs(self._x_for(name) - x)
            if dist <= _HIT_RADIUS_PX:
                candidates.append((dist, name))
        candidates.sort(key=lambda pair: pair[0])
        return [name for _dist, name in candidates]

    def mouseDoubleClickEvent(self, event):
        # diagnostic-only logging - see program_editor_window.py's
        # _load_sample_waveform for why this exists (an intermittent
        # "double-click does nothing" failure in real interactive use that
        # no scripted or QTest-simulated repro could reproduce). This is
        # the other half of the trace: whether the double-click reached
        # here and passed the "is actually the placeholder state" guard at
        # all, before anything downstream (BridgeWorker, SamplerController)
        # ever gets involved.
        if self._samples is None and not self._loading:
            debug_log.get_logger().debug(
                "WaveformView.mouseDoubleClickEvent: accepted, emitting load_requested"
            )
            self.load_requested.emit()
        else:
            debug_log.get_logger().debug(
                "WaveformView.mouseDoubleClickEvent: ignored "
                f"(has_waveform={self._samples is not None}, loading={self._loading})"
            )
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        # frame_count, not samples - dragging must work in header-only
        # mode (markers known, no audio/envelope yet) too
        if self._frame_count == 0:
            return
        x = event.position().x()
        candidates = self._markers_within_hit_radius(x)
        if candidates and candidates == self._press_cycle_candidates:
            # same stack of overlapping markers as last press (order-
            # sensitive on purpose - candidates is always sorted the same
            # way for the same stack, so this only matches a genuine
            # repeat click) - advance to the NEXT one in rotation instead
            # of grabbing the same closest one every time, which is what
            # made overlapping markers hard to separate again in the
            # first place (see AGENTS.md)
            self._press_cycle_index = (self._press_cycle_index + 1) % len(candidates)
        else:
            self._press_cycle_candidates = candidates
            self._press_cycle_index = 0
        self._dragging = candidates[self._press_cycle_index] if candidates else None
        if self._dragging is not None:
            self._drag_anchor_x = x
            self._drag_value = float(self._markers[self._dragging])

    def mouseMoveEvent(self, event):
        if self._dragging is None:
            return
        index = _MARKER_ORDER.index(self._dragging)
        fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        if fine and not self._fine_active:
            self._enter_fine_drag()
        elif not fine and self._fine_active:
            self._exit_fine_drag()

        if self._fine_active:
            # the cursor gets warped back to _warp_anchor_global at the end
            # of every move event below, so THIS event's global position is
            # already the delta since the last one - not since drag start.
            # The warp itself generates its own synthetic move event on
            # most platforms, landing exactly on the anchor - skip it
            # rather than read it as a zero-length real move (harmless
            # either way since dx would be 0, but skips the redundant
            # clamp/repaint/signal-emit work)
            current = event.globalPosition()
            anchor = self._warp_anchor_global
            if (round(current.x()), round(current.y())) == (
                round(anchor.x()),
                round(anchor.y()),
            ):
                return
            dx = (current.x() - anchor.x()) / _FINE_DRAG_DIVISOR
            QCursor.setPos(anchor)  # QCursor.pos() is already a QPoint
        else:
            x = event.position().x()
            dx = x - self._drag_anchor_x
            self._drag_anchor_x = x

        view_length = self._view_length()
        frames_per_px = (view_length - 1) / max(1, self.width() - 1)
        # a float accumulator (_drag_value) carries the sub-frame remainder
        # between events instead of rounding it away each time, so fine
        # dragging doesn't feel "sticky" or drift off the real cursor
        # motion over a long drag
        self._drag_value += dx * frames_per_px
        frame = int(round(self._drag_value))
        self._markers = push_marker(
            _MARKER_ORDER, index, frame, self._markers, self._frame_count
        )
        # resync the accumulator to the actually-applied frame - still
        # needed for the whole-sample-bounds clamp at the very ends
        # (push_marker itself never stops this marker short of *frame*
        # just because a neighbour was in the way - it pushes the
        # neighbour along instead - so this is only ever a no-op except
        # at frame 0 / frame_count - 1)
        self._drag_value = self._markers[self._dragging]
        self.update()
        self._emit_markers_changed()

    def _enter_fine_drag(self):
        # Shift held mid-drag: the same physical mouse movement now covers
        # only 1/_FINE_DRAG_DIVISOR of the distance, for precise placement -
        # which means crawling across the same screen-width of physical
        # travel many times over for a large move. Hiding the cursor and
        # warping it back to a fixed point every event (see mouseMoveEvent)
        # is the standard trick creative tools (Blender included) use so
        # the user can keep moving the mouse in one direction indefinitely
        # instead of running out of screen and stalling at the edge.
        self._fine_active = True
        self._warp_anchor_global = QCursor.pos()
        QApplication.setOverrideCursor(Qt.CursorShape.BlankCursor)

    def _exit_fine_drag(self):
        # Shift released mid-drag, or the drag ending - always paired with
        # _enter_fine_drag's setOverrideCursor so the app is never left
        # with a permanently invisible cursor
        self._fine_active = False
        self._warp_anchor_global = None
        QApplication.restoreOverrideCursor()

    def mouseReleaseEvent(self, event):
        if self._dragging is None:
            return
        if self._fine_active:
            self._exit_fine_drag()
        which = self._dragging
        self._dragging = None
        m = self._markers
        self.marker_committed.emit(
            which, m["start"], m["loop_start"], m["loop_end"], m["end"]
        )

    def hideEvent(self, event):
        # safety net: if this widget is hidden mid-drag (switching tabs,
        # the window closing) with no mouseReleaseEvent ever arriving, make
        # sure the app isn't left with a stuck invisible cursor
        if self._fine_active:
            self._exit_fine_drag()
        super().hideEvent(event)

    def wheelEvent(self, event):
        # frame_count, not samples - zoom/pan are useful in header-only
        # mode too (more precise dragging), same as set_zoom/set_view_start
        if self._frame_count == 0:
            return
        angle = event.angleDelta()

        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # holding a modifier and scrolling to zoom, on any platform -
            # separate from _handle_pinch_zoom's real macOS trackpad pinch
            # gesture, which does not arrive as a Ctrl+wheel event at all
            # and needs its own handler
            delta = angle.x() if angle.x() != 0 else angle.y()
            if delta == 0:
                return
            anchor_frame = frame_for_x(
                event.position().x(),
                self.width(),
                self._view_start,
                self._view_length(),
            )
            if delta > 0:
                self.zoom_in(anchor_frame)
            else:
                self.zoom_out(anchor_frame)
            event.accept()
            return

        # Panning ONLY ever acts on a genuine angle.x() component - a real
        # horizontal trackpad swipe or a mouse's own horizontal wheel,
        # which is unambiguous by construction (nothing else produces a
        # nonzero angle.x()). A plain wheel mouse has no horizontal axis
        # at all, so Shift+scroll repurposes its angle.y() for pan instead
        # - the same "hold Shift to scroll sideways" convention most other
        # apps already use. Vertical scroll WITHOUT Shift does nothing.
        #
        # This used to auto-detect "the dominant axis" instead - first
        # per event (a stray value on the wrong axis at the very start of
        # a swipe could briefly out-vote the real one), then per gesture
        # via QWheelEvent.phase() locking (better, but a slow swipe still
        # occasionally bounced: both axes' per-event deltas are small and
        # close together at low speed, so ordinary hand tremor on the
        # "wrong" axis could still occasionally win even within one locked
        # gesture). Both were fixing symptoms of the same root problem -
        # trying to infer intent from a genuinely ambiguous, noisy signal.
        # Never treating angle.y() as pan unless Shift makes the intent
        # explicit removes the ambiguity outright rather than getting
        # better at guessing it.
        if angle.x() != 0:
            delta = angle.x()
        elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier and angle.y() != 0:
            delta = angle.y()
        else:
            return

        # a fixed fraction of the current view per notch, so panning stays
        # useful at any zoom level instead of crawling at high zoom or
        # flying past at low
        view_length = self._view_length()
        pan_frames = int(-delta / 120 * max(1, view_length // 10))
        self.set_view_start(self._view_start + pan_frames)
        event.accept()

    def event(self, event):
        # macOS trackpad pinch-to-zoom arrives as its own native gesture
        # event, not a modified wheel event (an earlier version of this
        # code assumed Qt translated it to Ctrl+wheel - it doesn't; that
        # was never actually verified against a real trackpad). event()
        # is the right override for this, same as Qt's own docs example -
        # QWidget has no dedicated nativeGestureEvent() virtual to override.
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
        # value() is the incremental scale change for this one event (a
        # small step like 0.02 per pinch increment), not an absolute zoom
        # level - multiplies into the current zoom rather than replacing
        # it, same as repeated zoom_in()/zoom_out() calls do. Clamped away
        # from 0/negative defensively - set_zoom's own min/max clamp
        # handles the normal range, this only guards an extreme single
        # event value.
        factor = max(0.1, 1.0 + event.value())
        anchor_frame = frame_for_x(
            event.position().x(), self.width(), self._view_start, self._view_length()
        )
        self.set_zoom(self._zoom * factor, anchor_frame)
