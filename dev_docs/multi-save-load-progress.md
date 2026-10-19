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

## Results from the owner's unit (fill in)

| Date | Command | Result |
|---|---|---|
| | `s2000_multi_check.py compare REAL.m3` | |
| | `s2000_multi_check.py plan REAL.m3` | |
| | write check (A), per field | |
