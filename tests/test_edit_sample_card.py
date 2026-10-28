# The Edit Sample card (Program Editor): a working-copy strip that is ALWAYS there (so the card never changes height), the
# primary button styling, the inline external-app hint, the two body rows aligned on one column, and no horizontal scroll bar.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QScrollArea

from test_program_editor_window import _pump_until, _stub_demo_audio, editor, qapp  # noqa: F401
from test_staged_edits import _ORIGINAL, _stage, staged_editor  # noqa: F401


def _unsent(editor):
    return bool(editor._staged_banner.property("unsent"))


def test_the_strip_is_always_there_and_only_its_state_changes(staged_editor):
    editor = staged_editor
    strip = editor._staged_banner
    assert not strip.isHidden() and not _unsent(editor)
    assert editor._working_copy_label.text() == "Original from the sampler" and editor._working_copy_dot.isHidden()
    for button in (editor.undo_edits_button, editor.send_edits_button, editor.send_edits_as_new_button, editor.revert_edits_button):
        assert not button.isEnabled()  # there, but off
    _stage(editor, [5] * 100)
    assert not strip.isHidden() and _unsent(editor)
    assert editor._working_copy_label.text() == "Edited – not on the sampler yet" and not editor._working_copy_dot.isHidden()
    assert editor.send_edits_button.isEnabled() and editor.revert_edits_button.isEnabled()
    editor.undo_edits_button.click()
    assert not _unsent(editor) and editor._working_copy_label.text() == "Original from the sampler"


def test_the_card_does_not_change_height_when_a_sample_gets_unsent_edits_or_waits_for_an_external_app(staged_editor, qapp):
    editor = staged_editor
    editor.resize(1250, 900)
    editor.show()
    editor.main_tabs.setCurrentIndex(editor._samples_tab_index)
    qapp.processEvents()
    try:
        origin = lambda w: w.mapTo(editor, QPoint(0, 0)).y()  # noqa: E731
        before = origin(editor.loop_preview)
        _stage(editor, [5] * 100)
        qapp.processEvents()
        assert origin(editor.loop_preview) == before  # the cards below did not move
        editor._external_waiting["SQUARE"] = "iZotope RX 10 Audio Editor"
        editor._refresh_external_edit_buttons()
        qapp.processEvents()
        assert not editor._external_hint.isHidden() and origin(editor.loop_preview) == before
    finally:
        editor._staged_edits.clear()
        editor.hide()


def test_send_to_sampler_is_the_primary_button_and_load_edited_audio_becomes_one_once_the_file_is_saved(staged_editor, monkeypatch):
    editor = staged_editor
    assert editor.send_edits_button.property("primary") is True
    assert not editor.external_load_button.property("primary")
    monkeypatch.setattr(editor._external_edit, "is_ready", lambda name: True)
    editor._refresh_external_edit_buttons()
    assert editor.external_load_button.isEnabled() and editor.external_load_button.property("primary") is True
    monkeypatch.setattr(editor._external_edit, "is_ready", lambda name: False)
    editor._refresh_external_edit_buttons()
    assert not editor.external_load_button.property("primary")


def test_the_external_hint_follows_the_round_trip(staged_editor, monkeypatch, tmp_path):
    from core import dropped_files, external_edit as ee, sds_encoder

    editor = staged_editor
    assert editor._external_hint.isHidden()
    monkeypatch.setattr(dropped_files, "_session_dir", tmp_path)
    opened = []
    monkeypatch.setattr(ee, "launch", lambda path, editor=None: opened.append(path))
    from core import app_config

    monkeypatch.setattr(app_config, "get_saved_external_editor", lambda: "/Applications/iZotope RX 10 Audio Editor.app/")
    editor.external_edit_button.click()
    assert not editor._external_hint.isHidden()
    assert editor._external_hint.text() == "Opened in iZotope RX 10 Audio Editor"
    # saved: ready to load
    sds_encoder.write_wav_file(opened[0], [1000] * 100, 44100, bit_depth=16)
    stat = os.stat(opened[0])
    os.utime(opened[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    editor._refresh_external_edit_buttons()
    assert editor._external_hint.text() == "Saved – ready to load" and editor.external_load_button.property("primary") is True
    # loaded: the round trip is over
    editor.external_load_button.click()
    assert editor._external_hint.isHidden() and not editor.external_load_button.property("primary")


def test_the_hint_for_a_default_app_and_a_long_name_never_widens_the_page(staged_editor, qapp, monkeypatch):
    from core import app_config

    editor = staged_editor
    monkeypatch.setattr(app_config, "get_saved_external_editor", lambda: "")
    editor._external_waiting["SQUARE"] = "your default app"
    editor.resize(1250, 900)
    editor.show()
    editor.main_tabs.setCurrentIndex(editor._samples_tab_index)
    qapp.processEvents()
    try:
        scroll = editor.main_tabs.currentWidget().findChildren(QScrollArea)[0]
        editor._external_waiting["SQUARE"] = "A Really Quite Remarkably Long Audio Editor Application Name Pro Edition"
        editor._refresh_external_edit_buttons()
        qapp.processEvents()
        assert scroll.horizontalScrollBar().maximum() == 0
    finally:
        editor.hide()


def test_both_body_rows_start_their_buttons_at_the_same_x_and_the_groups_are_divided(staged_editor, qapp):
    editor = staged_editor
    editor.resize(1250, 900)
    editor.show()
    editor.main_tabs.setCurrentIndex(editor._samples_tab_index)
    qapp.processEvents()
    try:
        x = lambda w: w.mapTo(editor, QPoint(0, 0)).x()  # noqa: E731
        assert x(editor.trim_sample_button) == x(editor.duplicate_sample_button)
        # a rule between the groups of the second row: after Slice Editor, before Edit in External App; and after Load Edited Audio, before Save as Akai File
        dividers = sorted(
            (w for w in editor.findChildren(type(editor._staged_banner)) if w.objectName() == "groupDivider"), key=x
        )
        assert len(dividers) == 2
        assert x(editor.slice_editor_button) < x(dividers[0]) < x(editor.external_edit_button)
        assert x(editor.external_load_button) < x(dividers[1]) < x(editor.save_akai_button)
    finally:
        editor.hide()


def test_the_unsent_dot_is_a_widget_in_the_list_row_next_to_the_name(staged_editor):
    editor = staged_editor
    assert editor._sample_row_dots[0].isHidden()
    _stage(editor, [5] * 100)
    assert not editor._sample_row_dots[0].isHidden() and editor._sample_row_dots[0].objectName() == "unsentDot"
    assert editor._sample_row_labels[0][1].text() == "SQUARE"  # the name is untouched: no suffix glued on


def test_the_warning_tint_exists_in_both_palettes_and_the_style_sheet_uses_it():
    from ui import theme

    assert theme.DARK_PALETTE["warning_tint"].startswith("#") and theme.LIGHT_PALETTE["warning_tint"].startswith("#")
    template = open(os.path.join(os.path.dirname(theme.__file__), "style.qss.template"), encoding="utf-8").read()
    for rule in ('QFrame#workingCopyStrip[unsent="true"]', 'QPushButton[primary="true"]:enabled', "QLabel#unsentDot", "QFrame#groupDivider"):
        assert rule in template


def test_the_external_hint_is_given_room_to_show(staged_editor, qapp):
    editor = staged_editor
    editor.resize(1250, 900)
    editor.show()
    editor.main_tabs.setCurrentIndex(editor._samples_tab_index)
    editor._external_waiting["SQUARE"] = "iZotope RX 10 Audio Editor"
    editor._refresh_external_edit_buttons()
    qapp.processEvents()
    try:
        assert editor._external_hint.width() > 100  # it had 0 once: an Ignored-policy label beside a trailing stretch
    finally:
        editor.hide()
