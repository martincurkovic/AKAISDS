# tests for core/akai_sysex.py
# should be fast to run as they don't require Qt

from core import akai_sysex


def test_build_slist_request_default_channel():
    assert akai_sysex.build_slist_request() == [0x47, 0x00, 0x04, 0x48]


def test_build_slist_request_respects_channel():
    # check to ensure updated channel number is reflected in the slist request
    request = akai_sysex.build_slist_request(channel=5)
    assert request[1] == 5


def test_tuning_offset_round_trip():
    # decode_tuning_offset must be the exact inverse of decode_tuning_offset
    for semitones in [0, 0.5, -0.5, 1.0, -1.0, 12.0, -12.0, 0.25, -0.25]:
        encoded = akai_sysex.encode_tuning_offset(semitones)
        decoded = akai_sysex.decode_tuning_offset(encoded)
        assert abs(decoded - semitones) < (
            1 / 256
        )  # ie, within one fixed point step (good enough for government work...)


def test_sdata_message_round_trip():
    # build SDATA message, parse it back as if it was a response
    # confirms whether every field survived round trip
    msg = akai_sysex.build_sdata_message(
        name="TEST KEY",
        sample_length=5000,
        sample_rate=44100,
        sample_number=7,
        channel=3,
        original_pitch=64,
        playback_type=2,
    )
    payload = msg[1:-1]  # strip F0/F7 wrapper that mido adds

    result = akai_sysex.parse_sdata_response(payload)

    assert result["sample_number"] == 7
    assert result["name"] == "TEST KEY"
    assert result["root_key"] == 64
    assert result["sample_length"] == 5000
    assert result["sample_rate"] == 44100
    assert result["detune"] == 0.0
    assert result["bit_depth"] == 16


def test_dels_request_targets_correct_sample_and_channel():
    # deliberately using a massive sample number so it uses both bytes of the sample number
    # anything less than 128 would just leave MSB untested (ie, always 0)
    request = akai_sysex.build_dels_request(sample_number=200, channel=2)
    assert request[1] == 2  # channel number
    assert request[2] == 0x14  # DELS function code
    assert request[4] == (200 & 0x7F)  # sample number LSB
    assert request[5] == (200 >> 7) & 0x7F  # sample number MSB


def test_rename_request_only_touches_name_bytes():
    # the whole point of offset-write rename trick is that it does NOT re-send the whole audio sample
    # just confirm functino code is in the expected offset write (0x2C) not full SDATA send (0x0B)
    request = akai_sysex.build_rename_sample_request(3, "NEW NAME", channel=0)
    assert request[2] == 0x2C


# -----------------------
# NAME ENCODING
# -----------------------


def test_encode_name_pads_and_uppercases():
    encoded = akai_sysex.encode_name("kick")
    assert len(encoded) == 12
    assert akai_sysex.decode_name(encoded) == "KICK        "


def test_encode_name_truncates_long_names():
    encoded = akai_sysex.encode_name("A VERY LONG SAMPLE NAME")
    assert len(encoded) == 12
    assert akai_sysex.decode_name(encoded) == "A VERY LONG "


def test_encode_name_unknown_char_falls_back_to_space():
    encoded = akai_sysex.encode_name("HI!")
    decoded = akai_sysex.decode_name(encoded)
    assert decoded[2] == " "  # '!' isn't in AKAI_CHAR_MAP


def test_build_stereo_channel_name_appends_suffix():
    assert akai_sysex.build_stereo_channel_name("KICK", "-L") == "KICK-L"


def test_build_stereo_channel_name_truncates_to_fit_total_length():
    name = akai_sysex.build_stereo_channel_name(
        "A VERY LONG SAMPLE NAME", "-R", total_length=12
    )
    assert len(name) == 12
    assert name.endswith("-R")


# -----------------------
# SLIST PARSING
# -----------------------


def test_parse_slist_response_decodes_names():
    names_in = ["KICK", "SNARE", "HAT"]
    payload = [0x47, 0x00, 0x05, 0x48, len(names_in), 0x00]
    for name in names_in:
        payload.extend(akai_sysex.encode_name(name))

    count, names = akai_sysex.parse_slist_response(payload)
    assert count == len(names_in)
    assert names == [
        akai_sysex.decode_name(akai_sysex.encode_name(n)) for n in names_in
    ]


# -----------------------
# REQUEST BUILDERS
# -----------------------


def test_build_rsdata_request_encodes_sample_and_channel():
    # deliberately using a sample number > 127 so both LSB and MSB bytes get exercised
    request = akai_sysex.build_rsdata_request(sample_number=200, channel=3)
    assert request[1] == 3
    assert request[2] == 0x0A
    assert request[4] == (200 & 0x7F)
    assert request[5] == (200 >> 7) & 0x7F


def test_build_rspack_request_encodes_offset_and_count():
    request = akai_sysex.build_rspack_request(
        sample_number=5, offset=1000, num_samples=2000, interval=1, function=0, channel=2
    )
    assert request[1] == 2
    assert request[2] == 0x0C
    assert request[4] == 5  # sample number LSB
    assert request[5] == 0  # sample number MSB

    offset_bytes = request[6:10]
    decoded_offset = (
        offset_bytes[0]
        | (offset_bytes[1] << 7)
        | (offset_bytes[2] << 14)
        | (offset_bytes[3] << 21)
    )
    assert decoded_offset == 1000

    num_bytes = request[10:14]
    decoded_num = (
        num_bytes[0] | (num_bytes[1] << 7) | (num_bytes[2] << 14) | (num_bytes[3] << 21)
    )
    assert decoded_num == 2000

    assert request[14] == 1  # interval
    assert request[15] == 0  # function


# -----------------------
# BANDWIDTH/TUNING
# -----------------------


def test_compute_bandwidth_and_tuning_picks_native_rates_with_no_offset():
    bandwidth, offset = akai_sysex.compute_bandwidth_and_tuning(44100)
    assert bandwidth == 1
    assert abs(offset) < 0.01

    bandwidth, offset = akai_sysex.compute_bandwidth_and_tuning(22050)
    assert bandwidth == 0
    assert abs(offset) < 0.01


def test_compute_bandwidth_and_tuning_offsets_non_native_rate():
    bandwidth, offset = akai_sysex.compute_bandwidth_and_tuning(48000)
    assert bandwidth == 1  # closer to 44100 than 22050
    assert offset > 0  # 48000 plays sharper/faster than native 44100


def test_to_nibble_pairs_splits_low_high_nibbles():
    assert akai_sysex.to_nibble_pairs([0xAB]) == [0x0B, 0x0A]


# -----------------------
# PDATA / KDATA (create program / create keygroup)
# -----------------------


def test_pdata_request_shape_and_round_trip():
    raw_header = bytes(range(192))
    frame = akai_sysex.build_pdata_request(200, raw_header, channel=2)

    assert frame[0] == 0xF0
    assert frame[-1] == 0xF7
    assert frame[1] == 0x47  # akai manufacturer id
    assert frame[2] == 2  # channel
    assert frame[3] == 0x07  # PDATA function code
    assert frame[4] == 0x48  # S1000-family model id
    assert frame[5] == 200 & 0x7F  # program number LSB
    assert frame[6] == (200 >> 7) & 0x7F  # program number MSB - >=128 so this is exercised
    nibbled = frame[7:-1]
    assert len(nibbled) == 192 * 2
    decoded = bytes(
        (nibbled[i] & 0x0F) | ((nibbled[i + 1] & 0x0F) << 4)
        for i in range(0, len(nibbled), 2)
    )
    assert decoded == raw_header


def test_kdata_request_shape_and_round_trip():
    raw_header = bytes(range(192))
    frame = akai_sysex.build_kdata_request(5, 12, raw_header, channel=1)

    assert frame[0] == 0xF0
    assert frame[-1] == 0xF7
    assert frame[1] == 0x47  # akai manufacturer id
    assert frame[2] == 1  # channel
    assert frame[3] == 0x09  # KDATA function code
    assert frame[4] == 0x48
    assert frame[5] == 5  # program number LSB
    assert frame[6] == 0  # program number MSB
    assert frame[7] == 12  # keygroup number
    nibbled = frame[8:-1]
    assert len(nibbled) == 192 * 2
    decoded = bytes(
        (nibbled[i] & 0x0F) | ((nibbled[i + 1] & 0x0F) << 4)
        for i in range(0, len(nibbled), 2)
    )
    assert decoded == raw_header
    assert akai_sysex.to_nibble_pairs([0x00, 0xFF]) == [0x00, 0x00, 0x0F, 0x0F]


def test_pdata_request_default_channel():
    frame = akai_sysex.build_pdata_request(0, bytes(192))
    assert frame[2] == 0  # channel defaults to 0


def test_kdata_request_default_channel():
    frame = akai_sysex.build_kdata_request(0, 0, bytes(192))
    assert frame[2] == 0  # channel defaults to 0
