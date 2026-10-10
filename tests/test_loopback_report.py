import os
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core import loopback_report as lr

PASS_ALL = [(8, True, "OK"), (256, True, "OK")]
SOME_FAIL = [(8, True, "OK"), (512, False, "no response (timed out)"), (1024, False, "no response (timed out)")]


def test_header_names_interface_os_and_version():
    lines = lr.header_lines("Studio 26", "Studio 26", "macOS 15.1", "1.3.0")
    assert lines == ["MIDI interface: Studio 26", "Operating system: macOS 15.1", "AKAISDS version: 1.3.0"]


def test_header_lists_both_ports_when_they_differ():
    lines = lr.header_lines("In A", "Out B", "Linux", "1.3.0")
    assert lines[:2] == ["MIDI input: In A", "MIDI output: Out B"]


def test_verdict_reports_the_first_failing_size():
    assert "All sizes tested passed" in lr.verdict(PASS_ALL)
    assert "around 512 bytes" in lr.verdict(SOME_FAIL)


def test_report_has_header_results_and_verdict():
    text = lr.report_text(SOME_FAIL, "X", "X", "Windows 11", "1.3.0")
    assert "MIDI interface: X" in text and "Windows 11" in text
    assert "  512 bytes: FAIL - no response" in text
    assert "starts failing around 512" in text


def test_issue_url_prefills_title_and_body():
    url = lr.issue_url(SOME_FAIL, "In A", "Out B", "macOS 15", "1.3.0")
    assert url.startswith(lr.GITHUB_ISSUES_URL)
    query = parse_qs(urlparse(url).query)
    assert query["title"] == ["MIDI interface test: In A / Out B"]
    assert "macOS 15" in query["body"][0] and "512 bytes: FAIL" in query["body"][0]


def test_describe_os_is_never_empty():
    assert lr.describe_os()


@pytest.fixture
def dialog(qapp_instance=None):
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from ui.loopback_results_dialog import LoopbackResultsDialog

    d = LoopbackResultsDialog(SOME_FAIL, "X", "X", None)
    yield d
    d.deleteLater()


def test_dialog_copy_puts_the_report_on_the_clipboard(dialog):
    from PySide6.QtWidgets import QApplication

    dialog.copy_button.click()
    assert QApplication.clipboard().text() == dialog.report()
    assert dialog.copy_button.text() == "Copied"


def test_dialog_share_opens_the_prefilled_issue(dialog, monkeypatch):
    import ui.loopback_results_dialog as mod

    opened = []
    monkeypatch.setattr(mod.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    dialog.share_button.click()
    assert len(opened) == 1 and opened[0].startswith(lr.GITHUB_ISSUES_URL)
