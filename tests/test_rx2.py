"""core/rx2.py against real .rx2 files (tests/fixtures/rx2, from VelociLoops' own test data, public domain).

These need the compiled library: `uv run python tools/build_velociloops.py` (CI does it before the tests).
"""

import os
import shutil

import pytest

from core import rx2

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "rx2")

needs_library = pytest.mark.skipif(
    not rx2.is_available(), reason="VelociLoops not built - run tools/build_velociloops.py"
)


def test_slice_base_name_uses_the_sampler_alphabet():
    assert rx2.slice_base_name("/x/My_Loop 2.rx2") == "MY-LOOP 2"
    assert rx2.slice_base_name("amen.rx2") == "AMEN"
    assert rx2.slice_base_name("") == "RX2"


@needs_library
def test_stereo_loop_has_ten_slices_that_tile_a_two_second_bar():
    loop = rx2.load(os.path.join(FIXTURES, "120Stereo.rx2"))
    assert (loop.sample_rate, loop.channels, loop.tempo_bpm) == (44100, 2, 120.0)
    assert len(loop.slices) == 10
    assert all(s.right is not None and len(s.left) == len(s.right) for s in loop.slices)
    # one 4/4 bar at 120 BPM is exactly 2 s: the raw slices (no stretch tail) must add up to it
    assert sum(len(s) for s in loop.slices) == 2 * 44100


@needs_library
def test_mono_loop_has_no_right_channel():
    loop = rx2.load(os.path.join(FIXTURES, "120Mono.rx2"))
    assert loop.channels == 1
    assert all(s.right is None for s in loop.slices)
    assert all(-32768 <= v <= 32767 for s in loop.slices for v in s.left)
    assert any(v for s in loop.slices for v in s.left)  # not silence


@needs_library
def test_mono_buffer_markers_sit_on_slice_boundaries():
    loop = rx2.load(os.path.join(FIXTURES, "120Stereo.rx2"))
    samples, markers = loop.mono_buffer()
    assert len(samples) == sum(len(s) for s in loop.slices)
    assert len(markers) == len(loop.slices) - 1
    lengths = [len(s) for s in loop.slices]
    assert markers[0] == lengths[0]
    assert markers[-1] == len(samples) - lengths[-1]
    assert markers == sorted(set(markers))


@needs_library
def test_channel_buffers_keep_stereo_and_share_the_markers():
    stereo = rx2.load(os.path.join(FIXTURES, "120Stereo.rx2"))
    channels, markers = stereo.channel_buffers()
    assert len(channels) == 2 and len(channels[0]) == len(channels[1]) == 2 * 44100
    assert markers == stereo.mono_buffer()[1] and len(markers) == 9
    mono = rx2.load(os.path.join(FIXTURES, "120Mono.rx2"))
    assert len(mono.channel_buffers()[0]) == 1


@needs_library
def test_corrupt_file_raises_a_short_error():
    with pytest.raises(rx2.Rx2Error) as e:
        rx2.load(os.path.join(FIXTURES, "ErrorCorrupt.rx2"))
    assert "ErrorCorrupt.rx2" in str(e.value)


@needs_library
def test_missing_file_raises_rx2_error(tmp_path):
    with pytest.raises(rx2.Rx2Error):
        rx2.load(str(tmp_path / "nope.rx2"))


def test_missing_library_is_reported_not_crashed(monkeypatch, tmp_path):
    monkeypatch.setattr(rx2, "_library", None)
    monkeypatch.setattr(rx2, "_library_error", None)
    monkeypatch.setenv(rx2._LIBRARY_ENV, str(tmp_path / "absent.lib"))
    try:
        assert rx2.is_available() is False
        with pytest.raises(rx2.Rx2Unavailable):
            rx2.load(os.path.join(FIXTURES, "120Mono.rx2"))
    finally:
        # the failure is cached on the module; clear it so later tests see the real library
        monkeypatch.undo()
        rx2._library = None
        rx2._library_error = None
