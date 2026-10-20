# The sampler's memory + OS version (RSTAT) in the Program Editor: the bar under the Samples tab's list and the Global tab's "Memory & OS" card
# (core/program_editor_bridge.py's memory_status job, ui/memory_bar.py).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import s3k.messages as m
from s3k.bridge import DeviceStatus
from PySide6.QtWidgets import QWidget

from core.program_editor_bridge import BridgeWorker
from test_program_editor_window import FakeBridge, _pump_until, _wait_for_program_load, qapp  # noqa: F401
from ui.memory_bar import MemoryBar, level_for, percent_used
from ui.program_editor_window import ProgramEditorWindow


def _Status(version_major=17, version_minor=1, max_words=1000, free_words=250, max_blocks=100, free_blocks=40):
    # the REAL s3k DeviceStatus over the bytes a real sampler sends (the major number is offset by 15: OS 2.01 = major byte 17, minor 1) -
    # a hand-made stand-in once had attributes the real class doesn't, and hid a crash
    return DeviceStatus(
        m.Status(
            version_major=version_major, version_minor=version_minor, max_blocks=max_blocks, free_blocks=free_blocks,
            max_words=max_words, free_words=free_words, exclusive_channel_setting=0,
        )
    )


class _StatusBridge(FakeBridge):
    def __init__(self, status=None, error=None):
        super().__init__()
        self._status, self._error = status or _Status(), error
        self.status_calls = 0

    def status(self, *, timeout=None):
        self.status_calls += 1
        if self._error:
            raise self._error
        return self._status


def _info(max_words=1000, free_words=250, free_blocks=40):
    return {"max_num_samp_words": max_words, "num_words_free": free_words, "num_blocks_free": free_blocks}


# --- the bar ----------------------------------------------------------------------------------------------------------


def test_percent_and_level_follow_the_dashboards_thresholds():
    assert percent_used(_info(1000, 250)) == 75
    assert percent_used(_info(0, 0)) == 0  # a sampler reporting no memory must not divide by zero
    assert [level_for(p) for p in (0, 69, 70, 89, 90, 100)] == ["normal", "normal", "warning", "warning", "critical", "critical"]


def test_the_bar_shows_a_status_and_forgets_it_when_asked(qapp):
    bar = MemoryBar()
    assert bar.value() == 0 and "-" in bar.format()  # not read yet: says so
    bar.set_status(_info(1000, 100))
    assert bar.value() == 90 and bar.property("memoryLevel") == "critical" and "100 sample words free" in bar.toolTip()
    assert bar.text() == "90% memory used"
    bar.set_unknown("no reply")
    assert bar.value() == 0 and bar.property("memoryLevel") == "normal" and bar.toolTip() == "no reply" and "-" in bar.format()


# --- the worker -----------------------------------------------------------------------------------------------------------


def test_the_worker_reads_the_memory_and_os_version():
    bridge = _StatusBridge(_Status(version_major=18, version_minor=20, max_words=8000, free_words=2000, max_blocks=200, free_blocks=50))
    worker = BridgeWorker(bridge)
    seen = []
    worker.memory_status_loaded.connect(seen.append)
    worker.submit_memory_status()
    worker.process_pending()
    assert seen == [
        {"max_num_samp_words": 8000, "num_words_free": 2000, "num_blocks_free": 50, "max_blocks": 200, "version": "3.20"}
    ]


def test_the_os_version_is_the_panels_not_the_raw_byte():
    # a real S2000 on OS 2.0 sends major byte 17: it must read "2.00", not "17.00"
    worker = BridgeWorker(_StatusBridge(_Status(version_major=17, version_minor=0)))
    seen = []
    worker.memory_status_loaded.connect(seen.append)
    worker.submit_memory_status()
    worker.process_pending()
    assert seen[0]["version"] == "2.00"


def test_a_failed_read_is_reported_not_raised():
    worker = BridgeWorker(_StatusBridge(error=TimeoutError("no reply")))
    errors, loaded = [], []
    worker.memory_status_load_failed.connect(errors.append)
    worker.memory_status_loaded.connect(loaded.append)
    worker.submit_memory_status()
    worker.process_pending()
    assert errors == ["no reply"] and loaded == []


def test_queued_requests_collapse_into_one_read():
    bridge = _StatusBridge()
    worker = BridgeWorker(bridge)
    for _ in range(5):
        worker.submit_memory_status()
    worker.process_pending()
    assert bridge.status_calls == 1


# --- the window --------------------------------------------------------------------------------------------------------------


@pytest.fixture
def editor(qapp):
    bridge = _StatusBridge(_Status(max_words=1000, free_words=250))
    window = ProgramEditorWindow(QWidget(), bridge=bridge)
    _wait_for_program_load(window, qapp)
    window._bridge_for_test = bridge
    yield window
    window._worker.stop()
    window._worker.wait()


def test_the_samples_bar_and_the_global_card_show_what_the_sampler_reported(editor, qapp):
    _pump_until(qapp, lambda: editor.sample_memory_bar.value() == 75)
    assert editor.sample_memory_bar.text() == "75% memory used"
    assert editor.global_tab.memory_bar.value() == 75 and editor.global_tab.os_version_label.text() == "v2.01"


def test_the_bar_sits_under_the_sample_list_at_its_width(editor, qapp):
    editor.resize(1250, 900)
    editor.main_tabs.setCurrentIndex(editor._samples_tab_index)
    editor.show()
    qapp.processEvents()
    try:
        from PySide6.QtCore import QPoint

        listing, bar = editor.sample_list_widget, editor.sample_memory_bar
        assert bar.mapTo(editor, QPoint(0, 0)).y() >= listing.mapTo(editor, QPoint(0, listing.height())).y()
        assert bar.width() == listing.width()
    finally:
        editor.hide()


def test_a_reloaded_sample_list_refreshes_the_figure(editor, qapp):
    _pump_until(qapp, lambda: editor.sample_memory_bar.value() == 75)
    editor._bridge_for_test._status = _Status(free_words=50)  # a send filled the memory
    editor._worker.submit_sample_list()
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.sample_memory_bar.value() == 95)
    assert editor.sample_memory_bar.property("memoryLevel") == "critical"


def test_a_failed_read_clears_both_bars_instead_of_leaving_a_stale_figure(editor, qapp):
    _pump_until(qapp, lambda: editor.sample_memory_bar.value() == 75)
    editor._bridge_for_test._error = TimeoutError("no reply")
    editor._worker.submit_sample_list()
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.sample_memory_bar.value() == 0)
    assert "no reply" in editor.sample_memory_bar.toolTip()
    assert editor.global_tab.memory_bar.value() == 0 and editor.global_tab.os_version_label.text() == "-"


def _card_of(widget):
    while widget.objectName() != "sectionCard":
        widget = widget.parentWidget()
    return widget


def test_the_memory_card_sits_right_of_scsi_and_is_not_a_setting(editor, qapp):
    from PySide6.QtCore import QPoint

    editor.resize(1250, 900)
    editor.main_tabs.setCurrentIndex(editor._global_tab_index)
    editor.show()
    qapp.processEvents()
    try:
        tab = editor.global_tab
        scsi, memory = _card_of(tab._widgets["scsi_disk_id"]), _card_of(tab.memory_bar)
        assert abs(memory.mapTo(editor, QPoint(0, 0)).y() - scsi.mapTo(editor, QPoint(0, 0)).y()) <= 1
        assert memory.mapTo(editor, QPoint(0, 0)).x() > scsi.mapTo(editor, QPoint(0, 0)).x()
        # nothing in the card is a setting: the tab's enable/disable bookkeeping never touches it
        tab.set_unavailable("x")
        assert tab.memory_bar.isEnabled()
    finally:
        editor.hide()


# --- the Samples tab's bar shrinks to a thin line and back --------------------------------------------------------------------------------


def test_double_clicking_the_samples_bar_thins_it_and_double_clicking_again_restores_it(editor, qapp, monkeypatch, tmp_path):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from core import app_config

    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "config.json")
    _pump_until(qapp, lambda: editor.sample_memory_bar.value() == 75)
    bar = editor.sample_memory_bar
    full_height = bar.sizeHint().height()
    assert not bar.is_compact() and bar.isTextVisible() and "Double-click to shrink" in bar.toolTip()

    def double_click():
        QApplication.sendEvent(bar, QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(5, 3), QPointF(5, 3), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    double_click()
    assert bar.is_compact() and not bar.isTextVisible() and bar.maximumHeight() == bar.THIN_HEIGHT < full_height
    assert bar.value() == 75  # still the same figure, just no text
    assert "75% memory used" in bar.toolTip() and "show the full bar" in bar.toolTip()  # the number moves to the tooltip
    assert app_config.get_memory_bar_compact() is True
    editor._on_memory_status_loaded({"max_num_samp_words": 1000, "num_words_free": 50, "num_blocks_free": 5, "version": "2.00"})  # a new reading keeps it thin
    assert bar.is_compact() and bar.value() == 95 and "95% memory used" in bar.toolTip() and bar.property("memoryLevel") == "critical"
    double_click()
    assert not bar.is_compact() and bar.isTextVisible() and bar.maximumHeight() > 1000
    assert app_config.get_memory_bar_compact() is False


def test_the_choice_is_remembered_and_the_global_tabs_bar_never_shrinks(qapp, monkeypatch, tmp_path):
    from core import app_config

    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "config.json")
    first = MemoryBar(collapsible=True)
    first.set_compact(True)
    assert MemoryBar(collapsible=True).is_compact()  # the next window starts thin
    plain = MemoryBar()
    plain.set_compact(True)
    assert not plain.is_compact() and plain.isTextVisible()  # not opted in: unchanged


# --- the Dashboard's bar is the same widget and the same setting ----------------------------------------------------------------------------


def test_the_dashboards_bar_shrinks_too_and_follows_the_editors_bar_live(qapp, editor, monkeypatch, tmp_path):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from controller.sampler_controller import SamplerController
    from core import app_config
    from core.midi_manager import MidiManager
    from ui.dashboard import TransferDashboard

    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "config.json")
    dash = TransferDashboard(SamplerController(MidiManager()), MidiManager())
    try:
        bar, editor_bar = dash.memory_avail_prog_bar, editor.sample_memory_bar
        assert isinstance(bar, MemoryBar) and not bar.is_compact()
        bar.setEnabled(True)  # (the Dashboard disables it with no MIDI input: a disabled widget takes no clicks)
        dash.on_memory_status_updated({"max_num_samp_words": 1000, "num_words_free": 250, "num_blocks_free": 40})
        assert bar.value() == 75 and bar.text() == "75% memory used" and "Double-click to shrink" in bar.toolTip()
        QApplication.sendEvent(bar, QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(5, 3), QPointF(5, 3), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        assert bar.is_compact() and not bar.isTextVisible() and "75% memory used" in bar.toolTip()
        assert editor_bar.is_compact()  # one setting: the editor's bar followed at once
        editor_bar.set_compact(False)  # ...and the other way round
        assert not bar.is_compact() and bar.isTextVisible()
    finally:
        dash.deleteLater()


def test_a_closed_windows_bar_does_not_break_the_others(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QWidget

    from core import app_config

    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "config.json")
    window = QWidget()
    gone = MemoryBar(window, collapsible=True)
    window.deleteLater()
    del window
    qapp.processEvents()
    qapp.sendPostedEvents(None, 0)
    survivor = MemoryBar(collapsible=True)
    survivor.set_compact(True)  # would raise if the deleted bar were still connected
    survivor.set_compact(False)
    assert not survivor.is_compact()
