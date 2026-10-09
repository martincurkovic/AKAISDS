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
# The settle pauses around a block write (a real second or more) must not slow the suite; tests that check the spacing replace this hook.
_program_editor_bridge._sleep = lambda seconds: None


# The Global tab's test switch must never leak in from a developer's shell: the locked-controls tests assume nothing is unlocked.
os.environ.pop("AKAISDS_UNLOCK_GLOBAL", None)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_LOG_DIR, ignore_errors=True)
