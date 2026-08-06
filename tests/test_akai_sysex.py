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
