# tests for ui/waveform_view.py's coordinate/clamping math - kept out of
# WaveformView's paintEvent/mouse handlers specifically so it can be tested
# without a QApplication or any actual rendering, same reasoning as
# test_envelope_graph.py's own _adsr_points tests.

import pytest

from ui.waveform_view import (
    _MARKER_ORDER,
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


def test_frame_for_x_spans_the_full_range():
    assert frame_for_x(0, width=100, frame_count=1000) == 0
    assert frame_for_x(99, width=100, frame_count=1000) == 999


def test_x_for_frame_and_frame_for_x_round_trip():
    width, frame_count = 400, 5000
    for frame in (0, 1, 2500, 4999):
        x = x_for_frame(frame, width, frame_count)
        assert frame_for_x(x, width, frame_count) == pytest.approx(frame, abs=1)


def test_x_for_frame_handles_a_single_frame_sample_without_dividing_by_zero():
    assert x_for_frame(0, width=100, frame_count=1) == 0.0


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
