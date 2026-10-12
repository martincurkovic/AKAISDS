"""The files behind "Edit in External App": one exported WAV per sample, watched until the editor saves over it.

Shared by the Program Editor (S1000, S2000/S3000) and the Yamaha editor. A sample is exported to
`dropped_files.session_dir()/external_edit/<n>/<sample name>.wav` (its own directory, so the editor's atomic save - write a temp
file, rename over - is seen too), opened in the editor chosen in Settings, and `changed` fires (debounced) when the file's
size/mtime differ from the export. The window decides what "Send Edited Audio" does with the file; the tracker only knows whether
it was saved. `is_ready(key)` re-checks the file itself, so a missed watcher event never hides a saved edit.
"""

import os
import shutil
import tempfile

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from core import app_config, debug_log, dropped_files
from core import external_edit as ee

#: wait for an editor's burst of writes (temp file, rename, metadata) to settle before telling the window
DEBOUNCE_MS = 600


def _log(message):
    debug_log.get_logger().info(f"ExternalEdit: {message}")


class ExternalEditTracker(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending = {}  # key -> {"path": str, "dir": str, "fingerprint": tuple|None}
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_fs_event)
        self._watcher.directoryChanged.connect(self._on_fs_event)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._settled)

    # -- the exported file -----------------------------------------------------------------------------------

    def open(self, key, display_name, write_wav):
        """Export the audio for `key` and open it in the external editor. `write_wav(path)` writes the file. Returns None on
        success, else a short reason (the window shows it)."""
        self.discard(key)
        try:
            base = dropped_files.session_dir() / "external_edit"
            base.mkdir(parents=True, exist_ok=True)
            directory = tempfile.mkdtemp(prefix="e", dir=base)
            path = os.path.join(directory, ee.safe_filename(display_name) + ".wav")
            write_wav(path)
        except Exception as e:  # nothing has been opened yet
            _log(f"couldn't write {display_name!r}: {e!r}")
            return f"Couldn't write the file: {e}"
        entry = {"path": path, "dir": directory, "fingerprint": ee.fingerprint(path)}
        self._pending[key] = entry
        self._watch(entry)
        editor = app_config.get_saved_external_editor()
        problem = ee.launch(path, editor or None)
        if problem:
            _log(f"couldn't open {path!r} in {editor or 'the system default'!r}: {problem}")
            self.discard(key)
            return f"Couldn't open the editor: {problem}"
        _log(f"opened {display_name!r} ({path}) in {editor or 'the system default'}")
        return None

    def path(self, key):
        entry = self._pending.get(key)
        return entry["path"] if entry else None

    def has(self, key):
        return key in self._pending

    def is_ready(self, key):
        """True when the exported file was saved over since it was exported (or last marked sent)."""
        entry = self._pending.get(key)
        if entry is None:
            return False
        now = ee.fingerprint(entry["path"])
        return now is not None and now != entry["fingerprint"]

    def mark_sent(self, key):
        """The saved version was used: `is_ready` is false until the editor saves again. The file stays for further edits."""
        entry = self._pending.get(key)
        if entry is not None:
            entry["fingerprint"] = ee.fingerprint(entry["path"])
            self.changed.emit()

    def discard(self, key):
        entry = self._pending.pop(key, None)
        if entry is None:
            return
        self._unwatch(entry)
        shutil.rmtree(entry["dir"], ignore_errors=True)
        self.changed.emit()

    def close(self):
        for key in list(self._pending):
            self.discard(key)

    # -- watching --------------------------------------------------------------------------------------------

    def _watch(self, entry):
        # a rename-over save drops the file watch (the old inode is gone), so the file is re-added on every event
        self._watcher.addPath(entry["dir"])
        if os.path.exists(entry["path"]):
            self._watcher.addPath(entry["path"])

    def _unwatch(self, entry):
        watched = self._watcher.files() + self._watcher.directories()
        paths = [p for p in (entry["path"], entry["dir"]) if p in watched]
        if paths:
            self._watcher.removePaths(paths)

    def _on_fs_event(self, _path):
        self._debounce.start()

    def _settled(self):
        for entry in self._pending.values():
            self._watch(entry)
        self.changed.emit()
