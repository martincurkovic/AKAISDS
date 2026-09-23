"""Stable local copies of files dropped/opened into the Transfer Dashboard's
file queue - see dashboard.py's on_files_dropped/create_local_row.

Some sample-browser apps (e.g. Sononym, dragging a "cropped" preview out)
hand the OS drag-and-drop a path to a file the SOURCE app owns and expects
to clean up shortly after the drop completes - not a stable, permanent
file the way dragging straight out of Finder is. Qt's cross-platform
QMimeData.urls() has no way to participate in macOS's own file-promise
negotiation for this, so by the time this app sees the path, there's no
way to tell "the source app will delete this soon" from "this is exactly
where the file lives forever" - it just looks like an ordinary path
either way. Reading the file promptly (which create_local_row already
does once anyway, for its bit-depth probe) still works, since the temp
file is still there at THAT moment; it's the *later*, lazy read at actual
Send time (sampler_controller._send_next_queued_file_impl, whenever this
file's turn in the queue comes up) that loses the race once the source
app's own cleanup has already run - confirmed directly: the queue row
adds fine, but Send fails with "Skipping <name>: [Errno 2] No such file
or directory".

The fix: copy the file into a stable, app-owned location immediately -
right at drop/add time, while it's still guaranteed to exist - and queue
that copy instead of the original. Whatever the source app does to its
own temp file afterward stops mattering.
"""

import os
import shutil
from pathlib import Path

# every session's own copies live under a PID-named subdirectory here, so
# a normal shutdown only ever has to remove its own subtree (cleanup_
# session), and a crashed session's leftovers (sweep_orphaned_sessions)
# can be identified unambiguously by a PID that's no longer running -
# no separate lock file that could itself go stale.
_BASE_DIR = Path.home() / ".akaisds" / "dropped_files"

_session_dir = None  # cached by session_dir() - one per process


def session_dir():
    """This process's own subdirectory - created on first use. Never
    reused across process restarts: a fresh PID means a fresh directory,
    which is also what makes sweep_orphaned_sessions safe - a PID-named
    directory found at the next launch is unambiguously from a process
    that is no longer running (or it wouldn't have that same PID free to
    reuse), never this one.
    """
    global _session_dir
    if _session_dir is None:
        _session_dir = _BASE_DIR / str(os.getpid())
        _session_dir.mkdir(parents=True, exist_ok=True)
    return _session_dir


def copy_into_session(source_path):
    """Copies *source_path* into this session's own directory and returns
    the copy's path (a plain str, matching every other path this app
    passes around). Raises OSError the same way a plain shutil.copy2
    would - callers (create_local_row) already have their own established
    "couldn't read this file" handling for exactly that shape of failure.
    """
    dest_dir = session_dir()
    dest_path = dest_dir / _unique_name(dest_dir, os.path.basename(source_path))
    shutil.copy2(source_path, dest_path)
    return str(dest_path)


def _unique_name(dest_dir, filename):
    # two different dropped files can share a basename (cropped from the
    # same source sample in two different editors, say, or the same
    # source file dropped twice) - suffix with a counter rather than
    # silently overwriting one copy with another
    candidate = filename
    stem, ext = os.path.splitext(filename)
    counter = 1
    while (dest_dir / candidate).exists():
        candidate = f"{stem}-{counter}{ext}"
        counter += 1
    return candidate


def remove_copy(path):
    """Deletes one file this module copied in - once its queue row is
    sent, skipped, or removed (see dashboard.py's on_file_transferred/
    _remove_local_row/clear_local_queue) there's no reason to keep it
    around for the rest of the session. Only ever deletes something
    actually under _BASE_DIR, even if a caller passes something
    unexpected - this is the one function in this module that takes a
    path from outside it, so it's the one place that needs to guard
    against ever being pointed at a file this module didn't create.
    Silently does nothing for a path outside _BASE_DIR, already gone, or
    otherwise undeletable (permissions, in use, etc) - failing to clean
    up a temp copy is a minor annoyance, not something worth surfacing to
    the user or interrupting a transfer over.
    """
    try:
        resolved = Path(path).resolve()
        base_resolved = _BASE_DIR.resolve()
        if base_resolved == resolved or base_resolved not in resolved.parents:
            return
        os.remove(resolved)
    except OSError:
        pass


def cleanup_session():
    """Removes this whole process's own subtree - called on normal
    shutdown (see main_window.py's closeEvent, alongside its other
    end-of-life cleanup like closing the MIDI ports). The crash-safety
    backstop for when this DOESN'T run is sweep_orphaned_sessions, called
    once at the next normal launch instead - not attempted here.
    """
    global _session_dir
    if _session_dir is not None:
        shutil.rmtree(_session_dir, ignore_errors=True)
        _session_dir = None


def sweep_orphaned_sessions():
    """Removes every OTHER session's subdirectory whose PID is no longer
    running - the crash-safety fallback requested directly: a session
    that never reaches cleanup_session() (a crash, a force-quit, a killed
    process) leaves its directory behind forever otherwise, slowly
    accumulating stale copies of whatever was in the queue at the time.
    Self-healing at the next normal launch rather than a background
    watchdog process - this is a desktop app with no reason to notice a
    crash while it isn't even running.

    Called once, early, before this process's own session_dir() is ever
    used for real (see dashboard.py's __init__) - never touches this
    process's own directory (its PID can't appear as "not running" to
    itself) or anything in _BASE_DIR that isn't one of this module's own
    PID-named subdirectories.
    """
    if not _BASE_DIR.is_dir():
        return
    own_pid = os.getpid()
    for entry in _BASE_DIR.iterdir():
        if not entry.is_dir():
            continue
        try:
            pid = int(entry.name)
        except ValueError:
            continue  # not one of ours - never touch it
        if pid == own_pid or _pid_is_running(pid):
            continue
        shutil.rmtree(entry, ignore_errors=True)


def _pid_is_running(pid):
    # signal 0 sends nothing - just checks whether the process exists and
    # is signalable, the standard POSIX liveness check. Any failure mode
    # this can't confidently interpret defaults to "running" (i.e. don't
    # delete) - an orphaned directory left one more launch cycle costs
    # nothing; deleting a live session's own files would.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True
