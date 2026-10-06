"""Akai S1000 / S2000 / S3000 program files (`.p1` / `.p3`) <-> raw SysEx blocks.

A program file is the program header followed by N keygroups, all the same
length, with no extra file header:

    S1000:        150-byte program header + N x 150-byte keygroups  (`.p1`)
    S2000/S3000:  192-byte program header + N x 192-byte keygroups  (`.p3`)

N is the byte at header offset 42 (`GROUPS`). The two layouts are identical
field for field (checked against the S1000/S3000 specs and `s3k.params`), only
the block lengths differ. These are exactly the blocks RPDATA/RKDATA return, so
saving is "read the blocks, fix the pointers, concatenate" and loading is the
reverse - the fields never need interpreting, and bytes this app doesn't model
travel untouched.

The pointer bytes (1-2 of the program header = FIRSTKG, 1-2 of each keygroup =
NXTKG) are little-endian ADDRESSES within the program's own memory. In a file
they are relative to the start of the program: FIRSTKG = one block length,
keygroup i's NXTKG = the offset of keygroup i+1, the last one's = the end of the
file. That is what a real S1000 reported for a program at address 0
(`FIRSTKG` 0x96 = 150, last `NXTKG` 0x672 = 11 blocks) and what the disk format
describes. A sampler holds absolute addresses instead, so `build_file` always
rewrites them; `parse_file` hands them back as they are (a sampler repoints them
as keygroups are appended).

Layout source: https://lsnl.jp/~ohsaki/software/akaitools/S3000-format.html and
lakai.sourceforge.net's S1000/S2000 SysEx docs. NOT yet checked against a file
exported by another tool - see tests/akai_program_file_test_plan.md.
"""

from dataclasses import dataclass, field

from s3k import params as p

PROGRAM_IDENT = 1
KEYGROUP_IDENT = 2

#: block length -> file extension. The S1000 length is what a real unit sent
#: (2026-10-05 log); the spec only says "about 150".
BLOCK_SIZES = {150: ".p1", 192: ".p3"}
S1000_BLOCK_SIZE = 150
S3000_BLOCK_SIZE = 192

_GROUPS_OFFSET = 42
_MAX_KEYGROUPS = 99
_POINTER_OFFSET = 1


class ProgramFileError(ValueError):
    """The bytes are not a program file this app can use (message is user-facing)."""


@dataclass
class ProgramFile:
    block_size: int
    # repr hidden: the worker logs every queued job, and these are kilobytes
    program: bytes = field(repr=False)
    keygroups: list = field(default_factory=list, repr=False)

    @property
    def name(self):
        return program_name(self.program)

    @property
    def extension(self):
        return BLOCK_SIZES[self.block_size]

    @property
    def family(self):
        """'s1000' or 's2000_s3000' - which sampler family these blocks fit."""
        return "s1000" if self.block_size == S1000_BLOCK_SIZE else "s2000_s3000"


def program_name(program_block):
    """The program's name, trailing spaces stripped, from a raw program block."""
    param = p.lookup("PRNAME", "program")
    return str(p.decode_field(param, bytes(program_block[param.offset : param.offset + param.size]))).rstrip()


def _pointer(value):
    return bytes([value & 0xFF, (value >> 8) & 0xFF])


def validate_blocks(program, keygroups):
    """Raise ProgramFileError unless these blocks can make a program file; returns the block size."""
    block_size = len(program)
    if block_size not in BLOCK_SIZES:
        raise ProgramFileError(
            f"the sampler sent a {block_size}-byte program block; only "
            f"{', '.join(map(str, BLOCK_SIZES))}-byte blocks (S1000, S2000/S3000) are supported"
        )
    if program[0] != PROGRAM_IDENT:
        raise ProgramFileError(f"not a program block (identifier {program[0]}, expected {PROGRAM_IDENT})")
    groups = program[_GROUPS_OFFSET]
    if not 1 <= groups <= _MAX_KEYGROUPS:
        raise ProgramFileError(f"program claims {groups} keygroups (expected 1-{_MAX_KEYGROUPS})")
    if len(keygroups) != groups:
        raise ProgramFileError(f"program claims {groups} keygroups but {len(keygroups)} were read")
    for index, block in enumerate(keygroups):
        if len(block) != block_size:
            raise ProgramFileError(
                f"keygroup {index + 1} is {len(block)} bytes, the program block is {block_size}"
            )
        if block[0] != KEYGROUP_IDENT:
            raise ProgramFileError(
                f"keygroup {index + 1} has block identifier {block[0]}, expected {KEYGROUP_IDENT}"
            )
    return block_size


def build_file(program, keygroups):
    """Raw program block + keygroup blocks -> the bytes of a `.p1`/`.p3` file."""
    block_size = validate_blocks(program, keygroups)
    head = bytearray(program)
    head[_POINTER_OFFSET : _POINTER_OFFSET + 2] = _pointer(block_size)
    out = bytearray(head)
    for index, block in enumerate(keygroups):
        kg = bytearray(block)
        # offset of the NEXT block; for the last keygroup that is the end of the file
        kg[_POINTER_OFFSET : _POINTER_OFFSET + 2] = _pointer(block_size * (index + 2))
        out += kg
    return bytes(out)


def _guess_block_size(data):
    """150 or 192, from the file itself (FIRSTKG first, then the total length)."""
    if len(data) > _POINTER_OFFSET + 1:
        first = data[_POINTER_OFFSET] | (data[_POINTER_OFFSET + 1] << 8)
        if first in BLOCK_SIZES:
            return first
    if len(data) > _GROUPS_OFFSET:
        groups = data[_GROUPS_OFFSET]
        for size in BLOCK_SIZES:
            if len(data) == size * (groups + 1):
                return size
    raise ProgramFileError(
        "this doesn't look like an Akai S1000/S2000/S3000 program file "
        "(unrecognised header size)"
    )


def parse_file(data):
    """Bytes of a `.p1`/`.p3` file -> ProgramFile. Raises ProgramFileError with a user-facing message."""
    data = bytes(data)
    block_size = _guess_block_size(data)
    if len(data) < block_size:
        raise ProgramFileError("the file is shorter than one program header")
    program = data[:block_size]
    if program[0] != PROGRAM_IDENT:
        raise ProgramFileError(
            f"not a program file (first byte is {program[0]}, expected {PROGRAM_IDENT})"
        )
    groups = program[_GROUPS_OFFSET]
    if not 1 <= groups <= _MAX_KEYGROUPS:
        raise ProgramFileError(f"the file claims {groups} keygroups (expected 1-{_MAX_KEYGROUPS})")
    needed = block_size * (groups + 1)
    if len(data) < needed:
        raise ProgramFileError(
            f"the file is truncated: {groups} keygroups need {needed} bytes, it has {len(data)}"
        )
    # trailing bytes past the last keygroup are ignored (some tools pad)
    keygroups = [
        data[block_size * (i + 1) : block_size * (i + 2)] for i in range(groups)
    ]
    validate_blocks(program, keygroups)
    return ProgramFile(block_size=block_size, program=program, keygroups=keygroups)


def zone_sample_names(program_file):
    """Sample names the file's zones point at, in first-use order, empty (unused) zones skipped.

    Zones reference samples by NAME, so these are what has to be resident on a
    sampler for the program to make sound. S1000 keygroups have all four
    velocity zones too (S3000 offsets are a superset at the same positions).
    """
    names = []
    for block in program_file.keygroups:
        for zone in range(1, 5):
            param = p.lookup(f"SNAME{zone}", "keygroup")
            if param.end > len(block):
                continue
            name = str(p.decode_field(param, bytes(block[param.offset : param.end]))).rstrip()
            if name and name not in names:
                names.append(name)
    return names
