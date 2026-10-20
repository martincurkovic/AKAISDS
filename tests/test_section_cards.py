# Collapsible section cards (ui/section_card.py): the disclosure arrow, pairs that collapse together, remembered state, and the editors' use of them.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from collections import Counter

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from core import app_config
from test_program_editor_window import _select_sample_with_full_audio, editor, qapp  # noqa: F401
from test_s950_program_editor import win as s950_win  # noqa: F401
from test_yamaha_markers_ui import user_sample
from test_yamaha_program_editor import warning_acknowledged  # noqa: F401
from test_yamaha_waveform import window  # noqa: F401
from ui import section_card
from ui.editor_layout import equalize_card_heights
from ui.qt_helpers import build_editor_card, build_section_card
from ui.section_card import SectionCard


@pytest.fixture(autouse=True)
def _own_config(monkeypatch, tmp_path):
    # collapsing writes config.json: never the session's shared test file
    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "config.json")
    section_card.reset_state()


def _rows(text="a body row"):
    row = QHBoxLayout()
    row.addWidget(QLabel(text))
    return row


def _card(title="Filter", scope="test", *rows, **kwargs):
    return build_editor_card(scope, title, *(rows or (_rows(),)), **kwargs)


def _saved():
    path = app_config.CONFIG_PATH
    return json.loads(path.read_text()).get("collapsed_cards", []) if path.exists() else []


# --- one card -----------------------------------------------------------------------------------------------------------------


def test_a_card_starts_open_and_the_arrow_toggles_it(qapp):
    card = _card()
    assert isinstance(card, SectionCard) and not card.is_collapsed()
    open_height = card.sizeHint().height()
    card.arrow().click()
    assert card.is_collapsed() and card._body.isHidden()
    assert card.sizeHint().height() < open_height  # only the title row is left
    card.arrow().click()
    assert not card.is_collapsed() and not card._body.isHidden() and card.sizeHint().height() == open_height


def test_clicking_the_title_toggles_too_but_the_empty_header_does_not(qapp):
    card = _card()
    card.resize(400, 80)
    card.show()
    qapp.processEvents()
    try:
        title = card._title_label
        QTest.mouseClick(title, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(3, 3))
        assert card.is_collapsed()
        QTest.mouseClick(card, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(card.width() - 10, 18))
        assert card.is_collapsed()  # a click in the dead space to the right changed nothing
        QTest.mouseClick(title, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(3, 3))
        assert not card.is_collapsed()
    finally:
        card.hide()


def test_the_arrow_works_from_the_keyboard_and_is_tab_focus_only(qapp):
    card = _card()
    arrow = card.arrow()
    assert arrow.focusPolicy() == Qt.FocusPolicy.TabFocus  # a click must not take focus
    QTest.keyClick(arrow, Qt.Key.Key_Space)
    assert card.is_collapsed()
    QTest.keyClick(arrow, Qt.Key.Key_Return)
    assert not card.is_collapsed()
    assert "Collapse Filter" in arrow.toolTip()
    card.toggle()
    assert "Expand Filter" in arrow.toolTip()
    assert not arrow.grab().isNull()  # both triangles paint


def test_the_small_controls_beside_a_title_hide_with_the_body(qapp):
    zoom = QPushButton("1:1")
    card = _card("Loop Preview", "test", _rows(), header_widgets=(zoom,))
    card.show()
    qapp.processEvents()
    try:
        assert zoom.isVisible()
        card.set_collapsed(True)
        assert not zoom.isVisible()
        card.set_collapsed(False)
        assert zoom.isVisible()
    finally:
        card.hide()


def test_collapsing_gives_up_the_focus_inside_the_body(qapp):
    # a hidden focused widget hands its focus on and a scroll area scrolls to it
    page = QWidget()
    layout = QVBoxLayout(page)
    box = QLineEdit()
    row = QHBoxLayout()
    row.addWidget(box)
    card = _card("Filter", "test", row)
    layout.addWidget(card)
    layout.addWidget(QLineEdit())
    page.show()
    box.setFocus()
    qapp.processEvents()
    try:
        assert box.hasFocus()
        card.set_collapsed(True)
        assert not box.hasFocus() and QApplication.focusWidget() is not box
    finally:
        page.hide()


def test_a_plain_card_is_still_what_the_settings_dialog_and_dashboard_get(qapp):
    plain = build_section_card("Audio", _rows())
    assert not isinstance(plain, SectionCard) and plain.objectName() == "sectionCard"
    assert not isinstance(_card(collapsible=False), SectionCard)


# --- remembered ----------------------------------------------------------------------------------------------------------------


def test_the_state_is_remembered_per_editor_and_title(qapp):
    _card("Filter", "program_editor").set_collapsed(True)
    assert _saved() == ["program_editor/Filter"]
    section_card.reset_state()  # a new session
    assert _card("Filter", "program_editor").is_collapsed()
    assert not _card("Filter", "yamaha_sample").is_collapsed()  # same title, another editor
    assert not _card("LFO", "program_editor").is_collapsed()
    reopened = _card("Filter", "program_editor")
    reopened.set_collapsed(False)
    assert _saved() == []


def test_a_change_the_user_did_not_ask_for_is_not_remembered(qapp):
    card = _card()
    card.set_collapsed(True, remember=False)
    assert _saved() == []
    assert not build_editor_card("test", "Filter", _rows()).is_collapsed()  # a new card with the key reads the saved state: nothing was saved


def test_a_damaged_config_entry_reads_as_nothing_collapsed(qapp):
    app_config.CONFIG_PATH.write_text(json.dumps({"collapsed_cards": "Filter"}))
    assert app_config.get_collapsed_cards() == []
    app_config.CONFIG_PATH.write_text(json.dumps({"collapsed_cards": ["a/b", 5, None]}))
    assert app_config.get_collapsed_cards() == ["a/b"]


# --- pairs --------------------------------------------------------------------------------------------------------------------------


def _pair(qapp):
    tall = QVBoxLayout()
    for i in range(6):
        tall.addWidget(QLabel(f"row {i}"))
    left, right = _card("Loop Preview", "test", _rows()), _card("Root Note & Tune", "test", tall)
    equalize_card_heights(left, right)
    return left, right


def test_a_pair_collapses_and_expands_together_at_one_height(qapp):
    left, right = _pair(qapp)
    assert left.maximumHeight() == right.maximumHeight() > 0
    open_height = left.maximumHeight()
    left.arrow().click()
    assert left.is_collapsed() and right.is_collapsed()  # one click, both
    assert left.maximumHeight() == right.maximumHeight() < open_height  # still one height, now just the title row
    right.arrow().click()  # either one opens them again
    assert not left.is_collapsed() and not right.is_collapsed()
    assert left.maximumHeight() == right.maximumHeight() == open_height  # the tallest again, not whatever the collapsed measure was


def test_a_pair_restored_collapsed_is_pinned_to_the_collapsed_height(qapp):
    _pair(qapp)[0].set_collapsed(True)
    section_card.reset_state()
    left, right = _pair(qapp)
    assert left.is_collapsed() and right.is_collapsed() and left.maximumHeight() == right.maximumHeight()
    left.set_collapsed(False)
    assert left.maximumHeight() > 60  # and it expands to the full height from there


def test_a_card_that_asks_for_attention_shows_a_dot_only_while_collapsed(qapp):
    card = _card()
    card.set_attention(True, "unsent")
    assert card._attention_dot.isHidden()
    card.set_collapsed(True)
    assert not card._attention_dot.isHidden() and card._attention_dot.toolTip() == "unsent"
    card.set_collapsed(False)
    assert card._attention_dot.isHidden()
    card.set_collapsed(True)
    card.set_attention(False)
    assert card._attention_dot.isHidden()


# --- the editors ------------------------------------------------------------------------------------------------------------------------


def _titles(widget):
    return [c.title() for c in widget.findChildren(SectionCard)]


def _assert_unique_keys(widget):
    keys = Counter(c.key() for c in widget.findChildren(SectionCard))
    assert keys, "no collapsible cards found"
    assert [k for k, n in keys.items() if n > 1] == []  # two cards with a key would collapse together and be remembered as one


def test_the_akai_editor_has_unique_keys_and_keeps_the_waveform_open(editor):
    _assert_unique_keys(editor)
    titles = _titles(editor)
    for expected in ("Edit Sample", "Loop Preview", "Root Note & Tune", "Filter", "LFO1", "Modulation", "Memory & OS", "SCSI"):
        assert expected in titles, expected
    assert "Loop Controls" not in titles  # the waveform card has no arrow
    assert all(key.startswith("program_editor/") for key in (c.key() for c in editor.findChildren(SectionCard)))


def test_the_akai_samples_cards_collapse_in_pairs(editor):
    cards = {c.title(): c for c in editor.findChildren(SectionCard)}
    cards["Loop Preview"].arrow().click()
    assert cards["Root Note & Tune"].is_collapsed()
    assert not cards["Edit Sample"].is_collapsed()  # a lone card on its own
    cards["Loop Preview"].arrow().click()


def test_a_collapsed_edit_sample_card_flags_unsent_edits_and_opens_when_they_appear(editor, qapp, monkeypatch):
    import ui.program_editor_window as pew

    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    _select_sample_with_full_audio(editor, qapp, 0, [int(8000 * ((i % 20) / 10 - 1)) for i in range(100)], {"start": 10, "loop_start": 30, "loop_end": 60, "end": 90})
    card = editor._edit_sample_card
    card.set_collapsed(True)
    assert card._attention_dot.isHidden()
    assert editor._stage_working_copy(0, [5] * 100, {"start": 10, "loop_start": 30, "loop_end": 60, "end": 90})
    assert not card.is_collapsed()  # opened by itself...
    assert _saved() == ["program_editor/Edit Sample"]  # ...without forgetting the user's choice
    card.set_collapsed(True)  # the user closes it again while editing: no fight on later refreshes
    editor._refresh_staged_controls()
    editor._refresh_staged_controls()
    assert card.is_collapsed() and not card._attention_dot.isHidden()
    editor._discard_staged("SQUARE", restore_entry=True)
    assert card._attention_dot.isHidden()


def test_the_yamaha_editor_has_unique_keys_per_tab(window):  # noqa: F811
    _assert_unique_keys(window)
    keys = {c.key() for c in window.findChildren(SectionCard)}
    assert any(k.startswith("yamaha_program/") for k in keys) and any(k.startswith("yamaha_sample/") for k in keys)
    assert "Loop Controls" not in _titles(window)


def test_a_collapsed_yamaha_edit_sample_card_flags_unsent_edits(window):  # noqa: F811
    tab = user_sample(window, mode=4, end=3500)
    card = tab._edit_sample_card
    card.set_collapsed(True)
    tab.edit_buttons["reverse"].click()
    assert not card.is_collapsed()  # the edit made a working copy: the card opened
    card.set_collapsed(True)
    tab.update_edit_buttons()
    assert card.is_collapsed() and not card._attention_dot.isHidden()


def test_the_s950_editor_has_unique_keys(s950_win):  # noqa: F811
    _assert_unique_keys(s950_win)
    assert all(c.key().startswith("s950_editor/") for c in s950_win.findChildren(SectionCard))
