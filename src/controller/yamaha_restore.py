"""Restoring a Yamaha object from a backup `.syx` - the sequencing half (core/yamaha_restore.py decides what to write, and why a
restore is a series of guarded parameter writes and not a bulk load; read that docstring first).

`RestoreJob(session, backup_dump)`:
  `prepare(cb)`  reads the object as it is now and plans the restore -> cb(RestorePlan | None, message)
  `run(cb, on_progress=None)`  saves a SNAPSHOT of the current state first (so a restore can itself be undone - and a restore to an
                 older backup never loses the edits made since), writes every differing value through
                 `YamahaSession.write_parameter` (so each one keeps its guards: object proven selected, read-back), then re-reads the
                 object and writes whatever is still different (the wave/loop addresses are coupled - writing one moves another - so up
                 to `MAX_PASSES` passes) -> cb(RestoreResult)
  `cancel()`     stop after the write in flight; the result says how far it got (the object is then part-restored, the snapshot is saved).
"""

import dataclasses

from core import debug_log
from core import yamaha_restore as yr


@dataclasses.dataclass
class RestoreResult:
    ok: bool  # every value the editor can write now matches the backup
    written: int = 0  # parameter writes that were accepted
    passes: int = 0
    remaining: list = dataclasses.field(default_factory=list)  # labels of values still different
    residual_offsets: list = dataclasses.field(default_factory=list)  # bytes still different (what no row covers) - see yr.residual_offsets
    snapshot_path: object = None  # the state that was replaced
    message: str = ""
    notes: list = dataclasses.field(default_factory=list)


class RestoreJob:
    MAX_PASSES = 3

    def __init__(self, session, backup):
        self._session = session
        self.backup = backup
        self.plan = None
        self.current = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    # -- planning -----------------------------------------------------------------------------------------------

    def prepare(self, callback):
        def got(dump):
            if dump is None:
                callback(None, f"Couldn't read {self._what()} from the sampler")
                return
            self.current = dump
            try:
                self.plan = yr.plan_restore(self.backup, dump)
            except yr.RestoreError as e:
                callback(None, str(e))
                return
            callback(self.plan, "")

        self._session.request_bulk(self.backup.fmt, self.backup.name, got)

    def _what(self):
        return f"{ {'PG': 'program', 'SP': 'sample'}.get(self.backup.fmt, self.backup.fmt) } {self.backup.name!r}"

    # -- running ------------------------------------------------------------------------------------------------

    def run(self, callback, on_progress=None):
        if self.plan is None or self.current is None:
            raise RuntimeError("prepare() first")
        state = {"written": 0, "passes": 0, "total": len(self.plan.items), "done": 0, "snapshot": None}
        notes = list(self.plan.notes)

        def finish(ok, message, remaining=(), final=None):
            offsets = yr.residual_offsets(self.backup, final) if final is not None else []
            callback(
                RestoreResult(ok, state["written"], state["passes"], list(remaining), offsets, state["snapshot"], message, notes)
            )

        if self.plan.identical:
            # nothing differs: still report any bytes no row covers, from the dump we already hold
            finish(True, f"{self._what().capitalize()} already matches the backup", final=self.current)
            return
        try:
            state["snapshot"] = self._session.save_backup(self.current)
        except Exception as e:
            debug_log.get_logger().error("YamahaRestore: couldn't save the snapshot", exc_info=True)
            finish(False, f"Nothing was changed: couldn't save a snapshot of the current {self._what()} first ({e})")
            return

        def write_items(items, index):
            if self._cancelled:
                finish(False, f"Cancelled after {state['written']} write(s) - {self._what()} is only partly restored",
                       [i.label for i in items[index:]])
                return
            if index >= len(items):
                verify()
                return
            item = items[index]
            if on_progress is not None:
                on_progress(state["done"], state["total"], item.label)
            state["done"] += 1
            self._session.write_parameter(
                item.row, item.value, self.backup.name,
                lambda result, items=items, index=index: after_write(result, items, index), slot=item.slot,
            )

        def after_write(result, items, index):
            if result.ok:
                state["written"] += 1
            elif not result.edit_sent or (result.previous is not None and result.readback == result.previous):
                # nothing reached the unit (no answer, a refused row), or the unit took the message and changed NOTHING (what Bulk
                # Protect does): stop rather than carry on blind. A read-back that is merely a DIFFERENT value than we wrote is not
                # this - that is the coupled wave/loop addresses, and the next pass settles it.
                finish(False, f"Stopped: {result.message}", [i.label for i in items[index:]])
                return
            write_items(items, index + 1)  # a read-back that differs is retried in the next pass (coupled addresses)

        def verify():
            state["passes"] += 1
            self._session.request_bulk(self.backup.fmt, self.backup.name, lambda dump: checked(dump))

        def checked(dump):
            if dump is None:
                finish(False, f"Couldn't read {self._what()} back to check it", [])
                return
            try:
                plan = yr.plan_restore(self.backup, dump)
            except yr.RestoreError as e:
                finish(False, str(e), [], dump)
                return
            if plan.identical:
                finish(True, f"Restored {self._what()} from the backup ({state['written']} value(s) written)", final=dump)
            elif state["passes"] >= self.MAX_PASSES:
                finish(False, f"{len(plan.items)} value(s) of {self._what()} still differ after {state['passes']} passes",
                       [i.label for i in plan.items], dump)
            else:
                state["total"] += len(plan.items)
                write_items(plan.items, 0)

        write_items(self.plan.items, 0)
