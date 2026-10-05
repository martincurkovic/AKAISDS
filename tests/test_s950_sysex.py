# tests for core/s950_sysex.py
# pure byte-level tests (no Qt, no MIDI). Golden vectors are ported from
# s950tools (MIT, Copyright (c) 2026 Brandon Ivers) - see THIRD_PARTY_NOTICES.md

import random

import pytest

from core import s950_sysex as s


def test_db_known_vectors():
    for value, lo, hi in [
        (0x00, 0x00, 0x00),
        (0x7F, 0x7F, 0x00),
        (0x80, 0x00, 0x01),
        (0xFF, 0x7F, 0x01),
        (0x42, 0x42, 0x00),
        (0xC3, 0x43, 0x01),
    ]:
        assert s.encode_db(value) == [lo, hi]
        assert s.decode_db(lo, hi) == value


def test_db_round_trip_exhaustive_and_always_7_bit():
    for v in range(256):
        lo, hi = s.encode_db(v)
        assert lo <= 0x7F and hi <= 0x7F
        assert s.decode_db(lo, hi) == v


def test_dw_dd_tb_round_trip_and_7_bit():
    rng = random.Random(1)
    for _ in range(500):
        w = rng.getrandbits(16)
        d = rng.getrandbits(32)
        t = rng.getrandbits(21)
        assert s.decode_dw(s.encode_dw(w)) == w
        assert s.decode_dd(s.encode_dd(d)) == d
        assert s.decode_tb(s.encode_tb(t)) == t
        assert max(s.encode_dw(w) + s.encode_dd(d) + s.encode_tb(t)) <= 0x7F
    for d in (0, 1, 0x7F, 0x80, 0xFF, 0x100, 0xFFFF, 0x10000, 0xFFFFFFFF):
        assert s.decode_dd(s.encode_dd(d)) == d


def test_tb_known_vectors():
    assert s.encode_tb(0) == [0, 0, 0]
    assert s.encode_tb(127) == [0x7F, 0, 0]
    assert s.encode_tb(128) == [0, 1, 0]
    assert s.encode_tb(0x1FFFFF) == [0x7F, 0x7F, 0x7F]


def test_sw_known_vectors():
    # the doc: "zero is sent as 40 00H", i.e. offset-binary silence 0x800
    for word, b0, b1 in [
        (0x800, 0x40, 0x00),
        (4095, 0x7F, 0x7C),
        (0, 0x00, 0x00),
        (1, 0x00, 0x04),
        (0x020, 0x01, 0x00),
    ]:
        assert s.encode_sw(word) == [b0, b1]
        assert s.decode_sw(b0, b1) == word


def test_sw_round_trip_exhaustive():
    for v in range(4096):
        b0, b1 = s.encode_sw(v)
        assert b0 <= 0x7F and b1 <= 0x7F
        assert s.decode_sw(b0, b1) == v


def test_signed_values_survive_the_unsigned_wire():
    assert s.to_signed(s.decode_dw(s.encode_dw(-100)), 16) == -100
    assert s.to_signed(s.decode_db(*s.encode_db(-50)), 8) == -50
    assert s.to_signed(s.decode_db(*s.encode_db(50)), 8) == 50


def test_name_round_trip_pad_truncate_and_non_ascii():
    assert s.decode_name(s.encode_name("KICK")) == "KICK"
    assert len(s.encode_name("KICK")) == 20
    assert s.decode_name(s.encode_name("")) == ""
    assert s.decode_name(s.encode_name("1234567890")) == "1234567890"
    assert s.decode_name(s.encode_name("OVERLONG NAME")) == "OVERLONG N"
    assert s.decode_name(s.encode_name("CAFÉ")) == "CAF?"
    assert max(s.encode_name("ZZZZZZZZZZ")) <= 0x7F


def test_xor_checksum_masks_to_7_bits():
    assert s.xor_checksum([0x10, 0x20, 0x30, 0x40, 0x50]) == 0x10 ^ 0x20 ^ 0x30 ^ 0x40 ^ 0x50
    assert s.xor_checksum([]) == 0


# --- framing ---------------------------------------------------------------


def test_build_akai_request_golden():
    # F0 47 00 03 40 00 00 F7 - catalog request
    assert s.build_akai_request(s.FUNC_RCAT) == [0x47, 0x00, 0x03, 0x40, 0x00, 0x00]
    # F0 47 05 04 40 07 00 F7 - RSPRM #7 on channel 5
    assert s.build_akai_request(s.FUNC_RSPRM, 7, channel=5) == [
        0x47, 0x05, 0x04, 0x40, 0x07, 0x00,
    ]


def test_akai_data_round_trip_and_checksum():
    payload = [0x10, 0x20, 0x30, 0x40, 0x50]
    msg = s.build_akai_data(s.FUNC_SPRM, 9, payload, channel=2)
    assert msg[:6] == [0x47, 2, s.FUNC_SPRM, 0x40, 9, 0x00]
    assert msg[-1] == s.xor_checksum(payload)
    parsed = s.parse_akai(msg)
    assert (parsed.channel, parsed.function, parsed.num) == (2, s.FUNC_SPRM, 9)
    assert parsed.payload == bytes(payload)


def test_parse_akai_rejects_bad_input():
    good = s.build_akai_data(s.FUNC_SPRM, 1, [1, 2, 3])
    tampered = good[:-1] + [good[-1] ^ 0x01]
    for bad in (
        [],
        [0x47, 0, 0],  # too short
        [0x42] + good[1:],  # wrong manufacturer
        good[:3] + [0x48] + good[4:],  # S1000-family device byte, not ours
        tampered,
    ):
        with pytest.raises(ValueError):
            s.parse_akai(bad)


def test_request_sample_dump_golden():
    # F0 7E 00 03 00 F7 - not the standard SDS request
    assert s.build_request_sample_dump(3) == [0x7E, 0x00, 0x03, 0x00]


def test_handshake_is_the_four_byte_form():
    for code in (s.CODE_ACKS, s.CODE_NAKS, s.CODE_ASD):
        msg = s.build_handshake(code)
        assert msg == [0x7E, code]  # F0 7E code F7 on the wire
        assert s.handshake_code(msg) == code
    assert s.handshake_code([]) is None
    assert s.handshake_code([0x7E, 0x10]) is None
    assert s.handshake_code([0x7F, 0x7F]) is None
    # the standard 6-byte SDS ACK is NOT a handshake here
    assert s.handshake_code([0x7E, 0x00, 0x7F, 0x00]) is None
    with pytest.raises(ValueError):
        s.build_handshake(0x10)


def test_parse_catalog():
    payload = b"P\x01KICK      S\x02SNARE     "
    entries = s.parse_catalog(payload)
    assert entries == [
        s.CatalogEntry("P", 1, "KICK"),
        s.CatalogEntry("S", 2, "SNARE"),
    ]
    assert s.parse_catalog(b"") == []
    with pytest.raises(ValueError):
        s.parse_catalog(b"\x00\x00\x00")


# --- SPRM --------------------------------------------------------------------


def _sprm():
    return s.SampleParams(
        name="TEST",
        total_words=12345,
        sample_rate_hz=22050,
        nominal_pitch=960,
        loud_offset=-100,
        replay_mode=s.REPLAY_LOOP,
        end=12000,
        start=0,
        loop_length=500,
        vel_xfade=0,
        reversed=s.PLAY_FORWARD,
    )


def test_sprm_round_trip_is_a_fixed_point():
    rng = random.Random(42)
    p = _sprm()
    p.raw = bytes(rng.randrange(128) for _ in range(s.SPRM_PAYLOAD_SIZE))
    payload = p.to_payload()
    assert len(payload) == s.SPRM_PAYLOAD_SIZE
    assert max(payload) <= 0x7F
    q = s.SampleParams.from_payload(payload)
    for field in (
        "name total_words sample_rate_hz nominal_pitch loud_offset replay_mode "
        "end start loop_length vel_xfade reversed"
    ).split():
        assert getattr(q, field) == getattr(p, field), field
    assert q.to_payload() == payload


def test_sprm_preserves_reserved_bytes():
    q = s.SampleParams.from_payload(_sprm().to_payload())
    raw = bytearray(q.raw)
    raw[28] = 0x42  # an unmodelled byte
    q.raw = bytes(raw)
    q.total_words = 99
    out = q.to_payload()
    assert out[28] == 0x42
    assert s.SampleParams.from_payload(out).total_words == 99


def test_sprm_longer_than_documented_keeps_its_tail():
    # never assume a block length: extra bytes must survive a round trip
    payload = _sprm().to_payload() + bytes([0x11, 0x22])
    q = s.SampleParams.from_payload(payload)
    assert q.to_payload() == payload


def test_sprm_too_short_names_the_real_length():
    with pytest.raises(ValueError, match="100 bytes"):
        s.SampleParams.from_payload(bytes(100))


# --- sample dump -------------------------------------------------------------


def _dump_header(words):
    return s.SampleDumpHeader(
        num=7,
        period_ns=45351,
        total_words=len(words),
        loop_start=len(words) - 5,
        loop_end=len(words) - 1,
    )


def test_dump_header_round_trip():
    h = s.SampleDumpHeader(
        num=7,
        bits_per_word=12,
        period_ns=45351,
        total_words=22050,
        loop_start=21000,
        loop_end=22049,
        mode=0,
    )
    data = h.to_bytes()
    assert len(data) == s.DUMP_HEADER_SIZE
    assert data[:2] == [0x7E, 0x01]  # no channel byte, unlike standard SDS
    assert max(data) <= 0x7F
    assert s.SampleDumpHeader.from_bytes(data) == h


def test_dump_header_rejects_other_messages():
    with pytest.raises(ValueError):
        s.SampleDumpHeader.from_bytes([0x7E, 0x02] + [0] * 16)
    with pytest.raises(ValueError):
        s.SampleDumpHeader.from_bytes([0x7E, 0x01, 0])


def test_block_layout_and_checksum_excludes_block_number():
    words = [(i * 17) & 0xFFF for i in range(s.WORDS_PER_BLOCK)]
    block = s.encode_block(3, words)
    assert len(block) == s.BLOCK_SIZE == 122
    assert block[0] == 3
    assert block[-1] == s.xor_checksum(block[1:-1])
    assert max(block) <= 0x7F
    assert s.decode_block(block) == (3, words)


def test_block_number_wraps_at_128():
    zeros = [0] * s.WORDS_PER_BLOCK
    assert s.encode_block(128, zeros)[0] == 0
    assert s.encode_block(129, zeros)[0] == 1


def test_block_corruption_is_detected():
    block = s.encode_block(0, [0] * s.WORDS_PER_BLOCK)
    block[5] ^= 0x01
    with pytest.raises(ValueError, match="checksum"):
        s.decode_block(block)


def test_block_needs_exactly_60_words():
    with pytest.raises(ValueError):
        s.encode_block(0, [0] * 59)


def test_num_blocks():
    for words, want in [(0, 0), (1, 1), (60, 1), (61, 2), (120, 2), (121, 3), (475020, 7917)]:
        assert s.num_blocks(words) == want


def test_sample_dump_round_trip_pads_the_last_block_with_silence():
    words = [(i * 7) & 0xFFF for i in range(250)]  # 4 full blocks + 10
    data = s.build_sample_dump(_dump_header(words), words)
    assert len(data) == s.DUMP_HEADER_SIZE + 5 * s.BLOCK_SIZE
    assert max(data) <= 0x7F
    header, got = s.parse_sample_dump(data, expected_slot=7)
    assert header.total_words == 250
    assert got == words
    # the padding really is offset-binary silence
    last = data[s.DUMP_HEADER_SIZE + 4 * s.BLOCK_SIZE :]
    assert s.decode_block(last)[1][10:] == [s.SILENCE_WORD] * 50


def test_sample_dump_header_must_match_word_count():
    words = [0] * 250
    h = _dump_header(words)
    h.total_words = 251
    with pytest.raises(ValueError):
        s.build_sample_dump(h, words)


def test_parse_sample_dump_accepts_a_short_final_block():
    # measured on hardware by s950tools: the last block can come up short
    words = [(i * 13) & 0xFFF for i in range(250)]
    data = s.build_sample_dump(_dump_header(words), words)
    # rebuild the last block with only its 10 real words (1 + 20 + 1 bytes)
    body_end = s.DUMP_HEADER_SIZE + 4 * s.BLOCK_SIZE
    short_data = []
    for w in words[240:]:
        short_data += s.encode_sw(w)
    short = [4] + short_data + [s.xor_checksum(short_data)]
    header, got = s.parse_sample_dump(data[:body_end] + short)
    assert got == words


def test_parse_sample_dump_rejects_wrong_slot_and_truncation():
    words = [5] * 130
    data = s.build_sample_dump(_dump_header(words), words)
    with pytest.raises(ValueError, match="slot"):
        s.parse_sample_dump(data, expected_slot=1)
    with pytest.raises(ValueError, match="short"):
        s.parse_sample_dump(data[: s.DUMP_HEADER_SIZE + s.BLOCK_SIZE])


# --- audio conversion --------------------------------------------------------


def test_pcm16_to_word_vectors():
    for pcm, word in [(0, 2048), (32767, 4095), (-32768, 0), (16, 2049), (-16, 2047)]:
        assert s.pcm16_to_word(pcm) == word


def test_word_to_pcm16_vectors():
    assert s.word_to_pcm16(0x800) == 0
    assert s.word_to_pcm16(4095) == 2047 << 4
    assert s.word_to_pcm16(0) == -2048 << 4


def test_pcm16_round_trip_only_loses_the_low_4_bits():
    for pcm in range(-32768, 32768, 37):
        want = min(pcm >> 4, 2047) << 4
        assert s.word_to_pcm16(s.pcm16_to_word(pcm)) == want


def test_hz_period_conversion():
    assert s.hz_to_period_ns(22050) == 45351
    assert s.hz_to_period_ns(44100) == 22676
    assert s.hz_to_period_ns(32000) == 31250
    assert s.period_ns_to_hz(45351) == 22050
    assert s.period_ns_to_hz(0) == 0
    for hz in (0, 1000, 100000):
        with pytest.raises(ValueError):
            s.hz_to_period_ns(hz)
