# Akai S2000/S3000XL multi file format - research notes

Written 2026-10-08, condensed 2026-10-10 while considering a "Save / Load Multi" feature. **No code exists for this**; it records what is
known, from where, and what is still a guess. Nothing here has been run against the owner's S2000.

## Summary

- A multi is a disk file of type `0xED` (`'m' + 0x80`), default name `MULTI FILE` (extracts as `MULTI FILE.M3`), **always 4096 bytes**:
  a 1024-byte multi header (`0x000-0x3FF`), then 16 part records of 192 bytes (`0x400 + n * 0xC0`). `1024 + 16 * 192 = 4096`.
- **A part record is a program header**, and its fields sit at the same offsets as the SysEx wire layout (`s3k.params`' `multipart`
  region), defaults included - checked against one real file. For the parts, file layout and wire layout are the same bytes.
- Only ~32 bytes of the header are understood (name, FX1-FX4, FX filename); the rest was zero in the one file seen.
- Programs are bound to parts by **name** in the file, but over SysEx a part's program name can be written without the part playing that
  program (measured by s3ked), so a SysEx load can't reproduce the binding. Only S2000 / S3000XL / S3200XL have multi mode.

## Sources

| Source | Multi content |
|---|---|
| Ohsaki "AKAI S3000 Series Disk and File Format", libgig `Akai.h` | None (samples and programs only). |
| [akaiutil](https://github.com/Midi-In/akaiutil) (GPL-2) | Type code and default name only. No layout. |
| [lentferj/s3ked](https://github.com/lentferj/s3ked) (`RESOLUTION_NOTES.md`) - our `s3k` dependency | The WIRE layout, measured on hardware: 32-byte header + 16 x 192-byte parts, offsets, ranges, gotchas. It does not know the file layout. |
| [pageorge/Akai-S3000-Floppy-Disk-Editor](https://github.com/pageorge/Akai-S3000-Floppy-Disk-Editor) | **The file layout**, reverse-engineered with hardware byte-diffs on an S3000XL, plus a floppy image containing a real multi. |

akaiutil is GPL-2 and pageorge's repo has no licence file (checked 2026-10-08): facts are recorded here, **no code or image bytes were
copied**. Re-derive from our own captures if bytes are needed.

## File layout

Floppy image directory entry: 24 bytes at `5 * 1024 + i * 24`: name (12, Akai charset), type at `+16` (`0xED` multi, `0xF0` S3000 program,
`0xF3` S3000 sample), size (3 bytes LE) at `+17`, first block (2 bytes) at `+20`; FAT at `0x600` (2 bytes per 1024-byte block, `>= 0xC000`
ends a chain). Hard-disk/CD images use 8192-byte blocks (use `akaiutil`).

**Multi header** (`0x000-0x3FF`): `0x003-0x00E` `MULTINAME` (12, Akai charset; **the firmware reads this, not the directory name**);
`0x010-0x013` `FX1`..`FX4` (wire offsets 16-19; effects/reverb indices, **0-204 only** - see Safety); `0x014-0x01F` `FXFILENAME` (12 zero bytes in
the file seen); `0x020-0x3FF` unknown, all zero in the one file seen.

**Part record** (192 bytes, base `0x400 + (N-1) * 0xC0`; "wire" = the `multipart` field in `s3k.params`):

| Offset | Wire | Meaning (value in the file seen; parts 2-16 are defaults) |
|---|---|---|
| `+0x00` | | `0x01` record marker (a program header's block id) |
| `+0x01-02` | | **program link pointer - sampler-assigned, internal** (part 1 `0C 90`, empty `F4 BF`) |
| `+0x03-0E` | `PRNAME` | program name, Akai charset (`TEST PROGRAM`, then blanks) |
| `+0x10` | `PMCHAN` | MIDI channel, 0-indexed, 255 = OMNI (part N = N-1) |
| `+0x11` | `POLYPH` | polyphony byte (`1F`) |
| `+0x12` | `PRIORT` | 0 low, 1 norm, 2 high, 3 hold (1) |
| `+0x13/14` | `PLAYLO/HI` | play range 21-127 (24 / 127) |
| `+0x16` | `OUTPUT` | individual output (255) |
| `+0x17` | `STEREO` | the panel's **Lev**, 0-99 (99) |
| `+0x18` | `PANPOS` | signed -50..50 (0) |
| `+0x46` | `VOSCL` | level to individual outputs 0-99 (50) |
| `+0x4B` | `TRANSPOSE` | signed semitones (0) |
| `+0x6D` | | **last note played - tracks playback, not a setting** |
| `+0x71` | `PFXCHAN` | 0 off, 1 FX1, 2 FX2, 3 RV3, 4 RV4 (0) |
| `+0x72` | `PFXSLEV` | effects send 0-99 (**25**) |
| `+0x73` | `PTUNOCM` | tune offset in cents, MULTI mode only (0) |
| `+0xBE-BF` | | **end link pointer**, `FF FF` = unassigned ("must be `FFFF` or the wrong multi loads" - pageorge) |

Every other byte is program-header content unused in multi mode: preserved, not modelled.

**What one real file confirmed (2026-10-08):** the floppy image shipped with pageorge's repo has exactly one multi (4096 bytes, one
program, two samples); the part layout holds; file and wire agree (`PFXSLEV = 25` on all parts, `FXFILENAME` zero); bytes that differ
between parts are only the pointers, name, channel and `+0x6D`. Limits: one file, made by that author, one assigned part, no non-default
settings - it says nothing about fully used multis, effects, or header bytes `0x20-0x3FF`.

## Wire facts for a save/load (s3k/s3ked, measured on hardware)

- `RMULTIDATA`/`MULTIDATA` (0x41/0x42): selector 0 = header (32 bytes known), selector 1 = part (item index = part 0-15).
  `BridgeWorker.submit_multi_parts` already reads parts this way.
- **Exactly 16 parts**: a write to part 16+ gets `REPLY` error 1, but a READ of part 16+ silently returns part 15's buffer. Never probe upward by reading.
- **Safety: `FX1`-`FX4` above 204 are dangerous.** Writing 205 crashed the machine ("Internal Error - divide overflow"), the byte stuck and the
  sampler stopped answering SysEx until F8 was pressed. A loader must clamp to 0-204 and never trust a file's FX bytes.
- `PRNAME` accepts a write but the part did not play that program; the working route is a MIDI Program Change on the part's channel (what the
  Multis tab does - `AGENTS.md`, "PRGNUM and Program Change"). `PMCHAN` stores 0-255 but the panel clamps (22 shows "Ch 16").
- pageorge's note: the firmware "always loads the first multi in the directory regardless of which file is selected" (unconfirmed by us).

## Implications for Save / Load Multi

1. **Save is straightforward and low risk:** read the header and 16 parts over SysEx (read-only), write the layout above, zero-fill header bytes
   `0x20-0x3FF`. It is unproven that a real sampler or Akai tool accepts the file; treat it as our format until someone loads one.
2. **Load over SysEx restores parameters, not (provably) the program binding:** write channel, level, pan, priority, play range, output,
   transpose, FX bus/send and tune with `FX1-FX4` clamped, **never the pointer bytes** (`+0x01-02`, `+0xBE-BF`) or `+0x6D`, then re-bind programs by
   Program Change on the saved program NAME. Model it on `core/akai_program_file.py`: pure codec, `BridgeWorker` submit/handler pairs, a
   confirmation listing programs that aren't resident, no samples or programs inside the file.
3. **Questions for the real S2000:** save a multi with distinctive values on every part, extract it and diff against a SysEx read (settles
   header `0x20-0x3FF`, the pointers, `+0x01`/`+0x6D`); does writing every part over SysEx reproduce the file byte for byte (ignoring pointers and
   `+0x6D`); is `FXFILENAME` ever non-zero; does a hand-built 4096-byte file load from disk.
