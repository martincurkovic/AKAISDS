"""Qt-side helpers for bug reports: log every message box the user is shown, and
a Help-menu way to reach the log folder."""

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QMessageBox

from core import debug_log

_KINDS = ("warning", "critical", "information", "question")
_MAX_TEXT = 500
_installed = False


def _one_line(value):
    text = " ".join(str(value).split())
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "..."


def install_dialog_logging():
    # what the user was TOLD is half of every bug report ("it said something about
    # a name"); this records the title/text of each static QMessageBox and which
    # button they pressed. Idempotent.
    global _installed
    if _installed:
        return
    _installed = True

    def wrap(kind):
        original = getattr(QMessageBox, kind)

        def wrapper(*args, **kwargs):
            title = args[1] if len(args) > 1 else kwargs.get("title", "")
            text = args[2] if len(args) > 2 else kwargs.get("text", "")
            log = debug_log.get_logger()
            log.info(f"dialog [{kind}] {_one_line(title)!r}: {_one_line(text)!r}")
            result = original(*args, **kwargs)
            log.info(
                f"dialog [{kind}] {_one_line(title)!r} -> {getattr(result, 'name', result)}"
            )
            return result

        setattr(QMessageBox, kind, staticmethod(wrapper))

    for kind in _KINDS:
        wrap(kind)


def open_log_folder():
    folder = debug_log.LOG_PATH.parent
    folder.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def add_open_log_folder_action(menu, parent):
    action = QAction("Open Log Folder", parent)
    action.setToolTip(f"Opens {debug_log.LOG_PATH.parent} - attach akaisds.log to a bug report")
    action.triggered.connect(open_log_folder)
    menu.addAction(action)
    return action
