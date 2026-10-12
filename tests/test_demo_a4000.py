# tests for core/demo_a4000.py - the fake Yamaha A4000. It must reproduce what the real unit did on
# 2026-10-06: the fixtures in tests/fixtures/a4000 are the reference.

import pathlib

import pytest

from core import demo_a4000 as demo
from core import yamaha_params as yp
from core import yamaha_sysex as y

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "a4000"


def _fixture(name):
    return y.parse_bulk_dump(y.split_messages((FIXTURES / name).read_bytes())[0]).data


def ask(fake, message):
    """Send a message (no F0/F7) and return what the fake said back, as messages without F0/F7."""
    fake.out.send_message([0xF0, *message, 0xF7])
    out = []
    while (got := fake.inp.get_message()) is not None:
        out.append(bytes(got[0][1:-1]))
    return out


def dump(fake, fmt, name=""):
    replies = ask(fake, y.build_dump_request(0, fmt, name))
    assert len(replies) == 1, replies
    return y.parse_bulk_dump(replies[0])


def read_param(fake, params):
    """select is the caller's job; returns (announce, value message)."""
    replies = ask(fake, y.build_parameter_request(0, params))
    return replies


# --- it reproduces the real captures byte for byte ----------------------------------------------------


def test_a_cold_program_is_the_captured_one():
    assert bytes(demo.make_program_payload(1)) == _fixture("program_001_empty.syx")


def test_assigning_the_sine_wave_reproduces_the_captured_assigned_program():
    fake = demo.FakeA4000()
    fake.assign(1, "sine wave")
    assert bytes(fake.programs[1]) == _fixture("program_001_assigned.syx")


def test_the_sample_is_the_captured_sine_wave():
    assert bytes(demo.make_sample_payload("sine wave")) == _fixture("sample_sine_wave.syx")


def test_identity_reply_matches_the_capture():
    fake = demo.FakeA4000()
    (reply,) = ask(fake, y.build_identity_request())
    assert reply == y.split_messages((FIXTURES / "identity_reply.syx").read_bytes())[0]
    assert y.parse_identity_reply(reply).model == "A4000"


def test_object_list_has_128_programs_then_wave_and_sample_pairs():
    entries = y.parse_object_list(dump(demo.FakeA4000(), "OL").data)
    assert [e.name for e in entries if e.kind == "program"] == [f"{n:03d}" for n in range(1, 129)]
    rest = entries[128:]
    assert [e.kind for e in rest[:4]] == ["wave", "sample", "wave", "sample"]
    assert [e.name for e in rest[::2]] == list(demo.FACTORY_SAMPLES)


def test_bulk_dumps_are_framed_like_the_real_unit():
    fake = demo.FakeA4000()
    ol = dump(fake, "OL")
    assert ol.blocks == 2 and ol.name == "Object List"  # 2414 bytes -> two blocks, as measured
    assert dump(fake, "PG", "001").blocks == 1


# --- parameters -----------------------------------------------------------------------------------------


def test_a_select_gets_no_reply_and_a_request_is_announced_then_answered():
    fake = demo.FakeA4000()
    assert ask(fake, y.build_object_select(0, "001", "program")) == []
    announce, value = read_param(fake, (1, 10, 0, 0, 0, 0))
    assert y.parse_parameter_message(announce).kind == "select"
    assert y.parse_parameter_message(announce).object_name == "001"
    reply = y.parse_parameter_message(value)
    assert reply.kind == "object" and reply.params == (1, 10, 0, 0, 0, 0)
    assert y.decode_value(reply.data) == 127


def test_easy_edit_requests_use_the_slot():
    fake = demo.FakeA4000()
    fake.assign(1, "sine wave")
    ask(fake, y.build_object_select(0, "001", "program"))
    level = yp.get("easy_edit", "level_offset")
    _announce, value = read_param(fake, yp.request_params(level, 0))
    assert y.decode_value(y.parse_parameter_message(value).data, signed=True) == 0
    assert read_param(fake, yp.request_params(level, 7))  # the 8th of 8 blocks exists
    assert read_param(fake, yp.request_params(level, 50)) == []  # no such block


def test_an_edit_changes_exactly_the_tables_bytes_and_sets_the_edited_flag():
    fake = demo.FakeA4000()
    fake.assign(1, "sine wave")
    before = bytes(fake.programs[1])
    ask(fake, y.build_object_select(0, "001", "program"))
    level = yp.get("easy_edit", "level_offset")
    assert ask(fake, y.build_object_edit(0, yp.request_params(level, 0), y.encode_value(30, 1, signed=True))) == []
    after = bytes(fake.programs[1])
    assert yp.extract(level, after, 0) == 30
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert changed == [408 + 22]  # assign() already set the edited flag, so only the level offset moved
    assert fake.edits == 1


def test_the_edited_flag_is_set_by_any_edit():
    fake = demo.FakeA4000()
    flag_before = fake.programs[128][1] & 1
    ask(fake, y.build_object_select(0, "128", "program"))
    ask(fake, y.build_object_edit(0, (1, 10, 0, 0, 0, 0), b"\x2f"))
    assert flag_before == 0 and fake.programs[128][1] & 1 == 1
    assert yp.extract(yp.get("program", "program_level"), fake.programs[128]) == 47


def test_bitfields_edit_only_their_bits():
    fake = demo.FakeA4000()
    before = yp.extract(yp.get("program", "lfo_wave"), fake.programs[1])
    ask(fake, y.build_object_select(0, "001", "program"))
    ask(fake, y.build_object_edit(0, (1, 11, 0, 0, 0, 0), b"\x02"))  # lfo cycle (shares byte 73 with wave/phase)
    assert yp.extract(yp.get("program", "lfo_cycle"), fake.programs[1]) == 2
    assert yp.extract(yp.get("program", "lfo_wave"), fake.programs[1]) == before


def test_rows_the_unit_ignored_writes_to_are_ignored_here_too():
    fake = demo.FakeA4000()
    ask(fake, y.build_object_select(0, "sine wave", "sample"))
    freq = yp.get("sample", "sampling_frequency_l")
    ask(fake, y.build_object_edit(0, freq.p, y.encode_value(22050, 2)))
    assert yp.extract(freq, fake.samples["sine wave"]) == 48000
    assert fake.samples["sine wave"][1] & 1 == 1  # accepted: the flag still flips


def test_bulk_protect_blocks_edits():
    fake = demo.FakeA4000(bulk_protect=True)
    ask(fake, y.build_object_select(0, "001", "program"))
    ask(fake, y.build_object_edit(0, (1, 10, 0, 0, 0, 0), b"\x10"))
    assert yp.extract(yp.get("program", "program_level"), fake.programs[1]) == 127
    assert fake.edits == 0


def test_a_wrong_or_off_device_number_gets_silence():
    assert ask(demo.FakeA4000(), y.build_dump_request(3, "OL")) == []
    assert ask(demo.FakeA4000(device_number_off=True), y.build_dump_request(0, "OL")) == []


def test_unknown_objects_and_unsupported_formats_get_silence():
    fake = demo.FakeA4000()
    assert ask(fake, y.build_dump_request(0, "PG", "200")) == []
    assert ask(fake, y.build_dump_request(0, "SP", "nope")) == []
    assert ask(fake, y.build_dump_request(0, "SB", "x")) == []
    assert ask(fake, y.build_object_select(0, "nope", "sample")) == []
    assert ask(fake, y.build_parameter_request(0, (2, 33))) == []  # no current object
    assert len(fake.ignored_ops) >= 4


def test_assigning_marks_the_program_in_the_samples_link_map():
    fake = demo.FakeA4000()
    assert yp.linked_programs(fake.samples["sine wave"]) == []
    fake.assign(1, "sine wave")
    fake.assign(40, "sine wave")
    fake.assign(128, "saw up")
    assert yp.linked_programs(fake.samples["sine wave"]) == [1, 40]
    assert yp.linked_programs(fake.samples["saw up"]) == [128]
    # and it is what a dump of the sample says
    assert yp.linked_programs(dump(fake, "SP", "sine wave").data) == [1, 40]
