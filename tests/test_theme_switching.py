# tests for ui/theme.py's live theme switching: the System/Light/Dark
# preference (Settings > Appearance), as opposed to test_theme.py's pure
# stylesheet-rendering checks.
#
# theme.py keeps its state in module globals (the active palette, the
# QApplication, the preference) and Qt keeps an app-wide color scheme
# override of its own - the fixture below puts both back exactly as it found
# them so nothing leaks into the rest of the suite.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from ui import theme


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeApp:
    """Stands in for QApplication where only theme.py's own logic is under
    test. A real QApplication.setStyleSheet() restyles EVERY live widget in
    the process - and by the time these tests run, the earlier window tests
    have left plenty alive, so each theme switch took seconds."""

    def __init__(self):
        self._sheet = ""

    def setStyle(self, name):
        pass

    def setStyleSheet(self, sheet):
        self._sheet = sheet

    def styleSheet(self):
        return self._sheet


@pytest.fixture
def themed(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(theme, "_GENERATED_ICONS_DIR", str(tmp_path / "icons"))
    saved = (theme._active_palette, theme._app, theme._preference, theme._connected_app)
    changes = []
    theme.notifier.changed.connect(lambda: changes.append(1))
    # start every test from a known, un-applied state
    theme._active_palette = None
    theme._connected_app = None
    yield changes
    QGuiApplication.styleHints().unsetColorScheme()
    (theme._active_palette, theme._app, theme._preference, theme._connected_app) = saved
    theme.notifier.changed.disconnect()


def test_light_preference_pins_the_light_palette(qapp, themed):
    qapp = _FakeApp()
    theme.apply_to_app(qapp, "light")
    assert theme.current_palette() is theme.LIGHT_PALETTE
    assert theme.current_preference() == "light"
    assert theme.LIGHT_PALETTE["bg"] in qapp.styleSheet()


def test_dark_preference_pins_the_dark_palette(qapp, themed):
    qapp = _FakeApp()
    theme.apply_to_app(qapp, "dark")
    assert theme.current_palette() is theme.DARK_PALETTE
    assert theme.DARK_PALETTE["bg"] in qapp.styleSheet()


def test_switching_theme_live_restyles_the_whole_app(qapp, themed):
    qapp = _FakeApp()
    theme.apply_to_app(qapp, "light")
    light_sheet = qapp.styleSheet()
    theme.set_theme_preference("dark")
    assert theme.current_palette() is theme.DARK_PALETTE
    assert qapp.styleSheet() != light_sheet
    theme.set_theme_preference("light")
    assert theme.current_palette() is theme.LIGHT_PALETTE
    assert qapp.styleSheet() == light_sheet


def test_system_follows_whatever_the_os_reports(qapp, themed):
    qapp = _FakeApp()
    theme.apply_to_app(qapp, "dark")
    theme.set_theme_preference("system")
    os_scheme = QGuiApplication.styleHints().colorScheme()
    assert theme.current_palette() is theme._palette_for_scheme(os_scheme)
    assert theme.current_preference() == "system"


def test_listeners_are_told_once_per_real_change(qapp, themed):
    qapp = _FakeApp()
    changes = themed
    theme.apply_to_app(qapp, "light")
    assert len(changes) == 1  # the first apply
    theme.set_theme_preference("light")  # nothing actually changes
    assert len(changes) == 1
    theme.set_theme_preference("dark")
    assert len(changes) == 2
    theme.set_theme_preference("dark")
    assert len(changes) == 2


def test_choosing_the_theme_the_os_already_shows_does_no_restyle(qapp, themed):
    qapp = _FakeApp()
    changes = themed
    theme.apply_to_app(qapp, "system")
    first = len(changes)
    os_theme = (
        "light"
        if QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Light
        else "dark"
    )
    theme.set_theme_preference(os_theme)
    assert len(changes) == first


def test_a_pinned_theme_ignores_the_os_changing(qapp, themed):
    qapp = _FakeApp()
    theme.apply_to_app(qapp, "dark")
    # the OS reports a scheme change - a pinned theme must not follow it
    QGuiApplication.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Light)
    assert theme.current_palette() is theme.DARK_PALETTE


def test_system_still_follows_the_os_changing(qapp, themed):
    qapp = _FakeApp()
    theme.apply_to_app(qapp, "system")
    # simulate the OS flipping: the handler just re-reads the effective scheme
    flipped = (
        theme.LIGHT_PALETTE
        if theme.current_palette() is theme.DARK_PALETTE
        else theme.DARK_PALETTE
    )
    theme._palette_for_scheme_original = theme._palette_for_scheme
    try:
        theme._palette_for_scheme = lambda scheme: flipped
        QGuiApplication.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Light)
    finally:
        theme._palette_for_scheme = theme._palette_for_scheme_original
    assert theme.current_palette() is flipped


def test_unrecognised_preference_means_system(qapp, themed):
    qapp = _FakeApp()
    theme.apply_to_app(qapp, "solarized")
    assert theme.current_preference() == "system"


def test_apply_to_app_uses_the_saved_theme_by_default(qapp, themed, monkeypatch):
    qapp = _FakeApp()
    monkeypatch.setattr(theme.app_config, "get_saved_theme", lambda: "light")
    theme.apply_to_app(qapp)
    assert theme.current_palette() is theme.LIGHT_PALETTE


def test_theme_choices_are_system_light_dark_in_that_order():
    assert theme.CHOICES == [("System", "system"), ("Light", "light"), ("Dark", "dark")]


def test_muted_labels_are_themed_by_the_stylesheet_not_baked_in(
    monkeypatch, tmp_path
):
    # QLabel#mutedLabel replaced two inline setStyleSheet(color: ...) calls
    # that froze the color at construction and couldn't follow a theme switch
    monkeypatch.setattr(theme, "_GENERATED_ICONS_DIR", str(tmp_path / "icons"))
    for palette in (theme.DARK_PALETTE, theme.LIGHT_PALETTE):
        sheet = theme.render_stylesheet(palette)
        rule = sheet[sheet.index("QLabel#mutedLabel") :].split("}")[0]
        assert palette["text_disabled"] in rule


def test_generated_chevron_files_are_per_color_so_they_never_change(
    monkeypatch, tmp_path
):
    # Qt caches stylesheet images by path - the same file path getting new
    # contents on a theme switch could keep showing the old arrow
    monkeypatch.setattr(theme, "_GENERATED_ICONS_DIR", str(tmp_path / "icons"))
    dark = theme.render_stylesheet(theme.DARK_PALETTE)
    light = theme.render_stylesheet(theme.LIGHT_PALETTE)
    import re

    dark_arrows = set(re.findall(r"[^\s'\"(]*down_arrow[^\s'\")]*\.svg", dark))
    light_arrows = set(re.findall(r"[^\s'\"(]*down_arrow[^\s'\")]*\.svg", light))
    assert dark_arrows and light_arrows
    assert dark_arrows.isdisjoint(light_arrows)
