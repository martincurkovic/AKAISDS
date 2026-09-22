# tests for ui/waveform_view.py's coordinate/clamping math - kept out of
# WaveformView's paintEvent/mouse handlers specifically so it can be tested
# without a QApplication or any actual rendering, same reasoning as
# test_envelope_graph.py's own _adsr_points tests. The one exception is at
# the bottom of this file (wheelEvent's pan-axis behaviour), which needs a
# real WaveformView + a live QApplication - see the section comment there.

import math

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from ui import theme
from ui.waveform_view import (
    _MARKER_ORDER,
    WaveformView,
    build_envelope,
    frame_for_x,
    push_marker,
    x_for_frame,
)


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

    def angleDelta(self):
        return self._angle

    def modifiers(self):
        return self._mods

    def position(self):
        return _FakePos()

    def accept(self):
        pass


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
    # deliberate: there's no vertical content to scroll, and repurposing
    # unmodified vertical wheel/scroll for pan was the source of every
    # axis-guessing bug this section's own comment describes
    view = _loaded_waveform_view()
    before = view._view_start
    view.wheelEvent(_FakeWheelEvent(ax=0, ay=120))
    assert view._view_start == before


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
