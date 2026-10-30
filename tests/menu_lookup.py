"""Find a top-level menu of a window by title, in a way that works on every Qt we build against.

`next(a.menu() for a in window.menuBar().actions() ...)` hands back a QMenu wrapper that PySide6 6.8 invalidates ("Internal C++ object
already deleted") as soon as the temporary QAction wrapper is released, though the menu is still in the bar (found 2026-10-31 while
trying the Qt 6.8 legacy build; fine on 6.11). The menu bar is the menus' parent, so look them up as children instead.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu


def top_menu(window, title):
    """The top-level menu titled `title` (a leading `&` mnemonic is ignored on both sides)."""
    wanted = title.replace("&", "")
    return next(m for m in window.menuBar().findChildren(QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly)
                if m.title().replace("&", "") == wanted)
