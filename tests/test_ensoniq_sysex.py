# tests for core/ensoniq_sysex.py - pure byte-level tests (no Qt, no MIDI).
# Golden vectors come from Ensoniq's own spec documents (the offset and scale-factor examples, the parameter-number rule and the
# ASR-10 parameter tables); see dev_docs/ensoniq-spec-crosscheck.md. Nothing here has run against an Ensoniq unit.

import random

import pytest

from core import ensoniq_sysex as e


# --- words and offsets ----------------------------------------------------------


def test_word12_known_vectors():
    assert e.encode_word12(0) == [0x00, 0x00]
    assert e.encode_word12(0xFFF) == [0x3F, 0x3F]
    assert e.encode_word12(0x200) == [0x08, 0x00]
    assert e.decode_word12(0x08, 0x00) == 0x200


def test_word12_rejects_a_13_bit_value():
    with pytest.raises(ValueError):
        e.encode_word12(0x1000)


def test_word16_known_vectors():
    assert e.encode_word16(0xFFFF) == [0x0F, 0x3F, 0x3F]
    assert e.encode_word16(0x1234) == [0x01, 0x08, 0x34]
    assert e.decode_word16(0x01, 0x08, 0x34) == 0x1234


def test_word_round_trips_and_stays_7_bit():
    rng = random.Random(1)
    for _ in range(500):
        v = rng.randrange(0x10000)
        b = e.encode_word16(v)
        assert all(x <= 0x3F for x in b)
        assert e.decode_word16(*b) == v
        v12 = v & 0xFFF
        assert e.decode_word12(*e.encode_word12(v12)) == v12


def test_offset_examples_from_the_spec():
    # "the offset 0AAAA hex would be sent as 00, 0A, 2A and 2A hex"
    assert e.encode_offset(0x0AAAA) == [0x00, 0x0A, 0x2A, 0x2A]
    # the EPS-16+ spec's example; the EPS and ASR-10 specs print 00 00 40 00, a typo (40 isn't a legal 6-bit byte)
    assert e.encode_offset(0x00200) == [0x00, 0x00, 0x08, 0x00]


def test_offset_is_20_bits():
    assert e.encode_offset(0xFFFFF) == [0x03, 0x3F, 0x3F, 0x3F]
    with pytest.raises(ValueError):
        e.encode_offset(0x100000)
    with pytest.raises(ValueError):
        e.encode_offset(-1)


def test_offset_round_trip():
    rng = random.Random(2)
    for _ in range(500):
        v = rng.randrange(0x100000)
        assert e.decode_offset(e.encode_offset(v)) == v


def test_scale_factor_examples_split_the_same_way_as_an_offset():
    # EPS spec 4.4: 255.99 (0xFF7F) is 00 0F 3D 3F, 1.0 (0x0100) is 00 00 04 00. This is the evidence that the offset typo is a typo
    def split(value):
        return e.encode_word12(value >> 12) + e.encode_word12(value & 0xFFF)

    assert split(0xFF7F) == [0x00, 0x0F, 0x3D, 0x3F]
    assert split(0x0100) == [0x00, 0x00, 0x04, 0x00]


# --- samples -------------------------------------------------------------------------


def test_sample_known_vectors():
    assert e.encode_sample(0) == [0x00, 0x00, 0x00]
    assert e.encode_sample(-1) == [0x0F, 0x3F, 0x3F]
    assert e.encode_sample(32767) == [0x07, 0x3F, 0x3F]
    assert e.encode_sample(-32768) == [0x08, 0x00, 0x00]


def test_sample_round_trip_at_the_extremes_and_random():
    values = [-32768, -32767, -1, 0, 1, 32766, 32767]
    rng = random.Random(3)
    values += [rng.randrange(-32768, 32768) for _ in range(500)]
    assert e.decode_samples(e.encode_samples(values)) == values


def test_sample_out_of_range_is_refused():
    with pytest.raises(ValueError):
        e.encode_sample(32768)
    with pytest.raises(ValueError):
        e.encode_sample(-32769)


def test_decode_samples_rejects_a_ragged_or_8_bit_stream():
    with pytest.raises(ValueError):
        e.decode_samples([0, 0, 0, 0])
    with pytest.raises(ValueError):
        e.decode_samples([0, 0x80, 0])


# --- parameter numbers and values --------------------------------------------------------


def test_param_number_is_one_12_bit_word():
    # EPS spec section 9: Free System Blocks is "$34 $00", not "$0D $00" (page 13)
    assert e.encode_param_number(e.PAGE_SYSTEM, e.SYSTEM_ITEM_FREE_BLOCKS) == [0x34, 0x00]
    # EPS: MIDI SysEx Enable is page 12 item 5; ASR-10: system page "34" item "12" hex
    assert e.encode_param_number(e.PAGE_MIDI, 5) == [0x30, 0x05]
    assert e.encode_param_number(e.PAGE_SYSTEM, 0x12) == [0x34, 0x12]
    # ASR-10 wavesample page "20", absolute start "15" hex
    assert e.encode_param_number(e.PAGE_WAVESAMPLE, e.WAVESAMPLE_ITEM_START) == [0x20, 0x15]
    # ASR-10 envelope pages 04 / 08 / 0C
    assert [e.encode_param_number(p, 0)[0] for p in (1, 2, 3)] == [0x04, 0x08, 0x0C]


def test_param_number_item_above_63_carries_into_the_first_byte():
    assert e.encode_param_number(8, 0x45) == [0x21, 0x05]
    assert e.decode_param_number(0x21, 0x05) == (8, 0x45)


def test_param_number_round_trip_and_range():
    for page in range(16):
        for item in (0, 1, 0x15, 0x3F, 0x40, 0xFF):
            assert e.decode_param_number(*e.encode_param_number(page, item)) == (page, item)
    with pytest.raises(ValueError):
        e.encode_param_number(16, 0)
    with pytest.raises(ValueError):
        e.encode_param_number(0, 256)


def test_param_value_is_24_bits_in_two_12_bit_words():
    assert e.encode_param_value(0) == [0, 0, 0, 0]
    assert e.encode_param_value(0xFFFFFF) == [0x3F, 0x3F, 0x3F, 0x3F]
    assert e.encode_param_value(0x000200) == [0x00, 0x00, 0x08, 0x00]
    assert e.decode_param_value(e.encode_param_value(0x123456)) == 0x123456


def test_param_value_negative_is_twos_complement():
    assert e.decode_param_value(e.encode_param_value(-127), signed=True) == -127
    assert e.decode_param_value(e.encode_param_value(-127)) == 0xFFFF81
    with pytest.raises(ValueError):
        e.encode_param_value(0x1000000)


# --- context, framing, commands -----------------------------------------------------------


def test_context_is_three_12_bit_words():
    assert e.encode_context(2, 0, 1) == [0, 2, 0, 0, 0, 1]
    assert e.decode_context(e.encode_context(7, 7, 127)) == (7, 7, 127)


def test_context_range_checks():
    for args in ((8, 0, 1), (0, 8, 1), (0, 0, 128), (-1, 0, 1)):
        with pytest.raises(ValueError):
            e.encode_context(*args)


def test_head_carries_the_base_channel_minus_one():
    assert e.head(0) == [0x0F, 0x03, 0x00]
    assert e.head(5) == [0x0F, 0x03, 0x05]
    with pytest.raises(ValueError):
        e.head(16)


def test_get_instrument_message():
    assert e.build_get_instrument(2) == [0x0F, 0x03, 0x00, 0x03, 0, 2, 0, 0, 0, 1]


def test_get_wavesample_data_carries_the_range_as_two_offsets():
    msg = e.build_get_wavesample_data(1, 2, 3, 0x200, 0x0AAAA, channel=4)
    assert msg[:4] == [0x0F, 0x03, 0x04, 0x06]
    assert msg[4:10] == [0, 1, 0, 2, 0, 3]
    assert msg[10:] == [0x00, 0x00, 0x08, 0x00, 0x00, 0x0A, 0x2A, 0x2A]
    parsed = e.parse_message(msg)
    assert e.parse_context_and_range(parsed.body) == (1, 2, 3, 0x200, 0x0AAAA)


def test_a_range_that_ends_before_it_starts_is_refused():
    with pytest.raises(ValueError):
        e.build_get_wavesample_data(0, 0, 1, 10, 5)


def test_put_parameter_message():
    msg = e.build_put_parameter(0, 0, 1, e.PAGE_WAVESAMPLE, e.WAVESAMPLE_ITEM_LOOP_MODE, 2)
    assert msg == [0x0F, 0x03, 0x00, 0x11, 0, 0, 0, 0, 0, 1, 0x20, 0x00, 0, 0, 0, 2]


def test_get_parameter_message_for_free_memory():
    msg = e.build_get_parameter(0, 0, 1, e.PAGE_SYSTEM, e.SYSTEM_ITEM_FREE_BLOCKS)
    assert msg[3] == e.CMD_GET_PARAMETER
    assert msg[-2:] == [0x34, 0x00]


def test_create_commands_use_the_command_codes_from_the_spec():
    assert e.build_create_instrument(3)[3] == 0x15
    assert e.build_create_layer(3, 0)[3] == 0x16
    assert e.build_create_wavesample(3, 0, 1)[3] == 0x19
    assert e.build_truncate_wavesample(3, 0, 1)[3] == 0x1E


def test_put_wavesample_data_first_message_then_data_only_message():
    first = e.build_put_wavesample_data(0, 0, 1, 0, 4)
    assert first[3] == 0x0F
    second = e.build_data_message([1, -1, 0], channel=0)
    assert second == [0x0F, 0x03, 0x00] + [0, 0, 1] + [0x0F, 0x3F, 0x3F] + [0, 0, 0]
    assert e.parse_data_message(second) == [1, -1, 0]


def test_the_commands_that_destroy_or_write_the_disk_are_never_sent():
    assert e.NEVER_SEND == {0x14, 0x17, 0x1A, 0x1C}
    # nothing the transfer builders emit uses them
    built = [
        e.build_get_instrument(0),
        e.build_get_layer(0, 0),
        e.build_get_wavesample_parameters(0, 0, 1),
        e.build_get_wavesample_data(0, 0, 1, 0, 10),
        e.build_create_instrument(0),
        e.build_create_layer(0, 0),
        e.build_create_wavesample(0, 0, 1),
        e.build_truncate_wavesample(0, 0, 1),
        e.build_put_wavesample_parameters(0, 0, 1),
        e.build_put_wavesample_data(0, 0, 1, 0, 10),
        e.build_put_parameter(0, 0, 1, 8, 0, 0),
        e.build_get_parameter(0, 0, 1, 13, 0),
    ]
    assert not {m[3] for m in built} & e.NEVER_SEND


# --- responses -------------------------------------------------------------------------------


def test_response_round_trip():
    msg = e.build_response(e.STATUS_ACK, channel=2)
    assert msg == [0x0F, 0x03, 0x02, 0x01, 0x00, 0x00]
    assert e.parse_response(e.parse_message(msg)) == e.STATUS_ACK
    nak = e.parse_message(e.build_response(e.STATUS_NAK))
    assert e.parse_response(nak) == 0x17


def test_cancel_has_no_body():
    assert e.build_cancel() == [0x0F, 0x03, 0x00, 0x02]


def test_parse_message_rejects_other_makers_and_8_bit_bytes():
    with pytest.raises(ValueError):
        e.parse_message([0x47, 0x00, 0x05])
    with pytest.raises(ValueError):
        e.parse_message([0x0F, 0x03, 0x00, 0x01, 0x80, 0x00])
    with pytest.raises(ValueError):
        e.parse_message([0x0F, 0x03, 0x00])


def test_parse_response_refuses_a_non_response():
    with pytest.raises(ValueError):
        e.parse_response(e.parse_message(e.build_get_instrument(0)))


def test_status_codes_match_the_spec_table():
    assert e.STATUS_NAMES[0x05] == "invalid instrument"
    assert e.STATUS_NAMES[0x0C] == "memory full"
    assert e.STATUS_NAMES[0x17] == "NAK"
    assert e.STATUS_NAMES[0x1E] == "quiet layer"
    assert sorted(e.STATUS_NAMES) == list(range(0x1F))
    assert e.STATUS_INVALID_INSTRUMENT == 0x05 and e.STATUS_NO_LAYER_EDIT == 0x18


def test_status_text_is_short_and_unknown_codes_still_read():
    assert "memory" in e.status_text(e.STATUS_MEMORY_FULL)
    assert "0x7F" in e.status_text(0x7F)
    assert all(len(t) < 80 for t in e._STATUS_USER_TEXT.values())


def test_chunk_ranges():
    assert e.chunk_ranges(0, 5000) == []
    assert e.chunk_ranges(5000, 5000) == [(0, 5000)]
    assert e.chunk_ranges(12001, 5000) == [(0, 5000), (5000, 10000), (10000, 12001)]
    with pytest.raises(ValueError):
        e.chunk_ranges(10, 0)


# --- parameter blocks -------------------------------------------------------------------------


def test_block_uses_the_high_byte_of_each_16_bit_word():
    assert e.encode_block([0x41]) == e.encode_word16(0x4100)
    assert e.decode_block(e.encode_block([0, 1, 255, 128])) == [0, 1, 255, 128]
    with pytest.raises(ValueError):
        e.encode_block([256])


def test_decode_block_checks_the_length():
    data = e.encode_block([0] * 4)
    with pytest.raises(ValueError):
        e.decode_block(data, expected_words=5)
    with pytest.raises(ValueError):
        e.decode_block(data[:-1])


def test_name_codec_pads_to_12_and_strips_trailing_spaces():
    words = e.encode_name("KICK")
    assert len(words) == 12 and words[:4] == [75, 73, 67, 75] and words[4:] == [32] * 8
    assert e.decode_name(words) == "KICK"
    assert e.decode_name([0] * 12) == ""
    assert e.encode_name("A" * 20) == [65] * 12
    assert e.encode_name("café")[3] == ord("?")


def test_wavesample_block_size_and_round_trip():
    ws = e.WavesampleParams.blank()
    assert len(ws.to_block()) == e.WAVESAMPLE_BLOCK_WORDS == 139
    ws.name = "SNARE 1"
    ws.root_key = 60
    ws.loop_mode = e.LOOP_FORWARD
    ws.sample_rate_code = 20
    ws.key_range = (0, 127)
    ws.start = 0x200
    ws.end = 1000
    ws.loop_start = 100
    ws.set_loop_end(900, 5)
    data = e.encode_block(ws.to_block())
    assert len(data) == 139 * 3
    back = e.WavesampleParams.from_block(e.decode_block(data, 139))
    assert back.name == "SNARE 1"
    assert (back.root_key, back.loop_mode, back.sample_rate_code) == (60, 2, 20)
    assert back.key_range == (0, 127)
    assert (back.start, back.end, back.loop_start) == (0x200, 1000, 100)
    assert back.loop_end == (900, 5)
    assert back.length == 1000 - 0x200


def test_wavesample_offsets_are_left_justified_and_shifted_by_9():
    ws = e.WavesampleParams.blank()
    ws.start = 0x200  # 0x200 << 9 = 0x40000 -> hi bytes 00 04 00 00
    assert ws.raw[e.WS_START : e.WS_START + 4] == [0x00, 0x04, 0x00, 0x00]
    ws.set_loop_end(1, 0)  # (1 << 4) << 5 = 0x200
    assert ws.raw[e.WS_LOOP_END : e.WS_LOOP_END + 4] == [0x00, 0x00, 0x02, 0x00]


def test_wavesample_field_positions_match_the_spec_table():
    assert (e.WS_ROOT_KEY, e.WS_FINE_TUNE, e.WS_LOOP_MODE) == (80, 86, 114)
    assert (e.WS_START, e.WS_END, e.WS_LOOP_START, e.WS_LOOP_END) == (115, 119, 123, 127)
    assert (e.WS_SAMPLE_RATE, e.WS_KEY_LO, e.WS_KEY_HI) == (131, 132, 133)


def test_unmodelled_words_survive_a_round_trip_untouched():
    rng = random.Random(4)
    raw = [rng.randrange(256) for _ in range(139)]
    ws = e.WavesampleParams.from_block(raw)
    ws.root_key = raw[e.WS_ROOT_KEY]  # the same value: nothing else may move
    assert ws.to_block() == raw


def test_wavesample_block_of_the_wrong_length_is_refused():
    with pytest.raises(ValueError):
        e.WavesampleParams.from_block([0] * 138)


def test_block_message_is_head_plus_words_only():
    ws = e.WavesampleParams.blank()
    msg = e.build_block_message(ws.to_block(), channel=1)
    assert msg[:3] == [0x0F, 0x03, 0x01] and len(msg) == 3 + 139 * 3
    assert e.parse_block_message(msg, 139) == ws.to_block()


def test_instrument_helpers():
    values = e.encode_name("PIANO") + [0] * 3 + [42] + [0] * (e.INSTRUMENT_BLOCK_WORDS - 16)
    assert len(values) == e.INSTRUMENT_BLOCK_WORDS
    assert e.instrument_name(values) == "PIANO"
    assert e.instrument_size_blocks(values) == 42


# --- sample rate ----------------------------------------------------------------------------------


def test_rate_code_is_a_period_of_1_6_microseconds_per_step():
    assert e.rate_code_to_hz(20) == pytest.approx(31250.0)
    assert e.rate_code_to_hz(10) == pytest.approx(62500.0)
    with pytest.raises(ValueError):
        e.rate_code_to_hz(0)
    with pytest.raises(ValueError):
        e.rate_code_to_hz(128)


def test_44k1_is_not_representable_so_the_nearest_code_is_off_pitch():
    code = e.hz_to_rate_code(44100)
    assert code == 14
    assert abs(e.rate_code_to_hz(code) - 44100) > 400  # about 1.2 % sharp: a send has to resample or accept this
    assert e.hz_to_rate_code(31250) == 20
    assert e.hz_to_rate_code(1) == 127
