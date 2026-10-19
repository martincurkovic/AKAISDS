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


# --- a dry run of a Load (what WOULD change on the sampler; nothing is written anywhere) ------------------------------------
#
# There is no Load Multi yet. This is its first half: given the multi now on the sampler and a multi file, say field by field what a load would write, how sure
# we are that each write is safe, and what would be clamped or left alone. A real loader would write exactly these fields (one SysEx write per field, never a
# whole-block MULTIDATA write) and nothing else. See dev_docs/multi-save-load-progress.md.

#: (field, how much evidence there is that WRITING it from this app is safe). "proven": the Multi tab writes it today and it works on the owner's unit.
#: "measured": s3ked measured the write on hardware (the range matters: see FX_MAX). "unproven": no measurement of writing it exists.
LOADABLE_HEADER_FIELDS = (
    ("MULTINAME", "proven"),
    ("FX1", "measured"),
    ("FX2", "measured"),
    ("FX3", "measured"),
    ("FX4", "measured"),
)
LOADABLE_PART_FIELDS = (
    ("PMCHAN", "proven"),
    ("STEREO", "proven"),  # the panel's "Lev"
    ("PANPOS", "proven"),
    ("PRIORT", "unproven"),
    ("PLAYLO", "unproven"),
    ("PLAYHI", "unproven"),
    ("OUTPUT", "unproven"),
    ("VOSCL", "unproven"),
    ("TRANSPOSE", "unproven"),
    ("PFXCHAN", "unproven"),
    ("PFXSLEV", "unproven"),
    ("PTUNOCM", "unproven"),
)
OMNI = 255


@dataclass
class Change:
    scope: str  # "multi" or "part 3"
    name: str
    current: object
    new: object  # what would be written (after any clamping)
    evidence: str  # "proven" | "measured" | "unproven"
    note: str = ""


@dataclass
class Binding:
    """A part's program: not a field write (a part is bound to a program by a MIDI Program Change on its channel, by the program's name)."""

    part: int  # 1-16
    current: str
    new: str
    state: str  # "same" | "blank in file" | "resident" | "not resident" | "unknown" (no program list was given)


@dataclass
class LoadPlan:
    changes: list = field(default_factory=list)
    bindings: list = field(default_factory=list)
    unchanged: int = 0  # loadable fields that already match
    not_loaded: list = field(default_factory=list)  # things in the file a load would not touch, in words


def _differs(a, b):
    return a != b


def _clamp_channel(raw):
    """The panel shows a stored channel above 15 (but not 255 = OMNI) as 16, so a load stores 15 for it rather than a value the panel only clamps."""
    value = int(raw)
    if value == OMNI or 0 <= value <= 15:
        return value, ""
    return 15, f"the file has {value}, which the panel shows as channel 16"


def plan_load(current, target, resident_programs=None):
    """What loading `target` (a MultiFile) over `current` (the multi on the sampler) would write. `resident_programs` is the sampler's program names, if known."""
    plan = LoadPlan()
    resident = None if resident_programs is None else {name.rstrip() for name in resident_programs}  # (as part_name: a name may start with a space)

    def consider(scope, region, name, evidence, current_block, target_block):
        have = _decode(region, name, current_block)
        want = _decode(region, name, target_block)
        note = ""
        if name in ("FX1", "FX2", "FX3", "FX4"):
            # judged on the BYTE (s3k shows an effect index one higher than the byte: display_offset 1), because the byte is what crashes the sampler above 204
            prm = _field(region, name)
            raw = target_block[prm.offset]
            if raw > FX_MAX:
                want = p.decode_field(prm, bytes([FX_MAX]))
                note = f"the file's byte is {raw}; above {FX_MAX} the sampler crashes, so {FX_MAX} would be written (shown as {want})"
        elif name == "PMCHAN":
            want, note = _clamp_channel(want)
        if _differs(have, want):
            plan.changes.append(Change(scope, name, have, want, evidence, note))
        else:
            plan.unchanged += 1

    for name, evidence in LOADABLE_HEADER_FIELDS:
        consider("multi", "multi", name, evidence, current.header, target.header)
    for index in range(PART_COUNT):
        for name, evidence in LOADABLE_PART_FIELDS:
            consider(f"part {index + 1}", "multipart", name, evidence, current.parts[index], target.parts[index])
        have, want = current.part_name(index), target.part_name(index)
        if have == want:
            state = "same"
        elif not want:
            state = "blank in file"
        elif resident is None:
            state = "unknown"
        else:
            state = "resident" if want in resident else "not resident"
        plan.bindings.append(Binding(index + 1, have, want, state))

    if _decode("multi", "FXFILENAME", current.header) != _decode("multi", "FXFILENAME", target.header):
        plan.not_loaded.append("the effects file name (never seen non-zero; not written)")
    if any(target.extra_header):
        plan.not_loaded.append(f"{sum(1 for b in target.extra_header if b)} non-zero header byte(s) beyond the first 32 (unknown meaning; SysEx cannot write them)")
    modelled = _modelled_part_offsets()
    other = sum(
        1
        for index in range(PART_COUNT)
        for offset in range(PART_SIZE)
        if offset not in modelled and offset not in IGNORED_PART_OFFSETS and current.parts[index][offset] != target.parts[index][offset]
    )
    if other:
        plan.not_loaded.append(f"{other} part byte(s) the app has no field for differ (not written)")
    return plan


def _modelled_part_offsets():
    """Every part-record offset that is one of the loadable fields or the program name."""
    offsets = set()
    for name, _evidence in LOADABLE_PART_FIELDS + (("PRNAME", ""),):
        prm = _field("multipart", name)
        offsets.update(range(prm.offset, prm.offset + prm.size))
    return offsets


def _channel_text(value):
    value = int(value)
    return "OMNI" if value == OMNI else f"{value + 1}"


def format_value(name, value):
    """A value as the panel would show it, for a plan's lines."""
    if name == "PMCHAN":
        return _channel_text(value)
    if name == "PRNAME" or name == "MULTINAME":
        return repr(str(value).rstrip())
    return str(value)
