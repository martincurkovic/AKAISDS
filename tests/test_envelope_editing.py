# Dragging the envelope graphs' handles (ui/envelope_graph.py): the inverse maths, the drag itself (live signals, one commit on release, Shift, Esc, reset),
# and what each editor does with it - knobs follow at once, nothing reaches a sampler until the release.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import random

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QToolTip

from test_program_editor_window import _select_keygroup_1, editor, qapp  # noqa: F401
from test_s950_program_editor import _show, win as s950_win  # noqa: F401
from test_s950_transfers import wait_until
from test_yamaha_program_editor import fake, warning_acknowledged, win as yamaha_win  # noqa: F401
from ui import yamaha_writer as writer_module
from PySide6.QtWidgets import QMessageBox
from ui.envelope_graph import (
    GRAPH_SIZE,
    ADSREnvelopeGraph,
    Envelope2Graph,
    LevelEnvelopeGraph,
    _adsr_points,
    _env2_points,
    _level_envelope_points,
    adsr_values_for_handle,
    adsr_values_for_sustain,
    env2_values_for_handle,
    level_values_for_handle,
    level_values_for_sustain,
    yamaha_adsr_rates,
    yamaha_adsr_values,
)

W, H = 264, 94  # the inner size of a GRAPH_SIZE graph


# --- the maths: dragging a handle onto where a value draws it gives that value back -------------------------------------------------------


def test_adsr_handles_invert_the_drawing():
    rng = random.Random(1)
    for _ in range(200):
        values = tuple(rng.randint(0, 99) for _ in range(4))
        points = _adsr_points(*values, W, H)
        for index, point in zip(range(3), (points[1], points[2], points[4])):
            assert adsr_values_for_handle(index, *point, values, W, H) == values


def test_dragging_one_adsr_stage_never_changes_the_others():
    # each stage's width is its own: moving the decay handle must leave attack and release alone (the bug _adsr_points' comment tells)
    values = (25, 50, 70, 45)
    points = _adsr_points(*values, W, H)
    attack, decay, sustain, release = adsr_values_for_handle(1, points[2][0] + 20, points[2][1] - 10, values, W, H)
    assert (attack, release) == (25, 45) and decay != 50 and sustain != 70


def test_adsr_handles_clamp_to_the_ranges_however_far_they_are_dragged():
    values = (25, 50, 70, 45)
    assert adsr_values_for_handle(0, -500, 0, values, W, H)[0] == 0
    assert adsr_values_for_handle(0, 5000, 0, values, W, H)[0] == 99
    assert adsr_values_for_handle(1, 5000, -500, values, W, H)[1:3] == (99, 99)
    assert adsr_values_for_handle(1, -500, 5000, values, W, H)[1:3] == (0, 0)
    assert adsr_values_for_handle(2, 5000, 0, values, W, H)[3] == 99


def test_env2_handles_invert_the_drawing():
    rng = random.Random(2)
    for _ in range(200):
        stages = [(rng.randint(0, 99), rng.randint(0, 99)) for _ in range(4)]
        points = _env2_points(stages, W, H)
        for index in range(4):
            assert env2_values_for_handle(index, *points[index + 1], stages, W, H) == stages


def test_level_handles_invert_the_drawing():
    rng = random.Random(3)
    for _ in range(200):
        values = (*(rng.randint(-127, 127) for _ in range(4)), *(rng.randint(0, 127) for _ in range(3)))
        points = _level_envelope_points(*values, W, H, margin=0)
        for index, point in zip(range(4), (points[0], points[1], points[2], points[4])):
            assert level_values_for_handle(index, *point, values, W, H, margin=0) == values


def test_the_sustain_line_changes_only_the_sustain_level():
    rng = random.Random(4)
    for _ in range(100):
        values = tuple(rng.randint(0, 99) for _ in range(4))
        points = _adsr_points(*values, W, H)
        assert adsr_values_for_sustain(points[2][1], values, H) == values  # the line is where the level draws it
    assert adsr_values_for_sustain(-50, (10, 20, 30, 40), H) == (10, 20, 99, 40)
    assert adsr_values_for_sustain(500, (10, 20, 30, 40), H) == (10, 20, 0, 40)
    level = (0, 100, 50, -20, 100, 100, 100)
    points = _level_envelope_points(*level, W, H, margin=0)
    assert level_values_for_sustain(points[2][1], level, H, margin=0) == level
    assert level_values_for_sustain(-50, level, H, margin=0) == (0, 100, 127, -20, 100, 100, 100)
    assert level_values_for_sustain(500, level, H, margin=0)[2] == -127


def test_the_yamaha_adsr_rates_are_the_inverse_of_the_graphs_times():
    for rate in range(128):
        assert yamaha_adsr_rates(*yamaha_adsr_values(rate, rate, rate, rate)) == (rate,) * 4
    # dragging right is a longer stage: a LOWER rate
    assert yamaha_adsr_rates(80, 0, 0, 0)[0] < yamaha_adsr_rates(20, 0, 0, 0)[0]


# --- the drag ------------------------------------------------------------------------------------------------------------------------------


def _event(kind, position, *, buttons=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier):
    button = Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton
    return QMouseEvent(kind, QPointF(position), QPointF(position), button, buttons, modifiers)


def press(graph, position, *, shift=False):
    QApplication.sendEvent(
        graph,
        _event(QEvent.Type.MouseButtonPress, position, modifiers=Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier),
    )


def move(graph, position, *, shift=False):
    QApplication.sendEvent(
        graph, _event(QEvent.Type.MouseMove, position, modifiers=Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier)
    )


def release(graph, position):
    QApplication.sendEvent(graph, _event(QEvent.Type.MouseButtonRelease, position, buttons=Qt.MouseButton.NoButton))


def double_click(graph, position):
    QApplication.sendEvent(graph, _event(QEvent.Type.MouseButtonDblClick, position))


def key(graph, which):
    QApplication.sendEvent(graph, __import__("PySide6.QtGui", fromlist=["QKeyEvent"]).QKeyEvent(QEvent.Type.KeyPress, which, Qt.KeyboardModifier.NoModifier))


class Recorder:
    def __init__(self, graph):
        self.live, self.committed, self.cancelled = [], [], 0
        graph.values_changed.connect(self.live.append)
        graph.edit_committed.connect(self.committed.append)
        graph.edit_cancelled.connect(lambda: setattr(self, "cancelled", self.cancelled + 1))


@pytest.fixture
def adsr(qapp):
    graph = ADSREnvelopeGraph()
    graph.setFixedSize(*GRAPH_SIZE)
    graph.set_values(25, 50, 70, 45)
    graph.set_defaults({"attack": 25, "decay": 50, "sustain": 99, "release": 45})
    graph.set_editable(True)
    graph.show()
    qapp.processEvents()
    yield graph
    graph.hide()


def test_a_graph_is_inert_until_it_is_made_editable(qapp):
    graph = ADSREnvelopeGraph()
    graph.setFixedSize(*GRAPH_SIZE)
    graph.set_values(25, 50, 70, 45)
    rec = Recorder(graph)
    centre = graph.handle_positions()[0]
    press(graph, centre)
    move(graph, centre + QPointF(30, 0))
    release(graph, centre + QPointF(30, 0))
    assert not graph.is_dragging() and graph.values()["attack"] == 25 and rec.live == [] and rec.committed == []
    assert not graph.grab().isNull()


def test_a_disabled_graph_does_not_edit_either(adsr):
    adsr.setEnabled(False)
    rec = Recorder(adsr)
    centre = adsr.handle_positions()[0]
    press(adsr, centre)
    move(adsr, centre + QPointF(30, 0))
    assert rec.live == [] and adsr.values()["attack"] == 25


def test_dragging_reports_live_and_commits_once_on_release(adsr):
    rec = Recorder(adsr)
    start = adsr.handle_positions()[0]
    press(adsr, start)
    assert adsr.is_dragging()
    for dx in (5, 12, 20, 31):
        move(adsr, start + QPointF(dx, 0))
    assert len(rec.live) >= 3 and all(set(v) == {"attack"} for v in rec.live)
    assert rec.committed == [] and adsr.values()["attack"] > 25  # nothing is committed while the pointer is down
    release(adsr, start + QPointF(31, 0))
    assert not adsr.is_dragging()
    assert rec.committed == [{"attack": adsr.values()["attack"]}] and rec.cancelled == 0


def test_a_handle_does_not_jump_to_the_pointer(adsr):
    rec = Recorder(adsr)
    centre = adsr.handle_positions()[0]
    grab = centre + QPointF(5, 6)  # not exactly on the centre
    press(adsr, grab)
    move(adsr, grab)  # no movement
    assert rec.live == [] and adsr.values()["attack"] == 25


def test_a_two_value_handle_commits_both_and_a_release_without_change_commits_nothing(adsr):
    rec = Recorder(adsr)
    start = adsr.handle_positions()[1]  # the end of the decay: decay and sustain
    press(adsr, start)
    move(adsr, start + QPointF(15, -12))
    release(adsr, start + QPointF(15, -12))
    assert set(rec.committed[0]) == {"decay", "sustain"}
    rec.committed.clear()
    start = adsr.handle_positions()[2]
    press(adsr, start)
    release(adsr, start)
    assert rec.committed == []


def test_pressing_away_from_every_handle_does_nothing(adsr):
    press(adsr, QPointF(GRAPH_SIZE[0] - 20, 20))  # the empty top-right corner
    assert not adsr.is_dragging()


def test_shift_slows_the_drag(adsr):
    def dragged(shift):
        graph = ADSREnvelopeGraph()
        graph.setFixedSize(*GRAPH_SIZE)
        graph.set_values(25, 50, 70, 45)
        graph.set_editable(True)
        start = graph.handle_positions()[0]
        press(graph, start, shift=shift)
        move(graph, start + QPointF(40, 0), shift=shift)
        release(graph, start + QPointF(40, 0))
        return graph.values()["attack"] - 25

    assert 0 < dragged(True) < dragged(False) / 3  # (Shift held from the press: slower from the very first move)


def test_leaving_shift_mid_drag_does_not_make_the_handle_jump(adsr):
    start = adsr.handle_positions()[0]
    press(adsr, start)
    move(adsr, start + QPointF(20, 0), shift=True)
    before = adsr.values()["attack"]
    move(adsr, start + QPointF(20, 0), shift=False)  # the same pointer position, shift released
    assert adsr.values()["attack"] == before


def test_esc_cancels_the_drag_and_puts_everything_back(adsr):
    rec = Recorder(adsr)
    start = adsr.handle_positions()[1]
    press(adsr, start)
    move(adsr, start + QPointF(20, 15))
    assert adsr.values() != {"attack": 25, "decay": 50, "sustain": 70, "release": 45}
    key(adsr, Qt.Key.Key_Escape)
    assert adsr.values() == {"attack": 25, "decay": 50, "sustain": 70, "release": 45}
    assert rec.cancelled == 1 and rec.committed == [] and not adsr.is_dragging()
    assert rec.live[-1] == {"decay": 50, "sustain": 70}  # the owner's knobs are told to go back too
    release(adsr, start + QPointF(20, 15))  # the release that follows changes nothing
    assert rec.committed == []


def test_hiding_the_graph_mid_drag_cancels_it(adsr):
    rec = Recorder(adsr)
    start = adsr.handle_positions()[0]
    press(adsr, start)
    move(adsr, start + QPointF(30, 0))
    adsr.hide()
    assert not adsr.is_dragging() and adsr.values()["attack"] == 25 and rec.cancelled == 1


def test_double_clicking_a_handle_resets_what_it_controls(adsr):
    rec = Recorder(adsr)
    double_click(adsr, adsr.handle_positions()[1])  # decay 50 is the default; sustain 70 is not (default 99)
    assert adsr.values()["sustain"] == 99 and adsr.values()["decay"] == 50
    assert rec.live == [{"sustain": 99}] and rec.committed == [{"sustain": 99}]
    double_click(adsr, adsr.handle_positions()[1])  # already default
    assert len(rec.committed) == 1


def test_a_reload_during_a_drag_does_not_fight_the_pointer(adsr):
    start = adsr.handle_positions()[0]
    press(adsr, start)
    move(adsr, start + QPointF(30, 0))
    dragged = adsr.values()["attack"]
    adsr.set_values(1, 1, 1, 1)
    assert adsr.values()["attack"] == dragged
    release(adsr, start + QPointF(30, 0))
    adsr.set_values(1, 1, 1, 1)  # after the drag it is allowed again
    assert adsr.values()["attack"] == 1


def test_dragging_shows_no_tooltip_over_the_graph(adsr, monkeypatch):
    # a floating readout got in the way of the graph; the knobs show the numbers
    shown = []
    monkeypatch.setattr(QToolTip, "showText", lambda *a, **k: shown.append(a))
    start = adsr.handle_positions()[0]
    press(adsr, start)
    move(adsr, start + QPointF(30, 0))
    release(adsr, start + QPointF(30, 0))
    assert shown == [] and adsr.toolTip() == ""


def _on_sustain_line(graph, dx=0):
    # a point on the flat sustain line, away from the handle at its left end
    w, h = graph._inner_size()
    points = graph._curve_points(w, h)
    (ax, ay), (bx, _by) = points[2], points[3]
    return QPointF((ax + bx) / 2 + 8 + dx, ay + 8)  # (+ the inset)


def test_the_sustain_line_drags_up_and_down_and_changes_only_the_sustain(adsr):
    rec = Recorder(adsr)
    start = _on_sustain_line(adsr)
    assert not adsr._handles_at(start)  # not a handle
    press(adsr, start)
    assert adsr.is_dragging()
    move(adsr, start + QPointF(25, -20))  # sideways movement is ignored: the line only moves up and down
    release(adsr, start + QPointF(25, -20))
    values = adsr.values()
    assert values["sustain"] > 70 and (values["attack"], values["decay"], values["release"]) == (25, 50, 45)
    assert rec.live and all(set(v) == {"sustain"} for v in rec.live) and rec.committed == [{"sustain": values["sustain"]}]


def test_the_line_does_not_jump_to_the_pointer_and_the_handle_at_its_end_still_wins(adsr):
    rec = Recorder(adsr)
    start = _on_sustain_line(adsr) + QPointF(0, 3)  # a little off the line's exact height
    press(adsr, start)
    move(adsr, start)
    assert rec.live == [] and adsr.values()["sustain"] == 70
    release(adsr, start)
    end = adsr.handle_positions()[1]  # the end of the decay is a handle, and sits on the line's left end
    press(adsr, end + QPointF(3, 0))
    move(adsr, end + QPointF(3, 0) + QPointF(20, 0))
    assert "decay" in {n for v in rec.live for n in v}  # the handle took it, not the line


def test_the_sustain_line_shift_esc_and_double_click_work_like_the_handles(adsr):
    rec = Recorder(adsr)
    start = _on_sustain_line(adsr)
    press(adsr, start, shift=True)
    move(adsr, start + QPointF(0, -20), shift=True)
    fine = adsr.values()["sustain"]
    key(adsr, Qt.Key.Key_Escape)
    assert adsr.values()["sustain"] == 70 and rec.cancelled == 1 and rec.committed == []
    press(adsr, start)
    move(adsr, start + QPointF(0, -20))
    coarse = adsr.values()["sustain"]
    release(adsr, start + QPointF(0, -20))
    assert 70 < fine < coarse
    rec.committed.clear()
    adsr.set_values(25, 50, 40, 45)
    double_click(adsr, _on_sustain_line(adsr))
    assert adsr.values()["sustain"] == 99 and rec.committed[-1] == {"sustain": 99}


def test_the_yamaha_level_graphs_sustain_line_drags_too(qapp):
    graph = LevelEnvelopeGraph()
    graph.setFixedSize(*GRAPH_SIZE)
    graph.set_values(0, 100, 50, 0, 90, 90, 90)
    graph.set_editable(True)
    rec = Recorder(graph)
    start = _on_sustain_line(graph)
    press(graph, start)
    move(graph, start + QPointF(0, 25))
    release(graph, start + QPointF(0, 25))
    assert set(rec.committed[0]) == {"sustain_level"} and rec.committed[0]["sustain_level"] < 50


def test_a_graph_without_a_flat_part_has_no_line_to_drag(qapp):
    graph = Envelope2Graph()
    graph.setFixedSize(*GRAPH_SIZE)
    graph.set_values(20, 99, 40, 60, 30, 80, 45, 0)
    graph.set_editable(True)
    w, h = graph._inner_size()
    points = graph._curve_points(w, h)
    mid = QPointF((points[1][0] + points[2][0]) / 2 + 8, (points[1][1] + points[2][1]) / 2 + 8)
    press(graph, mid)
    assert not graph.is_dragging()


def test_overlapping_handles_are_told_apart_by_the_direction_of_the_drag(qapp):
    # an instant attack and decay (0, 0) with full sustain: the attack peak and the decay's end are the same point
    def graph():
        g = ADSREnvelopeGraph()
        g.setFixedSize(*GRAPH_SIZE)
        g.set_values(0, 0, 99, 45)
        g.set_editable(True)
        return g

    sideways = graph()
    start = sideways.handle_positions()[0]
    assert sideways.handle_positions()[1] == start
    rec = Recorder(sideways)
    press(sideways, start)
    move(sideways, start + QPointF(1, 0))  # not far enough to decide: nothing moves yet
    assert rec.live == []
    move(sideways, start + QPointF(30, 2))
    release(sideways, start + QPointF(30, 2))
    assert set(rec.committed[0]) == {"attack"}  # dragging right pulls out the ATTACK

    down = graph()
    rec = Recorder(down)
    press(down, start)
    move(down, start + QPointF(1, 25))
    release(down, start + QPointF(1, 25))
    assert "sustain" in rec.committed[0] and "attack" not in rec.committed[0]  # dragging down pulls the SUSTAIN level


def test_env2_and_level_graphs_drag_too(qapp):
    env2 = Envelope2Graph()
    env2.setFixedSize(*GRAPH_SIZE)
    env2.set_values(20, 99, 40, 60, 30, 80, 45, 0)
    env2.set_editable(True)
    rec = Recorder(env2)
    start = env2.handle_positions()[1]
    press(env2, start)
    move(env2, start + QPointF(10, 12))
    release(env2, start + QPointF(10, 12))
    assert set(rec.committed[0]) == {"rate2", "level2"}

    level = LevelEnvelopeGraph()
    level.setFixedSize(*GRAPH_SIZE)
    level.set_values(0, 100, 50, 0, 100, 100, 100)
    level.set_editable(True)
    rec = Recorder(level)
    start = level.handle_positions()[0]  # the init level: up and down only
    press(level, start)
    move(level, start + QPointF(25, -20))
    release(level, start + QPointF(25, -20))
    assert set(rec.committed[0]) == {"init"} and rec.committed[0]["init"] > 0


# --- the editors ------------------------------------------------------------------------------------------------------------------------------


def _spread_feg(tab):
    # the factory sample's filter envelope is all instant stages, i.e. every handle on one point: give the knobs distinct values, as the unit would hold
    for key, value in (("init_level", -40), ("attack_level", 80), ("sustain_level", 30), ("release_level", -20), ("attack_rate", 70), ("decay_rate", 60), ("release_rate", 50)):
        tab.panel.set_value_quietly(f"feg_{key}", value)
    tab._update_graphs()


def _drag(graph, handle, dx, dy):
    start = graph.handle_positions()[handle]
    press(graph, start)
    move(graph, start + QPointF(dx, dy))
    return start + QPointF(dx, dy)


def test_akai_the_knobs_follow_at_once_and_the_sampler_hears_nothing_until_the_release(editor, qapp):
    _select_keygroup_1(editor, qapp)
    graph = editor.env1_graph
    assert graph.is_editable()
    graph.show()
    end = _drag(graph, 0, 40, 0)
    attack = graph.values()["attack"]
    assert attack > 0 and editor.attack1_knob.value() == attack and editor.attack1_value_label.text() == str(attack)
    # live: no write is scheduled, let alone sent
    assert not getattr(editor, "_pending_writes", {}) and editor._bridge.set_parameter_calls == []
    editor._worker.wait_until_idle()
    assert editor._bridge.set_parameter_calls == []
    release(graph, end)
    editor._worker.wait_until_idle()
    assert [c for c in editor._bridge.set_parameter_calls if c[0] == "ATTAK1"] == [("ATTAK1", 0, attack, 1)]


def test_akai_dragging_the_sustain_line_writes_only_the_sustain_on_release(editor, qapp):
    _select_keygroup_1(editor, qapp)
    graph = editor.env1_graph
    graph.show()
    graph.set_values(25, 50, 70, 45)
    start = _on_sustain_line(graph)
    press(graph, start)
    move(graph, start + QPointF(0, 20))
    sustain = graph.values()["sustain"]
    assert sustain < 70 and editor.sustain1_knob.value() == sustain and editor.sustain1_value_label.text() == str(sustain)
    editor._worker.wait_until_idle()
    assert editor._bridge.set_parameter_calls == []
    release(graph, start + QPointF(0, 20))
    editor._worker.wait_until_idle()
    assert editor._bridge.set_parameter_calls == [("SUSTN1", 0, sustain, 1)]


def test_akai_a_two_value_drag_writes_both_values_once_each(editor, qapp):
    _select_keygroup_1(editor, qapp)
    graph = editor.env2_graph
    end = _drag(graph, 1, 15, -10)
    release(graph, end)
    editor._worker.wait_until_idle()
    written = [c[0] for c in editor._bridge.set_parameter_calls]
    assert sorted(written) == ["ENV2L2", "ENV2R2"]


def test_akai_esc_puts_the_knobs_back_and_writes_nothing(editor, qapp):
    _select_keygroup_1(editor, qapp)
    graph = editor.env1_graph
    before = editor.attack1_knob.value()
    end = _drag(graph, 0, 40, 0)
    assert editor.attack1_knob.value() != before
    key(graph, Qt.Key.Key_Escape)
    assert editor.attack1_knob.value() == before and editor.attack1_value_label.text() == str(before)
    release(graph, end)
    editor._worker.wait_until_idle()
    assert editor._bridge.set_parameter_calls == []


def test_akai_double_click_resets_to_the_knobs_default_and_writes_it(editor, qapp):
    _select_keygroup_1(editor, qapp)
    graph = editor.env1_graph
    graph.set_values(10, 10, 10, 10)
    double_click(graph, graph.handle_positions()[0])
    editor._worker.wait_until_idle()
    assert editor.attack1_knob.value() == editor.attack1_knob.defaultValue()
    assert [c for c in editor._bridge.set_parameter_calls if c[0] == "ATTAK1"][-1][2] == editor.attack1_knob.defaultValue()


def test_akai_the_graphs_are_inert_until_the_selected_keygroups_values_have_loaded(editor, qapp):
    _select_keygroup_1(editor, qapp)
    assert all(g.is_editable() for g in editor._envelope_graphs)
    editor.keygroup_list.setCurrentRow(0)  # the load has not been serviced yet
    assert not any(g.is_editable() for g in editor._envelope_graphs)
    editor._worker.wait_until_idle()
    from test_program_editor_window import _pump_until

    _pump_until(qapp, lambda: all(g.is_editable() for g in editor._envelope_graphs))


def test_akai_s1000_envelope_2_adsr_graph_is_draggable_too(editor, qapp):
    # the 4-stage graph is the S3000's: the S1000's second envelope is the ADSR graph, wired the same way when it is built
    assert editor.env1_graph in editor._envelope_graphs and editor.env2_graph in editor._envelope_graphs
    assert len(editor._envelope_graphs) == 2  # (the S1000 window adds env2_adsr_graph: tests/test_program_editor_window_s1000.py)


def test_yamaha_dragging_moves_the_knobs_and_sends_nothing_until_the_release(yamaha_win, fake, qapp):  # noqa: F811
    tab = yamaha_win.samples_tab
    tab.select_sample("sine wave")
    assert wait_until(lambda: tab.name_label.text() == "sine wave" and not tab.cards_scroll.isHidden())
    graph = tab.panel.envelope_graphs["feg"]
    assert graph.is_editable()
    _spread_feg(tab)
    graph.show()
    qapp.processEvents()
    edits = fake.edits
    end = _drag(graph, 1, 30, -25)  # the attack peak: attack rate and attack level
    knobs = tab.panel
    assert knobs.value("feg_attack_rate") == graph.values()["attack_rate"] and knobs.value("feg_attack_level") == graph.values()["attack_level"]
    assert knobs.widgets["feg_attack_level"].property("valueReadout").text() == str(graph.values()["attack_level"])
    wait_until(lambda: False, timeout=0.4)  # give any (wrong) write time to happen
    assert fake.edits == edits and not tab._writer.busy
    release(graph, end)
    assert wait_until(lambda: fake.edits == edits + 2 and not tab._writer.busy)


def test_yamaha_the_amplitude_graph_edits_the_units_rates_not_the_graphs_times(yamaha_win, fake, qapp):  # noqa: F811
    tab = yamaha_win.samples_tab
    tab.select_sample("sine wave")
    assert wait_until(lambda: tab.name_label.text() == "sine wave" and not tab.cards_scroll.isHidden())
    graph = tab.panel.envelope_graphs["aeg"]
    tab.panel.set_value_quietly("aeg_attack_rate", 100)  # not the factory instant attack, whose handle sits on the decay's
    tab._update_graphs()
    graph.show()
    qapp.processEvents()
    end = _drag(graph, 0, 60, 0)  # the attack peak, pulled right: a slower attack = a lower rate
    rate = tab.panel.value("aeg_attack_rate")
    assert rate == yamaha_adsr_rates(*graph.values().values())[0] and rate < 127
    release(graph, end)
    assert wait_until(lambda: not tab._writer.busy)
    assert tab.panel.value("aeg_attack_rate") == rate


def test_yamaha_declining_the_first_write_warning_puts_the_knobs_and_graph_back(yamaha_win, fake, warning_acknowledged, monkeypatch, qapp):  # noqa: F811
    # the one-time warning appears at the RELEASE (the first write), never mid-drag
    tab = yamaha_win.samples_tab
    tab.select_sample("sine wave")
    assert wait_until(lambda: tab.name_label.text() == "sine wave" and not tab.cards_scroll.isHidden())
    warning_acknowledged["ack"] = False
    asked = []
    monkeypatch.setattr(writer_module.QMessageBox, "question", lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.Cancel)
    graph = tab.panel.envelope_graphs["feg"]
    held = tab.panel.value("feg_attack_rate")  # what the unit holds
    _spread_feg(tab)  # (knobs only: nothing was written)
    graph.show()
    qapp.processEvents()
    end = _drag(graph, 1, 30, -25)
    assert asked == [] and tab.panel.value("feg_attack_rate") != held  # moved, not asked
    release(graph, end)
    assert len(asked) >= 1
    assert tab.panel.value("feg_attack_rate") == held and graph.values()["attack_rate"] == held  # all back to what the unit holds
    assert fake.edits == 0


def test_yamaha_a_graph_for_a_sample_that_is_not_shown_is_inert(yamaha_win, qapp):  # noqa: F811
    tab = yamaha_win.samples_tab
    assert not any(g.is_editable() for g in tab.panel.envelope_graphs.values())


def test_s950_dragging_edits_the_working_copy_and_nothing_is_written(s950_win, qapp):  # noqa: F811
    win = s950_win
    assert not win.amp_graph.is_editable()  # no program shown yet
    _show(win, 0)
    assert win.amp_graph.is_editable() and win.filter_graph.is_editable()
    win.amp_graph.show()
    qapp.processEvents()
    kg = win._working.keygroups[0]
    before = kg.attack
    end = _drag(win.amp_graph, 0, 40, 0)
    assert win._working.keygroups[0].attack == win.amp_graph.values()["attack"] != before
    assert win.is_dirty() and win.rig.fake.prgm_writes == 0
    release(win.amp_graph, end)
    assert win.rig.fake.prgm_writes == 0  # staged: only Write sends
    key(win.amp_graph, Qt.Key.Key_Escape)  # (no drag any more: harmless)
