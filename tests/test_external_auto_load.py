# Settings > "Load automatically when the file is saved" (opt-in): once the external editor has saved and the file has stopped changing, the
# edited audio becomes the shown sample's working copy without the click - only the SHOWN sample, never while the window is busy.
# Also the demo timing switch (core/demo_timing.py).

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from core import app_config, demo_timing
from core import external_edit as ee
from core import sds_encoder
from test_program_editor_window import editor, qapp  # noqa: F401
from test_staged_edits import _ORIGINAL, staged_editor  # noqa: F401
from test_s950_transfers import wait_until
from test_yamaha_markers_ui import user_sample
from test_yamaha_program_editor import warning_acknowledged  # noqa: F401
from test_yamaha_waveform import window  # noqa: F401


def _pump(qapp, predicate, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        loop = QEventLoop()
        QTimer.singleShot(25, loop.quit)
        loop.exec()
    return predicate()


def _save(path, samples, rate=44100):
    sds_encoder.write_wav_file(path, samples, rate, bit_depth=16)
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


@pytest.fixture
def auto_setting(monkeypatch):
    state = {"on": True}
    monkeypatch.setattr(app_config, "get_saved_external_auto_load", lambda: state["on"])
    return state


@pytest.fixture
def launched(monkeypatch, tmp_path):
    from core import dropped_files

    monkeypatch.setattr(dropped_files, "_session_dir", tmp_path)
    opened = []
    monkeypatch.setattr(ee, "launch", lambda path, editor=None: opened.append(path))
    return opened


def _quick(tracker):
    tracker._debounce.setInterval(30)
    tracker._stable_timer.setInterval(30)


# --- the setting ----------------------------------------------------------------------------------------------------------


def test_the_setting_is_off_by_default_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "config.json")
    assert app_config.get_saved_external_auto_load() is False
    app_config.save_external_auto_load(True)
    assert app_config.get_saved_external_auto_load() is True
    app_config.save_external_auto_load(False)
    assert app_config.get_saved_external_auto_load() is False
    # only a real true counts: a hand-edited string does not turn it on
    app_config.save_config({"external_auto_load": "yes"})
    assert app_config.get_saved_external_auto_load() is False


def test_the_settings_dialog_has_the_checkbox_and_saves_it(qapp, tmp_path, monkeypatch):
    from test_settings_dialog import _FakeMidiManager, _FakeSamplerController
    from ui.settings_dialog import MidiSettingsDialog

    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "config.json")
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    assert dialog.check_external_auto_load.text() == "Load automatically when the file is saved"
    assert not dialog.check_external_auto_load.isChecked()  # opt-in
    dialog.check_external_auto_load.setChecked(True)
    dialog._apply_and_close()
    assert app_config.get_saved_external_auto_load() is True
    reopened = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    assert reopened.check_external_auto_load.isChecked()
    reopened.check_external_auto_load.setChecked(False)
    reopened._apply_and_close()
    assert app_config.get_saved_external_auto_load() is False


# --- the tracker: stable means "stopped changing" -------------------------------------------------------------------------


def test_a_saved_file_is_only_stable_once_it_has_stopped_changing(qapp, tmp_path, monkeypatch):
    from core import dropped_files
    from ui.external_edit_tracker import ExternalEditTracker

    monkeypatch.setattr(dropped_files, "_session_dir", tmp_path)
    monkeypatch.setattr(ee, "launch", lambda path, editor=None: None)
    tracker = ExternalEditTracker()
    _quick(tracker)
    try:
        tracker.open("K", "K", lambda path: _save(path, [1, 2, 3]))
        path = tracker.path("K")
        assert not tracker.is_ready("K") and not tracker.is_stable("K")
        _save(path, [9] * 50)
        assert _pump(qapp, lambda: tracker.is_stable("K"))
        # saved again: ready, but not stable until it settles
        _save(path, [8] * 80)
        assert tracker.is_ready("K") and not tracker.is_stable("K")
        assert _pump(qapp, lambda: tracker.is_stable("K"))
        tracker.mark_sent("K")
        assert not tracker.is_ready("K") and not tracker.is_stable("K")
    finally:
        tracker.close()


# --- the Akai editor ------------------------------------------------------------------------------------------------------


def test_akai_the_saved_file_loads_by_itself_when_the_setting_is_on(staged_editor, qapp, launched, auto_setting):
    editor = staged_editor
    _quick(editor._external_edit)
    editor.external_edit_button.click()
    _save(launched[0], [1000] * 100)
    assert _pump(qapp, lambda: "SQUARE" in editor._staged_edits)
    assert editor._staged_edits["SQUARE"].samples == [1000] * 100
    assert "automatically" in editor.status_bar.currentMessage() and "Undo" in editor.status_bar.currentMessage()
    assert editor._external_hint.isHidden()  # the round trip is over
    editor.undo_edits_button.click()  # ...and Undo goes back to what the sampler has
    assert editor._staged_edits == {} and editor._sample_waveform_cache[0]["samples"] == _ORIGINAL


def test_akai_nothing_loads_by_itself_when_the_setting_is_off(staged_editor, qapp, launched, auto_setting):
    auto_setting["on"] = False
    editor = staged_editor
    _quick(editor._external_edit)
    editor.external_edit_button.click()
    _save(launched[0], [1000] * 100)
    assert _pump(qapp, lambda: editor._external_edit.is_stable("SQUARE"))
    _pump(qapp, lambda: False, timeout=0.3)
    assert editor._staged_edits == {}
    assert editor.external_load_button.isEnabled() and editor.external_load_button.property("primary") is True  # waiting for the click


def test_akai_a_save_while_another_sample_is_selected_waits_for_it_to_be_selected(staged_editor, qapp, launched, auto_setting):
    editor = staged_editor
    editor._sample_waveform_cache[0].setdefault("ldwell1", 0)  # (the shared fixture's hand-made entry; real ones always have it)
    _quick(editor._external_edit)
    editor.external_edit_button.click()
    editor.sample_list_widget.setCurrentRow(1)
    qapp.processEvents()
    _save(launched[0], [1000] * 100)
    assert _pump(qapp, lambda: editor._external_edit.is_stable("SQUARE"))
    _pump(qapp, lambda: False, timeout=0.3)
    assert editor._staged_edits == {}  # not the shown sample: left alone
    editor.sample_list_widget.setCurrentRow(0)
    assert _pump(qapp, lambda: "SQUARE" in editor._staged_edits)  # back on it: loaded
    assert editor._staged_edits["SQUARE"].samples == [1000] * 100


def test_akai_nothing_loads_while_the_window_is_busy(staged_editor, qapp, launched, auto_setting):
    editor = staged_editor
    _quick(editor._external_edit)
    editor.external_edit_button.click()
    editor._hardware_busy_ui = True
    _save(launched[0], [1000] * 100)
    assert _pump(qapp, lambda: editor._external_edit.is_stable("SQUARE"))
    _pump(qapp, lambda: False, timeout=0.3)
    assert editor._staged_edits == {}
    editor._hardware_busy_ui = False
    editor._refresh_external_edit_buttons()  # what the end of the operation triggers
    assert _pump(qapp, lambda: "SQUARE" in editor._staged_edits)


# --- the Yamaha editor ----------------------------------------------------------------------------------------------------


def test_yamaha_the_saved_file_loads_by_itself_when_the_setting_is_on(window, qapp, launched, auto_setting):  # noqa: F811
    tab = user_sample(window, mode=4, end=3500)
    _quick(tab._edits._external)
    tab.external_edit_button.click()
    rate = sds_encoder.read_wav_info(launched[0])[1]
    _save(launched[0], [7] * 900, rate=rate)
    assert _pump(qapp, lambda: "UM" in tab._staged)
    assert tab._staged["UM"].channels[0] == [7] * 900
    tab.undo_edits_button.click()
    assert tab._staged == {}


def test_yamaha_nothing_loads_by_itself_when_the_setting_is_off(window, qapp, launched, auto_setting):  # noqa: F811
    auto_setting["on"] = False
    tab = user_sample(window, mode=4, end=3500)
    _quick(tab._edits._external)
    tab.external_edit_button.click()
    _save(launched[0], [7] * 900, rate=sds_encoder.read_wav_info(launched[0])[1])
    assert _pump(qapp, lambda: tab._edits._external.is_stable("UM"))
    _pump(qapp, lambda: False, timeout=0.3)
    assert tab._staged == {} and tab.external_load_button.isEnabled()


def test_yamaha_a_save_for_a_sample_that_is_not_shown_is_left_for_later(window, qapp, launched, auto_setting):  # noqa: F811
    tab = user_sample(window, mode=4, end=3500)
    _quick(tab._edits._external)
    tab.external_edit_button.click()
    other = next(n for n in tab.sample_names() if n != "UM")
    tab.select_sample(other)
    assert wait_until(lambda: tab._selected == other and not tab.cards_scroll.isHidden())
    _save(launched[0], [7] * 900, rate=sds_encoder.read_wav_info(launched[0])[1])
    assert _pump(qapp, lambda: tab._edits._external.is_stable("UM"))
    _pump(qapp, lambda: False, timeout=0.3)
    assert tab._staged == {}


# --- demo timing ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value, expected", [(None, False), ("", False), ("0", False), ("false", False), ("No", False), ("off", False), ("1", True), ("true", True), ("yes", True)])
def test_the_instant_switch_reads_the_environment_sensibly(monkeypatch, value, expected):
    monkeypatch.delenv(demo_timing.ENV, raising=False)
    if value is not None:
        monkeypatch.setenv(demo_timing.ENV, value)
    assert demo_timing.is_instant() is expected  # "0" used to count as on: any non-empty string is truthy in Python


def test_the_flag_or_the_variable_asks_for_instant(monkeypatch):
    monkeypatch.delenv(demo_timing.ENV, raising=False)
    assert demo_timing.requested(["tool.py"]) is False
    assert demo_timing.requested(["tool.py", "--editor", "--instant"]) is True
    monkeypatch.setenv(demo_timing.ENV, "1")
    assert demo_timing.requested(["tool.py"]) is True


def test_instant_removes_the_yamaha_wire_time_and_waits(window):  # noqa: F811
    from ui import yamaha_sample_edit

    saved = yamaha_sample_edit.LINK_SETTLE_MS
    try:
        window._session.send_baud, window._session.send_gap_ms = 31250, 400
        window.controller._yamaha_transfers.verify_delay_ms = 2500
        demo_timing.apply_to_yamaha(window.controller)
        assert window._session.send_baud >= 10**8 and window._session.send_gap_ms == 0
        assert window.controller._yamaha_transfers.verify_delay_ms == 0 and yamaha_sample_edit.LINK_SETTLE_MS == 0
    finally:
        yamaha_sample_edit.LINK_SETTLE_MS = saved


def test_instant_removes_the_s950_pacing():
    from controller.sampler_controller import SamplerController

    class _Midi:  # (what SamplerController needs to be built; nothing is sent)
        pass

    from core.midi_manager import MidiManager

    controller = SamplerController(MidiManager())
    engine = controller._s950
    assert engine.ack_interval_ms == 50 and engine.nak_window_ms == 500
    demo_timing.apply_to_s950(controller)
    assert engine.ack_interval_ms <= 1 and engine.nak_window_ms <= 20 and engine.drain_margin == 0.0


def test_the_program_editor_demo_replace_is_not_paced_when_instant(editor, qapp, monkeypatch):
    import ui.program_editor_window as pew

    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    monkeypatch.setenv(demo_timing.ENV, "1")
    monkeypatch.setattr(pew, "_DEMO_MS_PER_WORD", 50.0)  # 50 ms per word would take ~25 s for 500 words if it paced
    monkeypatch.setattr(pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.Yes)
    entry = {"samples": list(range(500)), "framerate": 44100, "frame_count": 500, "start": 0, "loop_start": 0, "loop_end": 499, "end": 499}
    editor.sample_list_widget.setCurrentRow(0)
    started = time.time()
    editor._perform_sample_edit_demo(0, entry, list(range(500)), 44100, 0, 0, 499, 499, "Send")
    assert time.time() - started < 2.0
