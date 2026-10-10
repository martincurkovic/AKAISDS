# AKAISDS - Yamaha A4000/A5000 editor: measured facts, open items, how to test

Started 2026-10-06, condensed 2026-10-10 (history is in git). Read the repo's `AGENTS.md` first (house rules and the "don't regress" list),
then this file for the MEASURED protocol facts, open items and hardware tools.

**State:** the protocol is proven on the real A4000 and the editor works end to end - read AND write of program / Easy Edit / sample parameters,
native wave dumps (both channels of a stereo sample), editable loop/wave markers + click-to-preview, restore from backup, assign/remove samples,
native sample LOADING (Dashboard Send), sample edits and the Slice Editor (each creates NEW samples), Detect Pitch, and Dashboard list/receive.
**Not built:** create/delete objects (no opcode), effects / controllers / system parameter tables, sample banks in the UI, A5000-specific bulk/channel support.

## Open items (nothing below has been checked on the real unit unless it says so)

1. **Link -> silent unit (the most valuable thing to measure).** After a natively LOADED sample is linked to a program the unit can show "MIDI Bulk Received" and
   answer nothing over MIDI until OK (Knob 5) is pressed on it (details under "Session 6"). Unknown: does it only happen for loaded samples or also recorded ones? Is
   there a pause / front-panel state that makes the first link safe? If a cause is found, replace the assign warning (`_confirm_assign_midi_loaded`) with the real guard.
2. **Detect Pitch** (Samples tab, Pitch card): (a) the Fine tune unit (`FINE_TUNE_STEPS_PER_CENT`, assumed 1.0): detect a tone a known number of cents off and read back
   what is written; (b) the sign (a positive Fine tune is assumed to raise the pitch): after Detect Pitch the sample at its Original key should be in tune against a
   tuner; (c) a stereo sample - does the unit mirror the left key/fine tune into the right?
3. **A5000 gating** (written for an A5000 the user doesn't own): on the A4000 check (a) the Output dropdowns (Program > Audio Input, assigned sample's Output, Samples tab >
   Output) end at `effect3`, nothing else changed; (b) `YamahaEditor: identity: A4000` is in `~/.akaisds/akaisds.log`; (c) opening the editor with the unit's MIDI-in cable
   pulled still shows the usual "no reply" message. Unknown for a real A5000: whether it answers bulk requests with the A4000 header (`HEADER_A4000`).
4. **Dropdown value ORDER** (UI polish pass, from the owner's manual's lists, assuming the raw value counts up in that order - change it on the front panel, read the
   editor, fix `yp.ENUMS` if off): LFO cycle (Eighth, Quarter, 3 eighths, 2/4/8/16 quarters = 0-6), LFO initial phase (0/90/180/270 = 0-3), step wave total steps
   (2,3,4,6,8,12,16 = 0-6) and slope (Off, Up, Down, Up & down = 0-3), program portamento type (rate fingered, rate full-time, time fingered, time full-time = 0-3),
   AD input source (L/R, L+R, 2 mono = 0-2), sample EQ type (Peak/dip, Low shelf, High shelf = 0-2). Still numeric: pitch bend type (0-13; the manual names only
   Normal, Slow, Slow&Rev, Stop, Stop&Rev, the Up[A]Dwn[B] types and Up&Dwn12 - photograph the unit's own list: Sample > Knob 2 LIST...).
5. **Edit copies carry less than the original**: envelopes, filter, EQ, LFO, controllers and outputs are the template's defaults. If that matters, build the copy's SP from
   the source's own dump with the name / wave names / object address and the "linked to program" map (+24..+39) cleared - untested.
6. **Native load leftovers** (`a4000-native-load-findings.md`): pacing minimum, odd names, wave memory full, a failed REPLACE. Samples sent by an earlier run
   are not flagged by the assign warning (only this run's loads are known).
7. **Remote-panel delete/rename** (the `58 03` switch remote, below) was never tried, and open-loop button pushes are unsafe while the silent-unit state is unexplained.
8. Implemented but unproven: the right CHANNEL playing from its own copy of the address twins (they stay equal, that is all that was shown); restore is a series
   of guarded writes, NOT a bulk load (a bulk load back into the unit is unmeasured); sample BANKS (link/restore); aborting a bulk dump with other messages (identity
   and SDS CANCEL don't work). A program took 96 samples with no refusal - the limit, if any, is higher.

**Next steps without the unit:** the missing parameter tables as rows plus fake tests (effect blocks P2=21, 40 bytes x3 at bulk 96 - read the service manual p.39 first;
the four controllers P2=22, 4 bytes x4 at bulk 216; MIDI-channel bitmaps P2=1,2; system parameters are single reads/writes only; sample banks; stereo R wave/loop
addresses), verified later with `a4000_verify_params.py` (read-only) then `a4000_write_verify.py` on throwaways. The restore job restores whatever rows exist, so new
rows are restored for free. **Polish backlog:** remember a loaded waveform per sample, program names for empty programs on demand, remember the last program/tab, a
Device Number field in Settings (`yamaha_device_number` in config.json only today), restoring a program's ASSIGNMENTS, A5000 MIDI-B channels 17-32.

## Decisions made with the user (don't undo without asking)

Layout: BOTH program-centric (Programs | assigned samples | cards) and a Samples tab, like the S3000 editor. Aligned side-by-side card pairs pinned to equal height - they rejected
independent columns and collapsible cards after trying both. The stereo waveform is ONE display (two half-height views, no gap, markers spanning both, a faint hairline at the
seam, the "Double-click to load" hint once across the seam); progressive AND smooth loading; centre-origin ("bipolar") knob arcs in every editor with no tick mark;
S3000-style sample list rows and envelope graphs. Empty programs are hidden by default ("Show empty programs"). Edits go to the unit as you make them (throttled), with a
backup first and a one-time warning. Dialog/note text is SHORT. The user doesn't know the A4000 well: give exact front-panel steps, one action at a time.

## How to run, and the hardware tools

Settings > Sampler Type "Yamaha A4000/A5000 (experimental)", pick the ports, press Open Editor. No hardware: `uv run python tools/a4000_demo.py` (see AGENTS.md).
Hardware at the user's desk: Yamaha **A4000**, **Device Number 0**, **Bulk Protect OFF (keep it off)**, through a **PreSonus Studio 26** (`mido` port name `PreSonus Studio 26`
in and out, already saved in config, so the scripts need no flags). **The app must be CLOSED while a script runs** (two programs on one port race). Unit data is RAM only; a
power cycle clears it, and the user said nothing of value is on it (still back up first, and prefer throwaway objects). A bad state may need the user at the front panel.

| Tool | What it does |
| --- | --- |
| `a4000_discovery.py {identity \| dump OL \| dump PG --name 001 \| dump SP --name "sine wave" \| param program 001 2 0 0 3 0 \| listen 60 \| decode <file>}` | read-only probe (refuses anything but identity/dump/parameter requests and select); saves replies under `~/.akaisds/a4000_discovery/`; `--device N`, `--fill 0` |
| `a4000_verify_params.py` | read-only: every table row read two ways (request vs bulk offset) |
| `a4000_write_verify.py {backup,noop,one,sweep --scope ...}` | WRITES a value per row to a throwaway object and diffs the dump |
| `a4000_session_smoke.py`, `a4000_session_write_check.py` | read-only session smoke + scan timing; the session's own write path (program 128 / "pulse 3" / program 001 slot 0, restored) |
| `a4000_sds_probe.py`, `a4000_sds_numbering.py {send\|list\|probe}` | SDS numbering by audio fingerprint (`send` WRITES test samples) |
| `a4000_wave_probe.py "<sample>"` | read-only native wave dumps -> WAVs, fingerprints |
| `a4000_restore_check.py`, `a4000_link_probe.py {status\|cycle\|multi}`, `a4000_assign_check.py` | WRITES to program 128 + factory samples: restore round trips; object link change with dump diffs; assign/remove/restore |
| `a4000_marker_probe.py`, `a4000_marker_check.py "<name>" [--preview]` | marker geometry rules; the real tab + write path on a user sample |
| `a4000_load_probe.py`, `a4000_load_lab.py`, `a4000_app_check.py <stage>` | native load measurements; the APP's own code (Dashboard send, Samples tab, session) driven headlessly (throwaways are `T-...`) |

Offscreen GUI against the fake: build a window like `tests/test_yamaha_program_editor.py::build_window` and `.grab().save(...)`; against the real unit:
`YamahaProgramEditorWindow(QMainWindow(), controller)` with a real `MidiManager` (patch `writer_module.app_config.get_yamaha_write_warning_acknowledged` so the modal
warning doesn't block a script). A tool that talks to the unit while a window is open must wait for the session to be idle.

## Source material

The manuals are copyrighted and NOT in this repo (the owner has them). **Service manual** (61 pp): PDF pages 32-42 "MIDI DATA FORMAT" (35 = SDS tail,
identity, bulk dump + request; 36 = parameter change/request/object link; 37 = Table 1 bulk layouts; 38 = Control/Sample Parameter/Easy Edit blocks; 39 = effect
parameters, never read in detail; 40-41 = Table 2 numbering + value enums; 42 = filter types, outputs, system parameters, switch remote), 43 = implementation chart.
`pdftotext -layout` scrambles its tables - render with `pdftoppm -f 35 -l 42 -r 110 -png` and look; the tables were read by eye, so re-check any offset against
the page. **Owner's manual** (296 pp, `pdftotext -layout` works): assignment p.55-56, Easy Edit p.96-101, the effective key range p.99, UTILITY > MIDI bulk
(Bulk Protect, Device #) ~p.201, Wave Data Bulk Dump p.277-279, error messages near the end.

**Switch remote** (`F0 43 1n 58 03 <id> 00 00 00 00 00 <value> F7`): front-panel key/knob remote control, found in a third-party Ctrl panel (unlicensed - protocol
facts only, never its code). Buttons are `id` 0-18 (F1-F6 = 0-5, Audition 8, Play/Edit/Rec/Disk/Util 9-13, Knob 1-5 push 14-18) with value 64; a knob turn is
`id` 109 + knob, value 64 + steps. There is NO delete/rename opcode, so delete and rename COULD be driven open loop through the menus with fixed sleeps. Untested; a
Knob 5 push is not processed while the unit is silent.

## Protocol facts (service manual + measured; **CORRECTION** marks where the manual was wrong)

- **Messages:** parameter messages `F0 43 <type|n> 58 ...`, bulk `F0 43 <type|n> 7A ...`; `n` = Device Number (an unset one errors "Please set device number"). Reception is disabled
  while **Bulk Protect** is on. Every 8-bit data byte is two MIDI bytes (high nibble first); multi-byte values big-endian, signed = two's complement. Types: UC u8, SC s8, US u16,
  SS s16, UL u32, SL s32. Objects have 16-char names (space padded; programs are named by their number string `"001"`, so duplicate sample names are ambiguous - same trap as the
  Akai). Object type codes: Program 20, Sample Bank 17, Sample 16, Wave data 2, Sequence 19.
- **Parameter messages:** object select `1n 58 00 <name> <type>` (STATEFUL - every edit is select-then-edit, serialise it); object edit `1n 58 01 <P1..P6> <data>`; system `1n 58 02`;
  object link change `1n 58 04 <upper name> <type> <lower name> <type> <0/1>`; requests use `3n` instead of `1n` and the unit answers in the same format as the change. No checksum.
  **CORRECTION: a select gets NO reply**; the unit ANNOUNCES its current object (a select-shaped message) right before it answers a parameter request - hence the session rule that a
  value is trusted only after a fresh announce naming the right object. No settle time is needed between select and request (0-200 ms sweeps, all correct; the session keeps 30 ms).
- **Bulk dump** `F0 43 0n 7A <count 2> "LM  0474" <fmt 2> <16-byte name> <data> <checksum> F7` (`...0475` for the A5000); formats `SY PG SB SP WD SQ OL`. **CORRECTION: the byte count is
  MSB-first** (`20 00` = 4096), a dump is ONE F0..F7 holding several blocks each `count(2) span xor-checksum(1)` (XOR of the span, not a sum), only the first span carries the 26-byte
  header, a block holds at most 4096 span bytes. The object list was 2 blocks (4096 + 758). Dump request `F0 43 2n 7A "LM  0474" <fmt> <name> F7` works exactly as documented (the name for
  `OL` can be spaces; the reply names itself "Object List"). Don't rewrite `parse_bulk_dump` to the manual's wording.
- **Identity** `F0 7E <ch> 06 01 F7` -> family bytes `5A 03` = A4000 (the manual's A5000 `5B 03` was never seen). Don't rely on detection for anything but UI gating.
- **Object list** = repeated `<type byte><16-char name>`: 128 programs (`"001"`..`"128"`) then wave-data/sample PAIRS (every sample has a same-named wave object). The unit **cannot report
  free memory over MIDI**.
- **Bulk sizes:** program 408 + 56 x n Easy Edit blocks (a fresh unit has 8 empty blocks, 856 bytes, whatever the assigned count @94/95), sample bank 312 + 20 x members, sample 336, wave 72 + 2 x words.
  Program: type @0, name @2, short name @64, level @83, LFO tempo @92, reset note @93 (-1 = all), reset MIDI channel @87 (-2 = off), AD-in source = a bitmap @72.
- **`[Common]` byte 1** of a program is `0x60` with nothing assigned and `0x61` once a sample is; **every edit sets bit 0 of it** (an "edited" flag - tools mask it out when diffing).
  Assigning a sample changes only byte 1, the count and Easy Edit block 0.
- **Easy Edit** (56-byte blocks from bulk 408, request `P = [2, slot/100, slot%100, param, 0, 0]`): assigning fills block 0 (`@0` name, `@16-19` four opaque id bytes, `@20 = 0x10`, `@21`
  receive channel: 0 = channel 01, -1 = "=sample" the empty default); slots fill from the front; Level offset is block +22 (SC), one byte.
- **Sample object** parameters: request `P1=2, P2=3..34, P3` (0 for L/R pairs); read from the SP bulk at `112 + <Table 1 offset>`; wave length/addresses are UL big-endian. A sample holds a
  128-bit "linked to program" map (+24..+39, four big-endian words, bit 0 = program 001). Effective key range of an assigned sample = the sample's own range (low -1 / high 128 = "Original")
  moved by the Easy Edit shift and cut by its limits.
- **Speeds:** object list 2.3 s, program dump 1.2 s, sample dump 0.9 s, scan of all 128 programs' counts ~6.5 s (MIDI wire speed).

### Parameter tables (`src/core/yamaha_params.py`, 204 rows: 47 program, 31 Easy Edit, 126 sample)

Each `Param` ties the request address (P1..P6, Table 2) to its bulk offset (Table 1). NOT covered: the program's effect blocks, controls, MIDI-channel bitmaps, a sample's
"linked to program"/bank-member bits, stereo R wave/loop addresses (no P-numbers), system parameters (no documented bulk layout), sample banks.
Proven: read-only 959/959 agree (weak alone - factory defaults); **write-proven** with `a4000_write_verify.py`: program 128 44/44 EXACT, Easy Edit slot 0 29/29, sample "pulse 3" 86 EXACT +
32 EXTRA (real side effects) + 2 NOCHANGE. Real behaviours: writing original key/fine tune L also moves the mirrored R byte + two derived bytes; the four EQ rows update ~10 derived
coefficient bytes (rel +170..+179); the six sample controls are mirrored into the first 24 bytes of the sample block (rel +0..+23, which the manual calls reserved) - writing either
changes both; `sampling_frequency`/`wave_length`/`wave_end_address` writes are ignored on a BUILT-IN sample (accepted on a user sample - see markers); wave/loop addresses are COUPLED
(writing the wave start moves loop/length bytes and writing it back doesn't undo them). **Manual corrections:** sample `P2=66` (AEG sustain) is a 4-byte array at +151..+154 whose real
sustain is **P3=2 (+153)** (P3=0/1/3 are reserved: 8, 127, 0) - don't "fix" back; LFO reset note is SC (Table 2 says UC); sample `velocity offset`/`PEG range` are signed.

### Markers and loop rules (`src/core/yamaha_markers.py`, measured with `a4000_marker_probe.py`, proven with `a4000_marker_check.py`)

The unit keeps FOUR independent addresses (wave start, wave end, loop start, loop end) and derives the lengths. It SILENTLY IGNORES a write that would break start <= loop_start <=
loop_end <= end (or an end past the wave's size), so a change must be ordered (widen the wave, widen the loop, narrow the loop, narrow the wave). While the loop mode doesn't loop
(0/3/4/5) the loop END follows the wave end (and the loop can end up with start > end and an underflowed length - move the loop first); a loop start may sit AT the wave end (a fresh user
sample's default). A built-in sample ignores an end change. Every row we model has a duplicate "twin" right after it (payload 176-183 wave start x2, 186-193 length x2, 194-201 loop start
x2, 202-209 loop length x2 - the R-channel copy, also on a mono sample); the unit keeps the twins equal after a left write, so writing the left rows is enough. Offsets in no row of ours:
183, 190-191, 198-199, 206-207. Stereo markers: PROVEN (both channel views always agree).

### SDS facts (the Dashboard's SENDS still use SDS; receive/audio moved to the native dump)

The SDS dump request number is the sample's CURRENT list position (0-based; the 7 factory waveforms are 0-6), also after a delete (measured by audio fingerprint). A number with no sample is
answered by an SDS CANCEL. The header period is whole nanoseconds (48000 Hz arrives as 48001; `RATE_TOLERANCE_HZ = 2`). The unit trims 4 frames off a sent sample (4000 sent -> `wave_length`
3996). A stereo WAV sent through the Dashboard becomes TWO independent mono samples; a genuine stereo sample is ONE entry with a right wave name at payload @80 (`yp.is_stereo`) - over SDS it
announces the left channel and then stalls (cause unknown), which is why audio moved to the native dump. **Bulk Protect ON makes the unit CANCEL incoming SDS sends** (WAIT, then CANCEL at
packet 0) and silently ignores parameter edits - check it first when a send or a write "does nothing". SDS is MIDI-bound with a handshake per 120-byte packet (31k frames ~2 min).

### The native wave dump ("WD", `core/yamaha_wave.py`; `a4000_wave_probe.py`)

Request `F0 43 2n 7A "LM  0474" "WD" <wave object name> F7`. A sample links its left wave object by name (SP payload @64) and, if stereo, a right one (@80). A long wave arrives as
SEVERAL COMPLETE bulk messages (~4 KB each; 4070 data bytes in the first, 4068 in the rest) - `request_bulk` returns only the first, so a wave has its own collector. Each message's `data`
starts with a 2-byte block number; the first holds [Common]; its UL at data[22:26] is the word count = frames + 4 guard words (a copy of the wave's start, not audio). Audio is 16-bit
big-endian signed: frame k = the word at data[75 + 2k] of the first message (the manual's offset 72 is one byte short - trust the measurement), continuing at data[2:] of each later one.
Verified byte for byte against SDS dumps. ~620 frames/s, MIDI-bound. **Nothing aborts a bulk dump** (an identity request and an SDS CANCEL mid-stream did not): Cancel can only stop WAITING, so
the session DRAINS (stays busy) until the stream has been quiet for `drain_idle_ms` (~70 s worst case on a 2 s stereo sample).

### Writes, restore, assign (how they work)

`YamahaSession.write_parameter`: backup first (once per object per session), object proven selected by an announce, read-back must equal. Proven: 13/13 program + sample rows EXACT (signed
values, bitfields), write-back of a row's own value leaves the dump identical, Easy Edit 6/6, the real window edited a knob and a sample parameter. **Restore** (`core/yamaha_restore.py`,
`controller/yamaha_restore.py`) = a series of guarded writes of every writable row that differs, geometry rows last in `GEOMETRY_ORDER`, re-read, up to 3 passes; a SNAPSHOT of the
current state is saved first (none -> nothing written); a write the unit swallowed stops it. Proven: program 128, sample "pulse 3" with its coupled rows, Easy Edit of 3 assigned slots,
byte-identical apart from the edited flag and two unrestorable R mirror bytes (191/207). Limits: assignments are not restored; a sample restores only onto the same sample.
**Assign** = the OBJECT LINK CHANGE (`change_link`: backup once, send, ASK the unit with the link request - the answer is the verification, a change gets no reply). Measured: linking APPENDS
the sample as the next Easy Edit slot, count+1, every Easy Edit value at its default, receive channel -1 ("=sample", not the front panel's 0), the 4 id bytes = the sample's own id (@60) + 0x18,
link bit set, edited flag set; unlinking a MIDDLE slot COMPACTS (later slots keep their values), clears the bit and leaves stale bytes in the vacated last block; linking twice or an unknown
name changes nothing; unlink restores a dump byte for byte when nothing was edited. Stereo assign + Easy Edit restore round trip + remove: PROVEN.

### Native sample loading (measurements in `a4000-native-load-findings.md`)

**Proven through the app code** (`a4000_app_check.py load`): a looping mono sample (loop mode 1, loop 1000..5000, key 57, fine tune 17) and a real stereo sample, params
carried, L and R audio identical. Edits (Reverse, Normalise, Trim, Fade, Filter) on a looping mono and a stereo sample: copy created, loop/wave addresses as computed,
key/tune/rate carried, stereo kept, audio identical to the computed transform. Slice Editor + fill a program: 8 stereo slices (109 s) and 6 mono, then 40 mono slices
(export 663 s, fill 14 s), each on its own key (C1 up), one-shot, linked in order.

## Session 6: the silent unit (2026-10-07, hardware)

After native loads the unit can show "MIDI Bulk Received" and then answer NOTHING over MIDI (identity, object list, links) until a person presses OK (Knob 5):
716 s untouched, then it answered the instant OK was pressed. It is a dialog, not busyness. Reads often still work right after a load; the first LINK after loads
hung it in 5 of 6 runs (mono and stereo; the link itself DOES take effect, only the confirmation never comes), also for a sample loaded 15 minutes earlier. Factory
samples link fine, and linking at another time (nothing pending) never hung. Repeated object-list requests at a waiting unit just pop "Transmitting Object List", so
the app probes with an identity request. The Knob 5 remote is not processed while blocked. The app copes through `yamaha_sample_edit._recover_silent_link` and the
assign warning. **Open (item 1 above): what makes the first link after loads safe?**

## Working rules

Back up before any write (the session enforces it); throwaway objects first; check a licence before borrowing code (the S950 port's MIT notice is in
`THIRD_PARTY_NOTICES.md`); don't edit the pinned `s3k`/`s3ked`; no thread-per-action; no real multi-threaded stress tests. `tests/fixtures/a4000/*.syx` are REAL
captures - never regenerate them from the codec; take a new capture with `a4000_discovery.py` and add it alongside.
