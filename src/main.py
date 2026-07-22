import sys
import os
from PySide6.QtWidgets import QApplication
from ui.main_window import ApplicationWindow


def _load_stylesheet(app):
    # style.qss should live next to the ui/ package
    # this points to it
    qss_path = os.path.join(os.path.dirname(__file__), "ui", "style.qss")
    try:
        with open(qss_path, "r") as f:
            app.setStyleSheet(f.read())
    except OSError as e:
        print(
            f"[WARN] Couldn't load stylesheet ({e}) - continuing with default appearance"
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    _load_stylesheet(app)
    window = ApplicationWindow()
    window.show()
    sys.exit(app.exec())
