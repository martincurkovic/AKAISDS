"""The `.syx` backups the Yamaha editor saves before the first write to an object (see controller/yamaha_session.py), listed for
the Restore dialog. Files are named `<FMT>-<object>-<YYYYmmdd-HHMMSS>[-n].syx`; the object really comes from the dump inside."""

import dataclasses
import os
import time

from core import yamaha_restore as yr


@dataclasses.dataclass
class BackupEntry:
    path: str
    fmt: str  # "PG" / "SP"
    name: str  # "001" / the sample's name
    saved_at: float  # file time

    @property
    def kind(self):
        return {"PG": "Program", "SP": "Sample"}.get(self.fmt, self.fmt)

    @property
    def text(self):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.saved_at))
        return f"{self.kind} {self.name}   {stamp}"


def list_backups(directory):
    """Every readable backup in `directory`, newest first (unreadable files are skipped, never raised on)."""
    entries = []
    try:
        names = os.listdir(directory)
    except OSError:
        return entries
    for filename in names:
        if not filename.lower().endswith(".syx"):
            continue
        path = os.path.join(directory, filename)
        try:
            dump = yr.load_backup(path)
            entries.append(BackupEntry(path, dump.fmt, dump.name, os.path.getmtime(path)))
        except (yr.RestoreError, OSError):
            continue
    entries.sort(key=lambda e: (e.saved_at, e.path), reverse=True)
    return entries
