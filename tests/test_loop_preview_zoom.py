# The Loop Preview's zoom (ui/loop_preview_view.py): a ladder of half-windows from 19 to 2400 frames with 150 as the default, three tiny
# buttons beside the card's title, Ctrl+scroll and a trackpad pinch - in both the Program Editor and the Yamaha editor.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QNativeGestureEvent, QPointingDevice, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from test_program_editor_window import _pump_until, _stub_demo_audio, editor, qapp  # noqa: F401
from test_s950_transfers import wait_until
from test_yamaha_markers_ui import user_sample
from test_yamaha_program_editor import warning_acknowledged  # noqa: F401
from test_yamaha_waveform import window  # noqa: F401
from ui.loop_preview_view import (
    DEFAULT_ZOOM_INDEX,
    HALF_WINDOW_FRAMES,
    ZOOM_LADDER,
    LoopJoinPreview,
    LoopPreviewZoomControls,
)


def _wheel(widget, delta, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QWheelEvent(
        QPointF(20, 20), QPointF(20, 20), QPoint(0, 0), QPoint(0, delta),
        Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(widget, event)
    return event


def _pinch(widget, value):
    event = QNativeGestureEvent(
        Qt.NativeGestureType.ZoomNativeGesture, QPointingDevice.primaryPointingDevice(), 2,
        QPointF(20, 20), QPointF(20, 20), QPointF(20, 20), value, QPointF(0, 0),
    )
    QApplication.sendEvent(widget, event)


# --- the zoom ---------------------------------------------------------------------------------------------------------------


def test_the_ladder_doubles_each_step_and_150_is_the_default(qapp):
    assert ZOOM_LADDER == (19, 38, 75, 150, 300, 600, 1200, 2400)
    assert ZOOM_LADDER[DEFAULT_ZOOM_INDEX] == HALF_WINDOW_FRAMES == 150
    preview = LoopJoinPreview()
    assert preview.half_window() == 150 and preview.is_default_zoom()  # nothing changes for anyone who never zooms


def test_zoom_in_and_out_walk_the_ladder_and_stop_at_both_ends(qapp):
    preview = LoopJoinPreview()
    seen = []
    preview.zoom_changed.connect(seen.append)
    assert preview.zoom_in() and preview.half_window() == 75
    assert preview.zoom_out() and preview.zoom_out() and preview.half_window() == 300
    while preview.zoom_in():
        pass
    assert preview.half_window() == 19 and not preview.can_zoom_in() and not preview.zoom_in()
    while preview.zoom_out():
        pass
    assert preview.half_window() == 2400 and not preview.can_zoom_out() and not preview.zoom_out()
    assert seen[:3] == [75, 150, 300]
    count = len(seen)
    preview.zoom_out()  # at the limit: no change, no signal
    assert len(seen) == count


def test_reset_goes_back_to_the_default_and_says_so_only_when_it_moved(qapp):
    preview = LoopJoinPreview()
    seen = []
    preview.zoom_changed.connect(seen.append)
    assert not preview.reset_zoom() and seen == []
    preview.zoom_out()
    preview.zoom_out()
    assert preview.reset_zoom() and preview.half_window() == 150 and preview.is_default_zoom() and seen[-1] == 150


# --- the tiny controls ---------------------------------------------------------------------------------------------------------


def test_the_controls_are_tiny_follow_the_zoom_and_never_take_focus(qapp):
    preview = LoopJoinPreview()
    controls = LoopPreviewZoomControls(preview)
    for button in (controls.zoom_out_button, controls.zoom_in_button, controls.reset_button):
        assert button.height() <= 18 and button.focusPolicy() == Qt.FocusPolicy.NoFocus
        assert button.property("compact") is True
    assert controls.zoom_in_button.isEnabled() and controls.zoom_out_button.isEnabled()
    assert not controls.reset_button.isEnabled()  # already at the default
    controls.zoom_in_button.click()
    assert controls.reset_button.isEnabled() and "75 frames" in controls.zoom_in_button.toolTip()
    while preview.zoom_in():
        pass
    assert not controls.zoom_in_button.isEnabled() and controls.zoom_out_button.isEnabled()
    controls.reset_button.click()
    assert preview.is_default_zoom() and not controls.reset_button.isEnabled()
    while preview.zoom_out():
        pass
    assert not controls.zoom_out_button.isEnabled()
    assert "Ctrl+scroll" in controls.zoom_out_button.toolTip()


# --- Ctrl+scroll and pinch --------------------------------------------------------------------------------------------------------


def test_ctrl_scroll_zooms_and_a_plain_scroll_is_left_for_the_page(qapp):
    preview = LoopJoinPreview()
    plain = _wheel(preview, 120)
    assert not plain.isAccepted() and preview.is_default_zoom()  # ignored: it reaches the scroll area
    event = _wheel(preview, 120, Qt.KeyboardModifier.ControlModifier)
    assert event.isAccepted() and preview.half_window() == 75  # up = in, like the main waveform
    _wheel(preview, -240, Qt.KeyboardModifier.ControlModifier)
    assert preview.half_window() == 300


def test_a_trackpads_small_scroll_deltas_add_up_to_a_step(qapp):
    preview = LoopJoinPreview()
    for _ in range(3):
        _wheel(preview, 30, Qt.KeyboardModifier.ControlModifier)
    assert preview.is_default_zoom()  # 90 of 120
    _wheel(preview, 30, Qt.KeyboardModifier.ControlModifier)
    assert preview.half_window() == 75


def test_pinching_out_zooms_in_and_pinching_in_zooms_out_with_the_same_limits(qapp):
    preview = LoopJoinPreview()
    for _ in range(10):
        _pinch(preview, 0.05)  # fingers apart: ~x1.6 in all
    assert preview.half_window() < 150
    for _ in range(300):
        _pinch(preview, 0.05)
    assert preview.half_window() == 19  # the limit holds
    for _ in range(300):
        _pinch(preview, -0.05)
    assert preview.half_window() == 2400


# --- dragging follows the zoom ------------------------------------------------------------------------------------------------------


def test_a_drag_is_finer_when_zoomed_in(qapp):
    # the drag maths measures frames per pixel from whatever is shown: 75 frames across the same width move half as far per pixel
    deltas = {}
    for frames in (150, 75):
        preview = LoopJoinPreview()
        preview.resize(400, 140)
        preview.set_join(list(range(frames)), list(range(frames)))
        got = []
        preview.marker_drag_delta.connect(lambda name, delta, got=got: got.append(delta))
        QTest.mousePress(preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(300, 70))
        QTest.mouseMove(preview, QPoint(260, 70))
        QTest.mouseRelease(preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(260, 70))
        deltas[frames] = sum(got)
    assert deltas[150] != 0 and abs(deltas[150]) == pytest.approx(2 * abs(deltas[75]), abs=2)


# --- the card and both editors -----------------------------------------------------------------------------------------------------------


def _title_label(widget, text):
    return next(l for l in widget.findChildren(QLabel) if l.text() == text)


def test_akai_the_controls_sit_beside_the_title_and_the_zoom_changes_what_the_preview_shows(editor, qapp, monkeypatch):
    import ui.program_editor_window as pew

    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    monkeypatch.setattr(pew, "_DEMO_MS_PER_WORD", 0.001)
    _stub_demo_audio(editor, monkeypatch, [int(8000 * ((i % 100) / 50 - 1)) for i in range(3000)])
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    editor._load_sample_waveform()
    editor.waveform_view.set_marker("loop_start", 1000)
    editor.waveform_view.set_marker("loop_end", 2000)
    editor.main_tabs.setCurrentIndex(editor._samples_tab_index)
    editor.resize(1250, 900)
    editor.show()
    qapp.processEvents()
    try:
        editor._refresh_loop_preview(1000, 2000)
        assert len(editor.loop_preview._end_samples) == 150 and len(editor.loop_preview._start_samples) == 150
        editor.loop_preview.zoom_in()
        assert len(editor.loop_preview._end_samples) == 75 and len(editor.loop_preview._start_samples) == 75
        editor.loop_preview.zoom_out()
        editor.loop_preview.zoom_out()
        assert len(editor.loop_preview._end_samples) == 300
        editor.loop_preview.reset_zoom()
        assert len(editor.loop_preview._end_samples) == 150
        # the buttons are in the card's title row, right beside the words
        title = _title_label(editor, "Loop Preview")
        controls = editor.findChild(LoopPreviewZoomControls)
        t = title.mapTo(editor, QPoint(0, 0))
        c = controls.mapTo(editor, QPoint(0, 0))
        assert abs(c.y() + controls.height() / 2 - (t.y() + title.height() / 2)) <= 6  # on the title's line
        assert 0 <= c.x() - (t.x() + title.width()) < 40  # just after it
        assert editor.loop_preview.height() == 140  # the preview itself did not shrink
    finally:
        editor.hide()


def test_akai_the_zoom_survives_selecting_another_sample(editor, qapp):
    editor.loop_preview.zoom_in()
    editor.loop_preview.zoom_in()
    editor.sample_list_widget.setCurrentRow(1)
    editor._worker.wait_until_idle()
    qapp.processEvents()
    assert editor.loop_preview.half_window() == 38  # session-wide: not reset by changing sample


def test_yamaha_the_controls_are_beside_the_title_and_the_zoom_changes_the_preview(window):  # noqa: F811
    tab = user_sample(window, mode=1, end=3500, loop=(1500, 2500))
    window.resize(1250, 900)
    window.show()
    window.main_tabs.setCurrentIndex(window._samples_tab_index)
    QApplication.processEvents()
    try:
        tab._refresh_loop_preview(1500, 2499)
        assert len(tab.loop_preview._end_samples) == 150
        tab.loop_preview.zoom_in()
        assert len(tab.loop_preview._end_samples) == 75
        tab.loop_preview.reset_zoom()
        assert len(tab.loop_preview._end_samples) == 150
        title = _title_label(tab, "Loop Preview")
        controls = tab.findChild(LoopPreviewZoomControls)
        t = title.mapTo(window, QPoint(0, 0))
        c = controls.mapTo(window, QPoint(0, 0))
        assert abs(c.y() + controls.height() / 2 - (t.y() + title.height() / 2)) <= 6 and 0 <= c.x() - (t.x() + title.width()) < 40
    finally:
        window.hide()
