# The Samples tab's actions as buttons (Edit Sample card, Root Note & Tune), the Sample menu and the sample list's right-click menu:
# one QAction per action, shared by the menu and the list, enabled together with its button, only on the Samples tab and never while
# the window is busy. Plus the layout (Loop Preview and Root Note & Tune side by side).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint

from test_program_editor_window import (  # noqa: F401
    _pump_until,
    _stub_demo_audio,
    editor,
    qapp,
)

_EDIT_KEYS = ["trim", "reverse", "fade", "normalize", "filter", "stretch", "reverb"]


def _go_to_samples(editor, qapp):
    editor.main_tabs.setCurrentIndex(editor._samples_tab_index)
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())


def _load_audio(editor, monkeypatch, samples):
    import ui.program_editor_window as pew

    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    monkeypatch.setattr(pew, "_DEMO_MS_PER_WORD", 0.001)
    monkeypatch.setattr(pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.Yes)
    _stub_demo_audio(editor, monkeypatch, samples)
    editor._load_sample_waveform()


def _texts(actions):
    return [a.text() if not a.isSeparator() else "-" for a in actions]


def test_the_sample_menu_lists_every_action_in_groups(editor):
    menu = next(a.menu() for a in editor.menuBar().actions() if a.menu() is editor._sample_menu)
    assert _texts(menu.actions()) == [
        "Load Audio", "-",
        "Trim to Markers", "Reverse", "Fade In/Out", "Normalise", "Filter…", "Stretch / Pitch…", "Reverb…", "-",
        "Duplicate", "Slice Editor…", "-",
        "Edit in External App…", "Load Edited Audio", "Save as Akai File…", "-",
        "Undo", "Send to Sampler", "Send as New Sample…", "Revert", "-",
        "Detect Root Note", "-",
        "Rename Sample...", "Delete Sample...",
    ]  # fmt: skip
    # between File and Hardware
    titles = [a.text() for a in editor.menuBar().actions()]
    assert titles.index("&File") + 1 == titles.index("&Sample") < titles.index("&Hardware")


def test_the_right_click_menu_shares_the_menus_actions_above_rename_and_delete(editor):
    listed = editor.sample_list_widget.actions()
    assert _texts(listed)[0] == "Load Audio"
    assert _texts(listed)[-3:] == ["Rename Sample...", "-", "Delete Sample..."]
    for key in _EDIT_KEYS + ["duplicate", "slice", "external_edit", "external_load", "stage_undo", "stage_send", "stage_new", "stage_revert", "detect"]:
        assert editor._sample_edit_controls[key][1] in listed  # the very same QAction as the menu's


def test_actions_follow_their_buttons_but_only_on_the_samples_tab(editor, qapp, monkeypatch):
    _go_to_samples(editor, qapp)
    assert not any(a.isEnabled() for _b, a in editor._sample_edit_controls.values())  # no audio yet
    _load_audio(editor, monkeypatch, list(range(500)))
    for key in _EDIT_KEYS + ["slice", "external_edit", "detect"]:
        button, action = editor._sample_edit_controls[key]
        assert button.isEnabled() and action.isEnabled(), key
    # Duplicate is off in demo mode, Load waits for a save and the working-copy controls need unsent edits: button and action agree
    for key in ("duplicate", "external_load", "stage_undo", "stage_send", "stage_new", "stage_revert"):
        button, action = editor._sample_edit_controls[key]
        assert not button.isEnabled() and not action.isEnabled(), key
    # a destructive menu item must not act on a sample the user can't see
    editor.main_tabs.setCurrentIndex(1)
    assert not any(a.isEnabled() for _b, a in editor._sample_edit_controls.values())
    assert editor._sample_edit_controls["reverse"][0].isEnabled()  # (the button just sits on a hidden tab)
    editor.main_tabs.setCurrentIndex(editor._samples_tab_index)
    assert editor._sample_edit_controls["reverse"][1].isEnabled()


def test_load_audio_is_offered_until_the_audio_is_loaded(editor, qapp, monkeypatch):
    editor.main_tabs.setCurrentIndex(1)
    assert not editor._load_audio_action.isEnabled()  # not on the Samples tab
    _go_to_samples(editor, qapp)
    assert editor._load_audio_action.isEnabled()
    _load_audio(editor, monkeypatch, list(range(500)))
    assert not editor._load_audio_action.isEnabled() and editor.waveform_view.has_waveform()
    editor.sample_list_widget.setCurrentRow(-1)
    assert not editor._load_audio_action.isEnabled()  # nothing selected


def test_load_audio_action_loads_the_audio_like_a_double_click(editor, qapp, monkeypatch):
    import ui.program_editor_window as pew

    _go_to_samples(editor, qapp)
    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    monkeypatch.setattr(pew, "_DEMO_MS_PER_WORD", 0.001)
    _stub_demo_audio(editor, monkeypatch, list(range(300)))
    editor._load_audio_action.trigger()
    assert editor.waveform_view.has_waveform()
    assert editor._sample_waveform_cache[0]["frame_count"] == 300


def test_triggering_the_menu_action_does_what_the_button_does(editor, qapp, monkeypatch):
    _go_to_samples(editor, qapp)
    _load_audio(editor, monkeypatch, list(range(100)))
    editor._sample_edit_controls["reverse"][1].trigger()
    assert editor._sample_waveform_cache[0]["samples"] == list(reversed(range(100)))


def test_the_sample_menu_is_disabled_while_the_window_is_busy_even_when_the_timer_ticks(editor):
    assert editor._sample_menu.isEnabled()
    editor._set_hardware_busy_ui(True)
    editor._sync_window_menu_busy_state()  # the 150 ms timer: no transfer running, but this window is busy
    assert not editor._sample_menu.isEnabled()
    editor._set_hardware_busy_ui(False)
    editor._sync_window_menu_busy_state()
    assert editor._sample_menu.isEnabled()


def test_rename_and_delete_in_the_menu_need_a_selection_on_the_samples_tab(editor, qapp):
    editor.main_tabs.setCurrentIndex(1)
    assert not editor._menu_rename_sample_action.isEnabled()
    _go_to_samples(editor, qapp)
    assert editor._menu_rename_sample_action.isEnabled() and editor._menu_delete_sample_action.isEnabled()
    editor.sample_list_widget.setCurrentRow(-1)
    assert not editor._menu_rename_sample_action.isEnabled()


# --- layout ---------------------------------------------------------------------------------------------------------


def test_loop_preview_and_root_note_cards_share_a_row_with_the_tune_controls_stacked(editor, qapp):
    editor.resize(1250, 900)
    editor.show()
    editor.main_tabs.setCurrentIndex(editor._samples_tab_index)
    qapp.processEvents()
    try:
        origin = lambda w: w.mapTo(editor, QPoint(0, 0))  # noqa: E731
        preview, root, tune, detect = (
            origin(editor.loop_preview),
            origin(editor.sample_root_note_spinbox),
            origin(editor.sample_tune_spinbox),
            origin(editor.detect_root_note_button),
        )
        # side by side: the Root Note card starts to the right of the Loop Preview's end
        assert root.x() > preview.x() + editor.loop_preview.width()
        # Tune is stacked under Root Note; Detect Root Note sits beside Root Note
        assert tune.y() > root.y() and abs(tune.x() - root.x()) <= 1 and abs(detect.y() - root.y()) <= 3
        # the Edit Sample card sits between Loop Controls and the pair: Trim above Loop Preview
        assert origin(editor.trim_sample_button).y() < preview.y()
    finally:
        editor.hide()
