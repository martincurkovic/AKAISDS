# tests for core/transient_detection.py - deliberately not exhaustive
# (see AGENTS.md/the PR this shipped in: onset-detection tuning is a
# matter of taste, not correctness) - just enough to catch something
# OBVIOUSLY broken (crashes, degenerate inputs, sensitivity direction).

from core import transient_detection

_FRAMERATE = 44100


def _burst(position, width=200, amplitude=20000, total=10000):
    """A silent buffer with one short loud burst starting at *position*."""
    samples = [0] * total
    for i in range(position, min(total, position + width)):
        samples[i] = amplitude if (i - position) % 2 == 0 else -amplitude
    return samples


def test_empty_or_too_short_returns_nothing():
    assert transient_detection.detect_transients([], 50, _FRAMERATE) == []
    assert transient_detection.detect_transients([1], 50, _FRAMERATE) == []


def test_degenerate_framerate_returns_nothing():
    assert transient_detection.detect_transients([1, 2, 3, 4], 50, 0) == []
    assert transient_detection.detect_transients([1, 2, 3, 4], 50, -1) == []


def test_silence_returns_nothing():
    assert transient_detection.detect_transients([0] * 5000, 100, _FRAMERATE) == []


def test_constant_nonzero_signal_returns_nothing():
    # no energy VARIATION at all - a flat DC-ish buffer has nothing that
    # looks like an onset, even though it isn't silent
    assert (
        transient_detection.detect_transients([500] * 5000, 100, _FRAMERATE) == []
    )


def test_single_burst_is_detected_near_its_actual_start():
    samples = _burst(position=3000, total=10000)
    markers = transient_detection.detect_transients(samples, 80, _FRAMERATE)
    assert len(markers) == 1
    # onset detection reports the START of the window the energy rose in,
    # not a sample-accurate position - allow a generous tolerance rather
    # than pin an exact frame
    assert abs(markers[0] - 3000) < 500


def test_two_widely_spaced_bursts_are_both_detected():
    samples = _burst(1000, total=20000)
    for i in range(10000, 10200):
        samples[i] = 20000 if (i - 10000) % 2 == 0 else -20000
    markers = transient_detection.detect_transients(samples, 80, _FRAMERATE)
    assert len(markers) == 2
    assert markers[0] < markers[1]


def test_two_closely_spaced_bursts_collapse_to_one_via_min_gap():
    # both bursts inside a single min_gap window (30ms default @ 44100 is
    # ~1323 frames) - must not report two markers on top of each other
    samples = _burst(1000, total=10000)
    for i in range(1050, 1100):
        samples[i] = 20000 if (i - 1050) % 2 == 0 else -20000
    markers = transient_detection.detect_transients(samples, 80, _FRAMERATE)
    assert len(markers) == 1


def test_higher_sensitivity_never_finds_fewer_onsets():
    # same signal, increasing sensitivity should only ever reveal MORE
    # (or equally many) candidate onsets, never fewer - a basic monotonicity
    # sanity check, not a claim about exact counts
    samples = _burst(1000, total=20000)
    for i in range(10000, 10200):
        samples[i] = 3000 if (i - 10000) % 2 == 0 else -3000  # a quieter 2nd hit
    counts = [
        len(transient_detection.detect_transients(samples, s, _FRAMERATE))
        for s in (0, 25, 50, 75, 100)
    ]
    assert counts == sorted(counts)


def test_sensitivity_is_clamped_rather_than_raising():
    samples = _burst(1000, total=10000)
    # must not raise for out-of-range input
    transient_detection.detect_transients(samples, -10, _FRAMERATE)
    transient_detection.detect_transients(samples, 150, _FRAMERATE)


def test_markers_are_sorted_and_within_bounds():
    samples = _burst(1000, total=20000)
    for i in range(10000, 10200):
        samples[i] = 20000 if (i - 10000) % 2 == 0 else -20000
    markers = transient_detection.detect_transients(samples, 90, _FRAMERATE)
    assert markers == sorted(markers)
    assert all(0 <= m < len(samples) for m in markers)


def test_sensitivity_zero_is_always_fully_off():
    # 0 is the live sensitivity slider's own rest position - must mean
    # "nothing detected," not "only the single loudest hit" (which a bare
    # threshold-at-peak_flux would otherwise still pick out)
    samples = _burst(1000, total=10000, amplitude=32000)
    assert transient_detection.detect_transients(samples, 0, _FRAMERATE) == []


def test_compute_flux_plus_markers_from_flux_matches_detect_transients():
    # the split exists so a live-updating slider can cache compute_flux()'s
    # output and re-run only markers_from_flux() per tick - confirm that
    # split actually produces identical results to the one-shot wrapper
    samples = _burst(1000, total=20000)
    for i in range(10000, 10200):
        samples[i] = 20000 if (i - 10000) % 2 == 0 else -20000
    flux, window_frames = transient_detection.compute_flux(samples, _FRAMERATE)
    assert flux is not None
    for sensitivity in (0, 25, 50, 75, 100):
        assert transient_detection.markers_from_flux(
            flux, window_frames, sensitivity, len(samples)
        ) == transient_detection.detect_transients(samples, sensitivity, _FRAMERATE)


def test_compute_flux_is_none_for_silence_or_too_short():
    assert transient_detection.compute_flux([0] * 5000, _FRAMERATE) == (None, None)
    assert transient_detection.compute_flux([1], _FRAMERATE) == (None, None)


def test_markers_from_flux_with_no_flux_returns_nothing():
    assert transient_detection.markers_from_flux(None, 100, 50, 5000) == []
    assert transient_detection.markers_from_flux([], 100, 50, 5000) == []
