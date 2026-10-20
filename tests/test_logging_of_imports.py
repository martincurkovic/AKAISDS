# The import features leave a trail in the debug log (AGENTS.md: "Ask for this log before guessing at a hardware bug"):
# whether the VelociLoops library loaded, what was decoded, what was refused and why.

import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtWidgets import QApplication, QMessageBox

from core import debug_log, diagnostics, rx2, superflux
from ui import import_audio

RX2_STEREO = os.path.join(os.path.dirname(__file__), "fixtures", "rx2", "120Stereo.rx2")
needs_library = pytest.mark.skipif(not rx2.is_available(), reason="VelociLoops not built")


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def log_lines():
    lines = []

    class _Capture(logging.Handler):
        def emit(self, record):
            lines.append(f"{record.levelname} {record.getMessage()}")

    logger = debug_log.get_logger()
    handler = _Capture(level=logging.DEBUG)
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    yield lines
    logger.removeHandler(handler)
    logger.setLevel(previous_level)


def _wav(path, seconds):
    sf.write(str(path), np.zeros((int(seconds * 8000), 1), dtype="float32"), 8000)
    return str(path)


def test_a_missing_library_is_logged_with_where_it_looked(monkeypatch, tmp_path, log_lines):
    monkeypatch.setattr(rx2, "_library", None)
    monkeypatch.setattr(rx2, "_library_error", None)
    monkeypatch.setenv(rx2._LIBRARY_ENV, str(tmp_path / "absent.lib"))
    try:
        assert rx2.is_available() is False
    finally:
        monkeypatch.undo()
        rx2._library = None
        rx2._library_error = None
    assert any(l.startswith("WARNING rx2: VelociLoops didn't load from") and "absent.lib" in l for l in log_lines)


@needs_library
def test_reading_an_rx2_logs_what_it_found(log_lines):
    rx2.load(RX2_STEREO)
    assert any("rx2: read '120Stereo.rx2': 10 slices" in l and "2 ch" in l and "44100 Hz" in l for l in log_lines)


@needs_library
def test_a_corrupt_rx2_is_logged_as_a_warning(log_lines):
    with pytest.raises(rx2.Rx2Error):
        rx2.load(os.path.join(os.path.dirname(RX2_STEREO), "ErrorCorrupt.rx2"))
    assert any(l.startswith("WARNING rx2: couldn't open") and "ErrorCorrupt.rx2" in l for l in log_lines)


@needs_library
def test_superflux_logs_its_sensitivity_and_timing(log_lines):
    samples, _markers = rx2.load(RX2_STEREO).mono_buffer()
    superflux.detect(superflux.to_float_buffer(samples), 44100, 40)
    assert any("superflux:" in l and "sensitivity 40" in l and "cut points in" in l and " ms" in l for l in log_lines)


def test_an_import_that_is_too_long_is_logged_as_refused(qapp, tmp_path, monkeypatch, log_lines):
    monkeypatch.setattr(import_audio, "REFUSE_SECONDS", 1)
    monkeypatch.setattr(import_audio, "WARN_SECONDS", 0.5)
    import_audio.load_path(None, _wav(tmp_path / "huge.wav", 2), lambda m: None)
    assert any(l.startswith("WARNING ImportAudio: refused 'huge.wav'") for l in log_lines)


def test_the_length_question_and_the_answer_are_logged(qapp, tmp_path, monkeypatch, log_lines):
    monkeypatch.setattr(import_audio, "WARN_SECONDS", 1)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    assert import_audio.load_path(None, _wav(tmp_path / "long.wav", 2), lambda m: None) is None
    assert any("'long.wav' is 2 s" in l and "No" in l for l in log_lines)


def test_a_decoded_import_is_logged(qapp, tmp_path, log_lines):
    import_audio.load_path(None, _wav(tmp_path / "short.wav", 0.5), lambda m: None)
    assert any("ImportAudio: 'short.wav': 1 ch, 8000 Hz, 4000 frames decoded in" in l for l in log_lines)


def test_the_startup_config_line_says_whether_the_rx2_library_loaded(log_lines):
    diagnostics.log_session_config("test")
    line = next(l for l in log_lines if "config (test)" in l)
    assert "slice_detection=" in line and ("rx2_library=ok" in line or "rx2_library=MISSING" in line)
