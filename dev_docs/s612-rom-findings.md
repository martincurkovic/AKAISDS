# Akai S612 SysEx, read from the ROM

Written 2026-10-16. **Static reading of one firmware image. Nothing was run, no S612 was tried, nothing here is built.** Every statement is
"what the code does", not a hardware measurement. Roles given to the letters below are my reading of the code, not names from an Akai document.

## What was read

`612AKI11.OBJ` (S612 v1.1, 8192 bytes, raw; the string "COPYRIGHT AKAI DENKI JAN 1985"). **Z80** code. Addresses are file offsets; the ROM looks
mapped at 0x0000 with RAM at 0x8000 up. Disassembled with the `z80dis` package and followed by hand (scripts were scratch, not in the repo).
The S700 note is `s700-rom-findings.md`. The S612 shares **almost nothing** with the S900/S950/S700 dialect.

## Wire format

```
F0 47 <ch> <func> 20 <kind> [data...] F7
```

- Receive state machine at 0x0E7B: `47`, then the channel (matched against the unit's own, or accepted when it is set to omni), then the
  function, then **`20`** (checked), then the kind, then data until F7 (the F7 handler is at 0x0D70).
- **`func`** is an ASCII letter: `40 @`, `41 A`, `42 B`, `44 D`, `45 E`, `4D M` are accepted; the unit also sends `4E N`.
- **`kind`** is `21 !` (sample data) or `41 A` (parameters). Nothing else is accepted.
- **No checksum** on send or receive that I could find. The only feedback is the unit's `M` / `N`.
- Parameter bytes travel as two wire bytes (low 7 bits, then bit 7), like the S900.
- **Sample words:** two 7-bit bytes per 12-bit word. The sample RAM is two planes (an 8-bit byte plane in memory and a 4-bit plane read through an I/O
  port at the same addresses). Transmit routine at 0x0E37: first wire byte = `(byte bit 0) << 6 | nibble << 2`, second = `byte >> 1`; so
  word = `nibble << 8 | (second << 1 | bit 6 of first)`. **Inferred from the instructions; the offset/sign convention of the 12 bits is not known.**

## The conversation (as the code reads)

| Direction | Message | Effect |
|---|---|---|
| host to unit | `A 20 !` | the unit sets its page pointer to 0x8200 and sends the first page at once |
| unit to host | `D 20 !` + 256 words | one page: 6 header bytes, 256 words as 512 wire bytes, F7 |
| host to unit | `B 20 !` | next page; when the pointer has wrapped the unit sends `E 20 !` instead (the end) |
| host to unit | `A 20 A` | the unit replies `D 20 A` + the 32-byte parameter block (64 wire bytes) |
| host to unit | `@ 20 ! <page>` | sets the page the next data goes to (page byte below 0x7E, address = page + 0x82 in the high byte); the unit answers `M 20 !`, or `N 20 !` if it refused |
| host to unit | `D 20 !` + data | writes a page; `M 20 !` when it is accepted (without a preceding `@` it lands on the first page and is not acknowledged) |
| host to unit | `D 20 A` + 32 bytes | writes the parameter block (applied when the 32nd byte arrives) |
| host to unit | `E 20 !` or `E 20 A` | answered `M 20 !` / `M 20 A` |

The unit always walks pages 0x82 to 0xFF: **126 pages of 256 words, 32,256 words**, which looks like the whole sample memory. No length is sent,
so the receiver must trim, and the sample rate has to come from the parameter block or from the user.

## Parameter block

32 bytes, copied to and from RAM through a table of addresses at 0x1AC4. About 20 of them are distinct (RAM 0x802C-0x802F, 0x80AA, 0x80AE,
0x80AF, 0x808D-0x8090, 0x8022, 0x809A, 0x8026, 0x80B1, 0x80B2, 0x809B, 0x8080, 0x8081, 0x80AB-0x80AD, 0x8048, 0x8049); the rest point at one dummy
byte (0x8083) as padding. **What each one is (rate, length, loop, tune, ...) is not in the ROM.** The S612 MIDI spec or a capture would say.

## Features that AKAISDS could offer (nothing is built)

There is very little to offer, because the protocol only moves whole memory and a parameter block:
1. **Receive the sample.** `A 20 !`, then `B 20 !` until `E`; decode to a 12-bit WAV and open it in the Slice Editor to trim (the length is not sent).
2. **Send a sample.** Convert a WAV to 12-bit, then `@` plus `D` page by page, waiting for each `M`. One sample, replacing what is in memory.
3. **Read and write the 32-byte parameter block.** As raw bytes (with a backup first) until the spec names the fields; named controls after that.

Not possible with this protocol: a list (there is one sample), names, delete, programs or a program editor, a memory bar, sample numbers. A new
protocol family would be needed, with its own engine (like `s950`), and every send would be experimental.

## Open

- The 12-bit value convention, the field meanings, the sample rate and length (which parameter holds them).
- Whether the unit handles a missing `@`, a short page or an `N` the way this reading says.
- All of it unverified on a real S612.
