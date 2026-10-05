# Akai S900/S950 MIDI System Exclusive wire format: codecs, framing, catalog,
# sample parameters (SPRM) and the sample dump. Pure Python, no Qt / MIDI.
#
# Ported from s950tools (https://github.com/diemonster/s950tools, MIT,
# Copyright (c) 2026 Brandon Ivers) - internal/sysex/codec.go,
# internal/protocol/messages.go and internal/sample/convert.go. See
# THIRD_PARTY_NOTICES.md. s950tools in turn cites "Akai S900 MIDI System
# Exclusive Data Format V2.0" (which applies to the S950); we have not seen
# that document ourselves.
#
# Message convention matches core/akai_sysex.py: a "message" is the list of
# data bytes BETWEEN the F0 and F7 (what mido's `sysex` message carries), so
# an Akai request is [0x47, chan, func, 0x40, num, 0x00]. The S950 is a
# different protocol from the S1000/S2000/S3000 in core/akai_sysex.py:
# device byte 0x40 (not 0x48), different function codes, every 8-bit value
# sent as TWO MIDI bytes, 10-char plain-ASCII names, XOR checksums.
#
# Nothing here has been run against hardware by THIS project. Where
# s950tools says a behaviour was verified on a real S950 it is noted below;
# the SPRM field offsets in particular are only as good as s950tools' copy
# of them (its real-device captures cover the PRGM header, see
# core/s950_program.py).

import dataclasses

MANUFACTURER_AKAI = 0x47
DEVICE_ID_S950 = 0x40  # the S900/S950 identifier byte (decimal 64)
UNIVERSAL_NRT = 0x7E  # "system exclusive common" ID, used by the sample dump

# AKAI function codes (data byte 3 of an Akai-exclusive message).
#
# There is NO delete-sample / delete-program opcode in the documented
# surface (s950tools has a TODO saying the same) - deleting is front-panel
# only unless an undocumented one turns up. Don't invent one.
FUNC_RDRS = 0  # request drum settings
FUNC_ROVS = 1  # request overall settings
FUNC_RPRGM = 2  # request program + keygroups
FUNC_RCAT = 3  # request program/sample name catalog
FUNC_RSPRM = 4  # request sample parameters
FUNC_SECRE = 5  # system-exclusive-common reception enable
FUNC_SECRD = 6  # system-exclusive-common reception disable
FUNC_DRS = 7  # drum settings (data)
FUNC_OVS = 8  # overall settings (data)
FUNC_PRGM = 9  # program + keygroups (data)
FUNC_SPRM = 10  # sample parameters (data)
FUNC_CAT = 11  # name catalog (data)

# sub-IDs of the F0 7E ... "system exclusive common" family
CODE_RSD = 0x00  # request sample dump
CODE_SD = 0x01  # sample dump
CODE_ASD = 0x7D  # abort sample dump (the standard SDS "CANCEL")
CODE_NAKS = 0x7E  # not-acknowledge / retransmit
CODE_ACKS = 0x7F  # acknowledge

# SPRM replay modes (ASCII letters on the wire) and the reverse flag
REPLAY_ONE_SHOT = ord("O")
REPLAY_LOOP = ord("L")
REPLAY_ALTERNATING = ord("A")
PLAY_FORWARD = ord("N")
PLAY_REVERSED = ord("R")

SPRM_PAYLOAD_SIZE = 120

# sample dump geometry / limits
WORDS_PER_BLOCK = 60
BLOCK_SIZE = 1 + 2 * WORDS_PER_BLOCK + 1  # block#, 60 two-byte words, checksum
DUMP_HEADER_SIZE = 18  # F0 7E 01 ... without the F0 (19 on the wire)
MIN_TOTAL_WORDS = 200
MAX_TOTAL_WORDS = 475020
MIN_PERIOD_NS = 15259  # ~65.5 kHz
MAX_PERIOD_NS = 500000  # 2 kHz
SILENCE_WORD = 0x800  # offset-binary zero


# --- codecs ---------------------------------------------------------------
# Every value travels as 7-bit MIDI bytes. Multi-byte values are
# little-endian. "DB" = one 8-bit value as two bytes (low 7 bits, then bit 7).


def encode_db(value):
    value &= 0xFF
    return [value & 0x7F, (value >> 7) & 0x01]


def decode_db(lo, hi):
    return (lo & 0x7F) | ((hi & 0x01) << 7)


def encode_dw(value):
    # 16 bit, two DB groups = 4 wire bytes
    value &= 0xFFFF
    return encode_db(value) + encode_db(value >> 8)


def decode_dw(b):
    return decode_db(b[0], b[1]) | (decode_db(b[2], b[3]) << 8)


def encode_dd(value):
    # 32 bit, four DB groups = 8 wire bytes
    value &= 0xFFFFFFFF
    out = []
    for i in range(4):
        out += encode_db(value >> (8 * i))
    return out


def decode_dd(b):
    return sum(decode_db(b[2 * i], b[2 * i + 1]) << (8 * i) for i in range(4))


def encode_tb(value):
    # 21 bit, three 7-bit groups (used by the sample dump header)
    value &= 0x1FFFFF
    return [value & 0x7F, (value >> 7) & 0x7F, (value >> 14) & 0x7F]


def decode_tb(b):
    return (b[0] & 0x7F) | ((b[1] & 0x7F) << 7) | ((b[2] & 0x7F) << 14)


def encode_sw(word):
    # 12 bit sample word as two bytes: 0 d11..d5, 0 d4..d0 0 0
    word &= 0x0FFF
    return [(word >> 5) & 0x7F, (word << 2) & 0x7F]


def decode_sw(b0, b1):
    return ((b0 & 0x7F) << 5) | ((b1 & 0x7F) >> 2)


def to_signed(value, bits):
    # reinterpret an unsigned wire value as two's complement
    sign = 1 << (bits - 1)
    return value - (sign << 1) if value & sign else value


NAME_LENGTH = 10


def encode_name(name):
    # 10 chars, DB-encoded (20 bytes) - the form used INSIDE program/sample
    # blocks. Space padded, truncated, non-ASCII becomes '?'.
    out = []
    for i in range(NAME_LENGTH):
        c = ord(name[i]) if i < len(name) else 0x20
        out += encode_db(c if c <= 0x7F else ord("?"))
    return out


def decode_name(b):
    chars = [decode_db(b[2 * i], b[2 * i + 1]) for i in range(NAME_LENGTH)]
    return "".join(chr(c) for c in chars).rstrip(" \x00")


def xor_checksum(data):
    # XOR of every byte, masked to 7 bits. Two uses on the wire:
    #   - sample dump block: the 120 data bytes only (NOT the block number)
    #   - Akai-exclusive message: every byte after the 6-byte header
    c = 0
    for b in data:
        c ^= b
    return c & 0x7F


# --- framing --------------------------------------------------------------

_AKAI_HEADER_SIZE = 6  # 47 chan func 40 num 00


@dataclasses.dataclass
class AkaiMessage:
    channel: int
    function: int
    num: int  # sample/program slot, 0 when the message doesn't address one
    payload: bytes  # everything between the header and the checksum


def build_akai_request(function, num=0, channel=0):
    # request: F0 47 cc ff 40 nn 00 F7 (no payload, no checksum)
    return [
        MANUFACTURER_AKAI,
        channel & 0x0F,
        function & 0x7F,
        DEVICE_ID_S950,
        num & 0x7F,
        0x00,
    ]


def build_akai_data(function, num, payload, channel=0):
    # data message: header + payload + XOR checksum of the payload
    payload = list(payload)
    return build_akai_request(function, num, channel) + payload + [xor_checksum(payload)]


def parse_akai(data):
    # validate and split an Akai-exclusive message (bytes between F0 and F7)
    data = bytes(data)
    if len(data) < _AKAI_HEADER_SIZE + 1:
        raise ValueError(f"S950 message too short: {len(data)} bytes")
    if data[0] != MANUFACTURER_AKAI:
        raise ValueError(f"not an Akai message: manufacturer 0x{data[0]:02X}")
    if data[3] != DEVICE_ID_S950:
        raise ValueError(f"not an S900/S950 message: device byte 0x{data[3]:02X}")
    payload = data[_AKAI_HEADER_SIZE:-1]
    want = xor_checksum(payload)
    if data[-1] != want:
        raise ValueError(f"checksum mismatch: got 0x{data[-1]:02X}, want 0x{want:02X}")
    return AkaiMessage(
        channel=data[1] & 0x0F,
        function=data[2] & 0x7F,
        num=data[4] & 0x7F,
        payload=payload,
    )


def build_request_sample_dump(num):
    # F0 7E 00 nn 00 F7 - note: NOT the standard SDS request (F0 7E cc 03 ..)
    return [UNIVERSAL_NRT, CODE_RSD, num & 0x7F, 0x00]


def build_handshake(code):
    # F0 7E <code> F7. Hardware-verified by s950tools: the 4-byte form is the
    # real ACK/NAK/abort on the wire; the standard 6-byte SDS form (with a
    # channel and packet number) is silently ignored by the S950.
    if code not in (CODE_ACKS, CODE_NAKS, CODE_ASD):
        raise ValueError(f"not a handshake code: 0x{code:02X}")
    return [UNIVERSAL_NRT, code]


def handshake_code(data):
    # the code if `data` is a 4-byte-form handshake, else None
    if len(data) == 2 and data[0] == UNIVERSAL_NRT and data[1] in (
        CODE_ACKS,
        CODE_NAKS,
        CODE_ASD,
    ):
        return data[1]
    return None


# --- catalog (RCAT -> CAT) ------------------------------------------------

_CATALOG_ENTRY_SIZE = 12


@dataclasses.dataclass
class CatalogEntry:
    kind: str  # "P" program or "S" sample
    num: int
    name: str


def parse_catalog(payload):
    # 12 bytes per entry: type char, slot, 10 PLAIN ASCII name chars (the
    # catalog names are not DB-encoded, unlike names inside blocks)
    payload = bytes(payload)
    if len(payload) % _CATALOG_ENTRY_SIZE:
        raise ValueError(
            f"CAT payload length {len(payload)} is not a multiple of {_CATALOG_ENTRY_SIZE}"
        )
    entries = []
    for i in range(0, len(payload), _CATALOG_ENTRY_SIZE):
        name = payload[i + 2 : i + _CATALOG_ENTRY_SIZE].decode("ascii", "replace")
        entries.append(
            CatalogEntry(
                kind=chr(payload[i]), num=payload[i + 1], name=name.rstrip(" \x00")
            )
        )
    return entries


# --- sample parameters (SPRM) ----------------------------------------------
# Offsets are WIRE offsets in the 120-byte payload (DB = 2 bytes, DW = 4,
# DD = 8). Unmodelled bytes are kept in `raw` and written back untouched:
# several "undefined" S900 fields are used by the S950.

_SPRM_NAME = 0  # 20 bytes
_SPRM_LENGTH = 32  # DD
_SPRM_RATE = 40  # DW, Hz
_SPRM_PITCH = 44  # DW, 1/16 semitone, C3 = 960
_SPRM_LOUD_OFFSET = 48  # DW signed
_SPRM_REPLAY_MODE = 52  # DB
_SPRM_END = 56  # DD
_SPRM_START = 64  # DD
_SPRM_LOOP = 72  # DD
_SPRM_VEL_XFADE = 84  # DB
_SPRM_REVERSED = 86  # DB


@dataclasses.dataclass
class SampleParams:
    name: str = ""
    total_words: int = 0  # SLNGTH, 12-bit words
    sample_rate_hz: int = 0  # SMRATE
    nominal_pitch: int = 960  # SNOMP, 1/16 semitone units (C3 = 960)
    loud_offset: int = 0  # SDFLDO, signed
    replay_mode: int = REPLAY_ONE_SHOT  # SRPLMD, an ord() of O/L/A
    end: int = 0  # SEND
    start: int = 0  # SSTART
    loop_length: int = 0  # SLOOP
    vel_xfade: int = 0  # VC
    reversed: int = PLAY_FORWARD  # NOREV, ord() of N/R
    raw: bytes = dataclasses.field(default=bytes(SPRM_PAYLOAD_SIZE), compare=False)

    @classmethod
    def from_payload(cls, payload):
        # Accepts MORE than 120 bytes (keeps the tail in raw) but not fewer.
        # The S1000 work showed spec'd block lengths can't be trusted, so
        # the real length is in the error text to make it into a bug report.
        raw = bytes(payload)
        if len(raw) < SPRM_PAYLOAD_SIZE:
            raise ValueError(
                f"SPRM payload is {len(raw)} bytes, need at least {SPRM_PAYLOAD_SIZE}"
            )
        return cls(
            name=decode_name(raw[_SPRM_NAME : _SPRM_NAME + 20]),
            total_words=decode_dd(raw[_SPRM_LENGTH : _SPRM_LENGTH + 8]),
            sample_rate_hz=decode_dw(raw[_SPRM_RATE : _SPRM_RATE + 4]),
            nominal_pitch=decode_dw(raw[_SPRM_PITCH : _SPRM_PITCH + 4]),
            loud_offset=to_signed(
                decode_dw(raw[_SPRM_LOUD_OFFSET : _SPRM_LOUD_OFFSET + 4]), 16
            ),
            replay_mode=decode_db(raw[_SPRM_REPLAY_MODE], raw[_SPRM_REPLAY_MODE + 1]),
            end=decode_dd(raw[_SPRM_END : _SPRM_END + 8]),
            start=decode_dd(raw[_SPRM_START : _SPRM_START + 8]),
            loop_length=decode_dd(raw[_SPRM_LOOP : _SPRM_LOOP + 8]),
            vel_xfade=decode_db(raw[_SPRM_VEL_XFADE], raw[_SPRM_VEL_XFADE + 1]),
            reversed=decode_db(raw[_SPRM_REVERSED], raw[_SPRM_REVERSED + 1]),
            raw=raw,
        )

    def to_payload(self):
        out = bytearray(self.raw)

        def put(offset, encoded):
            out[offset : offset + len(encoded)] = bytes(encoded)

        put(_SPRM_NAME, encode_name(self.name))
        put(_SPRM_LENGTH, encode_dd(self.total_words))
        put(_SPRM_RATE, encode_dw(self.sample_rate_hz))
        put(_SPRM_PITCH, encode_dw(self.nominal_pitch))
        put(_SPRM_LOUD_OFFSET, encode_dw(self.loud_offset))
        put(_SPRM_REPLAY_MODE, encode_db(self.replay_mode))
        put(_SPRM_END, encode_dd(self.end))
        put(_SPRM_START, encode_dd(self.start))
        put(_SPRM_LOOP, encode_dd(self.loop_length))
        put(_SPRM_VEL_XFADE, encode_db(self.vel_xfade))
        put(_SPRM_REVERSED, encode_db(self.reversed))
        return bytes(out)


# --- sample dump ------------------------------------------------------------
# Not standard SDS: the header has no channel byte, and the header plus EVERY
# data block go out inside ONE SysEx with a single F7 at the very end. When
# the S950 SENDS a dump it pauses between blocks waiting for an ACK, but
# since mido only surfaces complete F0..F7 messages there is nothing to
# react to per block - s950tools keeps a steady stream of 4-byte ACKs
# flowing from just after the RSD until the dump lands (its "ACK pump").
# That's transfer-layer work, not done here.


@dataclasses.dataclass
class SampleDumpHeader:
    num: int  # sample slot (the S950 uses 0..99)
    bits_per_word: int = 12  # S950 sends 12, accepts 8..16
    period_ns: int = 0  # sample period, MIN_PERIOD_NS..MAX_PERIOD_NS
    total_words: int = 0  # MIN_TOTAL_WORDS..MAX_TOTAL_WORDS
    loop_start: int = 0  # words from sample start
    loop_end: int = 0  # (the doc says: actually used as the playback end)
    mode: int = 0  # 0 = looping, 1 = alternating

    def to_bytes(self):
        return (
            [UNIVERSAL_NRT, CODE_SD, self.num & 0x7F, (self.num >> 7) & 0x7F]
            + [self.bits_per_word & 0x7F]
            + encode_tb(self.period_ns)
            + encode_tb(self.total_words)
            + encode_tb(self.loop_start)
            + encode_tb(self.loop_end)
            + [self.mode & 0x7F]
        )

    @classmethod
    def from_bytes(cls, data):
        # `data` starts at the 0x7E (no F0) and may carry trailing blocks
        if len(data) < DUMP_HEADER_SIZE:
            raise ValueError(f"dump header too short: {len(data)} bytes")
        if data[0] != UNIVERSAL_NRT or data[1] != CODE_SD:
            raise ValueError("not a sample dump header (expected 7E 01)")
        return cls(
            num=(data[2] & 0x7F) | ((data[3] & 0x7F) << 7),
            bits_per_word=data[4] & 0x7F,
            period_ns=decode_tb(data[5:8]),
            total_words=decode_tb(data[8:11]),
            loop_start=decode_tb(data[11:14]),
            loop_end=decode_tb(data[14:17]),
            mode=data[17] & 0x7F,
        )


def num_blocks(total_words):
    return -(-total_words // WORDS_PER_BLOCK)


def encode_block(block_num, words):
    # 122 bytes: block number (only the low 7 bits, wraps at 128), 60 sample
    # words, checksum of the 120 data bytes
    if len(words) != WORDS_PER_BLOCK:
        raise ValueError(f"a block holds {WORDS_PER_BLOCK} words, got {len(words)}")
    data = []
    for w in words:
        data += encode_sw(w)
    return [block_num & 0x7F] + data + [xor_checksum(data)]


def decode_block(block):
    # -> (block number low 7 bits, words). Also accepts the SHORT final block
    # a real S950 sometimes sends (1 + 2N + 1 bytes, N < 60, see
    # parse_sample_dump).
    block = bytes(block)
    if len(block) < 4 or len(block) % 2:
        raise ValueError(f"block of {len(block)} bytes is not a valid 1+2N+1 layout")
    data = block[1:-1]
    if block[-1] != xor_checksum(data):
        raise ValueError(
            f"block {block[0]} checksum mismatch: got 0x{block[-1]:02X}, "
            f"want 0x{xor_checksum(data):02X}"
        )
    return block[0] & 0x7F, [decode_sw(data[i], data[i + 1]) for i in range(0, len(data), 2)]


def build_sample_dump(header, words):
    # the whole dump as one message body: header + every block. The last
    # block is padded with offset-binary silence. `header.total_words` is
    # what the receiver trusts, so it must match len(words).
    if header.total_words != len(words):
        raise ValueError(
            f"header says {header.total_words} words but {len(words)} were given"
        )
    out = header.to_bytes()
    for block in range(num_blocks(len(words))):
        chunk = list(words[block * WORDS_PER_BLOCK : (block + 1) * WORDS_PER_BLOCK])
        chunk += [SILENCE_WORD] * (WORDS_PER_BLOCK - len(chunk))
        out += encode_block(block, chunk)
    return out


def parse_sample_dump(data, expected_slot=None):
    # -> (SampleDumpHeader, words trimmed to header.total_words).
    #
    # Hardware diverges from the spec on the LAST block (s950tools, measured):
    # the spec says pad to 60 words but the S950 sometimes sends fewer - a
    # 164060-word sample arrived 6 bytes short of the formula. So full blocks
    # are decoded from the front and whatever is left is read as one short
    # block; the true length always comes from the header, never the body.
    data = bytes(data)
    header = SampleDumpHeader.from_bytes(data)
    if expected_slot is not None and header.num != expected_slot:
        raise ValueError(f"dump is for slot {header.num}, expected {expected_slot}")
    body = data[DUMP_HEADER_SIZE:]
    words = []
    cursor = 0
    while cursor + BLOCK_SIZE <= len(body) and len(words) < header.total_words:
        words += decode_block(body[cursor : cursor + BLOCK_SIZE])[1]
        cursor += BLOCK_SIZE
    remainder = len(body) - cursor
    if remainder > 2 and len(words) < header.total_words:
        words += decode_block(body[cursor:])[1]
    if len(words) < header.total_words:
        raise ValueError(
            f"dump short on words: got {len(words)}, header says {header.total_words}"
        )
    return header, words[: header.total_words]


# --- audio conversion --------------------------------------------------------


def pcm16_to_word(sample):
    # 16 -> 12 bit: arithmetic shift right 4, then offset-binary (silence 0x800)
    return max(0, min(4095, (sample >> 4) + 2048))


def word_to_pcm16(word):
    return ((word & 0x0FFF) - 2048) << 4


def hz_to_period_ns(hz):
    # the header stores the sample PERIOD in nanoseconds
    if hz <= 0:
        raise ValueError("sample rate must be positive")
    period = round(1e9 / hz)
    if not MIN_PERIOD_NS <= period <= MAX_PERIOD_NS:
        raise ValueError(f"{hz} Hz is outside the S950's range (~2 kHz..65.5 kHz)")
    return period


def period_ns_to_hz(period_ns):
    return round(1e9 / period_ns) if period_ns else 0
