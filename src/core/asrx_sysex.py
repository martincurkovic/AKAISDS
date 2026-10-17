# Ensoniq ASR-X SysEx: framing, number/payload encoding, the commands a sample transfer needs and the `SMP1` sample object.
# Pure Python, no Qt / MIDI.
#
# **Written from a STATIC READING of the ASR-X v2.67 ROM (dev_docs/asrx-rom-findings.md). It has never run against an ASR-X.** Nothing here comes from Ensoniq documents
# (there is no public ASR-X SysEx specification) and nothing is copied from any other project. Grades below: ROM-CERTAIN (the code says so
# unambiguously), ROM-INFERRED (read from the code with a step of reasoning), GUESS (ours). The first real log from a tester is what confirms them.
#
# Message convention matches core/ensoniq_sysex.py and core/s950_sysex.py: a "message" is the bytes BETWEEN the F0 and the F7.
#
# Wire facts:
#   family header   0F 10 01 <dev> <cmd> ...          manufacturer, family 10, product 01 (the ASR-10/EPS use 0F 03 - the ASR-X drops that) ROM-CERTAIN
#   device byte     the unit's "SysEx Device ID" (0-127) or 7F for all; replies carry the unit's OWN id                                     ROM-CERTAIN
#   14-bit number   2 bytes, low 7 bits first                                                                                                ROM-CERTAIN
#   32-bit number   5 bytes, low 7 bits first                                                                                                ROM-CERTAIN
#   payload         four memory bytes read as a BIG-ENDIAN 32-bit word -> 5 septets, last group zero padded                                  ROM-CERTAIN
#   checksum        object transfers only: sum of every payload septet, low 14 bits, 2 septets, just before F7                               ROM-CERTAIN
#   reply opcode    command + 0x40 (01 -> 41, 02 -> 42, 03 -> 43, 04 -> 44); an error is  0F 10 01 <dev> 7F <code>                            ROM-CERTAIN
#   universal       F0 7E <dev> 05 04 <slot:2> F7 asks a sample's name; the answer is 7E <dev> 05 03 <slot:2> 00 10 <16 name bytes>          ROM-CERTAIN
#
# The sample (object type 8, tag "SMP1") is a 52-byte header followed by signed 16-bit BIG-ENDIAN samples; layout in SAMPLE_HEADER below.

import dataclasses
import math
import struct

MANUFACTURER_ENSONIQ = 0x0F
FAMILY_ASRX = 0x10
PRODUCT_ASRX = 0x01
HEADER = bytes((MANUFACTURER_ENSONIQ, FAMILY_ASRX, PRODUCT_ASRX))

UNIVERSAL_NON_REALTIME = 0x7E
BROADCAST_DEVICE = 0x7F

# --- commands (request) and replies (command + 0x40) ----------------------------------------------------------
CMD_GET_OBJECT = 0x01  # type, slot -> reply 0x41: the whole object, with a checksum
CMD_PUT_OBJECT = 0x02  # type, slot, size, payload, checksum -> reply 0x42 (an ACK) once committed
CMD_GET_OBJECT_EXPORT = 0x03  # like 01, reply 0x43 (the same layout for a sample)
CMD_READ_RANGE = 0x04  # type, slot, section, index, offset, length -> reply 0x44 with that part, no checksum
REPLY_OBJECT = 0x41
REPLY_PUT_ACK = 0x42
REPLY_OBJECT_EXPORT = 0x43
REPLY_RANGE = 0x44
REPLY_ERROR = 0x7F

#: error codes seen in `0F 10 01 <dev> 7F <code>`: the failed command's reply opcode (0x41/0x43/0x45) or the command number (6) - the ROM isn't consistent
ERROR_NO_SUCH_OBJECT = 0x41  # what a name request or a GET of an empty slot answers (ROM-CERTAIN for the name request)

TYPE_SYSTEM, TYPE_SOUND, TYPE_PERFORMANCE, TYPE_KIT = 0, 1, 2, 3
TYPE_SAMPLE = 8  # tag "SMP1"
SAMPLE_TAG = b"SMP1"
SLOT_MAX = 0x3FF  # the sample table holds 0..0x3FF
SLOT_LIMIT = SLOT_MAX + 1

HEADER_BYTES = 0x34
NAME_LENGTH = 16
DEFAULT_ROOT_KEY = 60
#: pitch word reference: EPS rate code 21, 1 / (21 x 1.6 us)
REFERENCE_RATE_HZ = 29762.0
NO_PARENT = 0xFFFF

LOOP_NONE, LOOP_FORWARD, LOOP_ALTERNATING = 0, 2, 3  # byte at +0x25 (0 and 1 both mean "no loop")

#: more than this many samples is refused (32 MB of audio) - the unit's own limit isn't known
MAX_SAMPLES = 16 * 1024 * 1024
#: shorter than this is padded with silence
MIN_SAMPLES = 32

# The 52-byte header, big-endian, in the order it sits in memory (offset: field). ROM-CERTAIN unless noted.
#   0x00 u32 total size (= 0x34 + 2 x samples)      0x04 "SMP1"            0x08 16 bytes name, ASCII, space padded
#   0x18 u8 flag (0)      0x19 u8 flag (0)          0x1A u8 root key        0x1B u8 0          0x1C u16 0 (meaning unknown)
#   0x1E u16 0xFFFF = owns its audio, else the slot whose audio it shares                       0x20 u16 its own slot number
#   0x22 u16 0            0x24 u8 0x7F (meaning unknown)        0x25 u8 loop mode               0x26 u16 pitch word
#   0x28 u32 start x16    0x2C u32 loop start x16               0x30 u32 end (loop end when looping) x16
_HEADER_STRUCT = struct.Struct(">I4s16sBBBBHHHHBBHIII")
assert _HEADER_STRUCT.size == HEADER_BYTES


class AsrxError(ValueError):
    pass


# --- numbers ---------------------------------------------------------------------------------------------------


def encode_u14(value):
    if not 0 <= value <= 0x3FFF:
        raise AsrxError(f"{value} doesn't fit in 14 bits")
    return bytes((value & 0x7F, (value >> 7) & 0x7F))


def encode_u32(value):
    if not 0 <= value <= 0xFFFFFFFF:
        raise AsrxError(f"{value} doesn't fit in 32 bits")
    return bytes((value >> (7 * i)) & 0x7F for i in range(5))


def decode_u14(data, pos=0):
    a, b = data[pos], data[pos + 1]
    if (a | b) & 0x80:
        raise AsrxError("a data byte has bit 7 set")
    return a | (b << 7)


def decode_u32(data, pos=0):
    chunk = data[pos : pos + 5]
    if len(chunk) != 5 or any(c & 0x80 for c in chunk):
        raise AsrxError("bad 32-bit number")
    return sum(c << (7 * i) for i, c in enumerate(chunk)) & 0xFFFFFFFF


def pack_payload(data):
    """Memory bytes -> septets (what the wire carries): each 4-byte group, read big-endian, becomes 5 septets low first; the last group is zero padded."""
    data = bytes(data)
    if len(data) % 4:
        data += b"\x00" * (4 - len(data) % 4)
    out = bytearray()
    for (word,) in struct.iter_unpack(">I", data):
        out += encode_u32(word)
    return bytes(out)


def unpack_payload(septets, nbytes):
    """The inverse of pack_payload: `septets` (a multiple of 5 long) -> the first `nbytes` memory bytes."""
    if len(septets) % 5:
        raise AsrxError(f"payload length {len(septets)} isn't a multiple of 5")
    if any(b & 0x80 for b in septets):
        raise AsrxError("payload contains a byte with bit 7 set")
    out = bytearray()
    for i in range(0, len(septets), 5):
        out += struct.pack(">I", decode_u32(septets, i))
    if nbytes > len(out):
        raise AsrxError(f"payload holds {len(out)} bytes, {nbytes} wanted")
    return bytes(out[:nbytes])


def payload_septet_count(nbytes):
    return -(-nbytes // 4) * 5


def checksum(septets):
    """Sum of the payload septets, low 14 bits (the unit's 0xBE02CC0 / 0xBE02C68 accumulator)."""
    return sum(septets) & 0x3FFF


# --- names -----------------------------------------------------------------------------------------------------


def encode_name(name, length=NAME_LENGTH):
    text = "".join(c if 0x20 <= ord(c) <= 0x7E else "_" for c in (name or "").strip())[:length]
    text = text or "SAMPLE"
    return text.ljust(length).encode("ascii")


def decode_name(raw):
    return bytes(raw).split(b"\x00")[0].decode("ascii", errors="replace").rstrip()


# --- requests --------------------------------------------------------------------------------------------------


def _slot_check(slot):
    if not 0 <= slot <= SLOT_MAX:
        raise AsrxError(f"sample slot {slot} is outside 0..{SLOT_MAX}")


def build_identity_request(device=BROADCAST_DEVICE):
    return bytes((UNIVERSAL_NON_REALTIME, device & 0x7F, 0x06, 0x01))


def build_name_request(device, slot):
    """`F0 7E <dev> 05 04 <slot> F7` - the standard SDS-extension sample name request. Answers with the name, or 7F 41 for an empty slot."""
    _slot_check(slot)
    return bytes((UNIVERSAL_NON_REALTIME, device & 0x7F, 0x05, 0x04)) + encode_u14(slot)


def build_get_object(device, slot, *, type_=TYPE_SAMPLE, command=CMD_GET_OBJECT):
    _slot_check(slot)
    return HEADER + bytes((device & 0x7F, command, type_)) + encode_u14(slot)


def build_read_range(device, slot, offset, length, *, type_=TYPE_SAMPLE, section=0, index=0):
    """Read `length` bytes at `offset` of section `section` (0 = from the start of the object) of slot `slot`. Out-of-range requests are answered with SILENCE."""
    _slot_check(slot)
    return (
        HEADER
        + bytes((device & 0x7F, CMD_READ_RANGE, type_))
        + encode_u14(slot)
        + bytes((section & 0x7F,))
        + encode_u14(index)
        + encode_u32(offset)
        + encode_u32(length)
    )


def build_put_object(device, slot, obj, *, type_=TYPE_SAMPLE):
    """Upload a whole object (header and audio, from byte 0) into `slot`. Returns the message and the checksum it carries."""
    _slot_check(slot)
    septets = pack_payload(obj)
    message = (
        HEADER
        + bytes((device & 0x7F, CMD_PUT_OBJECT, type_))
        + encode_u14(slot)
        + encode_u32(len(obj))
        + septets
        + encode_u14(checksum(septets))
    )
    return message


# --- replies ---------------------------------------------------------------------------------------------------


@dataclasses.dataclass
class Message:
    kind: str  # "name" | "error" | "object" | "range" | "ack" | "identity" | "other"
    device: int = 0
    command: int = 0
    slot: int = None
    name: str = None
    error_code: int = None
    type_: int = None
    size: int = None
    payload: bytes = None
    checksum_sent: int = None
    checksum_ok: bool = None
    section: int = None
    index: int = None
    offset: int = None
    length: int = None
    raw: bytes = b""


def is_asrx(data):
    return len(data) >= 5 and bytes(data[:3]) == HEADER


def parse_message(data):
    """Classify and decode a message from the unit (the bytes between F0 and F7). Raises AsrxError if it claims to be one kind and is malformed."""
    data = bytes(data)
    if len(data) >= 4 and data[0] == UNIVERSAL_NON_REALTIME:
        if data[2] == 0x05 and data[3] == 0x03 and len(data) >= 8:
            slot = decode_u14(data, 4)
            pos = 6
            tag_len = data[pos]
            pos += 1 + tag_len
            if pos >= len(data):
                raise AsrxError("sample name reply is cut short")
            name_len = data[pos]
            raw = data[pos + 1 : pos + 1 + name_len]
            return Message("name", device=data[1], slot=slot, name=decode_name(raw), raw=data)
        if data[2] == 0x06 and data[3] == 0x02:
            return Message("identity", device=data[1], payload=data[4:], raw=data)
        return Message("other", device=data[1], raw=data)
    if not is_asrx(data):
        return Message("other", raw=data)
    device, command = data[3], data[4]
    if command == REPLY_ERROR:
        if len(data) < 6:
            raise AsrxError("error reply without a code")
        return Message("error", device=device, command=command, error_code=data[5], raw=data)
    if command == REPLY_PUT_ACK:
        return Message("ack", device=device, command=command, raw=data)
    if command in (REPLY_OBJECT, REPLY_OBJECT_EXPORT):
        if len(data) < 5 + 1 + 2 + 5 + 2:
            raise AsrxError("object reply is cut short")
        type_ = data[5]
        slot = decode_u14(data, 6)
        size = decode_u32(data, 8)
        septets = data[13:-2]
        want = payload_septet_count(size)
        if len(septets) != want:
            raise AsrxError(f"object reply carries {len(septets)} payload bytes, {want} expected for {size} bytes")
        sent = decode_u14(data, len(data) - 2)
        return Message(
            "object",
            device=device,
            command=command,
            type_=type_,
            slot=slot,
            size=size,
            payload=unpack_payload(septets, size),
            checksum_sent=sent,
            checksum_ok=sent == checksum(septets),
            raw=data,
        )
    if command == REPLY_RANGE:
        if len(data) < 5 + 1 + 2 + 1 + 2 + 5 + 5:
            raise AsrxError("range reply is cut short")
        type_ = data[5]
        slot = decode_u14(data, 6)
        section = data[8]
        index = decode_u14(data, 9)
        offset = decode_u32(data, 11)
        length = decode_u32(data, 16)
        septets = data[21:]
        want = payload_septet_count(length)
        if len(septets) != want:
            raise AsrxError(f"range reply carries {len(septets)} payload bytes, {want} expected for {length} bytes")
        return Message(
            "range",
            device=device,
            command=command,
            type_=type_,
            slot=slot,
            section=section,
            index=index,
            offset=offset,
            length=length,
            payload=unpack_payload(septets, length),
            raw=data,
        )
    return Message("other", device=device, command=command, raw=data)


# --- the sample object -----------------------------------------------------------------------------------------


def pitch_word(rate_hz, root_key=DEFAULT_ROOT_KEY):
    """The unsigned 16-bit value for the header's pitch word at +0x26: -(round(3072 x log2(29762 / rate))) - 256 x root_key (ROM: routine 0xAE7FA)."""
    if rate_hz <= 0:
        raise AsrxError("sample rate must be positive")
    t = math.floor(256 * 12 * math.log2(REFERENCE_RATE_HZ / rate_hz) + 0.5)
    value = -t - 256 * root_key
    if not -32768 <= value <= 32767:
        raise AsrxError(f"a sample rate of {rate_hz:g} Hz is outside what the pitch word can hold")
    return value & 0xFFFF


def rate_from_pitch_word(word, root_key=DEFAULT_ROOT_KEY):
    """The inverse of pitch_word, in Hz (a float; the unit's own default word 0xCACD is about 44,082 Hz at root 60)."""
    signed = word - 0x10000 if word >= 0x8000 else word
    t = -signed - 256 * root_key
    return REFERENCE_RATE_HZ / (2.0 ** (t / 3072.0))


@dataclasses.dataclass
class SampleObject:
    slot: int
    name: str
    root_key: int
    pitch_word: int
    rate_hz: float
    loop_mode: int
    start: float  # samples
    loop_start: float
    end: float
    parent: int  # NO_PARENT, or the slot whose audio this sample shares
    own_slot: int
    flags: tuple  # (+0x18, +0x19, +0x1B, +0x1C, +0x22, +0x24) as read
    size: int
    samples: list
    header: bytes

    @property
    def shares_audio(self):
        return self.parent != NO_PARENT


def build_sample_object(slot, name, samples, rate_hz, *, root_key=DEFAULT_ROOT_KEY):
    """The bytes of a new `SMP1` object for `slot` (what an upload carries). `samples` are signed 16-bit ints; an odd count gets one silent sample on the end
    so the object stays a whole number of 32-bit words; fewer than MIN_SAMPLES are padded with silence."""
    _slot_check(slot)
    words = [max(-32768, min(32767, int(v))) for v in samples]
    if len(words) > MAX_SAMPLES:
        raise AsrxError(f"too long for the ASR-X ({len(words):,} samples, at most {MAX_SAMPLES:,})")
    if len(words) < MIN_SAMPLES:
        words += [0] * (MIN_SAMPLES - len(words))
    if len(words) % 2:
        words.append(0)
    n = len(words)
    header = _HEADER_STRUCT.pack(
        HEADER_BYTES + 2 * n,
        SAMPLE_TAG,
        encode_name(name),
        0,
        0,
        root_key & 0x7F,
        0,
        0,
        NO_PARENT,
        slot,
        0,
        0x7F,
        LOOP_NONE,
        pitch_word(rate_hz, root_key),
        0,
        0,
        (n - 1) << 4,
    )
    return header + struct.pack(f">{n}h", *words)


def parse_sample_object(data, *, slot=None, header_only=False):
    """Decode a whole `SMP1` object. Raises AsrxError if it isn't one. A sample that shares another's audio may legitimately hold no samples of its own.
    With `header_only` the first 52 bytes are decoded and the size field isn't compared with the length (no samples are returned)."""
    data = bytes(data)
    if len(data) < HEADER_BYTES:
        raise AsrxError(f"object is only {len(data)} bytes, a sample header alone is {HEADER_BYTES}")
    (size, tag, raw_name, f18, f19, root, f1b, f1c, parent, own, f22, f24, loop_mode, word, start, loop_start, end) = _HEADER_STRUCT.unpack_from(data)
    if tag != SAMPLE_TAG:
        raise AsrxError(f"object tag is {tag!r}, not {SAMPLE_TAG!r}")
    if size < HEADER_BYTES:
        raise AsrxError(f"object says it is {size} bytes, less than its own header")
    samples = []
    if not header_only:
        if size != len(data):
            raise AsrxError(f"object says it is {size} bytes, {len(data)} arrived")
        count = (size - HEADER_BYTES) // 2
        samples = list(struct.unpack_from(f">{count}h", data, HEADER_BYTES)) if count else []
    return SampleObject(
        slot=own if slot is None else slot,
        name=decode_name(raw_name),
        root_key=root,
        pitch_word=word,
        rate_hz=rate_from_pitch_word(word, root),
        loop_mode=loop_mode,
        start=start / 16,
        loop_start=loop_start / 16,
        end=end / 16,
        parent=parent,
        own_slot=own,
        flags=(f18, f19, f1b, f1c, f22, f24),
        size=size,
        samples=samples,
        header=data[:HEADER_BYTES],
    )


def describe_header(header):
    """One line a bug report can quote: every field of the 52-byte header, decoded."""
    try:
        obj = parse_sample_object(header, header_only=True)
    except AsrxError as ex:
        return f"unreadable header ({ex})"
    return (
        f"size={obj.size} name={obj.name!r} root={obj.root_key} pitch_word=0x{obj.pitch_word:04X} (~{obj.rate_hz:.1f} Hz) "
        f"loop_mode={obj.loop_mode} start={obj.start:g} loop_start={obj.loop_start:g} end={obj.end:g} "
        f"parent={'none' if not obj.shares_audio else obj.parent} own_slot={obj.own_slot} flags(18,19,1B,1C,22,24)={obj.flags}"
    )
