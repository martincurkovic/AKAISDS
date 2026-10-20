# core/time_stretch.py: the port against the real C++ header's output (tests/fixtures/time_stretch_golden.json, made by
# tools/make_time_stretch_golden.py), and the properties the editors rely on (length, pitch kept, markers).

import json
import math
from pathlib import Path

import pytest

from core import time_stretch as ts

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "time_stretch_golden.json").read_text())


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: f"grain{c['grain']}-x{c['factor']}{'-pitch' if c['comp'] else ''}")
def test_the_port_matches_the_cpp_output(case):
    run = ts.pitch_shift if case["comp"] else ts.stretch
    out = run(GOLDEN["input"], case["factor"], case["grain"])
    assert len(out) == len(case["output"])
    worst = max(abs(a - b) for a, b in zip(out, case["output"]))
    assert worst < 1e-5  # the C++ works in float32 at the edges (interp results, the return value)


def test_output_length_follows_the_factor():
    samples = [0] * 1000
    assert len(ts.stretch(samples, 1.0, 100)) == 1000
    assert len(ts.stretch(samples, 2.0, 100)) == 2000
    assert len(ts.stretch(samples, 0.5, 100)) == 500
    assert ts.stretch([], 2.0, 100) == []


def test_a_factor_of_one_reproduces_the_input_in_the_first_grain():
    samples = [int(10000 * math.sin(i * 0.05)) for i in range(2000)]
    out = ts.stretch(samples, 1.0, 400)
    assert out[:200] == samples[:200]


def test_integers_stay_clamped_sixteen_bit_integers():
    out = ts.stretch([32767, -32768] * 500, 1.5, 64)
    assert all(isinstance(v, int) and -32768 <= v <= 32767 for v in out)


def _power_at(samples, period):
    """Signal power at a frequency of 1/period cycles per sample (Goertzel)."""
    w = 2 * math.pi / period
    re = sum(v * math.cos(w * i) for i, v in enumerate(samples))
    im = sum(v * math.sin(w * i) for i, v in enumerate(samples))
    return re * re + im * im


def test_stretching_keeps_the_pitch_when_the_grain_joins_land_on_whole_cycles():
    # Grains are not phase aligned (like the sampler's own, as far as known): a tone gets a phase jump at every join unless the step
    # between grain starts is a whole number of cycles. Here a grain of 1000 steps 0.6 x 1000 / 2 = 300 source samples = 3 cycles.
    sine = [int(12000 * math.sin(2 * math.pi * i / 100)) for i in range(8000)]  # a 100-sample period
    out = ts.stretch(sine, 2.0, 1000)[1000:-1000]
    at_pitch = _power_at(out, 100)
    for wrong in (50, 70, 90, 110, 140, 200):
        assert at_pitch > 20 * _power_at(out, wrong)


def test_unaligned_grains_phase_a_tone_but_keep_its_level():
    # documents the warble: the dominant period drifts off 100, but the level (RMS) stays about the same
    sine = [int(12000 * math.sin(2 * math.pi * i / 100)) for i in range(8000)]
    out = ts.stretch(sine, 1.5, 800)[1000:-1000]
    rms = lambda x: math.sqrt(sum(v * v for v in x) / len(x))  # noqa: E731
    assert 0.5 < rms(out) / rms(sine) < 1.1


def test_markers_move_in_proportion_and_stay_ordered():
    samples = list(range(1000))
    new, start, loop_start, loop_end, end = ts.stretch_with_markers(
        samples, 100, 300, 600, 999, factor=2.0, grain_frames=100
    )
    assert len(new) == 2000 and end == 1999  # the old last frame is the new last frame
    assert start == round(100 * 1999 / 999)
    assert 0 <= start <= loop_start <= loop_end <= end


def test_a_bad_factor_is_refused():
    with pytest.raises(ValueError):
        ts.stretch([1, 2, 3], 0, 100)


def test_pitch_shift_keeps_the_length_and_the_markers():
    samples = list(range(1000))
    new, start, loop_start, loop_end, end = ts.pitch_shift_with_markers(
        samples, 100, 300, 600, 900, ratio=ts.pitch_ratio(7), grain_frames=100
    )
    assert len(new) == 1000 and (start, loop_start, loop_end, end) == (100, 300, 600, 900)


def test_pitch_ratio_is_equal_tempered():
    assert ts.pitch_ratio(12) == pytest.approx(2.0)
    assert ts.pitch_ratio(-12) == pytest.approx(0.5)
    assert ts.pitch_ratio(0, 50) == pytest.approx(2 ** (0.5 / 12))
    assert ts.pitch_ratio(0) == 1.0


def test_pitch_shift_moves_the_pitch_an_octave_when_the_grains_line_up():
    # (unaligned grains pull a tone's pitch back toward the original - see the Time Stretch notes in AGENTS.md)
    sine = [int(12000 * math.sin(2 * math.pi * i / 100)) for i in range(8000)]
    out = ts.pitch_shift(sine, 2.0, 1000)[1000:-1000]
    at_octave = _power_at(out, 50)
    for wrong in (40, 60, 70, 100):
        assert at_octave > 20 * _power_at(out, wrong)


def test_pitch_shift_refuses_a_bad_ratio_and_the_edit_table_covers_both_modes():
    with pytest.raises(ValueError):
        ts.pitch_shift([1, 2, 3], 0, 100)
    assert set(ts.EDITS) == {"stretch", "pitch"}
    out = ts.transform_for("pitch", ratio=1.5, grain_frames=64)(list(range(500)), 0, 0, 499, 499)
    assert len(out[0]) == 500
