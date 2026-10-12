# tests for core/yamaha_wave.py - the Yamaha WAVE DATA ("WD") bulk dump.
# The fixtures are REAL captures from a user's A4000 (2026-10-06): the whole 128-frame "sine wave" (one message) and the first
# three of the ~4 messages of a 4000-frame user sample ("SMP 045070", a 440 Hz sine of amplitude 24000 sent at 44.1 kHz).
# Never regenerate them from the codec. The sine's frames were ALSO read over Sample Dump Standard: md5 of its int16
# frames is 53256086e9be - the proof that the layout below is right.

import hashlib
import os
import struct

import pytest

from core import yamaha_sysex as y
from core import yamaha_wave as w

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "a4000")


def load(name):
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return y.parse_bulk_dump(y.split_messages(fh.read())[0])


def fingerprint(frames):
    return hashlib.md5(struct.pack(f"<{len(frames)}h", *frames)).hexdigest()[:12]


def test_the_real_sine_wave_matches_its_sample_dump_standard_fingerprint():
    a = w.WaveAssembler("sine wave")
    new = a.feed(load("wave_sine_wave.syx"))
    assert a.done and a.total_words == 132 and a.total_frames == 128
    assert len(new) == 128 and a.frames == new
    assert fingerprint(a.frames) == "53256086e9be"  # measured over SDS for the same sample
    assert new[:4] == [0, 1607, 3211, 4807]


def test_the_guard_words_are_not_audio():
    a = w.WaveAssembler("sine wave")
    a.feed(load("wave_sine_wave.syx"))
    assert len(a._words) == 132 and len(a.frames) == 128  # four loop-guard words held back


def test_real_blocks_of_a_longer_wave_join_without_a_seam():
    a = w.WaveAssembler("SMP 045070")
    chunks = [a.feed(load(f"wave_smp045070_block{i}.syx")) for i in range(3)]
    assert a.total_words == 4000 and a.total_frames == 3996 and not a.done
    assert [len(c) for c in chunks] == [980, 1016, 1016]
    assert a.blocks == 3 and a.started
    frames = a.frames
    assert frames[:4] == [0, 1503, 3001, 4487]
    # a 440 Hz sine of amplitude 24000 at 44.1 kHz never moves more than ~1505 between frames - a misaligned
    # joint (a byte lost or repeated at a message boundary) would jump by thousands
    assert max(abs(b - a_) for a_, b in zip(frames, frames[1:])) < 1600
    assert max(abs(f) for f in frames) <= 24000


def test_a_message_out_of_order_or_for_another_wave_is_refused():
    a = w.WaveAssembler("SMP 045070")
    with pytest.raises(y.YamahaSysexError):
        a.feed(load("wave_smp045070_block1.syx"))  # block 1 before block 0
    a.feed(load("wave_smp045070_block0.syx"))
    with pytest.raises(y.YamahaSysexError):
        a.feed(load("wave_smp045070_block0.syx"))  # a repeat
    with pytest.raises(y.YamahaSysexError):
        w.WaveAssembler("someone else").feed(load("wave_sine_wave.syx"))


def test_built_messages_have_the_measured_sizes_and_round_trip():
    frames = [(i * 37) % 30000 - 15000 for i in range(5000)]
    messages = w.build_wave_messages(0, "SMP 000001", frames)
    dumps = [y.parse_bulk_dump(m) for m in messages]
    assert [len(d.data) for d in dumps][:2] == [2035, 2034]  # as the real unit's
    assert [int.from_bytes(d.data[:2], "big") for d in dumps] == list(range(len(dumps)))
    a = w.WaveAssembler("SMP 000001")
    got = []
    for d in dumps:
        got += a.feed(d)
    assert a.done and got == frames and a.frames == frames


def test_a_short_wave_and_an_empty_one_still_build():
    for n in (0, 1, 3, 4, 5):
        frames = list(range(1, n + 1))
        a = w.WaveAssembler("tiny")
        got = []
        for m in w.build_wave_messages(0, "tiny", frames):
            got += a.feed(y.parse_bulk_dump(m))
        assert a.done and got == frames, n


def test_the_real_dump_is_rebuilt_byte_for_byte_by_the_same_layout():
    real = load("wave_sine_wave.syx")
    a = w.WaveAssembler("sine wave")
    frames = a.feed(real)
    rebuilt = y.parse_bulk_dump(w.build_wave_messages(0, "sine wave", frames)[0])
    again = w.WaveAssembler("sine wave")
    assert again.feed(rebuilt) == frames and again.total_words == 132
