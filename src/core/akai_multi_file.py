"""Akai S2000 / S3000XL multi files (`.m3`) <-> the raw blocks the sampler sends over SysEx.

A multi file is ALWAYS 4096 bytes (layout from `dev_docs/s3000-multi-file-format.md`):

    0x000-0x3FF   the multi header, 1024 bytes. Only the first 32 are understood and they are exactly the 32-byte block RMULTIDATA
                  selector 0 returns (name at +3, FX1-FX4 at +16, the FX file name at +20); the rest is unknown and zero in the one real file seen
    0x400 + n*192 part n (n = 0..15), 192 bytes: a program header, whose fields sit at the same offsets as the SysEx `multipart` region

    NOT YET CHECKED against a file the sampler itself wrote beyond the one floppy image of a third-party project: `tools/s2000_multi_check.py` does
    that, read-only. Until it has been run this is "our format", not proven to be accepted by a real S2000 or by akaiutil.

Saving is "read the 32-byte header and the 16 part blocks, write them where the file wants them": the fields are never interpreted, so every byte the
app does not model travels untouched. Three things are not copied as read:

    * each part's first byte is written as 01, the record marker the one real file has (what a real sampler puts there on the WIRE is not known:
      the demo bridge sends 00, so reading does not insist on it);
    * the end link at part +0xBE-0xBF is written as FF FF ("unassigned"): a third-party note says anything else loads the wrong multi;
    * the header bytes beyond the first 32 are zero (SysEx cannot read them).

Left as the sampler reported them (they are sampler-internal): the link pointer at part +0x01-0x02 and the "last note played" byte at +0x6D.
"""

from dataclasses import dataclass, field

from s3k import params as p

MULTI_FILE_SIZE = 4096
HEADER_SIZE = 1024
WIRE_HEADER_SIZE = 32  # what RMULTIDATA selector 0 returns, and the first 32 bytes of the file
PART_SIZE = 192
PART_COUNT = 16
EXTENSION = ".m3"
DEFAULT_NAME = "MULTI FILE"

#: part-record bytes that are not settings: the block marker, the sampler-assigned link pointer, "last note played", the end link
PART_MARKER = 0x00
PART_MARKER_VALUE = 0x01  # a program header's block id
PART_LINK = (0x01, 0x03)  # [start, end)
PART_LAST_NOTE = 0x6D
PART_END_LINK = (0xBE, 0xC0)
UNASSIGNED_LINK = b"\xff\xff"
#: bytes a comparison with another source ignores: they are pointers and playback state, not settings
IGNORED_PART_OFFSETS = frozenset(
    {PART_LAST_NOTE, *range(*PART_LINK), *range(*PART_END_LINK)}
)

#: the effects/reverb indices in the header: the sampler crashed ("divide overflow") when one above this was written, so a loader clamps to it
FX_MAX = 204


class MultiFileError(ValueError):
    """The bytes are not a multi file this app can use (message is user-facing)."""


def _field(region, name):
    return p.lookup(name, region)


def _decode(region, name, block):
    prm = _field(region, name)
    return p.decode_field(prm, bytes(block[prm.offset : prm.offset + prm.size]))


@dataclass
class MultiFile:
    header: bytes = field(repr=False)  # the 32 wire bytes
    parts: list = field(default_factory=list, repr=False)  # 16 x 192 bytes
    extra_header: bytes = field(default=bytes(HEADER_SIZE - WIRE_HEADER_SIZE), repr=False)  # file bytes 0x20-0x3FF (zero when read over SysEx)

    @property
    def name(self):
        return str(_decode("multi", "MULTINAME", self.header)).rstrip()

    @property
    def fx(self):
        """FX1..FX4 as stored (the file's values: a loader must clamp them to 0..FX_MAX)."""
        offset = _field("multi", "FX1").offset
        return tuple(self.header[offset : offset + 4])

    def part_name(self, index):
        """The program name part `index` (0..15) points at, trailing spaces stripped; '' for an empty part."""
        return str(_decode("multipart", "PRNAME", self.parts[index])).rstrip()

    def part_channel(self, index):
        """The part's MIDI channel as stored (0-based; 255 = OMNI)."""
        return int(_decode("multipart", "PMCHAN", self.parts[index]))


def validate(header, parts):
    """Raise MultiFileError unless these blocks can make a multi file."""
    if len(header) != WIRE_HEADER_SIZE:
        raise MultiFileError(f"the multi header is {len(header)} bytes, expected {WIRE_HEADER_SIZE}")
    if len(parts) != PART_COUNT:
        raise MultiFileError(f"expected {PART_COUNT} multi parts, got {len(parts)}")
    for index, part in enumerate(parts):
        if len(part) != PART_SIZE:
            raise MultiFileError(f"multi part {index + 1} is {len(part)} bytes, expected {PART_SIZE}")


def read_from_bridge(bridge):
    """The resident multi as a MultiFile, read over SysEx (an `S3kBridge`, or anything with its `get_header_bytes`). READ ONLY.

    One request at a time, and parts 0-15 only: a read of part 16 or more silently returns part 15's buffer (s3ked), so nothing here ever probes
    upward. Sizes are checked, not block identifiers: what byte 0 of a part is on the wire is not known."""
    header = bytes(bridge.get_header_bytes("multi", 0, 0, WIRE_HEADER_SIZE))
    parts = [bytes(bridge.get_header_bytes("multipart", index, 0, PART_SIZE)) for index in range(PART_COUNT)]
    validate(header, parts)
    return MultiFile(header=header, parts=parts)


def build_file(header, parts, extra_header=None):
    """The 32-byte header and 16 part blocks as read from the sampler -> the 4096 bytes of a `.m3` file."""
    validate(header, parts)
    extra = bytes(HEADER_SIZE - WIRE_HEADER_SIZE) if extra_header is None else bytes(extra_header)
    if len(extra) != HEADER_SIZE - WIRE_HEADER_SIZE:
        raise MultiFileError(f"the extra header is {len(extra)} bytes, expected {HEADER_SIZE - WIRE_HEADER_SIZE}")
    out = bytearray(header) + extra
    for part in parts:
        block = bytearray(part)
        block[PART_MARKER] = PART_MARKER_VALUE
        block[PART_END_LINK[0] : PART_END_LINK[1]] = UNASSIGNED_LINK
        out += block
    assert len(out) == MULTI_FILE_SIZE
    return bytes(out)


def parse_file(data):
    """The bytes of a `.m3` file -> MultiFile (the blocks exactly as the file holds them: nothing is repaired or clamped, and only the size is checked,
    so the check tool can look at a real file whose markers or pointers are not what we expect)."""
    data = bytes(data)
    if len(data) != MULTI_FILE_SIZE:
        raise MultiFileError(
            f"this isn't an Akai multi file: it is {len(data)} bytes, a multi is always {MULTI_FILE_SIZE}"
        )
    parts = [data[HEADER_SIZE + n * PART_SIZE : HEADER_SIZE + (n + 1) * PART_SIZE] for n in range(PART_COUNT)]
    return MultiFile(header=data[:WIRE_HEADER_SIZE], parts=parts, extra_header=data[WIRE_HEADER_SIZE:HEADER_SIZE])


def safe_file_stem(name):
    """A file name stem from a multi name (Akai names allow characters some file systems don't)."""
    stem = "".join(c if c.isalnum() or c in "-_ #+." else "_" for c in name).strip()
    return stem or DEFAULT_NAME


# --- comparing two multis (the read-only check tool) --------------------------------------------------------------------


def _part_field_names():
    """offset -> parameter name for the `multipart` region (one entry per byte the parameter covers)."""
    names = {}
    for prm in p.region_params("multipart"):
        for offset in range(prm.offset, prm.offset + prm.size):
            names[offset] = prm.name
    return names


def describe_part_offset(offset):
    """'+0x10 PMCHAN' for a modelled byte, '+0x47 (not modelled)' otherwise."""
    name = _part_field_names().get(offset)
    return f"+{offset:#04x} {name}" if name else f"+{offset:#04x} (not modelled)"


def _describe_header_offset(offset):
    names = {}
    for prm in p.region_params("multi"):
        for o in range(prm.offset, prm.offset + prm.size):
            names[o] = prm.name
    name = names.get(offset)
    return f"{offset:#05x} {name}" if name else f"{offset:#05x} (not modelled)"


def compare(a, b):
    """Differences between two MultiFile objects as readable lines; an empty list means identical apart from the bytes that are not settings.

    Ignored: each part's link pointer, 'last note played' and end link (`IGNORED_PART_OFFSETS`). Compared: every other part byte, the 32 header
    bytes and the rest of the file header (where a real file may hold something SysEx cannot read)."""
    lines = []
    for offset in range(WIRE_HEADER_SIZE):
        if a.header[offset] != b.header[offset]:
            lines.append(f"header {_describe_header_offset(offset)}: {a.header[offset]} vs {b.header[offset]}")
    extra_diff = [o for o in range(len(a.extra_header)) if a.extra_header[o] != b.extra_header[o]]
    if extra_diff:
        first = extra_diff[0] + WIRE_HEADER_SIZE
        lines.append(
            f"header bytes beyond the 32 SysEx can read differ at {len(extra_diff)} offset(s), first {first:#05x} "
            f"({a.extra_header[extra_diff[0]]} vs {b.extra_header[extra_diff[0]]})"
        )
    for index in range(PART_COUNT):
        for offset in range(PART_SIZE):
            if offset in IGNORED_PART_OFFSETS:
                continue
            if a.parts[index][offset] != b.parts[index][offset]:
                lines.append(f"part {index + 1} {describe_part_offset(offset)}: {a.parts[index][offset]} vs {b.parts[index][offset]}")
    return lines
