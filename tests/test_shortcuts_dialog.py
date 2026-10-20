import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMainWindow

from ui import shortcuts_dialog as sd


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _window_with_menus(qapp):
    window = QMainWindow()
    file_menu = window.menuBar().addMenu("&File")
    open_action = QAction("&Open...", window)
    open_action.setShortcut("Ctrl+O")
    file_menu.addAction(open_action)
    file_menu.addSeparator()
    file_menu.addAction(QAction("No shortcut", window))
    view_menu = window.menuBar().addMenu("&View")
    zoom = QAction("Zoom In", window)
    zoom.setShortcuts(["Ctrl+=", "Ctrl++"])
    view_menu.addAction(zoom)
    window.menuBar().addMenu("&Empty").addAction(QAction("Nothing", window))
    return window


def test_menu_shortcuts_are_read_from_the_menu_bar(qapp):
    window = _window_with_menus(qapp)  # (kept: the menu bar dies with its window)
    sections = sd.collect_menu_shortcuts(window.menuBar())
    assert [title for title, _rows in sections] == ["File", "View"]  # a menu without shortcuts is left out
    assert sections[0][1] == [("Open...", sd._native("Ctrl+O"))]  # mnemonics stripped, unbound actions skipped
    assert " or " in sections[1][1][0][1]  # every binding of an action is shown


def test_a_submenu_is_searched_too(qapp):
    window = QMainWindow()
    menu = window.menuBar().addMenu("&Tools")
    sub = menu.addMenu("More")
    action = QAction("Deep", window)
    action.setShortcut("Ctrl+D")
    sub.addAction(action)
    assert sd.collect_menu_shortcuts(window.menuBar()) == [("Tools", [("Deep", sd._native("Ctrl+D"))])]


def test_the_dialog_lists_menu_and_extra_sections(qapp):
    window = _window_with_menus(qapp)
    sections = sd.collect_menu_shortcuts(window.menuBar()) + [sd.audition_section()]
    dialog = sd.ShortcutsDialog(window, sections)
    assert [title for title, _rows in dialog.sections][-1].startswith("Audition keyboard")


def test_the_dialog_is_reused_per_window(qapp):
    window = _window_with_menus(qapp)
    sd.show_shortcuts_dialog(window)
    first = window._shortcuts_dialog
    sd.show_shortcuts_dialog(window)
    assert window._shortcuts_dialog is first
    first.close()


@pytest.mark.parametrize(
    "section", [sd.audition_section, sd.lists_section, sd.slice_editor_section, sd.preview_dialogs_section, sd.dragging_section]
)
def test_every_extra_section_has_a_title_and_filled_rows(qapp, section):
    title, rows = section()
    assert title and rows
    assert all(what and keys for what, keys in rows)


def test_the_dialog_is_wide_enough_for_its_widest_row(qapp):
    sections = [
        ("Window", [("Transfer Dashboard", "⌘T")]),
        ("Audition", [("Play notes, starting at C3 and a very much longer description than usual to force the width", "A W S E D F T G Y H U J K O L P ;")]),
    ]
    dialog = sd.ShortcutsDialog(None, sections)
    dialog.show()
    qapp.processEvents()
    scroll = dialog.findChild(sd.QScrollArea)
    assert scroll.horizontalScrollBar().maximum() == 0
    assert dialog.width() >= dialog.minimumWidth() > 0
    dialog.close()
