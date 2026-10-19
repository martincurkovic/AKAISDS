# Multi files (S2000/S3000): where Save / Load stands, and what the next agent does

Written 2026-10-19 at the end of the session that built Save Multi, its check tool and the Load dry run. Read this first, then `dev_docs/s3000-multi-file-format.md`
(the file layout research and what is guessed) and the "Multi files" section of `AGENTS.md`. **No hardware was involved in any of this**: the owner (who has an S2000, no
S1000-family unit) tests, and nothing below is called working until they have run it. Record each run's result in the table at the bottom.

## State at a glance

| Piece | Where | State |
|---|---|---|
| Multi file codec (4096 bytes: 1024-byte header + 16 x 192-byte parts) | `src/core/akai_multi_file.py` | built, tested, **format not checked against a file the sampler wrote** |
| Save Multi (read only) | Multi tab "Save to File...", File > Save Multi to File...; `BridgeWorker.submit_export_multi`; `akai_multi_file.read_from_bridge` | built, tested against fakes. Never run on the S2000 |
| Read-only check tool | `tools/s2000_multi_check.py` (`show`, `save`, `compare REAL.m3`, `diff A B`, `plan FILE`) | built, tested offline. Never run on the S2000 |
| Load dry run (what a load WOULD write) | `akai_multi_file.plan_load`, tool command `plan` | built 2026-10-19, tested offline. Writes nothing anywhere |
| **Load Multi** | nothing | **not started on purpose**: see "Why there is no Load yet" |
| Backup All (every program + the one multi, zipped) | nothing | only discussed (see "Ideas not started") |

Tests: `tests/test_akai_multi_file.py` (codec, comparison, worker read, Multi tab button, tool, planner). The whole suite passed (3458 tests) with the dry run in.

## What Save Multi writes

`build_file(header, parts)`: the 32 wire bytes of the multi header at file offset 0, zeros to 0x3FF, then the 16 wire part blocks at `0x400 + n * 0xC0`, with three changes
(see the module docstring): each part's byte 0 written as `01`, each part's end link (`+0xBE-BF`) as `FF FF`, header bytes beyond 32 zero. The link pointer (`+0x01-02`) and "last
note played" (`+0x6D`) stay as the sampler reported them. The read is `S3kBridge.get_header_bytes("multi", 0, 0, 32)` then `("multipart", n, 0, 192)` for n in 0..15, one request
at a time. **Never read part 16 or above** (the sampler silently answers with part 15's buffer).

Guesses the check tool exists to settle: (1) what byte 0 of a part is ON THE WIRE (unknown; the DemoBridge sends 00, the one real file has 01 in the file); (2) header bytes 0-2
are passed through as read while a real file may want something else; (3) header bytes 0x20-0x3FF cannot be read over SysEx, so a multi that holds anything there loses it in our
file; (4) the link pointer as read is a sampler address, not what a disk file holds.

## Why there is no Load yet (the reasoning, so it is not re-litigated blind)

What a SysEx Load can safely do is bounded by what has been MEASURED:

- **Proven from this app** (the Multi tab writes them today and it works on the owner's unit): the multi name (`MULTINAME`), and per part `PMCHAN` (channel), `STEREO` (the panel's
  "Lev"), `PANPOS`. Program binding by a MIDI Program Change on the part's channel using the program's `PRGNUM` (`BridgeWorker._handle_program_change`, `renumber_programs`).
- **Measured by s3ked, write works, range matters**: `FX1`-`FX4`. The BYTE must be 0-204; writing 205 crashed the machine ("Internal Error - divide overflow"), the byte stuck,
  and the SysEx surface stayed dead until F8 was pressed. s3k shows the index one higher than the byte (`display_offset` 1: byte 204 is shown as 205), so judge the BYTE.
- **Nothing measured**: writing `PRIORT`, `PLAYLO`, `PLAYHI`, `OUTPUT`, `VOSCL`, `TRANSPOSE`, `PFXCHAN`, `PFXSLEV`, `PTUNOCM`. s3k has no notes on any of them. The Multi tab never writes them.
- **Cannot be done over SysEx**: writing a part's program NAME is accepted (s3ked, 2026-09-13) but the part does not then play that program, so binding has to be a Program Change. A
  load also cannot UNASSIGN a part.
- `PMCHAN` stores any value to 255 but the panel clamps in use (22 shows as "Ch 16"): a load should store 15 for anything above 15 that is not 255 (OMNI).

So the loader is not blind for the proven set, but its value is small without the rest, and the rest is exactly what is unmeasured. The dry run and the write check below close
that gap before any loader exists.

## What the owner needs to do (in order)

1. **Compare**: set up a multi with distinctive values on every part (program, channel, level, pan; priority, play range, output, transpose, FX bus/send and tune on some; an effect in
   each of FX1-FX4). Save it to disk with the sampler's own SAVE page, export it with akaiutil as `REAL.m3`, then with that multi still resident:
   `uv run python tools/s2000_multi_check.py compare REAL.m3` (app closed, no DAW open). Send the whole output. Settles guesses (1)-(4) above.
2. **Dry run**: `uv run python tools/s2000_multi_check.py plan REAL.m3` (reads the resident multi and the program list; writes nothing). Check that the plan reads sensibly: that
   each "-> new" value is what you would expect the file to say. Offline: `plan FILE.m3 --against OTHER.m3 [--programs "A,B"]`.
3. Then the write check (next section), on a throwaway multi.

## Next steps for the agent

### A. A write check for the unmeasured fields (WRITES: owner runs it, throwaway multi, app closed)

Model it on `tools/s2000_mdata_write_check.py` (read it first: backup, one write, read-back must match, restore, stop on the first surprise). One field on ONE part at a time,
in this order (least to most surprising): `PRIORT`, `PLAYLO`/`PLAYHI`, `TRANSPOSE`, `PTUNOCM`, `VOSCL`, `PFXCHAN`, `PFXSLEV`, `OUTPUT`. For each: read it, write a different value through
`bridge.set_parameter` (the same per-parameter path the Multi tab uses, NOT a whole-block `MULTIDATA` write), read it back, compare, write the original back, read again, compare.
Print PASS/FAIL per field with the values, and ask the owner to say whether the panel shows the change (add a prompt, or print the panel page to look at). Save the whole multi to a
`.m3` first (`akai_multi_file.build_file`) in `~/.akaisds/multi_backups/` as the safety net. Never write FX1-FX4 above byte 204 (the tool should refuse). Never write the pointer
bytes or `+0x6D`. Record results in the table below and in `AGENTS.md`'s Multi section.

### B. Load Multi

Only after A (and ideally after the owner's compare run). Design, following how Load Program is built (`core/akai_program_file.py`, `BridgeWorker.submit_import_program`, the
`ProgramEditorWindow._load_program_*` flow):

- **Pure part**: `akai_multi_file.plan_load(current, target, resident_programs)` already says what to write, with `evidence` per field and the FX/channel clamps. The loader writes
  exactly `plan.changes` (maybe only the evidence levels the owner has cleared) and nothing else. Add `parse_file` strictness decisions (it only checks size today).
- **Worker**: `submit_import_multi(multi_file)` / `_handle_import_multi` on `BridgeWorker`: (1) read the current multi with `read_from_bridge` (the plan's baseline), (2) save it to
  `~/.akaisds/multi_backups/<name>-<timestamp>.m3` FIRST, no backup no write, (3) write each planned field with `self._bridge.set_parameter(...)` (per field, one at a time, FX clamped to
  the byte range 0-204), (4) read the multi back and compare every written field, stop and report on a mismatch, (5) emit a result signal. Add a session flag like
  `program_import_blocked` if a read-back fails.
- **Program binding** is separate from the field writes: for each part whose program changed and is resident, send a Program Change on the part's (new) channel with that program's
  `PRGNUM` via the existing `_handle_program_change` path (it runs `renumber_programs` once per session first: note that this WRITES PRGNUMs). Programs not resident are only listed.
- **UI**: Multi tab "Load from File..." next to Save, File menu entry, a confirmation that lists the changes grouped by evidence (UNPROVEN ones called out), the non-resident programs
  and the backup path; default answer No. Disabled in demo mode (like Load Program), on an S1000 (no multi; the tab is hidden), and while `_hardware_busy_ui` or a Dashboard transfer
  owns the wire (the same gating Save Multi has: `_sync_save_multi_enabled`).
- **Tests**: against `FakeBridge`-style fakes as in `tests/test_akai_multi_file.py`: only planned fields are written, FX clamped, backup written before the first write, a failing
  read-back stops, no pointer/`+0x6D` byte ever written, nothing happens in demo mode.
- Update `AGENTS.md` ("Multi files") and this file; keep "NOT tried on hardware" until the owner has.

## Hardware-safety rules that apply here (from `AGENTS.md`, "Real-hardware safety")

- Close DAWs and MIDI monitors before any tool or the app touches the unit: an echoed reply is executed as a write.
- One request outstanding at a time; accept only the reply that names what was asked; never send into silence.
- Any write path needs a backup first and a read-back that must match.
- Never read byte registers 6-9. Never write FX bytes above 204. Never read multi parts above 15.

## Ideas not started

- **Backup All** (every program as `.p3` + the multi + a manifest, zipped; read-only): see the discussion in the 2026-10-19 session. Cheap once the multi codec is trusted; restore would
  loop the proven program-load path. Not requested yet.
- **Load a `.s3`** exists (Samples tab, `akai_sample_file.restore_plan`) and is the model for "send, then write fields back one by one".

## Hardware findings so far (2026-10-19, the owner's S2000, OS 2.00 = RSTAT version 17.00, via a PreSonus Studio 26)

- **Cabling and echo are clean**: `tools/midi_echo_check.py` got exactly one STAT per request and nothing else, with no DAW running.
- **"Nothing in memory" is the power-up state, not empty**: the unit holds the factory `TEST PROGRAM` (1 keygroup, PRGNUM shown as 1) and four 256-word 44.1 kHz waveforms
  (`SQUARE`, `SAWTOOTH`, `PULSE`, `SINE`, all SPTYPE 0). That is what the repo's test fakes imitate. The resident multi is named `MULTI FILE` (FX1-FX4 all 0).
- **A mistake of mine, corrected (so nobody repeats it)**: the first hardware run made every part look like a copy of the multi header. That was NOT the sampler: `get_header_bytes`
  defaults to selector 0 (the multi header), and a part is `RMULTIDATA` selector 1 (`s3k.bridge._REGION_SELECTOR["multipart"]`); `read_from_bridge` left the selector out. The first
  diagnosis ("the multi has no parts yet") was wrong, and the owner's panel report (part 1 = TEST PROGRAM, parts 2-16 empty on channels 2-16) is what exposed it. Fixed:
  `PART_SELECTOR = 1`, a regression test that every part is asked for with selector 1, and a guard kept on purpose (a part whose first 32 bytes equal the header is refused: that is
  exactly what a missing selector produces). **Any new code that reads a multi part through `get_header_bytes` must pass `selector=1`**; `get_header("multipart", i)` does it itself.
- **With the selector fixed, the power-up multi reads exactly as the panel shows it**: name `MULTI FILE`, FX1-FX4 = 0; part 1 `TEST PROGRAM` on channel 1; parts 2-16 blank, channels 2-16
  (stored 0-indexed). Every part has the defaults in the research note: polyphony `1F`, priority normal (1), play range 24-127, output `FF`, level 99, pan 0, `VOSCL` 50, transpose 0,
  FX bus 0, FX send 25, tune 0.
- **Guess (1) settled: byte 0 of a part on the wire is `01`**, as in the one real file (so the writer's "set byte 0 to 01" changes nothing on this unit). **Header bytes 0-2 on the wire are
  `00 00 00`** (whether a real FILE wants the same is still for `compare`).
- **The end link on the wire is not always `FF FF`**: part 1 (assigned) reads `00 60`, parts 2-16 (unassigned) `FF FF`. Our file writes `FF FF` for all, as a third-party note says a disk
  file must have; `compare` will say what the sampler's own file holds. The link pointers (`+1,+2`) read `0C 60` (part 1) and `F4 8F` (empty): close to the real file's `0C 90` / `F4 BF`,
  so they are sampler addresses that move with its memory, as assumed. "Last note played" (`+0x6D`) reads 0 on every part.

- **The owner's S2000 has no multi-output board and no FX card.** So `OUTPUT`, `VOSCL`, `PFXCHAN`, `PFXSLEV` and `FX1`-`FX4` cannot be observed to DO anything on it (the panel may not even show
  them). A write check there can only read the byte back, not hear or see an effect. Consequence for the plan: a first Load Multi should write only what the owner's unit can verify on the
  panel - name, channel, level, pan, priority, play range, transpose, tune - and list the FX/output fields as "not loaded (needs a unit with the cards to test)" until someone with the
  hardware has tried them. `plan_load` already tags each field with its evidence; add a "can't be verified here" tier for these.
- **First live field checks (the owner changed the panel, I read it back, nothing written from here)**: after the owner set part 3's channel to 10 the read showed part 3 channel 10 and
  nothing else different from the previous read, so `PMCHAN` (stored 0-indexed, 9) is decoded and positioned correctly; part 2's level 80 and pan -20 (set earlier) likewise read back
  as `STEREO` 80 / `PANPOS` -20. Program names bind by name as expected: parts 1-4 read `TEST PROGRAM`, `PROG A`, `PROG B`, `PROG C`.

- **All four panel-visible unmeasured part fields are decoded and positioned correctly (owner set them on part 4, read back with nothing written from here)**: priority high -> `PRIORT` (+0x12) 2;
  play range C3-C5 -> `PLAYLO`/`PLAYHI` (+0x13/+0x14) 60/84; transpose +5 -> `TRANSPOSE` (+0x4B) 5; tune +20 cents -> `PTUNOCM` (+0x73) 20. Every other part and field was unchanged. These are READ
  confirmations only: whether WRITING them is safe is still the write check's job.
- **A part record mirrors its PROGRAM's header, so unmodelled part bytes change when a program is edited.** Part 3 differed from the default at `+0x19` (60 vs 80): that is `PRLOUD`, the program's own
  loudness, which the owner had changed on `PROG B` (the program header's `+0x19` reads 60 for `PROG B`, 80 for the others). Consequences: (1) a Load must never write the unmodelled bytes (it
  doesn't plan to: `plan_load` counts them under "not loaded"); (2) `compare REAL.m3` can show differences at program-derived offsets if a program changed after the multi was saved to disk,
  so save the multi to disk right before exporting it and do not edit programs in between; (3) the panel's "Level" for a part (`STEREO` +0x17) and the program's loudness (`PRLOUD` +0x19)
  are different bytes.
- **The earlier no-reply** (one request timed out while the owner was editing on the panel) did not recur once the sampler was idle on a normal page: leave it idle before running a tool.
- **Program values read back too** (existing decode, now checked on the unit): `PROG C` keygroup 1 range A#3-G8 (notes 70-127), zone 1 tune raw 1331 = +5.1992 semitones, which is the panel's
  "05.20" semitones.cents (20 cents = 0.20 semitone; 5.2 x 256 = 1331.2), and every other zone and program unchanged.

## Results from the owner's unit (fill in)

| Date | Command | Result |
|---|---|---|
| 2026-10-19 | `midi_echo_check.py` | clean (5/5 single STAT, nothing stray) |
| 2026-10-19 | `s2000_check.py snapshot` | 1 program `TEST PROGRAM`, samples SQUARE/SAWTOOTH/PULSE/SINE |
| 2026-10-19 | `s2000_multi_check.py save` on the power-up multi | first run: every part read as the header (my missing selector, fixed); second run: reads as the panel shows, defaults match the research note |
| 2026-10-19 | `s2000_multi_check.py plan` of the saved file over its own source | 0 changes, 197 loadable fields match |
| 2026-10-19 | panel changes read back (part 2 level/pan, part 3 channel) | decoded and positioned correctly |
| 2026-10-19 | panel changes on part 4 (priority, play range, transpose, tune) read back | all four decoded and positioned correctly |
| 2026-10-19 | program values (keygroup range, zone tune) read back | correct (range 70-127, zone 1 tune +5.1992 st = panel 05.20) |
| | `s2000_multi_check.py compare REAL.m3` | |
| | `s2000_multi_check.py plan REAL.m3` | |
| | write check (A), per field | |
