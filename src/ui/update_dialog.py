from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLayout, QPushButton, QVBoxLayout
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices

try:
    from ui._version import APP_VERSION
except ImportError:
    APP_VERSION = "0.0.0-dev"


class UpdateAvailableDialog(QDialog):
    """Non-nagging "a new version exists" notice.

    Deliberately doesn't offer to download/install anything itself - it
    just points the user at the GitHub release page. See the update
    checker's design discussion for why (no code signing/notarization in
    the build pipeline yet, so a downloaded asset needs the user's own
    Gatekeeper/SmartScreen click-through regardless).
    """

    def __init__(self, update_info, parent=None):
        super().__init__(parent)
        self.update_info = update_info
        self.setWindowTitle("Update Available")

        layout = QVBoxLayout(self)

        heading = QLabel(f"AKAISDS {update_info.version} is available")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading_font = heading.font()
        heading_font.setBold(True)
        heading.setFont(heading_font)
        layout.addWidget(heading)

        current_label = QLabel(f"You have version {APP_VERSION} installed.")
        current_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(current_label)

        button_row = QHBoxLayout()

        skip_button = QPushButton("Skip This Version")
        skip_button.clicked.connect(self._skip_version)
        button_row.addWidget(skip_button)

        later_button = QPushButton("Remind Me Later")
        later_button.clicked.connect(self.reject)
        button_row.addWidget(later_button)

        view_button = QPushButton("View Release...")
        view_button.setDefault(True)
        view_button.clicked.connect(self._open_release_page)
        button_row.addWidget(view_button)

        layout.addLayout(button_row)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

    def _open_release_page(self):
        QDesktopServices.openUrl(QUrl(self.update_info.html_url))
        self.accept()

    def _skip_version(self):
        from core import app_config

        app_config.save_skipped_update_version(self.update_info.version)
        self.reject()
