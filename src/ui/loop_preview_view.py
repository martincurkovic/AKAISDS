import math

from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QWidget, QSizePolicy
from PySide6.QtGui import QCursor, QPainter, QPen, QColor
from PySide6.QtCore import QEvent, Qt, QRectF, QPointF, Signal

from ui import theme
from ui.waveform_view import (
    _FINE_DRAG_DIVISOR_MACOS,
    _MACOS,
    build_envelope,
    x_for_frame,
)

# mirrored from waveform_view.py rather than imported - small enough that
# duplicating costs less than coupling two widgets with otherwise unrelated
# lifetimes together (same reasoning slice_waveform_view.py's own module
# docstring gives for its own mirrored constants)
_BORDER_RADIUS = 4
_FINE_DRAG_DIVISOR = 8  # how much slower a Shift-held drag moves

# how many frames either side of the loop point this widget shows - fixed,
# not tied to the main WaveformView's own zoom level (a user could be
# looking at the whole multi-minute sample zoomed out, which would make a
# same-zoom preview either show nothing useful or need an enormous window) -
# per direct user request. Halved from an original 300 (still per direct
# user request - 300 read as "a tad too zoomed out") to 150, tight enough to
# actually read the seam's own shape at ordinary sample rates without
# needing to zoom the main waveform in first.
HALF_WINDOW_FRAMES = 150

# The zoom levels the preview offers: how many frames each side of the loop point it shows, from every sample readable one by one (19)
# out to the overall shape (2400). Each step doubles or halves the window; 150 is the default and what "reset" returns to. Dragging
# needs no change when this does: the drag maths measures frames per pixel from whatever is shown, so zooming in makes a drag finer.
ZOOM_LADDER = (19, 38, 75, 150, 300, 600, 1200, 2400)
DEFAULT_ZOOM_INDEX = ZOOM_LADDER.index(HALF_WINDOW_FRAMES)
# Ctrl+scroll: one mouse-wheel notch (120 units) is one step; a trackpad's small deltas add up to one
_WHEEL_UNITS_PER_STEP = 120
# trackpad pinch: events carry small incremental scale changes; one ladder step per x1.41 of pinch (half a doubling)
_PINCH_OCTAVES_PER_STEP = 0.5

_PLACEHOLDER_NO_AUDIO = "No audio loaded"
_PLACEHOLDER_NO_LOOP = "No loop on this sample"


class LoopJoinPreview(QWidget):
    """A small, read-only waveform pane showing the actual SPLICE a loop
    makes - the real S3000XL front panel's own LOOP screen is a single
    widget with one dividing line, not two independently-zoomed views: the
    left half is the audio leading INTO loop_end (loop OUT), the right half
    is the audio leading OUT of loop_start (loop IN), spliced together
    edge-to-edge at the divider exactly where the sampler would jump from
    one to the other during playback. This lets a user see the literal
    join - whether the waveform's shape/amplitude actually lines up at the
    seam - not just look at each edge in its own isolated context. The
    divider sits at a fixed 50/50 split of the widget's own width - each
    half is painted independently within its own half (see _draw_half),
    NOT as one shared buffer mapped uniformly across the full width, so a
    loop point near either end of the sample (where samples_before/
    samples_after can't supply a full HALF_WINDOW_FRAMES) shows that half
    more zoomed in rather than shifting the divider and resizing the OTHER
    half to compensate.

    Dragging the LEFT half (loop-out audio) moves loop_end; dragging the
    RIGHT half (loop-in audio) moves loop_start, at THIS widget's own much
    tighter zoom (a fixed ~600-frame window vs. however zoomed-out the main
    view happens to be), for finer per-pixel control - per direct user
    request. Unlike WaveformView's own marker drag, there's no marker glyph
    tracking the cursor here - the divider stays fixed on screen and the
    WAVEFORM CONTENT around it updates each step, so the natural feel is
    "grab and pan the content" (drag left -> content follows the cursor
    left, same as dragging a photo/map), which is the OPPOSITE sign
    relationship a naive port of WaveformView's own dx-to-delta mapping
    would give - confirmed by direct user testing after an initial version
    had this backwards. See mouseMoveEvent's own comment for the sign
    derivation.
    Holding Shift while dragging slows that down further still (same
    _FINE_DRAG_DIVISOR/warp-the-cursor-back trick WaveformView's own
    mouseMoveEvent uses, mirrored rather than shared - see this widget's
    own class docstring elsewhere on why this file mirrors rather than
    imports that widget's constants), for the finest per-pixel adjustment
    this page offers anywhere.
    This widget owns no marker state itself and never writes anything -
    it only emits a frame DELTA (marker_drag_delta) for whoever owns the
    real value (ProgramEditorWindow) to apply via WaveformView.set_marker,
    the same single source of truth every other marker-editing control on
    this page already goes through (spinboxes, canvas drag) - see
    _on_loop_preview_marker_dragged. This widget then gets its own
    set_join() refresh as a normal consequence of that, the same
    markers_changed round trip _refresh_loop_preview already uses.

    Reuses waveform_view.py's free helpers (build_envelope/x_for_frame)
    rather than re-deriving that math, the same way SliceWaveformView does.
    """

    marker_drag_delta = Signal(str, int)  # ("loop_end"/"loop_start", delta_frames)
    #: the zoom changed: the new half window (frames each side of the loop point). The editor that owns the audio asks
    #: WaveformView.samples_before/after for that many frames again and calls set_join.
    zoom_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # the whole widget is draggable (every pixel maps to some target -
        # see _target_at) - a permanent horizontal-drag cursor hints that
        # without needing a hover state the way WaveformView's own marker
        # hover feedback does (nothing here to be ambiguous about WHICH of
        # several stacked things a click would grab, unlike that widget)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        # the audio immediately BEFORE (and including) loop_end, and
        # immediately AFTER (and including) loop_start - see
        # WaveformView.samples_before/samples_after, which supply these.
        # Each half is painted into its OWN fixed half of this widget's
        # width (see _rebuild_envelope/paintEvent) - NOT concatenated into
        # one buffer mapped uniformly across the full width, which is what
        # this used to do. That made the divider's screen position (and so
        # each half's own apparent width) track len(end_samples) vs.
        # len(start_samples) - fine when a loop point sits far from either
        # end of the sample (both sides get a full HALF_WINDOW_FRAMES,
        # so the split naturally comes out ~50/50), but samples_before/
        # samples_after both CLAMP at the sample's own boundary rather than
        # padding - a loop point sitting near frame 0 or near the sample's
        # last frame gets fewer than HALF_WINDOW_FRAMES on that side, which
        # visibly shifted the divider and made one half "resize" relative
        # to the other as you dragged a loop marker close to either edge -
        # exactly the bug report (and screen recording) that prompted this
        # rewrite. Splitting the width in half up front means the divider
        # never moves; a side with fewer real frames available just reads
        # as more zoomed in on that side, which is what "not enough sample
        # left before/after this point" should actually look like.
        self._zoom_index = DEFAULT_ZOOM_INDEX
        self._wheel_units = 0  # Ctrl+scroll that has not yet added up to a step
        self._pinch_octaves = 0.0  # trackpad pinch likewise
        self._end_samples = None
        self._start_samples = None
        self._end_envelope = []
        self._start_envelope = []
        self._placeholder = _PLACEHOLDER_NO_AUDIO
        self._dragging = None  # "loop_end" / "loop_start" / None
        self._drag_anchor_x = 0.0
        self._drag_value = 0.0  # float accumulator - same reasoning as WaveformView
        # Shift-held fine drag - same "warp the (hidden) cursor back to a
        # fixed point every event" trick WaveformView's own mouseMoveEvent
        # uses, so a large fine-adjustment move never runs out of screen to
        # keep crawling across (see _enter_fine_drag's own comment)
        self._fine_active = False
        self._warp_anchor_global = None
        # safety net alongside hideEvent's own below: if the whole
        # application loses focus mid-fine-drag (Cmd-Tab, a system dialog,
        # a second monitor) with no mouseReleaseEvent/hideEvent ever
        # arriving for this widget, the override cursor _enter_fine_drag
        # set would otherwise stay stuck on QApplication's global cursor
        # stack forever, masking every OTHER widget's own setCursor()
        # app-wide - including this widget's OWN SizeHorCursor, reported
        # as "the cursor change when hovering over the loop viewer seems
        # to be gone" - not a rendering bug here, the real cursor was just
        # permanently hidden by a stuck override from elsewhere.
        QApplication.instance().applicationStateChanged.connect(
            self._on_application_state_changed
        )

    # -- zoom ---------------------------------------------------------------------------------------------------

    def half_window(self):
        """How many frames each side of the loop point to show at the current zoom."""
        return ZOOM_LADDER[self._zoom_index]

    def can_zoom_in(self):
        return self._zoom_index > 0

    def can_zoom_out(self):
        return self._zoom_index < len(ZOOM_LADDER) - 1

    def is_default_zoom(self):
        return self._zoom_index == DEFAULT_ZOOM_INDEX

    def _set_zoom_index(self, index):
        index = max(0, min(len(ZOOM_LADDER) - 1, index))
        if index == self._zoom_index:
            return False
        self._zoom_index = index
        self.zoom_changed.emit(self.half_window())
        return True

    def zoom_in(self):
        """Show fewer frames (closer). Returns False at the closest level."""
        return self._set_zoom_index(self._zoom_index - 1)

    def zoom_out(self):
        """Show more frames (wider). Returns False at the widest level."""
        return self._set_zoom_index(self._zoom_index + 1)

    def reset_zoom(self):
        return self._set_zoom_index(DEFAULT_ZOOM_INDEX)

    def wheelEvent(self, event):
        # Ctrl+scroll zooms (like the main waveform); a plain scroll is NOT taken, so it scrolls the page this card sits in
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.ignore()
            return
        angle = event.angleDelta()
        delta = angle.x() if angle.x() != 0 else angle.y()
        if delta == 0:
            return
        self._wheel_units += delta
        while self._wheel_units >= _WHEEL_UNITS_PER_STEP:
            self._wheel_units -= _WHEEL_UNITS_PER_STEP
            self.zoom_in()
        while self._wheel_units <= -_WHEEL_UNITS_PER_STEP:
            self._wheel_units += _WHEEL_UNITS_PER_STEP
            self.zoom_out()
        event.accept()

    def event(self, event):
        # a macOS trackpad pinch is its own native gesture event, not a modified wheel event (see WaveformView.event)
        if (
            event.type() == QEvent.Type.NativeGesture
            and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture
        ):
            # value() is an incremental scale change (~0.02 per increment); spreading the fingers (positive) zooms IN
            self._pinch_octaves += math.log2(max(0.1, 1.0 + event.value()))
            while self._pinch_octaves >= _PINCH_OCTAVES_PER_STEP:
                self._pinch_octaves -= _PINCH_OCTAVES_PER_STEP
                self.zoom_in()
            while self._pinch_octaves <= -_PINCH_OCTAVES_PER_STEP:
                self._pinch_octaves += _PINCH_OCTAVES_PER_STEP
                self.zoom_out()
            return True
        return super().event(event)

    def set_join(self, end_samples, start_samples):
        self._end_samples = end_samples
        self._start_samples = start_samples
        self._rebuild_envelope()
        self.update()

    def clear(self):
        self._end_samples = None
        self._start_samples = None
        self._placeholder = _PLACEHOLDER_NO_AUDIO
        self.update()

    def clear_no_loop(self):
        # SPTYPE "No looping"/"One-shot" - same gate _set_loop_markers_
        # enabled uses on the main WaveformView (loop_start/loop_end don't
        # just grey out there, they don't draw at all) - distinct wording
        # from clear() so it's clear THIS is why nothing's shown, not a
        # loading problem
        self._end_samples = None
        self._start_samples = None
        self._placeholder = _PLACEHOLDER_NO_LOOP
        self.update()

    def _combined(self):
        # still used by mouseMoveEvent's "did the sample/loop state vanish
        # out from under an in-progress drag" check, and by clear()'s own
        # callers-checking-truthiness pattern elsewhere - kept as a plain
        # "is there anything to show" test, not as a shared render buffer
        # (see _half_width's own comment for why painting no longer
        # concatenates the two halves together)
        if self._end_samples is None or self._start_samples is None:
            return None
        return self._end_samples + self._start_samples

    def _half_width(self):
        # left half gets the floor, right half gets the remainder, so the
        # two always sum to exactly self.width() with no rounding gap
        # between them - which one gets the extra odd pixel doesn't matter,
        # a single pixel is never visible as an asymmetry
        left = self.width() // 2
        return left, self.width() - left

    def _rebuild_envelope(self):
        left_width, right_width = self._half_width()
        self._end_envelope = (
            build_envelope(self._end_samples, left_width) if self._end_samples else []
        )
        self._start_envelope = (
            build_envelope(self._start_samples, right_width)
            if self._start_samples
            else []
        )

    def resizeEvent(self, event):
        self._rebuild_envelope()
        super().resizeEvent(event)

    def _draw_half(self, painter, samples, envelope, x_offset, width, mid_y, half):
        # draws one half's own waveform into [x_offset, x_offset + width) -
        # entirely independent of the other half's sample count, which is
        # exactly what keeps the divider fixed at the midpoint regardless
        # of how many real frames either side actually has available (see
        # this class's own __init__ comment for why that matters)
        length = len(samples)
        if length <= width:
            # few enough samples across this half's own width that a plain
            # point-to-point polyline reads better than a min/max bar per
            # column - same threshold/reasoning WaveformView's own
            # _draw_connected_samples uses
            prev_point = None
            for frame in range(length):
                x = x_offset + x_for_frame(frame, width, 0, length)
                y = mid_y - (samples[frame] / 32768) * half
                point = QPointF(x, y)
                if prev_point is not None:
                    painter.drawLine(prev_point, point)
                prev_point = point
        else:
            for col, (lo, hi) in enumerate(envelope):
                x = x_offset + col
                y_lo = mid_y - (hi / 32768) * half
                y_hi = mid_y - (lo / 32768) * half
                painter.drawLine(int(x), int(y_lo), int(x), int(y_hi) + 1)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        palette = theme.current_palette()

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillRect(rect, QColor(palette["bg_input"]))
        painter.setPen(QColor(palette["border"]))
        painter.drawRoundedRect(rect, _BORDER_RADIUS, _BORDER_RADIUS)

        if not self._combined():
            painter.setPen(QColor(palette["text_disabled"]))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder
            )
            return

        mid_y = self.height() / 2
        half = self.height() / 2 - 10
        left_width, right_width = self._half_width()

        # a plain horizontal guide at zero amplitude, same as WaveformView's
        # own _draw_zero_crossing_line - drawn UNDER the waveform (before
        # it, not after), which is what "guide the eye without competing
        # with the actual signal" means for a background reference line
        pen = QPen(QColor(palette["border"]))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawLine(QPointF(0, mid_y), QPointF(self.width(), mid_y))

        pen = QPen(QColor(palette["accent"]))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        self._draw_half(
            painter, self._end_samples, self._end_envelope, 0, left_width, mid_y, half
        )
        self._draw_half(
            painter,
            self._start_samples,
            self._start_envelope,
            left_width,
            right_width,
            mid_y,
            half,
        )

        # the seam itself - a single divider line, not a draggable-looking
        # marker (no handle triangle) - matches the hardware's own plain
        # dividing line. keygroup_color_3 ties it to the same colour the
        # main WaveformView's own loop markers/loop-region tint use. Always
        # exactly at the midpoint now (see _half_width) - it no longer
        # tracks len(end_samples) vs. len(start_samples).
        x = left_width
        pen = QPen(QColor(palette["keygroup_color_3"]))
        pen.setWidthF(1.5)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))

        painter.setPen(QColor(palette["text"]))
        painter.drawText(
            QRectF(8, 4, x - 12, 16), Qt.AlignmentFlag.AlignLeft, "Loop End"
        )
        painter.drawText(
            QRectF(x + 6, 4, self.width() - x - 14, 16),
            Qt.AlignmentFlag.AlignLeft,
            "Loop Start",
        )

    def _target_at(self, x):
        # which marker a click at widget-local x *would* drag - "loop_end"
        # left of the (now fixed, mid-width) divider, "loop_start" at or
        # right of it. Every pixel in this widget maps to SOME frame in one
        # half or the other (unlike WaveformView's own canvas, there's no
        # zoomed-out empty space), so this never needs an "off-screen, no
        # target" case the way that widget's own hit-testing does.
        if not self._combined():
            return None
        left_width, _right_width = self._half_width()
        return "loop_end" if x < left_width else "loop_start"

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        target = self._target_at(x)
        if target is None:
            return
        self._dragging = target
        self._drag_anchor_x = x
        self._drag_value = 0.0

    def mouseMoveEvent(self, event):
        if self._dragging is None:
            return
        combined = self._combined()
        if not combined:
            # the sample/loop state changed out from under an in-progress
            # drag (selecting a different sample, say) - nothing left to
            # scale against, so just stop rather than dividing by a stale
            # length
            if self._fine_active:
                self._exit_fine_drag()
            self._dragging = None
            return

        fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if fine and not self._fine_active:
            self._enter_fine_drag()
        elif not fine and self._fine_active:
            self._exit_fine_drag()

        if self._fine_active and _MACOS:
            # no warp on macOS - see waveform_view._MACOS's own comment.
            # Same inverted sign convention as the non-fine branch below.
            x = event.position().x()
            dx = (self._drag_anchor_x - x) / _FINE_DRAG_DIVISOR_MACOS
            self._drag_anchor_x = x
        elif self._fine_active:
            # the cursor gets warped back to _warp_anchor_global at the end
            # of every move event below, so THIS event's global position is
            # already the delta since the last one - not since drag start.
            # Same reasoning/mechanics as WaveformView's own mouseMoveEvent -
            # see its own comment on why (the warp itself generates its own
            # synthetic move event landing exactly on the anchor, which
            # this skips rather than reading as a zero-length real move)
            current = event.globalPosition()
            anchor = self._warp_anchor_global
            if (round(current.x()), round(current.y())) == (
                round(anchor.x()),
                round(anchor.y()),
            ):
                return
            dx = (anchor.x() - current.x()) / _FINE_DRAG_DIVISOR
            QCursor.setPos(anchor)  # QCursor.pos() is already a QPoint
        else:
            x = event.position().x()
            dx = self._drag_anchor_x - x
            self._drag_anchor_x = x

        # dx is measured as "anchor minus current" (inverted from a plain
        # screen-space delta) because this widget has no marker glyph
        # that tracks the cursor the way WaveformView's own drag does -
        # what the user is actually grabbing is the WAVEFORM CONTENT
        # itself, and the divider stays fixed on screen while the content
        # around it updates each step. Content follows the cursor (drag
        # left -> the feature under the cursor moves left, same as
        # dragging a photo/map) only when increasing loop_end/loop_start
        # shifts that half's own coordinate mapping so that a fixed source
        # frame's screen x decreases - i.e. the frame delta must be the
        # OPPOSITE sign of the raw screen dx. Confirmed backwards or by
        # direct user testing on real hardware-adjacent use - don't
        # "simplify" this back to a plain `x - anchor` without re-deriving
        # the sign first.
        #
        # Scaled by the DRAGGED half's own sample count and pixel width,
        # not a single uniform scale across the whole widget's combined
        # buffer - the two halves no longer share one x_for_frame mapping
        # (see _half_width's own comment), so a drag on one half must use
        # that half's own frames-per-pixel, not the other's.
        left_width, right_width = self._half_width()
        if self._dragging == "loop_end":
            half_samples, half_width = self._end_samples, left_width
        else:
            half_samples, half_width = self._start_samples, right_width
        frames_per_px = (len(half_samples) - 1) / max(1, half_width - 1)
        # a float accumulator carries the sub-frame remainder between
        # events instead of rounding it away each time - same "slow drag
        # barely moves the marker" bug this exact fix prevents on
        # WaveformView's own canvas drag (see its mouseMoveEvent)
        self._drag_value += dx * frames_per_px
        delta = int(round(self._drag_value))
        if delta != 0:
            self.marker_drag_delta.emit(self._dragging, delta)
            self._drag_value -= delta

    def mouseReleaseEvent(self, event):
        if self._fine_active:
            self._exit_fine_drag()
        self._dragging = None

    def _enter_fine_drag(self):
        # same trick WaveformView._enter_fine_drag uses: hide the cursor
        # and warp it back to a fixed point every event, so the user can
        # keep moving the mouse in one direction indefinitely (at 1/
        # _FINE_DRAG_DIVISOR the effective speed) instead of running out of
        # screen and stalling at the edge partway through a big fine move
        self._fine_active = True
        if _MACOS:
            return
        self._warp_anchor_global = QCursor.pos()
        QApplication.setOverrideCursor(Qt.CursorShape.BlankCursor)

    def _exit_fine_drag(self):
        # Shift released mid-drag, or the drag ending some other way -
        # always paired with _enter_fine_drag's own setOverrideCursor so
        # the app is never left with a permanently invisible cursor
        self._fine_active = False
        if _MACOS:
            return
        self._warp_anchor_global = None
        QApplication.restoreOverrideCursor()

    def hideEvent(self, event):
        # safety net: if this widget is hidden mid-drag (switching away
        # from the Samples tab, the window closing) with no
        # mouseReleaseEvent ever arriving, make sure the app isn't left
        # with a stuck invisible cursor - same reasoning as WaveformView's
        # own hideEvent
        if self._fine_active:
            self._exit_fine_drag()
        super().hideEvent(event)

    def _on_application_state_changed(self, state):
        # the OTHER safety net - see __init__'s own comment on why this
        # exists alongside hideEvent above
        if state != Qt.ApplicationState.ApplicationActive and self._fine_active:
            self._exit_fine_drag()


class LoopPreviewZoomControls(QWidget):
    """Three tiny buttons for a LoopJoinPreview's zoom - out, in, and back to the default - meant to sit right beside the card's title,
    so they take no room from the preview itself. They give no keyboard focus (nothing to hand on when one switches itself off at the
    end of the range, which would scroll the page: see qt_helpers.set_enabled_without_scrolling)."""

    def __init__(self, preview, parent=None):
        super().__init__(parent)
        self._preview = preview
        self.zoom_out_button = self._button("-", 22)  # a plain hyphen: the Unicode minus drew a stray dot in this font
        self.zoom_in_button = self._button("+", 22)
        self.reset_button = self._button("1:1", 30)
        self.zoom_out_button.clicked.connect(preview.zoom_out)
        self.zoom_in_button.clicked.connect(preview.zoom_in)
        self.reset_button.clicked.connect(preview.reset_zoom)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        for button in (self.zoom_out_button, self.zoom_in_button, self.reset_button):
            layout.addWidget(button)
        preview.zoom_changed.connect(self._sync)
        self._sync()
        theme.notifier.changed.connect(self._restyle)
        self._restyle()

    @staticmethod
    def _button(text, width):
        button = QPushButton(text)
        button.setProperty("compact", True)  # style.qss.template: no padding, so the symbol fits
        button.setFixedSize(width, 18)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _restyle(self):
        """In a theme whose section cards have a title bar (System 6) these are small boxes the height of the card's disclosure arrow, so the bar is no
        taller than the other cards' (18 px buttons made it taller); `1:1` is wider than the other two. Other themes keep 18 px buttons."""
        boxes = bool(theme.current_palette().get("card_titlebar"))
        for button, width in ((self.zoom_out_button, 22), (self.zoom_in_button, 22), (self.reset_button, 30)):
            button.setProperty("titleBox", boxes)
            button.setFixedSize((30 if button is self.reset_button else 16) if boxes else width, 14 if boxes else 18)
            style = button.style()
            style.unpolish(button)
            style.polish(button)

    def _sync(self, *_args):
        preview = self._preview
        frames = preview.half_window()
        self.zoom_out_button.setEnabled(preview.can_zoom_out())
        self.zoom_in_button.setEnabled(preview.can_zoom_in())
        self.reset_button.setEnabled(not preview.is_default_zoom())
        self.zoom_out_button.setToolTip(f"Zoom out - show more of the loop join (now \u00b1{frames} frames). Ctrl+scroll or pinch also zooms.")
        self.zoom_in_button.setToolTip(f"Zoom in - show less, in more detail (now \u00b1{frames} frames). Ctrl+scroll or pinch also zooms.")
        self.reset_button.setToolTip(f"Back to the default zoom (\u00b1{HALF_WINDOW_FRAMES} frames; now \u00b1{frames})")
