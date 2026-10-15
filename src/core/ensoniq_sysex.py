# Ensoniq EPS / EPS-16 PLUS / ASR-10 "External Command" SysEx: word codecs, framing, status codes, the commands a sample transfer
# needs, and the wavesample parameter block. Pure Python, no Qt / MIDI.
#
# Written from Ensoniq's own spec documents (the EPS External Command Specification MKB2 of 12 June 1989, and the ASR-10 and
# EPS-16 PLUS ones), NOT from any community code: `summitt/EnsoniqEPS16Plus` has no licence and nothing here is copied from it.
# dev_docs/ensoniq-support-plan.md has the research and dev_docs/ensoniq-spec-crosscheck.md says which statements were checked
# against a second source. **Nothing here has run against an Ensoniq unit.** Grades below: SPEC (stated in a document), GUESS (ours).
#
# Message convention matches core/akai_sysex.py and core/s950_sysex.py: a "message" is the list of data bytes BETWEEN the F0 and
# the F7 (what mido's `sysex` message carries), so a request is [0x0F, 0x03, 0x0n, command, ...body].
#
# Wire facts (SPEC unless marked):
#   head           F0 0F 03 0n   0F = Ensoniq, 03 = EPS / EPS-16+ / ASR-10, n = base MIDI channel minus 1 (ASR-10 spec; the ASR-X manual
#                                has a separate "SysEx Device ID" instead - which one the byte wants is open, so it is a parameter here)
#   12-bit word    00xxxxxx 00yyyyyy                       (two bytes, 6 bits each)
#   16-bit word    0000HHHH 00hhhhhh 00llllll              (three bytes: 4 + 6 + 6 bits)
#   20-bit offset  two 12-bit words = FOUR bytes, 6 bits each, high first: 0AAAA -> 00 0A 2A 2A, 00200 -> 00 00 08 00.
#                  The EPS and ASR-10 specs print 00 00 40 00 for the second one - a typo (40 isn't a legal 6-bit byte, and the
#                  EPS spec's own scale-factor examples split a value the way the arithmetic does); the EPS-16+ spec has 00 00 08 00
#   no checksum    reliability is the RESPONSE message and the 2 s command timer
#   edit context   every command's body starts with instrument (0-7), layer (0-7) and wavesample (1-127; 0 = "all" for PUT PARAMETER)
#                  as three 12-bit words
#   PUT with data  two messages: [command + context (+ range)], then a message that is ONLY the data words (no command byte)
#                  (SPEC by reading sections 2.3 and 8; unverified on a unit)

import dataclasses

MANUFACTURER_ENSONIQ = 0x0F
PRODUCT_EPS_ASR = 0x03  # EPS, EPS-16 PLUS and ASR-10 share it

# --- command codes (section 4) ------------------------------------------------
CMD_RESPONSE = 0x01
CMD_CANCEL = 0x02  # only valid in WAIT mode
CMD_GET_INSTRUMENT = 0x03
CMD_GET_LAYER = 0x04
CMD_GET_WAVESAMPLE_PARAMETERS = 0x05
CMD_GET_WAVESAMPLE_DATA = 0x06
CMD_GET_PITCH_TABLE = 0x07
CMD_GET_PARAMETER = 0x08
CMD_GET_WAVESAMPLE_OVERVIEW = 0x0A
CMD_PUT_INSTRUMENT = 0x0C
CMD_PUT_LAYER = 0x0D
CMD_PUT_WAVESAMPLE_PARAMETERS = 0x0E
CMD_PUT_WAVESAMPLE_DATA = 0x0F
CMD_PUT_PITCH_TABLE = 0x10
CMD_PUT_PARAMETER = 0x11
CMD_PUT_WAVESAMPLE_OVERVIEW = 0x13
CMD_CREATE_INSTRUMENT = 0x15
CMD_CREATE_LAYER = 0x16
CMD_CREATE_WAVESAMPLE = 0x19
CMD_TRUNCATE_WAVESAMPLE = 0x1E
# Never sent by a send or a receive (they delete data, or on the ASR-10 / EPS-16+ write the disk and overwrite a file of the
# same name without a warning). Named here so nothing has to invent a number; the engine must not use them.
CMD_DELETE_WAVESAMPLE = 0x1A
CMD_DELETE_INSTRUMENT = 0x1C
CMD_DELETE_LAYER = 0x17
CMD_SAVE_INSTRUMENT = 0x14  # ASR-10 / EPS-16+ only ("unused code" in the 1989 EPS spec)
NEVER_SEND = frozenset(
    {CMD_DELETE_WAVESAMPLE, CMD_DELETE_INSTRUMENT, CMD_DELETE_LAYER, CMD_SAVE_INSTRUMENT}
)

# --- status codes (section 5): the low byte of a RESPONSE ---------------------------
STATUS_ACK = 0x00
STATUS_WAIT = 0x01
STATUS_INSERT_SYSTEM_DISK = 0x02
STATUS_INVALID_PARAMETER_NUMBER = 0x03
STATUS_INVALID_PARAMETER_VALUE = 0x04
STATUS_INVALID_INSTRUMENT = 0x05  # "doesn't exist, i.e. is not loaded"
STATUS_INVALID_LAYER = 0x06
STATUS_LAYER_IN_USE = 0x07
STATUS_INVALID_WAVESAMPLE = 0x08
STATUS_WAVESAMPLE_IN_USE = 0x09
STATUS_INVALID_DATA_RANGE = 0x0A
STATUS_FILE_NOT_FOUND = 0x0B
STATUS_MEMORY_FULL = 0x0C
STATUS_INSTRUMENT_IN_USE = 0x0D
STATUS_NO_MORE_LAYERS = 0x0E
STATUS_NO_MORE_WAVESAMPLES = 0x0F
STATUS_WAVESAMPLE_IS_A_COPY = 0x11
STATUS_ZONE_TOO_BIG = 0x12
STATUS_SEQUENCER_MUST_BE_STOPPED = 0x13
STATUS_DISK_ACCESS_IN_PROGRESS = 0x14
STATUS_DISK_FULL = 0x15
STATUS_LOOP_TOO_LONG = 0x16
STATUS_NAK = 0x17
STATUS_NO_LAYER_EDIT = 0x18
STATUS_NO_MORE_PITCH_TABLES = 0x19
STATUS_CROSS_FADE_LENGTH_IS_ZERO = 0x1A
STATUS_CROSS_FADE_GREATER_THAN_50 = 0x1B
STATUS_LOOP_START_TOO_CLOSE = 0x1C
STATUS_LOOP_END_TOO_CLOSE = 0x1D
STATUS_QUIET_LAYER = 0x1E

STATUS_NAMES = {
    0x00: "ACK",
    0x01: "WAIT",
    0x02: "insert system disk",
    0x03: "invalid parameter number",
    0x04: "invalid parameter value",
    0x05: "invalid instrument",
    0x06: "invalid layer",
    0x07: "layer in use",
    0x08: "invalid wavesample",
    0x09: "wavesample in use",
    0x0A: "invalid wavedata range",
    0x0B: "file not found",
    0x0C: "memory full",
    0x0D: "instrument in use",
    0x0E: "no more layers",
    0x0F: "no more wavesamples",
    0x10: "reserved",
    0x11: "wavesample is a copy",
    0x12: "zone too big",
    0x13: "sequencer must be stopped",
    0x14: "disk access in progress",
    0x15: "disk full",
    0x16: "loop too long",
    0x17: "NAK",
    0x18: "no layer edit",
    0x19: "no more pitch tables",
    0x1A: "cross fade length is zero",
    0x1B: "cross fade length greater than 50%",
    0x1C: "loop start too close to sample start",
    0x1D: "loop end too close to sample end",
    0x1E: "quiet layer",
}

# One short line per status a transfer is likely to meet; the log gets the code and name. Anything else: status_text().
_STATUS_USER_TEXT = {
    STATUS_INSERT_SYSTEM_DISK: "Insert the system disk in the Ensoniq's drive",
    STATUS_MEMORY_FULL: "The Ensoniq is out of sample memory",
    STATUS_NO_MORE_LAYERS: "The Ensoniq has no free layers",
    STATUS_NO_MORE_WAVESAMPLES: "The Ensoniq has no free wavesamples",
    STATUS_SEQUENCER_MUST_BE_STOPPED: "Stop the Ensoniq's sequencer and try again",
    STATUS_DISK_ACCESS_IN_PROGRESS: "The Ensoniq is busy with its disk - try again in a moment",
    STATUS_NAK: "The Ensoniq rejected the last data block",
    STATUS_INVALID_INSTRUMENT: "That instrument isn't loaded on the Ensoniq",
}

WAVESAMPLE_MIN = 1
WAVESAMPLE_MAX = 127
INSTRUMENT_MAX = 7
LAYER_MAX = 7

COMMAND_TIMEOUT_S = 2.0  # section 3.1: the sender waits this long even for commands that get no response
WAIT_TIMEOUT_S = 30.0  # section 3.2: after a WAIT status

MAX_OFFSET = 0xFFFFF  # data offsets are 20 bits: one range reaches 1,048,575 words (about 23.8 s at 44.1 kHz)
MAX_PARAM_VALUE = 0xFFFFFF  # parameter values are 24 bits


def status_name(code):
    return STATUS_NAMES.get(code, f"unknown status 0x{code:02X}")


def status_text(code):
    """A short sentence for the user. The log should carry the code too."""
    return _STATUS_USER_TEXT.get(code) or f"The Ensoniq answered: {status_name(code)}"


# --- word codecs ----------------------------------------------------------------


def _check_7bit(data, what):
    for b in data:
        if not 0 <= b <= 0x7F:
            raise ValueError(f"{what}: byte 0x{b:02X} is not a 7-bit MIDI data byte")


def encode_word12(value):
    if not 0 <= value <= 0xFFF:
        raise ValueError(f"12-bit word out of range: {value}")
    return [(value >> 6) & 0x3F, value & 0x3F]


def decode_word12(hi, lo):
    return ((hi & 0x3F) << 6) | (lo & 0x3F)


def encode_word16(value):
    # a raw 16-bit pattern (0..0xFFFF); signed samples go through encode_sample
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"16-bit word out of range: {value}")
    return [(value >> 12) & 0x0F, (value >> 6) & 0x3F, value & 0x3F]


def decode_word16(b0, b1, b2):
    return ((b0 & 0x0F) << 12) | ((b1 & 0x3F) << 6) | (b2 & 0x3F)


def encode_sample(sample):
    """One signed 16-bit sample as a 16-bit word (SDS words are unsigned and offset; Ensoniq's are plain two's complement)."""
    if not -32768 <= sample <= 32767:
        raise ValueError(f"sample out of 16-bit range: {sample}")
    return encode_word16(sample & 0xFFFF)


def decode_sample(b0, b1, b2):
    value = decode_word16(b0, b1, b2)
    return value - 0x10000 if value >= 0x8000 else value


def encode_samples(samples):
    out = []
    for s in samples:
        out += encode_sample(s)
    return out


def decode_samples(data):
    """The inverse of encode_samples. Raises ValueError for a stray byte, a non-multiple of 3 or a byte that isn't 7-bit."""
    data = list(data)
    if len(data) % 3:
        raise ValueError(f"sample data is {len(data)} bytes, not a multiple of 3")
    _check_7bit(data, "sample data")
    return [decode_sample(data[i], data[i + 1], data[i + 2]) for i in range(0, len(data), 3)]


def encode_offset(value):
    """A 20-bit wavedata offset as FOUR bytes: two 12-bit words, high word first."""
    if not 0 <= value <= MAX_OFFSET:
        raise ValueError(f"offset out of 20-bit range: {value}")
    return encode_word12(value >> 12) + encode_word12(value & 0xFFF)


def decode_offset(b):
    return (decode_word12(b[0], b[1]) << 12) | decode_word12(b[2], b[3])


def encode_param_number(page, item):
    """A parameter number is ONE 12-bit word, page << 8 | item (page 0-15, item 0-255), not two separate bytes.
    `page` is the EPS spec's "SysEx high byte" (system 13, MIDI 12, instrument 10, layer 9, wavesample 8, ...). The ASR-10 spec prints
    the wire value in hex (system 34, wavesample 20): the same thing times 4. Items 0-63 fit the low byte; the tables only use those."""
    if not 0 <= page <= 0xF:
        raise ValueError(f"parameter page out of range: {page}")
    if not 0 <= item <= 0xFF:
        raise ValueError(f"parameter item out of range: {item}")
    return encode_word12((page << 8) | item)


def decode_param_number(hi, lo):
    word = decode_word12(hi, lo)
    return word >> 8, word & 0xFF


def encode_param_value(value):
    """A parameter value: right justified in a 24-bit word, sent as two 12-bit words (four bytes). Negative values are two's complement."""
    if not -0x800000 <= value <= MAX_PARAM_VALUE:
        raise ValueError(f"parameter value out of 24-bit range: {value}")
    value &= 0xFFFFFF
    return encode_word12(value >> 12) + encode_word12(value & 0xFFF)


def decode_param_value(b, signed=False):
    value = (decode_word12(b[0], b[1]) << 12) | decode_word12(b[2], b[3])
    if signed and value >= 0x800000:
        value -= 0x1000000
    return value


# the EPS spec's parameter pages (the "SysEx high byte"); item numbers are per model, so they live with whoever uses them
PAGE_SYSTEM = 13
PAGE_MIDI = 12
PAGE_INSTRUMENT = 10
PAGE_LAYER = 9
PAGE_WAVESAMPLE = 8

# Free memory is readable (SPEC, EPS and ASR-10): "Free System Blocks", read only, 0-10000. GUESS: one block is 256 words, the
# unit the instrument size uses.
SYSTEM_ITEM_FREE_BLOCKS = 0x00
BLOCK_WORDS = 256

# wavesample page items that matched on the EPS and the ASR-10 (item numbers in hex on the ASR-10, decimal on the EPS)
WAVESAMPLE_ITEM_LOOP_MODE = 0x00
WAVESAMPLE_ITEM_SAMPLE_RATE = 0x0D  # "receive only" on the ASR-10
WAVESAMPLE_ITEM_START = 0x15
WAVESAMPLE_ITEM_END = 0x16
WAVESAMPLE_ITEM_LOOP_START = 0x17
WAVESAMPLE_ITEM_LOOP_END = 0x18
WAVESAMPLE_ITEM_LOOP_POSITION = 0x19


# --- context, framing -----------------------------------------------------------------


def encode_context(instrument, layer, wavesample):
    if not 0 <= instrument <= INSTRUMENT_MAX:
        raise ValueError(f"instrument must be 0-{INSTRUMENT_MAX}: {instrument}")
    if not 0 <= layer <= LAYER_MAX:
        raise ValueError(f"layer must be 0-{LAYER_MAX}: {layer}")
    if not 0 <= wavesample <= WAVESAMPLE_MAX:
        raise ValueError(f"wavesample must be 0-{WAVESAMPLE_MAX}: {wavesample}")
    return encode_word12(instrument) + encode_word12(layer) + encode_word12(wavesample)


def decode_context(b):
    return decode_word12(b[0], b[1]), decode_word12(b[2], b[3]), decode_word12(b[4], b[5])


CONTEXT_SIZE = 6


def head(channel=0):
    """The three bytes after F0: Ensoniq, product, base channel minus 1. `channel` is the wire value 0-15."""
    if not 0 <= channel <= 15:
        raise ValueError(f"channel must be 0-15: {channel}")
    return [MANUFACTURER_ENSONIQ, PRODUCT_EPS_ASR, channel]


def build_message(command, body=(), channel=0):
    body = list(body)
    _check_7bit(body, "message body")
    return head(channel) + [command] + body


def build_data_message(samples, channel=0):
    """The SECOND message of a PUT WAVESAMPLE DATA: the sample words alone, framed but with no command byte."""
    return head(channel) + encode_samples(samples)


@dataclasses.dataclass
class EnsoniqMessage:
    channel: int
    command: int  # None for a data-only message
    body: list


def is_ensoniq(data):
    return len(data) >= 3 and data[0] == MANUFACTURER_ENSONIQ and data[1] == PRODUCT_EPS_ASR


def parse_message(data):
    """Split a received message (F0/F7 already removed) into channel, command and body. A data-only second message has no command
    byte, so the caller that is waiting for one uses `parse_data_message` on the raw bytes instead."""
    data = list(data)
    if not is_ensoniq(data):
        raise ValueError(f"not an Ensoniq EPS/ASR message: {bytes(data[:4]).hex(' ')}")
    if len(data) < 4:
        raise ValueError("message has no command byte")
    _check_7bit(data, "message")
    return EnsoniqMessage(channel=data[2] & 0x0F, command=data[3], body=data[4:])


def parse_data_message(data):
    """The inverse of build_data_message: the samples of a data-only message."""
    data = list(data)
    if not is_ensoniq(data) or len(data) < 3:
        raise ValueError(f"not an Ensoniq EPS/ASR message: {bytes(data[:4]).hex(' ')}")
    return decode_samples(data[3:])


# --- requests and responses -----------------------------------------------------------


def build_response(status=STATUS_ACK, channel=0):
    return build_message(CMD_RESPONSE, encode_word12(status), channel)


def parse_response(message):
    """The status code of a RESPONSE message (an EnsoniqMessage from parse_message)."""
    if message.command != CMD_RESPONSE:
        raise ValueError(f"not a RESPONSE: command 0x{message.command:02X}")
    if len(message.body) < 2:
        raise ValueError("RESPONSE has no status word")
    return decode_word12(message.body[0], message.body[1])


def build_cancel(channel=0):
    return build_message(CMD_CANCEL, (), channel)


def _context_command(command, instrument, layer, wavesample, channel, extra=()):
    return build_message(command, encode_context(instrument, layer, wavesample) + list(extra), channel)


def build_get_instrument(instrument, channel=0):
    return _context_command(CMD_GET_INSTRUMENT, instrument, 0, 1, channel)


def build_get_layer(instrument, layer, channel=0):
    return _context_command(CMD_GET_LAYER, instrument, layer, 1, channel)


def build_get_wavesample_parameters(instrument, layer, wavesample, channel=0):
    return _context_command(CMD_GET_WAVESAMPLE_PARAMETERS, instrument, layer, wavesample, channel)


def _range(start, end):
    if start > end:
        raise ValueError(f"data range start {start} is after its end {end}")
    return encode_offset(start) + encode_offset(end)


def build_get_wavesample_data(instrument, layer, wavesample, start, end, channel=0):
    return _context_command(CMD_GET_WAVESAMPLE_DATA, instrument, layer, wavesample, channel, _range(start, end))


def build_get_parameter(instrument, layer, wavesample, page, item, channel=0):
    return _context_command(
        CMD_GET_PARAMETER, instrument, layer, wavesample, channel, encode_param_number(page, item)
    )


def build_put_parameter(instrument, layer, wavesample, page, item, value, channel=0):
    return _context_command(
        CMD_PUT_PARAMETER,
        instrument,
        layer,
        wavesample,
        channel,
        encode_param_number(page, item) + encode_param_value(value),
    )


def build_create_instrument(instrument, channel=0):
    return _context_command(CMD_CREATE_INSTRUMENT, instrument, 0, 1, channel)


def build_create_layer(instrument, layer, channel=0):
    return _context_command(CMD_CREATE_LAYER, instrument, layer, 1, channel)


def build_create_wavesample(instrument, layer, wavesample, channel=0):
    return _context_command(CMD_CREATE_WAVESAMPLE, instrument, layer, wavesample, channel)


def build_truncate_wavesample(instrument, layer, wavesample, channel=0):
    return _context_command(CMD_TRUNCATE_WAVESAMPLE, instrument, layer, wavesample, channel)


def build_put_wavesample_parameters(instrument, layer, wavesample, channel=0):
    """The FIRST message of a PUT WAVESAMPLE PARAMETERS; the block follows as `build_block_message`."""
    return _context_command(CMD_PUT_WAVESAMPLE_PARAMETERS, instrument, layer, wavesample, channel)


def build_put_wavesample_data(instrument, layer, wavesample, start, end, channel=0):
    """The FIRST message of a PUT WAVESAMPLE DATA; the words follow as `build_data_message`."""
    return _context_command(CMD_PUT_WAVESAMPLE_DATA, instrument, layer, wavesample, channel, _range(start, end))


def parse_context_and_range(body):
    """(instrument, layer, wavesample, start, end) of a PUT/GET WAVESAMPLE DATA body."""
    if len(body) < CONTEXT_SIZE + 8:
        raise ValueError(f"data command body is {len(body)} bytes, expected {CONTEXT_SIZE + 8}")
    instrument, layer, wavesample = decode_context(body)
    return (
        instrument,
        layer,
        wavesample,
        decode_offset(body[CONTEXT_SIZE : CONTEXT_SIZE + 4]),
        decode_offset(body[CONTEXT_SIZE + 4 : CONTEXT_SIZE + 8]),
    )


def chunk_ranges(total_words, chunk_words):
    """[(start, stop)] covering words 0..total_words-1 in pieces of at most chunk_words, `stop` one past the last word. Whether the
    unit's own end offset is the last word or one past it is not stated by the spec, so the engine decides that (and logs it)."""
    if total_words < 0 or chunk_words < 1:
        raise ValueError("chunk_ranges needs total_words >= 0 and chunk_words >= 1")
    return [(s, min(s + chunk_words, total_words)) for s in range(0, total_words, chunk_words)]


# --- parameter blocks (section 7) -----------------------------------------------------------
# A block is N words sent in the 16-bit format (three bytes each) with the VALUE IN THE HIGH BYTE of the word (low byte unused).

INSTRUMENT_BLOCK_WORDS = 323
LAYER_BLOCK_WORDS = 107
PITCH_TABLE_BLOCK_WORDS = 107
WAVESAMPLE_BLOCK_WORDS = 139

NAME_WORDS = 12


def encode_block(values):
    """Values 0-255 (the hi byte of each word) -> bytes of the 16-bit format."""
    out = []
    for v in values:
        if not 0 <= v <= 0xFF:
            raise ValueError(f"block value out of range: {v}")
        out += encode_word16(v << 8)
    return out


def decode_block(data, expected_words=None):
    data = list(data)
    if len(data) % 3:
        raise ValueError(f"block is {len(data)} bytes, not a multiple of 3")
    _check_7bit(data, "block")
    values = [decode_word16(data[i], data[i + 1], data[i + 2]) >> 8 for i in range(0, len(data), 3)]
    if expected_words is not None and len(values) != expected_words:
        raise ValueError(f"block is {len(values)} words, expected {expected_words}")
    return values


def build_block_message(values, channel=0):
    """The SECOND message of a PUT of a parameter block: the words alone, no command byte."""
    return head(channel) + encode_block(values)


def parse_block_message(data, expected_words=None):
    data = list(data)
    if not is_ensoniq(data):
        raise ValueError(f"not an Ensoniq EPS/ASR message: {bytes(data[:4]).hex(' ')}")
    return decode_block(data[3:], expected_words)


def decode_name(values):
    """12 ASCII characters, one per word. Trailing spaces and NULs are dropped; a byte above 0x7F shows as '?'."""
    chars = [chr(v) if 0x20 <= v < 0x7F else ("" if v == 0 else "?") for v in values[:NAME_WORDS]]
    return "".join(chars).rstrip(" ")


def encode_name(name):
    """Pads with spaces to 12 (GUESS: the spec says 12 ASCII bytes and not whether a short name is space or NUL padded).
    Characters outside printable ASCII become '?'."""
    name = "".join(c if 0x20 <= ord(c) < 0x7F else "?" for c in name)[:NAME_WORDS]
    return [ord(c) for c in name.ljust(NAME_WORDS)]


# wavesample block word offsets (section 7.3)
WS_NAME = 0
WS_COPY_NUMBER = 12
WS_COPY_LAYER = 13
WS_ROOT_KEY = 80
WS_FINE_TUNE = 86
WS_LOOP_MODE = 114
WS_START = 115  # 4 words: a left-justified 32-bit field in the hi bytes; shift right 9 for the WORD offset
WS_END = 119
WS_LOOP_START = 123
WS_LOOP_END = 127  # shift right 5 instead: the low 4 bits of the result are a loop-end fraction
WS_SAMPLE_RATE = 131
WS_KEY_LO = 132
WS_KEY_HI = 133

LOOP_FORWARD_NO_LOOP = 0
LOOP_BACKWARD_NO_LOOP = 1
LOOP_FORWARD = 2
LOOP_BIDIRECTIONAL = 3
LOOP_AND_RELEASE = 4


def _field32(values, at):
    return (values[at] << 24) | (values[at + 1] << 16) | (values[at + 2] << 8) | values[at + 3]


def _set_field32(values, at, field):
    values[at : at + 4] = [(field >> 24) & 0xFF, (field >> 16) & 0xFF, (field >> 8) & 0xFF, field & 0xFF]


def word_offset_from_field(field):
    return field >> 9


def field_from_word_offset(offset):
    if not 0 <= offset < (1 << 23):
        raise ValueError(f"word offset out of range for a block field: {offset}")
    return offset << 9


# sample period = code x 1.6 microseconds (SPEC, section 7.3 word 131). Consistent with the EPS's own rates (code 20 = 31.25 kHz,
# code 12 = 52.08 kHz), which is a GUESS from memory. 44.1 kHz is NOT representable: the nearest codes are 14 (44.64 kHz) and 15
# (41.67 kHz), so a send has to resample or accept a tuning error (open question 5).
SAMPLE_PERIOD_US_PER_CODE = 1.6
RATE_CODE_MIN = 1
RATE_CODE_MAX = 127


def rate_code_to_hz(code):
    if not RATE_CODE_MIN <= code <= RATE_CODE_MAX:
        raise ValueError(f"sample rate code out of range: {code}")
    return 1_000_000.0 / (code * SAMPLE_PERIOD_US_PER_CODE)


def hz_to_rate_code(hz):
    """The code whose rate is nearest to `hz` (nearest in Hz, clamped to 1-127)."""
    if hz <= 0:
        raise ValueError("sample rate must be positive")
    best = min(range(RATE_CODE_MIN, RATE_CODE_MAX + 1), key=lambda c: abs(rate_code_to_hz(c) - hz))
    return best


@dataclasses.dataclass
class WavesampleParams:
    """The wavesample block as a list of 139 values (`raw`, hi bytes) plus accessors for the fields a sample transfer cares about.
    Everything else stays in `raw` and is written back untouched (like core/s950_program.py)."""

    raw: list

    @classmethod
    def from_block(cls, values):
        values = list(values)
        if len(values) != WAVESAMPLE_BLOCK_WORDS:
            raise ValueError(f"a wavesample block is {WAVESAMPLE_BLOCK_WORDS} words, got {len(values)}")
        return cls(raw=values)

    @classmethod
    def blank(cls):
        return cls(raw=[0] * WAVESAMPLE_BLOCK_WORDS)

    def to_block(self):
        return list(self.raw)

    @property
    def name(self):
        return decode_name(self.raw[WS_NAME : WS_NAME + NAME_WORDS])

    @name.setter
    def name(self, value):
        self.raw[WS_NAME : WS_NAME + NAME_WORDS] = encode_name(value)

    @property
    def root_key(self):
        return self.raw[WS_ROOT_KEY]

    @root_key.setter
    def root_key(self, value):
        self.raw[WS_ROOT_KEY] = value

    @property
    def loop_mode(self):
        return self.raw[WS_LOOP_MODE]

    @loop_mode.setter
    def loop_mode(self, value):
        self.raw[WS_LOOP_MODE] = value

    @property
    def sample_rate_code(self):
        return self.raw[WS_SAMPLE_RATE]

    @sample_rate_code.setter
    def sample_rate_code(self, value):
        self.raw[WS_SAMPLE_RATE] = value

    @property
    def key_range(self):
        return self.raw[WS_KEY_LO], self.raw[WS_KEY_HI]

    @key_range.setter
    def key_range(self, value):
        self.raw[WS_KEY_LO], self.raw[WS_KEY_HI] = value

    @property
    def start(self):
        return word_offset_from_field(_field32(self.raw, WS_START))

    @start.setter
    def start(self, offset):
        _set_field32(self.raw, WS_START, field_from_word_offset(offset))

    @property
    def end(self):
        return word_offset_from_field(_field32(self.raw, WS_END))

    @end.setter
    def end(self, offset):
        _set_field32(self.raw, WS_END, field_from_word_offset(offset))

    @property
    def loop_start(self):
        return word_offset_from_field(_field32(self.raw, WS_LOOP_START))

    @loop_start.setter
    def loop_start(self, offset):
        _set_field32(self.raw, WS_LOOP_START, field_from_word_offset(offset))

    @property
    def loop_end(self):
        """(word offset, 4-bit fraction) - this field is shifted by 5 where the others shift by 9."""
        field = _field32(self.raw, WS_LOOP_END) >> 5
        return field >> 4, field & 0xF

    def set_loop_end(self, offset, fraction=0):
        if not 0 <= fraction <= 0xF:
            raise ValueError(f"loop end fraction out of range: {fraction}")
        _set_field32(self.raw, WS_LOOP_END, ((offset << 4) | fraction) << 5)

    @property
    def length(self):
        """Words between start and end as the block describes them. Whether `end` is inclusive is not stated by the spec."""
        return self.end - self.start


def instrument_name(values):
    return decode_name(values[:NAME_WORDS])


def instrument_size_blocks(values):
    """Total instrument size in 256-word blocks (instrument block word 15)."""
    return values[15]
