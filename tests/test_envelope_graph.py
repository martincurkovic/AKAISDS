# tests for ui/envelope_graph.py's coordinate math - extracted out of
# paintEvent into _adsr_points()/_env2_points() specifically so it can be
# tested without a QApplication or any actual rendering.

import pytest

from ui.envelope_graph import SUSTAIN_HOLD_FRACTION, _adsr_points, _env2_points


# --- _adsr_points (ADSREnvelopeGraph / ENV1) -----------------------------------


def test_adsr_starts_at_silence_and_ends_at_silence():
    points = _adsr_points(attack=20, decay=30, sustain=50, release=25, w=200, h=80)
    assert points[0] == (0, 80)
    assert points[-1][1] == 80


def test_adsr_reaches_the_full_width_only_when_every_stage_is_maxed():
    # each of attack/decay/release has its own fixed 1/3 share of the
    # non-hold width (see _adsr_points) - the full width is only reached
    # when all three are individually at their max (99), not just at some
    # combination that used to sum to "enough" under the old normalization
    points = _adsr_points(attack=99, decay=99, sustain=50, release=99, w=200, h=80)
    assert points[-1][0] == pytest.approx(200)


def test_adsr_stage_width_is_a_fixed_share_not_a_relative_one():
    # regression test for a real bug: widths used to be normalized against
    # time_total = attack+decay+release, so turning ONE knob silently
    # resized the OTHER stages' segments too, even though their own values
    # never changed - a user noticed this by comparing two screenshots
    # where only Decay differed and Attack's segment visibly changed width.
    # Each stage must now depend only on its own value.
    short_decay = _adsr_points(attack=25, decay=0, sustain=99, release=45, w=200, h=80)
    long_decay = _adsr_points(attack=25, decay=99, sustain=99, release=45, w=200, h=80)

    short_attack_width = short_decay[1][0] - short_decay[0][0]
    long_attack_width = long_decay[1][0] - long_decay[0][0]
    assert short_attack_width == pytest.approx(long_attack_width)

    short_release_width = short_decay[4][0] - short_decay[3][0]
    long_release_width = long_decay[4][0] - long_decay[3][0]
    assert short_release_width == pytest.approx(long_release_width)


def test_adsr_peak_reaches_the_top_after_attack():
    points = _adsr_points(attack=20, decay=30, sustain=50, release=25, w=200, h=80)
    _x0y0, (_x1, y1), *_rest = points
    assert y1 == 0


def test_full_sustain_holds_at_the_top_not_partway_down():
    points = _adsr_points(attack=10, decay=10, sustain=99, release=10, w=100, h=50)
    sustain_start_y = points[2][1]
    sustain_end_y = points[3][1]
    assert sustain_start_y == pytest.approx(0)
    assert sustain_end_y == pytest.approx(0)


def test_zero_sustain_drops_all_the_way_to_silence():
    points = _adsr_points(attack=10, decay=10, sustain=0, release=10, w=100, h=50)
    assert points[2][1] == pytest.approx(50)
    assert points[3][1] == pytest.approx(50)


def test_all_zero_rates_does_not_divide_by_zero():
    # each stage divides by the constant 99 now, not a live sum of the
    # other two, so this can't actually divide by zero any more - kept as
    # a plain "still renders a sane all-zero envelope" sanity check
    points = _adsr_points(attack=0, decay=0, sustain=0, release=0, w=100, h=50)
    assert all(isinstance(y, (int, float)) for _x, y in points)


def test_sustain_hold_segment_has_a_fixed_width_regardless_of_other_values():
    # the whole point of SUSTAIN_HOLD_FRACTION - sustain is a LEVEL, not a
    # duration, so its on-screen segment must not stretch/shrink with
    # attack/decay/release the way a real time-based stage would
    short_ar = _adsr_points(attack=5, decay=5, sustain=50, release=5, w=100, h=50)
    long_ar = _adsr_points(attack=90, decay=90, sustain=50, release=90, w=100, h=50)

    short_hold_width = short_ar[3][0] - short_ar[2][0]
    long_hold_width = long_ar[3][0] - long_ar[2][0]

    assert short_hold_width == pytest.approx(long_hold_width)
    assert short_hold_width == pytest.approx(SUSTAIN_HOLD_FRACTION * 100)


# --- _env2_points (Envelope2Graph / ENV2) --------------------------------------


def test_env2_starts_at_silence():
    points = _env2_points([(10, 50), (10, 0), (10, 99), (10, 0)], w=200, h=80)
    assert points[0] == (0, 80)


def test_env2_reaches_the_full_width_only_when_every_stage_rate_is_maxed():
    # each stage has its own fixed w/4 share (see _env2_points) - the full
    # width is only reached when every rate is individually at max (99)
    points = _env2_points([(99, 50), (99, 0), (99, 99), (99, 0)], w=200, h=80)
    assert points[-1][0] == pytest.approx(200)


def test_env2_stage_width_is_a_fixed_share_not_a_relative_one():
    # regression test for a real bug, same as ADSREnvelopeGraph's - widths
    # used to be normalized against sum(rate for rate, _ in stages), so
    # raising R1 alone silently resized R2/R3/R4's segments too
    short_r1 = _env2_points([(0, 50), (40, 0), (40, 99), (40, 0)], w=200, h=80)
    long_r1 = _env2_points([(99, 50), (40, 0), (40, 99), (40, 0)], w=200, h=80)

    r2_width_short = short_r1[2][0] - short_r1[1][0]
    r2_width_long = long_r1[2][0] - long_r1[1][0]
    assert r2_width_short == pytest.approx(r2_width_long)


def test_env2_is_not_forced_into_a_monotonic_adsr_shape():
    # this is the entire reason Envelope2Graph exists separately from
    # ADSREnvelopeGraph - ENV2 is a genuine 4-stage rate/level generator
    # where level can rise or fall freely between any two stages
    points = _env2_points([(10, 50), (10, 0), (10, 99), (10, 0)], w=200, h=80)
    ys = [y for _x, y in points]
    # level: 50 -> 0 -> 99 -> 0 translates to y: falls, RISES, falls, RISES -
    # not a shape a plain ADSR (attack-up, decay/release-down only) can draw
    assert ys[2] > ys[1]  # a rise following a fall
    assert ys[4] > ys[3]  # another rise after that


def test_env2_all_zero_rates_does_not_divide_by_zero():
    # same reasoning as ADSREnvelopeGraph's version - each stage now
    # divides by the constant 99, not a live sum of sibling rates
    points = _env2_points([(0, 0), (0, 0), (0, 0), (0, 0)], w=200, h=80)
    assert all(isinstance(y, (int, float)) for _x, y in points)


def test_env2_a_zero_rate_stage_still_shows_its_level_at_the_same_x():
    # a stage with no duration of its own is still a real level change at
    # whatever x the previous stage ended on, not skipped entirely
    points = _env2_points([(50, 20), (0, 80), (50, 20), (0, 0)], w=200, h=100)
    stage1_x = points[1][0]
    stage2_x = points[2][0]
    assert stage2_x == pytest.approx(stage1_x)
    assert points[1][1] != points[2][1]
