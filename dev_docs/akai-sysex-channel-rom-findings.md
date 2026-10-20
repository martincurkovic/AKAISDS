# How the Akai S900/S950 and S1000/S1100 number their SysEx channel, read from the ROMs

Written 2026-10-17. **Static reading of firmware images. Nothing was run on a unit.** Prompted by an S1100 tester whose Settings said Device ID 1 while the
sampler answered on 0 (the Program Editor then ignored the setting, now fixed). Question: should the app show 1-128 for Akai units, as the owner's S2000 does?

Images: S950 v1.2b, S900 v1.2a, S1000 v4.4 and S1100 v4.3, all 8086-family code (S1000: reset `EA` jump at the end of the image). Each ROM is two
files; build the image by taking each pair's byte from the file whose name ends `LSB` FIRST, then the `MSB` file's (the labels are the wrong way round for a
straight `msb, lsb` interleave: that gave "RAWDRA E ETTS" for "HARDWARE TEST"). Disassembled with capstone's 16-bit x86 mode; scratch scripts, not in the repo.

## S900 / S950: there is NO separate SysEx channel

- The panel has no "exclusive channel" page. The only channel setting is `Basic MIDI channel (01 to 16)` with `Omni on/off` (strings in both ROMs).
- The SysEx parser (S950 file offset 0x8de5) takes the byte after `47` and compares it with `[0x9e7b]` **as is**: `test [0x9e7b],0x80; jne accept; cmp al,ah`.
  `[0x9e7b]` is also what note messages are matched against (`0x88bf-0x88c9`: incoming status channel minus `[0x9e7b]`), so it is the Basic MIDI channel,
  stored 0-15, with bit 7 meaning Omni on (accept any).
- So for these two models **wire channel byte = Basic MIDI channel - 1**, and with Omni ON any value is accepted. The app masks the S950 channel with `& 0x0F`
  (`core/s950_sysex.py`), consistent with this. Not measured on a unit: that the panel's 01-16 is stored 0-15 (inferred from the note-channel compare).

## S1000 / S1100: raw byte on the wire; panel numbering NOT established

- Parser (S1000 v4.4, file offset 0xe24a onwards): `[0x65c5]` = manufacturer (`47`), then the device byte `48` (an `49` goes elsewhere), then at 0xed13 the
  received channel `[0x6638]` is compared with the stored byte `[0x6387]` **unchanged**; replies put `[0x6387]` back on the wire unchanged (0xf4c8). No +1 in the
  protocol. Function `0x15` copies the message's channel into `[0x6387]` without a check (looks like the "set channel"/identify path).
- The panel's "MIDI EXCLUSIVE" page (`channel:     (tran & rec)`, string at 0x4692) and the parameter-record table (0xf00-0x1100: `{max word, RAM address word}`
  records, field descriptors `{row, col, record ptr, 02 00, handler 0x8dc0}` before each page title) were found, but the handler that FORMATS a value
  (whether it prints raw or raw+1) was not located, and `[0x6387]`'s record (max 8, on the SCSI page) did not obviously belong to the MIDI EXCLUSIVE page.
  **Unresolved: whether an S1000/S1100 shows 0-127 or 1-128.** The tester's report fits either.
- Also unresolved: `exclusive channel:` at 0x4aa0 belongs to the DRUM UNIT CONTROL page (a DR-series drum unit), not the sampler's own.

## S3000 / S3200 v2.0 (read the same way, 2026-10-17)

Two 128 KB files (`HIGH`/`LOW`), 8086-family. Here the `LOW` file supplies the first byte of each pair (the opposite of the S1000 pair above).

- The SysEx state machine is the S1000's, moved: manufacturer `47` (file offset 0x17697), device byte `48` (0x18097), then the received channel `[0x752a]` is
  compared with the stored byte `[0x6d9a]` unchanged; function `0x15` copies the message's channel into it without a check. Raw 0-127 on the wire again.
- Parameter records are `{type word, RAM address word}`; a record that is a list is followed by its string pointers (the first points at a format
  header). The sysex channel record is `{0x08, 0x6d9a}` here and `{0x08, 0x6387}` on the S1000, and the transmit-test channel/note/velocity records are
  `{0x0a, 0x00, 0x07}` on both. On the S1000 the field descriptor `90 01 6a 0f` (record 0xf6a) comes right after the "MIDI EXCLUSIVE" labels, so it is that page's field.
- So both units file the SysEx channel under the SAME field type (8). The owner's S2000 shows 1-128 for it, which makes it **likely, not proven,** that
  an S1000/S1100 does too. The routine that turns a type-8 value into the digits on screen was not located in either ROM, so the +1 itself is unseen.
  (An earlier note above said the `{max 8, 0x6387}` record "did not obviously belong to the MIDI EXCLUSIVE page": it does, and the first word is a field
  TYPE, not a maximum.)

## What this means for the Device ID setting

- Don't assume 1-128 for every Akai (S900/S950 have no such setting): S900/S950 are 1-16 (Basic MIDI channel), S2000/S3000 are 1-128 (owner's S2000), S1000/S1100 are probably 1-128 like the S2000/S3000 (same field type, unproven).
- The stored/wire value stays 0-127 everywhere; any 1-based display is a presentation offset only.
