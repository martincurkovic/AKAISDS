# A button that disables ITSELF when clicked (Load Edited Audio waits for the next save; Undo/Revert go off when the working copy is gone)
# hands its keyboard focus to the next widget in the tab order, and the scroll area scrolls to reveal it - the page jumped. The controls
# must give up focus BEFORE they are disabled (AGENTS.md: "clear focus BEFORE disabling").

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from core import external_edit as ee
from core import sds_encoder
from test_program_editor_window import _pump_until, editor, qapp  # noqa: F401
from test_staged_edits import _stage, staged_editor  # noqa: F401
from test_s950_transfers import wait_until
from test_yamaha_markers_ui import user_sample
from test_yamaha_program_editor import warning_acknowledged  # noqa: F401
from test_yamaha_waveform import window  # noqa: F401


def _saved(path, samples, rate=44100):
    sds_encoder.write_wav_file(path, samples, rate, bit_depth=16)
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


def _click_with_focus(button):
    button.setFocus()
    QApplication.processEvents()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    QApplication.processEvents()


# --- Yamaha ---------------------------------------------------------------------------------------------------------------


@pytest.fixture
def yamaha_launched(monkeypatch, tmp_path):
    from core import dropped_files

    monkeypatch.setattr(dropped_files, "_session_dir", tmp_path)
    opened = []
    monkeypatch.setattr(ee, "launch", lambda path, editor=None: opened.append(path))
    return opened


def _yamaha_tall(window):  # noqa: F811
    tab = user_sample(window, mode=1, end=3500, loop=(1500, 2500))
    window.resize(1250, 800)
    window.show()
    window.main_tabs.setCurrentIndex(window._samples_tab_index)
    QApplication.processEvents()
    bar = tab.cards_scroll.verticalScrollBar()
    assert bar.maximum() > 0, "the page must be taller than the window for this test to mean anything"
    return tab, bar


def test_yamaha_load_edited_audio_does_not_scroll_the_page(window, yamaha_launched):  # noqa: F811
    tab, bar = _yamaha_tall(window)
    try:
        tab.external_edit_button.click()
        _saved(yamaha_launched[0], [3] * 2000, rate=sds_encoder.read_wav_info(yamaha_launched[0])[1])
        tab._edits._external.changed.emit()
        assert tab.external_load_button.isEnabled()
        bar.setValue(0)
        _click_with_focus(tab.external_load_button)
        assert "UM" in tab._staged and not tab.external_load_button.isEnabled()  # it did load, and went off
        assert bar.value() == 0
    finally:
        tab._staged.clear()
        window.hide()


def test_yamaha_the_working_copy_buttons_do_not_scroll_the_page_when_they_switch_themselves_off(window):  # noqa: F811
    tab, bar = _yamaha_tall(window)
    try:
        for name in ("undo_edits_button", "revert_edits_button"):
            tab.edit_buttons["reverse"].click()
            bar.setValue(0)
            button = getattr(tab, name)
            if name == "revert_edits_button":
                from PySide6.QtWidgets import QMessageBox

                QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
            _click_with_focus(button)
            assert tab._staged == {} and bar.value() == 0, name
    finally:
        tab._staged.clear()
        window.hide()


# --- Akai -----------------------------------------------------------------------------------------------------------------


def _akai_tall(editor, qapp):  # noqa: F811
    editor.resize(1250, 500)
    editor.show()
    editor.main_tabs.setCurrentIndex(editor._samples_tab_index)
    qapp.processEvents()
    scroll = editor.main_tabs.currentWidget().findChildren(QScrollArea)[0]
    bar = scroll.verticalScrollBar()
    assert bar.maximum() > 0, "the page must be taller than the window for this test to mean anything"
    return bar


def test_akai_load_edited_audio_does_not_scroll_the_page(staged_editor, qapp, monkeypatch, tmp_path):  # noqa: F811
    from core import dropped_files

    editor = staged_editor
    monkeypatch.setattr(dropped_files, "_session_dir", tmp_path)
    opened = []
    monkeypatch.setattr(ee, "launch", lambda path, editor=None: opened.append(path))
    bar = _akai_tall(editor, qapp)
    try:
        editor.external_edit_button.click()
        _saved(opened[0], [1000] * 100)
        editor._refresh_external_edit_buttons()
        assert editor.external_load_button.isEnabled()
        bar.setValue(0)
        _click_with_focus(editor.external_load_button)
        assert "SQUARE" in editor._staged_edits and bar.value() == 0
    finally:
        editor._staged_edits.clear()
        editor.hide()


def test_akai_undo_and_revert_do_not_scroll_the_page(staged_editor, qapp, monkeypatch):  # noqa: F811
    import ui.program_editor_window as pew

    editor = staged_editor
    monkeypatch.setattr(pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.Yes)
    bar = _akai_tall(editor, qapp)
    try:
        for button in (editor.undo_edits_button, editor.revert_edits_button):
            _stage(editor, [5] * 100)
            bar.setValue(0)
            _click_with_focus(button)
            assert editor._staged_edits == {} and bar.value() == 0
    finally:
        editor._staged_edits.clear()
        editor.hide()


# --- Settings > Troubleshooting ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("button_name, method", [("btn_run_id_request", "_run_identity_request"), ("btn_loopback_test", "_run_loopback_test")])
def test_the_troubleshooting_tests_do_not_scroll_the_settings_page(qapp, monkeypatch, button_name, method):
    # Run Hardware Test / Run Loopback Test turn themselves off while they run: with the focus still on them it moved on to a widget
    # further down the page and the scroll area followed it
    from PySide6.QtWidgets import QMessageBox, QTabWidget

    from test_settings_dialog import _FakeMidiManager, _FakeSamplerController
    from ui import settings_dialog
    from ui.settings_dialog import MidiSettingsDialog

    def refuse(*a, **k):
        raise OSError("no such port")  # the test stops at once; what matters is the moment the button turns itself off

    monkeypatch.setattr(settings_dialog.mido, "open_input", refuse)
    monkeypatch.setattr(settings_dialog.mido, "open_output", refuse)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)  # (never a modal that would block the test)
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    dialog.combo_input.setCurrentIndex(dialog.combo_input.findData("In A"))
    dialog.combo_output.setCurrentIndex(dialog.combo_output.findData("Out A"))
    assert dialog.combo_input.currentData() and dialog.combo_output.currentData()
    dialog.resize(dialog.width(), 380)
    dialog.show()
    tabs = dialog.findChild(QTabWidget)
    tabs.setCurrentIndex(1)
    QApplication.processEvents()
    try:
        bar = tabs.currentWidget().verticalScrollBar()
        assert bar.maximum() > 0, "the page must be taller than the dialog for this test to mean anything"
        bar.setValue(0)
        button = getattr(dialog, button_name)
        _click_with_focus(button)
        assert bar.value() == 0
        assert button.isEnabled()  # (and it is back on afterwards)
    finally:
        dialog.close()
