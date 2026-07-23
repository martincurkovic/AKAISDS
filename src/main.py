import sys

# import os
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from ui import theme
from ui.main_window import ApplicationWindow

# def _load_stylesheet(app):
#     # style.qss should live next to the ui/ package
#     # this points to it
#     qss_path = os.path.join(os.path.dirname(__file__), "ui", "style.qss")
#     try:
#         with open(qss_path, "r") as f:
#             app.setStyleSheet(f.read())
#     except OSError as e:
#         print(
#             f"[WARN] Couldn't load stylesheet ({e}) - continuing with default appearance"
#         )


def _palette_for_scheme(scheme):
    if scheme == Qt.ColorScheme.Light:
        return theme.LIGHT_PALETTE
    return theme.DARK_PALETTE


def _apply_theme(app, scheme):
    try:
        app.setStyleSheet(theme.render_stylesheet(_palette_for_scheme(scheme)))
    except (OSError, KeyError) as e:
        print(
            f"[WARN] Couldn't apply theme ({e}) - continuing with whatever's currently set"
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # _load_stylesheet(app)

    style_hints = QGuiApplication.styleHints()
    _apply_theme(app, style_hints.colorScheme())

    # live switch if user changes their system theme while app is running
    style_hints.colorSchemeChanged.connect(lambda scheme: _apply_theme(app, scheme))

    window = ApplicationWindow()
    window.show()
    sys.exit(app.exec())
