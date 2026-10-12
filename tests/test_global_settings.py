# tests for core/global_settings.py - the raw-byte <-> value maps for the S2000/S3000 GLOBAL page. The (raw, value) pairs are what the real S2000
# held when tools/s2000_misc_probe.py dumped it (2026-10-09); a mapping that doesn't reproduce them has drifted from the measurement.

import pytest

from core.global_settings import (
    BLOCK_SETTINGS,
    GLOBAL_SETTINGS,
    MDATA_BLOCK_LENGTH,
    PROGRAM_CHANGE_OMNI,
)


def test_every_register_is_distinct():
    registers = [s.register for s in GLOBAL_SETTINGS.values()]
    assert len(registers) == len(set(registers))


@pytest.mark.parametrize(
    "key, raw, value",
    [
        ("external_controller", 0, 0),  # Breath
        ("external_controller", 1, 1),  # Footpedal
        ("external_controller", 2, 2),  # Volume
        ("output_level", 9, -18),
        ("output_level", 12, 0),
        ("output_level", 15, 18),
        ("tune_semitones", 215, -50),
        ("tune_semitones", 9, 0),
        ("tune_semitones", 59, 50),
        ("tune_cents", 0, -50),
        ("tune_cents", 50, 0),
        ("tune_cents", 100, 50),
        ("program_change_channel", 0, 0),  # Off
        ("program_change_channel", 1, 1),
        ("program_change_channel", 16, 16),
        ("program_change_channel", 17, PROGRAM_CHANGE_OMNI),
        ("play_note", 21, 21),  # A-1
        ("play_note", 60, 60),  # C3
        ("play_note", 127, 127),  # G8
        ("play_channel", 0, 1),
        ("play_channel", 15, 16),
        ("play_velocity", 0, 0),
        ("play_velocity", 127, 127),
        ("scsi_disk_id", 0, 0),
        ("scsi_disk_id", 7, 7),
        ("scsi_sector", 0, 0),  # 512 B
        ("scsi_sector", 1, 1),  # 1 KB
        ("scsi_local_id", 6, 6),
    ],
)
def test_measured_points_decode_and_encode(key, raw, value):
    setting = GLOBAL_SETTINGS[key]
    assert setting.decode(raw) == value
    assert setting.encode(value) == raw


@pytest.mark.parametrize(
    "key, raw",
    [
        ("external_controller", 3),
        ("output_level", 8),
        ("output_level", 16),
        ("tune_semitones", 60),  # signed -> 60 - 9 = 51
        ("tune_semitones", 214),
        ("tune_cents", 101),
        ("program_change_channel", 18),
        ("play_note", 20),
        ("play_channel", 16),
        ("play_velocity", 128),
        ("scsi_disk_id", 8),
        ("scsi_sector", 2),
        ("scsi_local_id", 255),
    ],
)
def test_a_raw_byte_the_panel_cannot_show_is_refused(key, raw):
    with pytest.raises(ValueError):
        GLOBAL_SETTINGS[key].decode(raw)


@pytest.mark.parametrize("key", list(GLOBAL_SETTINGS))
def test_every_decodable_raw_byte_round_trips(key):
    setting = GLOBAL_SETTINGS[key]
    decodable = 0
    for raw in range(256):
        try:
            value = setting.decode(raw)
        except ValueError:
            continue
        decodable += 1
        assert setting.encode(value) == raw
    assert decodable > 1


def test_semitone_tune_wraps_as_a_signed_byte():
    # -50 + 9 = -41 -> 215 on the wire
    assert GLOBAL_SETTINGS["tune_semitones"].encode(-50) == 215
    assert GLOBAL_SETTINGS["tune_semitones"].encode(-9) == 0
    assert GLOBAL_SETTINGS["tune_semitones"].encode(-10) == 255


# --- the whole-block misc data (BLOCK_SETTINGS) ---------------------------------------------------
# The block states are what the real S2000 held per panel setting (mdump pc1/pc6/pc_off/pc_omni, cents_*, tune*; 2026-10-09).


def _block(*first, **at):
    block = bytearray(list(first) + [0] * (MDATA_BLOCK_LENGTH - len(first)))
    for offset, value in at.items():
        block[int(offset.lstrip("o"))] = value
    return bytes(block)


def test_the_block_settings_are_the_three_that_the_registers_get_wrong():
    assert set(BLOCK_SETTINGS) == {"program_change_channel", "tune_semitones", "tune_cents"}
    assert MDATA_BLOCK_LENGTH == 48


@pytest.mark.parametrize(
    "first_three, value",
    [
        ([0, 0, 1], 1),  # channel 1
        ([5, 0, 1], 6),  # channel 6
        ([15, 0, 1], 16),
        ([0, 0, 0], 0),  # Off
        ([15, 1, 1], PROGRAM_CHANGE_OMNI),  # Omni (the channel byte keeps its last value)
    ],
)
def test_program_change_states_decode_and_reencode(first_three, value):
    setting = BLOCK_SETTINGS["program_change_channel"]
    block = _block(*first_three)
    assert setting.decode(block) == value
    # writing the value it already shows changes nothing
    assert setting.encode(block, value) == block


def test_program_change_encoding_only_touches_its_three_bytes():
    setting = BLOCK_SETTINGS["program_change_channel"]
    block = _block(0, 0, 1, 7, 7, 7, 50, 9, 12, o20=33)
    for value in range(0, 18):
        new = setting.encode(block, value)
        assert new[3:] == block[3:]
        assert setting.decode(new) == value


def test_program_change_keeps_the_channel_byte_under_off_and_omni_like_the_panel():
    setting = BLOCK_SETTINGS["program_change_channel"]
    assert setting.encode(_block(5, 0, 1), 0)[:3] == bytes([5, 0, 0])
    assert setting.encode(_block(5, 0, 1), PROGRAM_CHANGE_OMNI)[:3] == bytes([5, 1, 1])
    assert setting.encode(_block(15, 1, 1), 3)[:3] == bytes([2, 0, 1])


@pytest.mark.parametrize("first_three", [[16, 0, 1], [0, 2, 1], [0, 0, 7], [0, 1, 2]])
def test_a_program_change_state_the_panel_cannot_show_is_refused(first_three):
    with pytest.raises(ValueError):
        BLOCK_SETTINGS["program_change_channel"].decode(_block(*first_three))


@pytest.mark.parametrize("value", [-1, 18])
def test_an_out_of_range_program_change_is_never_encoded(value):
    with pytest.raises(ValueError):
        BLOCK_SETTINGS["program_change_channel"].encode(_block(0, 0, 1), value)


@pytest.mark.parametrize(
    "key, offset, raw, value",
    [
        ("tune_cents", 6, 0, -50),
        ("tune_cents", 6, 50, 0),
        ("tune_cents", 6, 100, 50),
        ("tune_semitones", 7, 215, -50),
        ("tune_semitones", 7, 9, 0),
        ("tune_semitones", 7, 59, 50),
        ("tune_semitones", 7, 21, 12),
    ],
)
def test_tune_offsets_match_the_measured_points(key, offset, raw, value):
    setting = BLOCK_SETTINGS[key]
    block = _block(0, 0, 1, o6=50, o7=9, o8=12)
    shown = bytearray(block)
    shown[offset] = raw
    assert setting.decode(bytes(shown)) == value
    new = setting.encode(block, value)
    assert new[offset] == raw
    assert [i for i in range(48) if new[i] != block[i]] == ([offset] if new[offset] != block[offset] else [])


@pytest.mark.parametrize("key, value", [("tune_semitones", 51), ("tune_semitones", -51), ("tune_cents", 51), ("tune_cents", -51)])
def test_an_out_of_range_tune_is_never_encoded(key, value):
    with pytest.raises(ValueError):
        BLOCK_SETTINGS[key].encode(_block(0, 0, 1), value)


@pytest.mark.parametrize("key", list(BLOCK_SETTINGS))
@pytest.mark.parametrize("length", [0, 47, 49, 96])
def test_a_block_of_the_wrong_length_is_refused(key, length):
    setting = BLOCK_SETTINGS[key]
    with pytest.raises(ValueError):
        setting.decode(bytes(length))
    with pytest.raises(ValueError):
        setting.encode(bytes(length), 0)
