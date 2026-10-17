# tests for core/asrx_sysex.py (the ASR-X codec) and core/demo_asrx.py (its fake).
#
# The codec was written from a STATIC READING of the ASR-X v2.67 ROM (dev_docs/asrx-rom-findings.md) and the fake from the same reading, so these tests pin the
# facts the reading established and check the two agree with each other; they cannot show that a real ASR-X agrees.

import struct

import pytest

from core import asrx_sysex as a
from core.demo_asrx import FakeASRX, make_sample_object, triangle

SOX, EOX = 0xF0, 0xF7


# -- numbers and payload ------------------------------------------------------------------------------------


def test_numbers_are_low_septet_first():
    assert a.encode_u14(0x0000) == bytes((0, 0))
    assert a.encode_u14(300) == bytes((300 & 0x7F, 300 >> 7))
    assert a.encode_u32(0x12345678) == bytes((0x78 & 0x7F, (0x12345678 >> 7) & 0x7F, (0x12345678 >> 14) & 0x7F, (0x12345678 >> 21) & 0x7F, 0x12345678 >> 28))
    assert a.decode_u32(a.encode_u32(0xFFFFFFFF)) == 0xFFFFFFFF
    assert a.decode_u14(a.encode_u14(0x3FFF)) == 0x3FFF


def test_numbers_reject_what_does_not_fit():
    with pytest.raises(a.AsrxError):
        a.encode_u14(0x4000)
    with pytest.raises(a.AsrxError):
        a.encode_u32(1 << 32)
    with pytest.raises(a.AsrxError):
        a.decode_u14(bytes((0x80, 0)))


def test_payload_words_are_big_endian_and_five_septets():
    # the ROM's converter (0x6A64E) turns the four memory bytes 12 34 56 78 into the 32-bit value 0x12345678 and sends it low septet first
    assert a.pack_payload(bytes((0x12, 0x34, 0x56, 0x78))) == a.encode_u32(0x12345678)
    assert a.pack_payload(b"\x00\x00\x00\x01") == bytes((1, 0, 0, 0, 0))


def test_payload_pads_the_last_group_and_round_trips():
    data = bytes(range(1, 11))
    packed = a.pack_payload(data)
    assert len(packed) == a.payload_septet_count(10) == 15
    assert a.unpack_payload(packed, 10) == data
    assert all(b < 0x80 for b in a.pack_payload(bytes(range(256)) * 3))


def test_checksum_is_the_septet_sum_low_14_bits():
    septets = bytes((0x7F,)) * 1000
    assert a.checksum(septets) == (0x7F * 1000) & 0x3FFF
    assert a.checksum(b"") == 0


# -- names -----------------------------------------------------------------------------------------------------


def test_names_are_sixteen_ascii_chars_space_padded():
    raw = a.encode_name("Kick")
    assert raw == b"Kick" + b" " * 12 and len(raw) == 16
    assert a.decode_name(raw) == "Kick"
    assert a.encode_name("A" * 40) == b"A" * 16
    assert a.encode_name("") .strip() == b"SAMPLE"
    assert a.encode_name("café\x01x") == b"caf__x" + b" " * 10


# -- requests --------------------------------------------------------------------------------------------------


def test_name_request_is_the_standard_sds_extension():
    # F0 7E <dev> 05 04 <slot lo> <slot hi> F7
    assert a.build_name_request(0, 5) == bytes((0x7E, 0x00, 0x05, 0x04, 5, 0))
    assert a.build_name_request(0x7F, 300) == bytes((0x7E, 0x7F, 0x05, 0x04, 300 & 0x7F, 300 >> 7))


def test_get_object_request():
    assert a.build_get_object(3, 9) == bytes((0x0F, 0x10, 0x01, 3, 0x01, 8, 9, 0))


def test_read_range_request_layout():
    msg = a.build_read_range(0, 7, 640, 52)
    assert msg[:5] == bytes((0x0F, 0x10, 0x01, 0, 0x04))
    assert msg[5] == 8  # type
    assert a.decode_u14(msg, 6) == 7  # slot
    assert msg[8] == 0  # section
    assert a.decode_u14(msg, 9) == 0  # index
    assert a.decode_u32(msg, 11) == 640 and a.decode_u32(msg, 16) == 52
    assert len(msg) == 21


def test_slot_out_of_range_is_refused():
    with pytest.raises(a.AsrxError):
        a.build_name_request(0, a.SLOT_LIMIT)


def test_put_object_layout_and_checksum():
    obj = make_sample_object(4, "T", frames=100)
    msg = a.build_put_object(0, 4, obj)
    assert msg[:5] == bytes((0x0F, 0x10, 0x01, 0, 0x02))
    assert msg[5] == 8 and a.decode_u14(msg, 6) == 4 and a.decode_u32(msg, 8) == len(obj)
    septets = msg[13:-2]
    assert a.unpack_payload(septets, len(obj)) == obj
    assert a.decode_u14(msg, len(msg) - 2) == a.checksum(septets)
    assert all(b < 0x80 for b in msg)


# -- the sample object -----------------------------------------------------------------------------------------


def test_pitch_word_matches_the_rom_formula():
    # -(round(3072 x log2(29762 / rate))) - 256 x root, as an unsigned 16-bit value
    assert a.pitch_word(29762, 60) == (-256 * 60) & 0xFFFF
    assert a.pitch_word(29762 * 2, 60) == (3072 - 256 * 60) & 0xFFFF
    # the unit's own default for a fresh recording is 0xCACD, a few 1/256-semitone steps from 44.1 kHz at root 60
    assert abs(a.pitch_word(44100, 60) - 0xCACD) <= 4


def test_pitch_word_round_trips_through_the_rate():
    for rate in (8000, 11025, 22050, 32000, 44100, 48000, 96000):
        for root in (36, 60, 84):
            word = a.pitch_word(rate, root)
            assert a.rate_from_pitch_word(word, root) == pytest.approx(rate, rel=1e-3)


def test_pitch_word_refuses_what_it_cannot_hold():
    with pytest.raises(a.AsrxError):
        a.pitch_word(1, 60)
    with pytest.raises(a.AsrxError):
        a.pitch_word(0, 60)


def test_sample_object_header_layout():
    samples = triangle(300)
    obj = a.build_sample_object(7, "Hello", samples, 44100)
    assert len(obj) == 0x34 + 600
    assert struct.unpack(">I", obj[0:4])[0] == len(obj)
    assert obj[4:8] == b"SMP1"
    assert obj[8:24] == b"Hello" + b" " * 11
    assert obj[0x18] == 0 and obj[0x19] == 0
    assert obj[0x1A] == 60  # root key
    assert struct.unpack(">H", obj[0x1E:0x20])[0] == 0xFFFF  # owns its audio
    assert struct.unpack(">H", obj[0x20:0x22])[0] == 7  # its own slot
    assert obj[0x24] == 0x7F and obj[0x25] == a.LOOP_NONE
    assert struct.unpack(">H", obj[0x26:0x28])[0] == a.pitch_word(44100, 60)
    assert struct.unpack(">III", obj[0x28:0x34]) == (0, 0, (300 - 1) << 4)
    assert struct.unpack(">300h", obj[0x34:]) == tuple(samples)


def test_sample_object_pads_short_and_odd_samples():
    short = a.build_sample_object(0, "S", [100, 200, 300], 44100)
    assert len(short) == 0x34 + 2 * a.MIN_SAMPLES
    odd = a.build_sample_object(0, "S", [1] * 41, 44100)
    assert (len(odd) - 0x34) // 2 == 42 and len(odd) % 4 == 0


def test_sample_object_parses_back():
    samples = triangle(500)
    obj = a.build_sample_object(3, "Round Trip", samples, 22050, root_key=48)
    parsed = a.parse_sample_object(obj)
    assert parsed.name == "Round Trip" and parsed.root_key == 48 and parsed.own_slot == 3
    assert parsed.rate_hz == pytest.approx(22050, rel=1e-3)
    assert parsed.samples == samples
    assert parsed.end == 499 and parsed.start == 0 and not parsed.shares_audio
    assert "Round Trip" in a.describe_header(obj[:52])


def test_parse_sample_object_rejects_bad_objects():
    good = a.build_sample_object(0, "S", triangle(64), 44100)
    with pytest.raises(a.AsrxError):
        a.parse_sample_object(good[:30])
    with pytest.raises(a.AsrxError):
        a.parse_sample_object(good[:-2])
    with pytest.raises(a.AsrxError):
        a.parse_sample_object(good[:4] + b"PRG1" + good[8:])


def test_header_only_parse_ignores_the_length():
    good = a.build_sample_object(0, "S", triangle(64), 44100)
    assert a.parse_sample_object(good[:52], header_only=True).samples == []


def test_too_long_is_refused():
    with pytest.raises(a.AsrxError):
        a.build_sample_object(0, "S", [0] * (a.MAX_SAMPLES + 1), 44100)


# -- parsing the unit's replies --------------------------------------------------------------------------------


def _fake_ask(fake, message):
    fake.handle([SOX, *message, EOX])
    out = []
    while fake._outbox:
        out.append(bytes(fake._outbox.popleft()[1:-1]))
    return out


def test_name_reply_parses():
    fake = FakeASRX()
    (reply,) = _fake_ask(fake, a.build_name_request(0, 1))
    message = a.parse_message(reply)
    assert message.kind == "name" and message.slot == 1 and message.name == "SNARE"


def test_empty_slot_is_error_41():
    fake = FakeASRX()
    (reply,) = _fake_ask(fake, a.build_name_request(0, 50))
    assert reply == bytes((0x0F, 0x10, 0x01, 0, 0x7F, 0x41))
    message = a.parse_message(reply)
    assert message.kind == "error" and message.error_code == a.ERROR_NO_SUCH_OBJECT


def test_object_reply_parses_with_a_good_checksum():
    fake = FakeASRX()
    (reply,) = _fake_ask(fake, a.build_get_object(0, 0))
    message = a.parse_message(reply)
    assert message.kind == "object" and message.slot == 0 and message.checksum_ok
    assert message.payload == fake.slots[0]


def test_object_reply_with_a_bad_checksum_is_flagged():
    fake = FakeASRX(corrupt_object_checksum=1)
    (reply,) = _fake_ask(fake, a.build_get_object(0, 0))
    assert a.parse_message(reply).checksum_ok is False


def test_range_reply_parses():
    fake = FakeASRX()
    (reply,) = _fake_ask(fake, a.build_read_range(0, 2, 52, 100))
    message = a.parse_message(reply)
    assert message.kind == "range" and (message.offset, message.length) == (52, 100)
    assert message.payload == fake.slots[2][52:152]


def test_range_outside_the_object_is_silent():
    fake = FakeASRX()
    size = len(fake.slots[0])
    assert _fake_ask(fake, a.build_read_range(0, 0, size, 4)) == []
    assert _fake_ask(fake, a.build_read_range(0, 0, size - 2, 8)) == []
    assert _fake_ask(fake, a.build_read_range(0, 77, 0, 4)) == []


def test_put_is_stored_and_acked():
    fake = FakeASRX()
    obj = make_sample_object(9, "NEW", frames=400)
    replies = _fake_ask(fake, a.build_put_object(0, 9, obj))
    assert [a.parse_message(r).kind for r in replies] == ["ack"]
    assert fake.slots[9] == obj and fake.uploads == [(9, len(obj), True)]


def test_put_with_a_bad_checksum_is_stored_but_not_acked():
    fake = FakeASRX()
    obj = make_sample_object(9, "NEW", frames=400)
    message = bytearray(a.build_put_object(0, 9, obj))
    message[-2] = (message[-2] + 1) & 0x7F
    assert _fake_ask(fake, bytes(message)) == []
    assert fake.slots[9] == obj and fake.uploads[-1][2] is False


def test_put_when_memory_is_full_stores_nothing():
    fake = FakeASRX(memory_bytes=10_000)
    obj = make_sample_object(9, "BIG", frames=20_000)
    assert _fake_ask(fake, a.build_put_object(0, 9, obj)) == []
    assert 9 not in fake.slots


def test_hidden_slot_answers_a_range_read_but_not_a_name():
    fake = FakeASRX(hidden={5}, slots={5: make_sample_object(5, "REC", frames=100)})
    assert a.parse_message(_fake_ask(fake, a.build_name_request(0, 5))[0]).kind == "error"
    assert a.parse_message(_fake_ask(fake, a.build_read_range(0, 5, 0, 4))[0]).kind == "range"


def test_other_device_ids_are_ignored_and_broadcast_answers():
    fake = FakeASRX(device=5)
    assert _fake_ask(fake, a.build_name_request(0, 1)) == []
    (reply,) = _fake_ask(fake, a.build_name_request(0x7F, 1))
    assert a.parse_message(reply).device == 5


def test_unreadable_replies_raise():
    with pytest.raises(a.AsrxError):
        a.parse_message(a.HEADER + bytes((0, a.REPLY_OBJECT, 8, 0, 0)))
    with pytest.raises(a.AsrxError):
        a.parse_message(a.HEADER + bytes((0, a.REPLY_RANGE, 8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5, 0, 0, 0, 0, 1, 2)))
