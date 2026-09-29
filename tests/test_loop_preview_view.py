# tests for ui/loop_preview_view.py's LoopJoinPreview - the small waveform
# widget showing the actual splice a loop makes (loop-out audio on the left,
# loop-in audio on the right, one dividing line at the seam - see its own
# class docstring). Covers the placeholder/populated state transitions, the
# drag-to-nudge-the-loop-point interaction (this widget only ever emits a
# frame DELTA - it owns no marker state itself, see marker_drag_delta's own
# docstring), and paint crash-guards, same style test_waveform_view.py's own
# paintEvent crash-guard section uses.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ui.loop_preview_view import LoopJoinPreview, HALF_WINDOW_FRAMES
from ui.waveform_view import x_for_frame


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakePos:
    def __init__(self, x):
        self._x = x

    def x(self):
        return self._x


class _FakeEvent:
    # minimal duck-typed stand-in for a real QMouseEvent - same pattern
    # test_waveform_view.py's own _FakePressEvent/_FakeMoveEvent use, for
    # the same reason (avoids constructing a real QMouseEvent per call)
    def __init__(self, x, button=Qt.MouseButton.LeftButton):
        self._x = x
        self._button = button

    def position(self):
        return _FakePos(self._x)

    def button(self):
        return self._button


def _populated_view(width=400, end_len=HALF_WINDOW_FRAMES, start_len=HALF_WINDOW_FRAMES):
    view = LoopJoinPreview()
    view.resize(width, 140)
    # identity ramps (distinct ranges either side) so a test can tell
    # exactly which half a click landed in from the emitted signal alone
    view.set_join(list(range(end_len)), list(range(1000, 1000 + start_len)))
    return view


def _divider_x(view):
    # same mapping paintEvent/_target_at use internally - a test helper,
    # not exposed on the widget itself
    combined = view._combined()
    return x_for_frame(len(view._end_samples), view.width(), 0, len(combined))


def test_starts_with_no_audio_placeholder(qapp):
    view = LoopJoinPreview()
    assert view._combined() is None
    assert view._placeholder == "No audio loaded"


def test_set_join_concatenates_both_halves(qapp):
    view = LoopJoinPreview()
    end_samples = [1, 2, 3]
    start_samples = [4, 5, 6, 7]
    view.set_join(end_samples, start_samples)
    assert view._combined() == [1, 2, 3, 4, 5, 6, 7]


def test_clear_resets_to_no_audio_placeholder(qapp):
    view = LoopJoinPreview()
    view.set_join([1, 2, 3], [4, 5, 6])
    view.clear()
    assert view._combined() is None
    assert view._placeholder == "No audio loaded"


def test_clear_no_loop_uses_distinct_placeholder(qapp):
    view = LoopJoinPreview()
    view.set_join([1, 2, 3], [4, 5, 6])
    view.clear_no_loop()
    assert view._combined() is None
    assert view._placeholder == "No loop on this sample"


def test_paint_does_not_crash_with_no_audio(qapp):
    view = LoopJoinPreview()
    view.resize(400, 140)
    view.grab()


def test_paint_does_not_crash_with_no_loop(qapp):
    view = LoopJoinPreview()
    view.resize(400, 140)
    view.clear_no_loop()
    view.grab()


def test_paint_does_not_crash_with_a_populated_join(qapp):
    view = LoopJoinPreview()
    view.resize(400, 140)
    end_samples = [int(1000 * ((i % 50) - 25)) for i in range(HALF_WINDOW_FRAMES)]
    start_samples = [int(1000 * ((i % 50) - 25)) for i in range(HALF_WINDOW_FRAMES)]
    view.set_join(end_samples, start_samples)
    view.grab()


def test_paint_does_not_crash_with_unevenly_sized_halves(qapp):
    # reachable near either edge of the real buffer - samples_before/
    # samples_after (WaveformView's own) clamp rather than always returning
    # exactly HALF_WINDOW_FRAMES, so the divider isn't always at the exact
    # pixel midpoint
    view = LoopJoinPreview()
    view.resize(400, 140)
    view.set_join([1, 2, 3], list(range(200)))
    view.grab()


def test_resizing_rebuilds_the_envelope_without_crashing(qapp):
    view = LoopJoinPreview()
    view.resize(400, 140)
    end_samples = [int(1000 * ((i % 50) - 25)) for i in range(HALF_WINDOW_FRAMES)]
    start_samples = [int(1000 * ((i % 50) - 25)) for i in range(HALF_WINDOW_FRAMES)]
    view.set_join(end_samples, start_samples)
    view.resize(600, 140)
    view.grab()


# --- dragging: left half nudges loop_end, right half nudges loop_start -----
# this widget owns no marker state - it only ever emits a frame delta (see
# marker_drag_delta's own docstring); ProgramEditorWindow._on_loop_preview_
# marker_dragged is what actually applies it via WaveformView.set_marker,
# covered separately in tests/test_program_editor_window.py.


def test_target_at_picks_loop_end_left_of_the_divider(qapp):
    view = _populated_view()
    divider_x = _divider_x(view)
    assert view._target_at(divider_x - 5) == "loop_end"


def test_target_at_picks_loop_start_at_or_right_of_the_divider(qapp):
    view = _populated_view()
    divider_x = _divider_x(view)
    assert view._target_at(divider_x + 5) == "loop_start"


def test_target_at_returns_none_without_data(qapp):
    view = LoopJoinPreview()
    view.resize(400, 140)
    assert view._target_at(50) is None


def test_dragging_the_left_half_emits_loop_end_deltas(qapp):
    view = _populated_view()
    calls = []
    view.marker_drag_delta.connect(lambda name, delta: calls.append((name, delta)))
    x = _divider_x(view) - 50  # well inside the left (loop_end) half

    view.mousePressEvent(_FakeEvent(x))
    view.mouseMoveEvent(_FakeEvent(x + 30))

    assert calls
    assert all(name == "loop_end" for name, _delta in calls)
    assert sum(delta for _name, delta in calls) > 0  # dragged right -> increases


def test_dragging_the_right_half_emits_loop_start_deltas(qapp):
    view = _populated_view()
    calls = []
    view.marker_drag_delta.connect(lambda name, delta: calls.append((name, delta)))
    x = _divider_x(view) + 50  # well inside the right (loop_start) half

    view.mousePressEvent(_FakeEvent(x))
    view.mouseMoveEvent(_FakeEvent(x - 30))  # dragged LEFT this time

    assert calls
    assert all(name == "loop_start" for name, _delta in calls)
    assert sum(delta for _name, delta in calls) < 0  # dragged left -> decreases


def test_right_click_does_not_start_a_drag(qapp):
    view = _populated_view()
    calls = []
    view.marker_drag_delta.connect(lambda name, delta: calls.append((name, delta)))
    x = _divider_x(view) - 50

    view.mousePressEvent(_FakeEvent(x, button=Qt.MouseButton.RightButton))
    view.mouseMoveEvent(_FakeEvent(x + 30))

    assert calls == []


def test_release_stops_the_drag(qapp):
    view = _populated_view()
    calls = []
    view.marker_drag_delta.connect(lambda name, delta: calls.append((name, delta)))
    x = _divider_x(view) - 50
    view.mousePressEvent(_FakeEvent(x))
    view.mouseReleaseEvent(_FakeEvent(x))

    view.mouseMoveEvent(_FakeEvent(x + 30))  # no press in between

    assert calls == []


def test_mouse_move_without_a_prior_press_does_nothing(qapp):
    view = _populated_view()
    calls = []
    view.marker_drag_delta.connect(lambda name, delta: calls.append((name, delta)))

    view.mouseMoveEvent(_FakeEvent(_divider_x(view) + 30))

    assert calls == []


def test_slow_drag_accumulates_many_small_moves_into_real_progress(qapp):
    # same "sub-frame remainder carried between events" fix WaveformView's
    # own canvas drag uses - without it, a smooth/slow drag (many small
    # per-event deltas, each individually under the rounding threshold)
    # would round back down to zero every time and barely move anything
    view = _populated_view(width=400, end_len=2000)  # low frames-per-px
    calls = []
    view.marker_drag_delta.connect(lambda name, delta: calls.append((name, delta)))
    start_x = _divider_x(view) - 200

    view.mousePressEvent(_FakeEvent(start_x))
    x = start_x
    for _ in range(60):
        x += 1
        view.mouseMoveEvent(_FakeEvent(x))

    assert sum(delta for _name, delta in calls) > 0


def test_mouse_move_with_no_data_stops_the_drag_without_crashing(qapp):
    # the sample/loop selection changed out from under an in-progress drag
    view = _populated_view()
    calls = []
    view.marker_drag_delta.connect(lambda name, delta: calls.append((name, delta)))
    x = _divider_x(view) - 50
    view.mousePressEvent(_FakeEvent(x))
    view.clear()

    view.mouseMoveEvent(_FakeEvent(x + 30))

    assert calls == []
    assert view._dragging is None
