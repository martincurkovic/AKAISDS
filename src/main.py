import sys

from PySide6.QtWidgets import QApplication
from ui import theme
from ui.main_window import ApplicationWindow

if __name__ == "__main__":
    app = QApplication(sys.argv)
    theme.apply_to_app(app)

    window = ApplicationWindow()
    window.show()
    sys.exit(app.exec())
