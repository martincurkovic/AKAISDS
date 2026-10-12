# tests for core/diagnostics.py and ui/diagnostics_ui.py - the bug-report plumbing
import faulthandler
import logging
import os
import sys
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from core import debug_log, diagnostics
from ui import diagnostics_ui


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(f"{record.levelname} {record.getMessage()}")


@pytest.fixture
def captured():
    handler = _Capture()
    logger = debug_log.get_logger()
    logger.addHandler(handler)
    yield handler.lines
    logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


def test_crash_handlers_log_unhandled_exceptions(monkeypatch, tmp_path, captured):
    monkeypatch.setattr(diagnostics, "CRASH_PATH", tmp_path / "crash.log")
    monkeypatch.setattr(diagnostics, "_crash_file", None)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    monkeypatch.setattr(faulthandler, "enable", lambda *a, **k: None)
    monkeypatch.setattr("PySide6.QtCore.qInstallMessageHandler", lambda *_a: None)
    monkeypatch.setattr(sys, "__excepthook__", lambda *_a: None)  # no stderr noise

    diagnostics.install_crash_handlers()
    try:
        raise ValueError("boom")
    except ValueError:
        sys.excepthook(*sys.exc_info())

    assert any(l.startswith("CRITICAL Unhandled exception") for l in captured)


def test_previous_native_crash_is_reported_at_startup(monkeypatch, tmp_path, captured):
    crash = tmp_path / "crash.log"
    crash.write_text("Fatal Python error: Segmentation fault\n")
    monkeypatch.setattr(diagnostics, "CRASH_PATH", crash)
    monkeypatch.setattr(diagnostics, "_crash_file", None)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    monkeypatch.setattr(faulthandler, "enable", lambda *a, **k: None)
    monkeypatch.setattr("PySide6.QtCore.qInstallMessageHandler", lambda *_a: None)

    diagnostics.install_crash_handlers()

    assert any("previous session crashed natively" in l for l in captured)


def test_session_config_line_names_the_sampler_type(monkeypatch, captured):
    monkeypatch.setattr(diagnostics.app_config, "get_saved_device_type", lambda: "akai_s1000")
    monkeypatch.setattr(diagnostics.app_config, "get_saved_ports", lambda: ("IN", "OUT"))
    monkeypatch.setattr(diagnostics.app_config, "get_saved_channel", lambda: 3)

    diagnostics.log_session_config("test")

    line = next(l for l in captured if "config (test)" in l)
    assert "sampler_type=akai_s1000" in line
    assert "in='IN'" in line and "channel=3" in line


def test_dialog_logging_records_text_and_the_answer(monkeypatch, captured):
    for kind in diagnostics_ui._KINDS:  # restored at teardown
        monkeypatch.setattr(QMessageBox, kind, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(diagnostics_ui, "_installed", False)

    diagnostics_ui.install_dialog_logging()
    answer = QMessageBox.question(None, "Restore?", "Write this\nback?")

    assert answer == QMessageBox.StandardButton.Ok
    assert any("dialog [question] 'Restore?': 'Write this back?'" in l for l in captured)
    assert any("dialog [question] 'Restore?' -> Ok" in l for l in captured)
