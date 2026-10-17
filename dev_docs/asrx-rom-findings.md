# Ensoniq ASR-X SysEx, read from the v2.67 ROM

Written 2026-10-17. **Static reading of one firmware image. Nothing was run, no ASR-X was tried, nothing here is built.** Every statement is
"what the code does". Where a line is an inference it says so. Addresses are file offsets (the ROM is mapped at 0), RAM is `0x0BExxxxx`.

## Short answer

1. **There is no MIDI Sample Dump Standard in this ROM.** The SysEx parser accepts only four Universal Non-Realtime families
   (`7E`): Sample Name transmit/request (`05 03`/`05 04`), Identity (`06 01`), MIDI Tuning (`08`), General MIDI on/off (`09`). SDS dump header
   (`01`), data packet (`02`), dump request (`03`) and the handshakes (`7B`-`7F`) fall into "ignore until the next F0". Generic SDS mode
   is silently dropped, never ACKed. That matches the tester's symptom (progress shown, nothing arrives).
2. **The ASR-X does not speak the ASR-10 protocol either.** Its own messages are `F0 0F 10 01 <device> <command> ...` (ASR-10 / EPS-16+:
   `F0 0F 03 0n ...`), with a different command set, different reply codes and a different data encoding. `core/ensoniq_sysex.py` (ASR-10
   spec) sends `0F 03`, which the ASR-X parser drops (state 0x16 only accepts `10` or `7F` after `0F`). **The Sampler Type
   `ensoniq_eps_asr` cannot work against an ASR-X as written.** It may still be right for an EPS-16+/ASR-10 (nothing here says anything about them).
3. **Samples are objects, not a dump stream.** The ROM has a typed object store (tags `GBL1 PRG1 PRF1 DRM1 SEQx RHY1 PTN1 PST1 SMP1`). Type 8,
   `SMP1`, is the sample: a 52-byte header followed by signed 16-bit big-endian audio (header layout in "The sample object" below, second pass). Whole objects and byte
   ranges of them can be read and written over SysEx, and names can be listed with the standard `7E 05 04` request.

## What was read

| File in the zip | Notes |
|---|---|
| `asr267hi.bin`, `asr267lo.bin` | 512 KB each: the even bytes and the odd bytes of one 16-bit-wide ROM pair. |
| `asr267.hex` | Despite the name a binary file: exactly the byte interleave (`hi[0] lo[0] hi[1] lo[1] ...`), 1 MiB. Verified equal. |
| `readme.txt` | Two lines on burning EPROMs. |

CPU: Motorola 68k, `MOVEC` and 68020 addressing modes (capstone needs `CS_MODE_M68K_040`; 000 mis-decodes). Vector table at 0, reset PC `0x41A`,
initial SSP `0x0BE77400`, peripherals around `0xF00000`, `0x880378`, `0xB00000`. The code is compiled C with `LINK A6` frames, which makes
function boundaries easy to find. Method: interleave, linear sweep per `LINK`, follow the MIDI path by hand. The scripts were scratch and are not in the repo.

## The receive path

- **UART interrupt, `0x9C3E`.** `F0` sets an "in SysEx" flag (RAM `0xBE00098`); every following byte up to and including `F7` is pushed into a
  **4096-byte ring buffer** (module `0x7D5DA`-`0x7D8CA`: buffer `0xBE0C940`, read index `0xBE030C2`, write `0xBE030C4`, count `0xBE030C6`,
  overflow counter `0xBE030CA`). Real-time and channel messages take their own paths (the `0x7B`-`0x7F` compares at `0x6153x` are channel-mode CCs, not SDS).
  A flag at `0xBE47E43` gates the whole SysEx branch; it is probably the "SysEx Recv" setting but that is not confirmed.
- **Consumer, `0x872A2`** pops a byte at a time into the **state machine at `0x6DE4A`**. State in RAM `0xBE02C50`; jump table `0x6DEC8`
  (16-bit offsets, states 0-0x66); states 0x67-0x78 (done / ignore / errors) are dispatched at `0x720A8`.
- **Bit-collect mode** (state 0): a number arrives as 7-bit bytes, least significant first, accumulated at `0xBE02C58` until the wanted bit
  count (`0xBE02C5C`: 14 or 32) is reached; every data byte is also added to a running sum (`0xBE02C68`). A byte >= 0x80 aborts.
- **Device ID** is a byte in RAM `0xBE491E0` (the "SysEx Device ID" setting, read by `0x73076`). A message is accepted if its device byte is that or `7F`.
  Replies carry the unit's own ID, not the request's.

## Universal messages (`F0 7E <dev> ...`)

| Bytes | Handler | What it does |
|---|---|---|
| `05 03 ss ss ll <tag> ll <name> F7` | S0x0B-0x0E | **Sample Name transmit**: renames sample `ss ss` (14-bit, LS byte first) and shows "Rename sample successful!/failed!". Language tag and name are length-prefixed. |
| `05 04 ss ss F7` | S0x0F-0x10, `0x72E12` | **Sample Name request**: replies `F0 7E <own> 05 03 ...`. |
| `06 01 F7` | `0x73C76` | Identity request. Reply `F0 7E <own> 06 02` + **11** bytes from RAM `0xBE02C82` (contents set at boot, not decoded; the MMA layout has 9) + `F7`. |
| `08 01|02 ...` | S0x1C-0x22 | MIDI Tuning Standard bulk dump; the count `0x180` = 128 x 3 bytes. Also reached from `F0 7F <dev> 08 ...` (single-note change). |
| `09 01|02 F7` | S7-S9 | General MIDI on / off (posts an event, `0x86F6A`). |

Everything else under `7E` is state 0x69 (ignore). In particular **no `01`, `02`, `03`, `7B`, `7C`, `7D`, `7E`, `7F` and no loop-point or extended-header
extension (`05 01`, `05 02`, `05 05`...)**.

## The Ensoniq family (`F0 0F 10 01 <dev> <cmd> ...`)

Parser: `0F` -> family byte must be `10` (or `7F`, see below) -> product byte must be `01` -> device byte -> command byte (jump table `0x6E9B0`, commands 0-0x15).
Commands 0, 0x0B-0x0F and 0x14 are ignored. Reply opcodes are **command + 0x40**. Replies are built by `0x7476A` (bytes out, summed into `0xBE02CC0`)
with the 3 header bytes at RAM `0xBE02C90` (inferred to be `0F 10 01`, matching the parser).

### Encoding

- **14-bit number**: 2 bytes, low 7 bits first (`0x747EE`). **32-bit number**: 5 bytes, low 7 bits first (`0x747BA`, converter `0x6A64E`).
- **Payload**: taken four memory bytes at a time, read as a **big-endian** 32-bit word, sent as 5 septets; the last group is zero-padded
  (`0x7309A`). So 16-bit audio goes out two samples per word, first sample in the high half.
- **Checksum** (object transfers only): the sum of every payload septet, low 14 bits, 2 septets, just before `F7`. The count of bytes is in the size field.
- **Errors**: `F0 0F 10 01 <dev> 7F <code> F7` (`0x73C12`). Code 1 for a malformed message; some paths echo the failed command's reply opcode
  (`41`, `43`, `45`) and the list command echoes `06`. A bad checksum or "Bad location" is also shown on the unit's display.

### Objects

Type table at `0x6B97A` (count 9 at `0x6B976`, 40 bytes per entry; the entry also holds the per-type section functions). Message `type` is the index:

| Type | Tag | Notes |
|---|---|---|
| 0 | `GBL1` | System setup (one item). |
| 1 | `PRG1` | Sound (128 items). Sections: 0 header, 1 an `OFST` chunk table, 2 indexed sub-objects. |
| 2 | `PRF1` | Performance, fixed size `0x576`. Location `0x3FFF` = the edit performance, `0x3FFD` = track params. Location = bank << 5 + index. |
| 3 | `DRM1` | Kit, fixed size `0x19C`. Locations `0x500`/`0x501` are the song/rhythm edit kits. |
| 4 | `SEQx` | Fixed size `0x18`. |
| 5, 6 | `RHY1`, `PTN1` | Variable size. |
| 7 | `PST1` | Fixed size `0x1A8`. |
| 8 | `SMP1` | **Sample.** Variable size. |

Sound and kit location = bank << 7 + index (bank is shown as three characters on the unit's display). That types 0-3 are the first four is certain (the on-screen
"XMT/RCV system setup / perf / sound / kit" texts select by them); types 4-8 are the table order and are unconfirmed.
Every object starts with a 32-bit total size and the 4-char tag.

### Commands

| Cmd | Request body | Reply | Meaning |
|---|---|---|---|
| `03` | type(1) loc(2) | `43`: type loc(2) size(5) payload checksum(2) | Download an object ("XMT ..." on the display). For a sound or kit that is the one currently selected, a second, longer record is built (header from system fields, name, OFST chunk): **not decoded**. |
| `01` | type(1) loc(2) | `41`, same layout | The plain version of `03` without that special path. |
| `02` | type(1) loc(2) size(5) payload checksum(2) | `42` after commit | **Upload an object** ("RCV ..."). Allocates the slot (`0x6C0D2`), checks the sum, then commits (`0x6A74A`, `0x6BE4E`). **Size 0 clears the slot** for types 2 and 7. |
| `04` | type(1) loc(2) section(1) index(2) offset(5) length(5) | `44`: the same fields, then `length` payload bytes, no checksum | **Read part of an object**; bounds-checked against the object size. Type `7F` is special-cased (not decoded). |
| `05` | type(1) loc(2) section(1) index(2) a(5) b(5) payload | `45` | **Write part of an object** (a, b are offset and length by analogy with `04`; inferred). No checksum. |
| `06` | bank(1) | `46`: bank count(2), then per entry id(5) + a 33-byte NUL-terminated name | Bank directory (names and ids). What a given bank number lists is not worked out. |
| `07` | `01` x(2) y(2) | `47` | Clear a SoundBank (display "CLR SoundBank"). |
| `09` | (none) | `49`: 80 bytes from RAM `0xBE00438` | Looks like the display contents (2 x 2 x 20 characters; inferred). |
| `0A` | up to 48 chars, NUL; then 128 values 0-2 | `4A` | Shows a message, then sets 128 small values (`0x156AA`). UI related, not worked out. |
| `12`, `15` | size(5) = `0x80`, then payload | `52`, `55` | A `0x80`-byte block, not worked out. |
| `13` | two nibbles, `F7` | none | Jumps through a vector table (`0x968`) after comparing 4 bytes against a hardware address (`0xB80004`): a service entry, **do not poke**. |
| `08` | type(1) a(5) b(5) | none | Posts a UI event (`0x86F6A`), not worked out. |
| `10` | `02` loc(2) = `0x0020`, a byte `20`, size(5) = `0x576`, payload, checksum | `50` | Replace a whole PerfBank (display "RCV PerfBank"). |
| `11` | `01` loc(2) = `0x0080`, a 14-bit `0x80`, size(5) = `0x200` (handles), then context, then heap, each with a size; checksum | `51` | **Replace a whole SoundBank** (handles table, context, heap; progress shown as percentages). Sounds and their waves live in that bank's heap. |

`F0 0F 7F <dev> 01|02 ...` is a second, smaller family handled at `0x7393E` (`0x727FA`/`0x7284A`); the error names `eFPDisabled` and `eIllegalBtn` suggest it
is front-panel control, but that is a guess.

## The sample object (`SMP1`), second pass 2026-10-17

Read from the sample module (`0xAD3EE`-`0xAE600`) and from the code that builds and exports samples: the constructor `0xADF52`, header initialiser `0xADED2`,
disk-file importers (`0xA1596`, `0xA21EA`), the recorder (`0x3906`, `0x33D2`), the pitch-word routine `0xAE7FA`, the AIFF exporter (`0xAECE2`, `0xAEF82`).
Still static reading, nothing run on a unit. Type 8 slots run 0..0x3FF (`0xADFE2` takes the first free one).

**Object = 52-byte header + N signed 16-bit big-endian samples.** `0xADF52(slot, N)` allocates `size = 0x34 + 2N` (the constructor does no rounding). Audio is
copied to the AIFF exporter straight from `+0x34` with no byte swap, so memory is big-endian; the disk importer swaps bytes (`0xA3FC2`).
Header (all big-endian, as sent on the wire):

| Offset | Size | Value / meaning | Confidence |
|---|---|---|---|
| 0x00 | u32 | total size = 0x34 + 2N | certain |
| 0x04 | 4 | `SMP1` | certain |
| 0x08 | 16 | name, ASCII, **space padded** (a name request returns exactly these 16) | certain |
| 0x18, 0x19 | u8, u8 | flags, 0 (set to 1 by `0xADE5A`/`0xADEAA`; the "Cleaning up samples." pass looks at them) | init value certain, meaning guessed |
| 0x1A | u8 | root key, MIDI note, default 60 | certain |
| 0x1B | u8 | 0 | |
| 0x1C | u16 | 0 (compared when the unit looks for duplicate samples) | meaning unknown |
| 0x1E | u16 | `0xFFFF` = owns its audio; otherwise the slot of the sample whose audio is shared (its name then shows `*` in a name reply) | certain |
| 0x20 | u16 | **the object's own slot number** (the constructor writes it; the receive hook does not fix it) | certain |
| 0x22 | u16 | 0 | |
| 0x24 | u8 | `0x7F` | meaning unknown |
| 0x25 | u8 | loop mode: 0 = none (what we would send), 2 = forward loop, 3 = back-and-forth (from the AIFF exporter's mapping; 1 is also "no loop") | fairly certain |
| 0x26 | u16 | **pitch word**, below | formula certain, rounding approximate |
| 0x28 | u32 | start, in 1/16 sample (`index << 4`), 0 | fairly certain |
| 0x2C | u32 | loop start, 1/16 sample (0 when not looping) | certain (AIFF exporter shifts it back into a marker) |
| 0x30 | u32 | end, 1/16 sample; init value `(N - 1) << 4`; when looping it holds the loop end | certain |

**Pitch word (+0x26).** `word = -(round(3072 * log2(29762 / rate_hz))) - 256 * root_key`, as a signed 16-bit value (`0xAE7FA`: a log of the ratio to
29,762 Hz, which is EPS rate code 21, times 12 x 256, rounded, minus the root key x 256). The unit therefore plays any sample rate: no resampling is needed,
the rate is carried in this word. A new sample made by the recorder has `0xCACD` (about 44,082 Hz at root 60); the formula gives `0xCAD0` for 44,100 Hz at root 60,
three 1/256-semitone steps apart, so the rounding or constant differs in a detail that does not matter in practice.

**Listing names needs no object download.** `F0 7E <dev> 05 04 ss ss F7` (`ss ss` = the slot, low 7 bits first) returns
`F0 7E <own> 05 03 ss ss 00 10 <16 name bytes> F7`; a slot that is empty (or one of two internal sample numbers) answers `F0 0F 10 01 <own> 7F 41 F7`
(`0x72E12`). So the slot scan is also the way to find the first free slot.

**Writing a sample** (command `02`): send the whole object (header + audio, starting at byte 0 so the size and tag are in the payload) for a slot that is
empty, checksum over the payload septets. The unit allocates with `0x6C0D2(type 8, slot, size)`, copies the words in with `0x6BFAE`, and acks with `F0 0F 10 01 <own> 42 F7`
after the checksum matches. There is no pointer anywhere in the header, so nothing needs a fix-up. Mono only per object; the two halves of a stereo file are two samples.
The ack always follows the commit; a wrong checksum shows "Wrong checksum!" on the unit's display and, as far as the code goes, no ack, and may leave the slot written.

**Receiving** is command `01` (or `03`) for the whole object, or command `04` with section 1 for the audio alone (section 0 = the whole object, section 1 = `+0x34` onward).
The unit's SysEx input is a 4 KB ring buffer filled by interrupt, drained by the parser as it arrives, so a long upload streams as long as the sender sticks to MIDI speed
(about 2.5 KB/s of audio, 10 s of 44.1 kHz mono takes about 5 minutes).

## What this means for the app

1. Generic SDS to an ASR-X: dead end, confirmed from the firmware.
2. The ASR-10 dialect in `core/ensoniq_sysex.py`: wrong header (`0F 03`) and wrong commands for an ASR-X; the ROM drops it without a reply.
3. **Built 2026-10-17** (Sampler Type `ensoniq_asrx`: `core/asrx_sysex.py`, `controller/asrx_transfers.py`, `core/demo_asrx.py`; steps for a tester in `tests/asrx_test_plan.md`): list by name requests, receive by range reads (command 04) with a whole-object fallback (command 01), send as one upload (command 02) into a verified-free slot. None of it has met a unit.
4. If an ASR-X dialect is ever built, treat as never-send: `02` with size 0, `07`, `10`, `11`, `12`, `13`, `15`, and any `05` outside a verified range.

## Open

- Header fields 0x18/0x19/0x1C/0x22/0x24 (always sent as the constructor's defaults). Whether `06` lists samples. What the unit does on a memory-full upload (no reply is visible in the code).
- The special sound/kit path of `03`; type `7F` in `04`/`05`; the `0F 7F` family; commands `08`, `0A`, `12`, `15`.
- All of it unverified on a real ASR-X. v2.67 only; the ASR-X Pro / other OS versions were not read.
