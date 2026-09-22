# tests for ui/waveform_view.py's coordinate/clamping math - kept out of
# WaveformView's paintEvent/mouse handlers specifically so it can be tested
# without a QApplication or any actual rendering, same reasoning as
# test_envelope_graph.py's own _adsr_points tests. The one exception is at
# the bottom of this file (wheelEvent's pan-axis behaviour), which needs a
# real WaveformView + a live QApplication - see the section comment there.

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ui.waveform_view import (
    _MARKER_ORDER,
    WaveformView,
    build_envelope,
    clamp_marker,
    frame_for_x,
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


# --- clamp_marker ----------------------------------------------------------------


def test_clamp_marker_cannot_cross_its_neighbours():
    values = {"start": 0, "loop_start": 100, "loop_end": 500, "end": 999}

    # dragging loop_start past loop_end clamps at loop_end, not past it
    index = _MARKER_ORDER.index("loop_start")
    assert clamp_marker(_MARKER_ORDER, index, 9999, values, frame_count=1000) == 500

    # dragging loop_end before loop_start clamps at loop_start, not before it
    index = _MARKER_ORDER.index("loop_end")
    assert clamp_marker(_MARKER_ORDER, index, 0, values, frame_count=1000) == 100


def test_clamp_marker_stays_within_the_sample_itself_at_the_two_ends():
    values = {"start": 0, "loop_start": 100, "loop_end": 500, "end": 999}

    index = _MARKER_ORDER.index("start")
    assert clamp_marker(_MARKER_ORDER, index, -50, values, frame_count=1000) == 0

    index = _MARKER_ORDER.index("end")
    assert clamp_marker(_MARKER_ORDER, index, 5000, values, frame_count=1000) == 999


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
