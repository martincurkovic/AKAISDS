# The Help menu every window shares (ui/standard_menus.add_help_menu): its Third-Party Notices item opens the same viewer as the About window's
# Licences button.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMainWindow

from ui import standard_menus


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _help_menu(window):
    return next(a.menu() for a in window.menuBar().actions() if a.text().replace("&", "") == "Help")


def test_the_help_menu_lists_third_party_notices_between_about_and_updates(qapp):
    window = QMainWindow()
    standard_menus.add_help_menu(window)
    texts = [a.text() for a in _help_menu(window).actions() if not a.isSeparator()]
    assert texts == ["Quick Start Guide...", "Keyboard Shortcuts...", "Open Log Folder", "About AKAISDS...", "Third-Party Notices...", "Check for Updates..."]
    window._update_runner.wait()


def test_choosing_it_opens_the_notices_viewer_over_the_window(qapp, monkeypatch):
    opened = []

    class Stub:
        def __init__(self, parent):
            opened.append(parent)

        def exec(self):
            opened.append("exec")

    monkeypatch.setattr(standard_menus, "NoticesDialog", Stub)
    window = QMainWindow()
    standard_menus.add_help_menu(window)
    next(a for a in _help_menu(window).actions() if a.text() == "Third-Party Notices...").trigger()
    assert opened == [window, "exec"]
    window._update_runner.wait()


def test_the_real_viewer_shows_the_bundled_notices(qapp):
    from ui.about_dialog import NoticesDialog

    dialog = NoticesDialog()
    assert dialog.windowTitle() == "Third-party notices"
    text = dialog.findChild(__import__("PySide6.QtWidgets", fromlist=["QTextBrowser"]).QTextBrowser).toPlainText()
    assert "VelociLoops" in text and "ChiKareGo2" in text
    dialog.close()
