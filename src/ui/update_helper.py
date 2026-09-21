"""Shared "run an update check, then react" glue.

MainWindow, ProgramEditorWindow (manual "Check for Updates..." menu items)
and AboutDialog (its own button + progress bar) all trigger the exact same
check/throttle/skip-list policy - this exists so that's written once rather
than reimplemented three times.
"""

import time

from PySide6.QtWidgets import QMessageBox

from core import app_config, update_checker
from ui.update_dialog import UpdateAvailableDialog

try:
    from ui._version import APP_VERSION
except ImportError:
    APP_VERSION = "0.0.0-dev"


class UpdateCheckRunner:
    """Owns one in-flight UpdateCheckWorker for a single window/dialog.

    `parent` is used both as the QThread's Qt-parent and as the dialog
    parent for any QMessageBox/UpdateAvailableDialog this pops up - pass
    the window or dialog that owns this runner.
    """

    def __init__(self, parent):
        self._parent = parent
        self._worker = None
        self._dialog = None  # keeps UpdateAvailableDialog alive while shown

    def is_running(self):
        return self._worker is not None

    def wait(self):
        # blocks until the current check's background thread has actually
        # stopped - call this before the owning window/dialog can be
        # garbage collected out from under an in-flight worker (see
        # AGENTS.md's BridgeWorker section for why a QThread whose Python
        # wrapper gets GC'd mid-run is a real crash, not a theoretical one)
        if self._worker is not None:
            self._worker.wait()

    def start(self, manual, on_finished=None):
        if self._worker is not None:
            return  # a check is already in flight

        worker = update_checker.UpdateCheckWorker(self._parent)
        worker.succeeded.connect(lambda info: self._on_succeeded(info, manual))
        worker.failed.connect(lambda message: self._on_failed(message, manual))

        def _cleanup():
            self._worker = None
            if on_finished:
                on_finished()

        worker.finished.connect(_cleanup)
        self._worker = worker
        worker.start()

    def _on_succeeded(self, info, manual):
        app_config.save_last_update_check(time.time())

        if info is None:
            if manual:
                QMessageBox.information(
                    self._parent,
                    "No Updates Available",
                    f"You're up to date (version {APP_VERSION}).",
                )
            return

        if not manual and info.version == app_config.get_skipped_update_version():
            return

        self._dialog = UpdateAvailableDialog(info, self._parent)
        self._dialog.show()

    def _on_failed(self, message, manual):
        if manual:
            QMessageBox.warning(
                self._parent,
                "Update Check Failed",
                f"Could not check for updates:\n{message}",
            )
        # silent on an automatic/startup check - no need to nag about a flaky connection
