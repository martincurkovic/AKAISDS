# tests for core/s950_params.py - which program fields may change, their limits, and the
# diff / validate / apply helpers a write is built from.

import dataclasses

import pytest

from core import s950_params as sp
from core.s950_program import Keygroup, Program


def program(n=2, **kw):
    return Program(name="TEST", keygroups=[Keygroup(**kw) for _ in range(n)])


def with_kg(prog, index, **fields):
    kgs = list(prog.keygroups)
    kgs[index] = dataclasses.replace(kgs[index], **fields)
    return dataclasses.replace(prog, keygroups=kgs)


def test_every_offered_keygroup_field_is_a_real_attribute_with_a_rule():
    kg = Keygroup()
    for attr in sp.KEYGROUP_FIELDS:
        assert hasattr(kg, attr)
        assert attr in sp.KEYGROUP_RANGES or attr in ("soft_sample", "loud_sample", "voice_out")
    prog = Program()
    for attr in sp.PROGRAM_FIELDS:
        assert hasattr(prog, attr)


def test_unverified_bit_fields_are_not_offered():
    assert "control_bits" not in sp.KEYGROUP_FIELDS
    assert "vel_xfade_50" not in sp.KEYGROUP_FIELDS


def test_the_defaults_are_inside_their_own_ranges():
    kg = Keygroup()
    for attr, (lo, hi) in sp.KEYGROUP_RANGES.items():
        assert lo <= getattr(kg, attr) <= hi, attr


def test_diff_finds_exactly_what_changed():
    base = program()
    edited = with_kg(dataclasses.replace(base, key_tilt=5), 1, attack=9, soft_sample="KICK")
    changes = sp.diff_programs(base, edited)
    assert [(c.keygroup, c.attr, c.old, c.new) for c in changes] == [
        (None, "key_tilt", 0, 5),
        (1, "attack", 0, 9),
        (1, "soft_sample", "", "KICK"),
    ]
    assert sp.diff_programs(base, base) == []


def test_diff_ignores_fields_that_are_not_offered():
    base = program()
    edited = with_kg(base, 0, control_bits=63, vel_xfade_50=1)
    assert sp.diff_programs(base, edited) == []


def test_diff_refuses_a_changed_keygroup_count():
    with pytest.raises(ValueError, match="number of keygroups"):
        sp.diff_programs(program(2), program(3))


@pytest.mark.parametrize(
    "attr,value",
    [("attack", 100), ("attack", -1), ("release_vel", 51), ("midi_offset", 16),
     ("lower_key", 23), ("soft_transpose", 385), ("velocity_switch", 129)],
)
def test_out_of_range_values_are_problems(attr, value):
    base = program()
    problems = sp.validate_changes(sp.diff_programs(base, with_kg(base, 0, **{attr: value})))
    assert len(problems) == 1 and "Keygroup 1" in problems[0]


@pytest.mark.parametrize(
    "attr,value",
    [("attack", 99), ("attack", 0), ("release_vel", -50), ("velocity_switch", 128),
     ("soft_transpose", -384), ("voice_out", 255), ("voice_out", 9), ("lower_key", 24)],
)
def test_edge_values_are_fine(attr, value):
    base = program()
    assert sp.validate_changes(sp.diff_programs(base, with_kg(base, 0, **{attr: value}))) == []


def test_voice_out_must_be_all_or_0_to_9():
    base = program()
    for bad in (10, 100, 254):
        assert sp.validate_changes(sp.diff_programs(base, with_kg(base, 0, voice_out=bad)))


def test_names_must_be_short_plain_ascii():
    base = program()
    assert sp.validate_changes(sp.diff_programs(base, dataclasses.replace(base, name="X" * 11)))
    assert sp.validate_changes(sp.diff_programs(base, dataclasses.replace(base, name="café")))
    assert sp.validate_changes(sp.diff_programs(base, with_kg(base, 0, soft_sample="X" * 11)))
    assert sp.validate_changes(sp.diff_programs(base, dataclasses.replace(base, name="OK NAME 01"))) == []


def test_header_ranges():
    base = program()
    assert sp.validate_changes(sp.diff_programs(base, dataclasses.replace(base, midi_program_number=128)))
    assert sp.validate_changes(sp.diff_programs(base, dataclasses.replace(base, key_tilt=-51)))
    assert sp.validate_changes(sp.diff_programs(base, dataclasses.replace(base, key_tilt=-50))) == []


def test_only_changed_fields_are_checked():
    # the unit already holds a value beyond our assumed range: it may stay
    base = with_kg(program(), 0, filter_key_track=120)
    edited = with_kg(base, 0, attack=5)
    assert sp.validate_changes(sp.diff_programs(base, edited)) == []


def test_inverted_key_range_is_a_whole_program_problem():
    bad = with_kg(program(), 0, lower_key=100, upper_key=30)
    assert any("lower key is above" in p for p in sp.program_problems(bad))
    assert sp.program_problems(program()) == []


def test_apply_changes_touches_only_the_changed_fields_and_keeps_raw():
    base = program()
    kg_raw = bytearray(base.keygroups[0].raw)
    kg_raw[112] = 77
    base = with_kg(base, 0, raw=bytes(kg_raw))
    changes = sp.diff_programs(base, with_kg(base, 0, decay=3))
    fresh = with_kg(base, 1, attack=44)  # a different state than `base`, like a front-panel edit
    out = sp.apply_changes(fresh, changes)
    assert out.keygroups[0].decay == 3 and out.keygroups[1].attack == 44
    assert out.keygroups[0].raw[112] == 77
    assert fresh.keygroups[0].decay != 3  # the input wasn't modified


def test_apply_changes_refuses_a_keygroup_that_is_not_there():
    changes = sp.diff_programs(program(3), with_kg(program(3), 2, attack=1))
    with pytest.raises(ValueError, match="reload"):
        sp.apply_changes(program(2), changes)
