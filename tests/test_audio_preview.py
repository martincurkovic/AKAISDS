# tests for core/audio_preview.py - the Slice Editor's click-to-preview
# playback (see ui/slice_editor_window.py, ui/slice_waveform_view.py).
# Device enumeration/resolution is tested against whatever real audio
# devices this machine actually has (there's no way to fake QMediaDevices
# itself); actual playback tests are skipped outright on a machine with no
# audio output device at all, same reasoning tests/test_dashboard.py etc.
# use for anything that would otherwise need real hardware.

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaDevices

from core import app_config, audio_preview


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


_HAS_AUDIO_DEVICE = not QMediaDevices.defaultAudioOutput().isNull()
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
    _use_temp_config(monkeypatch, tmp_path)
    device = audio_preview.resolve_output_device()
    assert audio_preview.device_id_string(device) == audio_preview.device_id_string(
        QMediaDevices.defaultAudioOutput()
    )


def test_resolve_output_device_falls_back_when_saved_device_is_missing(
    qapp, monkeypatch, tmp_path
):
    _use_temp_config(monkeypatch, tmp_path)
    app_config.save_audio_output_device("not-a-real-device-id")
    device = audio_preview.resolve_output_device()
    assert audio_preview.device_id_string(device) == audio_preview.device_id_string(
        QMediaDevices.defaultAudioOutput()
    )


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
    assert audio_preview.device_id_string(device) == target_id


# --- SlicePreviewPlayer: graceful no-device handling ------------------------


def test_play_with_no_available_device_logs_and_noops(qapp, monkeypatch):
    monkeypatch.setattr(audio_preview, "resolve_output_device", lambda: None)
    player = audio_preview.SlicePreviewPlayer()
    player.play([0] * 100, 0, 99, 44100)  # must not raise
    assert player._sink is None
    assert player._buffer is None


def test_play_with_an_empty_slice_range_is_a_noop(qapp):
    player = audio_preview.SlicePreviewPlayer()
    player.play([], 0, -1, 44100)  # must not raise
    assert player._sink is None


def test_stop_without_playing_is_a_noop_and_emits_nothing(qapp):
    player = audio_preview.SlicePreviewPlayer()
    received = []
    player.finished.connect(lambda: received.append(True))
    player.stop()  # must not raise
    assert received == []


# --- SlicePreviewPlayer: real playback (skipped without an audio device) ---


@_requires_audio_device
def test_play_starts_a_sink_and_stop_emits_finished(qapp):
    player = audio_preview.SlicePreviewPlayer()
    finished_calls = []
    player.finished.connect(lambda: finished_calls.append(True))

    samples = [1000 if i % 2 == 0 else -1000 for i in range(4410)]
    player.play(samples, 0, len(samples) - 1, 44100)
    assert player._sink is not None

    player.stop()
    assert player._sink is None
    assert player._buffer is None
    assert finished_calls == [True]


@_requires_audio_device
def test_play_retriggers_instead_of_overlapping(qapp):
    # a second play() call while one is already going must stop the first
    # outright, not layer on top of it - see the class's own docstring
    player = audio_preview.SlicePreviewPlayer()
    samples = [0] * 4410
    player.play(samples, 0, len(samples) - 1, 44100)
    first_sink = player._sink
    player.play(samples, 0, len(samples) - 1, 44100)
    assert player._sink is not None
    assert player._sink is not first_sink
