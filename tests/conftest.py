# sys.path setup so tests can imports the app's own modules the same way that main.py does

import sys
import os

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))

# Force headless Qt for every test in this suite, unconditionally - not a
# setdefault. Every individual test_*.py file does its own
# os.environ.setdefault("QT_QPA_PLATFORM", "offscreen") before constructing
# a QApplication, but setdefault only takes effect if the var is ABSENT -
# on a desktop that already exports QT_QPA_PLATFORM globally (e.g. Omarchy/
# Hyprland setting "wayland;xcb" so Qt apps run natively on Wayland - real,
# confirmed via `env` on a real machine, not hypothetical), every one of
# those per-file calls is a silent no-op and the "offscreen" suite actually
# runs against the real platform instead. Nothing showed this for months
# because almost nothing in the suite calls .show() - tests/
# test_qt_helpers.py's tab-width tests are the one exception, and running
# for real is exactly why they were intermittently flaky: a genuine
# top-level window briefly appeared on-screen, and a real tiling WM
# (Hyprland) overrides its geometry to fit its own tiling layout the
# moment it's shown/resized, so the test's own resize(400, 200) never
# actually took effect - confirmed by a user who had the window manager
# visibly reflow a grey window mid test-run. conftest.py is always
# imported by pytest before any test module, so a plain, unconditional
# assignment here (not setdefault) wins over whatever the ambient shell
# already set, and every test file's own now-redundant setdefault call
# becomes a harmless no-op (already "offscreen") rather than the thing
# actually deciding anything.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Send the app's debug log to a throwaway directory for the whole run. Without this the
# suite appended thousands of lines (fake sends, fake MIDI errors) to the developer's REAL
# ~/.akaisds/akaisds.log - the very file a user is asked to send back, and one that rotates
# at 8 MB, so a test run could push genuine hardware evidence out of it. LOG_PATH must be
# replaced before anything calls debug_log.get_logger() (it opens the file lazily) and before
# core.diagnostics derives CRASH_PATH from it; conftest.py is imported before any test module.
import shutil
import tempfile
from pathlib import Path

from core import debug_log

_TEST_LOG_DIR = Path(tempfile.mkdtemp(prefix="akaisds-test-log-"))
debug_log.LOG_PATH = _TEST_LOG_DIR / "akaisds.log"


# Likewise the Yamaha editor's pre-write .syx backups (controller/yamaha_session.py) - a test run must not
# leave files in the developer's real ~/.akaisds/a4000_backups, where the genuine ones are.
from controller import yamaha_session as _yamaha_session

_yamaha_session.BACKUP_DIR = _TEST_LOG_DIR / "a4000_backups"


# And the program-file backups made before an S1000 keygroup delete-by-rebuild.
from core import program_editor_bridge as _program_editor_bridge

_program_editor_bridge.PROGRAM_BACKUP_DIR = _TEST_LOG_DIR / "program_backups"
# ...and the whole-block misc-data backups made before the first Global-tab block write.
_program_editor_bridge.MDATA_BACKUP_DIR = _TEST_LOG_DIR / "mdata_backups"
# ...and the multi backups made before Load Multi writes.
_program_editor_bridge.MULTI_BACKUP_DIR = _TEST_LOG_DIR / "multi_backups"
# The settle pauses around a block write (a real second or more) must not slow the suite; tests that check the spacing replace this hook.
_program_editor_bridge._sleep = lambda seconds: None
# ...and the copies of audio saved before an externally edited sample replaces it.
from core import external_edit as _external_edit

_external_edit.BACKUP_DIR = _TEST_LOG_DIR / "external_edit_backups"


# The user's real ~/.akaisds/config.json must never decide a test's outcome. It once did: the Yamaha editor's one-time
# "experimental write" warning is a modal QMessageBox unless the config says it was acknowledged, so tests passed on the
# owner's machine and hung forever in CI (no config, nobody to click the dialog). Tests start from a fresh install's config.
from core import app_config as _app_config

_app_config.CONFIG_PATH = _TEST_LOG_DIR / "config.json"


# The Global tab's test switch must never leak in from a developer's shell: the locked-controls tests assume nothing is unlocked.
os.environ.pop("AKAISDS_UNLOCK_GLOBAL", None)


import pytest as _pytest


@_pytest.fixture(autouse=True)
def _fresh_section_card_state():
    # which cards are collapsed is cached per process (ui/section_card.py): one test collapsing a card must not start the next one collapsed
    from ui import section_card

    section_card.reset_state()
    yield
    section_card.reset_state()


_exit_status = None


def pytest_sessionfinish(session, exitstatus):
    global _exit_status
    _exit_status = int(exitstatus)
    shutil.rmtree(_TEST_LOG_DIR, ignore_errors=True)


def pytest_terminal_summary(terminalreporter):
    # Opt-in (AKAISDS_TEST_WIDGET_COUNT=1): how many windows and dialogs the tests left alive, which is what the exit below skips destroying, and what every
    # theme switch while the suite runs has to restyle. Counts Python wrappers only (gc + shiboken6.isValid): QApplication.allWidgets() makes PySide wrap EVERY
    # widget Qt knows about, and segfaulted on one that was half destroyed (2026-10-31, at the end of a full run), so never use it here.
    if not os.environ.get("AKAISDS_TEST_WIDGET_COUNT"):
        return
    try:
        import gc
        from collections import Counter

        import shiboken6
        from PySide6.QtWidgets import QDialog, QMainWindow

        alive = Counter(type(o).__name__ for o in gc.get_objects() if isinstance(o, (QMainWindow, QDialog)) and shiboken6.isValid(o))
        common = ", ".join(f"{n} x {name}" for name, n in alive.most_common(6))
        terminalreporter.write_line(f"Windows and dialogs still alive: {sum(alive.values())} ({common})")
    except Exception:
        pass


@_pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    # Leave the moment the summary is out. Python's own shutdown destroys every Qt widget the tests left alive, and PySide's
    # SenderSignalDeletionTracker::senderDestroyed makes that slow in proportion to how many there are: 3 minutes on Qt 6.11 and about 9 on 6.8 after the
    # last test on the owner's Mac (sampled 2026-10-31: the main thread in destroyQCoreApplication), 6 minutes on a CI runner with 6.11, and the Qt 6.8 CI job
    # never finished (cancelled at its 40-minute limit, 16 minutes after its summary). Nothing a test checks happens at exit.
    # AKAISDS_TEST_NORMAL_EXIT=1 gives the ordinary shutdown back (to debug an exit-time problem).
    if _exit_status is None or os.environ.get("AKAISDS_TEST_NORMAL_EXIT"):
        return
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_exit_status)
