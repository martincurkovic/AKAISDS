# core/reverb.py: the S2000 custom-OS reverb prototype, copied for the Samples tab. The golden file was made with the prototype itself.

import hashlib
import json
import math
import struct
from pathlib import Path

import pytest

from core import reverb

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "reverb_golden.json").read_text())


def source(n, loud=False):
    """The test input (the same recipe the golden file was made from): a decaying 3-harmonic burst plus a little LCG noise."""
    out = []
    seed = 12345
    for i in range(n):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        noise = (seed >> 8) % 2001 - 1000
        env = max(0.0, 1 - i / n)
        tone = 9000 * env * (math.sin(i * 0.05) + 0.5 * math.sin(i * 0.11) + 0.25 * math.sin(i * 0.23))
        v = int(tone) + noise
        if loud:
            v *= 4
        out.append(max(-32768, min(32767, v)))
    return out


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=[c["name"] for c in GOLDEN["cases"]])
def test_the_output_is_sample_for_sample_what_the_prototype_made(case):
    out = reverb.process(source(case["n"], case["loud"]), reverb.Params(*case["params"]), case["rate"], trim=case["trim"])
    assert len(out) == case["length"]
    assert out[:12] == case["head"] and out[-12:] == case["tail"]
    assert hashlib.sha256(struct.pack(f"<{len(out)}h", *out)).hexdigest() == case["sha256"]


def test_params_are_checked():
    with pytest.raises(ValueError):
        reverb.Params(decay=100)
    with pytest.raises(ValueError):
        reverb.Params(mix=-1)
    assert reverb.Params() == reverb.Params(*reverb.DEFAULTS)


def test_the_presets_are_valid_controls():
    for decay, size, damping, mix in reverb.PRESETS.values():
        reverb.Params(decay, size, damping, mix)


def test_decay_runs_from_a_tenth_of_a_second_to_five_exponentially():
    assert reverb.rt60_seconds(0) == pytest.approx(0.1)
    assert reverb.rt60_seconds(99) == pytest.approx(5.0)
    assert reverb.rt60_seconds(50) == pytest.approx(0.1 * 50 ** (50 / 99))


def test_damping_has_six_steps_and_none_is_a_pass_through():
    assert reverb.damp_k(0) == reverb.NO_DAMPING_K
    assert {reverb.damping_step(d) for d in range(100)} == {0, 1, 2, 3, 4, 5}
    assert reverb.damp_k(99) == 1


def test_the_tail_stops_early_and_never_makes_the_result_shorter_than_the_source():
    x = source(2000)
    full = reverb.process(x, reverb.Params(80, 50, 30, 30), 44100, trim=None)
    short = reverb.process(x, reverb.Params(80, 50, 30, 30), 44100)
    assert len(full) == 2000 + int(reverb.rt60_seconds(80) * 44100)
    assert 2000 <= len(short) < len(full)
    assert short == full[: len(short)]  # a prefix of the untrimmed one
    assert len(reverb.process(x, reverb.Params(80, 50, 30, 0), 44100)) == 2000  # dry only: nothing rings


def test_silence_in_is_silence_out():
    assert reverb.process([0] * 1500, reverb.Params(), 44100) == [0] * 1500


def test_the_delay_lines_follow_the_sample_rate():
    low = reverb.setup(reverb.Params(), 8000)
    high = reverb.setup(reverb.Params(), 44100)
    assert all(a < b for a, b in zip(low.comb_delays, high.comb_delays))
    assert len(set(high.comb_delays)) == 4 and all(d % 2 == 1 for d in high.comb_delays + high.allpass_delays)


def test_the_longest_result_is_the_source_plus_the_reverb_time():
    assert reverb.max_output_frames(1000, 99, 44100) == 1000 + 220500
    assert reverb.max_output_frames(0, 0, 44100) == 4410


# -- the editor's transform -------------------------------------------------------------------------------------------


def apply(samples, markers, **controls):
    options = dict(decay=60, size=50, damping=30, mix=50, framerate=44100)
    options.update(controls)
    return reverb.reverb_with_markers(samples, *markers, **options)


def test_end_on_the_last_frame_follows_the_tail_and_the_rest_stay_put():
    x = source(4000)
    out, start, loop_start, loop_end, end = apply(x, (100, 500, 900, 3999))
    assert len(out) > len(x)
    assert (start, loop_start, loop_end) == (100, 500, 900)
    assert end == len(out) - 1


def test_an_end_set_earlier_stays_where_it_was():
    out, start, loop_start, loop_end, end = apply(source(4000), (0, 0, 2000, 3000))
    assert end == 3000 and loop_end == 2000


def test_the_markers_stay_ordered_and_inside_the_sample():
    out, start, loop_start, loop_end, end = apply(source(3000), (10, 20, 30, 2999))
    assert 0 <= start <= loop_start <= loop_end <= end <= len(out) - 1


def test_the_transform_works_on_any_int_sequence():
    import array

    out, *_ = apply(array.array("h", source(2000)), (0, 0, 1999, 1999))
    assert isinstance(out, list) and all(isinstance(v, int) for v in out)


def test_transform_for_fills_in_the_controls():
    transform = reverb.transform_for(decay=40, size=50, damping=30, mix=40, framerate=22050)
    direct = reverb.reverb_with_markers(source(2000), 0, 0, 1999, 1999, decay=40, size=50, damping=30, mix=40, framerate=22050)
    assert transform(source(2000), 0, 0, 1999, 1999) == direct


def test_changes_anything_needs_some_wet():
    assert not reverb.changes_anything(0) and reverb.changes_anything(1)
