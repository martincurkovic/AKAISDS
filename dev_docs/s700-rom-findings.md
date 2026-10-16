# Akai S700 SysEx, read from the ROM

Written 2026-10-16. **Static reading of two firmware images. Nothing was run, no S700 was tried, nothing here is built.** Every statement is
"what the code does", not a hardware measurement. Names for fields are guesses unless said otherwise.

## What was read

| Image | Notes |
|---|---|
| `Akai_S700_2.1.BIN` | v2.1, 16384 bytes, raw. |
| `Akai-S700_V1_6_128.BIN` | v1.6, despite the name an Intel HEX text file (convert it first). The result is the same 16 KB image. |

The CPU is a **Z80** (the S900/S950 are 8086-family). Addresses below are file offsets; the ROM looks mapped at 0x0000 and RAM references are
0x5xxx-0x6xxx. Disassembled with the `z80dis` package, following the MIDI parser by hand. The scripts were scratch and are not in the repo.

**v1.6 and v2.1 have the same SysEx implementation**: the state machine, function table, device byte and checks sit at the same addresses.
About 930 of 16384 bytes differ in 20 places; only two fall in the SysEx code (0x24C1 and 0x2702, a call to a helper at 0x3FF8 in v2.1 against a direct
store to 0x5703 in v1.6, in the dump routines). The other 18 places were not read.

## The message set

Parser: a MIDI ring buffer, then a state machine whose next-state pointer lives at RAM 0x6028 (entry 0x21B1). After `F0`:

```
47  -> channel (< 0x10, checked against the unit's own by the call at 0x1389; kept at 0x6084)
    -> function (kept at 0x6030)
    -> 0x41                         device byte (the S900/S950 use 0x40)
    -> number (kept at 0x60aa)      0 is always allowed; otherwise it must be < [0x6c22] AND the function must be 4 or 10
    -> 0x00
    -> handler for the function
```

A function that takes no sample (1, 5, 6, 8) therefore needs number 0. The handler table is at 0x2252 (functions) and 0x2258 (handlers):

| Func | Handler | Meaning (S900 names) |
|---|---|---|
| 1 | 0x2611 | ROVS, request overall settings |
| 4 | 0x27ED | RSPRM, request sample parameters |
| 5 | 0x2264 | SECRE, enable the `F0 7E` family (flag at 0x60BE) |
| 6 | 0x226F | SECRD, disable it |
| 8 | 0x2319 | OVS, overall settings data |
| 10 | 0x257C | SPRM, sample parameters data |

**No other function is accepted.** There is no catalog, no program, no drum settings, no delete, no memory status and none of the S950's
undocumented functions 12-14 (peek, poke, call).

Shared with the S900/S950 ROMs: every byte goes out as two (low 7 bits, then bit 7); the checksum is the XOR of the payload wire bytes (header excluded);
the sample dump is `F0 7E 00 nn 00 F7` (request) and `F0 7E 01 nn 00 ...` (dump, no channel byte); handshakes are the 4-byte `F0 7E 7F|7E|7D F7`
(waited for at 0x22AD); the dump header accepts 8 to 14 bits per word (check at 0x23B3).

**Difference from the S950 ROM:** the S700 puts the number in the reply header (`F0 47 <ch> <func> 41 <num> 00`, builder at 0x25EB, channel
from RAM 0x60BD). The S950 1.2b ROM never writes it. An out-of-range sample number on a dump is not refused: it falls back to RAM 0x6025.

## Sizes (data bytes; double them for the wire)

- **Overall settings (OVS/ROVS): 12.** Eight values, then four zero bytes. The ROM stores them at RAM 0x605E, 0x6BE6, 0x60BD, 0x6C14, 0x6C18,
  0x60C5, 0x601B, 0x6000. Only 0x60BD is certain: it is the SysEx channel (it fills the reply header). 0x605E is compared with the channel of incoming
  MIDI messages, so it is probably the basic channel. The rest are unnamed.
- **Sample parameters (SPRM/RSPRM): 155.** 32 groups of 4 bytes (read from a table at 0x60DD + 4 x sample, stride 0x40), then 27 bytes from a
  0x30-byte per-sample record at 0x68B1 + 0x30 x sample. Which bytes are the name, rate, loop and so on is **not** known from the ROM; the
  Akai S700/X7000 MIDI spec would say.
- Reply to a dump request: header `F0 7E 01 <num> 00`, then the usual words (builder at 0x268B).

## What could be built (nothing is)

An `s700` family would reuse the S950 engine's wire handling with device byte `41` and these differences:
1. **Receive and send samples** (same dump as the S950, 12-bit words).
2. **Read and write sample parameters** as one 155-byte block, patching only changed bytes (needs the field layout).
3. **Overall settings** (12 bytes; needs the field meanings).

Limits: no catalog, so listing means one RSPRM per number (and the ROM does not show whether a name is in them); the sample count is a RAM
value (0x6C22) the ROM does not state; no delete.

## Open

- Field layouts of both blocks. Sample count. Whether SPRM holds a name.
- All of it unverified on a real S700 or X7000 (probably the same firmware, unchecked). Anything built from this is a guess until a unit answers.
