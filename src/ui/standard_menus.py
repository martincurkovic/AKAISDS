"""Menu pieces every window shares, so the menu bars stay the same wherever an item applies:
Help (Quick Start, Keyboard Shortcuts, Open Log Folder, About, Third-Party Notices, Check for Updates), Hardware > Cancel Transfer and View > Zoom.
"""

from PySide6.QtGui import QAction, QKeySequence

from ui.about_dialog import AboutDialog, NoticesDialog
from ui.diagnostics_ui import add_open_log_folder_action
from ui.quickstart_dialog import show_quickstart_dialog
from ui.shortcuts_dialog import show_shortcuts_dialog
from ui.update_helper import UpdateCheckRunner


def add_help_menu(window, extra_shortcut_sections=()):
    """The Help menu, last on the bar. Gives `window` an `_update_runner`: wait() on it in closeEvent."""
    menu = window.menuBar().addMenu("&Help")

    quickstart_action = QAction("Quick Start Guide...", window)
    quickstart_action.triggered.connect(lambda: show_quickstart_dialog(window))
    menu.addAction(quickstart_action)

    shortcuts_action = QAction("Keyboard Shortcuts...", window)
    shortcuts_action.triggered.connect(lambda: show_shortcuts_dialog(window, extra_shortcut_sections))
    menu.addAction(shortcuts_action)

    menu.addSeparator()
    add_open_log_folder_action(menu, window)

    window._update_runner = UpdateCheckRunner(window)

    about_action = QAction("About AKAISDS...", window)
    about_action.setMenuRole(QAction.MenuRole.AboutRole)
    about_action.triggered.connect(lambda: AboutDialog(window).exec())
    menu.addAction(about_action)

    notices_action = QAction("Third-Party Notices...", window)  # (also behind the About window's Licences button)
    notices_action.triggered.connect(lambda: NoticesDialog(window).exec())
    menu.addAction(notices_action)

    check_for_updates_action = QAction("Check for Updates...", window)
    check_for_updates_action.triggered.connect(lambda: window._update_runner.start(manual=True))
    menu.addAction(check_for_updates_action)
    return menu


def add_cancel_transfer_action(window, menu, slot):
    # Ctrl+. (Cmd+. on a Mac); a no-op when nothing is running, so it is always enabled
    action = QAction("Cancel Transfer", window)
    action.setShortcut("Ctrl+.")
    action.triggered.connect(slot)
    menu.addAction(action)
    return action


def build_zoom_actions(window, get_view):
    """(zoom in, zoom out, zoom to fit) for the waveform `get_view()` returns. `get_view` is called when an action fires, so the
    view may not exist yet when the menu is built. Add them to a View menu with addAction."""
    # Ctrl+= first (shown in tooltips): StandardKey.ZoomIn's own primary, Ctrl++, needs Shift on a US keyboard. The standard
    # bindings stay as secondaries. Zoom out keeps its standard Ctrl+-; there is no standard key for fit, so Ctrl+0.
    zoom_in = QAction("Zoom In", window)
    zoom_in.setShortcuts([QKeySequence("Ctrl+=")] + QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomIn))
    zoom_in.triggered.connect(lambda: get_view().zoom_in())
    zoom_out = QAction("Zoom Out", window)
    zoom_out.setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomOut))
    zoom_out.triggered.connect(lambda: get_view().zoom_out())
    zoom_fit = QAction("Zoom to Fit", window)
    zoom_fit.setShortcut("Ctrl+0")
    zoom_fit.triggered.connect(lambda: get_view().reset_zoom())
    return zoom_in, zoom_out, zoom_fit
