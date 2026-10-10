from html import escape

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from core import debug_log, loopback_report

try:
    from ui._version import APP_VERSION
except ImportError:
    APP_VERSION = "0.0.0-dev"

# same green as the knob arcs; the red reads on both the light and dark theme
_PASS_COLOR = "#3aa88a"
_FAIL_COLOR = "#d9534f"

_SHARE_HINT = (
    "Help other users: share your results so this interface can be added to the "
    "tested list. Copy them, or open a GitHub issue with them filled in."
)


class LoopbackResultsDialog(QDialog):
    def __init__(self, results, input_name, output_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Loopback Test Results")
        self._results = results
        self._input_name = input_name
        self._output_name = output_name
        self._os_name = loopback_report.describe_os()

        header = QLabel(self._header_html())
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        body = QLabel(self._results_html())
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        verdict = QLabel(loopback_report.verdict(results))
        verdict.setWordWrap(True)
        verdict.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        verdict.setMinimumWidth(420)

        hint = QLabel(_SHARE_HINT)
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        hint.setObjectName("mutedLabel")

        self.copy_button = QPushButton("Copy Results")
        self.copy_button.clicked.connect(self._copy)
        self.share_button = QPushButton("Share on GitHub")
        self.share_button.clicked.connect(self._share)
        close_button = QPushButton("Close")
        close_button.setDefault(True)
        close_button.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.share_button)
        buttons.addStretch()
        buttons.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(header, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(10)
        layout.addWidget(body, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(10)
        layout.addWidget(verdict)
        layout.addSpacing(12)
        layout.addWidget(hint)
        layout.addSpacing(6)
        layout.addLayout(buttons)

    def _header_html(self):
        rows = [line.split(": ", 1) for line in loopback_report.header_lines(
            self._input_name, self._output_name, self._os_name, APP_VERSION)]
        return "<br>".join(f"<b>{escape(label)}:</b> {escape(value)}" for label, value in rows)

    def _results_html(self):
        rows = []
        for size, passed, detail in self._results:
            status = (
                f'<span style="color:{_PASS_COLOR}"><b>PASS</b></span>'
                if passed
                else f'<span style="color:{_FAIL_COLOR}"><b>FAIL</b></span> - {escape(detail)}'
            )
            padded = f"{size:>5}".replace(" ", "&nbsp;")
            rows.append(f"{padded} bytes: {status}")
        family = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
        return f'<span style="font-family:\'{family}\'">' + "<br>".join(rows) + "</span>"

    def report(self):
        return loopback_report.report_text(
            self._results, self._input_name, self._output_name, self._os_name, APP_VERSION
        )

    def _copy(self):
        QApplication.clipboard().setText(self.report())
        self.copy_button.setText("Copied")
        debug_log.get_logger().info("loopback results: copied to clipboard")

    def _share(self):
        url = loopback_report.issue_url(
            self._results, self._input_name, self._output_name, self._os_name, APP_VERSION
        )
        debug_log.get_logger().info("loopback results: opening GitHub issue page")
        QDesktopServices.openUrl(QUrl(url))
