# tests for core/s950_program.py
# pure byte-level tests (no Qt, no MIDI). The two header captures are from
# s950tools (MIT, Copyright (c) 2026 Brandon Ivers) - see THIRD_PARTY_NOTICES.md

import pytest

from core import s950_program as p
from core import s950_sysex as s

# The first 56 wire bytes of two PRGM replies captured from a real S950
# (firmware 1.2a) over RS-232 by the s950tools author on 2026-06-10. Unlike
# everything else in these tests they are bytes a real device emitted, so
# they pin the header offset table: the always-255 reserved sentinel landing
# at offset 44 proves the alignment.
_CAPTURES = [
    (
        "54 00 4F 00 4E 00 45 00 20 00 50 00 52 00 47 00 52 00 4D 00 "
        "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 7E 00 44 01 "
        "00 00 00 00 7F 01 02 00 00 00 00 00 00 00 7F 01",
        "TONE PRGRM",
        2,
        0,
        True,
    ),
    (
        "54 00 45 00 43 00 48 00 4E 00 4F 00 20 00 32 00 20 00 20 00 "
        "00 00 20 00 20 00 20 00 20 00 20 00 19 00 00 00 18 01 48 01 "
        "00 00 01 00 7F 01 09 00 00 00 00 00 00 00 00 00",
        "TECHNO 2",
        9,
        0,
        False,
    ),
]


@pytest.mark.parametrize("hexstr, name, num_kgs, midi_program, enabled", _CAPTURES)
def test_header_offsets_against_real_captures(hexstr, name, num_kgs, midi_program, enabled):
    captured = bytes.fromhex(hexstr.replace(" ", ""))
    # the capture is only the first 56 bytes; pad out to a one-keygroup program
    payload = captured.ljust(p.PROGRAM_HEADER_SIZE, b"\x00") + p.Keygroup().to_bytes()
    assert s.decode_db(payload[44], payload[45]) == 255  # the sentinel
    assert s.decode_db(payload[46], payload[47]) == num_kgs  # the wire's own count byte
    program = p.Program.from_payload(payload)
    assert program.name == name
    assert program.midi_program_number == midi_program
    assert program.enable_midi_program is enabled
    # the wire's count byte (2 / 9 here) disagrees with the one-keygroup body;
    # the body wins, and the byte is rewritten to match on encode
    assert program.num_keygroups == 1
    assert s.decode_db(program.to_payload()[46], program.to_payload()[47]) == 1


def test_default_program_round_trips_byte_for_byte():
    program = p.Program(name="DEFAULT PR", keygroups=[p.Keygroup(), p.Keygroup()])
    payload = program.to_payload()
    assert len(payload) == p.PROGRAM_HEADER_SIZE + 2 * p.KEYGROUP_SIZE
    assert max(payload) <= 0x7F
    again = p.Program.from_payload(payload)
    assert again == program
    assert again.to_payload() == payload


def test_default_header_has_the_reserved_sentinel_and_seed_bytes():
    payload = p.Program().to_payload()
    assert (payload[44], payload[45]) == (0x7F, 0x01)  # DB(255)
    assert payload[36:40] == bytes([0x7E, 0x00, 0x44, 0x01])
    kg = payload[p.PROGRAM_HEADER_SIZE :]
    assert kg[80:84] == bytes([0x44, 0x01, 0x44, 0x01])


def test_every_modelled_keygroup_field_round_trips():
    kg = p.Keygroup(
        lower_key=36,
        upper_key=60,
        velocity_switch=100,
        attack=1,
        decay=2,
        sustain=3,
        release=4,
        filter_attack=5,
        filter_decay=6,
        filter_sustain=7,
        filter_release=8,
        filter_vel=9,
        filter_key_track=10,
        attack_vel=11,
        release_vel=-12,
        loudness_vel=13,
        pitch_warp_vel=14,
        pitch_warp_offset=-15,
        pitch_warp_recovery=16,
        adsr_to_vcf=-17,
        aftertouch_depth=18,
        modwheel_depth=19,
        lfo_build=20,
        lfo_rate=21,
        lfo_depth=22,
        control_bits=0b101010,
        voice_out=7,
        midi_offset=3,
        vel_xfade_50=23,
        soft_sample="SOFT",
        soft_transpose=-960,
        soft_filter=24,
        soft_loudness=-25,
        loud_sample="LOUD",
        loud_transpose=1234,
        loud_filter=26,
        loud_loudness=27,
    )
    data = kg.to_bytes()
    assert len(data) == p.KEYGROUP_SIZE
    assert p.Keygroup.from_bytes(data) == kg


def test_no_two_keygroup_fields_overlap():
    # each field owns its own bytes, otherwise setting one corrupts another
    spans = []
    for _attr, offset, _signed in p._KEYGROUP_DB_FIELDS:
        spans.append((offset, offset + 2))
    for _attr, offset in p._KEYGROUP_DW_FIELDS:
        spans.append((offset, offset + 4))
    for _attr, offset in p._KEYGROUP_NAME_FIELDS:
        spans.append((offset, offset + 20))
    spans.sort()
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert end <= start
    assert spans[-1][1] <= p.KEYGROUP_SIZE


def test_unmodelled_bytes_survive_a_round_trip():
    payload = bytearray(p.Program().to_payload())
    payload[40] = 0x33  # PrUndef4, in the header
    payload[p.PROGRAM_HEADER_SIZE + 112] = 0x21  # KyUndef3, in the keygroup
    program = p.Program.from_payload(bytes(payload))
    program.name = "RENAMED"
    program.keygroups[0].attack = 50
    out = program.to_payload()
    assert out[40] == 0x33
    assert out[p.PROGRAM_HEADER_SIZE + 112] == 0x21


def test_wire_count_byte_is_rewritten_from_the_real_keygroup_count():
    program = p.Program(keygroups=[p.Keygroup() for _ in range(5)])
    payload = program.to_payload()
    assert s.decode_db(payload[46], payload[47]) == 5


def test_program_message_carries_a_valid_checksum():
    program = p.Program(name="X")
    msg = s.build_akai_data(s.FUNC_PRGM, 4, program.to_payload())
    parsed = s.parse_akai(msg)
    assert parsed.function == s.FUNC_PRGM and parsed.num == 4
    assert p.Program.from_payload(parsed.payload) == program


def test_rejects_malformed_programs():
    with pytest.raises(ValueError, match="too short"):
        p.Program.from_payload(bytes(100))
    with pytest.raises(ValueError, match="multiple"):
        p.Program.from_payload(bytes(p.PROGRAM_HEADER_SIZE + p.KEYGROUP_SIZE + 5))
    with pytest.raises(ValueError, match="keygroups"):
        p.Program.from_payload(bytes(p.PROGRAM_HEADER_SIZE + 32 * p.KEYGROUP_SIZE))
    with pytest.raises(ValueError):
        p.Program(keygroups=[]).to_payload()
    with pytest.raises(ValueError):
        p.Program(keygroups=[p.Keygroup()] * 32).to_payload()
    with pytest.raises(ValueError):
        p.Keygroup.from_bytes(bytes(139))


def test_an_untouched_field_keeps_its_exact_bytes_even_if_the_unit_encoded_it_oddly():
    # a name padded with NULs (not spaces) must come back NUL-padded when it wasn't edited
    program = p.Program(name="ABC", keygroups=[p.Keygroup(soft_sample="KICK")])
    payload = bytearray(program.to_payload())
    for i in range(3, 10):  # header name: NUL padding instead of spaces
        payload[2 * i : 2 * i + 2] = bytes(s.encode_db(0))
    kg0 = p.PROGRAM_HEADER_SIZE
    for i in range(4, 10):  # soft sample name likewise
        off = kg0 + 48 + 2 * i
        payload[off : off + 2] = bytes(s.encode_db(0))
    reparsed = p.Program.from_payload(bytes(payload))
    assert reparsed.to_payload() == bytes(payload)  # byte-identical, no normalisation


def test_an_edited_field_is_re_encoded_and_only_that_field():
    payload = p.Program(keygroups=[p.Keygroup(attack=1)]).to_payload()
    program = p.Program.from_payload(payload)
    program.keygroups[0].attack = 50
    out = program.to_payload()
    changed = [i for i in range(len(out)) if out[i] != payload[i]]
    assert changed and all(p.PROGRAM_HEADER_SIZE + 6 <= i < p.PROGRAM_HEADER_SIZE + 8 for i in changed)


def test_a_changed_name_is_space_padded():
    program = p.Program.from_payload(p.Program(name="ABC").to_payload())
    program.name = "XY"
    assert p.Program.from_payload(program.to_payload()).name == "XY"
