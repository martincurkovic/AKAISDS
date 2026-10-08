# Akai S2000/S3000XL multi file format - research notes

Written 2026-10-08 while considering a "Save / Load Multi" feature for the Multis tab. **No code was written from this**; it records what
is known, where it came from, and what is still a guess. Nothing here has been run against this project's own S2000 yet.

## TL;DR

- A multi is a disk file of type `0xED` (`'m' + 0x80`), default name `MULTI FILE` (extracts as `MULTI FILE.M3`), **always 4096 bytes**.
- Layout: **`0x000-0x3FF` multi header (1024 bytes), then 16 part records of `0xC0` (192) bytes each** (`0x400 + n * 0xC0`).
  `1024 + 16 * 192 = 4096` exactly.
- **A part record is a program header** and its fields sit at the same offsets as the SysEx wire layout (`s3k.params`' `multipart` region), defaults
  included. I checked this against a real file (below), so the file layout and the wire layout can be treated as the same bytes for the parts.
- Only the first ~32 bytes of the 1024-byte header are understood (name, FX1-FX4, FX filename); the rest was all zero in the one file seen.
- Programs are bound to parts by **name** inside the file, but over SysEx a part's program name can be written without making the part play
  that program (measured by s3ked, section 233) - see "Implications".
- Only newer machines have multi mode: s3k says S2000 / S3000XL / S3200XL ("on a plain S3000 there is no multi file"). akaiutil calls the type
  "S3000 multi file". The sample file below came from an S3000XL.

## Sources, and what each actually says

| Source | Multi content |
|---|---|
| [Ohsaki, "AKAI S3000 Series Disk and File Format"](https://lsnl.jp/~ohsaki/software/akaitools/S3000-format.html) | **None.** Samples and programs only; its file-type table has no multi. |
| [libgig `Akai.h`](https://download.linuxsampler.org/doc/libgig/api/Akai_8h_source.html) | **None.** Sample/program/keygroup structs only. |
| [akaiutil](https://github.com/Midi-In/akaiutil) (GPL-2) | Type code and default name only (`AKAI_MULTI3000_FTYPE`, `"MULTI FILE  "`); the file is treated through the generic file header (name at offset 3). No layout. |
| [lentferj/s3ked](https://github.com/lentferj/s3ked) (`docs/RESOLUTION_NOTES.md`, `TODO.md`) - our `s3k` dependency | The **wire** layout, measured on hardware: 32-byte header + 16 parts of 192 bytes, field offsets, ranges, and several hardware gotchas. It says outright it does **not** know the *file* layout (TODO "RE item 3"), and lists 64+21x192, 256+20x192 and 1024+16x192 as the candidates that divide 4096. |
| [pageorge/Akai-S3000-Floppy-Disk-Editor](https://github.com/pageorge/Akai-S3000-Floppy-Disk-Editor) (README "Multi file (type ed)", `AkaiDiskImage.swift`) | **The file layout**, reverse-engineered with isolated hardware byte-diff captures on an S3000XL: header `0x000-0x3FF`, 16 x `0xC0` parts, the confirmed part offsets (table below), and the fact that part 2 landed at exactly `0x4C0`. It also ships a floppy image containing a real multi. |

Licensing: akaiutil is GPL-2, and no licence file was found in pageorge's repository (checked 2026-10-08). **Facts from them are recorded here; no
code or image bytes were copied into this repo.** Don't copy from either - re-derive from our own hardware captures if bytes are needed.

## Directory entry and file size

The floppy image checked (1024-byte blocks, 1600 blocks) has a 24-byte directory entry at `5 * 1024 + i * 24`: name (12 bytes, Akai charset) at 0, type
at `+16` (`0xED` multi, `0xF0` S3000 program, `0xF3` S3000 sample), size (3 bytes, little endian) at `+17`, first block (2 bytes) at `+20`; the FAT is at
`0x600` (2 bytes per block, `>= 0xC000` ends a chain). Hard-disk / CD images use 8192-byte blocks and a different layout (akaiutil handles those; it
did not recognise the floppy image: "possibly floppy format in partition A, ignored"). A multi file's size field is 4096.

Akai charset: `0-9` = digits, `10` = space, `11-36` = `A-Z`, `37` = `#`, `38` = `+`, `39` = `-`, `40` = `.`.

## File layout

```
0x000-0x3FF   multi header (1024 bytes)
0x400-0x4BF   part 1      (192 bytes)
0x4C0-0x57F   part 2
  ...         parts 3-16, each 0xC0 further on
0xF40-0xFFF   part 16
```

### Multi header

| Offset | Size | Field | Notes |
|---|---|---|---|
| `0x000-0x002` | 3 | `00 00 00` | |
| `0x003-0x00E` | 12 | `MULTINAME` | Akai charset. **The firmware reads this, not the directory entry's name** (pageorge). |
| `0x010-0x013` | 4 | `FX1`..`FX4` | Wire offsets 16-19 (s3k). Effects setup / reverb indices, **0-204 only** (see the safety note). Zero in the file seen. Not documented by pageorge. |
| `0x014-0x01F` | 12 | `FXFILENAME` | Wire offset 20. Twelve zero bytes, which in Akai charset reads as `000000000000` - the same thing s3ked read over the wire on all 170 multis it knows. |
| `0x020-0x3FF` | 992 | unknown | All zero in the file seen. pageorge: "unknown, preserved". |

### Part record (192 bytes, base = `0x400 + (N-1) * 0xC0`, N = 1..16)

A part is a program header. Offsets are relative to the part's base. "Wire" is the `multipart` field name in `s3k.params` at the same offset.

| Offset | Size | Wire name | Meaning | Value in the file seen (parts 2-16 are defaults) |
|---|---|---|---|---|
| `+0x00` | 1 | | `0x01` record marker (a program header's block id) | `01` |
| `+0x01-02` | 2 | | **Program link pointer - sampler-assigned, internal** | part 1 `0C 90`, empty parts `F4 BF` |
| `+0x03-0E` | 12 | `PRNAME` | Program name, Akai charset | `TEST PROGRAM`, then blanks |
| `+0x0F` | 1 | | padding | 0 |
| `+0x10` | 1 | `PMCHAN` | MIDI channel, 0-indexed (255 = OMNI) | part N = N-1 |
| `+0x11` | 1 | (`POLYPH`) | program-header polyphony byte | `1F` in every part |
| `+0x12` | 1 | `PRIORT` | 0 low, 1 norm, 2 high, 3 hold | 1 |
| `+0x13` | 1 | `PLAYLO` | lowest note played (21-127) | 24 |
| `+0x14` | 1 | `PLAYHI` | highest note played | 127 |
| `+0x16` | 1 | `OUTPUT` | individual output routing | 255 |
| `+0x17` | 1 | `STEREO` | the panel calls it **Lev**, 0-99 | 99 |
| `+0x18` | 1 | `PANPOS` | signed, -50..50 | 0 |
| `+0x46` | 1 | `VOSCL` | level to individual outputs, 0-99 | 50 |
| `+0x4B` | 1 | `TRANSPOSE` | signed semitones | 0 |
| `+0x6D` | 1 | | **last note played - tracks playback** (s3ked saw offset 109 change while playing); don't treat as a setting | 60 in part 1, 0 elsewhere |
| `+0x71` | 1 | `PFXCHAN` | 0 off, 1 FX1, 2 FX2, 3 RV3, 4 RV4 | 0 |
| `+0x72` | 1 | `PFXSLEV` | effects send 0-99 | **25** |
| `+0x73` | 1 | `PTUNOCM` | tune offset in cents, MULTI mode only | 0 |
| `+0xBE-BF` | 2 | | **End link pointer** - `FF FF` = unassigned. pageorge: it "must be `FFFF` or the wrong multi loads" | part 1 `00 90`, others `FF FF` |

Every other byte of a part is program-header content that is not used in multi mode; it is preserved, not modelled (88 bytes at `+0x19-0x70` etc.).

## What I verified myself (2026-10-08)

I parsed the floppy image shipped with pageorge's repo (`akai_s3000.img`, 1,638,400 bytes) with a small script, outside this repo:

- 5 directory entries, exactly one multi (`MULTI FILE`, type `0xED`, **4096 bytes**, one program `TEST PROGRAM`, two samples).
- The part layout above holds: names at `+3`, channel at `+0x10` counting up 0..15, level/pan/priority/play range/output/FX bus/FX send where listed.
- **File and wire agree**: the file's defaults match what s3ked measured over SysEx - `PFXSLEV = 25` on all 16 parts (s3ked section 214, "incidental"), `FXFILENAME` all zero. (`FX1` was 33 in s3ked's own multi but 0 in this file, so the FX bytes do vary between multis.) Every `multipart` offset in `s3k.params` points at the same value in the file.
- Bytes that differ between parts: the link pointers (`+0x01-02`, `+0xBE-BF`), the name, the channel, and `+0x6D` (last note) - nothing else.
- Header: only the name (`MULTI FILE  `) was non-zero, so the 992 "unknown" bytes are unconfirmed rather than known-empty in general.

Limits of that evidence: it is **one file**, made by the repo's author, with a single assigned part and no non-default settings. It does not
show how real multis with all 16 parts in use, non-default routing, or effects look, nor what lives in header bytes `0x20-0x3FF`.

## Wire facts that matter for a save/load (from s3k / s3ked, measured on hardware)

- Multi data is read/written with `RMULTIDATA` / `MULTIDATA` (`0x41` / `0x42`): selector `0` = multi header (32 bytes known), selector `1` = part
  (item index = part number 0-15); byte offset and count are in the message. `BridgeWorker.submit_multi_parts` already reads parts this way.
- **There are exactly 16 parts**, established by writes: parts 16+ refuse a write with `REPLY` error 1, while a *read* of part 16+ silently returns part 15's
  buffer (plausible fiction). Never probe by reading upward.
- **Safety: `FX1`-`FX4` above 204 are dangerous.** Writing 205 crashed the machine to "Internal Error - divide overflow", stored the byte anyway and
  stopped answering SysEx; recovery was an F8 press. A loader must clamp to 0-204 and must not trust a file's FX bytes.
- `PRNAME` accepts a write (a resident program's name read back byte-exact), **but the part did not play that program** (s3ked section 233: part stayed
  at the noise floor). The documented route is a MIDI Program Change on the part's own channel - which is what this app already does (see AGENTS.md,
  "PRGNUM and Program Change"). Whether a *disk load* resolves names to programs is firmware behaviour we can't reproduce over SysEx.
- `PMCHAN` stores any value 0-255 but the panel clamps in use (22 shows as "Ch 16").
- pageorge's hardware note: the firmware "always loads the first multi in the directory regardless of which file is selected" (their observation,
  unconfirmed by us).

## Implications for a Save / Load Multi feature

1. **Save is straightforward and low risk**: read the 32-byte header and 16 x 192-byte parts over SysEx (all read-only operations) and write them in the
   layout above, zero-filling header bytes `0x20-0x3FF` (the only value seen). That gives a 4096-byte file that, on the evidence, matches what the
   machine writes - but **it is unproven that a real sampler or Akai tool accepts it**; treat it as our format until someone loads one.
2. **Load over SysEx can restore the parameters but not (provably) the program binding**: write channel, level, pan, priority, play range, output,
   transpose, FX bus/send, tune, with `FX1-FX4` clamped to 0-204, and **never write the pointer bytes** (`+0x01-02`, `+0xBE-BF`) or `+0x6D`
   (same rule as the `.p1`/`.p3` program files in AGENTS.md: addresses the sampler assigns are not ours to send). Then re-bind programs the way
   the Multis tab already does (Program Change by `PRGNUM`), keyed by the saved program *name*.
3. The existing Program Save/Load (`core/akai_program_file.py`) is the model to copy: pure codec, `BridgeWorker` submit/handler pairs, a
   confirmation that lists programs that are not resident, no samples/programs inside the file.
4. **Open questions for the real hardware (S2000):** (a) save a multi with distinctive values on every part to disk, extract it, and diff it against a SysEx
   read of the same multi - this settles header bytes `0x20-0x3FF`, the pointer bytes and `+0x01` / `+0x6D` behaviour; (b) does writing every part over SysEx
   reproduce that file byte for byte (ignoring pointers and `+0x6D`)?; (c) is `FXFILENAME` ever non-zero; (d) does a hand-built 4096-byte file load from disk.

## How to pull a multi out of a floppy image

Floppy `.img` files (1024-byte blocks) are not read by akaiutil here. The recipe above is enough for a few lines of Python: read directory entries
from `5 * 1024` (24 bytes each, 510 slots; type `0xED`), then follow the FAT at `0x600` from the entry's first block, taking 1024 bytes per block until
the size field is reached. Hard-disk / CD images: use `akaiutil` (`ls`, then `get`) - its types and names are the same.
