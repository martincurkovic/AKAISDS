"""Akai S1000 / S2000 / S3000 sample files (`.s1` / `.s3`) <-> the raw header block and audio words the sampler sends over SysEx.

A sample file is the sample header followed by the audio, with no other framing:

    S2000/S3000:  192-byte sample header + SLNGTH x 16-bit little-endian words   (`.s3`)
    S1000:        150-byte sample header + SLNGTH x 16-bit little-endian words   (`.s1`)

SLNGTH is the 4-byte value at header offset 26. The header is exactly the block `get_header_bytes("sample", ...)` returns, or the nibbled one an
Akai-mode receive gets in the SDATA reply, so every field - the name, rate, root note, tuning, start/end, all four loops with their dwell times, and the bytes
this app does not model - travels untouched: saving is "keep the header block, write the audio after it".

Two header fields are addresses the SAMPLER assigns, and a file has no use for them:

    SLOCAT  (offset 22, 4 bytes)  the sample's absolute location in wave memory
    SSPAIR  (offset 136, 2 bytes) the block address of the other half of a stereo pair

`build_file` writes both as zero ("unassigned"), which is also what this app's own SDATA send puts there (`akai_sysex.build_sample_header_block`) and the sampler
accepts. NOT YET CHECKED against a file the sampler itself wrote: `tools/s2000_sample_check.py` compares our header with one exported by akaiutil, read-only, and
says whether a real file has other values there, whether the audio is padded to a block size, and what else differs.

Layout source: https://lsnl.jp/~ohsaki/software/akaitools/S3000-format.html (the same document the program-file code cites) and `s3k.params`'
`sample` region. The S1000 layout is the same field for field at the same offsets up to 140 (it is a shorter block).
"""

from array import array
from dataclasses import dataclass, field
import sys

from s3k import params as p

S3000_HEADER_SIZE = 192
S1000_HEADER_SIZE = 150
#: header length -> file extension
HEADER_SIZES = {S3000_HEADER_SIZE: ".s3", S1000_HEADER_SIZE: ".s1"}

_NAME = p.lookup("SHNAME", "sample")
_RATE = p.lookup("SSRATE", "sample")
SLOCAT = (22, 26)  # [start, end)
SSPAIR = (136, 138)
SLNGTH = (26, 30)
SSTART = (30, 34)
SMPEND = (34, 38)
LOOPAT1 = (38, 42)
LLNGTH1 = (42, 48)  # 32.16 fixed point: raw = frames * 65536 + fraction
#: header bytes a comparison with another source ignores: sampler-assigned addresses
IGNORED_OFFSETS = frozenset({*range(*SLOCAT), *range(*SSPAIR)})

_LOOP_LENGTH_SCALE = 65536


class SampleFileError(ValueError):
    """The bytes are not a sample file this app can use (message is user-facing)."""


def _get(block, span):
    return int.from_bytes(block[span[0] : span[1]], "little")


def _put(block, span, value):
    block[span[0] : span[1]] = int(value).to_bytes(span[1] - span[0], "little")


@dataclass
class SampleFile:
    header: bytes = field(repr=False)
    samples: list = field(default_factory=list, repr=False)  # signed 16-bit ints

    @property
    def block_size(self):
        return len(self.header)

    @property
    def extension(self):
        return HEADER_SIZES[self.block_size]

    @property
    def family(self):
        """'s1000' or 's2000_s3000' - which sampler family this header block fits."""
        return "s1000" if self.block_size == S1000_HEADER_SIZE else "s2000_s3000"

    @property
    def name(self):
        return str(p.decode_field(_NAME, bytes(self.header[_NAME.offset : _NAME.offset + _NAME.size]))).rstrip()

    @property
    def sample_rate(self):
        return _get(self.header, (_RATE.offset, _RATE.offset + _RATE.size))

    @property
    def length(self):
        """SLNGTH, in words."""
        return _get(self.header, SLNGTH)


def validate_header(header):
    """Raise SampleFileError unless `header` can head a sample file."""
    if len(header) not in HEADER_SIZES:
        raise SampleFileError(
            f"the sampler sent a {len(header)}-byte sample header; only {', '.join(map(str, HEADER_SIZES))}-byte headers (S2000/S3000, S1000) are supported"
        )


def normalized_header(header):
    """The header as `build_file` writes it: the sampler-assigned addresses zeroed, everything else as read."""
    validate_header(header)
    block = bytearray(header)
    block[SLOCAT[0] : SLOCAT[1]] = bytes(SLOCAT[1] - SLOCAT[0])
    block[SSPAIR[0] : SSPAIR[1]] = bytes(SSPAIR[1] - SSPAIR[0])
    return bytes(block)


def with_markers(header, frames, start, end, loop_start, loop_end):
    """`header` with the length and markers of `frames` words of audio filled in (for audio that is no longer what the header describes: an edited working
    copy). SLNGTH, SSTART, SMPEND, LOOPAT1 (the loop's END) and LLNGTH1 (32.16 fixed point, measured backwards from LOOPAT1) are set; every other byte stays."""
    validate_header(header)
    block = bytearray(header)
    _put(block, SLNGTH, frames)
    _put(block, SSTART, start)
    _put(block, SMPEND, end)
    _put(block, LOOPAT1, loop_end)
    _put(block, LLNGTH1, max(loop_end - loop_start, 0) * _LOOP_LENGTH_SCALE)
    return bytes(block)


def _words_to_bytes(samples):
    words = array("h", samples)
    if sys.byteorder == "big":
        words.byteswap()
    return words.tobytes()


def _bytes_to_words(data):
    words = array("h")
    words.frombytes(data)
    if sys.byteorder == "big":
        words.byteswap()
    return list(words)


def build_file(header, samples):
    """The raw sample header block and the audio (signed 16-bit ints, as many as the header's SLNGTH says) -> the bytes of a `.s1`/`.s3` file."""
    validate_header(header)
    block = normalized_header(header)
    if len(samples) != _get(block, SLNGTH):
        raise SampleFileError(
            f"the header says {_get(block, SLNGTH)} words but {len(samples)} were received, so this isn't a complete sample"
        )
    try:
        audio = _words_to_bytes(samples)
    except (OverflowError, TypeError) as e:
        raise SampleFileError(f"the audio isn't 16-bit: {e}") from e
    return block + audio


def _guess_header_size(data, allow_padding):
    """192 or 150, from the file itself. SLNGTH sits at the same offset in both, so the audio's length decides: the header for which the file is EXACTLY
    header + 2 x SLNGTH. With `allow_padding` a file longer than that (padded to a block size?) is accepted too, the larger header preferred; otherwise an
    inexact length is refused, which also catches a truncated file."""
    candidates = [size for size in HEADER_SIZES if len(data) >= size and _get(data[:size], SLNGTH) > 0]
    for size in candidates:
        if len(data) == size + 2 * _get(data[:size], SLNGTH):
            return size
    if allow_padding:
        for size in candidates:
            if len(data) - size >= 2 * _get(data[:size], SLNGTH):
                return size
    raise SampleFileError(
        "this doesn't look like an Akai S1000/S2000/S3000 sample file (its length doesn't match the sample length in its header)"
    )


def parse_file(data, allow_padding=False):
    """The bytes of a `.s1`/`.s3` file -> SampleFile (the header exactly as the file holds it). Strict about the length unless `allow_padding` (bytes after
    the audio are then ignored); whether real files are padded is what `tools/s2000_sample_check.py` is for."""
    data = bytes(data)
    size = _guess_header_size(data, allow_padding)
    header = data[:size]
    length = _get(header, SLNGTH)
    return SampleFile(header=header, samples=_bytes_to_words(data[size : size + 2 * length]))


# --- loading a file back (what is written to the sampler after the audio has been sent) --------------------------------------

LOOP_COUNT = 4
SAMPLE_RATE_VALID = 0x80  # SSRVLD: 128 = the rate field is valid
#: SBANDW -> the rate the sampler's engine runs that bucket at (core/akai_sysex._NATIVE_RATES); the fallback for a header with no valid SSRATE
_NATIVE_RATES = {0: 22050, 1: 44100}
_SPTYPES = range(0, 4)  # normal looping, loop until release, no looping, play to sample end
_PITCHES = range(21, 128)
_LOOP_TUNES = range(-50, 51)


def field_value(header, name):
    """The decoded value of sample-header field `name` (the same decoding the editor's own reads use: STUNO and the loop lengths come back raw)."""
    prm = p.lookup(name, "sample")
    if len(header) < prm.end:
        raise SampleFileError(f"this header is too short to have {name}")
    return p.decode_field(prm, bytes(header[prm.offset : prm.end]))


def sample_param(name):
    """The `s3k.params` entry of a sample-header field (so a caller can ask whether a sampler's block holds it)."""
    return p.lookup(name, "sample")


def playback_rate(header):
    """The sample's rate: SSRATE when the header says it is valid, else what its SBANDW bucket runs at (a header without a rate)."""
    rate = int(field_value(header, "SSRATE"))
    if field_value(header, "SSRVLD") & SAMPLE_RATE_VALID and rate > 0:
        return rate
    return _NATIVE_RATES.get(int(field_value(header, "SBANDW")), 44100)


@dataclass
class RestorePlan:
    """What loading a sample file does AFTER the audio has been sent as an ordinary new sample (the sampler resets the header when audio arrives, so the
    fields are written one by one; the file's header block is never sent as a whole)."""

    rate: int
    sptype: int
    spitch: int
    shlto: int
    stuno: int  # raw, as the editor's cache holds it
    sbandw: int
    start: int
    end: int
    loop_start: int  # of loop 1, in whole frames (the fraction is kept in `writes`)
    loop_end: int
    #: (field name, value) in the order to write them; STUNO is left out because it has to be recomputed for the bandwidth bucket the sampler gives the new sample
    writes: list = field(default_factory=list)
    #: what was left out or changed, in words
    notes: list = field(default_factory=list)


def restore_plan(header, frames):
    """Work out what to write back for a file whose audio is `frames` words long. Only fields the S1000's 141-byte block also has are ever planned, and
    anything outside what the sampler could use (a start past the end, a loop outside the sample, a root note off the keyboard) is left at the sampler's
    default with a note rather than written."""
    validate_header(header)
    notes = []
    value = lambda name: int(field_value(header, name))  # noqa: E731
    last = max(frames - 1, 0)

    def checked(name, allowed):
        v = field_value(header, name)
        if v in allowed:
            return v
        notes.append(f"{name} {v} is out of range and was left at the sampler's default")
        return None

    sptype = checked("SPTYPE", _SPTYPES)
    spitch = checked("SPITCH", _PITCHES)
    shlto = checked("SHLTO", _LOOP_TUNES)
    start, end = value("SSTART"), value("SMPEND")
    if not 0 <= start <= end <= last:
        notes.append(f"start {start} / end {end} don't fit a sample of {frames} words; the whole sample is used")
        start, end = 0, last

    writes = []
    for name, v in (("SPTYPE", sptype), ("SPITCH", spitch), ("SHLTO", shlto)):
        if v is not None:
            writes.append((name, v))
    writes += [("SSTART", start), ("SMPEND", end)]

    loop_start = loop_end = 0
    for n in range(1, LOOP_COUNT + 1):
        at, length, dwell = value(f"LOOPAT{n}"), value(f"LLNGTH{n}"), value(f"LDWELL{n}")
        fits = 0 <= at <= last and (length >> 16) <= at
        if n == 1:
            # loop 1 is always written (its own fields are what the editor shows); an unusable one becomes "no loop, as a fresh sample has it"
            if not fits:
                notes.append(f"loop 1 (end {at}, length {length / 65536:g}) doesn't fit the sample and was cleared")
                at, length, dwell = frames, 0, 0
            writes += [("LOOPAT1", at), ("LLNGTH1", length), ("LDWELL1", dwell)]
            loop_end, loop_start = min(at, last), max(min(at, last) - (length >> 16), 0)
        elif dwell != 0 and length > 0:
            if fits:
                writes += [(f"LOOPAT{n}", at), (f"LLNGTH{n}", length), (f"LDWELL{n}", dwell)]
            else:
                notes.append(f"loop {n} doesn't fit the sample and was left out")
    return RestorePlan(
        rate=playback_rate(header),
        sptype=sptype if sptype is not None else 2,
        spitch=spitch if spitch is not None else 60,
        shlto=shlto if shlto is not None else 0,
        stuno=value("STUNO"),
        sbandw=value("SBANDW"),
        start=start,
        end=end,
        loop_start=loop_start,
        loop_end=loop_end,
        writes=writes,
        notes=notes,
    )


def safe_file_stem(name):
    """A file name stem from a sample name (Akai names allow characters some file systems don't)."""
    stem = "".join(c if c.isalnum() or c in "-_ #+." else "_" for c in name).strip()
    return stem or "SAMPLE"


# --- comparing two sample headers (the read-only check tool) --------------------------------------------------------------


def _offset_names():
    names = {}
    for prm in p.region_params("sample"):
        for offset in range(prm.offset, prm.offset + prm.size):
            names[offset] = prm.name
    return names


def describe_offset(offset):
    """'22 SLOCAT' for a modelled byte, '147 (not modelled)' otherwise."""
    name = _offset_names().get(offset)
    return f"{offset} {name}" if name else f"{offset} (not modelled)"


def compare_headers(a, b):
    """Differences between two header blocks as readable lines; an empty list means identical apart from the sampler-assigned addresses.

    Compares only the bytes both blocks have; a length difference is itself reported."""
    lines = []
    if len(a) != len(b):
        lines.append(f"header lengths differ: {len(a)} vs {len(b)}")
    for offset in range(min(len(a), len(b))):
        if offset in IGNORED_OFFSETS:
            continue
        if a[offset] != b[offset]:
            lines.append(f"{describe_offset(offset)}: {a[offset]} vs {b[offset]}")
    return lines
