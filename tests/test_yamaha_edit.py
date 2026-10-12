# tests for core/yamaha_edit.py (editing a Yamaha sample's audio - pure functions) and the carried-over parameters of a copy.

import wave

import pytest

from core import demo_a4000 as demo
from core import sample_editing as se
from core import yamaha_edit as ye
from core import yamaha_load as yl
from core import yamaha_params as yp

MONO = [100, -200, 300, -400, 500, -600, 700, -800]


def test_copy_name_keeps_the_suffix_and_stays_within_sixteen_characters():
    assert ye.copy_name("kick", " TRIM") == "kick TRIM"
    long = ye.copy_name("A VERY LONG SAMPLE NAME", " NORM")
    assert long.endswith(" NORM") and len(long) <= 16
    assert ye.copy_name("EXACTLY16CHARS!!", " REV") == "EXACTLY16CHA REV"  # 12 + 4 = 16


def test_trim_cuts_both_channels_identically_and_rebases_the_markers():
    left, right = list(range(10)), list(range(100, 110))
    (new_l, new_r), markers = ye.apply_edit("trim", [left, right], (2, 3, 6, 7))
    assert new_l == [2, 3, 4, 5, 6, 7] and new_r == [102, 103, 104, 105, 106, 107]
    assert markers == (0, 1, 4, 5)


def test_reverse_mirrors_both_channels_and_the_markers():
    (new_l, new_r), markers = ye.apply_edit("reverse", [[1, 2, 3, 4], [5, 6, 7, 8]], (0, 1, 2, 3))
    assert new_l == [4, 3, 2, 1] and new_r == [8, 7, 6, 5]
    assert markers == (0, 1, 2, 3)


def test_a_stereo_normalise_uses_one_gain_so_the_balance_is_kept():
    quiet, loud = [1000, -1000, 500, -500], [4000, -4000, 2000, -2000]
    (new_q, new_l), _ = ye.apply_edit("normalise", [quiet, loud], (0, 0, 3, 3))
    assert max(abs(v) for v in new_l) == se._MAX_AMPLITUDE
    # the quiet channel is still a quarter as loud as the loud one, not boosted to full scale on its own
    assert abs(new_q[0] / new_l[0] - 0.25) < 0.01


def test_a_mono_normalise_reaches_full_scale():
    (new,), _ = ye.apply_edit("normalise", [[1000, -2000, 500]], (0, 0, 2, 2))
    assert max(abs(v) for v in new) == se._MAX_AMPLITUDE


def test_fade_leaves_the_markers_and_filters_run_on_every_channel():
    channels = [[10000] * 200, [10000] * 200]
    (fl, fr), markers = ye.apply_edit("fade", channels, (50, 50, 150, 150))
    assert markers == (50, 50, 150, 150) and fl[0] == 0 and fl[100] == 10000 and fr[-1] == 0
    opts = dict(lowpass_enabled=True, lowpass_cutoff_hz=500, lowpass_slope_db_per_octave=12)
    out, _ = ye.apply_edit("filter", channels, (0, 0, 199, 199), framerate=22050, filter_options=opts)
    assert len(out) == 2 and len(out[0]) == 200


def test_an_unknown_edit_is_refused():
    with pytest.raises(ValueError):
        ye.apply_edit("echo", [MONO], (0, 0, 7, 7))


# --- the carried-over parameters ----------------------------------------------------------------------


def _source(loop_mode, key=61, fine=-7, coarse=3):
    data = demo.make_sample_payload("src")
    for name, value in (("loop_mode", loop_mode), ("original_key_l", key), ("original_key_r", key),
                        ("fine_tune_l", fine), ("fine_tune_r", fine), ("coarse_tune", coarse)):
        yp.store(yp.get("sample", name), data, value)
    return data


def test_a_looping_copy_carries_key_tuning_mode_and_the_new_loop_addresses():
    params = ye.params_for_copy(_source(1), (10, 20, 80, 90))
    assert params["original_key_l"] == 61 and params["fine_tune_l"] == -7 and params["coarse_tune"] == 3
    assert params["loop_mode"] == 1
    assert (params["wave_start_address"], params["wave_end_address"], params["wave_length"]) == (10, 91, 81)
    assert (params["loop_start_address"], params["loop_end_address"], params["loop_length"]) == (20, 81, 61)


def test_a_non_looping_copy_parks_the_loop_at_the_wave_end_with_no_length():
    params = ye.params_for_copy(_source(0), (0, 0, 49, 49))
    assert params["loop_mode"] == 0
    assert (params["loop_start_address"], params["loop_end_address"], params["loop_length"]) == (50, 50, 0)


def test_the_carried_rows_make_it_into_the_sample_dump_and_read_back():
    params = ye.params_for_copy(_source(1), (0, 100, 400, 499))
    payload = yl.build_sample_payload("copy", ["SMP 000001"], 500, 22050, params=params)
    g = lambda key: yp.extract(yp.get("sample", key), payload)  # noqa: E731
    assert g("original_key_l") == 61 and g("fine_tune_l") == -7 and g("loop_mode") == 1
    assert (g("wave_start_address"), g("wave_end_address"), g("wave_length")) == (0, 500, 500)
    assert (g("loop_start_address"), g("loop_end_address"), g("loop_length")) == (100, 401, 301)
    assert g("sampling_frequency_l") == 22050


def test_an_uncarriable_row_is_refused():
    with pytest.raises(yl.LoadError):
        yl.build_sample_payload("x", ["SMP 000001"], 10, 22050, params={"filter_cutoff": 5})


# --- the WAV a copy is sent from ----------------------------------------------------------------------


@pytest.mark.parametrize("count", [1, 2])
def test_write_wav_round_trips_mono_and_stereo(tmp_path, count):
    channels = [[(i * 37 - 500) % 3000 - 1500 for i in range(300)] for _ in range(count)]
    if count == 2:
        channels[1] = [-v for v in channels[1]]
    path = tmp_path / "x.wav"
    ye.write_wav(path, channels, 22050)
    with wave.open(str(path), "rb") as f:
        assert (f.getnchannels(), f.getsampwidth(), f.getframerate(), f.getnframes()) == (count, 2, 22050, 300)
        raw = f.readframes(300)
    values = [int.from_bytes(raw[i : i + 2], "little", signed=True) for i in range(0, len(raw), 2)]
    for index in range(count):
        assert values[index::count] == channels[index]


def test_write_wav_clamps_instead_of_wrapping(tmp_path):
    path = tmp_path / "c.wav"
    ye.write_wav(path, [[40000, -40000, 5]], 8000)
    with wave.open(str(path), "rb") as f:
        raw = f.readframes(3)
    assert [int.from_bytes(raw[i : i + 2], "little", signed=True) for i in range(0, 6, 2)] == [32767, -32768, 5]
