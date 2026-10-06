# tests for core/yamaha_params.py - the A4000/A5000 parameter tables.
#
# Two kinds of test: (1) the table is internally consistent (no overlapping bytes, nothing past its
# block, unique keys/addresses) - catches transcription slips offline; (2) decoding the REAL captures
# in tests/fixtures/a4000/ gives the values measured on the unit. The full request-vs-bulk check
# against live hardware is tools/a4000_verify_params.py (not run here).

import pathlib

import pytest

from core import yamaha_params as yp
from core import yamaha_sysex as y

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "a4000"


def _dump(name):
    return y.parse_bulk_dump(y.split_messages((FIXTURES / name).read_bytes())[0])


ALL = [(scope, p) for scope in yp.SCOPES for p in yp.rows(scope)]


def test_keys_are_unique_per_scope():
    for scope in yp.SCOPES:
        keys = [p.key for p in yp.rows(scope)]
        assert len(keys) == len(set(keys)), scope


def test_request_addresses_are_unique_per_scope():
    for scope in yp.SCOPES:
        seen = {}
        for p in yp.rows(scope):
            if p.bulk_only:
                continue
            assert p.p not in seen, f"{p.key} and {seen[p.p]} share P={p.p}"
            seen[p.p] = p.key


@pytest.mark.parametrize("scope,param", ALL, ids=[f"{s}.{p.key}" for s, p in ALL])
def test_each_row_is_well_formed(scope, param):
    assert param.scope == scope
    assert len(param.p) == 6 and all(0 <= v <= 0x7F for v in param.p)
    assert param.p[0] in (1, 2)
    if param.kind == "int":
        assert param.size in (1, 2, 4) and param.lo <= param.hi
        if param.bits:
            shift, width = param.bits
            assert param.size == 1 and shift >= 0 and width >= 1 and shift + width <= 8
    else:
        assert param.size in (8, 16) and param.read_only
    if param.enum:
        assert param.enum in yp.ENUMS


def _block_limit(scope):
    return {"program": yp.PROGRAM_EASY_EDIT_BASE, "easy_edit": yp.EASY_EDIT_BLOCK_SIZE, "sample": 224}[scope]


def test_nothing_runs_past_its_block():
    for scope, p in ALL:
        assert p.offset >= 0 and p.offset + p.bulk_size <= _block_limit(scope), p.key


def test_no_two_rows_overlap_in_the_bulk():
    # byte -> list of bit ranges claimed; a plain row claims all 8 bits of each byte it covers
    for scope in yp.SCOPES:
        claims = {}
        for p in yp.rows(scope):
            bits = p.bits or (0, 8)
            for byte in range(p.offset, p.offset + p.bulk_size):
                lo, width = bits
                mask = ((1 << width) - 1) << lo
                assert not claims.get(byte, 0) & mask, f"{scope}.{p.key} overlaps another row at byte {byte}"
                claims[byte] = claims.get(byte, 0) | mask


def test_easy_edit_requests_carry_the_slot_in_p2_and_p3():
    level = yp.get("easy_edit", "level_offset")
    assert yp.request_params(level, 0) == (2, 0, 0, 3, 0, 0)
    assert yp.request_params(level, 7) == (2, 0, 7, 3, 0, 0)
    assert yp.request_params(level, 123) == (2, 1, 23, 3, 0, 0)
    assert yp.bulk_offset(level, 0) == 408 + 22
    assert yp.bulk_offset(level, 2) == 408 + 2 * 56 + 22
    with pytest.raises(ValueError):
        yp.request_params(level)
    with pytest.raises(ValueError):
        yp.request_params(yp.get("easy_edit", "assigned_type"), 0)  # bulk only


def test_other_scopes_ignore_the_slot():
    level = yp.get("program", "program_level")
    assert yp.request_params(level) == (1, 10, 0, 0, 0, 0) and yp.bulk_offset(level) == 83
    key = yp.get("sample", "key_range_high")
    assert yp.bulk_offset(key) == 112 + 58


# --- decoding the real captures -----------------------------------------------------------------


def test_program_values_decode_to_what_the_unit_reported():
    data = _dump("program_001_level30.syx").data
    expected = {
        "program_name": "Pgm 001",
        "program_level": 127,
        "transpose": 0,
        "lfo_tempo": 120,
        "lfo_reset_note": -1,
        "lfo_reset_midi_channel": -2,
        "portamento_rate": 90,
        "portamento_time": 90,
        "sh_speed": 39,
        "assigned_samples": 1,
        "lfo_cycle": 5,
    }
    for key, value in expected.items():
        assert yp.extract(yp.get("program", key), data) == value, key


def test_easy_edit_slot_zero_decodes_the_front_panel_changes():
    data = _dump("program_001_level30.syx").data
    get = lambda key: yp.extract(yp.get("easy_edit", key), data, 0)  # noqa: E731
    assert get("assigned_name") == "sine wave"
    assert get("assigned_type") == y.OBJECT_TYPES["sample"]
    assert get("receive_channel") == 0  # MIDI channel 01
    assert get("level_offset") == 30
    assert get("pan_offset") == 0
    # an empty slot is "=sample" everywhere it is a choice
    assert yp.extract(yp.get("easy_edit", "receive_channel"), data, 1) == -1
    assert yp.extract(yp.get("easy_edit", "assigned_name"), data, 1) == ""


def test_sample_values_decode_to_what_the_unit_reported():
    data = _dump("sample_sine_wave.syx").data
    expected = {
        "original_key_l": 66,
        "sampling_frequency_l": 48000,
        "fine_tune_l": -20,
        "key_range_high": 127,
        "key_range_low": 0,
        "loop_mode": 1,
        "wave_start_address": 0,
        "wave_length": 128,
        "filter_type": 0,
        "filter_cutoff": 127,
        "filter_q": 4,
        "sample_level": 100,
        "pan": 0,
        "pitch_bend_range": 2,
        "aeg_sustain_level": 127,
    }
    for key, value in expected.items():
        assert yp.extract(yp.get("sample", key), data) == value, key


def test_a_parameter_reply_decodes_like_the_bulk_does():
    # reply to P=(2,0,0,3,0,0) on the unit: Level offset +30 (value byte 0x1e, nibbled 01 0e)
    reply = y.parse_parameter_message(bytes.fromhex("43 10 58 01 02 00 00 03 00 00 01 0e"))
    level = yp.get("easy_edit", "level_offset")
    assert reply.params == yp.request_params(level, 0)
    assert yp.decode_reply(level, reply.data) == 30
    assert yp.decode_reply(yp.get("sample", "fine_tune_l"), b"\xec") == -20
    assert yp.decode_reply(yp.get("sample", "sampling_frequency_l"), b"\xbb\x80") == 48000


def test_signed_two_bit_fields_read_minus_one():
    # Easy Edit's portamento/mono/x-fade are -1/0/1 packed in 2 bits: 0b11 = -1 ("=sample")
    data = bytearray(yp.PROGRAM_EASY_EDIT_BASE + yp.EASY_EDIT_BLOCK_SIZE)
    data[yp.PROGRAM_EASY_EDIT_BASE + 35] = 0b00_11_01_11
    assert yp.extract(yp.get("easy_edit", "portamento"), data, 0) == -1
    assert yp.extract(yp.get("easy_edit", "mono_mode"), data, 0) == 1
    assert yp.extract(yp.get("easy_edit", "key_xfade_on"), data, 0) == -1


def test_every_enum_row_has_a_label_for_each_value_the_a4000_can_hold():
    # values above 16 on the channel enums are the A5000's MIDI-B channels - the only labels allowed to be missing
    for scope, param in ALL:
        if not param.enum:
            continue
        labels = yp.ENUMS[param.enum]
        missing = [v for v in range(param.lo, param.hi + 1) if v not in labels]
        assert all(v > 16 for v in missing), (param.key, missing)
        if "channel" not in param.enum:
            assert not missing, (param.key, missing)


def test_rows_the_unit_ignored_writes_to_are_flagged():
    flagged = {p.key for p in yp.rows("sample") if p.write_ignored}
    assert flagged == {"sampling_frequency_l", "sampling_frequency_r", "wave_length", "wave_end_address"}
    assert not any(p.write_ignored for p in yp.rows("program") + yp.rows("easy_edit"))


def test_range_check_and_enums():
    assert yp.in_range(yp.get("sample", "filter_cutoff"), 127)
    assert not yp.in_range(yp.get("sample", "filter_cutoff"), 128)
    assert not yp.in_range(yp.get("program", "assigned_samples"), 1)  # read-only
    assert yp.ENUMS["filter_type"][0] == "Bypass" and len(yp.ENUMS["filter_type"]) == 17
    assert yp.ENUMS["output1"][1] == "stereo out" and yp.ENUMS["output2"][6] == "stereo out"


def test_store_is_the_inverse_of_extract():
    data = bytearray(yp.PROGRAM_EASY_EDIT_BASE + 2 * yp.EASY_EDIT_BLOCK_SIZE)
    for scope_key, slot, value in [
        (("program", "program_level"), None, 77),
        (("program", "transpose"), None, -12),
        (("program", "lfo_cycle"), None, 3),  # a bitfield sharing its byte with others
        (("program", "lfo_wave"), None, 5),
        (("easy_edit", "level_offset"), 1, -40),
        (("easy_edit", "portamento"), 1, -1),  # signed 2-bit
        (("easy_edit", "assigned_name"), 1, "snare"),
    ]:
        param = yp.get(*scope_key)
        yp.store(param, data, value, slot)
        assert yp.extract(param, data, slot) == value, scope_key
    # the two LFO bitfields share a byte and must not disturb each other
    assert yp.extract(yp.get("program", "lfo_cycle"), data) == 3
    # ...nor the neighbours
    assert yp.extract(yp.get("easy_edit", "level_offset"), data, 0) == 0


def test_store_refuses_to_run_past_the_payload():
    with pytest.raises(ValueError):
        yp.store(yp.get("program", "program_level"), bytearray(10), 1)


def test_linked_programs_reads_the_128_bit_map():
    data = bytearray(112 + 224)
    assert yp.linked_programs(data) == []
    data[112 + 24 : 112 + 28] = (1).to_bytes(4, "big")  # program 001 - what the real unit showed for "sine wave"
    assert yp.linked_programs(data) == [1]
    data[112 + 28 : 112 + 32] = (1 << 31 | 2).to_bytes(4, "big")  # words are programs 33-64: bit 0 = 33, bit 31 = 64
    data[112 + 36 : 112 + 40] = (1 << 31).to_bytes(4, "big")  # program 128
    assert yp.linked_programs(data) == [1, 34, 64, 128]
    assert yp.ENUMS["lfo_reset_channel"][-2] == "Off"


def test_a_samples_right_channel_wave_name_marks_it_stereo():
    from core import demo_a4000 as demo

    mono = demo.make_sample_payload("sine wave")
    assert yp.wave_name_right(mono) == "" and not yp.is_stereo(mono)
    stereo = bytearray(mono)
    stereo[80:96] = b"SMP 000002R     "
    assert yp.wave_name_right(stereo) == "SMP 000002R" and yp.is_stereo(stereo)
