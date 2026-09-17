from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QLayout
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from ui.ascii_logo import LOGO

try:
    from ui._version import APP_VERSION
except ImportError:
    APP_VERSION = "1.0.0-dev"  # fallback version number
GITHUB_URL = "https://github.com/martincurkovic/AKAISDS"


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About AKAISDS")

        layout = QVBoxLayout(self)

        logo_label = QLabel(LOGO)
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        logo_font.setPointSize(8)
        logo_label.setFont(logo_font)
        layout.addWidget(logo_label)

        version_label = QLabel(f"Version {APP_VERSION}")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version_label)

        description_label = QLabel(
            "A cross-platform MIDI Sample Dump Standard transfer tool\n"
            "for Akai and generic SDS-compatible samplers."
        )
        description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description_label.setWordWrap(True)
        layout.addWidget(description_label)

        link_label = QLabel(f'<a href="{GITHUB_URL}">{GITHUB_URL}</a>')
        link_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        link_label.setOpenExternalLinks(True)
        layout.addWidget(link_label)

        credits_label = QLabel(
            "Built with PySide6 (Qt), mido, python-rtmidi, soundfile and Nuitka.\n"
            "Licensed under GPL-3.0"
        )
        credits_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credits_label.setWordWrap(True)
        layout.addWidget(credits_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
