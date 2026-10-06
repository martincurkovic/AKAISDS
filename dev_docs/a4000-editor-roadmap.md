# AKAISDS - Yamaha A4000/A5000 editor: roadmap & handoff

Started 2026-10-06. For whoever picks this up (human or agent). Read the repo's `AGENTS.md` first (house rules,
the S1000/S950 precedents this plan leans on), then this file. **The research is done and the protocol is proven on
real hardware; the codec exists and is tested; nothing else is built yet.**

## START HERE - status at a glance (updated 2026-10-06, end of session 1)

| Piece | State |
|---|---|
| Protocol research (service manual p.32-42) | done; **verified against the real A4000** (see "Phase 0 results") |
| `src/core/yamaha_sysex.py` - the codec | **DONE, 35 tests** (`tests/test_yamaha_sysex.py`), real captures as fixtures in `tests/fixtures/a4000/` |
| `tools/a4000_discovery.py` - read-only hardware probe | done and used; tracked in git (moved out of the gitignored `test_scripts/`) |
| `src/core/yamaha_params.py` - the P1..P6 parameter tables | **DONE for program / Easy Edit / sample** (204 rows, `tests/test_yamaha_params.py`); effects, controls, system params NOT covered - see "Parameter tables" below |
| `tools/a4000_verify_params.py` - checks every table row against the live unit (read-only) | done; **959/959 agree** (see "Parameter tables") |
| `core/demo_a4000.py` - `FakeA4000` | not started |
| `core/yamaha_bridge.py` - single-thread worker over the shared MIDI transport | not started |
| Sampler Type entry + Dashboard "Open Editor" gating | not started |
| `ui/yamaha_program_editor.py` | not started |
| Any write to the unit | **never done** - nothing has ever been written to the A4000 |

Git: the earlier S1000/S950 work is committed (HEAD `68aeeab`). **Uncommitted at time of writing:**
`src/core/yamaha_params.py`, `tests/test_yamaha_params.py`, `tools/a4000_verify_params.py` (the codec, its tests, the
fixtures, `dev_docs/` and `tools/` were committed by the user before this session's parameter work).
Full suite passes (`uv run pytest tests/ -q`, ~40 s).

Hardware at the user's desk: Yamaha **A4000**, cold-booted, only the factory built-in waveforms loaded (sine wave,
saw up, triangle, square, pulse 1/2/3), **Device Number 0**, Bulk Protect off, connected through a **PreSonus Studio 26**
(`mido` port name `PreSonus Studio 26` for both in and out; it is the port saved in AKAISDS's config, so the scripts
need no flags). The AKAISDS app must be CLOSED while a script runs (two programs on one MIDI port race). **State left
on the unit (RAM only, nothing saved):** program 001 has "sine wave" assigned on MIDI channel 01 with an Easy Edit
Level offset of +30 - harmless, handy for write tests, undone by a power cycle.

The user said they don't know the A4000 well. When you need a front-panel action, give exact button steps, keep
each to one knob and one dump, and diff the bulk dumps before/after (that is how Easy Edit was mapped - see below).
The Yamaha **owner's** manual (`yamaha_a4000_a5000_manual.pdf`, 296 pp - a copyrighted PDF, deliberately NOT in this repo; the user keeps it on their Mac, ask for it): program
assignment p.55-56 (PLAY > PROGRAM > SAMPLE, Knob 2 pick a sample, Knob 4 assign/channel); Easy Edit p.96-101
(PLAY > F3, Knob 1 = page, Knob 2 = sample, Knob 3/4 = Level/Pan offset on the Mix page); UTILITY > MIDI bulk page
(Knob 3 Bulk Protect, Knob 4 Device#) ~p.201; error messages ("Please set device number", "Bulk protect switch is ON")
near the end. `pdftotext -layout` works well on this one (single column).

## Goal

The user owns a Yamaha **A4000** (and an Akai S2000). Sample transfer to the A4000 already works through the
Dashboard's **Generic SDS** mode. They want to *edit programs / samples / their parameters* on the A4000 over MIDI,
"just like" the Program Editor does for the S2000. This is NOT for sending samples - that part is done.

The user can test on their own A4000 (unlike the S1000 work, which relies on a remote tester). They said they
don't know the A4000 well - when you need a front-panel fact (device number, bulk protect, how a parameter
sounds), give exact steps and say plainly if you're not sure of a menu path; the Yamaha **owner's** manual
(see above) settles most of it.

## Source material

`Yamaha_A4000_A5000_Sampler_Service_Manual.pdf` (61 pages, 1999, covers both the A4000 and A5000; copyrighted, NOT in
this repo - the user has it, ask for it). **PDF pages 32-42 are "MIDI DATA FORMAT"**; page 43 is the MIDI implementation chart.

How to read it: `pdftotext -layout` interleaves the two page columns and is nearly useless for the tables.
`pdftotext` without `-layout` is better for prose. For tables, render and look:
`pdftoppm -f 35 -l 42 -r 110 -png <pdf> <outprefix>` and open the PNGs (that is how this was researched -
**everything below was read off those images by eye; transcription errors are possible. Re-check any
offset/number against the page before coding it.**)

Page map: 35 = SDS tail, inquiry/identity, bulk dump + dump request; 36 = parameter change / request /
object link messages; 37 = Table 1 bulk dump layouts; 38 = Control/Sample Parameter/Easy Edit blocks; 39 = effect
parameters (not read in detail); 40-41 = Table 2 parameter-change numbering (program, sample bank, sample, value
enums); 42 = filter-type enum, output enums, system parameters, switch remote.

## What the protocol offers (from the manual)

All Yamaha messages: `F0 43 <type|n> 58 ...` for parameter messages, `F0 43 <type|n> 7A ...` for bulk. `n` =
**Device Number** (the unit must have one set - it errors "Please set device number" otherwise). Bulk/parameter
*reception* is disabled while the **Bulk Protect** switch is on (error "Bulk protect switch is ON").
Data bytes are 7-bit; every 8-bit data byte is sent as **two MIDI bytes** (high nibble first, then low nibble -
`0000 b7 b6 b5 b4` then `0000 b3 b2 b1 b0`). Multi-byte values are big-endian; signed = two's complement.

Data type codes used in tables: UC u8, SC s8, US u16, SS s16, UL u32, SL s32, `*n` n bytes, `c` = ASCII text,
`b` = bitmap (see the name column).

**Object model** - three editable object kinds plus two data kinds. Type codes: Program 20 ($14), Sample Bank
17 ($11), Sample 16 ($10), Wave data 2 ($02), Sequence 19 ($13). Objects are named by a **16-char name**;
**programs are named by their program number string** (the manual's example is `"001"` padded with spaces), samples
and sample banks by their real names (so duplicate sample names will be ambiguous - same trap as on the Akai).

**Parameter messages** (page 36) - byte layouts; the byte *positions* of the 16-char name / type / F7 in the
object-select message looked slightly garbled in the page image, so verify on the page:
- Object select `F0 43 1n 58 00 <16-byte name> <type> F7` - makes that object current. **Stateful**: the
  selection persists until the next select, so every edit sequence is select-then-edit and must be serialised.
- Object edit `F0 43 1n 58 01 <P1..P6: 6 bytes> <data, nibbled> F7` - set one parameter of the selected object.
- System parameter change `... 58 02 <P1..P6> <data> F7`.
- Switch remote `... 58 03 ...` (front-panel key/knob remote control; not needed).
- Object link change `... 58 04 <upper name> <upper type> <lower name> <lower type> <0/1> F7` - link/unlink
  Program<->Sample Bank/Sample, Sample Bank<->Sample.
- Requests use `3n` instead of `1n`: `3n 58 01 <P1..P6>` (object parameter), `3n 58 02 <P1..P6>` (system),
  `3n 58 04 ...` (object link). The unit **answers a request with the same message format as the change**
  (so the reply to `3n 58 01` looks like `1n 58 01`). Parameter messages have **no checksum**.

**Bulk messages** (page 35): bulk dump `F0 43 0n 7A <bytecount 2 bytes> "LM  0474" <fmt 2 chars> <16-byte object
name> <nibbled data...> <checksum> F7`. The header text is `LM  0474` for the A4000 (`...0475` for the A5000).
Format endings: `SY` system, `PG` program, `SB` sample bank, `SP` sample, `WD` wave data, `SQ` sequence, `OL`
object list. Checksum = XOR of everything between byte count and checksum. Dumps over 4096 data bytes are split
into 4096-byte blocks (`F7` only after the last; later blocks omit the header bytes 6-31). **Dump request**
`F0 43 2n 7A "LM  0474" <fmt 2 chars> <16-byte name> F7`. A bulk dump only goes in if Bulk Protect is off.
The object list (`OL`) bulk is just `<type byte><16-char name>` repeated for every object in memory - that is
how to enumerate programs/samples.

**Identity** (page 35): `F0 7E <ch> 06 01 F7` -> reply with manufacturer `$43`, device family code `$0041`
("LM"), family number `$01DA` (A4000, "#0474") or `$01DB` (A5000, "#0475"), software revision. Usable to confirm
the model - but see the S1000 note in `AGENTS.md`: don't *rely* on detection, make the model a user setting.

**Bulk layouts** (page 37, Table 1): Program bulk = 408 + 56*(number of assigned samples) bytes; Sample Bank bulk
= 312 + 20*(members); Sample bulk = 336; Wave data = 72 + 2*(words); every object starts with a 64-byte
`[Common]` block (object type, name, size...). Program body: name, AD-in (audio input) settings, program LFO,
portamento, level/transpose, effects 1-3 (4-6 are A5000-only), 4 controls, MIDI reset/toggle bitmaps,
AD-in output/level assignments, then `[Easy Edit Parameter] 56 bytes x number of assigned samples`.

**Parameter-change numbering** (Table 2, pages 40-42): the `P1..P6` space. Roughly: `P1` picks the block
(0 = common, 1 = the object's own parameters, 2 = the per-sample block - verify on pages 40-41); for programs
`P1=1` carries program-level params (P2.. index within, e.g. controller 1-4, effect 1-3 parameters with `P2`=effect
number), `P1=2` is the **Easy Edit** block (`P2*100+P3` = which assigned sample, `P4` = parameter number). For
samples `P1=2` is the full Sample Parameter list (key range, original key, tune, loop mode/points, filter type,
cutoff, Q, FEG/PEG/AEG, LFO, EQ, velocity ranges, outputs, alternate group, portamento ...). Pages 40-42 hold
the exact index, size, range and name of each; value enums (filter types, outputs 1/2 assignments, control
functions) are on 41-42.

## How this differs from the Akai editor (design consequences)

1. **No keygroups.** A program = a list of assigned samples/sample banks. Each *sample object* carries its own
   key range / filter / envelopes (shared by every program that uses it); the program only adds per-assignment
   **Easy Edit offsets** (level, pan, tune, key limit, velocity limit, EG offsets...). The honest S2000-editor
   analogue of a keygroup is "a sample assigned to this program". The window layout needs real thought - don't
   force the S2000 Program/Keygroup tabs onto it. Make the shared-sample edit scope obvious in the UI ("affects N
   programs").
2. **Single-parameter reads/writes exist** (like S3000's byte ops, unlike the S1000 whole-block rewrite) - so
   `s3k`-style field-by-field editing is feasible. `s3k`/`s3ked` themselves are Akai-only and unusable here;
   this needs its own parameter table + codec (same approach as `core/s950_*.py`).
3. **Stateful select** means one in-flight sequence at a time, on one thread. Apply the `BridgeWorker` rules
   from `AGENTS.md` (persistent single `QThread`, queue, no thread-per-action) and the shared-transport
   freeze/unfreeze ordering around Settings.
4. **Transfers keep using Generic SDS**; only the *editor* is a new family. Careful inversion of the S950 rule:
   the S950 must never fall through to generic; here transfers *should* take the generic branch while the editor
   uses the Yamaha path. `SamplerController.device_type` is the transfer family, `sampler_model` the full choice -
   add a Yamaha entry to `core/sampler_models.py` whose transfer family is `generic` and whose editor family is
   new, then extend the Dashboard's Open Editor gating (`_update_open_editor_enabled` / `open_program_editor`,
   currently akai-or-s950 only).
5. **A4000 vs A5000**: effects 4-6, MIDI channels 17-32 and the second effect bank are A5000-only; gate them.

## Suggested files (match existing naming)

- `core/yamaha_sysex.py` - **DONE** (see "The codec" below). Nibble codec, framing, bulk header/checksum/4096-block split, request builders,
  object list parser, object-select/edit/request builders. Pure, no Qt/MIDI. Messages as the bytes BETWEEN F0
  and F7, like `core/akai_sysex.py` / `core/s950_sysex.py`.
- `core/yamaha_params.py` - the P1..P6 tables as data (name, P-tuple, size/type, range, enum), one source of truth.
  Keep the manual's page reference next to each group.
- `core/yamaha_bridge.py` - the transport + `BridgeWorker`-style single-thread queue; reuse
  `core/midi_transport.py`'s shared ports (do **not** open a second connection to the same port - confirmed
  race in `AGENTS.md`).
- `core/demo_a4000.py` - `FakeA4000` on rtmidi-style ports (like `FakeS950`/`FakeS1000`): object store,
  bulk dumps, select/edit/request semantics, bulk-protect and device-number behaviour as togglable errors.
  Document in its docstring what is from the manual vs guessed.
- `ui/yamaha_program_editor.py` - new window, built from `ui/editor_layout.py` helpers (see its section in
  `AGENTS.md`) so it matches the other editors visually.
- Tests: `tests/test_yamaha_sysex.py`, `..._params.py`, `..._bridge.py`, window tests. Follow `TESTING.md` and
  the "offscreen Qt" + teardown notes in `AGENTS.md`.
- Add a logging line set like the S950's (`YamahaEditor: ...`) and log every wire op via `debug_log`.

## Phases (each ends with something the user can run on the real A4000)

**Phase 0 - read-only discovery (do this first; it validates the whole manual).**
`tools/a4000_discovery.py`, no writes anywhere: open the configured MIDI ports, send Identity Request,
`OL` dump request, then a `PG` dump of one program and an `SP` dump of one sample; save each reply as `.syx`
under `~/.akaisds/a4000_discovery/` and decode them. Then a few `3n 58 01` parameter requests (a program
level, a sample's key range/original key) and compare their values with the same fields parsed out of the bulk
dumps. **Done when**: the object list decodes to the names the user sees on the unit; the bulk layout from
Table 1 yields sensible values (program name at the documented offset, key ranges 0-127...); parameter-request
values equal the bulk values for >=10 fields. Record every mismatch against the manual in a "What is a guess /
what the manual got wrong" list (the S950's `tests/s950_test_plan.md` is the template). Ask the user to confirm
Device Number is set (and what it is) and Bulk Protect is off *before* running.

**Phase 1 - codec + fake + tests** for what Phase 0 proved. **Phase 2 - read-only editor window** (program list,
per-program assigned samples, Easy Edit values, selected sample's parameters), reads only. **Phase 3 - writes**:
one parameter at a time, object-select -> edit -> parameter-request read-back -> compare; **refuse to write
unless a `.syx` bulk backup of that object was saved first** (S950 editor precedent, `S950Transfers.write_program`),
range-check from the table, first-time experimental warning, a "write back unchanged (test)" action for the first
hardware run. **Phase 4 (maybe)** create/delete/link objects (object link change exists; create/delete have no
obvious opcode - bulk-load an object may create it; don't guess, test).

## PHASE 0 RESULTS (run on the real A4000, 2026-10-06) - the manual is accurate

`tools/a4000_discovery.py` (read-only; it refuses to send anything but identity / dump request /
parameter request / object select) was run against the user's A4000 over the PreSonus Studio 26 (same ports
as the SDS setup), **Device Number 0** (worked first time; Bulk Protect evidently off). Saved dumps:
`~/.akaisds/a4000_discovery/*.syx`. Measured facts (corrections to the "from the manual" text above are marked
**CORRECTION**):

- **Identity reply** `F0 7E 00 06 02 43 00 41 5A 03 16 00 00 7F F7`: family number bytes `5A 03` = 0x01DA
  (LSB-first 7-bit) = A4000, as the manual says. Revision raw `16 00 00 7F`. (Family code raw is `00 41`.)
- **Dump request works exactly as the manual describes**, no tweaks needed: `F0 43 2n 7A "LM  0474" <fmt> <16-byte
  name> F7`, name padded with spaces (0x20). For `OL` the name field can be empty/spaces; the reply names itself
  `"Object List     "`. Programs are requested by `"001"` padded with spaces.
- **CORRECTION - bulk framing:** the byte count is **MSB-first** (`20 00` = 4096, not 32). A dump is ONE F0..F7
  containing one or more blocks, each `count(2 bytes) span checksum(1)`; the **checksum is the XOR of the span**
  (verified on every block; the 2's-complement sum does not match); only the FIRST block's span starts with the
  26-byte header (`"LM  0474"` + 2-char format + 16-byte name); a block holds at most 4096 span bytes. The object
  list was 2 blocks (4096 + 758). `split_bulk()`/`describe_bulk()` in the script implement this.
- **Object list** = repeated `<type byte><16-char name>`, 17 bytes each: **128 programs** (type 20, names `"001"`...`"128"`,
  space padded) then the built-in waveforms as **pairs** `type 2 (wave data)` + `type 16 (sample)` ("sine wave",
  "saw up", "triangle", "square", "pulse 1/2/3") - i.e. every sample has a same-named wave-data object. 142 objects
  in this unit (no user samples loaded at the time).
- **Program bulk (`PG`, "001")** = 856 bytes = 408 + 56*8, matching Table 1. Layout checked: type byte 20 @0,
  name @2 (16 chars) and a short 8-char name `"Pgm 001 "` @64, program level @83, LFO tempo @92, LFO reset
  note @93 (-1 = all), LFO reset MIDI channel @87 (-2 = off). **Sample bulk (`SP`)** = 336 bytes, linked wave
  object name L @64 - matches.
- **Parameter request/reply works as documented.** `1n 58 00 <name> <type>` (object select) then
  `3n 58 01 P1..P6` returns `1n 58 01 P1..P6 <nibbled data> F7`. **The unit echoes the object-select message
  back** (a `1n 58 00 ...` message arrives right after sending one) - expect and ignore/consume it. A batch compare
  of 11 program parameters (level, transpose, LFO tempo, LFO reset note/channel, portamento type/rate/time, S/H
  speed, AD-in L pan, assigned-sample count) gave **identical values via parameter request and via the bulk
  dump, 11/11** (the one "DIFF" in the run was a wrong offset in my own comparison table: AD-in source is a
  bitmap at offset 72, not a byte at 74).
- (The "not yet checked" list that was here is superseded by the addendum and Easy Edit sections below: sample
  parameters and Easy Edit are now verified. Still unchecked: sample BANK bulk/parameters, system parameters,
  effects/controls, and every WRITE.)

### Phase 0 addendum (same day)
- **Sample object parameters: 17/17 match** between `3n 58 01` requests (`P1=2`, `P2`=3..34, `P3`=0 for the L/R
  pairs) and the `SP` bulk, read at `112 + <Table 1 [Sample Parameter] offset>`: MIDI rx channel, pitch bend
  type/range, coarse/fine tune, original key, sampling frequency (48000), key range hi/lo, loop mode, wave start/
  length (UL, 4 bytes big-endian), filter type/cutoff/Q, sample level, pan. Values for the built-in "sine wave":
  128 frames at 48 kHz, original key 66, fine tune -20, cutoff 127, level 100. Sizes/signedness per Table 2 all held
  (SC negative decodes correctly as two's complement).
- **All 128 programs dumped: every one is 856 bytes, "assigned samples" (@94) = 0, checksums OK** (cold-booted
  unit, nothing loaded, only the built-in waveforms). **Each program carries 8 Easy Edit blocks even with 0 assigned
  samples - all empty (name all zero, type 0, receive channel -1)** - so the manual's "408 + 56*n" is not "n =
  assigned count" on a fresh unit; what `n` means is unresolved. **Easy Edit parameters are therefore still unchecked
  against real data** - needs a program with a sample assigned (front panel, or an object-link write once writes
  are being tested).

### Easy Edit, measured with front-panel changes by the user (same day) - Phase 0 is now complete
The user assigned "sine wave" to program 001 on MIDI channel 01 (PLAY > PROGRAM > SAMPLE, Knob 4), then set its
Easy Edit Level offset to +30 (PLAY > F3 > Mix page > Knob 3). Diffing the program bulk before/after:
- **Assigning a sample:** the program's "assigned samples" count @94 went 0 -> 1 and **Easy Edit block 0** filled in:
  `@0 name "sine wave"` (16 chars), `@16-19` 4 bytes `01 44 3c 48` (unknown id - the sample bulk has `01 44 3c 30`
  at its @60 and @96; treat as opaque, preserve), `@20 = 0x10` (object type 16 = sample), `@21 = 0` (receive channel
  assign: 0 = channel 01; -1 = "=sample" is the empty-slot default). The other 7 of the 8 blocks stay empty
  (name zeros, type 0, `@21 = ff`). Slots fill from the front. The block count stayed 8 (856 bytes) with 1 assigned.
- **Changing the Level offset:** EXACTLY ONE BYTE changed in the whole dump: **Easy Edit block 0, +22: 0x00 -> 0x1e
  (+30)**, which is what Table 1 says (`+22 SC level offset`). Confirms the Easy Edit block layout in the manual.
- **Easy Edit parameter requests work as documented:** after `1n 58 00 "001" 14` (select program), `3n 58 01
  P1..P6` with **P = [2, 0, 0, 3, 0, 0]** returns 30 (P1=2, P2*100+P3 = index of the assigned sample, P4 = the
  Easy Edit parameter number from Table 2, P5=0). Also verified: P4=0 returns the assigned name, P4=2 the receive
  channel (0), P4=4 pan offset (0), P4=5 fine tune offset (0).
- The user's unit now has that assignment + offset in RAM (nothing saved). It is harmless; a power cycle or setting
  the sample to "off" in PLAY-SmpSel undoes it.

## The codec - `src/core/yamaha_sysex.py` (done, tested against real captures)

Pure functions, no Qt/MIDI; a "message" is the bytes BETWEEN F0 and F7 (a leading F0 / trailing F7 is tolerated on
input). Public surface:
- coding: `nibble/denibble`, `xor7`, `pad_name/decode_name`, `program_object_name(n)` ("001"), `encode_value/decode_value`
  (big-endian, two's complement; UC/SC 1 byte, US/SS 2, UL/SL 4).
- identity: `build_identity_request`, `parse_identity_reply` -> `IdentityReply.model` ("A4000"/"A5000").
- bulk: `build_dump_request(device, fmt, name)`, `parse_bulk_dump(msg, verify=True)` -> `BulkDump(device, fmt, header,
  name, name_raw, data, blocks)` (raises `YamahaSysexError` on a bad checksum/truncation), `build_bulk_dump(...)`
  (rebuilds **byte for byte** every real dump we captured - tested), `parse_object_list(data)` -> `[ObjectEntry(type, name)]`.
- parameters: `build_object_select`, `build_parameter_request(device, params, system=False)`, `parse_parameter_message`
  -> `ParameterMessage(kind "select"|"object"|"system", params, data, ...)`; WRITE builders `build_object_edit`,
  `build_system_parameter_change` (never sent to a real unit yet - the first write test is a Phase 3 job).
- streams: `split_messages(blob)`, `classify(msg)`. Exception: `YamahaSysexError(ValueError)`.

Facts the tests pin (don't "simplify" them): byte count is MSB-first; multi-block inside ONE F0..F7; XOR checksum per
block; first block = 26-byte header + 4070 data bytes; the unit echoes object-select; `[Common]` byte 1 of a program is
`0x60` with nothing assigned and `0x61` once a sample is (unexplained - preserve, never "fix"); assigning a sample
changes ONLY byte 1, the count @95 and Easy Edit block 0; Easy Edit Level offset change = exactly one byte (block 0 +22).

## Parameter tables - `src/core/yamaha_params.py` (done for program / Easy Edit / sample)

One `Param` row per editable value ties its parameter-request address (P1..P6, Table 2) to its bulk-dump offset
(Table 1): `Param(key, name, scope, p, size, signed, lo, hi, offset, bits, kind, read_only, a5000_only, enum, bulk_only)`.
Scopes: `"program"` (offset absolute in the PG payload), `"easy_edit"` (56-byte block per assigned sample at bulk 408; a
request needs the slot: P2*100+P3), `"sample"` (the `[Sample Parameter]` block, bulk offset 112 of the SP payload).
Access: `get(scope, key)`, `rows(scope)`, `request_params(param, slot)`, `bulk_offset(param, slot)`, `extract(param, bulk_data, slot)`,
`decode_reply(param, reply.data)`, `in_range`. Bitfields use `bits=(shift, width)` (manual "b5-3" = shift 3, width 3; a signed
2-bit field gives Easy Edit's -1/0/1). Enums: filter types (17), output1/output2 assignment (12 each; 10-12 A5000-only).
**204 rows:** 47 program, 31 Easy Edit, 126 sample (incl. sample controls 1-6).

**Not covered:** the program's effect blocks (P2=21, 40 bytes x3 at bulk 96; page 39 was never read) and controls (P2=22,
4 bytes x4 at bulk 216); the MIDI-channel bitmaps (P2=1, 2); a sample's "linked to program" / bank-member bits; stereo R
addresses of wave/loop (the manual gives no P-numbers); **system parameters** - the manual gives P-numbers (p.42) but
documents NO bulk layout for the `SY` dump, so they can only be read/written one at a time; sample bank objects.

**Verification, and its limits (read before trusting an offset):**
- `uv run python tools/a4000_verify_params.py [--sample NAME ...]` reads every row two ways (parameter request vs bulk
  offset), flags DIFF / SIZE / RANGE / NO REPLY. Result on the real A4000: program 001 + its Easy Edit slot 0 + all seven
  built-in samples = **959 of 959 agree** (203 distinct rows).
- **That is weaker than it sounds.** The unit is cold-booted with factory values, and the seven built-in waveforms have
  identical parameters, so every row has only ever shown ONE value. A bulk offset that points at a neighbouring byte
  holding the same default (0, 127...) would still agree. Measured on the fixtures: only ~12 of 201 rows have a value
  that no other byte of their block shares (so only those are pinned by value alone - mostly the non-default ones found
  earlier: sampling frequency 48000, fine tune -20, original key 66, level 100, Q 4, bend range 2, loop mode 1, program
  level/LFO tempo/portamento values, the Easy Edit level offset). The rest are verified only as "request address and
  transcribed offset are consistent" plus the offline test that no two rows overlap and nothing runs past its block.
- **The strong check is a write test** (Phase 3): on a throwaway object, write a distinctive value to each row with a
  parameter change, dump the object, confirm EXACTLY that byte (or those bits) changed, restore. That proves every
  offset, size and bit position. It needs the first-ever write to the unit - do it only with the user's go-ahead, a
  `.syx` backup first, and on a throwaway program/sample.

**Corrections to the manual found this way:** `P2=66` (sample AEG sustain) is a 4-byte array at +151..+154 - the manual
says "P3 0-1" but the real sustain level is **P3=2 (+153)**; P3=0/1/3 are reserved bytes (values 8, 127, 0 on a default
sample). Other manual quirks kept as-is and confirmed harmless: Table 2 calls LFO reset note UC though it includes -1
(Table 1 says SC; we treat it signed); sample `velocity offset`/`PEG range` are SC in Table 2 but UC in Table 1 (signed
used).

## Next steps (in order)

1. ~~`core/yamaha_params.py`~~ done (above). Remaining table work, lower priority: effect blocks + controls (read page 39 first),
   system parameters (single-value only), sample banks, the MIDI-channel bitmaps.
2. **`core/demo_a4000.py` - `FakeA4000`** on rtmidi-style ports like `FakeS950`/`FakeS1000`: object store seeded from the
   fixtures (128 programs, built-in samples), answers identity / dump requests / object select (+ echo) / parameter
   requests; togglable device-number-off and bulk-protect behaviours. Docstring: what is measured vs guessed (the
   behaviour on write and on a wrong device number is NOT measured - guess and label it).
3. **`core/yamaha_bridge.py`**: one persistent `QThread` + queue (the `BridgeWorker` rules in `AGENTS.md`), select-then-
   request sequences serialised, consume the select echo, reuse `core/midi_transport.py`'s shared ports. Add a **Sampler
   Type** entry in `core/sampler_models.py` (transfer family stays `generic`; new editor family) and extend the Dashboard's
   Open Editor gating (`_update_open_editor_enabled` / `open_program_editor`).
4. **Read-only editor window** `ui/yamaha_program_editor.py` (design question for the user: program-centric - program ->
   assigned samples -> Easy Edit - or sample-centric, or both; samples' own parameters are shared by every program using
   them, so say so in the UI). Build from `ui/editor_layout.py`.
5. **Writes** (Phase 3): per-parameter select -> edit -> request read-back -> compare; refuse unless a `.syx` backup of the
   object was saved first (S950 `write_program` precedent); first-write experimental warning; a "write back unchanged" test
   action. First hardware write test: write the Level offset (+30) back to itself on program 001, read it back, then
   change it to another value, then restore. Add an `AGENTS.md` section for this feature as soon as code lands.
6. Later/maybe: create/delete/link objects (object-link-change message exists; create/delete have no known opcode - a bulk
   load of a new object may create it; don't guess, test on a throwaway).

### Open questions for the user
1. Editor organisation: program-centric, sample-centric, or both (step 4)?
2. A4000 only, or keep A5000-only parameters present-but-hidden for later?

## Safety / working rules

- Unit data lives in RAM until saved to disk/SCSI, so a bad edit is *likely* undoable by reloading - but that is
  an assumption; confirm with the user before relying on it, and still always back up first.
- Never write anything to the user's unit in Phase 0. Prefer to have them test with a throwaway program/sample.
- Don't copy code from other projects without checking their licence (the S950 port recorded the MIT notice in
  `THIRD_PARTY_NOTICES.md`; one candidate repo had no licence and was off-limits). No Yamaha-specific open-source
  editor has been looked at yet - if one is found, check its licence first.
- Don't edit the pinned `s3k`/`s3ked` dependency. Don't reintroduce thread-per-action. Don't add real multi-threaded
  stress tests (see the MIDI transport section of `AGENTS.md`).
- Add a section to `AGENTS.md` as you go, recording measured-vs-guessed facts (that file's whole point is to stop
  the next agent "fixing" hard-won corrections).

## Other things worth knowing

- Questions that were open earlier are answered: Device Number 0 and Bulk Protect off work; the owner's manual is in
  hand; the ports are the PreSonus Studio 26 (same as the SDS setup). The open ones are listed under "Next steps".
- The S1000 keygroup-delete redesign (branch `s1000-support`, in the same repo) is unrelated - see `AGENTS.md`
  "S1000 memory layout and DELK". Don't confuse the two efforts.
- The `tests/fixtures/a4000/*.syx` files are real captures; do NOT regenerate them from the codec (that would make the
  byte-for-byte tests circular). If a new capture is needed, take it with `a4000_discovery.py` and add it alongside.
- Re-probing the unit: `uv run python tools/a4000_discovery.py {identity | dump OL | dump PG --name 001 |
  dump SP --name "sine wave" | param program 001 2 0 0 3 0 | listen 60 | decode <file>}`. It only ever sends identity /
  dump / parameter requests and object select (a guard refuses anything else) and saves every reply under
  `~/.akaisds/a4000_discovery/`. `--device N` if the unit's Device Number changes; `--fill 0` if a name needs NUL padding.
