"""Yamaha A4000/A5000 conversation engine: object list, bulk dumps, parameter reads.

Owned by `SamplerController` (built lazily, and handed every incoming 0x43 SysEx while the Sampler Type
is the Yamaha), so the editor shares the Dashboard's ONE MIDI connection - no second port, no
`BridgeWorker`. Event-driven on the GUI thread the way `S950Transfers` is: Qt timers + incoming
messages, strictly one operation on the wire at a time.

Why strictly one: an object select is STATEFUL - parameter requests/edits apply to whichever object was
selected last (core/yamaha_sysex.py) - so two interleaved conversations would read each other's objects.
Operations queue first-in-first-out and each reports through a callback with the result, or `None` on a
timeout/failure (so a waiting window can never hang).

Wire facts this relies on (all measured on a real A4000, 2026-10-06 - see dev_docs/a4000-editor-roadmap.md):
 - a select gets NO reply; the unit ANNOUNCES its current object (a select-shaped message) right before
   EVERY parameter reply (even repeated requests on one object), then sends the value. A value is accepted
   only if an announce for the object we asked for arrived since the request went out - so a read can never
   silently come from the wrong object, and a stale or duplicated reply (no fresh announce) is ignored.
   (Found the hard way: a duplicated delivery once made the NEXT program's read return the previous one's value.)
 - a bulk dump is one F0..F7 holding one or more blocks.
 - a short pause between a select and the request that follows it (`select_settle_ms`) is kept for safety, but
   measured not to be needed.

Nothing here WRITES to the unit yet (no object edit / bulk load is ever built).
"""

from PySide6.QtCore import QObject, QTimer

from core import app_config, debug_log
from core import yamaha_sysex as ysx


class _Op:
    def __init__(self, kind, callback, **kw):
        self.kind, self.callback, self.kw = kind, callback, kw
        # parameter-read bookkeeping
        self.index = 0
        self.results = []
        self.awaiting = False  # a request is on the wire, its reply not yet seen
        self.announced_ok = None  # did the unit announce the right object since that request?


class YamahaSession(QObject):
    def __init__(self, controller):
        super().__init__()
        self._c = controller
        # timing in ms - attributes so tests can shrink them
        self.reply_timeout_ms = 2500
        self.bulk_timeout_ms = 8000
        # measured: the unit answers correctly even with NO pause after a select (a 0-200 ms sweep, 10 reads each,
        # all right); a small margin stays, and the announce check (see handle_sysex) is what guards correctness
        self.select_settle_ms = 30
        #: the unit's Device Number (SysEx is addressed to it); manual-edit-only config
        self.device = app_config.get_yamaha_device_number()

        self._queue = []
        self._op = None
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._send_next_parameter_request)

    # -- public API ---------------------------------------------------------------------------------

    @property
    def idle(self):
        return self._op is None and not self._queue

    def request_object_list(self, callback):
        """callback(list[ObjectEntry] | None)."""
        self._enqueue(_Op("bulk", lambda dump: callback(None if dump is None else ysx.parse_object_list(dump.data)), fmt="OL", name=""))

    def request_bulk(self, fmt, name, callback):
        """callback(BulkDump | None) - fmt "PG" (a program, name "001") or "SP" (a sample, by name)."""
        self._enqueue(_Op("bulk", callback, fmt=fmt, name=name))

    def request_parameters(self, object_type, name, params_list, callback):
        """Select one object, then read each P1..P6 in `params_list` (one at a time).
        callback(list[ParameterMessage | None]) - one entry per request; after the first failure the rest
        are None (a unit that stopped answering won't answer the next one either)."""
        if isinstance(object_type, str):
            object_type = ysx.OBJECT_TYPES[object_type]
        self._enqueue(
            _Op("params", callback, otype=object_type, name=name, params=[tuple(p) for p in params_list])
        )

    def cancel(self):
        """Drop everything queued and abandon the operation in flight (each callback gets None)."""
        ops = ([self._op] if self._op else []) + self._queue
        self._queue = []
        self._op = None
        self._timeout.stop()
        self._settle.stop()
        for op in ops:
            self._finish(op, failed=True, quiet=True)

    # -- the queue ------------------------------------------------------------------------------------

    def _enqueue(self, op):
        self._queue.append(op)
        if self._op is None:
            self._start_next()

    def _start_next(self):
        if self._op is not None or not self._queue:
            return
        op = self._queue.pop(0)
        self._op = op
        try:
            if op.kind == "bulk":
                self._send(ysx.build_dump_request(self.device, op.kw["fmt"], op.kw["name"]))
                self._timeout.start(self.bulk_timeout_ms)
            else:
                self._send(ysx.build_object_select(self.device, op.kw["name"], op.kw["otype"]))
                self._timeout.start(self.reply_timeout_ms + self.select_settle_ms)
                self._settle.start(self.select_settle_ms)
        except Exception as e:  # a closed port etc: fail now rather than wait for a timeout
            debug_log.get_logger().error(f"YamahaSession: send failed: {e}", exc_info=True)
            self._complete(failed=True, message=f"Couldn't send to the sampler: {e}")

    def _send(self, message):
        self._c.midi_manager.send_sysex(message)

    def _send_next_parameter_request(self):
        op = self._op
        if op is None or op.kind != "params":
            return
        if op.index >= len(op.kw["params"]):
            self._complete()
            return
        op.awaiting, op.announced_ok = True, None
        try:
            self._send(ysx.build_parameter_request(self.device, op.kw["params"][op.index]))
        except Exception as e:
            debug_log.get_logger().error(f"YamahaSession: send failed: {e}", exc_info=True)
            self._complete(failed=True, message=f"Couldn't send to the sampler: {e}")
            return
        self._timeout.start(self.reply_timeout_ms)

    def _complete(self, *, failed=False, message=None):
        op, self._op = self._op, None
        self._timeout.stop()
        self._settle.stop()
        if message:
            self._c.status_changed.emit(message)
        if op is not None:
            self._finish(op, failed=failed)
        self._start_next()

    def _finish(self, op, *, failed, quiet=False):
        try:
            if op.kind == "bulk":
                op.callback(None if failed else op.results_value)
            else:
                params = op.kw["params"]
                results = list(op.results) + [None] * (len(params) - len(op.results))
                op.callback(results)
        except Exception:
            debug_log.get_logger().error("YamahaSession: a callback raised", exc_info=True)

    def _on_timeout(self):
        op = self._op
        if op is None:
            return
        what = (
            f"{op.kw['fmt']} dump of {op.kw['name']!r}" if op.kind == "bulk" else f"parameter {op.kw['params'][min(op.index, len(op.kw['params']) - 1)]} of {op.kw['name']!r}"
        )
        debug_log.get_logger().warning(f"YamahaSession: no reply to {what}")
        self._complete(
            failed=True,
            message=(
                "No reply from the Yamaha sampler - check its Device Number "
                f"(set to {self.device} here), that Bulk Protect is off, and the MIDI cables"
            ),
        )

    # -- incoming ---------------------------------------------------------------------------------------

    def handle_sysex(self, data):
        """Every 0x43 message the controller receives (bytes between F0 and F7)."""
        op = self._op
        if op is None:
            return  # a stray/late message, or our own announce arriving after a timeout
        kind = ysx.classify(data)
        if op.kind == "bulk":
            if kind != "bulk_dump":
                return
            try:
                dump = ysx.parse_bulk_dump(data)
            except ysx.YamahaSysexError as e:
                debug_log.get_logger().error(f"YamahaSession: bad bulk dump: {e}")
                self._complete(failed=True, message=f"The sampler's dump was corrupt ({e})")
                return
            wanted = op.kw["fmt"]
            if dump.fmt != wanted:
                return
            name = op.kw["name"]
            if name and dump.name != name.rstrip():
                return  # someone else's dump
            op.results_value = dump
            self._complete()
            return
        if kind != "parameter" or not op.awaiting:
            return  # nothing outstanding: a late or duplicated message
        try:
            msg = ysx.parse_parameter_message(data)
        except ysx.YamahaSysexError:
            return
        if msg.kind == "select":
            expected = (op.kw["name"].rstrip(), op.kw["otype"])
            op.announced_ok = (msg.object_name, msg.object_type) == expected
            if not op.announced_ok:
                debug_log.get_logger().error(
                    f"YamahaSession: unit announced {msg.object_name!r}/{msg.object_type}, expected {expected}"
                )
            return
        if msg.kind != "object" or op.index >= len(op.kw["params"]):
            return
        if msg.params != op.kw["params"][op.index]:
            return  # a reply to something else
        if op.announced_ok is False:
            # the value came from a different object than the one we selected: refuse it
            self._complete(failed=True, message="The sampler answered for a different object than the one requested")
            return
        if op.announced_ok is None:
            return  # a value with no announce before it is not this request's reply (stale/duplicate)
        op.results.append(msg)
        op.awaiting = False
        op.index += 1
        self._timeout.stop()
        self._send_next_parameter_request()
