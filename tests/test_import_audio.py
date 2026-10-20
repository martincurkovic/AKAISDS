# ui/import_audio.py: File > Import Audio (shared by the Akai and Yamaha editors) - decoding, the .rx2 branch and the length guard.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from core import rx2
from ui import import_audio

RX2_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "rx2")


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _write(path, seconds=0.5, channels=1, rate=44100, **kwargs):
    t = np.arange(int(seconds * rate)) / rate
    tone = (0.4 * np.sin(2 * np.pi * 440 * t)).astype("float32")
    sf.write(str(path), np.stack([tone] * channels, axis=1), rate, **kwargs)
    return str(path)


def _load(path, reports):
    return import_audio.load_path(None, path, reports.append)


def test_a_mono_wav_opens_with_no_markers(qapp, tmp_path):
    reports = []
    imported = _load(_write(tmp_path / "my_tone.wav"), reports)
    assert imported.name == "MY-TONE" and imported.rate == 44100
    assert len(imported.channels) == 1 and len(imported.channels[0]) == 22050
    assert imported.markers == [] and not imported.is_loop and not imported.stereo
    assert reports == []


def test_a_stereo_mp3_keeps_both_channels(qapp, tmp_path):
    imported = _load(_write(tmp_path / "wide.mp3", channels=2, format="MP3", subtype="MPEG_LAYER_III"), [])
    assert imported.stereo and len(imported.channels[0]) == len(imported.channels[1])


@pytest.mark.skipif(not rx2.is_available(), reason="VelociLoops not built")
def test_an_rx2_opens_with_its_cut_points_marked(qapp):
    imported = _load(os.path.join(RX2_FIXTURES, "120Stereo.rx2"), [])
    assert imported.is_loop and imported.stereo and imported.name == "120STEREO"
    assert len(imported.markers) == 9 and len(imported.channels[0]) == 2 * 44100


def test_a_file_that_cannot_be_read_says_so_and_returns_nothing(qapp, tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio")
    reports = []
    assert _load(str(bad), reports) is None
    assert reports and "bad.mp3" in reports[0]


def test_more_than_two_channels_is_refused_with_a_message(qapp, tmp_path):
    reports = []
    assert _load(_write(tmp_path / "surround.wav", channels=3), reports) is None
    assert reports and "channels" in reports[0]


def test_the_warning_starts_at_one_minute_and_refusal_at_fifteen():
    assert import_audio.WARN_SECONDS == 60
    assert import_audio.REFUSE_SECONDS == 15 * 60


def test_a_long_file_asks_first_and_declining_opens_nothing(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(import_audio, "WARN_SECONDS", 1)
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No
    )
    reports = []
    assert _load(_write(tmp_path / "long.wav", seconds=2), reports) is None
    assert len(asked) == 1 and "0:02" in asked[0] and "memory" in asked[0]


def test_a_long_file_opens_if_the_user_says_yes(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(import_audio, "WARN_SECONDS", 1)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    imported = _load(_write(tmp_path / "long.wav", seconds=2), [])
    assert imported is not None and len(imported.channels[0]) == 88200


def test_a_short_file_is_not_asked_about(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("asked about a short file"))
    assert _load(_write(tmp_path / "short.wav", seconds=0.5), []) is not None


def test_a_file_over_the_limit_is_refused_without_decoding_it(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(import_audio, "REFUSE_SECONDS", 1)
    monkeypatch.setattr(import_audio, "WARN_SECONDS", 0.5)
    monkeypatch.setattr(
        import_audio.sds_encoder, "read_wav_channels", lambda p: pytest.fail("decoded a file that is too long")
    )
    reports = []
    assert _load(_write(tmp_path / "huge.wav", seconds=2), reports) is None
    assert reports and "Trim it first" in reports[0]


def test_the_file_dialog_offers_audio_and_rx2():
    for extension in (".wav", ".mp3", ".ogg", ".flac", ".rx2"):
        assert f"*{extension}" in import_audio.FILTER


def test_cancelling_the_dialog_opens_nothing(qapp, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    assert import_audio.pick_and_load(None, lambda m: None) is None
