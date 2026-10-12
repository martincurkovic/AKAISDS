# regression tests for two Yamaha Samples-tab polish fixes:
#  - on a STEREO sample the marker under the cursor lights up (solid) in BOTH channel views, not only the one hovered or dragged
#  - an edit (Reverse ...) leaves the page where it was: disabling the clicked button used to hand its focus down the tab order and the
#    scroll area threw the page about 3/4 of the way down to reveal the widget that received it
# Real window + session vs FakeA4000.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from test_s950_transfers import qapp, wait_until  # noqa: F401
from test_yamaha_markers_ui import user_sample
from test_yamaha_program_editor import warning_acknowledged  # noqa: F401  (autouse: no modal warning)
from test_yamaha_waveform import window  # noqa: F401


def send_mouse(view, kind, x, button=Qt.MouseButton.NoButton):
    """Deliver a mouse event straight to the view (a synthetic move only reaches an exposed, active window)."""
    pos = QPointF(x, view.height() / 2)
    event = QMouseEvent(kind, pos, view.mapToGlobal(pos), button, button, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view, event)
    QApplication.processEvents()


def hover(view, name):
    send_mouse(view, QEvent.Type.MouseMove, view._x_for(name))


def test_a_marker_hovered_in_one_channel_is_solid_in_both(window):
    tab = user_sample(window, "ST", mode=1, end=3000, loop=(1000, 2000), right=True)
    top, bottom = tab.waveform_view, tab.waveform_view_right
    assert top._active_marker_names() == set() and bottom._active_marker_names() == set()
    hover(top, "loop_start")
    assert top._active_marker_names() == {"loop_start"}
    assert bottom._active_marker_names() == {"loop_start"}  # the other half lights the same marker
    QApplication.sendEvent(top, QEvent(QEvent.Type.Leave))  # (the cursor moves down into the other half)
    hover(bottom, "loop_end")  # ...whichever half the cursor is over
    assert top._active_marker_names() == {"loop_end"} and bottom._active_marker_names() == {"loop_end"}
    QApplication.sendEvent(bottom, QEvent(QEvent.Type.Leave))
    assert top._active_marker_names() == set() and bottom._active_marker_names() == set()


def test_a_marker_being_dragged_is_solid_in_both_channels(window):
    tab = user_sample(window, "ST", mode=1, end=3000, loop=(1000, 2000), right=True)
    top, bottom = tab.waveform_view, tab.waveform_view_right
    x = top._x_for("loop_start")
    send_mouse(top, QEvent.Type.MouseButtonPress, x, Qt.MouseButton.LeftButton)
    assert top._dragging == "loop_start" and bottom._active_marker_names() == {"loop_start"}
    send_mouse(top, QEvent.Type.MouseButtonRelease, x, Qt.MouseButton.LeftButton)


def test_a_mono_sample_is_unchanged(window):
    tab = user_sample(window, "MONO", mode=1, end=3000, loop=(1000, 2000))
    view = tab.waveform_view
    hover(view, "loop_end")
    assert view._active_marker_names() == {"loop_end"}  # no partner to consult


def test_an_edit_does_not_scroll_the_page(window, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    tab = user_sample(window, "ST", mode=4, end=4000, right=True)
    window.resize(1250, 800)
    window.show()
    window.activateWindow()
    QApplication.processEvents()
    bar = tab.cards_scroll.verticalScrollBar()
    assert bar.maximum() > 0, "the page must be taller than the window for this test to mean anything"
    bar.setValue(0)
    window._session.send_baud, window._session.send_gap_ms, window._session.send_tick_ms = 10_000_000, 0, 5
    window.controller._yamaha_transfers.verify_delay_ms = 0
    button = tab.edit_buttons["reverse"]
    button.setFocus()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    seen = []
    assert wait_until(lambda: (seen.append(bar.value()), "ST REV" in window.fake.samples and not tab._edit_busy)[1], timeout=20)
    assert wait_until(lambda: "ST REV" in tab.sample_names())
    assert set(seen) == {0} and bar.value() == 0  # the page never moved
