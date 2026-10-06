"""Restoring a Yamaha A4000/A5000 object from one of the `.syx` backups the editor saves before its first write to it.

Pure (no Qt/MIDI): decides WHAT to write. `controller/yamaha_restore.py` does the writing.

How a restore works - and why it is not a bulk load: the unit's parameter-change message is the only WRITE path proven on
real hardware (a bulk load back into the unit is unmeasured), so a restore rewrites, one parameter at a time through
`YamahaSession.write_parameter`, exactly the writable values that differ between the backup and the object as it is now.
That means a restore can put back every value the editor can change, and it CANNOT put back what the editor cannot write:

 - which samples a program has assigned (the Easy Edit slots' names/types) - only the values of slots that still hold the SAME
   sample as the backup are restored; a program whose assignments changed is restored where it can be, and says so;
 - the read-only/ignored rows (a sample's rate, wave length, ...), the A5000-only rows, and bytes no row covers (the unit's
   side effects: mirrored sample-control bytes, derived EQ coefficients, the right channel's mirror bytes, the "edited" flag).
   After a restore the remaining byte differences are REPORTED (`residual_offsets`), never silently ignored.

A sample can only be restored onto the same sample: its left (and right) wave object names must match the backup's.
"""

import dataclasses

from core import yamaha_params as yp
from core import yamaha_sysex as ysx

#: the wave/loop address rows are COUPLED on the unit (writing one moves others - measured), so they are written LAST and in this
#: order, and a second pass catches anything a write moved
GEOMETRY_ORDER = ("wave_start_address", "loop_start_address", "loop_length", "loop_end_address")


@dataclasses.dataclass
class RestoreItem:
    row: object
    slot: object  # an Easy Edit slot, else None
    value: int  # what the backup holds (to be written)
    current: int  # what the object holds now

    @property
    def label(self):
        where = f" (slot {self.slot})" if self.slot is not None else ""
        return f"{self.row.name}{where}"


@dataclasses.dataclass
class RestorePlan:
    fmt: str  # "PG" / "SP"
    name: str
    items: list  # [RestoreItem], in the order they should be written
    notes: list  # things the user should know (assignments that differ, slots skipped, ...)

    @property
    def identical(self):
        return not self.items


class RestoreError(ValueError):
    """The backup can't be restored onto the object as it is now (a different object, or a different wave)."""


def writable(row):
    """Rows a restore may write: the editor's own write rules."""
    return not (row.read_only or row.bulk_only or row.write_ignored or row.a5000_only) and row.kind == "int"


def _ordered(items):
    plain = [i for i in items if i.row.key not in GEOMETRY_ORDER]
    geometry = sorted((i for i in items if i.row.key in GEOMETRY_ORDER), key=lambda i: GEOMETRY_ORDER.index(i.row.key))
    return plain + geometry


def plan_restore(backup, current):
    """`backup` / `current`: `BulkDump`s of the same object (the backup, and the object as it is now). Returns a `RestorePlan`;
    raises `RestoreError` when they can't be the same object."""
    if (backup.fmt, backup.name) != (current.fmt, current.name):
        raise RestoreError(f"the backup is of {backup.fmt} {backup.name!r}, not {current.fmt} {current.name!r}")
    old, now = bytes(backup.data), bytes(current.data)
    notes, items = [], []
    if backup.fmt == "SP":
        for label, offset in (("left", yp.WAVE_NAME_L_OFFSET), ("right", yp.WAVE_NAME_R_OFFSET)):
            if old[offset : offset + 16] != now[offset : offset + 16]:
                raise RestoreError(f"the sample's {label} wave is not the one the backup was taken from")
        scopes = [("sample", None)]
    elif backup.fmt == "PG":
        scopes = [("program", None)]
        count_old = yp.extract(yp.get("program", "assigned_samples"), old)
        count_now = yp.extract(yp.get("program", "assigned_samples"), now)
        if count_old != count_now:
            notes.append(
                f"The program has {count_now} assigned sample(s) now and had {count_old} in the backup - assignments are not "
                "restored (only the values of samples that are still assigned)."
            )
        for slot in range(min(count_old, count_now)):
            name_old = yp.extract(yp.get("easy_edit", "assigned_name"), old, slot)
            name_now = yp.extract(yp.get("easy_edit", "assigned_name"), now, slot)
            if name_old != name_now:
                notes.append(f"Slot {slot} holds {name_now!r} now but held {name_old!r} - its values are left alone.")
            else:
                scopes.append(("easy_edit", slot))
    else:
        raise RestoreError(f"{backup.fmt} objects can't be restored")
    for scope, slot in scopes:
        for row in yp.rows(scope):
            if not writable(row):
                continue
            value_old, value_now = yp.extract(row, old, slot), yp.extract(row, now, slot)
            if value_old != value_now:
                items.append(RestoreItem(row, slot, value_old, value_now))
    return RestorePlan(backup.fmt, backup.name, _ordered(items), notes)


def residual_offsets(backup, current):
    """Byte offsets where the two payloads still differ, ignoring the unit's "edited" flag (bit 0 of byte 1 of [Common], set by any
    edit). What a restore could not put back."""
    old, now = bytearray(backup.data), bytearray(current.data)
    if len(old) > 1 and len(now) > 1:
        old[1] &= 0xFE
        now[1] &= 0xFE
    return [i for i in range(min(len(old), len(now))) if old[i] != now[i]] + (
        list(range(min(len(old), len(now)), max(len(old), len(now)))) if len(old) != len(now) else []
    )


def load_backup(path):
    """Parse a backup `.syx` into a `BulkDump` (raises `RestoreError` for anything that isn't one)."""
    try:
        with open(path, "rb") as fh:
            messages = ysx.split_messages(fh.read())
        return ysx.parse_bulk_dump(messages[0])
    except (OSError, IndexError, ysx.YamahaSysexError) as e:
        raise RestoreError(f"{path} isn't a readable Yamaha bulk dump ({e})") from e
