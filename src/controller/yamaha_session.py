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

WAVES (`request_wave`): a wave object's audio as the unit's native "WD" bulk dump (core/yamaha_wave.py) - several
complete messages per wave, joined by `WaveAssembler`, each reported to `on_chunk` as it lands so a window can draw the
wave progressively. A bulk dump can't be aborted: after a cancel (or a failure mid-stream) the unit keeps sending the
rest, which would swamp the next request, so the session DRAINS - it stays busy until the stream has been quiet for
`drain_idle_ms` and only then starts the next operation.

ASSIGNING (`change_link`): a sample is assigned to / removed from a program with the OBJECT LINK CHANGE message (manual 5.3.7),
guarded like a write: the program is backed up first (once per session), the message is sent (the unit never replies to it - MEASURED
2026-10-06 on throwaway objects), and the unit is then ASKED whether the link exists (object link REQUEST, 5.3.8) - that answer is
the verification. Measured behaviour the UI relies on: a new sample is appended as the NEXT slot with every per-program value at its
default (receive channel -1 = "=sample"); removing a middle one COMPACTS the slots (later ones shift down and keep their values);
linking twice, or a name the unit doesn't have, changes nothing.

WRITING (`write_parameter`): one parameter of one object per operation, and every write is guarded three ways -
 1. the FIRST write to an object in a session first dumps it and saves the dump as a `.syx` backup
    (`backup_dir`, default ~/.akaisds/a4000_backups); if that backup can't be saved NOTHING is written;
 2. an object edit applies to whichever object was selected last, so the edit is only sent after a parameter
    request has been ANSWERED with an announce naming the object we meant (a select by itself gets no reply, so
    that is the only proof it took) - that same request also reads the value we are about to replace;
 3. after the edit the parameter is requested again and must equal what was written (the unit never acknowledges
    an edit); the result carries what the unit really holds, e.g. when Bulk Protect swallowed it.
Only edit-able rows are sent: not read-only / bulk-only / `write_ignored` / A5000-only ones, and only values in the
table's range. The unit's side effects (mirrored bytes, derived EQ coefficients, the "edited" flag) are NOT modelled
here - a caller that wants the whole truth re-reads the object's bulk dump after its writes.
"""

import dataclasses
import re
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from core import app_config, debug_log
from core import yamaha_params as yp
from core import yamaha_sysex as ysx
from core import yamaha_wave

BACKUP_DIR = Path.home() / ".akaisds" / "a4000_backups"

_SCOPE_TARGET = {"program": ("PG", "program"), "easy_edit": ("PG", "program"), "sample": ("SP", "sample")}


@dataclasses.dataclass
class LinkResult:
    ok: bool  # the unit confirms the program now is (or isn't) linked to the sample, as asked
    requested: bool  # True = assign, False = remove
    program: str
    sample: str
    linked: object = None  # what the unit said afterwards (None = it never answered)
    backup_path: object = None
    message: str = ""


@dataclasses.dataclass
class WriteResult:
    ok: bool  # the unit holds exactly the value we wrote
    key: str  # the row's key
    requested: int
    previous: object = None  # the value the unit held before (None if it never answered)
    readback: object = None  # the value it holds now (None if it never answered after the edit)
    edit_sent: bool = False  # False = the unit's data was not touched at all
    backup_path: object = None  # the .syx saved (this or an earlier write) before the first write to the object
    message: str = ""


class _Op:
    def __init__(self, kind, callback, **kw):
        self.kind, self.callback, self.kw = kind, callback, kw
        # parameter-read bookkeeping
        self.index = 0
        self.results = []
        self.awaiting = False  # a request is on the wire, its reply not yet seen
        self.announced_ok = None  # did the unit announce the right object since that request?
        # write bookkeeping
        self.phase = None  # "backup" while the pre-write dump is awaited
        self.edit_sent = False
        self.backup_path = None
        self.results_value = None
        # wave bookkeeping
        self.assembler = None
        self.request_sent = False


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
        #: a wave arrives in ~4 KB messages, one every ~1.3 s at MIDI speed: how long between two before giving up
        self.wave_chunk_timeout_ms = 6000
        #: after a wave stream is abandoned, how long the wire must be quiet before the next operation may start
        self.drain_idle_ms = 2500
        #: how long after an object edit (which gets no reply) before the value is read back
        self.edit_settle_ms = 120
        #: where a pre-write backup of an object is saved (tests point this at a temp dir)
        self.backup_dir = BACKUP_DIR
        #: while a Sample Dump transfer (e.g. a waveform load) owns the wire, queued operations wait and retry
        self.busy_retry_ms = 150
        #: the unit's Device Number (SysEx is addressed to it); manual-edit-only config
        self.device = app_config.get_yamaha_device_number()

        self._queue = []
        self._op = None
        self._backups = {}  # (object type, name) -> the .syx saved before this session's first write to it
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._send_next_parameter_request)
        self._busy = QTimer(self)
        self._busy.setSingleShot(True)
        self._busy.timeout.connect(self._start_next)
        self._draining = False
        self._drain = QTimer(self)
        self._drain.setSingleShot(True)
        self._drain.timeout.connect(self._end_drain)

    # -- public API ---------------------------------------------------------------------------------

    @property
    def idle(self):
        return self._op is None and not self._queue and not self._draining

    def request_object_list(self, callback):
        """callback(list[ObjectEntry] | None)."""
        self._enqueue(_Op("bulk", lambda dump: callback(None if dump is None else ysx.parse_object_list(dump.data)), fmt="OL", name=""))

    def request_bulk(self, fmt, name, callback):
        """callback(BulkDump | None) - fmt "PG" (a program, name "001") or "SP" (a sample, by name)."""
        self._enqueue(_Op("bulk", callback, fmt=fmt, name=name))

    def request_wave(self, name, callback, on_chunk=None):
        """A wave object's audio frames (list of int16; `name` is the WAVE object's name - a sample's linked wave,
        see yamaha_params.WAVE_NAME_*_OFFSET). callback(frames | None). `on_chunk(new_frames, total_frames)` is called
        for every message that adds frames, as it arrives (total_frames is known from the first one)."""
        self._enqueue(_Op("wave", callback, name=name, on_chunk=on_chunk))

    def request_parameters(self, object_type, name, params_list, callback):
        """Select one object, then read each P1..P6 in `params_list` (one at a time).
        callback(list[ParameterMessage | None]) - one entry per request; after the first failure the rest
        are None (a unit that stopped answering won't answer the next one either)."""
        if isinstance(object_type, str):
            object_type = ysx.OBJECT_TYPES[object_type]
        self._enqueue(
            _Op("params", callback, otype=object_type, name=name, params=[tuple(p) for p in params_list])
        )

    @property
    def writes_pending(self):
        """True while a write is queued or on the wire (a window must not close/switch under it)."""
        return any(op.kind in ("write", "link") for op in ([self._op] if self._op else []) + self._queue)

    def backup_of(self, object_type, name):
        """Path of the backup saved for an object this session, or None."""
        return self._backups.get((object_type, name.rstrip()))

    def write_parameter(self, param, value, object_name, callback, slot=None):
        """Change ONE parameter of one object. `param` is a `core.yamaha_params` row, `object_name` the program's
        "001" (program and Easy Edit rows - the latter need `slot`) or the sample's name. See the module docstring
        for the guards. callback(WriteResult), always called once, never raises into the caller.

        A write still waiting in the queue for the same row is updated in place instead of queued again (dragging a
        knob only needs to send the value it ends on); both callbacks then get the result of the one write."""
        value = int(value)
        problem = self._write_problem(param, value, slot)
        if problem:
            QTimer.singleShot(0, lambda: callback(WriteResult(False, param.key, value, message=problem)))
            return
        fmt, kind = _SCOPE_TARGET[param.scope]
        otype = ysx.OBJECT_TYPES[kind]
        target = (otype, object_name.rstrip())
        p = yp.request_params(param, slot)
        for queued in self._queue:
            if queued.kind == "write" and queued.kw["target"] == target and queued.kw["p"] == p and queued.kw["row"] is param:
                queued.kw["value"] = value
                first = queued.callback
                queued.callback = lambda r, first=first: (first(r), callback(r))
                return
        self._enqueue(
            _Op("write", callback, otype=otype, name=object_name, fmt=fmt, target=target, row=param, slot=slot,
                value=value, p=p, params=[p, p])  # params: [the probe read, the read-back]
        )

    def change_link(self, program_name, sample_name, linked, callback, sample_type="sample"):
        """Assign (`linked` True) or remove (False) a sample of a program. callback(LinkResult), always called once. See the module
        docstring for the guards and the measured behaviour (the sample is appended as the next slot; removing compacts the rest)."""
        self._enqueue(
            _Op("link", callback, name=program_name, sample=sample_name, linked=bool(linked), stype=sample_type,
                otype=ysx.OBJECT_TYPES["program"], fmt="PG", target=(ysx.OBJECT_TYPES["program"], program_name.rstrip()))
        )

    @staticmethod
    def _write_problem(param, value, slot):
        if param.read_only or param.bulk_only or param.kind != "int":
            return f"{param.name} can't be written"
        if param.write_ignored:
            return f"{param.name} is not changed by the sampler when written"
        if param.a5000_only:
            return f"{param.name} exists only on the A5000"
        if param.scope == "easy_edit" and (slot is None or slot < 0):
            return "an Easy Edit value needs the assigned sample's slot"
        if not yp.in_range(param, value):
            return f"{value} is outside {param.name}'s range ({param.lo} to {param.hi})"
        return None

    def cancel(self):
        """Drop everything queued and abandon the operation in flight (each callback gets None)."""
        ops = ([self._op] if self._op else []) + self._queue
        in_flight = self._op
        self._queue = []
        self._op = None
        self._timeout.stop()
        self._settle.stop()
        self._busy.stop()
        if in_flight is not None:
            self._maybe_drain(in_flight, only_if_streaming=False)
        for op in ops:
            self._finish(op, failed=True, quiet=True)

    # -- the queue ------------------------------------------------------------------------------------

    def _enqueue(self, op):
        self._queue.append(op)
        if self._op is None:
            self._start_next()

    def _maybe_drain(self, op, *, only_if_streaming):
        """A wave request that didn't finish may still have the unit streaming at us: keep the wire reserved. After a
        user cancel the unit may be about to start (`request_sent`); after a failure only a stream that had started
        (a first message arrived) is worth waiting for - a unit that never answered won't suddenly send."""
        started = op.assembler is not None and op.assembler.started
        if op.kind == "wave" and op.request_sent and (started or not only_if_streaming) and not (op.assembler and op.assembler.done):
            self._draining = True
            self._drain.start(self.drain_idle_ms)
            # measured: nothing sent mid-dump (identity request, SDS CANCEL) makes the unit stop - it sends the whole wave
            self._c.status_changed.emit("Waiting for the sampler to finish sending the wave (it can't be stopped)...")

    def _end_drain(self):
        self._draining = False
        self._start_next()

    def _start_next(self):
        if self._op is not None or not self._queue or self._draining:
            return
        if self._c.is_sds_transfer_busy():
            # a Sample Dump transfer is mid-flight on the same wire: Yamaha requests slipped in between its packets
            # can be dropped (and a stray reply would confuse the transfer) - wait for it
            if not self._busy.isActive():
                self._busy.start(self.busy_retry_ms)
            return
        op = self._queue.pop(0)
        self._op = op
        try:
            if op.kind == "bulk":
                self._send(ysx.build_dump_request(self.device, op.kw["fmt"], op.kw["name"]))
                self._timeout.start(self.bulk_timeout_ms)
            elif op.kind == "wave":
                op.assembler = yamaha_wave.WaveAssembler(op.kw["name"])
                self._send(ysx.build_dump_request(self.device, "WD", op.kw["name"]))
                op.request_sent = True
                self._timeout.start(self.bulk_timeout_ms)
            elif op.kind in ("write", "link") and op.kw["target"] not in self._backups:
                op.phase = "backup"  # the first write to this object: save it as it is now
                self._send(ysx.build_dump_request(self.device, op.kw["fmt"], op.kw["name"]))
                self._timeout.start(self.bulk_timeout_ms)
            elif op.kind == "link":
                self._send_link(op)  # the program is already backed up this session
            else:
                self._begin_select(op)
        except Exception as e:  # a closed port etc: fail now rather than wait for a timeout
            debug_log.get_logger().error(f"YamahaSession: send failed: {e}", exc_info=True)
            self._complete(failed=True, message=f"Couldn't send to the sampler: {e}")

    def _send(self, message):
        self._c.midi_manager.send_sysex(message)

    def _begin_select(self, op):
        op.phase = None
        self._send(ysx.build_object_select(self.device, op.kw["name"], op.kw["otype"]))
        self._timeout.start(self.reply_timeout_ms + self.select_settle_ms)
        self._settle.start(self.select_settle_ms)

    def _send_next_parameter_request(self):
        op = self._op
        if op is not None and op.kind == "link" and op.phase == "link_settle":
            self._send_link_request(op)
            return
        if op is None or op.kind not in ("params", "write"):
            return
        if op.index >= len(op.kw["params"]):
            self._complete()
            return
        if op.kind == "write" and op.index == 1 and not op.edit_sent:
            # the probe (index 0) was answered by the right object: only now is it safe to edit
            self._send_edit(op)
            return
        op.awaiting, op.announced_ok = True, None
        try:
            self._send(ysx.build_parameter_request(self.device, op.kw["params"][op.index]))
        except Exception as e:
            debug_log.get_logger().error(f"YamahaSession: send failed: {e}", exc_info=True)
            self._complete(failed=True, message=f"Couldn't send to the sampler: {e}")
            return
        self._timeout.start(self.reply_timeout_ms)

    def _send_edit(self, op):
        row, value = op.kw["row"], op.kw["value"]
        raw = ysx.encode_value(value, row.bulk_size if row.bits else row.size, signed=row.signed)
        try:
            self._send(ysx.build_object_edit(self.device, op.kw["p"], raw))
        except Exception as e:
            debug_log.get_logger().error(f"YamahaSession: send failed: {e}", exc_info=True)
            self._complete(failed=True, message=f"Couldn't send to the sampler: {e}")
            return
        op.edit_sent = True
        op.awaiting = False
        # no reply to an edit: give it a moment, then the read-back request (same slot as every other step)
        self._timeout.start(self.reply_timeout_ms + self.edit_settle_ms)
        self._settle.start(self.edit_settle_ms)

    def _send_link(self, op):
        self._send(ysx.build_object_link_change(
            self.device, op.kw["name"], "program", op.kw["sample"], op.kw["stype"], op.kw["linked"]))
        op.phase = "link_settle"  # no reply to a change: give it a moment, then ask
        self._timeout.start(self.reply_timeout_ms + self.edit_settle_ms)
        self._settle.start(self.edit_settle_ms)

    def _send_link_request(self, op):
        try:
            self._send(ysx.build_object_link_request(self.device, op.kw["name"], "program", op.kw["sample"], op.kw["stype"]))
        except Exception as e:
            debug_log.get_logger().error(f"YamahaSession: send failed: {e}", exc_info=True)
            self._complete(failed=True, message=f"Couldn't send to the sampler: {e}")
            return
        op.phase = "link_wait"
        self._timeout.start(self.reply_timeout_ms)

    def _backup_received(self, op, dump):
        try:
            path = self._save_backup(dump)
        except Exception as e:
            debug_log.get_logger().error(f"YamahaSession: backup failed: {e}", exc_info=True)
            self._complete(
                failed=True,
                message=f"Not written: couldn't save a backup of {op.kw['name'].rstrip()!r} first ({e})",
            )
            return
        self._backups[op.kw["target"]] = path
        debug_log.get_logger().info(f"YamahaSession: backed up {op.kw['name'].rstrip()!r} to {path}")
        try:
            if op.kind == "link":
                self._send_link(op)
            else:
                self._begin_select(op)
        except Exception as e:
            debug_log.get_logger().error(f"YamahaSession: send failed: {e}", exc_info=True)
            self._complete(failed=True, message=f"Couldn't send to the sampler: {e}")

    def _save_backup(self, dump):
        """Write `dump` as a .syx and prove the file decodes back to the same data - or raise."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", dump.name).strip("_") or "object"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self.backup_dir / f"{dump.fmt}-{safe}-{stamp}.syx"
        n = 1
        while path.exists():
            n += 1
            path = self.backup_dir / f"{dump.fmt}-{safe}-{stamp}-{n}.syx"
        blob = b"\xf0" + ysx.build_bulk_dump(dump.device, dump.fmt, dump.name_raw, dump.data, header=dump.header) + b"\xf7"
        path.write_bytes(blob)
        if ysx.parse_bulk_dump(ysx.split_messages(path.read_bytes())[0]).data != dump.data:
            raise OSError("the saved file doesn't match the dump")
        return path

    def save_backup(self, dump):
        """Save `dump` (a BulkDump of an object as it is NOW) as a backup `.syx`, verified by reading it back, and return its path
        - raises if it can't be saved. Also counts as THE backup of that object for this session if it has none yet, so the first
        write to it doesn't dump it all over again (a Restore uses this to snapshot the state it is about to replace)."""
        path = self._save_backup(dump)
        kind = {"PG": "program", "SP": "sample"}.get(dump.fmt)
        if kind is not None:
            self._backups.setdefault((ysx.OBJECT_TYPES[kind], dump.name.rstrip()), path)
        return path

    def _link_result(self, op, failed):
        program, sample, wanted = op.kw["name"].rstrip(), op.kw["sample"], op.kw["linked"]
        actual = op.results_value if not failed else None
        backup = self._backups.get(op.kw["target"])
        if actual is not None and actual == wanted:
            ok = True
            message = f"Assigned {sample!r} to program {program}" if wanted else f"Removed {sample!r} from program {program}"
        elif actual is None:
            ok = False
            message = f"The sampler didn't confirm the change to program {program}" + (
                " (its backup could not be made)" if op.phase == "backup" and backup is None else ""
            )
        else:
            ok = False
            message = (
                f"The sampler didn't assign {sample!r} to program {program} (it ignores a link it can't make)" if wanted
                else f"The sampler still has {sample!r} assigned to program {program}"
            )
        return LinkResult(ok, wanted, program, sample, actual, backup, message)

    def _write_result(self, op, failed):
        row, value = op.kw["row"], op.kw["value"]
        backup = self._backups.get(op.kw["target"])
        previous = yp.decode_reply(row, op.results[0].data) if len(op.results) > 0 else None
        readback = yp.decode_reply(row, op.results[1].data) if len(op.results) > 1 else None
        what = f"{row.name} of {op.kw['name'].rstrip()!r}"
        if readback == value:
            ok, message = True, f"Wrote {what} = {value}"
        elif not op.edit_sent:
            ok, message = False, f"Nothing was written to {op.kw['name'].rstrip()!r}" + (
                "" if backup or op.phase != "backup" else " (its backup could not be made)"
            )
        elif readback is None:
            ok, message = False, f"Sent {what} = {value}, but the sampler didn't confirm it"
        else:
            ok, message = False, (
                f"The sampler holds {readback} for {what}, not {value} - is Bulk Protect on?"
            )
        return WriteResult(ok, row.key, value, previous, readback, op.edit_sent, backup, message)

    def _complete(self, *, failed=False, message=None):
        op, self._op = self._op, None
        self._timeout.stop()
        self._settle.stop()
        if op is not None and failed:
            self._maybe_drain(op, only_if_streaming=True)
        if message:
            self._c.status_changed.emit(message)
        if op is not None:
            self._finish(op, failed=failed)
        self._start_next()

    def _finish(self, op, *, failed, quiet=False):
        try:
            if op.kind in ("bulk", "wave"):
                op.callback(None if failed else op.results_value)
            elif op.kind == "write":
                op.callback(self._write_result(op, failed))
            elif op.kind == "link":
                op.callback(self._link_result(op, failed))
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
        if op.kind == "link":
            what = f"object link of {op.kw['sample']!r} / {op.kw['name']!r}"
        elif op.kind == "wave":
            what = f"WD dump of {op.kw['name']!r}" + (
                f" after {op.assembler.blocks} message(s)" if op.assembler and op.assembler.blocks else ""
            )
        elif op.kind == "bulk" or op.phase == "backup":
            what = f"{op.kw['fmt']} dump of {op.kw['name']!r}"
        else:
            what = f"parameter {op.kw['params'][min(op.index, len(op.kw['params']) - 1)]} of {op.kw['name']!r}"
        debug_log.get_logger().warning(f"YamahaSession: no reply to {what}")
        self._complete(
            failed=True,
            message=(
                "No reply from the Yamaha sampler - check its Device Number "
                f"(set to {self.device} here), that Bulk Protect is off, and the MIDI cables"
            ),
        )

    def _handle_wave_message(self, op, data, kind):
        if kind != "bulk_dump":
            return
        try:
            dump = ysx.parse_bulk_dump(data)
        except ysx.YamahaSysexError as e:
            debug_log.get_logger().error(f"YamahaSession: bad wave message: {e}")
            self._complete(failed=True, message=f"The sampler's wave dump was corrupt ({e})")
            return
        if dump.fmt != "WD" or dump.name != op.kw["name"].rstrip():
            return  # someone else's dump
        try:
            new = op.assembler.feed(dump)
        except ysx.YamahaSysexError as e:
            debug_log.get_logger().warning(f"YamahaSession: wave message ignored: {e}")
            return
        callback = op.kw.get("on_chunk")
        if callback is not None and new:
            try:
                callback(new, op.assembler.total_frames)
            except Exception:
                debug_log.get_logger().error("YamahaSession: a wave chunk callback raised", exc_info=True)
        if op.assembler.done:
            op.results_value = list(op.assembler.frames)
            self._complete()
        else:
            self._timeout.start(self.wave_chunk_timeout_ms)

    # -- incoming ---------------------------------------------------------------------------------------

    def handle_sysex(self, data):
        """Every 0x43 message the controller receives (bytes between F0 and F7)."""
        op = self._op
        kind = ysx.classify(data)
        if op is None:
            if self._draining and kind == "bulk_dump":
                self._drain.start(self.drain_idle_ms)  # the abandoned wave is still streaming: keep waiting
            return  # a stray/late message, or our own announce arriving after a timeout
        if op.kind == "wave":
            self._handle_wave_message(op, data, kind)
            return
        if op.kind == "link" and op.phase != "backup":  # (the backup phase's bulk dump is handled below, like a write's)
            if op.phase == "link_wait" and kind == "parameter":
                try:
                    msg = ysx.parse_parameter_message(data)
                except ysx.YamahaSysexError:
                    return
                if msg.kind == "link" and (msg.object_name, msg.lower_name) == (op.kw["name"].rstrip(), op.kw["sample"].rstrip()):
                    op.results_value = msg.linked
                    self._complete()
            return
        if op.kind == "bulk" or (op.kind in ("write", "link") and op.phase == "backup"):
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
            if op.kind in ("write", "link"):
                self._backup_received(op, dump)
                return
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
