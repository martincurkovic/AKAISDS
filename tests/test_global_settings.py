# tests for core/global_settings.py - the raw-byte <-> value maps for the S2000/S3000 GLOBAL page. The (raw, value) pairs are what the real S2000
# held when tools/s2000_misc_probe.py dumped it (2026-10-09); a mapping that doesn't reproduce them has drifted from the measurement.

import pytest

from core.global_settings import GLOBAL_SETTINGS, PROGRAM_CHANGE_OMNI


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
