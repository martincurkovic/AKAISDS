# Akai S900/S950 program (PRGM, function 9) layout: one 76-byte program header
# followed by 1-31 keygroups of 140 bytes each, all DB-encoded.
#
# Ported from s950tools (https://github.com/diemonster/s950tools, MIT,
# Copyright (c) 2026 Brandon Ivers) - internal/protocol/program.go. See
# THIRD_PARTY_NOTICES.md. s950tools took the field layout from
# dxzl/akai-s950's PRGHEDR/KEYGROUP structs; that repo is UNLICENSED, so only
# the byte offsets (facts about the hardware) are carried over, no code.
#
# Offsets are WIRE offsets in the PRGM payload (DB = 2 bytes, DW = 4).
# Evidence level: the program HEADER offsets are pinned by two real S950
# captures (tests/test_s950_program.py). The KEYGROUP offsets have no such
# capture here - they are as reliable as s950tools' copy, so treat a
# keygroup field that reads as nonsense on real hardware as an offset bug
# before a hardware one.
#
# Like S1000Bridge's blocks, everything we don't model is kept in raw bytes
# and written back unchanged - the "undefined" fields are not all unused.

import dataclasses

from core.s950_sysex import (
    NAME_LENGTH,
    decode_db,
    decode_dw,
    decode_name,
    encode_db,
    encode_dw,
    encode_name,
    to_signed,
)

PROGRAM_HEADER_SIZE = 76
KEYGROUP_SIZE = 140
MAX_KEYGROUPS = 31

# header field offsets
_H_NAME = 0  # 20 bytes
_H_KEY_TILT = 32  # DW signed
_H_POSITIONAL_XFADE = 42  # DB
_H_RESERVED1 = 44  # DB, always 255 (the offset-table sanity check)
_H_NUM_KEYGROUPS = 46  # DB
_H_MIDI_PROGRAM = 52  # DB
_H_ENABLE_MIDI_PROGRAM = 54  # DB, 255 = enabled (S950 only)

# keygroup field offsets (all DB unless noted)
_K_UPPER_KEY = 0
_K_LOWER_KEY = 2
_K_VELOCITY_SWITCH = 4
_K_ATTACK = 6
_K_DECAY = 8
_K_SUSTAIN = 10
_K_RELEASE = 12
_K_FILTER_VEL = 14
_K_FILTER_KEY_TRACK = 16
_K_ATTACK_VEL = 18
_K_RELEASE_VEL = 20  # signed
_K_LOUDNESS_VEL = 22
_K_PITCH_WARP_VEL = 24
_K_PITCH_WARP_OFFSET = 26  # signed
_K_PITCH_WARP_RECOVERY = 28
_K_LFO_BUILD = 30
_K_LFO_RATE = 32
_K_LFO_DEPTH = 34
_K_CONTROL_BITS = 36
_K_VOICE_OUT = 38
_K_MIDI_OFFSET = 40
_K_AFTERTOUCH_DEPTH = 42
_K_MODWHEEL_DEPTH = 44
_K_ADSR_TO_VCF = 46  # signed
_K_SOFT_NAME = 48  # 20 bytes
_K_FILTER_ATTACK = 68
_K_FILTER_DECAY = 70
_K_FILTER_SUSTAIN = 72
_K_FILTER_RELEASE = 74
_K_VEL_XFADE_50 = 76
# 78..83 reserved
_K_SOFT_TRANSPOSE = 84  # DW signed, 1/16 semitone
_K_SOFT_FILTER = 88
_K_SOFT_LOUDNESS = 90  # signed
_K_LOUD_NAME = 92  # 20 bytes
# 112..127 reserved
_K_LOUD_TRANSPOSE = 128  # DW signed
_K_LOUD_FILTER = 132
_K_LOUD_LOUDNESS = 134  # signed
# 136..139 reserved

# (attribute, offset, signed) for every single-DB keygroup field
_KEYGROUP_DB_FIELDS = (
    ("upper_key", _K_UPPER_KEY, False),
    ("lower_key", _K_LOWER_KEY, False),
    ("velocity_switch", _K_VELOCITY_SWITCH, False),
    ("attack", _K_ATTACK, False),
    ("decay", _K_DECAY, False),
    ("sustain", _K_SUSTAIN, False),
    ("release", _K_RELEASE, False),
    ("filter_attack", _K_FILTER_ATTACK, False),
    ("filter_decay", _K_FILTER_DECAY, False),
    ("filter_sustain", _K_FILTER_SUSTAIN, False),
    ("filter_release", _K_FILTER_RELEASE, False),
    ("filter_vel", _K_FILTER_VEL, False),
    ("filter_key_track", _K_FILTER_KEY_TRACK, False),
    ("attack_vel", _K_ATTACK_VEL, False),
    ("release_vel", _K_RELEASE_VEL, True),
    ("loudness_vel", _K_LOUDNESS_VEL, False),
    ("pitch_warp_vel", _K_PITCH_WARP_VEL, False),
    ("pitch_warp_offset", _K_PITCH_WARP_OFFSET, True),
    ("pitch_warp_recovery", _K_PITCH_WARP_RECOVERY, False),
    ("adsr_to_vcf", _K_ADSR_TO_VCF, True),
    ("aftertouch_depth", _K_AFTERTOUCH_DEPTH, False),
    ("modwheel_depth", _K_MODWHEEL_DEPTH, False),
    ("lfo_build", _K_LFO_BUILD, False),
    ("lfo_rate", _K_LFO_RATE, False),
    ("lfo_depth", _K_LFO_DEPTH, False),
    ("control_bits", _K_CONTROL_BITS, False),
    ("voice_out", _K_VOICE_OUT, False),
    ("midi_offset", _K_MIDI_OFFSET, False),
    ("vel_xfade_50", _K_VEL_XFADE_50, False),
    ("soft_filter", _K_SOFT_FILTER, False),
    ("soft_loudness", _K_SOFT_LOUDNESS, True),
    ("loud_filter", _K_LOUD_FILTER, False),
    ("loud_loudness", _K_LOUD_LOUDNESS, True),
)
_KEYGROUP_DW_FIELDS = (
    ("soft_transpose", _K_SOFT_TRANSPOSE),
    ("loud_transpose", _K_LOUD_TRANSPOSE),
)
_KEYGROUP_NAME_FIELDS = (
    ("soft_sample", _K_SOFT_NAME),
    ("loud_sample", _K_LOUD_NAME),
)


def _default_keygroup_raw():
    # 78..83: KyUndef1/KyUndef2. s950tools seeds 44 01 44 01 at 80..83 from
    # dxzl's commented example ("could this be BCD fine-pitch?") - meaning
    # unknown, preserved on every round trip.
    raw = bytearray(KEYGROUP_SIZE)
    raw[80:84] = bytes([0x44, 0x01, 0x44, 0x01])
    return bytes(raw)


@dataclasses.dataclass
class Keygroup:
    # The defaults are what s950tools has seen the S950 accept: full-range
    # zone, no samples, a "hold and release" amp envelope.
    lower_key: int = 24  # 24..127
    upper_key: int = 127
    velocity_switch: int = 128  # 0..128; above it the loud sample plays, 128 = soft only

    attack: int = 0  # 0..99
    decay: int = 80
    sustain: int = 99
    release: int = 30

    filter_attack: int = 20  # VCF envelope, 0..99
    filter_decay: int = 20
    filter_sustain: int = 20
    filter_release: int = 20

    filter_vel: int = 10  # velocity -> filter
    filter_key_track: int = 50  # 0..99, ~50 = 1V/oct
    attack_vel: int = 0
    release_vel: int = 0  # signed +-50
    loudness_vel: int = 30
    pitch_warp_vel: int = 0
    pitch_warp_offset: int = 0  # signed +-50
    pitch_warp_recovery: int = 99
    adsr_to_vcf: int = 0  # signed +-50
    aftertouch_depth: int = 0  # aftertouch -> LFO depth
    modwheel_depth: int = 50  # mod wheel -> LFO depth

    lfo_build: int = 64  # LFO ramp-in
    lfo_rate: int = 42
    lfo_depth: int = 0

    # bit0 transpose OFF, bit1 vel-xfade on, bit2 vibrato-desync on,
    # bit3 one-shot trigger, bit4 vel-release mode, bit5 xfade curve
    # (the bit meanings are dxzl's, via s950tools - unverified here)
    control_bits: int = 4
    voice_out: int = 255  # 255 = all outputs; 0..9 individual, 8/9 L/R groups
    midi_offset: int = 0  # 0..15 channel offset
    vel_xfade_50: int = 64

    soft_sample: str = ""  # looked up by NAME at load time
    soft_transpose: int = 0  # signed, 1/16 semitone
    soft_filter: int = 99  # 0..99, 99 brightest
    soft_loudness: int = 0  # signed +-50, 0.375 dB per unit

    loud_sample: str = ""
    loud_transpose: int = 0
    loud_filter: int = 99
    loud_loudness: int = 0

    raw: bytes = dataclasses.field(default_factory=_default_keygroup_raw, compare=False)

    @classmethod
    def from_bytes(cls, block):
        block = bytes(block)
        if len(block) != KEYGROUP_SIZE:
            raise ValueError(f"keygroup is {len(block)} bytes, want {KEYGROUP_SIZE}")
        values = {"raw": block}
        for attr, offset, signed in _KEYGROUP_DB_FIELDS:
            v = decode_db(block[offset], block[offset + 1])
            values[attr] = to_signed(v, 8) if signed else v
        for attr, offset in _KEYGROUP_DW_FIELDS:
            values[attr] = to_signed(decode_dw(block[offset : offset + 4]), 16)
        for attr, offset in _KEYGROUP_NAME_FIELDS:
            values[attr] = decode_name(block[offset : offset + 20])
        return cls(**values)

    def to_bytes(self):
        # a field is only re-encoded when it no longer matches what `raw` already
        # holds, so an untouched field keeps its exact bytes (a name the unit padded
        # with NULs would otherwise come back padded with spaces)
        out = bytearray(self.raw)
        for attr, offset, signed in _KEYGROUP_DB_FIELDS:
            value = getattr(self, attr)
            held = decode_db(out[offset], out[offset + 1])
            if (to_signed(held, 8) if signed else held) != value:
                out[offset : offset + 2] = bytes(encode_db(value))
        for attr, offset in _KEYGROUP_DW_FIELDS:
            value = getattr(self, attr)
            if to_signed(decode_dw(out[offset : offset + 4]), 16) != value:
                out[offset : offset + 4] = bytes(encode_dw(value))
        for attr, offset in _KEYGROUP_NAME_FIELDS:
            value = getattr(self, attr)
            if decode_name(out[offset : offset + 20]) != value.rstrip(" \x00")[:20]:
                out[offset : offset + 20] = bytes(encode_name(value))
        return bytes(out)


def _default_header_raw():
    # s950tools seeds these from dxzl's example: PrUndef3 at 36 = 7E 00 44 01
    # (meaning unknown) and the PrReser1 sentinel at 44 = DB(255).
    raw = bytearray(PROGRAM_HEADER_SIZE)
    raw[36:40] = bytes([0x7E, 0x00, 0x44, 0x01])
    raw[_H_RESERVED1 : _H_RESERVED1 + 2] = bytes(encode_db(255))
    return bytes(raw)


@dataclasses.dataclass
class Program:
    name: str = ""
    key_tilt: int = 0  # key -> loudness scaling, signed +-50
    positional_xfade: bool = False
    midi_program_number: int = 0  # 0..127
    enable_midi_program: bool = True  # S950 only: lets the number above select this program
    keygroups: list = dataclasses.field(default_factory=lambda: [Keygroup()])
    header_raw: bytes = dataclasses.field(default_factory=_default_header_raw, compare=False)

    @property
    def num_keygroups(self):
        # always the real count - the wire's own count byte is recomputed on encode
        return len(self.keygroups)

    @classmethod
    def from_payload(cls, payload):
        payload = bytes(payload)
        if len(payload) < PROGRAM_HEADER_SIZE + KEYGROUP_SIZE:
            raise ValueError(
                f"PRGM payload too short: {len(payload)} bytes "
                f"(need at least {PROGRAM_HEADER_SIZE + KEYGROUP_SIZE})"
            )
        header = payload[:PROGRAM_HEADER_SIZE]
        body = payload[PROGRAM_HEADER_SIZE:]
        if len(body) % KEYGROUP_SIZE:
            raise ValueError(
                f"PRGM body is {len(body)} bytes, not a multiple of the "
                f"{KEYGROUP_SIZE}-byte keygroup size"
            )
        count = len(body) // KEYGROUP_SIZE
        if not 1 <= count <= MAX_KEYGROUPS:
            raise ValueError(f"PRGM has {count} keygroups (want 1..{MAX_KEYGROUPS})")
        # the wire's own keygroup count byte is ignored on purpose: the body
        # length is authoritative, and to_payload() rewrites the byte to match
        return cls(
            name=decode_name(header[_H_NAME : _H_NAME + 20]),
            key_tilt=to_signed(decode_dw(header[_H_KEY_TILT : _H_KEY_TILT + 4]), 16),
            positional_xfade=bool(
                decode_db(header[_H_POSITIONAL_XFADE], header[_H_POSITIONAL_XFADE + 1])
            ),
            midi_program_number=decode_db(
                header[_H_MIDI_PROGRAM], header[_H_MIDI_PROGRAM + 1]
            ),
            enable_midi_program=bool(
                decode_db(
                    header[_H_ENABLE_MIDI_PROGRAM], header[_H_ENABLE_MIDI_PROGRAM + 1]
                )
            ),
            keygroups=[
                Keygroup.from_bytes(body[i * KEYGROUP_SIZE : (i + 1) * KEYGROUP_SIZE])
                for i in range(count)
            ],
            header_raw=header,
        )

    def to_payload(self):
        if not 1 <= len(self.keygroups) <= MAX_KEYGROUPS:
            raise ValueError(
                f"a program needs 1..{MAX_KEYGROUPS} keygroups, has {len(self.keygroups)}"
            )
        header = bytearray(self.header_raw)

        # same rule as Keygroup.to_bytes: only a field that changed is re-encoded
        def put(offset, encoded, held, wanted):
            if held != wanted:
                header[offset : offset + len(encoded)] = bytes(encoded)

        def db_at(offset):
            return decode_db(header[offset], header[offset + 1])

        put(
            _H_NAME,
            encode_name(self.name),
            decode_name(header[_H_NAME : _H_NAME + 20]),
            self.name.rstrip(" \x00")[:NAME_LENGTH],
        )
        put(
            _H_KEY_TILT,
            encode_dw(self.key_tilt),
            to_signed(decode_dw(header[_H_KEY_TILT : _H_KEY_TILT + 4]), 16),
            self.key_tilt,
        )
        put(
            _H_POSITIONAL_XFADE,
            encode_db(1 if self.positional_xfade else 0),
            bool(db_at(_H_POSITIONAL_XFADE)),
            bool(self.positional_xfade),
        )
        put(
            _H_NUM_KEYGROUPS,
            encode_db(len(self.keygroups)),
            db_at(_H_NUM_KEYGROUPS),
            len(self.keygroups),
        )
        put(
            _H_MIDI_PROGRAM,
            encode_db(self.midi_program_number),
            db_at(_H_MIDI_PROGRAM),
            self.midi_program_number,
        )
        put(
            _H_ENABLE_MIDI_PROGRAM,
            encode_db(255 if self.enable_midi_program else 0),
            bool(db_at(_H_ENABLE_MIDI_PROGRAM)),
            bool(self.enable_midi_program),
        )
        return bytes(header) + b"".join(kg.to_bytes() for kg in self.keygroups)
