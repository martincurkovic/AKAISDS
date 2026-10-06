# tests for ui/yamaha_assign_dialog.py - choosing a sample to assign (the assigning itself is tested with the session and the window).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt

from ui.yamaha_assign_dialog import AssignDialog

from test_s950_transfers import qapp  # noqa: F401


def names(d):
    return [d.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(d.list.count())]


def test_it_lists_the_candidates_in_order_with_their_durations_and_preselects_the_first(qapp):  # noqa: F811
    d = AssignDialog(["a", "b", "c"], "program 001", {"b": "0.45s"})
    assert names(d) == ["a", "b", "c"] and d.list.currentRow() == 0 and d.assign_button.isEnabled()
    assert "0.45s" in d.list.item(1).text() and d.list.item(0).text() == "a"
    assert "program 001" in d.findChild(type(d.empty)).text()  # the explanation names the program


def test_choosing_returns_the_name_not_the_display_text(qapp):  # noqa: F811
    d = AssignDialog(["a", "b"], "program 001", {"b": "0.45s"})
    d.list.setCurrentRow(1)
    d.assign_button.click()
    assert d.chosen == "b"


def test_double_clicking_chooses_too(qapp):  # noqa: F811
    d = AssignDialog(["a", "b"], "program 001")
    d.list.setCurrentRow(1)
    d.list.itemDoubleClicked.emit(d.list.item(1))
    assert d.chosen == "b"


def test_with_nothing_left_to_assign_it_says_so_and_cannot_be_accepted(qapp):  # noqa: F811
    d = AssignDialog([], "program 001")
    assert not d.assign_button.isEnabled() and not d.empty.isHidden() and "already in this program" in d.empty.text()
    d._accept_current()
    assert d.chosen is None
