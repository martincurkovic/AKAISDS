import platform
import sys

import mido
from PySide6.QtWidgets import QApplication
from core import debug_log
from ui import theme
from ui._version import APP_VERSION
from ui.main_window import ApplicationWindow


def _log_startup():
    # first lines of every session: version/OS/backend and which MIDI ports the OS
    # offers - the context a user's log needs before anything else in it makes sense
    log = debug_log.get_logger()
    try:
        log.info(
            f"=== AKAISDS {APP_VERSION} on {platform.platform()}, python "
            f"{platform.python_version()}, mido backend {mido.backend.name}"
        )
        log.info(f"MIDI inputs: {mido.get_input_names()}")
        log.info(f"MIDI outputs: {mido.get_output_names()}")
    except Exception:
        log.error("startup: couldn't enumerate MIDI ports", exc_info=True)

if __name__ == "__main__":
    _log_startup()
    app = QApplication(sys.argv)
    theme.apply_to_app(app)

    window = ApplicationWindow()
    window.show()
    sys.exit(app.exec())
