"""Sends the Yamaha editor's widget edits to the sampler - shared by the Programs and Samples tabs.

Between a widget and `YamahaSession.write_parameter` (controller/yamaha_session.py, which owns the safety:
backup first, the object proven selected, a read-back) this adds what a UI needs:

 - THROTTLING. A knob drag emits dozens of values a second; edits wait `THROTTLE_MS` and only the latest value of
   each row goes out (the session also merges a write still queued for the same row).
 - the ONE-TIME EXPERIMENTAL WARNING (config flag `yamaha_write_warning_acknowledged`) before the first edit ever;
   declining refuses the edit so the caller can put the widget back.
 - a debounced RE-READ of an object once its writes have settled (`schedule_reread`): the unit has side effects an
   edit's own read-back can't show (mirrored bytes, derived EQ coefficients, the "edited" flag), and the cached copy
   the windows keep must not drift from what the unit holds. It waits while a mouse button is down so a widget the
   user is dragging is never pulled out from under them.
 - status-bar messages and `YamahaEditor: ...` log lines for every write and its result.
"""

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from core import app_config, debug_log

_WARNING_TITLE = "Editing a Yamaha sampler is experimental"
_WARNING_TEXT = (
    "Edits are written straight into the sampler's memory as you make them. Nothing is saved to its disk, so a "
    "power cycle (or reloading) undoes them - but this is new and has only been tried on one A4000.\n\n"
    "Before the first change to each program or sample, AKAISDS saves that object as a .syx file in "
    "~/.akaisds/a4000_backups, and it checks every change by reading it back.\n\n"
    "A sample is shared by every program that uses it: changing its sound changes it in all of them.\n\n"
    "Continue?"
)


def _log(message):
    debug_log.get_logger().info(f"YamahaEditor: {message}")


class WriteCoordinator(QObject):
    #: a short message for the host window's status bar
    message = Signal(str)

    THROTTLE_MS = 150
    REREAD_MS = 900

    def __init__(self, session, parent_widget):
        super().__init__(parent_widget)
        self._session = session
        self._parent_widget = parent_widget
        self._pending = {}  # (scope, key, object name, slot) -> (row, value, object name, slot, on_done)
        self._in_flight = 0
        self._rereads = {}  # (fmt, object name) -> callback(BulkDump | None)
        self._closed = False
        self._throttle = QTimer(self)
        self._throttle.setSingleShot(True)
        self._throttle.timeout.connect(self.flush)
        self._reread = QTimer(self)
        self._reread.setSingleShot(True)
        self._reread.timeout.connect(self._do_rereads)

    # -- state -------------------------------------------------------------------------------------------

    @property
    def busy(self):
        """True while any edit is waiting or on the wire (a window must not close under it)."""
        return bool(self._pending or self._in_flight or self._session.writes_pending)

    # -- edits -------------------------------------------------------------------------------------------

    def edit(self, row, value, object_name, slot, on_done):
        """Queue a change of `row` on the object `object_name` (a program's "001" or a sample's name).
        `on_done(WriteResult)` is called once it is written, refused or failed. Returns False - and does nothing -
        if the user declined the one-time warning (the caller should restore its widget)."""
        if self._closed:
            return False
        if not self._confirmed():
            return False
        self._pending[(row.scope, row.key, object_name, slot)] = (row, int(value), object_name, slot, on_done)
        if not self._throttle.isActive():
            self._throttle.start(self.THROTTLE_MS)
        return True

    def flush(self):
        """Send everything waiting now."""
        self._throttle.stop()
        pending, self._pending = self._pending, {}
        for row, value, name, slot, on_done in pending.values():
            self._in_flight += 1
            _log(f"write {row.scope}.{row.key} = {value} on {name!r}" + (f" slot {slot}" if slot is not None else ""))
            self._session.write_parameter(
                row, value, name, lambda result, d=on_done: self._finished(result, d), slot=slot
            )

    def _finished(self, result, on_done):
        self._in_flight = max(self._in_flight - 1, 0)
        _log(
            f"write result {result.key}: ok={result.ok} wrote={result.requested} before={result.previous} "
            f"after={result.readback} - {result.message}"
        )
        self.message.emit(result.message)
        try:
            on_done(result)
        except Exception:
            debug_log.get_logger().error("YamahaEditor: a write callback raised", exc_info=True)

    def _confirmed(self):
        if app_config.get_yamaha_write_warning_acknowledged():
            return True
        answer = QMessageBox.question(
            self._parent_widget,
            _WARNING_TITLE,
            _WARNING_TEXT,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            _log("the experimental-write warning was declined")
            return False
        app_config.save_yamaha_write_warning_acknowledged(True)
        return True

    # -- re-reading an object after its writes ---------------------------------------------------------------

    def schedule_reread(self, fmt, object_name, callback):
        """Once writes have settled, re-read the object (`fmt` "PG"/"SP") and call `callback(BulkDump | None)`.
        Scheduling again for the same object replaces the callback and restarts the wait."""
        if self._closed:
            return
        self._rereads[(fmt, object_name)] = callback
        self._reread.start(self.REREAD_MS)

    def _do_rereads(self):
        if self._closed or not self._rereads:
            return
        if self._pending or self._in_flight or self._session.writes_pending or QApplication.mouseButtons():
            self._reread.start(self.REREAD_MS)  # still editing: try again once it is quiet
            return
        rereads, self._rereads = self._rereads, {}
        for (fmt, name), callback in rereads.items():
            self._session.request_bulk(fmt, name, callback)

    def close(self):
        """The window is closing: send nothing more (pending edits are sent first by the caller via `flush`)."""
        self._closed = True
        self._throttle.stop()
        self._reread.stop()
        self._pending.clear()
        self._rereads.clear()
