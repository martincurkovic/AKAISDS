"""
The S2000/S3000's GLOBAL-page settings, as misc BYTE registers.

Neither Akai spec nor `s3k` names these registers (the misc data-index table is in no document): each was found by dumping every misc register with
the setting on two or more values (`tools/s2000_misc_probe.py`, read-only, measured on a real S2000, 2026-10-09) and seeing which one moved. Only
`scsi_disk_id` (11) was already known to `s3k` (`select_drive`). Pure - no Qt, no MIDI; `BridgeWorker` reads/writes the registers, this module only
says which register and how the raw byte maps to the value the editor shows.

`decode` raises `ValueError` for a raw byte outside what the panel can show: a firmware that keeps something else there is reported as "no value" rather
than shown as a wrong one. `encode` raises `ValueError` for a value the register can't hold, so nothing out of range reaches the wire.

What each measurement actually pinned down (the dumps: 2-4 points per setting, so a mapping is only as good as its points):
- external_controller (38): Breath 0 / Footpedal 1 / Volume 2 - three points, and two dumps of one setting were identical. Writing it is CONFIRMED on hardware.
- output_level (15): -18 dB -> 9, 0 -> 12, +18 dB -> 15. The 6 dB STEP between them is INFERRED (three points on a line); the in-between values were not dumped.
- tune_semitones (64): -50 -> 215, 0 -> 9, +50 -> 59, i.e. a signed byte holding semitones + 9 (both extremes fit the same offset). The offset of 9 is
  measured, not understood.
- tune_cents (65): -50 -> 0, 0 -> 50, +50 -> 100 (cents + 50).
- program_change_channel (71): Off 0, channels 1/16 -> 1/16, Omni 17 (four points).
- play_note (31): the MIDI note itself (A-1 21, C3 60, G8 127). Register 49 also moved in that dump - the panel's cursor-value register (`s3k`
  `_MISC_CURSOR_VALUE`), noise.
- play_channel (57): channel - 1 (1 -> 0, 16 -> 15). play_velocity (54): the velocity itself (0, 127).
- scsi_disk_id (11), scsi_local_id (12): the ID itself (0/5/7, 0/6/7). scsi_sector (14): 512 B 0 / 1 KB 1.
`s3k` itself deliberately never writes `scsi_local_id` ("changing what the sampler answers to, over the bus it is answering on, is not something this
offers"); this module can, because the editor was asked to.

Not here: the MIDI sysex channel (changing it would cut this connection) and MIDI-via-SCSI (never dumped).
"""

from dataclasses import dataclass
from typing import Callable

EXTERNAL_CONTROLLER_LABELS = ("Breath", "Footpedal", "Volume")
SECTOR_SIZE_LABELS = ("512 B", "1 KB")

# the S2000/S3000's own "Off" and "Omni" ends of the program-change channel register
PROGRAM_CHANGE_OFF = 0
PROGRAM_CHANGE_OMNI = 17

_OUTPUT_LEVEL_STEP_DB = 6
_OUTPUT_LEVEL_ZERO_RAW = 12
_OUTPUT_LEVEL_STEPS_EACH_WAY = 3
_TUNE_SEMITONES_OFFSET = 9
_TUNE_CENTS_OFFSET = 50


@dataclass(frozen=True)
class GlobalSetting:
    key: str
    register: int  # misc BYTE bank index
    decode: Callable[[int], int]  # raw byte -> the value the editor shows
    encode: Callable[[int], int]  # that value -> raw byte


def _in_range(name, value, low, high):
    if not low <= value <= high:
        raise ValueError(f"{name} {value} is outside {low}..{high}")
    return value


def _plain(name, low, high):
    """The raw byte IS the value."""

    def check(value):
        return _in_range(name, value, low, high)

    return check, check


def _offset(name, offset, low, high):
    """value = raw - offset, for a register that never goes negative."""

    def decode(raw):
        return _in_range(name, raw - offset, low, high)

    def encode(value):
        _in_range(name, value, low, high)
        return value + offset

    return decode, encode


def _signed_offset(name, offset, low, high):
    """value = signed(raw) - offset: the register is a signed byte."""

    def decode(raw):
        signed = raw - 256 if raw >= 128 else raw
        return _in_range(name, signed - offset, low, high)

    def encode(value):
        _in_range(name, value, low, high)
        return (value + offset) & 0xFF

    return decode, encode


def _output_level():
    low_raw = _OUTPUT_LEVEL_ZERO_RAW - _OUTPUT_LEVEL_STEPS_EACH_WAY
    high_raw = _OUTPUT_LEVEL_ZERO_RAW + _OUTPUT_LEVEL_STEPS_EACH_WAY

    def decode(raw):
        _in_range("output level", raw, low_raw, high_raw)
        return (raw - _OUTPUT_LEVEL_ZERO_RAW) * _OUTPUT_LEVEL_STEP_DB

    def encode(db):
        if db % _OUTPUT_LEVEL_STEP_DB:
            raise ValueError(f"output level {db} dB is not a multiple of {_OUTPUT_LEVEL_STEP_DB}")
        return _in_range("output level", _OUTPUT_LEVEL_ZERO_RAW + db // _OUTPUT_LEVEL_STEP_DB, low_raw, high_raw)

    return decode, encode


def _build():
    settings = [
        GlobalSetting("external_controller", 38, *_plain("external controller", 0, 2)),
        GlobalSetting("output_level", 15, *_output_level()),
        GlobalSetting("tune_semitones", 64, *_signed_offset("tune", _TUNE_SEMITONES_OFFSET, -50, 50)),
        GlobalSetting("tune_cents", 65, *_offset("fine tune", _TUNE_CENTS_OFFSET, -50, 50)),
        GlobalSetting("program_change_channel", 71, *_plain("program change channel", 0, 17)),
        GlobalSetting("play_note", 31, *_plain("play note", 21, 127)),
        # shown 1-16, stored 0-15
        GlobalSetting("play_channel", 57, *_offset("play channel", -1, 1, 16)),
        GlobalSetting("play_velocity", 54, *_plain("play velocity", 0, 127)),
        GlobalSetting("scsi_disk_id", 11, *_plain("SCSI disk ID", 0, 7)),
        GlobalSetting("scsi_sector", 14, *_plain("sector size", 0, 1)),
        GlobalSetting("scsi_local_id", 12, *_plain("local SCSI ID", 0, 7)),
    ]
    return {s.key: s for s in settings}


GLOBAL_SETTINGS = _build()
