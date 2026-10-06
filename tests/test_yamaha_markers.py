# tests for core/yamaha_markers.py - the order in which the four wave/loop addresses may be written (the unit silently ignores a write
# that would break start <= loop_start <= loop_end <= end).

import itertools

import pytest

from core import demo_a4000 as demo
from core import yamaha_markers as ym
from core import yamaha_params as yp

M = ym.Markers


def simulate(current, steps):
    """The unit's rule (MEASURED): a write that would break the order is ignored; otherwise the address changes."""
    now = {"start": current.start, "loop_start": current.loop_start, "loop_end": current.loop_end, "end": current.end}
    ignored = []
    for name, value in steps:
        tried = dict(now, **{name: value})
        if M(tried["start"], tried["loop_start"], tried["loop_end"], tried["end"]).valid:
            now = tried
        else:
            ignored.append((name, value))
    return M(now["start"], now["loop_start"], now["loop_end"], now["end"]), ignored


def test_nothing_to_do_when_the_markers_already_match():
    assert ym.plan_marker_writes(M(0, 100, 900, 1000), M(0, 100, 900, 1000)) == []


def test_only_the_changed_markers_are_written():
    assert ym.plan_marker_writes(M(0, 100, 900, 1000), M(50, 100, 900, 1000)) == [("start", 50)]
    assert ym.plan_marker_writes(M(0, 100, 900, 1000), M(0, 100, 800, 1000)) == [("loop_end", 800)]


def test_every_reachable_target_is_reached_and_no_step_is_ever_ignored():
    # exhaustively over a small grid: any valid current -> any valid target, simulating the unit's own rule
    values = range(0, 9, 2)
    valid = [M(*c) for c in itertools.product(values, repeat=4) if M(*c).valid]
    for current in valid:
        for target in valid:
            steps = ym.plan_marker_writes(current, target)
            final, ignored = simulate(current, steps)
            assert ignored == [], (current, target, steps)
            assert final == target, (current, target, steps)
            assert len(steps) <= 8


def test_the_outer_region_widens_first_and_narrows_last():
    # shrinking the wave end below the OLD loop end: the loop must come inside first
    steps = ym.plan_marker_writes(M(0, 1000, 3000, 3996), M(0, 1000, 2000, 2500))
    assert steps == [("loop_end", 2000), ("end", 2500)]
    # growing it: the end first, then the loop
    steps = ym.plan_marker_writes(M(0, 1000, 2000, 2500), M(0, 1000, 3000, 3996))
    assert steps == [("end", 3996), ("loop_end", 3000)]


def test_a_target_out_of_order_is_refused():
    for bad in (M(10, 5, 20, 30), M(0, 10, 5, 30), M(0, 10, 20, 15), M(-1, 0, 1, 2)):
        with pytest.raises(ValueError):
            ym.plan_marker_writes(M(0, 1, 2, 3), bad)


def test_markers_come_from_the_rows_and_convert_to_the_views_inclusive_frames():
    data = bytearray(demo.make_sample_payload("x"))  # 128 frames, looping 0..128
    assert ym.read_markers(data) == M(0, 0, 128, 128)
    assert ym.view_markers(data) == (0, 0, 127, 127)  # the two END markers are one past the last frame in the unit's numbers
    yp.store(yp.get("sample", "loop_start_address"), data, 10)
    yp.store(yp.get("sample", "loop_end_address"), data, 100)
    assert ym.view_markers(data) == (0, 10, 99, 127)
    assert ym.address_from_frame("end", 127) == 128 and ym.address_from_frame("loop_start", 10) == 10
    assert ym.frame_from_address("loop_end", 100) == 99
