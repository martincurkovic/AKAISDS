# tests for ui/waveform_view.py's coordinate/clamping math - kept out of
# WaveformView's paintEvent/mouse handlers specifically so it can be tested
# without a QApplication or any actual rendering, same reasoning as
# test_envelope_graph.py's own _adsr_points tests. The one exception is at
# the bottom of this file (wheelEvent's pan-axis behaviour), which needs a
# real WaveformView + a live QApplication - see the section comment there.

import math
import os

# must be set BEFORE the first QApplication() call below - see
# test_slice_editor_window.py's own comment on this exact guard for why a
# file lacking it only runs offscreen by accident (whichever OTHER test
# module happens to get collected first)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from ui import theme
from ui.waveform_view import (
    _MARKER_ORDER,
    WaveformView,
    _bridge_envelope_gaps,
    brighten_for_hover,
    build_envelope,
    frame_for_x,
    push_marker,
    x_for_frame,
)


# --- brighten_for_hover -------------------------------------------------------
# used by both WaveformView and SliceWaveformView (imported there) for
# marker hover/drag feedback - pure QColor-in/QColor-out, no widget needed.


def test_brighten_for_hover_lightens_in_dark_mode():
    dark_bg_palette = {"bg_input": "#2a2a35"}  # theme.DARK_PALETTE's own value
    base = QColor("#199e70")  # keygroup_color_3, dark theme
    brighter = brighten_for_hover(base, dark_bg_palette)
    assert brighter.lightness() > base.lightness()


def test_brighten_for_hover_darkens_in_light_mode():
    # the mirror image is correct here, not a bug - lightening further
    # against an already-light background would wash the colour out
    # instead of making it pop (see the function's own docstring)
    light_bg_palette = {"bg_input": "#ffffff"}  # theme.LIGHT_PALETTE's own value
    base = QColor("#1baf7a")  # keygroup_color_3, light theme
    darker = brighten_for_hover(base, light_bg_palette)
    assert darker.lightness() < base.lightness()


def test_brighten_for_hover_never_clips_a_light_colour_to_solid_white():
    # a flat lightness multiplier used to blow an already-fairly-light
    # colour straight to #ffffff, losing its own hue entirely - blending
    # only ever approaches white asymptotically, so it never fully gets
    # there
    dark_bg_palette = {"bg_input": "#2a2a35"}
    base = QColor("#9085e9")  # keygroup_color_7, dark theme - already fairly light
    brighter = brighten_for_hover(base, dark_bg_palette)
    assert brighter != QColor("#ffffff")
    assert brighter.hue() == base.hue()  # hue survives - same colour, just brighter


# --- build_envelope ------------------------------------------------------------


def test_build_envelope_returns_one_min_max_pair_per_pixel_column():
    envelope = build_envelope(list(range(-100, 100)), width=20)
    assert len(envelope) == 20
    assert all(lo <= hi for lo, hi in envelope)


def test_build_envelope_covers_the_full_sample_range():
    samples = [0] * 50 + [12345] + [0] * 49
    envelope = build_envelope(samples, width=10)
    assert max(hi for _lo, hi in envelope) == 12345


def test_build_envelope_handles_no_samples():
    assert build_envelope([], width=10) == [(0, 0)] * 10


# --- _bridge_envelope_gaps: no more disconnected-looking dashes at some ------
# zoom levels. Per direct user report (screenshots of zooming in on a
# decaying tail): the bar-drawing loop only draws a VERTICAL line per
# column, nothing connects one column to the next, so two adjacent columns
# whose (lo, hi) ranges don't overlap read as a visible gap even though the
# underlying waveform is continuous. build_envelope now runs every result
# through this before returning it.


def test_bridge_closes_a_gap_between_two_non_overlapping_columns():
    # column 0 covers [0, 10], column 1 covers [50, 60] - a real gap
    bridged = _bridge_envelope_gaps([(0, 10), (50, 60)])
    assert bridged[0] == (0, 10)
    # column 1's lower edge is pulled down to touch column 0's upper edge -
    # its own hi (60, the genuine peak) is never touched
    assert bridged[1] == (10, 60)


def test_bridge_does_not_touch_columns_that_already_overlap():
    envelope = [(0, 20), (15, 40), (35, 50)]
    assert _bridge_envelope_gaps(envelope) == envelope


def test_bridge_pulls_the_edge_nearer_the_gap_in_either_direction():
    # a descending gap (column 1 sits BELOW column 0) needs its upper edge
    # pulled up instead, not its lower edge pulled down
    bridged = _bridge_envelope_gaps([(50, 60), (0, 10)])
    assert bridged[0] == (50, 60)
    assert bridged[1] == (0, 50)


def test_bridge_cascades_through_a_whole_run_of_gaps():
    # three columns, each a real gap above the last - every gap closes,
    # not just the first one
    bridged = _bridge_envelope_gaps([(0, 5), (20, 25), (50, 55)])
    assert bridged == [(0, 5), (5, 25), (25, 55)]


def test_bridge_never_shrinks_a_columns_own_extreme_value():
    # bridging must never make a genuine peak/trough disappear - only ever
    # extend an edge outward (toward the gap it's bridging), never touch
    # the OTHER edge, which is what would actually erase a real peak
    bridged = _bridge_envelope_gaps([(0, 5), (100, 200), (5, 10)])
    assert bridged[1][1] == 200  # the peak itself is untouched
    assert bridged[1][0] == 5  # only the lower edge was pulled down to bridge


def test_build_envelope_bridges_gaps_between_flat_alternating_columns():
    # a deliberately worst-case repro of the reported bug: each column's
    # own chunk is perfectly CONSTANT (lo == hi, zero variance), and
    # consecutive columns alternate between two far-apart constant values -
    # every single column boundary is a real gap without bridging, the
    # same "narrow low-variance column next to another one" shape a user's
    # own screenshots showed on a quiet/decaying part of a real waveform,
    # just exaggerated to guarantee the repro rather than rely on luck
    samples = ([100] * 20 + [-100] * 20) * 5  # 10 columns of 20 samples, width=10
    envelope = build_envelope(samples, width=10)
    assert len(envelope) == 10
    for (lo0, hi0), (lo1, hi1) in zip(envelope, envelope[1:]):
        assert max(lo0, lo1) <= min(hi0, hi1), "adjacent columns must touch or overlap"


# --- frame_for_x / x_for_frame (inverses of each other) ------------------------
# both take a view window (view_start, view_length), not the whole sample's
# frame count - at "full zoom" (view_start=0, view_length=the sample's own
# length) that window IS the whole sample, so most of these exercise that
# case; a couple exercise a genuinely zoomed-in window explicitly.


def test_frame_for_x_spans_the_full_range():
    assert frame_for_x(0, width=100, view_start=0, view_length=1000) == 0
    assert frame_for_x(99, width=100, view_start=0, view_length=1000) == 999


def test_x_for_frame_and_frame_for_x_round_trip():
    width, view_length = 400, 5000
    for frame in (0, 1, 2500, 4999):
        x = x_for_frame(frame, width, view_start=0, view_length=view_length)
        assert frame_for_x(x, width, 0, view_length) == pytest.approx(frame, abs=1)


def test_x_for_frame_handles_a_single_frame_sample_without_dividing_by_zero():
    assert x_for_frame(0, width=100, view_start=0, view_length=1) == 0.0


def test_frame_for_x_respects_a_zoomed_in_view_window():
    # zoomed into frames [1000, 1100) - x=0 is frame 1000, not frame 0
    assert frame_for_x(0, width=100, view_start=1000, view_length=100) == 1000
    assert frame_for_x(99, width=100, view_start=1000, view_length=100) == 1099


def test_x_for_frame_and_frame_for_x_round_trip_when_zoomed():
    width, view_start, view_length = 400, 1000, 100
    for frame in (1000, 1050, 1099):
        x = x_for_frame(frame, width, view_start, view_length)
        assert frame_for_x(x, width, view_start, view_length) == pytest.approx(
            frame, abs=1
        )


# --- push_marker ----------------------------------------------------------------
# unlike a plain "stop at the neighbour" clamp, moving one marker past
# another pushes that neighbour (and, in turn, whichever one is next
# along) out of the way instead - see the user-facing request this
# implements and push_marker's own docstring.


def test_push_marker_stays_put_when_nothing_is_in_the_way():
    values = {"start": 0, "loop_start": 100, "loop_end": 500, "end": 999}
    index = _MARKER_ORDER.index("loop_start")
    result = push_marker(_MARKER_ORDER, index, 200, values, frame_count=1000)
    assert result == {"start": 0, "loop_start": 200, "loop_end": 500, "end": 999}


def test_push_marker_pushes_the_immediate_neighbour_it_collides_with():
    values = {"start": 0, "loop_start": 100, "loop_end": 500, "end": 999}

    # dragging loop_start past loop_end pushes loop_end along with it
    index = _MARKER_ORDER.index("loop_start")
    result = push_marker(_MARKER_ORDER, index, 700, values, frame_count=1000)
    assert result == {"start": 0, "loop_start": 700, "loop_end": 700, "end": 999}

    # dragging loop_end before loop_start pushes loop_start along with it
    index = _MARKER_ORDER.index("loop_end")
    result = push_marker(_MARKER_ORDER, index, 50, values, frame_count=1000)
    assert result == {"start": 0, "loop_start": 50, "loop_end": 50, "end": 999}


def test_push_marker_cascades_through_multiple_neighbours():
    # dragging "end" far enough left must push loop_end, which in turn
    # pushes loop_start too - the exact scenario from the user's own
    # request ("drag the sample end marker to the left... push the loop
    # end marker to the left as well")
    values = {"start": 0, "loop_start": 100, "loop_end": 500, "end": 999}
    index = _MARKER_ORDER.index("end")
    result = push_marker(_MARKER_ORDER, index, 50, values, frame_count=1000)
    assert result == {"start": 0, "loop_start": 50, "loop_end": 50, "end": 50}


def test_push_marker_can_cascade_all_the_way_to_the_far_end():
    # dragging "start" past every other marker pushes all three of them
    values = {"start": 0, "loop_start": 100, "loop_end": 500, "end": 999}
    index = _MARKER_ORDER.index("start")
    result = push_marker(_MARKER_ORDER, index, 999, values, frame_count=1000)
    assert result == {"start": 999, "loop_start": 999, "loop_end": 999, "end": 999}


def test_push_marker_only_pushes_whats_actually_in_the_way():
    # loop_end moving right, well short of "end" - "end" must be left
    # completely untouched
    values = {"start": 0, "loop_start": 100, "loop_end": 500, "end": 999}
    index = _MARKER_ORDER.index("loop_end")
    result = push_marker(_MARKER_ORDER, index, 700, values, frame_count=1000)
    assert result == {"start": 0, "loop_start": 100, "loop_end": 700, "end": 999}


def test_push_marker_stays_within_the_sample_itself_at_the_two_ends():
    values = {"start": 0, "loop_start": 100, "loop_end": 500, "end": 999}

    index = _MARKER_ORDER.index("start")
    result = push_marker(_MARKER_ORDER, index, -50, values, frame_count=1000)
    assert result["start"] == 0

    index = _MARKER_ORDER.index("end")
    result = push_marker(_MARKER_ORDER, index, 5000, values, frame_count=1000)
    assert result["end"] == 999


# --- WaveformView.wheelEvent: pan axis ----------------------------------------
# Unlike everything above, this needs a real WaveformView + a live
# QApplication, since it exercises the actual event handler and its effect
# on view state, not a standalone pure function.
#
# Two earlier approaches both tried to INFER which axis a wheel event's
# angleDelta() "really meant" - first per event (compare magnitudes, pick
# the larger), then per gesture (lock the winning axis at
# QWheelEvent.phase()'s own ScrollBegin, hold it through ScrollUpdate).
# Both were real, shipped fixes for real, reported bugs, and both still
# eventually bounced: a slow swipe's per-event deltas on both axes are
# small and close together, so ordinary hand tremor on the axis orthogonal
# to the intended motion can outweigh the real one regardless of whether
# the comparison happens once per event or once per gesture - there is no
# per-event or per-gesture heuristic that reliably wins against noise that
# can dominate at any point along the way.
#
# The fix that actually held: stop inferring. angle.x() is unambiguous by
# construction - nothing but a genuine horizontal swipe/wheel produces a
# nonzero value there - so it always wins outright, with no comparison
# against angle.y() at all. A plain vertical-only mouse wheel has no x
# axis to report, so Shift+scroll is required to explicitly repurpose its
# angle.y() for pan; unmodified vertical scroll does nothing.


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeAngle:
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class _FakePos:
    def x(self):
        return 200

    def y(self):
        return 90


class _FakeWheelEvent:
    def __init__(self, ax, ay, shift=False, ctrl=False):
        self._angle = _FakeAngle(ax, ay)
        mods = Qt.KeyboardModifier.NoModifier
        if shift:
            mods |= Qt.KeyboardModifier.ShiftModifier
        if ctrl:
            mods |= Qt.KeyboardModifier.ControlModifier
        self._mods = mods
        self.ignored = False

    def angleDelta(self):
        return self._angle

    def modifiers(self):
        return self._mods

    def position(self):
        return _FakePos()

    def accept(self):
        pass

    def ignore(self):
        self.ignored = True


def _loaded_waveform_view():
    view = WaveformView()
    view.resize(400, 180)
    samples = [int(20000 * ((i % 50) / 50 - 0.5)) for i in range(10000)]
    view.set_waveform(samples, 100, 2500, 7500, 9900)
    view.zoom_in()
    view.zoom_in()
    return view


def test_wheel_horizontal_delta_always_pans_no_modifier_needed(qapp):
    view = _loaded_waveform_view()
    before = view._view_start
    view.wheelEvent(_FakeWheelEvent(ax=120, ay=0))
    assert view._view_start != before


def test_wheel_vertical_delta_alone_does_nothing(qapp):
    # deliberate: repurposing unmodified vertical wheel/scroll for pan was
    # the source of every axis-guessing bug this section's own comment
    # describes. The event is IGNORED (not swallowed) so it bubbles up to
    # the Samples tab's scroll area and scrolls the page instead
    view = _loaded_waveform_view()
    before = view._view_start
    event = _FakeWheelEvent(ax=0, ay=120)
    view.wheelEvent(event)
    assert view._view_start == before
    assert event.ignored is True


def test_wheel_before_any_header_is_ignored_so_the_page_can_scroll(qapp):
    view = WaveformView()
    event = _FakeWheelEvent(ax=0, ay=120)
    view.wheelEvent(event)
    assert event.ignored is True


def test_wheel_shift_plus_vertical_delta_pans(qapp):
    # the explicit "hold Shift to scroll sideways" fallback for a plain
    # mouse with no horizontal wheel axis at all
    view = _loaded_waveform_view()
    before = view._view_start
    view.wheelEvent(_FakeWheelEvent(ax=0, ay=120, shift=True))
    assert view._view_start != before


def test_wheel_horizontal_delta_wins_even_with_a_larger_stray_vertical_one(qapp):
    # regression test for the exact bug that survived both earlier fixes:
    # a slow swipe can report a SMALLER x than its stray y - magnitude
    # comparison alone would pick y here and move the view the wrong way.
    # x must win purely by being nonzero, never by being larger.
    pure_x_view = _loaded_waveform_view()
    start = pure_x_view._view_start
    pure_x_view.wheelEvent(_FakeWheelEvent(ax=2, ay=0))
    pure_x_delta = pure_x_view._view_start - start

    mixed_view = _loaded_waveform_view()
    assert mixed_view._view_start == start  # same starting point, fresh view
    mixed_view.wheelEvent(_FakeWheelEvent(ax=2, ay=40))  # y is 20x larger than x
    mixed_delta = mixed_view._view_start - start

    assert mixed_delta == pure_x_delta, (
        "a large stray y component must never change the outcome of a "
        "real (even if smaller) x delta"
    )


def test_wheel_ctrl_zoom_still_accepts_either_axis(qapp):
    # zoom (Ctrl+scroll) is a deliberate, distinct modifier action with no
    # left/right ambiguity to get wrong - unlike plain pan, it's fine for
    # it to accept whichever axis is nonzero
    view = _loaded_waveform_view()
    zoom_before = view._zoom
    view.wheelEvent(_FakeWheelEvent(ax=0, ay=120, ctrl=True))
    assert view._zoom != zoom_before

    view2 = _loaded_waveform_view()
    zoom_before2 = view2._zoom
    view2.wheelEvent(_FakeWheelEvent(ax=120, ay=0, ctrl=True))
    assert view2._zoom != zoom_before2


# --- WaveformView.mousePressEvent: click-to-cycle overlapping markers --------
# Once a push (see push_marker) has landed several markers on the exact
# same frame, they all sit at the same pixel column too - the first press
# there always used to grab whichever one won _MARKER_ORDER's own tie-
# break (effectively always "start"), making it impossible to grab any of
# the others again without first dragging that one out of the way. Each
# successive press on the same stack now cycles to the next one instead.


class _FakeXPos:
    def __init__(self, x):
        self._x = x

    def x(self):
        return self._x


class _FakePressEvent:
    def __init__(self, x):
        self._x = x

    def position(self):
        return _FakeXPos(self._x)

    def button(self):
        return Qt.MouseButton.LeftButton


class _FakeMoveEvent(_FakePressEvent):
    # mouseMoveEvent also reads .modifiers() (for Shift-held fine dragging)
    # - always "none held" here, since none of these tests exercise that
    def modifiers(self):
        return Qt.KeyboardModifier.NoModifier


def _stacked_waveform_view(frame_count=10000, stacked_frame=5000):
    # all four markers pushed onto the exact same frame - the scenario
    # this whole feature exists for
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(frame_count, stacked_frame, stacked_frame, stacked_frame, stacked_frame)
    return view


def test_repeated_press_on_a_stack_cycles_through_every_marker(qapp):
    view = _stacked_waveform_view()
    x = view._x_for("start")  # all four are at the same x

    picks = []
    for _ in range(5):  # one full cycle plus one, to check it wraps
        view.mousePressEvent(_FakePressEvent(x))
        picks.append(view._dragging)
        view.mouseReleaseEvent(_FakePressEvent(x))  # end the drag, same as a real click

    assert picks == ["start", "loop_start", "loop_end", "end", "start"]


def test_pressing_elsewhere_then_back_on_the_stack_restarts_the_cycle(qapp):
    view = _stacked_waveform_view()
    stack_x = view._x_for("start")

    view.mousePressEvent(_FakePressEvent(stack_x))
    assert view._dragging == "start"
    view.mouseReleaseEvent(_FakePressEvent(stack_x))

    # click somewhere with no markers at all
    view.mousePressEvent(_FakePressEvent(stack_x + 100))
    assert view._dragging is None
    view.mouseReleaseEvent(_FakePressEvent(stack_x + 100))

    # back on the stack - starts over from the closest one, not where the
    # earlier cycle left off
    view.mousePressEvent(_FakePressEvent(stack_x))
    assert view._dragging == "start"


def test_cycling_only_covers_markers_actually_in_the_stack(qapp):
    # only loop_start/loop_end overlap here - start and end are well
    # apart, so cycling on the loop pair's shared spot must never pick
    # either of them
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(10000, 0, 5000, 5000, 9999)
    x = view._x_for("loop_start")

    view.mousePressEvent(_FakePressEvent(x))
    first = view._dragging
    view.mouseReleaseEvent(_FakePressEvent(x))
    view.mousePressEvent(_FakePressEvent(x))
    second = view._dragging

    assert {first, second} == {"loop_start", "loop_end"}
    assert first != second


# --- WaveformView: hover feedback -------------------------------------------
# "if you click now, this is what you'll grab" - a marker near enough to the
# cursor to be draggable should look different (brighter, solid line, bigger
# handle - see paintEvent) before the user ever actually clicks.


def test_hovering_near_a_marker_sets_hover_marker(qapp):
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(10000, start=0, loop_start=3000, loop_end=7000, end=9999)
    view.mouseMoveEvent(_FakeMoveEvent(view._x_for("start")))
    assert view._hover_marker == "start"


def test_hovering_empty_space_clears_hover_marker(qapp):
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(10000, start=0, loop_start=3000, loop_end=7000, end=9999)
    view.mouseMoveEvent(_FakeMoveEvent(view._x_for("start")))
    assert view._hover_marker == "start"
    view.mouseMoveEvent(_FakeMoveEvent(200))  # nowhere near a marker
    assert view._hover_marker is None


def test_hover_does_not_fire_while_dragging_a_different_marker(qapp):
    # mouseMoveEvent's hover branch only runs when NOT dragging - moving the
    # mouse mid-drag must keep updating the DRAGGED marker, never silently
    # swap in a hover read instead
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(10000, start=0, loop_start=3000, loop_end=7000, end=9999)
    view.mousePressEvent(_FakePressEvent(view._x_for("start")))
    assert view._dragging == "start"
    view.mouseMoveEvent(_FakeMoveEvent(view._x_for("end")))
    assert view._hover_marker is None  # untouched - the move fed the drag instead
    assert view._dragging == "start"


def test_leave_event_clears_hover_marker(qapp):
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(10000, start=0, loop_start=3000, loop_end=7000, end=9999)
    view.mouseMoveEvent(_FakeMoveEvent(view._x_for("start")))
    assert view._hover_marker == "start"
    view.leaveEvent(None)
    assert view._hover_marker is None


def test_release_sets_hover_marker_to_the_just_released_one(qapp):
    # so it doesn't flash back to the dim/dashed look for one repaint before
    # the next real mouseMoveEvent notices the cursor is still right there
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(10000, start=0, loop_start=3000, loop_end=7000, end=9999)
    x = view._x_for("loop_start")
    view.mousePressEvent(_FakePressEvent(x))
    view.mouseReleaseEvent(_FakePressEvent(x))
    assert view._hover_marker == "loop_start"


def test_paint_does_not_crash_with_a_marker_hovered(qapp):
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(10000, start=0, loop_start=3000, loop_end=7000, end=9999)
    view.mouseMoveEvent(_FakeMoveEvent(view._x_for("loop_end")))
    assert view._hover_marker == "loop_end"
    view.grab()


def test_dragging_a_marker_away_and_pressing_the_stack_again_finds_the_rest(qapp):
    # the actual escape hatch this feature exists for: peel one marker off
    # a stack, then the remaining ones must still be reachable
    view = _stacked_waveform_view()
    stack_x = view._x_for("start")

    view.mousePressEvent(_FakePressEvent(stack_x))
    assert view._dragging == "start"
    view.set_marker("start", 1000)  # moved away, same as a completed drag
    view.mouseReleaseEvent(_FakePressEvent(stack_x))

    view.mousePressEvent(_FakePressEvent(stack_x))
    assert view._dragging == "loop_start"


# --- WaveformView.mouseMoveEvent: a slow drag must accumulate sub-frame ------
# movement across events, not lose it every time. Regression test for a real
# bug: _drag_value (the float accumulator that's supposed to carry a drag's
# sub-frame remainder between events - see its own comment) was being
# unconditionally overwritten with the just-applied INTEGER frame after
# every single move event, not only at the whole-sample bounds its own
# comment claimed. A smooth, unhurried real mouse drag is delivered as many
# small per-event deltas, each individually worth well under half a frame
# at ordinary zoom - each one's contribution rounded right back down to the
# SAME frame and was thrown away before the next event could add to it, so
# the marker barely moved no matter how far the real cursor travelled. A
# fast flick (fewer, larger per-event deltas, each already past the
# rounding threshold on its own) happened to track fine, which is exactly
# what made this so easy to miss by testing with a mouse waved around
# quickly rather than dragged slowly and smoothly.


def _fit_zoom_view(frame_count=1599, width=1500):
    # mirrors the real bug report's own numbers - frames_per_px just over
    # 1, the "Fit" zoom level (whole sample visible, nothing zoomed in) -
    # to show this isn't a deep-zoom-only problem
    view = WaveformView()
    view.resize(width, 180)
    view.set_header(frame_count, 0, 81, 651, frame_count - 1)
    return view


def test_slow_drag_accumulates_many_small_moves_into_real_progress(qapp):
    view = _fit_zoom_view()
    end_x = view._x_for("end")
    view.mousePressEvent(_FakePressEvent(end_x))
    assert view._dragging == "end"

    # 500 tiny move events, each individually far too small to round to a
    # whole frame on its own at this view's ~1 frame/px (0.3px * ~1.07 is
    # well under the 0.5 rounding threshold) - mirrors a real slow drag's
    # actual per-event granularity (many small deltas, not one big jump)
    x = end_x
    for _ in range(500):
        x -= 0.3
        view.mouseMoveEvent(_FakeMoveEvent(x))

    total_real_px = end_x - x
    started_at = view._frame_count - 1
    # this much real, cumulative mouse travel must move the marker by a
    # comparable, non-trivial amount, not leave it stuck at (or within a
    # couple of frames of) its starting value
    assert view._markers["end"] < started_at - total_real_px * 0.5


def test_slow_and_fast_drags_covering_the_same_distance_land_in_the_same_place(
    qapp,
):
    # the actual bug report: a slow drag (many small steps) and a fast one
    # (few large steps) covering the identical real distance must end up
    # at the same marker position - before the fix, only the fast one did
    slow = _fit_zoom_view()
    end_x = slow._x_for("end")
    slow.mousePressEvent(_FakePressEvent(end_x))
    x = end_x
    for _ in range(500):
        x -= 0.3  # total: -150px, in 500 tiny steps
        slow.mouseMoveEvent(_FakeMoveEvent(x))

    fast = _fit_zoom_view()
    fast.mousePressEvent(_FakePressEvent(end_x))
    fast.mouseMoveEvent(_FakeMoveEvent(end_x - 150))  # same -150px, one step

    assert slow._markers["end"] == fast._markers["end"]


# --- WaveformView._waveform_zone_color: greyed-out/loop-tinted envelope ------
# outside Start/End: greyed out (that audio is resident but never plays).
# inside Loop Start/Loop End: the loop's own teal (same token the loop
# markers themselves already use). everywhere else within Start/End: the
# ordinary accent colour.


def test_waveform_zone_color_outside_start_end_is_greyed_out(qapp):
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(1000, start=100, loop_start=300, loop_end=700, end=900)
    palette = theme.current_palette()
    assert view._waveform_zone_color(50, palette) == QColor(palette["text_disabled"])
    assert view._waveform_zone_color(950, palette) == QColor(palette["text_disabled"])


def test_waveform_zone_color_within_loop_region_is_loop_tinted(qapp):
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(1000, start=100, loop_start=300, loop_end=700, end=900)
    palette = theme.current_palette()
    # inclusive at both loop edges
    for frame in (300, 500, 700):
        assert view._waveform_zone_color(frame, palette) == QColor(
            palette["keygroup_color_3"]
        )


def test_waveform_zone_color_between_start_and_loop_is_the_ordinary_accent(qapp):
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(1000, start=100, loop_start=300, loop_end=700, end=900)
    palette = theme.current_palette()
    assert view._waveform_zone_color(200, palette) == QColor(palette["accent"])  # lead-in
    assert view._waveform_zone_color(800, palette) == QColor(palette["accent"])  # lead-out
    # right at start/end themselves - still played, still the ordinary colour
    assert view._waveform_zone_color(100, palette) == QColor(palette["accent"])
    assert view._waveform_zone_color(900, palette) == QColor(palette["accent"])


# --- WaveformView.set_loop_enabled: SPTYPE with no loop hides the loop ------
# region entirely - the waveform's loop tint reverts to plain accent, the
# loop_start/loop_end markers stop being drawn at all (not just greyed),
# and hit-testing excludes them so they can't be dragged either. See
# program_editor_window.py's _set_loop_markers_enabled, which calls this
# whenever the current sample's SPTYPE is "No looping"/"One-shot".


def test_loop_disabled_greys_the_loop_region_tint_to_the_boundary_colour(qapp):
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(1000, start=100, loop_start=300, loop_end=700, end=900)
    view.set_loop_enabled(False)
    palette = theme.current_palette()
    # would be loop-tinted with a loop, but there's no loop on this SPTYPE -
    # reads as the ordinary accent colour, same as the rest of [start, end]
    for frame in (300, 500, 700):
        assert view._waveform_zone_color(frame, palette) == QColor(palette["accent"])


def test_loop_disabled_does_not_affect_the_greyed_out_region_outside_start_end(qapp):
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(1000, start=100, loop_start=300, loop_end=700, end=900)
    view.set_loop_enabled(False)
    palette = theme.current_palette()
    assert view._waveform_zone_color(50, palette) == QColor(palette["text_disabled"])
    assert view._waveform_zone_color(950, palette) == QColor(palette["text_disabled"])


def test_marker_colors_are_not_affected_by_loop_enabled(qapp):
    # paintEvent itself skips drawing loop_start/loop_end entirely when
    # disabled (see the crash-guard test below) rather than drawing them
    # greyed - _marker_colors doesn't need its own loop_enabled branching
    view = WaveformView()
    palette = theme.current_palette()
    enabled_colors = view._marker_colors(palette)
    view.set_loop_enabled(False)
    disabled_colors = view._marker_colors(palette)
    assert enabled_colors == disabled_colors
    assert enabled_colors["loop_start"] == QColor(palette["keygroup_color_3"])


def test_paint_does_not_crash_with_loop_disabled(qapp):
    # crash-guard for paintEvent's loop_start/loop_end skip in the marker-
    # drawing loop - grab() forces a real offscreen paint cycle, same
    # convention as the other paint crash-guards below
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(1000, start=100, loop_start=300, loop_end=700, end=900)
    view.set_loop_enabled(False)
    view.grab()


def test_loop_disabled_excludes_loop_markers_from_hit_testing(qapp):
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(1000, start=0, loop_start=300, loop_end=305, end=999)
    view.set_loop_enabled(False)
    x = view._x_for("loop_start")
    assert view._markers_within_hit_radius(x) == []
    # start/end are still draggable
    assert "start" in view._markers_within_hit_radius(view._x_for("start"))


def test_set_loop_enabled_false_then_true_restores_hit_testing(qapp):
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(1000, start=0, loop_start=300, loop_end=305, end=999)
    view.set_loop_enabled(False)
    view.set_loop_enabled(True)
    x = view._x_for("loop_start")
    assert "loop_start" in view._markers_within_hit_radius(x)


# --- WaveformView: loop_start/loop_end are frozen while the loop is off -----
# (not pushed along with a Start/End drag - see _push_marker), then
# reconciled back into [start, end] only once the loop comes back on (see
# set_loop_enabled/_reconcile_loop_into_range) - per direct user request:
# a Start/End trim taken while the loop is off shouldn't silently reshape
# a loop region the user isn't even looking at.


def test_dragging_start_past_a_frozen_loop_start_does_not_move_it(qapp):
    view = WaveformView()
    view.set_header(1000, start=100, loop_start=300, loop_end=700, end=900)
    view.set_loop_enabled(False)
    view.set_marker("start", 500)  # past the frozen loop_start
    m = view.markers()
    assert m["start"] == 500
    assert m["loop_start"] == 300  # untouched, even though start moved past it
    assert m["loop_end"] == 700


def test_dragging_end_past_a_frozen_loop_end_does_not_move_it(qapp):
    view = WaveformView()
    view.set_header(1000, start=100, loop_start=300, loop_end=700, end=900)
    view.set_loop_enabled(False)
    view.set_marker("end", 400)  # past the frozen loop_end
    m = view.markers()
    assert m["end"] == 400
    assert m["loop_start"] == 300
    assert m["loop_end"] == 700  # untouched, even though end moved past it


def test_start_and_end_still_push_each_other_while_the_loop_is_off(qapp):
    # freezing only excludes the LOOP markers from the cascade - start and
    # end still collide with each other exactly like an ordinary push
    view = WaveformView()
    view.set_header(1000, start=100, loop_start=300, loop_end=700, end=900)
    view.set_loop_enabled(False)
    view.set_marker("start", 950)  # past the current end (900)
    m = view.markers()
    assert m["start"] == 950
    assert m["end"] == 950  # pushed along, same as with the loop on


def test_loop_re_enabled_after_being_frozen_out_of_range_snaps_into_start_end(qapp):
    view = WaveformView()
    view.set_header(1000, start=100, loop_start=300, loop_end=700, end=900)
    view.set_loop_enabled(False)
    view.set_marker("start", 800)  # now past the frozen loop region entirely
    view.set_loop_enabled(True)
    m = view.markers()
    assert m["start"] <= m["loop_start"] <= m["loop_end"] <= m["end"]
    # both loop edges were below the new start - both clamp up to it
    assert m["loop_start"] == 800
    assert m["loop_end"] == 800


def test_loop_re_enabled_with_no_change_needed_does_not_touch_the_loop(qapp):
    view = WaveformView()
    view.set_header(1000, start=100, loop_start=300, loop_end=700, end=900)
    view.set_loop_enabled(False)
    # start/end never moved past the loop region - nothing to reconcile
    view.set_loop_enabled(True)
    m = view.markers()
    assert m["loop_start"] == 300
    assert m["loop_end"] == 700


def test_markers_with_loop_in_range_clamps_without_mutating_state(qapp):
    view = WaveformView()
    view.set_header(1000, start=100, loop_start=300, loop_end=700, end=900)
    view.set_loop_enabled(False)
    view.set_marker("start", 800)
    clamped = view.markers_with_loop_in_range()
    assert clamped["loop_start"] == 800
    assert clamped["loop_end"] == 800
    # the real (frozen) state is untouched - only the returned dict clamps
    assert view.markers()["loop_start"] == 300
    assert view.markers()["loop_end"] == 700


# --- WaveformView.paintEvent: zero-crossing line / connected samples ---------
# No pixel-level assertions here (nothing else in this file does either -
# there's no infrastructure for it) - these are crash-guards for the two
# new paint code paths: the zero-crossing line (drawn every paint once a
# header is known) and _draw_connected_samples (only reachable once
# zoomed in far enough that view_length <= the canvas width - see
# paintEvent's own mode switch). grab() forces a real, full paint cycle
# offscreen; it raises if paintEvent itself raises.


def test_paint_does_not_crash_in_header_only_mode(qapp):
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(1000, start=0, loop_start=250, loop_end=750, end=999)
    view.grab()


def test_paint_does_not_crash_at_high_zoom_in_connected_sample_mode(qapp):
    view = WaveformView()
    view.resize(400, 180)
    samples = [int(1000 * math.sin(i / 5)) for i in range(2000)]
    view.set_waveform(samples, 0, 500, 1500, 1999)
    for _ in range(20):
        view.zoom_in()
    assert 0 < view._view_length() <= view.width()  # actually exercising that mode
    view.grab()


def test_paint_does_not_crash_at_high_zoom_with_only_one_sample_loaded(qapp):
    # begin_live_capture/append_live_samples (progressive loading) can
    # leave self._samples shorter than the current zoomed-in view window
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(2000, start=0, loop_start=500, loop_end=1500, end=1999)
    for _ in range(20):
        view.zoom_in()
    view.begin_live_capture()
    view.append_live_samples([100])
    view.grab()


def test_paint_does_not_crash_with_all_markers_stacked_on_the_default_header(qapp):
    # real hardware's own default header - start=0, end=last frame, and
    # BOTH loop markers on top of end too - used to be genuinely invisible
    # (all four markers drew an identical full-height line + top handle at
    # the same x, so three of the four were completely hidden behind
    # whichever _MARKER_ORDER happened to paint last). See paintEvent's own
    # comment on why loop markers now get a bottom-anchored handle/line
    # segment instead, painted after start/end so it stays visible even
    # fully stacked - this is a crash-guard for that new code path,
    # exercising the exact reported scenario.
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(1000, start=0, loop_start=999, loop_end=999, end=999)
    view.grab()


# --- WaveformView.samples_before / samples_after -----------------------------
# for LoopJoinPreview (ui/loop_preview_view.py's "Loop Preview" card) - the
# audio leading into/out of a loop point, clamped to the buffer's own bounds
# rather than the whole envelope this widget itself renders.


def test_samples_before_returns_none_without_audio(qapp):
    view = WaveformView()
    view.set_header(1000, start=0, loop_start=300, loop_end=700, end=999)
    assert view.samples_before(500, 100) is None


def test_samples_before_ends_at_and_includes_the_given_frame(qapp):
    view = WaveformView()
    samples = list(range(1000))
    view.set_waveform(samples, start=0, loop_start=300, loop_end=700, end=999)
    window = view.samples_before(500, 100)
    assert window == samples[401:501]


def test_samples_before_clamps_at_the_low_edge(qapp):
    view = WaveformView()
    samples = list(range(1000))
    view.set_waveform(samples, start=0, loop_start=300, loop_end=700, end=999)
    window = view.samples_before(20, 100)
    assert window == samples[0:21]


def test_samples_before_returns_none_when_the_frame_is_beyond_whats_loaded_so_far(qapp):
    # reachable mid-progressive-load (begin_live_capture/append_live_samples)
    # if the loop point sits further into the sample than the transfer has
    # reached yet - nothing to show until more arrives, not a crash
    view = WaveformView()
    view.set_header(2000, start=0, loop_start=500, loop_end=1500, end=1999)
    view.begin_live_capture()
    view.append_live_samples(list(range(50)))  # far short of frame 1500
    assert view.samples_before(1500, 100) is None
    # still works fine for a point ALREADY within what's loaded
    assert view.samples_before(20, 10) == list(range(11, 21))


def test_samples_before_clamps_rather_than_returns_none_once_loading_is_complete(qapp):
    # regression test for a real, reported bug: a header claiming one more
    # frame than the actual SDS dump delivered leaves loop_end sitting one
    # past the real last frame - set_waveform's own self._frame_count =
    # len(samples) means loading is unambiguously COMPLETE by the time
    # this runs (unlike the mid-live-capture case above, where
    # self._frame_count is still the bigger, not-yet-reached total), so an
    # out-of-range frame here must clamp instead of returning None - a
    # blank Loop Preview card for an otherwise fully-loaded sample, not a
    # "still waiting for more" state
    view = WaveformView()
    samples = list(range(1000))
    view.set_waveform(samples, start=0, loop_start=300, loop_end=999, end=999)
    window = view.samples_before(1000, 100)  # one past the real last frame (999)
    assert window == samples[900:1000]


def test_samples_after_returns_none_without_audio(qapp):
    view = WaveformView()
    view.set_header(1000, start=0, loop_start=300, loop_end=700, end=999)
    assert view.samples_after(500, 100) is None


def test_samples_after_starts_at_and_includes_the_given_frame(qapp):
    view = WaveformView()
    samples = list(range(1000))
    view.set_waveform(samples, start=0, loop_start=300, loop_end=700, end=999)
    window = view.samples_after(500, 100)
    assert window == samples[500:600]


def test_samples_after_clamps_at_the_high_edge(qapp):
    view = WaveformView()
    samples = list(range(1000))
    view.set_waveform(samples, start=0, loop_start=300, loop_end=700, end=999)
    window = view.samples_after(980, 100)
    assert window == samples[980:1000]


def test_samples_after_returns_none_when_the_frame_is_beyond_whats_loaded_so_far(qapp):
    view = WaveformView()
    view.set_header(2000, start=0, loop_start=500, loop_end=1500, end=1999)
    view.begin_live_capture()
    view.append_live_samples(list(range(50)))
    assert view.samples_after(1500, 100) is None
    assert view.samples_after(20, 10) == list(range(20, 30))


def test_samples_after_clamps_rather_than_returns_none_once_loading_is_complete(qapp):
    # same regression/reasoning as samples_before's own version above
    view = WaveformView()
    samples = list(range(1000))
    view.set_waveform(samples, start=0, loop_start=300, loop_end=999, end=999)
    window = view.samples_after(1000, 100)  # one past the real last frame (999)
    assert window == [999]


# --- WaveformView.set_header: editable markers without audio -----------------
# A real SDS sample dump can take minutes; the header alone (a handful of
# fast get_parameter reads) is comparatively instant. set_header lets a
# user see and drag loop points immediately, before - or entirely without
# ever - loading the actual audio. Also needs a real WaveformView, for the
# same reason as the wheelEvent section above.


def test_set_header_shows_markers_with_no_audio(qapp):
    view = WaveformView()
    view.set_header(20000, start=0, loop_start=5000, loop_end=15000, end=19999)
    assert view.has_header() is True
    assert view.has_waveform() is False
    assert view.markers() == {"start": 0, "loop_start": 5000, "loop_end": 15000, "end": 19999}
    assert view.frame_count() == 20000


def test_set_marker_works_in_header_only_mode(qapp):
    view = WaveformView()
    view.set_header(20000, start=0, loop_start=5000, loop_end=15000, end=19999)
    clamped = view.set_marker("loop_start", 8000)
    assert clamped == 8000
    assert view.markers()["loop_start"] == 8000


def test_set_marker_still_pushes_neighbours_in_header_only_mode(qapp):
    view = WaveformView()
    view.set_header(20000, start=0, loop_start=5000, loop_end=15000, end=19999)
    moved = view.set_marker("loop_start", 99999)  # past loop_end AND end
    # clamped to the sample's own bound (19999), pushing loop_end and end
    # along with it rather than stopping at loop_end's old position
    assert moved == 19999
    assert view.markers() == {"start": 0, "loop_start": 19999, "loop_end": 19999, "end": 19999}


def test_set_marker_returns_none_with_nothing_loaded_at_all(qapp):
    view = WaveformView()
    assert view.set_marker("start", 100) is None


def test_zoom_and_pan_work_in_header_only_mode(qapp):
    # more precise dragging is still useful without an envelope to look at
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(20000, start=0, loop_start=5000, loop_end=15000, end=19999)
    zoom_before = view._zoom
    view.zoom_in()
    assert view._zoom > zoom_before
    view.set_view_start(100)
    assert view._view_start == 100


def test_max_zoom_reaches_single_sample_resolution_for_a_huge_sample(qapp):
    # regression test: zoom used to be capped at a flat 500x, which for
    # any sample bigger than ~500 * the canvas's own width in frames
    # could never reach single-sample resolution at all no matter how far
    # zoomed in - the actual reason dragging a marker couldn't get
    # sample-accurate (see AGENTS.md and _max_zoom's own comment)
    view = WaveformView()
    view.resize(400, 180)
    frame_count = 5_000_000  # far past what the old 500x cap could reach
    view.set_header(frame_count, 0, 100, 200, frame_count - 1)
    view.set_zoom(frame_count)  # the theoretical max, per _max_zoom
    assert view._view_length() == 1
    # frames_per_px, the actual per-pixel drag precision mouseMoveEvent
    # uses, must now be well under 1 - many pixels of movement per frame,
    # not several frames per pixel
    frames_per_px = (view._view_length() - 1) / max(1, view.width() - 1)
    assert frames_per_px < 1


def test_zoom_in_reaches_single_sample_resolution_through_repeated_steps(qapp):
    # the discoverable path (Zoom + button / scroll wheel) must be able to
    # actually reach the new, much higher ceiling through ordinary
    # repeated zoom_in() calls, not just by calling set_zoom() directly
    view = WaveformView()
    view.resize(400, 180)
    frame_count = 2000
    view.set_header(frame_count, 0, 100, 200, frame_count - 1)
    for _ in range(200):  # comfortably more than enough steps
        view.zoom_in()
    assert view._view_length() == 1


def test_clear_resets_header_only_state_too(qapp):
    view = WaveformView()
    view.set_header(20000, start=0, loop_start=5000, loop_end=15000, end=19999)
    view.clear()
    assert view.has_header() is False
    assert view.frame_count() == 0
    assert view.set_marker("start", 5) is None


def test_set_waveform_preserves_the_view_for_the_same_already_known_sample(qapp):
    # a user who zoomed in while editing header-only markers shouldn't get
    # snapped back to fully zoomed out the moment audio happens to arrive
    view = WaveformView()
    view.resize(400, 180)
    frame_count = 10000
    view.set_header(frame_count, start=0, loop_start=2500, loop_end=7500, end=9999)
    view.zoom_in()
    zoom_after_zoom_in = view._zoom
    view.set_view_start(500)

    samples = [0] * frame_count
    view.set_waveform(samples, 0, 2500, 7500, 9999)
    assert view._zoom == zoom_after_zoom_in
    assert view._view_start == 500


def test_set_waveform_resets_the_view_for_a_genuinely_new_sample(qapp):
    # clear()/set_header() (a real sample switch) must still reset the view
    view = WaveformView()
    view.resize(400, 180)
    view.set_header(10000, start=0, loop_start=2500, loop_end=7500, end=9999)
    view.zoom_in()
    view.clear()

    samples = [0] * 5000
    view.set_waveform(samples, 0, 1000, 3000, 4999)
    assert view._zoom == pytest.approx(1.0)
    assert view._view_start == 0


# --- WaveformView.begin_live_capture/append_live_samples: progressive fill --
# A live SDS dump (real hardware or demo mode's own simulated one - see
# program_editor_window.py's _fetch_sample_audio_blocking/
# _fetch_demo_sample_audio) can take minutes; these let the waveform grow
# in step with it instead of only appearing once the whole transfer is
# done.


def test_begin_live_capture_without_a_header_is_a_noop(qapp):
    view = WaveformView()
    view.begin_live_capture()
    assert view.has_waveform() is False


def test_begin_live_capture_starts_an_empty_growing_waveform(qapp):
    view = WaveformView()
    view.set_header(10000, start=0, loop_start=2500, loop_end=7500, end=9999)
    view.begin_live_capture()
    assert view.has_waveform() is True
    assert view.frame_count() == 10000
    assert view._envelope == []


def test_append_live_samples_before_begin_live_capture_is_a_noop(qapp):
    view = WaveformView()
    view.set_header(10000, start=0, loop_start=2500, loop_end=7500, end=9999)
    view.append_live_samples([100, 200, 300])
    assert view.has_waveform() is False


def test_append_live_samples_grows_the_envelope_left_to_right(qapp):
    # the whole point: a half-loaded sample should look like a waveform
    # occupying the left half of the canvas, not a fully-stretched
    # waveform squeezed from half the real data
    view = WaveformView()
    view.resize(400, 180)
    frame_count = 10000
    view.set_header(frame_count, start=0, loop_start=2500, loop_end=7500, end=9999)
    view.begin_live_capture()

    view.append_live_samples([100] * (frame_count // 2))
    half_width = len(view._envelope)
    assert 0 < half_width < view.width()

    view.append_live_samples([100] * (frame_count - frame_count // 2))
    assert len(view._envelope) == view.width()


def test_set_waveform_preserves_view_after_a_live_capture(qapp):
    # regression test: preserve_view used to also require self._samples
    # is None, which stopped being true the moment begin_live_capture ran
    # (a real, growing list, not None) - a live capture must still count
    # as "the same sample" for view-preservation purposes, same as plain
    # header-only mode already did
    view = WaveformView()
    view.resize(400, 180)
    frame_count = 10000
    view.set_header(frame_count, start=0, loop_start=2500, loop_end=7500, end=9999)
    view.begin_live_capture()
    view.append_live_samples([0] * 1000)
    view.zoom_in()
    zoom_after_zoom_in = view._zoom
    view.set_view_start(500)

    samples = [0] * frame_count
    view.set_waveform(samples, 0, 2500, 7500, 9999)
    assert view._zoom == zoom_after_zoom_in
    assert view._view_start == 500


@pytest.mark.parametrize(
    "frame_count,chunk_size",
    [
        (5, 1),  # shorter than a single pixel column
        (500, 37),  # short, uneven chunk size relative to canvas width
        (31169, 5000),  # roughly the demo fixture's own length
        (2_000_000, 200_000),  # far longer than any real sample, for headroom
    ],
)
def test_progressive_fill_scales_to_any_sample_length(qapp, frame_count, chunk_size):
    # nothing about begin_live_capture/append_live_samples/_rebuild_envelope
    # has a length baked in anywhere - this pins that down from shorter
    # than the canvas is wide up to a couple million frames, checking the
    # envelope only ever grows (never shrinks or overshoots) and lands on
    # exactly the full canvas width once everything's arrived
    view = WaveformView()
    view.resize(500, 180)
    view.set_header(
        frame_count, start=0, loop_start=frame_count // 4,
        loop_end=(frame_count * 3) // 4, end=max(0, frame_count - 1),
    )
    view.begin_live_capture()

    widths = []
    pushed = 0
    while pushed < frame_count:
        n = min(chunk_size, frame_count - pushed)
        view.append_live_samples([100] * n)
        pushed += n
        widths.append(len(view._envelope))

    assert all(w <= view.width() for w in widths)
    assert all(a <= b for a, b in zip(widths, widths[1:]))  # monotonically grows
    assert widths[-1] == view.width()


# --- click-to-preview (WaveformView.preview_requested) ----------------------
# same single-vs-double-click disambiguation SliceWaveformView's own
# _schedule_preview_click uses - a click on empty waveform space starts a
# deferred timer rather than firing immediately, cancelled by a genuine
# double-click. _preview_timer.timeout.emit() is called directly rather than
# waiting out the real interval, same reasoning
# tests/test_slice_waveform_view.py's own click-to-preview tests give for
# calling _fire_preview() directly.


def _preview_ready_waveform_view(frame_count=10000):
    view = WaveformView()
    view.resize(400, 180)
    view.set_waveform([0] * frame_count, 0, 2500, 7500, frame_count - 1)
    return view


def test_click_on_empty_space_without_a_waveform_does_not_schedule_a_preview(qapp):
    # header-only (set_header, not set_waveform) - has_waveform() is False,
    # nothing real to play
    view = _stacked_waveform_view()
    view.mousePressEvent(_FakePressEvent(view._x_for("start") + 200))
    assert view._preview_timer.isActive() is False


def test_click_on_empty_space_with_a_waveform_schedules_a_preview(qapp):
    view = _preview_ready_waveform_view()
    view.mousePressEvent(_FakePressEvent(200))  # nowhere near a marker
    assert view._preview_timer.isActive() is True


def test_clicking_a_marker_does_not_schedule_a_preview(qapp):
    view = _preview_ready_waveform_view()
    view.mousePressEvent(_FakePressEvent(view._x_for("start")))
    assert view._preview_timer.isActive() is False


def test_preview_timer_firing_emits_preview_requested(qapp):
    view = _preview_ready_waveform_view()
    received = []
    view.preview_requested.connect(lambda: received.append(True))
    view.mousePressEvent(_FakePressEvent(200))
    view._preview_timer.timeout.emit()
    assert received == [True]


def test_double_click_cancels_a_pending_preview(qapp):
    # a real QMouseEvent, not _FakePressEvent - unlike SliceWaveformView's
    # own mouseDoubleClickEvent, WaveformView's still calls
    # super().mouseDoubleClickEvent(event) at the end (see its own
    # comment), which requires an actual QMouseEvent
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    view = _preview_ready_waveform_view()
    view.mousePressEvent(_FakePressEvent(200))
    assert view._preview_timer.isActive() is True

    point = QPointF(200, 10)
    event = QMouseEvent(
        QEvent.Type.MouseButtonDblClick,
        point,
        point,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.mouseDoubleClickEvent(event)
    assert view._preview_timer.isActive() is False


def test_empty_space_click_still_resets_the_marker_click_cycle(qapp):
    # regression guard: click-to-preview's early return must not skip the
    # existing overlapping-marker click-cycle reset (see
    # test_pressing_elsewhere_then_back_on_the_stack_restarts_the_cycle,
    # which already covers the full user-facing behaviour this pins down
    # at the state level instead)
    view = _stacked_waveform_view()
    view._press_cycle_candidates = ["start", "loop_start", "loop_end", "end"]
    view._press_cycle_index = 2
    view.mousePressEvent(_FakePressEvent(view._x_for("start") + 200))
    assert view._press_cycle_candidates == []
    assert view._press_cycle_index == 0


# --- playhead (click-to-preview visual feedback) ----------------------------


def test_set_playhead_and_clear_playhead(qapp):
    view = _preview_ready_waveform_view()
    view.set_playhead(1234)
    assert view._playhead_frame == 1234
    view.clear_playhead()
    assert view._playhead_frame is None


# --- read-only display options (the S900/S950 Samples tab) ---------------------------------------


def _view_with_header(qapp):
    view = WaveformView()
    view.resize(800, 180)
    view.set_header(1000, 100, 400, 800, 900)
    return view


def test_markers_can_be_grabbed_by_default(qapp):
    view = _view_with_header(qapp)
    assert view._markers_within_hit_radius(view._x_for("start")) == ["start"]


def test_locked_markers_cannot_be_grabbed_or_dragged(qapp):
    view = _view_with_header(qapp)
    view.set_markers_locked(True)
    for name in _MARKER_ORDER:
        assert view._markers_within_hit_radius(view._x_for(name)) == []
    committed = []
    view.marker_committed.connect(lambda *a: committed.append(a))
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    x = view._x_for("loop_end")
    for kind, buttons in (
        (QMouseEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
        (QMouseEvent.Type.MouseMove, Qt.MouseButton.LeftButton),
        (QMouseEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        pos = QPointF(x + (30 if kind == QMouseEvent.Type.MouseMove else 0), 50)
        event = QMouseEvent(kind, pos, pos, Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
        getattr(view, {QMouseEvent.Type.MouseButtonPress: "mousePressEvent", QMouseEvent.Type.MouseMove: "mouseMoveEvent", QMouseEvent.Type.MouseButtonRelease: "mouseReleaseEvent"}[kind])(event)
    assert view.markers() == {"start": 100, "loop_start": 400, "loop_end": 800, "end": 900}
    assert committed == []


def test_unlocking_makes_markers_grabbable_again(qapp):
    view = _view_with_header(qapp)
    view.set_markers_locked(True)
    view.set_markers_locked(False)
    assert view._markers_within_hit_radius(view._x_for("start")) == ["start"]


def test_locking_drops_a_drag_in_progress_and_the_hover_highlight(qapp):
    view = _view_with_header(qapp)
    view._dragging = "start"
    view._hover_marker = "end"
    view.set_markers_locked(True)
    assert view._dragging is None and view._hover_marker is None


def test_the_placeholder_text_can_be_replaced(qapp):
    view = WaveformView()
    assert "freeze the interface" in view._placeholder_text  # the S1000/S2000/S3000 default
    view.set_placeholder_text("Select a sample on the left")
    assert view._placeholder_text == "Select a sample on the left"
    view.grab()  # paints without error in the placeholder state


# --- stacked halves (the Yamaha Samples tab's left/right channels drawn as ONE display) ------------------------------


def _stack_view(role, height=90):
    from ui import theme

    view = WaveformView()
    view.set_view_height(height)
    view.resize(300, height)
    view.set_header(1000, 0, 100, 900, 999)  # markers near x = 0, 30, 270, 299
    view.set_stack_position(role, height)
    return view, theme.current_palette()


def _color_at(view, x, y):
    from PySide6.QtGui import QColor

    return QColor(view.grab().toImage().pixel(x, y)).name()


def test_a_stacked_top_half_has_no_border_along_the_seam(qapp):
    from PySide6.QtGui import QColor

    alone, palette = _stack_view(None)
    top, _ = _stack_view("top")
    bg = QColor(palette["bg_input"]).name()
    assert _color_at(alone, 150, 89) != bg  # a stand-alone view has a border along its bottom edge
    assert _color_at(top, 150, 89) == bg  # the seam: background, not a border
    assert _color_at(top, 150, 0) != bg  # its outer (top) edge is still drawn
    assert _color_at(top, 0, 45) != bg and _color_at(top, 299, 45) != bg  # and so are the sides


def test_a_stacked_bottom_half_has_no_border_along_the_seam(qapp):
    from PySide6.QtGui import QColor

    alone, _ = _stack_view(None)
    bottom, palette = _stack_view("bottom")
    bg = QColor(palette["bg_input"]).name()
    # the seam edge carries only a FAINT hairline (not the full-strength border a stand-alone view has) - once a
    # waveform is showing; with nothing drawn yet there is no line to run through the centered hint text
    assert _color_at(bottom, 150, 0) == bg
    bottom.set_waveform([500, -500] * 500, 0, 100, 900, 999)
    assert _color_at(bottom, 150, 0) != bg and _color_at(bottom, 150, 0) != _color_at(alone, 150, 0)
    assert _color_at(bottom, 150, 1) == bg
    assert _color_at(bottom, 150, 89) != bg


def test_marker_handles_belong_to_the_half_that_owns_them_but_the_lines_cross_both(qapp):
    from PySide6.QtGui import QColor

    alone, palette = _stack_view(None)
    top, _ = _stack_view("top")
    bottom, _ = _stack_view("bottom")
    bg = QColor(palette["bg_input"]).name()
    # loop marker (frame 100 -> x ~ 30): its triangle sits at the BOTTOM of a stand-alone view...
    assert _color_at(alone, 33, 88) != bg
    # ...but a top half doesn't draw it (the bottom half owns it), and a bottom half does
    assert _color_at(top, 33, 88) == bg
    assert _color_at(bottom, 33, 88) != bg
    # boundary marker "end" (frame 999 -> x ~ 299): a triangle at the TOP, drawn by the top half only
    assert _color_at(alone, 295, 1) != bg and _color_at(top, 295, 1) != bg and _color_at(bottom, 295, 1) == bg
    # the dashed LINES themselves run through both halves
    assert any(_color_at(v, x, y) != bg for v in (top, bottom) for x in (29, 30, 31) for y in range(20, 70))


def test_an_unknown_stack_role_is_refused_and_none_restores_a_normal_view(qapp):
    view, _ = _stack_view("top")
    with pytest.raises(ValueError):
        view.set_stack_position("middle")
    view.set_stack_position(None)
    assert view._stack_role is None and view._stack_dash_offset_px == 0.0


def _ink_rows(view):
    """Rows where the view differs from itself drawn with an EMPTY hint - i.e. exactly where its hint text sits."""
    hint = view._header_hint
    with_hint = view.grab().toImage()
    view.set_header_hint("")
    without = view.grab().toImage()
    view.set_header_hint(hint)
    return [y for y in range(view.height()) if any(with_hint.pixel(x, y) != without.pixel(x, y) for x in range(view.width()))]


def _pair():
    top, _ = _stack_view("top")
    bottom, _ = _stack_view("bottom")
    top.set_stack_position("top", 90, bottom)
    bottom.set_stack_position("bottom", 90, top)
    for view in (top, bottom):
        view.set_header_hint("Double-click to load")
    return top, bottom


def test_a_stacked_pair_says_its_hint_once_centered_across_the_seam(qapp):
    top, bottom = _pair()
    top_rows, bottom_rows = _ink_rows(top), _ink_rows(bottom)
    assert top_rows and bottom_rows  # each half draws its share of the one line of text...
    assert min(top_rows) > 90 - 22 and max(bottom_rows) < 22  # ...right at the seam, not centered in each half


def test_the_hint_is_not_drawn_in_the_second_half_once_the_first_has_a_waveform(qapp):
    top, bottom = _pair()
    top.set_waveform([1000, -1000] * 500, 0, 100, 900, 999)
    assert _ink_rows(bottom) == []  # nothing left over in the other half (no half-clipped glyphs)


def test_a_stand_alone_view_centers_its_own_hint(qapp):
    alone, _ = _stack_view(None)
    alone.set_header_hint("Double-click to load")
    rows = _ink_rows(alone)
    assert rows and 45 - 12 < (min(rows) + max(rows)) / 2 < 45 + 12  # centered in its (90 px) height
