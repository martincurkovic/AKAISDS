# tests for core/yamaha_load.py - the messages of a native sample load (wave dump(s) + the sample dump) - pure, no Qt/MIDI.

import random

import pytest

from core import yamaha_load as yl
from core import yamaha_params as yp
from core import yamaha_sysex as ysx
from core import yamaha_wave as yw


def build(channels, rate=22050, **kw):
    return yl.build_sample_load(0, "Kick", channels, rate, rng=random.Random(1), **kw)


def sample_of(load):
    return ysx.parse_bulk_dump(load.messages[-1])


def test_a_mono_sample_is_its_wave_dump_then_a_sample_dump_naming_it():
    audio = [(i * 7) % 2000 - 1000 for i in range(5000)]
    load = build([audio])
    assert not load.stereo and len(load.wave_names) == 1
    waves = [ysx.parse_bulk_dump(m) for m in load.messages[:-1]]
    assert all(d.fmt == "WD" and d.name == load.wave_names[0] for d in waves)
    asm = yw.WaveAssembler(load.wave_names[0])
    for d in waves:
        asm.feed(d)
    assert asm.done and asm.frames == audio
    sp = sample_of(load)
    assert sp.fmt == "SP" and sp.name == "Kick" and len(sp.data) == 336
    left = bytes(sp.data[yp.WAVE_NAME_L_OFFSET : yp.WAVE_NAME_L_OFFSET + 16]).decode().strip(" \x00")
    assert left == load.wave_names[0] and yp.wave_name_right(sp.data) == "" and not yp.is_stereo(sp.data)


def test_a_mono_sample_gets_the_same_rate_on_the_right_row_too():
    sp = sample_of(build([[1] * 100], rate=8000))
    assert yp.extract(yp.get("sample", "sampling_frequency_r"), sp.data) == 8000


def test_the_sample_dump_describes_the_audio():
    sp = sample_of(build([[1] * 3000], rate=11025))
    g = lambda k: yp.extract(yp.get("sample", k), sp.data)  # noqa: E731
    assert (g("wave_start_address"), g("wave_length"), g("wave_end_address")) == (0, 3000, 3000)
    assert (g("loop_mode"), g("sampling_frequency_l"), g("original_key_l")) == (0, 11025, 60)
    assert g("loop_start_address") == g("loop_end_address") == 3000 and g("loop_length") == 0
    assert int.from_bytes(sp.data[20:24], "big") == 3004  # the wave's words incl. the 4 guard words


def test_the_right_channel_copies_of_the_addresses_follow_the_left_ones():
    sp = sample_of(build([[1] * 3000, [2] * 3000]))
    for key in ("wave_start_address", "wave_length", "loop_start_address", "loop_length"):
        at = yp.SAMPLE_PARAMETER_BASE + yp.get("sample", key).offset
        assert sp.data[at : at + 4] == sp.data[at + 4 : at + 8], key


def test_a_stereo_sample_has_two_waves_and_names_both():
    left, right = [i % 100 for i in range(4000)], [-(i % 100) for i in range(4000)]
    load = build([left, right])
    assert load.stereo and len(set(load.wave_names)) == 2
    sp = sample_of(load)
    assert yp.is_stereo(sp.data) and yp.wave_name_right(sp.data) == load.wave_names[1]
    assert yp.extract(yp.get("sample", "sampling_frequency_r"), sp.data) == 22050
    by_wave = {}
    for m in load.messages[:-1]:
        d = ysx.parse_bulk_dump(m)
        by_wave.setdefault(d.name, yw.WaveAssembler(d.name)).feed(d)
    assert by_wave[load.wave_names[0]].frames == left and by_wave[load.wave_names[1]].frames == right


def test_waves_are_sent_before_the_sample_and_every_message_is_a_valid_bulk_dump():
    load = build([[5] * 9000])
    kinds = [ysx.parse_bulk_dump(m).fmt for m in load.messages]
    assert kinds[-1] == "SP" and set(kinds[:-1]) == {"WD"} and len(kinds) > 2
    assert load.wire_bytes == sum(len(m) + 2 for m in load.messages) and load.wire_seconds > 3


def test_wave_names_avoid_the_ones_taken_and_look_like_the_units_own():
    taken = [f"SMP {n:06d}" for n in range(0, 1000)]
    load = yl.build_sample_load(0, "x", [[1, 2], [3, 4]], 44100, taken_waves=taken, rng=random.Random(2))
    assert all(w.startswith("SMP ") and w not in taken for w in load.wave_names) and load.wave_names[0] != load.wave_names[1]


@pytest.mark.parametrize(
    "channels, rate",
    [([], 44100), ([[]], 44100), ([[1], [1], [1]], 44100), ([[1, 2], [1]], 44100), ([[1]], 0), ([[1]], 70000)],
)
def test_audio_the_unit_cannot_hold_is_refused(channels, rate):
    with pytest.raises(yl.LoadError):
        yl.check_audio(channels, rate)


def test_names_are_made_safe_and_unique():
    assert yl.sample_name_for("  a very long sample name indeed  ") == "a very long samp"
    assert yl.sample_name_for("café") == "caf?"
    with pytest.raises(yl.LoadError):
        yl.sample_name_for("   ")
    assert yl.unique_name("Kick", ["snare"]) == "Kick"
    assert yl.unique_name("Kick", ["KICK ", "Kick 2"]) == "Kick 3"
    long = yl.unique_name("sixteen chars!!!", ["sixteen chars!!!"])
    assert long == "sixteen chars! 2" and len(long) == 16
