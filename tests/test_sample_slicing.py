# tests for core/sample_slicing.py - pure sample-buffer math for the Slice
# Editor, no Qt/MIDI/hardware needed, should be instant

from core.sample_slicing import (
    equal_slice_markers,
    find_nearest_zero_crossing,
    slice_bounds,
    slice_samples,
)


# --- find_nearest_zero_crossing --------------------------------------------


def test_zero_crossing_already_at_zero_is_a_no_op():
    samples = [100, 0, -100]
    assert find_nearest_zero_crossing(samples, 1) == 1


def test_zero_crossing_finds_a_sign_change_nearby():
    samples = [100, 50, -50, -100]  # crosses zero between index 1 and 2
    assert find_nearest_zero_crossing(samples, 0) == 1  # 50 closer to zero than -50


def test_zero_crossing_picks_the_closer_of_the_two_straddling_samples():
    samples = [100, 10, -90]  # crossing between idx 1 (10) and idx 2 (-90)
    assert find_nearest_zero_crossing(samples, 1) == 1
    samples2 = [100, 90, -10]  # crossing between idx 1 (90) and idx 2 (-10)
    assert find_nearest_zero_crossing(samples2, 1) == 2


def test_zero_crossing_searches_outward_for_the_nearest_one():
    # nearest crossing to frame 5 is at index 6/7 (distance 1), not the one
    # at index 0/1 (distance 5)
    samples = [10, -10, 5, 5, 5, 5, 5, -5, 5]
    assert find_nearest_zero_crossing(samples, 5) in (6, 7)


def test_zero_crossing_falls_back_to_original_frame_when_nothing_in_range():
    # every sample positive (DC-offset-like) - no crossing exists at all
    samples = [100] * 50
    assert find_nearest_zero_crossing(samples, 25, search_radius=10) == 25


def test_zero_crossing_respects_search_radius():
    # crossing exists, but well outside a small search radius
    samples = [100] * 20 + [-100] * 20
    assert find_nearest_zero_crossing(samples, 0, search_radius=5) == 0


def test_zero_crossing_clamps_an_out_of_range_frame():
    samples = [1, 2, 3]
    assert find_nearest_zero_crossing(samples, 999) == 2
    assert find_nearest_zero_crossing(samples, -5) == 0


def test_zero_crossing_on_empty_samples_returns_frame_unchanged():
    assert find_nearest_zero_crossing([], 3) == 3


# --- equal_slice_markers ----------------------------------------------------


def test_equal_slice_markers_splits_evenly():
    # 100 frames (0..99), 4 equal slices -> 3 interior boundaries at 25/50/75
    assert equal_slice_markers(0, 99, 4) == [25, 50, 75]


def test_equal_slice_markers_two_slices_is_one_boundary():
    assert equal_slice_markers(0, 9, 2) == [5]


def test_equal_slice_markers_count_below_two_returns_nothing():
    assert equal_slice_markers(0, 99, 1) == []
    assert equal_slice_markers(0, 99, 0) == []


def test_equal_slice_markers_stays_strictly_inside_start_end():
    markers = equal_slice_markers(10, 20, 8)
    assert all(10 < m < 20 for m in markers)


def test_equal_slice_markers_dedupes_when_region_too_short_for_the_count():
    # only 3 usable frames between start/end - can't produce 9 distinct
    # interior boundaries, so fewer come back, never a raised error
    markers = equal_slice_markers(0, 4, 10)
    assert markers == sorted(set(markers))
    assert all(0 < m < 4 for m in markers)


def test_equal_slice_markers_degenerate_region_returns_nothing():
    assert equal_slice_markers(5, 5, 4) == []
    assert equal_slice_markers(5, 6, 4) == []  # only one interior frame's worth


# --- slice_bounds / slice_samples -------------------------------------------


def test_slice_bounds_no_markers_is_one_slice_covering_the_whole_region():
    assert slice_bounds(10, 20, []) == [(10, 20)]


def test_slice_bounds_covers_start_to_end_with_no_gaps_or_overlaps():
    bounds = slice_bounds(0, 99, [25, 50, 75])
    assert bounds == [(0, 24), (25, 49), (50, 74), (75, 99)]
    # contiguous: each slice's end is exactly one before the next start
    for (_, end), (next_start, _) in zip(bounds, bounds[1:]):
        assert next_start == end + 1
    assert bounds[0][0] == 0
    assert bounds[-1][1] == 99


def test_slice_bounds_sorts_unsorted_markers():
    assert slice_bounds(0, 99, [75, 25, 50]) == [(0, 24), (25, 49), (50, 74), (75, 99)]


def test_slice_bounds_drops_markers_outside_or_on_the_open_interval():
    # only 50 is actually strictly inside (10, 90) - 5, 10 and 90 are dropped
    assert slice_bounds(10, 90, [5, 10, 50, 90, 200]) == [(10, 49), (50, 90)]


def test_slice_bounds_dedupes_duplicate_markers():
    assert slice_bounds(0, 99, [50, 50, 50]) == [(0, 49), (50, 99)]


def test_slice_samples_returns_the_right_sub_buffers():
    samples = list(range(100))
    slices = slice_samples(samples, 0, 99, [25, 50, 75])
    assert len(slices) == 4
    assert slices[0] == list(range(0, 25))
    assert slices[1] == list(range(25, 50))
    assert slices[2] == list(range(50, 75))
    assert slices[3] == list(range(75, 100))


def test_slice_samples_with_no_markers_returns_a_single_trimmed_slice():
    samples = list(range(100))
    slices = slice_samples(samples, 10, 40, [])
    assert len(slices) == 1
    assert slices[0] == list(range(10, 41))


def test_slice_samples_single_frame_slices():
    samples = [10, 20, 30, 40, 50]
    # markers strictly between start(0) and end(4) only - 4 isn't a valid
    # interior marker (it IS end), so this yields 4 slices, not 5
    slices = slice_samples(samples, 0, 4, [1, 2, 3])
    assert slices == [[10], [20], [30], [40, 50]]
