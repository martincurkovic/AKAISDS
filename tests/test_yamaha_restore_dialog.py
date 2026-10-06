# tests for ui/yamaha_restore_dialog.py - choosing a backup (reading/writing is tested with the job and the window).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt

from core import yamaha_backups as yb
from ui import yamaha_restore_dialog as dialog_module
from ui.yamaha_restore_dialog import RestoreDialog

from test_s950_transfers import qapp  # noqa: F401


def entries():
    return [
        yb.BackupEntry("/b/PG-001-3.syx", "PG", "001", 3000),
        yb.BackupEntry("/b/SP-pulse_3-2.syx", "SP", "pulse 3", 2000),
        yb.BackupEntry("/b/PG-001-1.syx", "PG", "001", 1000),
        yb.BackupEntry("/b/PG-002-0.syx", "PG", "002", 500),
    ]


def rows(d):
    return [d.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(d.list.count())]


def test_it_offers_only_the_selected_objects_backups_by_default_and_all_on_request(qapp):  # noqa: F811
    d = RestoreDialog(entries(), ("PG", "001"), "/b")
    assert rows(d) == ["/b/PG-001-3.syx", "/b/PG-001-1.syx"] and d.list.currentRow() == 0
    d.only_current.setChecked(False)
    assert len(rows(d)) == 4


def test_with_no_selection_it_lists_everything_and_hides_the_filter(qapp):  # noqa: F811
    d = RestoreDialog(entries(), None, "/b")
    assert len(rows(d)) == 4 and d.only_current.isHidden()


def test_choosing_returns_the_path_and_double_click_is_the_same(qapp):  # noqa: F811
    d = RestoreDialog(entries(), ("PG", "001"), "/b")
    d.list.setCurrentRow(1)
    d.restore_button.click()
    assert d.chosen_path == "/b/PG-001-1.syx"
    d2 = RestoreDialog(entries(), None, "/b")
    d2.list.setCurrentRow(1)  # a double click selects the row first, then fires
    d2.list.itemDoubleClicked.emit(d2.list.item(1))
    assert d2.chosen_path == "/b/SP-pulse_3-2.syx"


def test_with_nothing_to_restore_it_says_why_and_cannot_be_accepted(qapp):  # noqa: F811
    d = RestoreDialog(entries(), ("PG", "099"), "/some/folder")
    assert rows(d) == [] and not d.restore_button.isEnabled()
    assert "No backups in /some/folder" in d.empty.text()
    d._accept_current()
    assert d.chosen_path is None


def test_browse_picks_a_file_from_elsewhere(qapp, monkeypatch):  # noqa: F811
    monkeypatch.setattr(dialog_module.QFileDialog, "getOpenFileName", lambda *a, **k: ("/elsewhere/mine.syx", ""))
    d = RestoreDialog([], None, "/b")
    d.browse_button.click()
    assert d.chosen_path == "/elsewhere/mine.syx"
    monkeypatch.setattr(dialog_module.QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    d2 = RestoreDialog([], None, "/b")
    d2.browse_button.click()
    assert d2.chosen_path is None
