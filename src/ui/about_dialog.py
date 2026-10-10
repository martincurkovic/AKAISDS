import os

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
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
# the dialog is fixed-size, so its width follows the widest label: wrap the text labels here
_TEXT_MAX_WIDTH = 440
# bundled with the rest of ui/help (pysidedeploy.spec's --include-data-dir=ui/help=ui/help)
_NOTICES_PATH = os.path.join(
    os.path.dirname(__file__), "help", "THIRD_PARTY_NOTICES.md"
)


class NoticesDialog(QDialog):
    """Third-party licence notices (help/THIRD_PARTY_NOTICES.md), shown from the About dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Third-party notices")
        self.resize(760, 520)
        viewer = QTextBrowser()
        viewer.setOpenExternalLinks(True)
        try:
            with open(_NOTICES_PATH, "r", encoding="utf-8") as f:
                viewer.setMarkdown(f.read())
        except OSError as e:
            viewer.setPlainText(f"Couldn't load the notices: {e}")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(viewer)
        layout.addWidget(buttons)
        # the licence text is a preformatted block that doesn't wrap, so size the window to its
        # longest line (font-dependent) instead of a fixed width that needs a horizontal scrollbar
        wanted = (
            int(viewer.document().idealWidth())
            + 2 * viewer.frameWidth()
            + viewer.verticalScrollBar().sizeHint().width()
            + 2 * layout.contentsMargins().left()
            + 16
        )
        screen = self.screen()
        limit = int(screen.availableGeometry().width() * 0.9) if screen else wanted
        self.resize(min(max(wanted, self.width()), limit), self.height())


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
            "A cross-platform sample transfer tool and editor for Akai, Yamaha "
            "and generic MIDI SDS-compatible samplers."
        )
        description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description_label.setWordWrap(True)
        description_label.setFixedWidth(_TEXT_MAX_WIDTH)
        description_label.setFixedHeight(description_label.heightForWidth(_TEXT_MAX_WIDTH))
        layout.addWidget(description_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        link_label = QLabel(f'<a href="{GITHUB_URL}">{GITHUB_URL}</a>')
        link_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        link_label.setOpenExternalLinks(True)
        layout.addWidget(link_label)

        credits_label = QLabel(
            "Built with PySide6 (Qt), mido, python-rtmidi, soundfile, miniaudio, s3ked and Nuitka.\n"
            "Many thanks to Jan Lentfer for their hard work on s3ked, and to Frank Neumann "
            "for transcribing the Akai SysEx documentation (lakai.sourceforge.net).\n"
            "S900/S950 support is partly ported from s950tools by Brandon Ivers (MIT).\n"
            "Licensed under GPL-3.0"
        )
        credits_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credits_label.setWordWrap(True)
        credits_label.setFixedWidth(_TEXT_MAX_WIDTH)
        credits_label.setFixedHeight(credits_label.heightForWidth(_TEXT_MAX_WIDTH))
        layout.addWidget(credits_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._update_runner = UpdateCheckRunner(self)

        check_updates_row = QHBoxLayout()
        self._check_updates_button = QPushButton("Check for Updates")
        self._check_updates_button.clicked.connect(self._check_for_updates)
        check_updates_row.addStretch()
        check_updates_row.addWidget(self._check_updates_button)
        notices_button = QPushButton("Licences")
        notices_button.clicked.connect(lambda: NoticesDialog(self).exec())
        check_updates_row.addWidget(notices_button)
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
        self._update_runner.start(
            manual=True, on_finished=self._on_update_check_finished
        )

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
