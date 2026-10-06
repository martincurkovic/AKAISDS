# tests for core/yamaha_sysex.py - the Yamaha A4000/A5000 SysEx codec.
#
# tests/fixtures/a4000/*.syx are REAL captures from a user's A4000 (2026-10-06, device number 0, via
# tools/a4000_discovery.py). Several tests below pin facts measured on that unit - they are the
# reason the byte count is MSB-first and multi-block, so don't "simplify" them to the manual's wording.

import pathlib

import pytest

from core import yamaha_sysex as y

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "a4000"


def _message(name):
    messages = y.split_messages((FIXTURES / name).read_bytes())
    assert len(messages) == 1, name
    return messages[0]


def _raw(name):
    return (FIXTURES / name).read_bytes()


# --- low-level coding ----------------------------------------------------------------------------


def test_nibble_roundtrip_and_layout():
    assert y.nibble(b"\x12\xab") == bytes([0x1, 0x2, 0xA, 0xB])
    data = bytes(range(256))
    assert y.denibble(y.nibble(data)) == data


def test_denibble_rejects_odd_length_and_high_bytes():
    with pytest.raises(y.YamahaSysexError):
        y.denibble(b"\x01\x02\x03")
    with pytest.raises(y.YamahaSysexError):
        y.denibble(b"\x01\x12")


def test_names_are_padded_to_16_and_decoded_without_padding():
    assert y.pad_name("001") == b"001" + b" " * 13
    assert y.pad_name("x", fill=0) == b"x" + b"\x00" * 15
    assert y.decode_name(b"sine wave       ") == "sine wave"
    assert y.decode_name(b"\x00" * 16) == ""
    assert y.program_object_name(7) == "007"


@pytest.mark.parametrize("bad", ["a" * 17, "café", "tab\t"])
def test_bad_names_are_refused(bad):
    with pytest.raises(y.YamahaSysexError):
        y.pad_name(bad)


def test_values_are_big_endian_with_twos_complement():
    assert y.encode_value(48000, 2) == b"\xbb\x80"
    assert y.decode_value(b"\xbb\x80") == 48000
    assert y.encode_value(-20, 1, signed=True) == b"\xec"
    assert y.decode_value(b"\xec", signed=True) == -20
    assert y.encode_value(128, 4) == b"\x00\x00\x00\x80"
    with pytest.raises(y.YamahaSysexError):
        y.encode_value(128, 1, signed=True)
    with pytest.raises(y.YamahaSysexError):
        y.encode_value(-1, 2)


def test_xor_checksum_is_seven_bit():
    assert y.xor7(b"\x01\x02\x04") == 7
    assert y.xor7(b"\x7f\x7f") == 0


# --- identity ------------------------------------------------------------------------------------


def test_identity_request_matches_what_the_unit_answered():
    assert y.build_identity_request() == bytes([0x7E, 0x7F, 0x06, 0x01])


def test_identity_reply_from_a_real_a4000():
    reply = y.parse_identity_reply(_message("identity_reply.syx"))
    assert reply.model == "A4000"
    assert reply.family_number == 0x01DA
    assert reply.family_code == b"\x00\x41"
    assert reply.revision == bytes([0x16, 0x00, 0x00, 0x7F])


def test_identity_reply_rejects_other_messages():
    with pytest.raises(y.YamahaSysexError):
        y.parse_identity_reply(_message("program_001_empty.syx"))


# --- requests built byte for byte like the ones that worked ---------------------------------------


def test_dump_request_is_exactly_what_the_a4000_answered():
    # captured from the script: F0 43 20 7a "LM  0474" "PG" "001" + 13 spaces F7
    expected = bytes.fromhex(
        "43 20 7a 4c 4d 20 20 30 34 37 34 50 47 30 30 31 20 20 20 20 20 20 20 20 20 20 20 20 20"
    )
    assert y.build_dump_request(0, "PG", "001") == expected
    # the object list takes no name
    ol = y.build_dump_request(0, "OL")
    assert ol[:3] == bytes([0x43, 0x20, 0x7A]) and ol[11:13] == b"OL" and ol[13:] == b" " * 16


def test_object_select_matches_capture_and_the_units_echo_parses():
    sent = y.build_object_select(0, "001", "program")
    assert sent == bytes.fromhex("43 10 58 00 30 30 31" + " 20" * 13 + " 14")
    echo = y.parse_parameter_message(_message("object_select_echo.syx"))
    assert (echo.kind, echo.object_name, echo.object_type) == ("select", "001", 0x14)
    assert echo.device == 0


def test_parameter_request_matches_capture():
    # F0 43 30 58 01 01 0a 00 00 00 00 F7 (program level, P1=1 P2=10)
    assert y.build_parameter_request(0, [1, 10]) == bytes.fromhex("43 30 58 01 01 0a 00 00 00 00")
    assert y.build_parameter_request(0, [2, 0, 0, 3, 0]) == bytes.fromhex("43 30 58 01 02 00 00 03 00 00")
    assert y.build_parameter_request(0, [1, 1], system=True)[3] == 0x02


def test_parameter_reply_from_the_unit():
    reply = y.parse_parameter_message(_message("param_reply_program_level.syx"))
    assert reply.kind == "object"
    assert reply.params == (1, 10, 0, 0, 0, 0)
    assert y.decode_value(reply.data) == 127  # program level of a default program


def test_easy_edit_level_offset_reply_decodes_signed_30():
    # real reply to P=[2,0,0,3,0,0] after the user set Level +30 on the front panel
    reply = y.parse_parameter_message(bytes.fromhex("43 10 58 01 02 00 00 03 00 00 01 0e"))
    assert reply.params == (2, 0, 0, 3, 0, 0)
    assert y.decode_value(reply.data, signed=True) == 30


def test_parameter_numbers_are_validated():
    for bad in ([], [0] * 7, [128], [-1]):
        with pytest.raises(y.YamahaSysexError):
            y.build_parameter_request(0, bad)
    with pytest.raises(y.YamahaSysexError):
        y.build_parameter_request(16, [1])


def test_edit_builders_nibble_the_value_and_keep_the_layout():
    edit = y.build_object_edit(0, [2, 0, 0, 3, 0], y.encode_value(30, 1, signed=True))
    assert edit == bytes.fromhex("43 10 58 01 02 00 00 03 00 00 01 0e")
    assert y.parse_parameter_message(edit).data == b"\x1e"
    sysp = y.build_system_parameter_change(3, [1, 1, 0], b"\x7f")
    assert sysp[:4] == bytes([0x43, 0x13, 0x58, 0x02]) and sysp[-2:] == b"\x07\x0f"


# --- bulk dumps (all real captures) ---------------------------------------------------------------


def test_the_object_list_is_two_blocks_with_valid_xor_checksums():
    dump = y.parse_bulk_dump(_message("object_list.syx"))
    assert (dump.fmt, dump.header, dump.name, dump.blocks) == ("OL", b"LM  0474", "Object List", 2)
    assert len(dump.data) == 2414 and len(dump.data) % 17 == 0


def test_the_object_list_decodes_to_128_programs_then_builtin_waveforms():
    entries = y.parse_object_list(y.parse_bulk_dump(_message("object_list.syx")).data)
    assert len(entries) == 142
    programs = [e for e in entries if e.kind == "program"]
    assert [e.name for e in programs] == [f"{n:03d}" for n in range(1, 129)]
    rest = entries[128:]
    assert [e.kind for e in rest[:4]] == ["wave", "sample", "wave", "sample"]
    assert [e.name for e in rest[::2]] == [
        "sine wave", "saw up", "triangle", "square", "pulse 1", "pulse 2", "pulse 3",
    ]


@pytest.mark.parametrize(
    "name",
    [
        "object_list.syx",
        "program_001_empty.syx",
        "program_001_assigned.syx",
        "program_001_level30.syx",
        "sample_sine_wave.syx",
    ],
)
def test_every_real_dump_rebuilds_byte_for_byte(name):
    raw = _message(name)
    dump = y.parse_bulk_dump(raw)
    assert y.build_bulk_dump(dump.device, dump.fmt, dump.name_raw, dump.data, header=dump.header) == raw


def test_program_bulk_is_856_bytes_with_the_manuals_layout():
    dump = y.parse_bulk_dump(_message("program_001_empty.syx"))
    assert (dump.fmt, dump.name, dump.blocks, len(dump.data)) == ("PG", "001", 1, 856)
    d = dump.data
    assert d[0] == y.OBJECT_TYPES["program"]
    assert y.decode_name(d[2:18]) == "001"
    assert d[64:72] == b"Pgm 001 "
    assert d[83] == 127  # program level
    assert y.decode_value(d[94:96]) == 0  # no samples assigned on a cold-booted unit
    assert len(d) == 408 + 56 * 8


def test_assigning_a_sample_fills_the_first_easy_edit_block():
    empty = y.parse_bulk_dump(_message("program_001_empty.syx")).data
    assigned = y.parse_bulk_dump(_message("program_001_assigned.syx")).data
    assert y.decode_value(assigned[94:96]) == 1
    block = assigned[408 : 408 + 56]
    assert y.decode_name(block[:16]) == "sine wave"
    assert block[20] == y.OBJECT_TYPES["sample"]
    assert block[21] == 0  # receive channel 01
    assert empty[408 + 21] == 0xFF  # an empty slot is "=sample" (-1)
    assert assigned[408 + 56 :] == empty[408 + 56 :]  # the other 7 blocks untouched


def test_assigning_a_sample_also_flips_an_undocumented_flag_in_the_common_block():
    # observed, NOT in the manual: byte 1 of [Common] is 0x60 with nothing assigned and 0x61 once a sample
    # is (meaning unknown - preserve it, never "fix" it). Everything else that changes is the sample count
    # (@95) and Easy Edit block 0.
    empty = y.parse_bulk_dump(_message("program_001_empty.syx")).data
    assigned = y.parse_bulk_dump(_message("program_001_assigned.syx")).data
    changed = {i for i, (a, b) in enumerate(zip(empty, assigned)) if a != b}
    assert (empty[1], assigned[1]) == (0x60, 0x61)
    assert {1, 95} <= changed <= {1, 95} | set(range(408, 408 + 56))


def test_changing_the_easy_edit_level_offset_changes_exactly_one_byte():
    before = y.parse_bulk_dump(_message("program_001_assigned.syx")).data
    after = y.parse_bulk_dump(_message("program_001_level30.syx")).data
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert changed == [408 + 22]  # Easy Edit block 0, +22: level offset
    assert before[408 + 22] == 0 and after[408 + 22] == 30


def test_sample_bulk_is_336_bytes_with_the_linked_wave_name():
    dump = y.parse_bulk_dump(_message("sample_sine_wave.syx"))
    assert (dump.fmt, dump.name, len(dump.data)) == ("SP", "sine wave", 336)
    assert dump.data[0] == y.OBJECT_TYPES["sample"]
    assert y.decode_name(dump.data[64:80]) == "sine wave"
    # [Sample Parameter] starts at 112: sampling frequency L (+48) is 48000, original key L (+46) is 66
    assert y.decode_value(dump.data[112 + 48 : 112 + 50]) == 48000
    assert dump.data[112 + 46] == 66


def test_a_corrupted_checksum_or_truncation_is_caught():
    raw = bytearray(_message("program_001_empty.syx"))
    raw[-1] ^= 0x01
    with pytest.raises(y.YamahaSysexError, match="checksum"):
        y.parse_bulk_dump(bytes(raw))
    assert y.parse_bulk_dump(bytes(raw), verify=False).name == "001"
    with pytest.raises(y.YamahaSysexError):
        y.parse_bulk_dump(_message("program_001_empty.syx")[:-40])
    with pytest.raises(y.YamahaSysexError):
        y.parse_bulk_dump(_message("identity_reply.syx"))


def test_f0_and_f7_are_tolerated_on_input():
    assert y.parse_bulk_dump(_raw("program_001_empty.syx")).name == "001"
    assert y.parse_parameter_message(_raw("param_reply_program_level.syx")).kind == "object"


def test_building_a_bulk_splits_into_4096_byte_spans():
    data = bytes(i % 251 for i in range(5000))
    built = y.build_bulk_dump(2, "WD", y.pad_name("wave"), data)
    parsed = y.parse_bulk_dump(built)
    assert parsed.data == data and parsed.device == 2 and parsed.blocks == 3
    # first span = 26 header bytes + 4070 nibbled data bytes, exactly 4096 (as measured on the object list)
    assert (built[3] << 7) | built[4] == 4096
    assert len(y.build_bulk_dump(0, "OL", y.pad_name(""), b"")) == 3 + 2 + 26 + 1
    assert y.parse_bulk_dump(y.build_bulk_dump(0, "OL", y.pad_name(""), b"")).data == b""


def test_a_bulk_that_ends_exactly_on_a_block_boundary_still_roundtrips():
    data = bytes(2035)  # 2035 data bytes = 4070 nibbled = exactly the first block's room
    parsed = y.parse_bulk_dump(y.build_bulk_dump(0, "SY", y.pad_name(""), data))
    assert parsed.data == data and parsed.blocks == 1


# --- streams / classification ---------------------------------------------------------------------


def test_split_messages_and_classify():
    blob = _raw("identity_reply.syx") + _raw("param_reply_program_level.syx") + _raw("object_list.syx")
    labels = [y.classify(m) for m in y.split_messages(blob)]
    assert labels == ["identity_reply", "parameter", "bulk_dump"]
    assert y.classify(y.build_dump_request(0, "PG", "001")) == "dump_request"
    assert y.classify(y.build_parameter_request(0, [1, 1])) == "parameter_request"
    assert y.classify(b"\x7e\x7f\x09\x01") == "unknown"
    assert y.split_messages(b"\xf0\x01\x02") == []  # unterminated tail
