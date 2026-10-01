# tests for core/audio_preview.py - the Slice Editor's click-to-preview
# playback (see ui/slice_editor_window.py, ui/slice_waveform_view.py).
# Device enumeration/resolution is tested against whatever real audio
# devices this machine actually has (there's no way to fake miniaudio's own
# device enumeration); actual playback tests are skipped outright on a
# machine with no audio output device at all, same reasoning
# tests/test_dashboard.py etc. use for anything that would otherwise need
# real hardware.

import os

# must be set BEFORE the first QApplication() call below - see
# test_slice_editor_window.py's own comment on this exact guard for why a
# file lacking it only runs offscreen by accident (whichever OTHER test
# module happens to get collected first)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

import pytest
from PySide6.QtWidgets import QApplication

from core import app_config, audio_preview


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


_HAS_AUDIO_DEVICE = bool(audio_preview.list_output_devices())
_requires_audio_device = pytest.mark.skipif(
    not _HAS_AUDIO_DEVICE, reason="no audio output device available in this environment"
)


def _use_temp_config(monkeypatch, tmp_path):
    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "test_config.json")


# --- device id encoding / lookup --------------------------------------------


def test_device_id_string_round_trips_through_find_output_device(qapp):
    devices = audio_preview.list_output_devices()
    if not devices:
        pytest.skip("no audio output devices available in this environment")
    device = devices[0]
    id_string = audio_preview.device_id_string(device)
    found = audio_preview.find_output_device(id_string)
    assert found is not None
    assert audio_preview.device_id_string(found) == id_string


def test_find_output_device_returns_none_for_an_unknown_id(qapp):
    assert audio_preview.find_output_device("not-a-real-device-id") is None


def test_find_output_device_returns_none_for_falsy_input(qapp):
    assert audio_preview.find_output_device(None) is None
    assert audio_preview.find_output_device("") is None


# --- resolve_output_device ---------------------------------------------------


def test_resolve_output_device_uses_system_default_when_nothing_saved(
    qapp, monkeypatch, tmp_path
):
    # None IS the "system default" answer here (miniaudio.PlaybackDevice
    # resolves a None device_id to the default itself) - not "nothing
    # found", unlike the old QAudioDevice-based version which always
    # returned some concrete device object even for the default case
    _use_temp_config(monkeypatch, tmp_path)
    assert audio_preview.resolve_output_device() is None


def test_resolve_output_device_falls_back_when_saved_device_is_missing(
    qapp, monkeypatch, tmp_path
):
    _use_temp_config(monkeypatch, tmp_path)
    app_config.save_audio_output_device("not-a-real-device-id")
    assert audio_preview.resolve_output_device() is None


def test_resolve_output_device_uses_the_saved_device_when_it_still_exists(
    qapp, monkeypatch, tmp_path
):
    devices = audio_preview.list_output_devices()
    if not devices:
        pytest.skip("no audio output devices available in this environment")
    _use_temp_config(monkeypatch, tmp_path)
    target_id = audio_preview.device_id_string(devices[-1])
    app_config.save_audio_output_device(target_id)
    device = audio_preview.resolve_output_device()
    assert device is not None
    assert audio_preview.device_id_string(device) == target_id


# --- SlicePreviewPlayer: graceful no-device handling ------------------------


def test_play_with_no_available_device_logs_and_noops(qapp, monkeypatch):
    def _raise(*args, **kwargs):
        raise audio_preview.miniaudio.MiniaudioError("no device")

    monkeypatch.setattr(audio_preview.miniaudio, "PlaybackDevice", _raise)
    player = audio_preview.SlicePreviewPlayer()
    player.play([0] * 100, 0, 99, 44100)  # must not raise
    assert player._device is None


def test_play_with_an_empty_slice_range_is_a_noop(qapp):
    player = audio_preview.SlicePreviewPlayer()
    player.play([], 0, -1, 44100)  # must not raise
    assert player._device is None


def test_stop_without_playing_is_a_noop_and_emits_nothing(qapp):
    player = audio_preview.SlicePreviewPlayer()
    received = []
    player.finished.connect(lambda: received.append(True))
    player.stop()  # must not raise
    assert received == []


# --- SlicePreviewPlayer: real playback (skipped without an audio device) ---


@_requires_audio_device
def test_play_starts_a_device_and_stop_emits_finished(qapp):
    player = audio_preview.SlicePreviewPlayer()
    finished_calls = []
    player.finished.connect(lambda: finished_calls.append(True))

    samples = [1000 if i % 2 == 0 else -1000 for i in range(4410)]
    player.play(samples, 0, len(samples) - 1, 44100)
    assert player._device is not None

    player.stop()
    assert player._device is None
    assert finished_calls == [True]


@_requires_audio_device
def test_play_retriggers_instead_of_overlapping(qapp):
    # a second play() call while one is already going must stop the first
    # outright, not layer on top of it - see the class's own docstring
    player = audio_preview.SlicePreviewPlayer()
    samples = [0] * 4410
    player.play(samples, 0, len(samples) - 1, 44100)
    first_device = player._device
    player.play(samples, 0, len(samples) - 1, 44100)
    assert player._device is not None
    assert player._device is not first_device
    player.stop()


@_requires_audio_device
def test_play_advances_current_frame_over_time(qapp):
    # the generator's own progress counter is what _on_tick polls for the
    # playhead/end-of-playback - confirm it actually advances during real
    # playback rather than staying pinned at the start
    player = audio_preview.SlicePreviewPlayer()
    samples = [1000 if i % 2 == 0 else -1000 for i in range(44100)]
    player.play(samples, 0, len(samples) - 1, 44100)
    time.sleep(0.2)
    assert player._current_frame > 0
    player.stop()


# --- SlicePreviewPlayer.play_loop(): simulated loop preview ----------------
# _finished is set by the generator itself, on miniaudio's own real-time
# thread, independent of the Qt _timer - so it can be polled directly after
# a plain time.sleep() without needing a running Qt event loop, same as
# _current_frame above.


def _tone(n_frames, value=1000):
    return [value if i % 2 == 0 else -value for i in range(n_frames)]


@_requires_audio_device
def test_play_loop_with_degenerate_region_falls_back_to_plain_play(qapp):
    # loop_end <= loop_start - nothing to repeat
    player = audio_preview.SlicePreviewPlayer()
    samples = _tone(4410)
    player.play_loop(samples, 0, 2000, 2000, len(samples) - 1, 44100, dwell_ms=50)
    assert player._device is not None
    player.stop()


@_requires_audio_device
def test_play_loop_with_a_finite_dwell_eventually_finishes_on_its_own(qapp):
    player = audio_preview.SlicePreviewPlayer()
    samples = _tone(4410)
    player.play_loop(
        samples, 0, 1000, 2000, len(samples) - 1, 44100, dwell_ms=10
    )
    assert player._device is not None
    assert player._finished is False
    time.sleep(0.5)
    assert player._finished is True
    player.stop()


@_requires_audio_device
def test_play_loop_hold_does_not_finish_on_its_own(qapp):
    # dwell_ms=None ("Hold") - several loop passes' worth of real time
    # should NOT be enough to naturally end it; only an explicit stop()
    # (e.g. the user clicking the waveform again) does
    player = audio_preview.SlicePreviewPlayer()
    samples = _tone(4410)
    player.play_loop(samples, 0, 1000, 2000, len(samples) - 1, 44100, dwell_ms=None)
    time.sleep(0.3)
    assert player._finished is False
    assert player._device is not None
    player.stop()
    assert player._device is None


# --- SlicePreviewPlayer.update_loop_points(): live loop-point dragging -----


@_requires_audio_device
def test_update_loop_points_moves_the_live_playhead_into_the_new_region(qapp):
    # a short loop region (~2.5ms at 44100Hz) passes very rapidly, so the
    # new bounds should take effect within a couple of loop passes -
    # sleeping past that and checking _current_frame (updated by the
    # generator itself, on the real-time thread - see this file's own
    # module comment) confirms the live region actually changed, not just
    # that the call didn't crash
    player = audio_preview.SlicePreviewPlayer()
    samples = _tone(10000)
    player.play_loop(samples, 0, 1000, 1100, len(samples) - 1, 44100, dwell_ms=None)
    time.sleep(0.1)
    assert 1000 <= player._current_frame <= 1101

    player.update_loop_points(5000, 5100)
    time.sleep(0.1)
    assert 5000 <= player._current_frame <= 5101
    player.stop()


@_requires_audio_device
def test_update_loop_points_with_degenerate_bounds_is_ignored(qapp):
    player = audio_preview.SlicePreviewPlayer()
    samples = _tone(10000)
    player.play_loop(samples, 0, 1000, 1100, len(samples) - 1, 44100, dwell_ms=None)
    time.sleep(0.1)

    player.update_loop_points(2000, 2000)  # end <= start - ignored
    time.sleep(0.1)
    assert 1000 <= player._current_frame <= 1101  # unchanged
    player.stop()


def test_update_loop_points_before_any_play_loop_call_does_not_raise(qapp):
    player = audio_preview.SlicePreviewPlayer()
    player.update_loop_points(100, 200)  # must not raise


# --- SHLTO (Loop Tune) live pitch offset ------------------------------------


def test_resample_region_for_cents_with_zero_cents_is_unchanged():
    region = list(range(10))
    assert audio_preview._resample_region_for_cents(region, 0, 9, 0) == region


def test_resample_region_for_cents_positive_shortens_the_region():
    # positive cents -> higher pitch -> a faster effective playback rate ->
    # the SAME audio now takes fewer output frames to play through
    region = list(range(1000))
    out = audio_preview._resample_region_for_cents(region, 0, 999, 50)
    assert len(out) < len(region)


def test_resample_region_for_cents_negative_lengthens_the_region():
    region = list(range(1000))
    out = audio_preview._resample_region_for_cents(region, 0, 999, -50)
    assert len(out) > len(region)


def test_update_loop_tune_cents_before_any_play_loop_call_does_not_raise(qapp):
    player = audio_preview.SlicePreviewPlayer()
    player.update_loop_tune_cents(25)  # must not raise


def test_play_loop_with_loop_tune_cents_never_yields_a_short_chunk(qapp, monkeypatch):
    # same exact-buffer-size regression the short-chunk tests above cover,
    # but with a nonzero SHLTO baked in from the start - the loop region's
    # own resampled length won't generally divide evenly by required_frames
    # either, so this is the same historical bug in a new disguise if the
    # concatenation logic doesn't account for it
    player = audio_preview.SlicePreviewPlayer()
    fake_device = _FakeDevice()
    monkeypatch.setattr(player, "_open_device", lambda framerate: fake_device)

    samples = _tone(20000)
    player.play_loop(
        samples, 0, 1000, 1777, len(samples) - 1, 44100,
        dwell_ms=None, loop_tune_cents=-37,
    )

    required_frames = 256
    expected_bytes = required_frames * audio_preview._BYTES_PER_FRAME
    chunks = [fake_device.gen.send(required_frames) for _ in range(60)]

    assert all(len(chunk) == expected_bytes for chunk in chunks)


def test_update_loop_tune_cents_live_update_never_yields_a_short_chunk(qapp, monkeypatch):
    # a live update mid-playback (e.g. turning the Loop Tune knob while a
    # preview is looping) must be just as safe as one supplied up front
    player = audio_preview.SlicePreviewPlayer()
    fake_device = _FakeDevice()
    monkeypatch.setattr(player, "_open_device", lambda framerate: fake_device)

    samples = _tone(20000)
    player.play_loop(samples, 0, 1000, 1777, len(samples) - 1, 44100, dwell_ms=None)

    required_frames = 256
    expected_bytes = required_frames * audio_preview._BYTES_PER_FRAME
    for _ in range(10):
        assert len(fake_device.gen.send(required_frames)) == expected_bytes

    player.update_loop_tune_cents(41)

    chunks = [fake_device.gen.send(required_frames) for _ in range(60)]
    assert all(len(chunk) == expected_bytes for chunk in chunks)


# --- play()/play_loop() generator: every buffer-fill request must be met
# EXACTLY, never short - regression tests for a real, confirmed audio bug.
# miniaudio.PlaybackDevice._data_callback does
# `ffi.memmove(output, samples_bytes, len(samples_bytes))` and nothing
# else: a short-yielded chunk only overwrites the FIRST len(samples_bytes)
# bytes of its native output buffer, leaving the REST as whatever was
# already there - stale audio from an earlier callback, playing as an
# audible artifact. play_loop()'s own generator used to yield a short
# chunk at the end of EVERY loop pass whenever the loop region's length
# didn't divide evenly by the buffer size (reported as a "chop" at a large
# buffer size, and a "glitch" every loop repetition at a small one - both
# are this). No real audio device needed for these - a fake one captures
# the generator so the test can drive it directly with .send().


class _FakeDevice:
    def __init__(self):
        self.gen = None
        self.stopped = False
        self.closed = False

    def start(self, gen):
        self.gen = gen

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def test_play_loop_never_yields_a_short_chunk_mid_stream(qapp, monkeypatch):
    player = audio_preview.SlicePreviewPlayer()
    fake_device = _FakeDevice()
    monkeypatch.setattr(player, "_open_device", lambda framerate: fake_device)

    samples = _tone(20000)
    # loop region is 778 frames - deliberately NOT a multiple of
    # required_frames below, so every pass used to end with a short chunk
    player.play_loop(samples, 0, 1000, 1777, len(samples) - 1, 44100, dwell_ms=None)

    required_frames = 512  # a real "high buffer size" setting, in frames
    expected_bytes = required_frames * audio_preview._BYTES_PER_FRAME
    # several buffer-fills' worth of the attack PLUS several full loop
    # passes, so this crosses multiple loop-wrap boundaries
    chunk_lengths = [len(fake_device.gen.send(required_frames)) for _ in range(40)]

    assert all(length == expected_bytes for length in chunk_lengths)


def test_play_loop_never_yields_a_short_chunk_with_a_small_buffer(qapp, monkeypatch):
    # same regression, at the OTHER end of the reported symptom - a small
    # buffer size, which still needs to divide the loop region unevenly
    # to reproduce the historical bug
    player = audio_preview.SlicePreviewPlayer()
    fake_device = _FakeDevice()
    monkeypatch.setattr(player, "_open_device", lambda framerate: fake_device)

    samples = _tone(20000)
    player.play_loop(samples, 0, 1000, 1777, len(samples) - 1, 44100, dwell_ms=None)

    required_frames = 64  # a real "low buffer size" setting, in frames
    expected_bytes = required_frames * audio_preview._BYTES_PER_FRAME
    chunk_lengths = [len(fake_device.gen.send(required_frames)) for _ in range(200)]

    assert all(length == expected_bytes for length in chunk_lengths)


def test_play_loop_with_finite_dwell_pads_only_the_true_final_chunk(qapp, monkeypatch):
    # the one legitimate short-chunk case (matching play()'s own existing
    # "exhausted" behaviour) is zero-padded rather than left short, so
    # every chunk fed to miniaudio - including the very last one - is
    # always full-size
    player = audio_preview.SlicePreviewPlayer()
    fake_device = _FakeDevice()
    monkeypatch.setattr(player, "_open_device", lambda framerate: fake_device)

    samples = _tone(3000)
    player.play_loop(samples, 0, 1000, 1200, len(samples) - 1, 44100, dwell_ms=5)

    required_frames = 100
    expected_bytes = required_frames * audio_preview._BYTES_PER_FRAME
    chunk_lengths = []
    for _ in range(60):
        try:
            chunk_lengths.append(len(fake_device.gen.send(required_frames)))
        except StopIteration:
            # the generator function returned - every chunk up to and
            # including the padded final one has already been collected
            break

    assert chunk_lengths  # the tail was actually reached within 60 fills
    assert all(length == expected_bytes for length in chunk_lengths)
