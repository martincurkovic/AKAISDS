# tests for ui/yamaha_assign_dialog.py - choosing a sample to assign (the assigning itself is tested with the session and the window).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from ui.yamaha_assign_dialog import AssignDialog

from test_s950_transfers import qapp  # noqa: F401


def row_texts(d, row):
    """(name, grey label) shown in a row."""
    labels = d.list.itemWidget(d.list.item(row)).findChildren(QLabel)
    return labels[0].text(), labels[1].text()


def names(d):
    return [d.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(d.list.count())]


def test_it_lists_the_candidates_in_order_with_their_durations_and_preselects_the_first(qapp):  # noqa: F811
    d = AssignDialog(["a", "b", "c"], "program 001", {"b": "0.45s"})
    assert names(d) == ["a", "b", "c"] and d.list.currentRow() == 0 and d.assign_button.isEnabled()
    assert row_texts(d, 1) == ("b", "0.45s") and row_texts(d, 0) == ("a", "")
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


def test_samples_this_app_sent_over_midi_get_a_grey_asterisk_and_a_one_line_note(qapp):  # noqa: F811
    d = AssignDialog(["factory", "mine"], "program 001", {"mine": "0.45s"}, midi_loaded={"mine"})
    assert row_texts(d, 0) == ("factory", "") and row_texts(d, 1) == ("mine", "0.45s *")
    assert d.list.itemWidget(d.list.item(1)).findChildren(QLabel)[1].objectName() == "mutedLabel"  # grey, themed by the stylesheet
    assert not d.midi_note.isHidden() and d.midi_note.text() == "* Sent with AKAISDS"
    d.list.setCurrentRow(1)
    d.assign_button.click()
    assert d.chosen == "mine"  # the mark is only display: the chosen NAME is still the plain one


def test_the_asterisk_alone_when_there_is_no_duration(qapp):  # noqa: F811
    d = AssignDialog(["mine"], "program 001", midi_loaded={"mine"})
    assert row_texts(d, 0) == ("mine", "*")


def test_without_such_samples_there_is_no_note(qapp):  # noqa: F811
    assert AssignDialog(["a"], "program 001").midi_note.isHidden()
    assert AssignDialog(["a"], "program 001", midi_loaded={"not offered"}).midi_note.isHidden()
