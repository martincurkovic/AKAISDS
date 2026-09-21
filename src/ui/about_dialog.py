from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QLayout,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from ui.ascii_logo import LOGO
from ui.update_helper import UpdateCheckRunner

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
            "Built with PySide6 (Qt), mido, python-rtmidi, soundfile, s3ked and Nuitka.\n"
            "Many thanks to Jan Lentfer for their hard work on s3ked.\n"
            "Licensed under GPL-3.0"
        )
        credits_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credits_label.setWordWrap(True)
        layout.addWidget(credits_label)

        self._update_runner = UpdateCheckRunner(self)

        check_updates_row = QHBoxLayout()
        self._check_updates_button = QPushButton("Check for Updates")
        self._check_updates_button.clicked.connect(self._check_for_updates)
        check_updates_row.addStretch()
        check_updates_row.addWidget(self._check_updates_button)
        check_updates_row.addStretch()
        layout.addLayout(check_updates_row)

        # indeterminate - a version check has no measurable progress to show,
        # just "still waiting on the network". Hidden until a check is
        # actually running so it doesn't just sit there empty otherwise.
        self._update_progress = QProgressBar()
        self._update_progress.setRange(0, 0)
        self._update_progress.setTextVisible(False)
        self._update_progress.setFixedHeight(6)
        self._update_progress.setVisible(False)
        layout.addWidget(self._update_progress)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

    def _check_for_updates(self):
        self._check_updates_button.setEnabled(False)
        self._update_progress.setVisible(True)
        self._update_runner.start(manual=True, on_finished=self._on_update_check_finished)

    def _on_update_check_finished(self):
        self._update_progress.setVisible(False)
        self._check_updates_button.setEnabled(True)

    def done(self, result):
        # make sure a check that's still running can't outlive this dialog's
        # Python object - see UpdateCheckRunner.wait()'s docstring. done()
        # is the one method every close path (OK button, Escape, the title
        # bar's close box) funnels through.
        self._update_runner.wait()
        super().done(result)
