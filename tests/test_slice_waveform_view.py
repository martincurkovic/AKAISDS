# tests for ui/slice_waveform_view.py - the Slice Editor's own waveform
# canvas (start/end edge handles + an arbitrary-length interior marker
# list, no push-cascade - see the class's own docstring for why this is a
# genuinely different marker model from ui/waveform_view.py's WaveformView)

import math

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ui.slice_waveform_view import SliceWaveformView


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _sine(n, period=40, amplitude=10000):
    return [int(amplitude * math.sin(2 * math.pi * i / period)) for i in range(n)]


class _FakePos:
    def __init__(self, x):
        self._x = x

    def x(self):
        return self._x

    def toPoint(self):
        return self


class _FakeEvent:
    def __init__(self, x, button=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier):
        self._x = x
        self._button = button
        self._modifiers = modifiers

    def position(self):
        return _FakePos(self._x)

    def globalPosition(self):
        return _FakePos(self._x)

    def button(self):
        return self._button

    def modifiers(self):
        return self._modifiers

    def accept(self):
        pass


def _view(frame_count=4000, start=0, end=None, markers=None, width=400):
    view = SliceWaveformView()
    view.resize(width, 220)
    samples = _sine(frame_count)
    view.set_waveform(samples, start, frame_count - 1 if end is None else end, markers)
    return view


# --- set_waveform / basic state --------------------------------------------


def test_set_waveform_sets_frame_count_and_default_start_end(qapp):
    view = _view(1000)
    assert view.has_waveform()
    assert view.frame_count() == 1000
    assert view.start() == 0
    assert view.end() == 999
    assert view.slice_markers() == []
    assert view.slice_count() == 1


def test_set_waveform_accepts_initial_markers(qapp):
    view = _view(1000, markers=[300, 600])
    assert view.slice_markers() == [300, 600]
    assert view.slice_count() == 3


def test_set_waveform_filters_out_of_range_initial_markers(qapp):
    view = _view(1000, start=100, end=900, markers=[0, 50, 500, 950, 999])
    assert view.slice_markers() == [500]


# --- add_marker / remove_marker / set_markers -------------------------------


def test_add_marker_snaps_to_a_zero_crossing_and_is_sorted(qapp):
    samples = [0] * 2000
    view = SliceWaveformView()
    view.resize(400, 220)
    view.set_waveform(samples, 0, 1999)
    added = view.add_marker(1000)
    assert added == 1000  # every sample is already zero - itself is a crossing
    assert view.slice_markers() == [1000]


def test_add_marker_rejects_a_frame_outside_start_end(qapp):
    view = _view(1000, start=100, end=900)
    assert view.add_marker(50) is None
    assert view.add_marker(950) is None
    assert view.slice_markers() == []


def test_add_marker_rejects_a_duplicate(qapp):
    samples = [0] * 2000
    view = SliceWaveformView()
    view.resize(400, 220)
    view.set_waveform(samples, 0, 1999)
    view.add_marker(500)
    assert view.add_marker(500) is None
    assert view.slice_markers() == [500]


def test_remove_marker(qapp):
    view = _view(1000, markers=[300, 600])
    view.remove_marker(300)
    assert view.slice_markers() == [600]


def test_remove_marker_missing_frame_is_a_noop(qapp):
    view = _view(1000, markers=[300])
    view.remove_marker(999)
    assert view.slice_markers() == [300]


def test_set_markers_replaces_wholesale_and_filters(qapp):
    view = _view(1000, markers=[300])
    view.set_markers([100, 200, 5000])
    assert view.slice_markers() == [100, 200]  # 5000 is outside (0, 999)


# --- slice_bounds / slice_count ----------------------------------------------


def test_slice_bounds_with_no_markers_is_the_whole_start_end_region(qapp):
    view = _view(1000, start=10, end=90)
    assert view.slice_bounds() == [(10, 90)]


def test_slice_bounds_reflects_current_markers(qapp):
    view = _view(1000, markers=[250, 500, 750])
    assert view.slice_bounds() == [(0, 249), (250, 499), (500, 749), (750, 999)]
    assert view.slice_count() == 4


# --- double-click adds a marker ---------------------------------------------


def test_double_click_adds_a_marker_at_the_clicked_frame(qapp):
    samples = [0] * 4000
    view = SliceWaveformView()
    view.resize(400, 220)
    view.set_waveform(samples, 0, 3999)
    mid_x = view.width() / 2
    view.mouseDoubleClickEvent(_FakeEvent(mid_x))
    assert len(view.slice_markers()) == 1
    frame = view.slice_markers()[0]
    assert 0 < frame < 3999


def test_double_click_outside_start_end_does_nothing(qapp):
    view = _view(1000, start=400, end=600)
    view.mouseDoubleClickEvent(_FakeEvent(1))  # far left - outside [start, end]
    assert view.slice_markers() == []


def test_double_click_on_an_existing_handle_does_not_add_a_second_marker(qapp):
    samples = [0] * 4000
    view = SliceWaveformView()
    view.resize(400, 220)
    view.set_waveform(samples, 0, 3999)
    start_x = view._x_for(0)
    view.mouseDoubleClickEvent(_FakeEvent(start_x))
    assert view.slice_markers() == []


# --- dragging: start/end edges never cross a marker, markers never cross ---
# their neighbours - a clamp, not a push (see the widget's own docstring)


def test_dragging_start_clamps_at_the_first_marker(qapp):
    samples = [0] * 4000
    view = SliceWaveformView()
    view.resize(400, 220)
    view.set_waveform(samples, 0, 3999, [1000])
    start_x = view._x_for(0)
    far_right_x = view._x_for(3900)
    view.mousePressEvent(_FakeEvent(start_x))
    view.mouseMoveEvent(_FakeEvent(far_right_x))
    assert view.start() < 1000
    view.mouseReleaseEvent(_FakeEvent(far_right_x))
    assert view.start() < 1000


def test_dragging_end_clamps_at_the_last_marker(qapp):
    samples = [0] * 4000
    view = SliceWaveformView()
    view.resize(400, 220)
    view.set_waveform(samples, 0, 3999, [3000])
    end_x = view._x_for(3999)
    far_left_x = view._x_for(100)
    view.mousePressEvent(_FakeEvent(end_x))
    view.mouseMoveEvent(_FakeEvent(far_left_x))
    assert view.end() > 3000


def test_dragging_a_marker_clamps_between_its_neighbours(qapp):
    samples = [0] * 4000
    view = SliceWaveformView()
    view.resize(400, 220)
    view.set_waveform(samples, 0, 3999, [1000, 2000, 3000])
    middle_x = view._x_for(2000)
    beyond_right_neighbour_x = view._x_for(3500)
    view.mousePressEvent(_FakeEvent(middle_x))
    view.mouseMoveEvent(_FakeEvent(beyond_right_neighbour_x))
    assert view.slice_markers() == [1000, 2999, 3000]  # clamped just inside 3000


def test_release_snaps_the_dragged_marker_to_a_zero_crossing(qapp):
    # a sine wave has real, findable zero crossings - drag near one and
    # confirm release lands exactly on it, not the raw dragged-to frame
    samples = _sine(4000, period=200)
    view = SliceWaveformView()
    view.resize(400, 220)
    view.set_waveform(samples, 0, 3999, [1000])
    x = view._x_for(1000)
    view.mousePressEvent(_FakeEvent(x))
    # drag a few frames away from the exact crossing
    view.mouseMoveEvent(_FakeEvent(x + 1))
    view.mouseReleaseEvent(_FakeEvent(x + 1))
    landed = view.slice_markers()[0]
    assert samples[landed] == 0 or (
        (landed > 0 and (samples[landed - 1] < 0) != (samples[landed] < 0))
        or (landed < len(samples) - 1 and (samples[landed] < 0) != (samples[landed + 1] < 0))
    )


def test_drag_is_live_before_release_not_only_on_commit(qapp):
    samples = [0] * 4000
    view = SliceWaveformView()
    view.resize(400, 220)
    view.set_waveform(samples, 0, 3999)
    end_x = view._x_for(3999)
    view.mousePressEvent(_FakeEvent(end_x))
    view.mouseMoveEvent(_FakeEvent(end_x - 100))
    assert view.end() < 3999  # moved already, before mouseReleaseEvent


# --- right-click context menu deletes a marker ------------------------------


def test_right_click_on_a_marker_offers_delete(qapp, monkeypatch):
    # QMenu.exec() itself pops a real native popup with no headless-testable
    # hook (unlike QMessageBox.warning/question, a plain classmethod call
    # other tests in this suite already monkeypatch directly) - so this
    # verifies the same thing at the boundary that actually matters: a
    # right-click on a marker reaches _show_marker_context_menu with that
    # marker's own frame, and remove_marker (exercised directly above)
    # is what it calls once a user picks the delete action
    view = _view(1000, markers=[500])
    x = view._x_for(500)
    calls = []
    monkeypatch.setattr(
        view, "_show_marker_context_menu", lambda frame, pos: calls.append(frame)
    )
    view.mousePressEvent(_FakeEvent(x, button=Qt.MouseButton.RightButton))
    assert calls == [500]


def test_right_click_elsewhere_does_nothing(qapp):
    view = _view(1000, markers=[500])
    view.mousePressEvent(_FakeEvent(1, button=Qt.MouseButton.RightButton))
    assert view.slice_markers() == [500]


# --- zoom / view_changed -----------------------------------------------------


def test_zoom_in_shrinks_the_view_length(qapp):
    view = _view(100000)
    before = view._view_length()
    view.zoom_in()
    assert view._view_length() < before


def test_view_changed_emits_on_zoom(qapp):
    view = _view(100000)
    received = []
    view.view_changed.connect(lambda *args: received.append(args))
    view.zoom_in()
    assert received
    view_start, view_length, frame_count = received[-1]
    assert frame_count == 100000


def test_max_zoom_reaches_single_sample_resolution(qapp):
    view = _view(50000)
    for _ in range(60):
        view.zoom_in()
    assert view._view_length() == 1


# --- paint smoke tests -------------------------------------------------------


def test_paint_does_not_crash_with_no_waveform(qapp):
    view = SliceWaveformView()
    view.resize(400, 220)
    view.grab()


def test_paint_does_not_crash_with_markers_and_zoom(qapp):
    view = _view(20000, markers=[5000, 10000, 15000])
    for _ in range(10):
        view.zoom_in()
    view.grab()


def test_paint_does_not_crash_at_high_zoom_connected_sample_mode(qapp):
    view = _view(2000, markers=[500, 1000, 1500])
    for _ in range(20):
        view.zoom_in()
    assert 0 < view._view_length() <= view.width()
    view.grab()
