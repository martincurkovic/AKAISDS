"""Yamaha A4000/A5000 SysEx codec - pure functions, no Qt, no MIDI I/O.

Everything here was checked against a REAL A4000 on 2026-10-06 (see the roadmap file
`dev_docs/a4000-editor-roadmap.md` and `tests/fixtures/a4000/`, real captures): the framing, the
checksum, the nibble coding, parameter request/reply and the Easy Edit addressing all behave as the
Yamaha service manual (MIDI DATA FORMAT, pages 32-42) says, EXCEPT one thing the manual gets wrong or
leaves unclear - the bulk byte count is MSB-first and dumps are multi-block (see `parse_bulk_dump`).

Conventions, like core/akai_sysex.py / core/s950_sysex.py: a "message" is the bytes BETWEEN F0 and F7
(a leading F0 / trailing F7 is tolerated on input, never produced).

Nothing here talks to the unit. The builders that WRITE (`build_object_edit`,
`build_system_parameter_change`, `build_bulk_dump` when sent) exist for later phases; no write has
been sent to a real A4000 yet.

Wire facts (A4000, device number 0, all measured unless marked "manual"):
- Yamaha manufacturer 0x43. Bulk messages use model byte 0x7A, parameter messages 0x58.
- Status byte after 0x43 is `<kind><device>`: kind 0 bulk dump, 1 parameter change, 2 dump request,
  3 parameter request; device = the unit's Device Number (0-15, must not be "off" on the unit).
- Every 8-bit data byte travels as TWO MIDI bytes, high nibble first.
- Bulk dump: `43 0n 7A { count(2, MSB first) span checksum }+` all inside ONE F0..F7. The checksum is the
  XOR of the span (7 bits). Only the first block's span starts with the 26-byte header: "LM  0474"
  (A4000; "...0475" is the A5000, manual), a 2-char format, and the 16-char object name. A span holds at
  most 4096 bytes (so the first block carries 4070 data bytes, later ones 4096 - the first split is
  measured, anything beyond two blocks is the manual's rule, unmeasured).
- Object select `43 1n 58 00 <name16> <type>` makes an object current; the unit ECHOES it back.
  Parameter request `43 3n 58 01 P1..P6` (object) / `... 02 P1..P6` (system) is answered with
  `43 1n 58 01|02 P1..P6 <nibbled data>`. Parameter messages have no checksum.
"""

import dataclasses

MANUFACTURER = 0x43
MODEL_BULK = 0x7A
MODEL_PARAM = 0x58

HEADER_A4000 = b"LM  0474"
HEADER_A5000 = b"LM  0475"

NAME_LENGTH = 16
HEADER_LENGTH = 8 + 2 + NAME_LENGTH  # "LM  0474" + format + object name
BLOCK_SPAN_MAX = 4096

FORMATS = ("SY", "PG", "SB", "SP", "WD", "SQ", "OL")

#: Object type codes (manual, p.36; program/sample/wave confirmed by the real object list)
OBJECT_TYPES = {
    "program": 0x14,
    "sample_bank": 0x11,
    "sample": 0x10,
    "wave": 0x02,
    "sequence": 0x13,
}
OBJECT_TYPE_NAMES = {code: name for name, code in OBJECT_TYPES.items()}

#: Identity reply: the A4000's / A5000's "device family number" (LSB-first 7-bit pair)
FAMILY_NUMBERS = {0x01DA: "A4000", 0x01DB: "A5000"}

KIND_BULK_DUMP = 0
KIND_PARAMETER_CHANGE = 1
KIND_DUMP_REQUEST = 2
KIND_PARAMETER_REQUEST = 3

SUB_OBJECT_SELECT = 0x00
SUB_OBJECT_PARAMETER = 0x01
SUB_SYSTEM_PARAMETER = 0x02


class YamahaSysexError(ValueError):
    """A message that isn't a well-formed Yamaha A4000/A5000 message of the expected kind."""


# -- low-level coding ------------------------------------------------------------------------------


def nibble(data):
    """Each byte -> two MIDI bytes, high nibble first."""
    out = bytearray()
    for b in bytes(data):
        out.append(b >> 4)
        out.append(b & 0x0F)
    return bytes(out)


def denibble(data):
    """Inverse of `nibble`. An odd length is an error (it means the framing is out of step)."""
    data = bytes(data)
    if len(data) % 2:
        raise YamahaSysexError(f"nibbled data has an odd length ({len(data)})")
    if any(b > 0x0F for b in data):
        raise YamahaSysexError("nibbled data has a byte above 0x0F")
    return bytes((data[i] << 4) | data[i + 1] for i in range(0, len(data), 2))


def xor7(span):
    """The bulk-dump checksum: XOR of every byte of the span, 7 bits."""
    x = 0
    for b in bytes(span):
        x ^= b
    return x & 0x7F


def pad_name(name, fill=0x20):
    """A 16-byte object name: ASCII, padded (spaces by default)."""
    try:
        raw = name.encode("ascii")
    except UnicodeEncodeError as e:
        raise YamahaSysexError(f"object name {name!r} isn't plain ASCII") from e
    if len(raw) > NAME_LENGTH:
        raise YamahaSysexError(f"object name {name!r} is longer than {NAME_LENGTH} characters")
    if any(b > 0x7F or b < 0x20 for b in raw):
        raise YamahaSysexError(f"object name {name!r} has non-printable characters")
    return raw + bytes([fill]) * (NAME_LENGTH - len(raw))


def decode_name(raw):
    """A name field -> str, trailing spaces/NULs stripped (the unit pads with spaces; empty slots are NULs)."""
    return bytes(raw).decode("ascii", "replace").rstrip(" \x00")


def program_object_name(number):
    """Programs are addressed by their number as text - 1 -> "001" (padded by `pad_name` as usual)."""
    if not 1 <= number <= 999:
        raise YamahaSysexError(f"program number {number} out of range")
    return f"{number:03d}"


def encode_value(value, size, signed=False):
    """A parameter value as `size` big-endian bytes (UC/SC 1, US/SS 2, UL/SL 4) - NOT yet nibbled."""
    lo, hi = (-(1 << (8 * size - 1)), (1 << (8 * size - 1)) - 1) if signed else (0, (1 << (8 * size)) - 1)
    if not lo <= value <= hi:
        raise YamahaSysexError(f"{value} doesn't fit {'a signed' if signed else 'an unsigned'} {size}-byte value")
    return int(value).to_bytes(size, "big", signed=signed)


def decode_value(raw, signed=False):
    return int.from_bytes(bytes(raw), "big", signed=signed)


def _strip(message):
    message = bytes(message)
    if message[:1] == b"\xf0":
        message = message[1:]
    if message[-1:] == b"\xf7":
        message = message[:-1]
    return message


def _check_device(device):
    if not 0 <= device <= 15:
        raise YamahaSysexError(f"device number {device} must be 0-15")


def _check_params(params):
    params = tuple(params)
    if not 1 <= len(params) <= 6 or any(not 0 <= p <= 0x7F for p in params):
        raise YamahaSysexError(f"parameter number {params} must be 1-6 values of 0-127")
    return params + (0,) * (6 - len(params))


# -- identity --------------------------------------------------------------------------------------


def build_identity_request(channel=0x7F):
    """Universal Identity Request (F0 7E <ch> 06 01 F7); 0x7F = any device."""
    return bytes([0x7E, channel & 0x7F, 0x06, 0x01])


@dataclasses.dataclass(frozen=True)
class IdentityReply:
    channel: int
    family_code: bytes  # raw 2 bytes (the manual calls it $0041; the unit sends 00 41)
    family_number: int  # LSB-first 7-bit pair: 0x01DA = A4000
    revision: bytes  # raw 4 bytes, firmware specific

    @property
    def model(self):
        return FAMILY_NUMBERS.get(self.family_number, "unknown")


def parse_identity_reply(message):
    m = _strip(message)
    if len(m) != 13 or m[0] != 0x7E or m[2:4] != b"\x06\x02" or m[4] != MANUFACTURER:
        raise YamahaSysexError("not a Yamaha identity reply")
    return IdentityReply(
        channel=m[1],
        family_code=m[5:7],
        family_number=m[7] | (m[8] << 7),
        revision=m[9:13],
    )


# -- bulk dumps ------------------------------------------------------------------------------------


def build_dump_request(device, fmt, name="", *, header=HEADER_A4000, fill=0x20):
    """Ask the unit to send a bulk dump. `name` is the object (a program is its number, "001");
    the object list and system parameters have no name, so leave it empty."""
    _check_device(device)
    if fmt not in FORMATS:
        raise YamahaSysexError(f"unknown bulk format {fmt!r}")
    return (
        bytes([MANUFACTURER, (KIND_DUMP_REQUEST << 4) | device, MODEL_BULK])
        + header
        + fmt.encode("ascii")
        + pad_name(name, fill)
    )


@dataclasses.dataclass(frozen=True)
class BulkDump:
    device: int
    fmt: str  # "PG", "SP", "OL", ...
    header: bytes  # "LM  0474"
    name: str  # decoded object name ("Object List" for the list, "001" for a program)
    name_raw: bytes  # the 16 bytes as sent
    data: bytes  # the real (un-nibbled) payload, all blocks joined
    blocks: int  # how many blocks the dump arrived in


def parse_bulk_dump(message, *, verify=True):
    """Decode a bulk dump. With `verify` (default) a bad checksum, truncation or trailing bytes raise."""
    m = _strip(message)
    if len(m) < 3 or m[0] != MANUFACTURER or m[2] != MODEL_BULK or m[1] >> 4 != KIND_BULK_DUMP:
        raise YamahaSysexError("not a Yamaha bulk dump")
    device = m[1] & 0x0F
    pos, spans = 3, []
    while pos < len(m):
        if pos + 2 > len(m):
            raise YamahaSysexError("bulk dump truncated inside a byte count")
        count = (m[pos] << 7) | m[pos + 1]  # MSB first (measured: 20 00 = 4096)
        end = pos + 2 + count
        if end >= len(m):
            raise YamahaSysexError("bulk dump truncated: block runs past the end")
        span, checksum = m[pos + 2 : end], m[end]
        if verify and xor7(span) != checksum:
            raise YamahaSysexError(
                f"bulk dump block {len(spans) + 1} checksum mismatch "
                f"(got {checksum:#04x}, computed {xor7(span):#04x})"
            )
        spans.append(span)
        pos = end + 1
    if not spans or len(spans[0]) < HEADER_LENGTH:
        raise YamahaSysexError("bulk dump has no header")
    head = spans[0][:HEADER_LENGTH]
    return BulkDump(
        device=device,
        fmt=head[8:10].decode("ascii", "replace"),
        header=head[:8],
        name=decode_name(head[10:]),
        name_raw=head[10:],
        data=denibble(spans[0][HEADER_LENGTH:] + b"".join(spans[1:])),
        blocks=len(spans),
    )


def build_bulk_dump(device, fmt, name_raw, data, *, header=HEADER_A4000):
    """Frame `data` as a bulk dump message (what the unit sends; what we'd send to LOAD an object).
    `name_raw` is the exact 16 name bytes - pass `BulkDump.name_raw` to round-trip byte for byte.
    A WRITE when sent to the unit; unused so far."""
    _check_device(device)
    if fmt not in FORMATS:
        raise YamahaSysexError(f"unknown bulk format {fmt!r}")
    name_raw = bytes(name_raw)
    if len(name_raw) != NAME_LENGTH or len(header) != 8:
        raise YamahaSysexError("bulk header or object name has the wrong length")
    body = nibble(data)
    first = BLOCK_SPAN_MAX - HEADER_LENGTH
    spans = [header + fmt.encode("ascii") + name_raw + body[:first]]
    for i in range(first, len(body), BLOCK_SPAN_MAX):
        spans.append(body[i : i + BLOCK_SPAN_MAX])
    out = bytearray([MANUFACTURER, (KIND_BULK_DUMP << 4) | device, MODEL_BULK])
    for span in spans:
        out += bytes([len(span) >> 7, len(span) & 0x7F]) + span + bytes([xor7(span)])
    return bytes(out)


@dataclasses.dataclass(frozen=True)
class ObjectEntry:
    type: int
    name: str

    @property
    def kind(self):
        return OBJECT_TYPE_NAMES.get(self.type, f"type {self.type}")


def parse_object_list(data):
    """The `OL` payload: `<type byte><16-char name>` repeated, 17 bytes each (measured: 142 entries on a
    cold-booted A4000 - 128 programs, then built-in waveforms as wave + sample pairs)."""
    data = bytes(data)
    if len(data) % 17:
        raise YamahaSysexError(f"object list is {len(data)} bytes, not a multiple of 17")
    return [ObjectEntry(data[i], decode_name(data[i + 1 : i + 17])) for i in range(0, len(data), 17)]


# -- parameter messages ----------------------------------------------------------------------------


def build_object_select(device, name, object_type):
    """Make an object current for later parameter requests/edits. Changes no data; the unit echoes it."""
    _check_device(device)
    if isinstance(object_type, str):
        object_type = OBJECT_TYPES[object_type]
    return (
        bytes([MANUFACTURER, (KIND_PARAMETER_CHANGE << 4) | device, MODEL_PARAM, SUB_OBJECT_SELECT])
        + pad_name(name)
        + bytes([object_type])
    )


def build_parameter_request(device, params, *, system=False):
    """Read one parameter of the currently selected object (or a system parameter). `params` = P1..P6
    (fewer are zero-padded)."""
    _check_device(device)
    sub = SUB_SYSTEM_PARAMETER if system else SUB_OBJECT_PARAMETER
    return bytes([MANUFACTURER, (KIND_PARAMETER_REQUEST << 4) | device, MODEL_PARAM, sub]) + bytes(
        _check_params(params)
    )


def build_object_edit(device, params, value_bytes):
    """WRITE one parameter of the currently selected object. `value_bytes` = the value as raw big-endian
    bytes (use `encode_value`); nibbling is done here. Not yet sent to a real unit."""
    _check_device(device)
    return (
        bytes([MANUFACTURER, (KIND_PARAMETER_CHANGE << 4) | device, MODEL_PARAM, SUB_OBJECT_PARAMETER])
        + bytes(_check_params(params))
        + nibble(value_bytes)
    )


def build_system_parameter_change(device, params, value_bytes):
    """WRITE one system parameter. Not yet sent to a real unit."""
    _check_device(device)
    return (
        bytes([MANUFACTURER, (KIND_PARAMETER_CHANGE << 4) | device, MODEL_PARAM, SUB_SYSTEM_PARAMETER])
        + bytes(_check_params(params))
        + nibble(value_bytes)
    )


@dataclasses.dataclass(frozen=True)
class ParameterMessage:
    kind: str  # "select", "object", "system"
    device: int
    params: tuple  # P1..P6 (empty for a select)
    data: bytes  # the un-nibbled value ("object"/"system"), empty for a select
    object_name: str = ""  # a select only
    object_type: int = None  # a select only


def parse_parameter_message(message):
    """A parameter change/reply (kind 1): an object select (incl. the unit's echo of ours), or an object /
    system parameter with its value."""
    m = _strip(message)
    if len(m) < 4 or m[0] != MANUFACTURER or m[2] != MODEL_PARAM or m[1] >> 4 != KIND_PARAMETER_CHANGE:
        raise YamahaSysexError("not a Yamaha parameter message")
    device, sub = m[1] & 0x0F, m[3]
    if sub == SUB_OBJECT_SELECT:
        if len(m) != 4 + NAME_LENGTH + 1:
            raise YamahaSysexError("object select has the wrong length")
        return ParameterMessage(
            "select", device, (), b"", decode_name(m[4 : 4 + NAME_LENGTH]), m[4 + NAME_LENGTH]
        )
    if sub in (SUB_OBJECT_PARAMETER, SUB_SYSTEM_PARAMETER):
        if len(m) < 10:
            raise YamahaSysexError("parameter message too short")
        return ParameterMessage(
            "object" if sub == SUB_OBJECT_PARAMETER else "system",
            device,
            tuple(m[4:10]),
            denibble(m[10:]),
        )
    raise YamahaSysexError(f"unknown parameter sub-status {sub:#04x}")


# -- streams ---------------------------------------------------------------------------------------


def split_messages(blob):
    """Every F0..F7 message in a byte string (a .syx file or a capture), each without its F0/F7."""
    blob, out, i = bytes(blob), [], 0
    while True:
        start = blob.find(b"\xf0", i)
        if start < 0:
            return out
        end = blob.find(b"\xf7", start)
        if end < 0:
            return out  # unterminated tail: not a message
        out.append(blob[start + 1 : end])
        i = end + 1


def classify(message):
    """A short label for a received message: "identity_reply", "bulk_dump", "parameter", "dump_request",
    "parameter_request" or "unknown"."""
    m = _strip(message)
    if len(m) >= 4 and m[0] == 0x7E and m[2:4] == b"\x06\x02":
        return "identity_reply"
    if len(m) >= 3 and m[0] == MANUFACTURER:
        kind, model = m[1] >> 4, m[2]
        if model == MODEL_BULK and kind == KIND_BULK_DUMP:
            return "bulk_dump"
        if model == MODEL_BULK and kind == KIND_DUMP_REQUEST:
            return "dump_request"
        if model == MODEL_PARAM and kind == KIND_PARAMETER_CHANGE:
            return "parameter"
        if model == MODEL_PARAM and kind == KIND_PARAMETER_REQUEST:
            return "parameter_request"
    return "unknown"
