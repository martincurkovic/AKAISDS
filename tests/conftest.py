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
