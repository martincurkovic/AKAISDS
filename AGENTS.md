# Agent notes for AKAISDS

Cold-start context for AI agents picking up this repo. Purpose: stop you
from "fixing" things that look like bugs but are actually deliberate,
hard-won corrections. Human-facing docs: `README.md`, `BUILDING.md`,
`TESTING.md`, `CONTRIBUTING.md` - not repeated here.

## What this app is

Two independent PySide6/Qt windows, both launched from `src/main.py`,
swapped via a `&Window` menu action (only one ever shown at a time):

- **Transfer Dashboard** (`ui/dashboard.py`, `ui/main_window.py`,
  `controller/sampler_controller.py`) - sends/receives samples to Akai
  S1000/S2000/S3000 or generic MIDI SDS devices over SysEx. What the
  README documents.
- **Program Editor** (`ui/program_editor_window.py`,
  `core/program_editor_bridge.py`) - edits a program/keygroup/multi's
  *parameters* on an S3000-series sampler over SysEx. Opened via the
  Dashboard's Window menu. Most of this file is about this window.
- **S900/S950 Program Editor** (`ui/s950_program_editor.py`, `ui/s950_samples_tab.py`) - a separate,
  smaller window for the Akai S900/S950, opened by the same Open Editor button when that Sampler Type
  is selected. See "Akai S900/S950 support".

## Third-party deps - read the notes, don't edit in place

`s3k`/`s3ked` are pinned external deps (`pyproject.toml`, `s3ked` on a git
rev; `s3k` comes with it), living in `.venv/lib/python3.12/site-packages/`,
not part of this repo:

- `s3k.bridge.S3kBridge` - the real MIDI/SysEx bridge to hardware.
- `s3k.params` - the parameter registry (`p.lookup(name, region)`).
  `Parameter.notes` has dated hardware-measurement notes - **read these
  before trusting a field's declared range/meaning**; several past bugs
  came from trusting the Akai manual's prose instead of what was measured.
- `s3ked.demo.DemoBridge` - duck-typed fake sampler, deliberately
  reproduces real hardware quirks, not just canned values.

If something here looks wrong, raise it upstream - don't edit the
dependency.

**Three fields where this project's own hardware measurement overrides
`s3k.params`' own notes** (confirmed in front of the user - don't "fix"
these back without re-measuring):

- `K_FREQ` (keygroup filter key-tracking): declared `-30..99`, measured
  `-24..+24`. The widget already uses the narrower range.
- `B_PTCHD` (pitch-bend-down): declared `0..12` (asymmetric with
  bend-up's `0..24`), measured `0..24` symmetric - WIDER than declared, so
  a raw write above 12 fails the pinned dependency's own range check.
  `core/program_editor_bridge.py`'s `_HARDWARE_RANGE_OVERRIDES`/
  `_lookup_for_write` patches a corrected `Parameter` copy in front of
  every write.
- `LFO2TRIG`: declared raw `0..255`, no enum. Measured: a plain boolean.
  `lfo2_trig_combo` just narrows to Off/On - no override machinery needed.

## Developing without hardware

`AKAISDS_DEMO_SAMPLER=1` → `program_editor_bridge.connect()` returns a
`DemoBridge`. `SamplerController` has no demo mode of its own, so
`ProgramEditorWindow._fetch_demo_sample_audio` substitutes real audio from
`tests/test_audio.wav` (a ~0.7s mono chord-stab fixture) instead of a real
receive, paced with real `time.sleep()` so the progress bar/timing feel
genuine. Falls back to a synthesized tone if that fixture's missing.
`AKAISDS_DEMO_INSTANT=1` skips the pacing entirely (fast UI iteration).

**Yamaha A4000/A5000 demo (no hardware)**: `uv run python tools/a4000_demo.py` opens the real Dashboard on `core/demo_a4000.FakeA4000` (Sampler Type Yamaha,
fake MIDI ports, Settings unusable) with three demo samples that have real audio - `DEMO LOOP` (mono, loops), `DEMO STEREO`, `DEMO BREAK` (four hits, for the
Slice Editor); `--editor` opens the Program Editor straight on the Samples tab, `--smoke` (with `QT_QPA_PLATFORM=offscreen`) is a headless self-check. Double-click a
waveform to load its audio; edits and slices create new samples on the fake. Sends are paced at real MIDI speed (a stereo copy takes ~10 s) - same as the unit.

## `BridgeWorker` - read before touching `core/program_editor_bridge.py`

`S3kBridge` is unsafe for concurrent calls on one connection. A
pre-`BridgeWorker` version spun up a fresh `QThread` per UI action -
crashed real hardware (`QThread::~QThread()` calling `qFatal()` when a
stale thread was still mid-call during GC) and interleaved SysEx frames on
the wire. Confirmed from an actual macOS crash report.

Fix: `BridgeWorker` is ONE persistent `QThread` per editor lifetime,
processing a queue strictly one at a time. "Current state" requests
(keygroups, sample/multi detail) coalesce - a queued-but-not-yet-started
duplicate gets dropped. Writes/Program Changes never coalesce. **Don't go
back to thread-per-action** - add a `submit_*()`/`_handle_*()` pair to
`BridgeWorker` instead.

Tests: `process_pending()` drains the queue synchronously (no real
thread) in `test_program_editor_bridge.py`; window-level tests use a real
thread + `wait_until_idle()`. Any test constructing `ProgramEditorWindow`
directly must call `editor._worker.stop(); editor._worker.wait()` in
teardown or hit `QThread: Destroyed while thread is still running`.

## Akai S1000 support (`core/s1000_bridge.py`) - written without hardware

The Settings "Sampler Type" combo has four entries (`core/sampler_models.py`, alphabetical): "Akai S1000" (`akai_s1000`), "Akai S2000/S3000" (`akai_s2000_s3000`), "Akai S900/S950 (experimental)"
(`akai_s900_s950` - a different protocol, see its section), "Generic SDS" (`generic`). Before the S1000 the S2000/S3000 was persisted as plain `"akai"`; that exact legacy string is still accepted
everywhere (`sampler_models.normalize`) and `app_config.ensure_defaults_saved()` rewrites it at startup (an unrecognised value from a newer version is left alone). `"akai"` also remains the PROTOCOL
FAMILY name - a different thing from the saved selection. The S1000, S2000 and S3000 answer an identity/status request identically, so the model is a user setting, never detected.
`SamplerController.device_type` is the PROTOCOL FAMILY (`akai`/`generic`) every transfer path branches on (an S1000's SDS/RSTAT/list traffic equals an S2000/S3000's); the full choice is
`SamplerController.sampler_model`. Only the Program Editor cares about the S1000/S2000+ split.

**Why a separate bridge**: `S3kBridge.get_parameter`/`set_parameter` use the S3000-only byte-addressable header ops (0x27-0x38); the S1000 stays silent (a real user's log: lists worked, every
`get_parameter` timed out at 2.0 s). It only has whole-block RPDATA/RKDATA/RSDATA + PDATA/KDATA/SDATA (0x06-0x0B, nibbled), so `S1000Bridge` does read-modify-write of whole blocks behind the same
duck-typed surface (`program_editor_bridge.connect(midi_manager, sampler_model)` picks it). The S3000 header layout is a superset of the S1000's at the same offsets (program 0-71, keygroup 0-148, sample
0-140), so `s3k.params` is reused for those fields (`_S1000_BLOCK_SIZES`); anything past them reads as a neutral zero and refuses writes, so `BridgeWorker`'s fixed field lists work unmodified.
**Never assume the block length** (the spec says "about 150 bytes"): the adapter caches the length the device sent, patches in place, writes back that same length, and logs every raw block (hex) at DEBUG -
ask a real S1000 user for `~/.akaisds/akaisds.log`. Blocks are cached 1.5 s (~50 fields = one fetch) and invalidated by every `program_list`/`sample_list`/`delete_*`, and by raw frames passed through
`S1000Bridge.send_and_receive` (else the reload after a duplicate shows the old `GROUPS`).

**UI gating** (`ProgramEditorWindow._apply_s1000_gating`, one-way at construction - a model change in Settings while the editor is open closes it): hides Modulation (both tabs), LFO2, Portamento, LFO1
shape, Bend down, Resonance and the S3000's 4-stage ENV2 grid/graph; Multi tab hidden/never loaded. **ENV2 on an S1000 is a plain ADSR** (`ATTAK2`/`DECAY2`/`SUSTN2`/`RELSE2`, same KDATA block as ENV1's),
so the Envelope 2 card gets an ENV1-style graph + 4 knobs (`_build_s1000_env2_adsr`, loaded in `_on_detail_loaded`). Gotcha: hiding widgets in a not-yet-shown window leaves nested layouts' size hints stale
(the swapped card stayed 406px tall vs 225) until its layout is `invalidate()`d/`activate()`d before `equalize_card_heights` re-measures. **The S1000 has ONE bend field** (`B_PTCH`, 0-12 st; the S3000 splits
it into increase 0-24 + `B_PTCHD` at offset 73, past the S1000's block), so the combo is relabelled "Bend range". Other narrower ranges: polyphony 1-16 (S3000 1-32), keygroup note range and sample root note 24-127.

**S1000 controller routing is editable** (its fixed equivalent of the mod matrix - `ProgramEditorWindow._build_s1000_controller_cards`): program-tab "Controllers" grid (Loudness/Pan/Pitch/LFO1
depth/rate/delay x Velocity/Key/Pressure/Modwheel), keygroup-tab "Controllers" (Filter freq, Pitch, Loudness, Env 2 level x Velocity/Pressure/Envelope 2; `E_FREQ` is the filter envelope's depth) and
"Envelope response" (V_ATT/V_REL/O_REL/K_DAR) cards; the hidden LFO2 card is reused as the **Pan LFO** (`PANRAT`/`PANDEP`/`PANDEL`). **Gotcha that nearly broke all of it**: `s3k.params` declares most of
these fields "Not used - fixed value in the specification", range **0..0** (the S3000 spec); the S1000 spec lists "+/-50". Left alone, `encode_field` refuses every non-zero write AND `decode_field`
(sign-extends only when the DECLARED range goes negative) reads a stored -20 back as 236. `S1000Bridge` applies `_S1000_RANGE_OVERRIDES` (corrected copies via `s1000_param`, by name+region, never editing
the dependency) on every read, write and `get_header`. **Don't add a new S1000-only field without checking its declared range in `s3k.params` first.** The grids reuse `_ModMatrixGrid` (zebra rows + column
separators behind a `QGridLayout`) with stretch-1 data columns (the S3000 cards hug their content) and a per-grid label column; `fontMetrics()` is read at construction, which works because `apply_to_app` runs
before any window (a preview script that skips it renders the dark zebra stripes black on a light window). These fields are only READ for an S1000 (`BridgeWorker`'s
`extra_program_fields`/`extra_keygroup_fields`). `V_LOUD` deliberately has no grid cell (the Velocity knob in Volume, Pan & Velocity already covers it).

**Remaining gaps (audited against the spec, 2026-10-03)**: not in either model's UI - `OUTPUT`/`STEREO`, `PLAYLO`/`PLAYHI`, `OSHIFT`, `TEMPER`, `KXFADE`/`VXFADE`, `KGTUNO`, per-zone `VZOUT`/`VSS`/`VFREQ`.
The Samples tab edits loop 1 only (the S1000 has 8 loops; `SALOOP` says which plays). Drum-trigger (`DDATA`) and misc/MIDI-channel (`MDATA`) blocks have no UI.

**The S1000 spec's name rule is destructive, so renames are guarded**: a `PDATA`/`SDATA` whose name matches a DIFFERENT resident program/sample deletes that one first. Duplicate Program/Sample, and renames
(`_confirm_rename_program`, `_commit_program_name`, `_confirm_rename_sample`), refuse a clashing name on an S1000 only (on an S2000/S3000 a duplicate name is ambiguous, not destructive). The name field's
`editingFinished` fires on any click-away, so on an S1000 an UNCHANGED name writes nothing (every write re-sends the whole block); the baseline is `_program_name_on_sampler` because
`_on_program_name_typed` overwrites the list item's text live. `FakeS1000` models delete-on-clash (`deleted_by_name_clash`) and treats a rewrite under an item's OWN name as a plain replace - an assumption
the spec doesn't state and every editor write depends on: **the first thing a real S1000 must confirm.**

**Duplicate Program/Keygroup and "create program from slices" are ENABLED** (same `PDATA`/`KDATA` clone flows as the S2000/S3000, `BridgeWorker._handle_create_*`) so the first S1000 tester can find out whether
they work (KDATA for the new keygroup first, THEN PDATA with `GROUPS+1`, matches the spec's "GROUPS must be correct" - reading, not measurement). Clones are sent at the block's real length
(`build_pdata_request`'s "192 bytes" docstring is S3000-specific). `TransferDashboard.open_program_editor` shows a one-time experimental warning.

**S1000 memory layout and DELK (measured 2026-10-05/06 from tester logs; the full logs and analysis are in `dev_docs/s1000-delk-findings.md` - read it before touching delete/create flows)**:
programs, keygroups and sample headers are 150-byte blocks in ONE linked memory area; bytes 1-2 of a program block (`FIRSTKG`) and of a keygroup block (`NXTKG`) are ABSOLUTE addresses the sampler
assigns (the last keygroup keeps whatever terminator we sent - a clone's carries its template's stale one). **DELK is a plain linked-list unlink** (`prev.NXTKG = victim.NXTKG`): no memory compaction,
no `GROUPS` update, and a PDATA with the smaller `GROUPS` is REJECTED (`REPLY` error 01) - no DELK + PDATA sequence reaches a consistent state, and a stale `GROUPS` made the next "add keygroup" link
into ANOTHER program. So: **no DELK anywhere in create-from-slices** (`_handle_create_program(first_keygroup_only=True)` clones only keygroup 0 on an S1000, `_create_program_from_slices` refuses if the
clone has more than one - don't reintroduce a DELK), and **standalone Delete Keygroup on an S1000 is DISABLED (`s1000_bridge.KEYGROUP_DELETE_SUPPORTED = False`) after TWO failed real-sampler tests -
don't re-enable it and don't try variants blind; get a measurement that explains the rejection first.** The dead path (`BridgeWorker._delete_keygroup_s1000`: snapshot, DELK, rewrite `GROUPS`, compare
keygroup CONTENT, block for the session on a mismatch) is still in the code. The only untested idea is the REBUILD (see Program files below; `KEYGROUP_DELETE_BY_REBUILD`, OFF). Duplicate Program/Keygroup
still send the template's stale `NXTKG` (never failed in the logs; rewriting it to `FIRSTKG + 150*(n+1)` is a held-back hypothesis).

**Known/untested, for the first real-S1000 report**: whether re-sending a block under its own unchanged name is an in-place replace (the spec says a duplicate name "should be avoided" - read as a name
colliding with a DIFFERENT item); whether `LOOPAT1`/`LLNGTH1`/`STUNO`/`SBANDW` have the S2000's semantics; the sample edit actions (Trim/Reverse/...) write 8 header fields back through this adapter; the
editor's SDATA/PDATA/KDATA replies also reach `SamplerController` on the shared port ("unexpected SDATA"/"unrecognised Akai message" log noise, not a bug unless a Samples-tab audio receive is mid-flight);
`PRGNUM`'s +1 display offset (same `s3k.params` entry).

**`core/demo_s1000.py`'s `FakeS1000`** fakes the MIDI PORTS (not the bridge's Python surface, unlike s3ked's `DemoBridge`): the real `S3kBridge` + adapter run on top, and it ignores 0x20+ ops like the real
machine (a stray S3000 op times out and lands in `ignored_ops`). `AKAISDS_DEMO_SAMPLER=1` with the S1000 model uses it. Its blocks are 150 bytes with `0xA5` junk past the spec fields (tests also try
72..192) so wrongly-read or dropped trailing bytes are caught.

## Program files: Save/Load as `.p1`/`.p3` (Programs tab, S1000 and S2000/S3000) - written without hardware

Programs column Save... / Load... buttons (and context menu). Akai disk format: 150-byte header + N x 150-byte keygroups (`.p1`, S1000) or 192 + N x 192 (`.p3`, S2000/S3000), N at header byte 42.
`core/akai_program_file.py` (pure codec) + `BridgeWorker.submit_export_program`/`submit_import_program` (`program_exported`/`program_imported` + `*_failed` signals) +
`ProgramEditorWindow._save_program_to_file`/`_load_program_from_file`. User decisions: Akai file format (files from other tools work), **samples are NOT saved** (zones reference them by NAME; the Load
confirmation lists the ones not resident), lives in the Program Editor.

- **No parameter list is needed**: the blocks are what RPDATA/RKDATA return, so every byte (modelled or not) round-trips. Don't "decode" blocks into fields for this.
- **Pointer bytes** (1-2 of the header = FIRSTKG, of each keygroup = NXTKG; also per-zone `SBADD`, `LVXF/HVXF`, `TPNUM` - the spec calls them "internal use") are addresses the SAMPLER assigns. A file holds
  file-relative stand-ins (150, 300, ... - checked against the S1000 spec's defaults and two open-source tools: `header + N x block == file size`, no extra file header, on 2000+ real programs);
  `build_file` rewrites them on save. Whether a sampler honours the pointers of an incoming PDATA/KDATA is UNKNOWN (measured: an appended keygroup keeps the NXTKG it was sent; an S1000 walks the chain by
  stored pointers). So **`BridgeWorker._import_program` never sends a pointer the sampler didn't hand us**: after the create PDATA (the one write that must carry the file's) it reads the new program back and
  **stops before any keygroup if its FIRSTKG equals an address another program uses**; keygroup 0 goes out with the dummy slot's own NXTKG, each append with the previous slot's terminator, every GROUPS+1
  PDATA with the sampler's current FIRSTKG; each append's link is checked for collisions; then a full read-back compares the new program with the file (ignoring `_IMPORT_*_INTERNAL` bytes) and every
  other program with the pre-load snapshot (`_s1000_snapshot`, ~60-read budget). Any stop/mismatch raises `_ImportSafetyError` and sets `program_import_blocked` for the session. The log has `program load:`
  INFO lines (FIRSTKG sent vs held, keygroup placement, NXTKG and zone SBADD vs the file's) - ask a tester. **No DELK**; a failed load can leave a partial program (the message says so), never deleted
  automatically. The duplicate flows still send the template's pointers (never failed; same unknown).
- **Load = a NEW program** through the duplicate flow's order (PDATA GROUPS=1, KDATA 0, then KDATA + PDATA(GROUPS+1)). A name matching a resident program is never sent (PDATA would delete it) - the window prompts
  for another. A `.p3` is refused on an S1000 setting. A `.p1` on an S2000/S3000 is CONVERTED after a confirmation (`akai_program_file.convert_s1000_to_s3000`): SysEx takes raw blocks (the S2000 only converts
  from disk), so the app keeps the S1000 bytes (program 0-71, keygroup 0-148) and fills the S2000-only ones (program 72-191, keygroup 149-191) with `S3000_PROGRAM_TAIL`/`S3000_KEYGROUP_TAIL` - bytes MEASURED in
  an unedited program on a real S2000. **Proven only mechanically** (a `.p1` made by cutting a real program's blocks to 150 bytes converts, loads and comes back byte-identical); never run on a GENUINE S1000
  program, and the sampler's own parameter conversion is NOT replicated (S1000 "fixed controller" fields are carried over but the S2000 may ignore them) - the confirmation says so, keep that wording.
  Disabled in demo mode like Duplicate Program (DemoBridge has no add-program primitive); Save works there.
- **S1000 Delete Keygroup by REBUILD (`s1000_bridge.KEYGROUP_DELETE_BY_REBUILD`, OFF until a real S1000 has run the load above)**: `BridgeWorker._delete_keygroup_s1000_rebuild`: save a `.p1` backup
  (`PROGRAM_BACKUP_DIR`, `~/.akaisds/program_backups`; no backup, no change) -> drop the keygroup on the computer (GROUPS-1) -> load it through `_import_program` under a TEMP name (`-TMP`; never the
  original's, so delete-on-name-clash can't fire) -> only after its checks pass DELP the original, compare the other programs by name -> rename the copy to the original name. Every step leaves a complete
  program; a failure before the DELP says the original was not changed, one after it names the temp program and the backup. The program ends up LAST in the list (`program_rebuilt`, the window selects it).
  No DELK, no name-collision override. Takes the same `program_import_blocked` session block; when on it takes precedence over the dead DELK path. Unmeasured: DELP on a real S1000 (whether others'
  addresses move), free room for a second copy. Tests: `tests/test_s1000_keygroup_delete_rebuild.py`.
- **Fixed on the way**: `_on_samples_loaded` reset the program list to row 0 after EVERY program reload, undoing "select the new program" for Duplicate/Load/rebuild; it now selects row 0 only when nothing is selected.
- Tests: `tests/test_akai_program_file.py` (codec + worker, incl. a FakeS1000 round trip) and `tests/test_program_editor_window_program_files.py`. **The file layout and pointer values are NOT yet checked
  against a file from another tool** - `tests/akai_program_file_test_plan.md` lists every guess and the real-hardware steps.

## Akai S2000/S3000: program placement, create-from-slices and `.p3` files (MEASURED on a real S2000, 2026-10-07)

- **A new program does NOT land at the end of the list.** The sampler keeps its program list ordered by `PRGNUM`; a clone carries its template's
  number, so it sorts right after the template - BETWEEN two programs once their numbers differ (`renumber_programs`). A PDATA addressed "one past the
  end" creates it there, so **never address the next KDATA/PDATA at `len(list)`**: `BridgeWorker._locate_new_program(names_before)` re-reads the list
  and finds where it went (callers already refuse a name that is resident). The old assumption wrote a whole create-from-slices run into the wrong
  program (it overwrote that program's keygroup 0 and filled it with the slices). The same shift also hit `_import_program`'s post-load check (it compared
  the other programs with the pre-load snapshot BY INDEX, so the program pushed down a place looked "changed by the load" and loading was disabled
  for the session): it now maps `index -> index + 1` past the new program. `_handle_create_program` also reads the template's keygroups BEFORE the
  first write, because the insert can shift the template too.
- **Proven on the S2000** (`tools/s2000_check.py {snapshot|create|p3|multi}`, `tools/s2000_slices_check.py`): creating a program mid-list (new index
  reported correctly, every other program byte-identical afterwards); multi-keygroup (4) clone; `.p3` Save (384/960-byte files, saving twice is
  byte-identical) and Load (1 and 4 keygroups, equal to the original apart from the pointer bytes, others untouched); and the whole Slice Editor path
  (`ProgramEditorWindow._export_slices` + `_create_program_from_slices`: 4 slices over SDS in 44 s, program created in 6 s, keys 36..39, zone 1 = its slice).
- **The sampler recomputes the pointers itself** (`program load: new program N FIRSTKG sent c000, sampler holds 8460`): the "good case" in
  `tests/akai_program_file_test_plan.md`. Zone `SBADD` read back identical to the file's for a program saved from the same sampler (not yet checked for a
  file from another disk).
- A tool that talks to the bridge while a `ProgramEditorWindow` is open must wait for `window._worker.is_idle()` first (S3kBridge is single-caller).

## Akai S900/S950 support (written without hardware)

A DIFFERENT protocol from every other Akai entry, not a variant of the S1000 family: device byte `0x40` (not `0x48`), function codes 0-11, every 8-bit value as TWO MIDI bytes, 10-char plain-ASCII
names, XOR checksums, and a sample dump that is ONE SysEx (header + all blocks + a single `F7`) with 4-byte handshakes (`F0 7E code F7`) - the standard 6-byte SDS ACK is silently ignored.
Plan and staging: `dev_docs/s950-support-plan.md`. **Nothing here has been run against hardware**; every unverified fact and guess is listed in `tests/s950_test_plan.md` ("What is a guess").
Ported from [s950tools](https://github.com/diemonster/s950tools) (MIT - see `THIRD_PARTY_NOTICES.md`; keep the notice when porting more); its comments say which behaviours were hardware-verified.
`dxzl/akai-s950` has NO licence, so don't copy from it.

- `core/s950_sysex.py` (codecs DB/DW/DD/TB/SW, framing, catalog, SPRM, sample dump, 16->12-bit conversion) and `core/s950_program.py` (PRGM: 76-byte header + 1-31 140-byte keygroups). Messages are
  the bytes BETWEEN F0 and F7, like `core/akai_sysex.py`. Only the program HEADER offsets are pinned by real captures (`tests/test_s950_program.py`); SPRM and keygroup offsets are as good as
  s950tools' copy. Unmodelled bytes are kept in `raw` and written back untouched (`raw` is excluded from dataclass equality). There is NO delete opcode - don't invent one.
  **`Keygroup.to_bytes`/`Program.to_payload` re-encode a field only if it no longer matches what `raw` holds** (else a NUL-padded name would silently come back space-padded) - don't go back to
  unconditional re-encoding.
- Fake: `core/demo_s950.py` `FakeS950` on rtmidi-style ports; its docstring separates s950tools' hardware notes from what is GUESSED. `tools/s950_demo.py` opens the real
  Dashboard on it (Settings doesn't work there). `tests/test_s950_transfers.py` drives the real engine + controller against it over a fake MidiManager delivering replies asynchronously.
  **Tests that repopulate the hardware list must flush deferred deletes** (the `dashboard` fixture does): `QListWidget.clear()` defers deleting row widgets, which otherwise fire inside another
  test's nested event loop (`_wait_for_any_signal`) and segfault.
- Sampler Type "Akai S900/S950 (experimental)" (`akai_s900_s950`) has its OWN protocol family `"s950"` (`sampler_models.FAMILY_S950`) - not "akai", not "generic". **Nothing may fall through to the
  akai/generic branches for it** (they'd put S1000-family or standard-SDS bytes on the wire). `SamplerController` delegates the S950 entry points (refresh, send_file_queue, receive_samples, rename,
  sample info, cancel, incoming SysEx) to `controller/s950_transfers.py`'s `S950Transfers` (built lazily, reports through the controller's signals) and REFUSES the rest via `_s950_not_supported`
  (delete, legacy per-file/by-number senders). Add a new S950 path by delegating, never by letting it reach the Akai code.
- `S950Transfers` rules (from s950tools' hardware-verified comments): ONE operation at a time; an RCAT reply is the "device ready" barrier before every upload (a stale ACK from the last dump
  satisfies "any reply" and green-lit a second dump mid-bookkeeping); uploads are open loop - one SysEx, wait out the wire time (3125 B/s + 10%), listen for NAKs, and ALWAYS go to an EMPTY slot
  (overwriting triggers a NAK storm; the boot "TONE" placeholder counts as empty), then name the sample by SPRM read-modify-write; a receive streams a 4-byte ACK every 50 ms from just after the RSD
  (the unit stalls between blocks and `sysex_received` only surfaces whole messages) and ignores a header-only message the unit sometimes flushes first. Names are uppercased, 10 chars, made unique
  (zones find samples by name). The Transmission Settings' bit depth is ignored (always 12-bit); stereo is averaged to mono (left only with "mono"); the rate is clamped to the dump header's
  2-65.5 kHz (what the hardware plays is unconfirmed: S950 7.5-48 kHz, S900 40 kHz). Received rate prefers SPRM's exact Hz over the header's whole-nanosecond period (44100 would come back 44099).
  Root note/detune are shown as unavailable (SNOMP's direction is only inferred).
- The Dashboard needs a MIDI INPUT for everything S950 (no open-loop send), has no memory bar (no RSTAT), shows sparse slot numbers via `sample_slots_updated` (list index != sample number there,
  unlike `sample_list_updated`), can't delete, and shows a one-time experimental warning before the first Send. **Known unmeasured risk:** a receive is ONE message of up to ~1 MB; macOS CoreMIDI
  assembles it, but a backend that splits long SysEx would lose it (`MidiManager._on_raw_message` drops any fragment not starting with F0). A timeout says so; ask for `~/.akaisds/akaisds.log`.
- Byte-for-byte collision to remember: a standard SDS ACK on channel 0 (`F0 7E 00 pp F7`...) looks like the S950's own request-sample-dump (`F0 7E 00 nn 00 F7`). Generic SDS mode against an S950
  gives nonsense, not a clean failure.

**Program editor** - `ui/s950_program_editor.py`'s `S950ProgramEditorWindow`, opened by the Dashboard's Open Editor (`open_program_editor` branches to it; needs both ports, refuses with no MIDI
input). A deliberately separate small window, NOT `ProgramEditorWindow` (that is built on `s3k.params`); it shares the Dashboard's `SamplerController` (no `S3kBridge`/`BridgeWorker`, no second thread).
- **Layout deliberately mirrors the S1000/S2000/S3000 editor's Programs tab** (the user asked for consistency; keep them in step through `editor_layout`): Programs | Keygroups (`KeygroupRangeBar` +
  `keygroupList` rows with colored swatches recolored on `theme.notifier.changed`, item with NO text because it has a row widget) | `detail_stack` of a program page and a keygroup page; section cards via
  `build_section_card`; bottom bar with Refresh left / Close right plus the write buttons. The Zone card, Filter/envelope cards (`ADSREnvelopeGraph` over four `Knob`s) and the LFO card
  (Rate/Depth 52px over Build-up/Aftertouch/Mod Wheel 40px) follow the S3000 editor's shapes. The per-sample **Filter** knob (0..99, 99 brightest) is the only cutoff-like control in the keygroup
  block. Tune shows 4 decimals on purpose (raw unit 1/16 semitone, unverified). Knobs are used for 0..99 amounts and +-50 offsets (`_KNOB_ATTRS`); spinboxes for note range/velocity switch/MIDI
  offset/program number; combos for samples/output. **A fresh `Knob.setValue(0)` emits nothing**, so the readout is filled explicitly in `_set_widget`.
- Tabs Programs | Samples (Ctrl+2/Ctrl+3; no Multi); write buttons hidden on Samples. **The first program is auto-selected** on open (and when the chosen one vanishes); the placeholder before the catalog
  arrives says "Reading the program list...". The "N keygroups" note was removed at the user's request.
- **Samples tab = `ui/s950_samples_tab.py`'s `S950SamplesTab`, READ-ONLY** (list with durations, `WaveformView`, details card), built from the shared `editor_layout` pieces. No sample-parameter write
  exists (start/end/loop semantics are inferred; some SPRM changes sit in an "active edit buffer"). Rename from the Dashboard. Mechanics: (1) per-sample SPRM reads (`request_sample_params` ->
  `s950_sample_params_received(slot, SampleParams | None)`, op `"sample_read"`, in `_BUSY_OPS`) run one at a time, ONLY while the tab is active, selected sample first, a failed slot never retried until
  `refresh()`; (2) audio is received ASYNCHRONOUSLY through `receive_samples` into a temp WAV, shown only if its sample is still selected, temp file always removed; (3) a read that finds the wire
  busy RETRIES (`_RETRY_MS`); (4) a changed catalog name drops that sample's cached SPRM/audio; (5) `WaveformView.set_markers_locked(True)` keeps markers drawn but un-grabbable and
  `set_placeholder_text()` replaces the S3000 default that promises a freezing load.
- **Reads**: `request_program(slot)` (op `"program"`) -> `s950_program_received(slot, Program | None)` - **`None` on ANY failure** so a waiting window never hangs. Every catalog read also emits
  `program_slots_updated`. A catalog read is NOT "busy" to `is_transfer_busy()`, so the window waits on the stricter `is_s950_idle()`. Only the latest wanted slot is read; a failed slot is marked shown.
- **Writes are STAGED, not live** (s950tools live-syncs every 400 ms; we send once, on "Write to Sampler"). The window edits a deep copy (`_working`) of what the unit holds (`_baseline`).
  `core/s950_params.py`: `diff_programs(baseline, edited)` -> `Change`s (only fields the editor offers - NOT `control_bits`), `validate_changes` (s950tools' documented limits, unverified; the +-24 st
  transpose limit is OURS; only CHANGED fields are range-checked), `apply_changes`. `S950Transfers.write_program(slot, baseline, edited)` (op `"program_write"`): fresh RPRGM -> **refuse unless a `.syx`
  backup of that reply saved** to `~/.akaisds/s950_backups` (`backup_dir`; tests point it at tmp_path) -> apply ONLY the changes onto the FRESH copy (a front-panel edit since loading survives) -> PRGM ->
  wire time + 500 ms NAK window (NAK = fail, names the backup) + 200 ms settle -> RPRGM read-back -> compare. Reports `s950_program_written(slot, verified, Program|None, message)`; on a mismatch the
  window shows what the unit reports. It never creates a program, never changes the keygroup count, and a no-change write is allowed on purpose (Hardware menu > "Write Program Back Unchanged
  (test)" - the first thing a tester should try).
- Also: "Restore Previous" (session only), a one-time experimental warning before the first write (`app_config.get_s950_program_write_warning_acknowledged`), a refusal to give a program another
  program's name, discard-confirmation on switching/refresh/close, controls locked while a write is in flight, the old program dropped (`_working = None`) the moment the next read starts. **Not done:**
  create/duplicate/delete program, adding/removing keygroups, and renaming a sample does not rewrite the programs that use it.

## Yamaha A4000/A5000 editing (parameter editing, native wave dumps/loading, Dashboard list/receive, restore, assign/remove; no create/delete)

Goal: a program/sample editor for the user's Yamaha A4000 (they own an A4000 only). **Every measured fact, the open-verification list, the hardware tools and the manual page map are in
`dev_docs/a4000-editor-roadmap.md` - read it before touching anything here** (and `dev_docs/a4000-native-load-findings.md` for loading). Code map: `core/yamaha_sysex.py` (codec),
`core/yamaha_params.py` (204 program/Easy Edit/sample rows: P-address + bulk offset), `core/yamaha_wave.py`/`yamaha_load.py`/`yamaha_markers.py`/`yamaha_edit.py`/`yamaha_restore.py`,
`core/demo_a4000.py` (`FakeA4000`), `controller/yamaha_session.py` (conversation engine), `controller/yamaha_transfers.py` (Dashboard list/receive/send), `ui/yamaha_program_editor.py`/
`yamaha_samples_tab.py`/`yamaha_fields.py`/`yamaha_writer.py`/`yamaha_sample_edit.py`/`yamaha_tooltips.py`. Not done: effects/controls/system params, delete objects, A5000 bulk/channels.
Sampler Type `yamaha_a4000`: PROTOCOL FAMILY `generic` (but `sampler_models.is_yamaha()` makes the Dashboard offer the Yamaha editor, list, receive and native send).
**Demo (no hardware):** `uv run python tools/a4000_demo.py` (see above).

**Wire rules (don't regress):**
- **No worker thread.** `SamplerController.yamaha_session()` -> `YamahaSession`, event-driven on the GUI thread, ONE op on the wire at a time (a select is stateful) via a FIFO queue.
  **Never connect `controller.on_sysex_received` to `MidiManager.sysex_received` yourself** - the controller already does and a second connection delivers every message twice (once made
  the next program's read return the previous one's value). The session also trusts a parameter value only after a fresh announce naming the right object, so a duplicate can't fool it.
- A select gets NO reply (the unit announces its object before answering a request). Bulk byte count is MSB-first and a dump is several blocks inside one F0..F7 (XOR checksums) - don't
  rewrite `parse_bulk_dump` to the manual's wording. `tests/fixtures/a4000/*.syx` are REAL captures - never regenerate them from the codec. `tools/a4000_discovery.py` is read-only (refuses
  anything but identity/dump/parameter requests/select); `a4000_write_verify.py` WRITES - only on a unit with nothing of value in it, app closed.
- **Audio uses the native wave dump ("WD"), not SDS** (SDS stalled after a stereo recording and can't reach a stereo sample's right channel). **A bulk dump can't be aborted**, so a cancelled
  wave makes the session DRAIN (`idle` False until the stream has been quiet for `drain_idle_ms`, up to ~70 s) and everything queued behind it waits.
  `is_sds_transfer_busy()` (what the session waits out) vs `is_transfer_busy()` (also counts a Yamaha receive, which goes THROUGH the session - never make the session wait on that one).
- SDS facts that still hold (Dashboard SENDS used it before native loading): number == the sample's CURRENT list position; the unit trims 4 frames; a stereo send becomes two mono samples;
  **Bulk Protect ON makes the unit CANCEL SDS sends and silently ignore edits** (check it first when something "does nothing"). The controller drops SDS packets already on the wire for 3 s
  after a user cancel (`_RECEIVE_CANCEL_GRACE_S`).
- Hardware deviations from the manual: sample `P2=66` (AEG sustain) is `P3=2`, not 0-1 (don't "fix" back); wave/loop addresses are coupled; `sampling_frequency`/`wave_length`/`wave_end_address`
  are ignored on a BUILT-IN sample (accepted on a user sample - `write_ignored` is gone); every edit sets the object's "edited" flag; sample controls are mirrored into its first 24 bytes; EQ
  writes update derived bytes.

**Writes:** `YamahaSession.write_parameter(row, value, object_name, cb, slot=)`, ONE parameter per op: (1) the first write to an object per session dumps it and saves a `.syx` backup
(`~/.akaisds/a4000_backups`; conftest points `controller.yamaha_session.BACKUP_DIR` at a temp dir) - **no backup, no write**; (2) sent only after a probe request was answered by an announce naming
the right object; (3) a read-back must equal the value. It refuses read-only/bulk-only/`write_ignored`/A5000-only rows and out-of-range values. `WriteCoordinator` throttles widget edits (150 ms),
shows the one-time warning (`yamaha_write_warning_acknowledged`) and re-reads the object once writes settle. `FieldPanel.set_editable` enables only writable rows; spinboxes have keyboard
tracking OFF. The window refuses to close/refresh under a write. Hardware menu: "Write Program Back Unchanged (test)", "Open Backup Folder", "Restore from Backup..." (guarded writes of the
differing rows, NOT a bulk load; a snapshot of the current state is saved first - no snapshot, nothing written). **Assign/Remove sample** = `YamahaSession.change_link` (backup once, send, ASK
the unit - the answer is the verification); both lock the window like a restore.

**Samples tab / editor UI decisions:**
- Rows use `build_sample_list_row_widget` with NO item text (the name is in `Qt.UserRole` - use `tab.sample_names()`); the grey duration label is deliberately BLANK. **No per-sample durations in
  the list and no Dashboard memory bar / Settings wave-memory field** (user decisions: each needed a background scan of every sample's SP dump, one at a time, too slow; a stale
  `yamaha_wave_memory_kb` key is ignored; the Assign dialog's `durations` argument is unused) - don't bring them back without a cheap source.
- Layout: cards in ALIGNED side-by-side pairs pinned equal (`equalize_card_heights`); the user rejected independent columns and collapsible cards. The stereo waveform is ONE display (two
  `WaveformView`s via `set_view_state`/`set_stack_position`; markers hovered/dragged in either half are drawn solid in BOTH via `_stack_partner`/`_repaint_pair`); it draws progressively and
  smoothly (queued lumps released ~30x/s, display only). `Knob`'s value arc grows from ZERO for any range spanning zero in EVERY editor (`setBipolar(False)` opts out, no tick mark - the user
  loves it). `=Sample`/`=Program` combos keep the leading `=` (`FieldPanel._style_inherited` dims them). Nothing is written UNDER the waveform (the hint lives inside it).
- Envelope graphs: amplitude reuses `ADSREnvelopeGraph` via `envelope_graph.yamaha_adsr_values` (A4000 attack/decay/release are RATES - higher is FASTER); filter/pitch use the bipolar
  `LevelEnvelopeGraph`. Shape-only, like the originals.
- Samples tab = the S3000 editor's **Loop Controls** layout: four marker KNOBS + Loop Mode combo + edit buttons + a Loop Preview card. The A4000 has NO loop hold/loop tune (a 6-mode Loop Mode +
  loop tempo). A knob turn calls `WaveformView.set_marker`; `sliderReleased` commits through `_on_marker_committed` (the SAME guarded planner path as dragging); a Loop Preview drag commits after 400 ms
  of stillness. Everything locks while audio loads or an edit is being sent.
- **Markers:** `core/yamaha_markers.py` models the unit's FOUR addresses (it SILENTLY IGNORES a write that breaks start <= loop_start <= loop_end <= end, so `plan_marker_writes` orders the writes)
  and `target_for_edit` builds the target from only the markers that MOVED - **never write the view's loop markers back wholesale** (a non-looping sample's loop is drawn at the wave end, which isn't
  what the unit stores). The tab writes one step at a time through `WriteCoordinator`, re-reads, and restores markers from cache if a step fails. Restore writes the four ADDRESS rows in planner
  order and never the two length rows. Click-to-preview: `SlicePreviewPlayer.play/play_loop(right_samples=)`, loop modes 0/4 plain, 1 held (click again stops), 2 loops for `_RELEASE_PREVIEW_MS`, 3/5
  reversed; no pitch shift; audio must be loaded first; `audio_matches` accepts a wave LONGER than the end address.
- **Long operations freeze navigation** (`_apply_lock`/`_is_busy`: lists, tab bar, Refresh, menu actions, parameter controls, assign/remove/Edit Sample, plus a bottom Cancel via
  `_cancel_long_operation`) - because the session is one FIFO, a click on another sample only QUEUED behind the transfer and the window looked hung. Deliberately NOT
  `main_tabs.setEnabled(False)` (that would grey the tab's own Cancel/waveform/progress). Programmatic `select_sample` still works while locked.
  **A disabled widget hands its keyboard focus to the next one in the tab order, and a `QScrollArea` scrolls to reveal whatever gets focus** - clicking Reverse (then disabled) threw the page ~3/4
  down. `YamahaSamplesTab._drop_focus` and `_apply_lock` clear focus BEFORE disabling anything; any new "click a button, then disable it" flow inside a scroll area needs the same.
- **Loading bar:** the 120 px bar next to Refresh follows `YamahaSession.busy_changed`/`working` (queued/running read, write or link) with the S3000's debounce; wave loads and bulk sends do NOT count
  (they have their own Cancel/progress; `idle` still counts them - use `working` for bar-like things). Edit progress bar (`tab.edit_progress`) is fed by `controller.transfer_progress`.
- **UI text:** dropdown options are sentence case; seven numeric boxes are named dropdowns (`yp.ENUMS`, rows carry `enum=`) whose raw order is ASSUMED from the manual's lists (roadmap open item 4);
  `ui/yamaha_tooltips.py` builds every row's tooltip (title + optional `DESCRIPTIONS` sentence + `Range:` line) - keep sentences short and only say what is known. The user wants messages SHORT.

**Edits and slices make NEW samples - nothing is overwritten** (`ui/yamaha_sample_edit.py` + `core/yamaha_edit.py`): the A4000 has no delete/overwrite over MIDI (a re-sent name resets ALL parameters), so
the copy is named `<name> TRIM`/` REV`/` FADE`/` NORM`/` FILT` (16 chars, a clash adds ` 2`) and every confirmation says the original is unchanged. Pure transforms are `core/sample_editing.py`'s,
per channel; a stereo sample is edited as a pair (same trim/fade, one filter per channel, normalise with ONE gain from both peaks). The copy goes through the native bulk load with
`yamaha_edit.params_for_copy` (key, coarse/fine tune, loop mode and the wave/loop ADDRESSES via `yamaha_load.CARRIED_ROWS`; everything else is the template's defaults). A non-looping copy parks the
loop at the wave end with length 0. The window selects the name the loader REALLY gave (`SamplerController.yamaha_loaded_names()`). The Slice Editor is the S3000's dialog plus `extra_channels`
(a stereo sample's other channel sliced at the same frames), `hide_bit_depth` (a Yamaha wave is always 16-bit); its export blocks on a local `QEventLoop` until `transfer_finished`.
**"Also fill a program with the slices"**: the A4000 has no create-program and a program's NAME is read only, but its 128 programs always exist - so the slices are ASSIGNED in order to an EMPTY program
picked in the dialog (programs whose scan count is 0, `_free_programs`); configured, not forked: `program_labels`, `max_program_slices` (92 = keys 36..127), `program_confirm_message`. Each slice is loaded
already mapped (`key_range_low/high` = `original_key` = 36 + i, loop mode 4, only when the checkbox is ticked - `_wants_program`), then `_create_program` links each with `change_link`, stops at the first
refusal and reports how far it got. It waits `LINK_SETTLE_MS` (4 s) first and lets each link reply take up to `LINK_REPLY_TIMEOUT_MS`.

**Native loading** (`core/yamaha_load.py`, `YamahaTransfers.send_file_queue`; Dashboard Send): wave dump(s) + one SP, paced by wire time + `send_gap_ms`, then VERIFIED (object list has the sample + waves, SP
read-back matches frames/rate/stereo; each read retried - the first read after a load is sometimes unanswered). The app never overwrites (a clash gets ` 2`, `yamaha_load.unique_name`); wave names are random
`SMP nnnnnn` (a wave under an existing wave's name is overwritten in place). Sending needs a MIDI input.
**Silent unit after bulk loads:** the unit can show "MIDI Bulk Received" and answer NOTHING over MIDI until a person presses OK (Knob 5) - most often at the first link after loads. The app copes:
`yamaha_sample_edit._recover_silent_link` (the slice fill) tells the user to press OK, waits until the unit answers (probing with a General-MIDI IDENTITY request - an object-list request pops
"Transmitting Object List" on the unit, the wrong thing to repeat), checks whether the link landed and re-sends only if it got NO answer (`result.linked is None`); the Assign dialog's failure text carries the
hint. **Assigning a sample this app loaded asks first** (`YamahaTransfers.midi_loaded_names` records every name SENT this run; the Assign dialog marks them with a grey asterisk + a one-line note;
`_confirm_assign_midi_loaded`, Cancel default); it can't know about earlier runs' loads. Tell any tester: watch the unit's display during loads/assigns.

**Detect Pitch** (Samples tab, Pitch card; needs the audio loaded): same engine as the S3000's Detect Root Note (`core/root_note_detection.py`, left channel; the analysis-window choice is
`root_note_detection.analysis_window`, shared; the S3000 window keeps old `_MIN_LOOP_ANALYSIS_FRAMES`/`_ROOT_NOTE_*` aliases for its tests), then writes Original key and Fine tune = -cents through the
ordinary guarded path. **Two guesses, one-line fixes each**: `FINE_TUNE_STEPS_PER_CENT = 1.0` and the SIGN (a positive Fine tune raises the pitch) - check against a tuner.
**A5000 gating** (unverified - the user owns an A4000): the editor asks for the unit's identity once per session (`request_identity`, queued ahead of the first object list; a Universal 0x7E message the
controller routes to `handle_identity` BEFORE its 0x43 branch; 1 s timeout, silent failure = treated as an A4000 and re-asked at Refresh); only an A5000 gets effect4/5/6 as an output target
(`yp.A5000_ONLY_ENUM_VALUES`, `FieldPanel.set_a5000`). Bulk requests still use `HEADER_A4000`, MIDI-B channels 17-32 and `effect456_connection` aren't done.
The Slice Editor/edits fixed bugs worth remembering: the post-load check must expect the carried `wave_length` (`_verify_sample`), not the audio's frame count.

**Testing the Yamaha code:** replies that must arrive over time use `rig.midi.paced = True` on the shared `_Midi` fake (tests/test_s950_transfers.py) - NEVER swap a test object's `__class__` (intermittent
PySide segfault); `dispose(window)` in tests/test_yamaha_program_editor.py calls `samples_tab.disconnect_controller()`; the `window`/`win` fixtures shorten `_REVEAL_INITIAL_INTERVAL_S`/`_REVEAL_FINISH_S`
and an autouse fixture patches the one-time write warning; an unshown window has no layout geometry (`resize()` then `layout().activate()`); a pixel test of a custom-painted widget must call
`set_view_height`. Never let a bulk string replace touch more than one place in `yamaha_session.py` (one silently cleared `_backups` in `cancel()` - there is a regression test).

## Theme preference (Settings > Settings tab > Appearance)

`config.json`'s `"theme"` is `"system"` (default - follows the OS light/dark setting live, as
the app always has), `"light"` or `"dark"` (`app_config.THEME_*`; unrecognised values read as
`system`; `ensure_defaults_saved` creates the key). The first Settings tab used to be called
"Audio/MIDI" - renamed once Appearance joined it. `ui/theme.py` owns the switching:
`apply_to_app(app, preference=None)` at startup, `set_theme_preference()` live. The dialog
saves + applies the moment an option is PICKED (`combo_theme.activated` ->
`_on_theme_chosen`), not on OK, so Cancel does NOT undo it - the only setting here that
behaves that way. `activated` (user picks only) rather than `currentIndexChanged`, so
restoring the saved value into the combo when the dialog opens writes nothing.
`QComboBox` has no `editingFinished`. The Settings page's three `QFormLayout` cards share one
label-column width (`qt_helpers.align_form_label_columns`) so every entry field starts at the
same x - each form otherwise sizes its label column to its own widest label. Things worth knowing:

- **The palette follows the preference itself for a pinned theme, never `colorScheme()`.**
  `set_theme_preference` also calls Qt's `setColorScheme`/`unsetColorScheme` (6.8+) so native
  chrome like the macOS title bar follows, but that override is best-effort: the offscreen test
  platform ignores it (`colorScheme()` stays `Unknown`) and older Qt lacks it. Only "System"
  asks the OS (`_effective_scheme`).
- `_render_for_current_scheme` skips re-rendering when the palette object is unchanged, so the OS
  re-announcing a scheme we already show, or picking "Dark" on a dark OS, does no app-wide restyle.
- A live switch costs roughly 0.4s per open editor window (it's `QApplication.setStyleSheet`
  restyling every live widget - the same cost the OS-scheme path always had).
- **Colors baked in at widget construction can't follow a live switch.** Fixed ones: muted text
  now uses the `QLabel#mutedLabel` rule in `style.qss.template` (not an inline `setStyleSheet`),
  and the keygroup-row / marker-legend swatches carry a `swatchKind` property and are recolored by
  `ProgramEditorWindow._refresh_themed_swatches` on `theme.notifier.changed` (disconnected in
  `closeEvent`). Anything NEW that bakes `theme.current_palette()[...]` into an inline stylesheet
  or a list-row widget at build time has this same bug - use a QSS rule, or hook the notifier.
  Paint-time reads of `current_palette()` (knobs, waveforms, range bar) are fine.
- Generated chevron SVGs are named per color (`down_arrow_<hex>.svg`): Qt caches stylesheet
  images by path, so rewriting one fixed filename with a new color on a switch can keep serving
  the old arrow.
- **Tests that switch theme must not restyle the real `QApplication`**: the suite leaves many
  windows alive, and `setStyleSheet` restyles all of them (a full-suite run went from ~20s to
  minutes). `test_theme_switching.py`/the S1000 window tests pass a stand-in app object to
  `apply_to_app`. Also `ProgramEditorWindow` fixtures `deleteLater()` their window (`_dispose`).

## Update checker

`core/update_checker.py`/`ui/update_helper.py` polls GitHub's
`/releases/latest`, which ignores `draft: true` releases by design (so an
in-progress CI build stays invisible) - an unpublished tagged build just
means the checker keeps reporting the previous release, not a bug.
`UpdateCheckRunner` is shared by `MainWindow`/`ProgramEditorWindow`/
`AboutDialog` - same `QThread`-GC bug class as `BridgeWorker` above if you
don't `.wait()` it before closing.

## Transfer Dashboard

**Open Editor gating**: `btn_open_editor` (+ its mirrored menu action)
only enables with both MIDI ports selected AND `device_type` is `"akai"`
or the S900/S950 family (the Program Editor needs Akai-only SysEx extensions Generic SDS lacks;
for the S900/S950 it opens its own editor instead - see that section).
`_update_open_editor_enabled` recomputes at construction and after
Settings closes.

**Shortcuts**: `Ctrl+T`/`Ctrl+E` switch windows (mirrored in both windows'
own `&Window` menus so either works regardless of focus). `Ctrl+1/2/3` are
the Program Editor's OWN tab shortcuts (Multi/Programs/Samples) - a
separate, window-scoped numbering, not window-switching.

**Layout gotcha**: a short child layout/widget sitting in a `QHBoxLayout`
next to something much taller gets stretched to match the row's height,
dumping unclaimed slack into whichever gap exists inside it - a bare
`setSpacing()` doesn't fix this. Fix:
`outer_layout.setAlignment(child, Qt.AlignmentFlag.AlignVCenter)` right
after `addLayout`. Check for this before assuming a spacing value is
wrong.

**Dropped/opened files get a stable local copy**: dragging a cropped
region out of a sample editor added the queue row fine, but Send later
failed ("No such file or directory") - the editor renders the crop to a
real temp file and deletes it shortly after drop, well before Send.
`core/dropped_files.py` copies every dropped/opened file into
`~/.akaisds/dropped_files/<pid>/` right where the row is first built;
three points in `dashboard.py` delete the copy when its row goes away.
`sweep_orphaned_sessions()` cleans up other dead PIDs' leftovers at next
launch (self-heals, not real-time); `cleanup_session()` also runs on
normal exit.

**Follow-up, 32-bit float WAVs read as silence**: `sf.read(path,
dtype="int16")` isn't reliable for every source - a real affected file
read as int16 peaked at 1/32767, the same file read as float64 and
hand-scaled peaked at 22724 (its real content). `sds_encoder`'s
`_read_float_scaled_to_int16` always reads float first (libsndfile
normalizes reliably to `[-1,1]`) and scales to int16 by hand.

## Sample send hardening (`SamplerController._send_current_packet` and friends)

Akai SDATA and generic SDS sends share ONE packet loop (`_send_current_packet`/`_check_packet_timeout`/
`_on_handshake_message`), so everything here applies to both. Any batch (Dashboard Send, Program Editor
Trim/Duplicate, Slice Editor export) goes through it. Built after a real S1000 user's log, validated against a
real S2000 (2026-10-05).

- **Timeout = re-send, not skip.** The timeout used to assume "it got through" and drop the rest of the transfer
  into permanent open loop, so one lost packet/ACK silently produced a corrupt sample reported as success.
  Now: re-send the SAME packet (`_send_packet_max_retries` = 3) once the sampler has ACKed at least once; a
  sampler that has never ACKed (`_send_stats["acks"] == 0`) gets `_send_unproven_max_retries` = **0**, i.e. the
  old immediate fallback to open loop, so open-loop-only devices aren't slowed. The timeout is 500 ms until the
  first ACK, then `max(2000, 4 x slowest ACK)` (`_current_packet_timeout_ms`).
- **ACKs carry the packet number**, and an ACK for a different data packet is ignored (`stray_acks`) so a late
  original ACK can't double-advance after a re-send. The HEADER's ACK number is deliberately not checked (queue
  index 0; its numbering isn't pinned down). Data packet n is queue index n+1 (`_expected_ack_number`).
- **Once `_no_response_detected` is set, ACK/NAK/WAIT are all ignored** (`late_acks` counted) - a reply
  trickling in after we gave up must not advance the index or start a second packet chain next to the paced one.
  (The original code advanced on late ACKs; it was latent because fallback only happened with zero ACKs.)
- **WAIT** re-arms the timer to `_send_wait_cap_ms` (30 s) instead of being ignored; expiry aborts the send
  (`_abort_stalled_send`: SDS CANCEL, no DELS) - a sampler holding WAIT is busy, not unreachable.
- **Replies stop MID-send (after ACKs were flowing)**: skip the stuck packet, finish THIS sample open loop at
  `_send_midstream_delay_ms` (100 ms - the pace a real S2000 landed at; 40 ms untested for this case), mark it
  unverified, then `_finish_unit` CANCELS THE REST OF THE QUEUE (files and a stereo right channel) and says how
  many weren't sent. Aborting instead was tried first and left a partial, garbled sample on the sampler (it keeps
  header + the packets it got). Status text names the packet (`_batch_loss_note`).
- The ACK-less open-loop path also exists on purpose with no MIDI IN selected / Generic SDS (`_is_open_loop`).
  In S2000/S3000 mode with an input defined and the cable dead, a send never starts: the pre-send slot list
  (RSLIST) can't get a reply. That's correct - slot choice and verification need it.
- Per-unit end line in the log: `acks naks waits late_acks retries timeouts stray_acks midstream_loss
  slowest_ack_ms open_loop_fallback`. DEBUG logs every ACK's packet number.

**Measured on a real S2000 (don't re-derive, and don't "tune" these without re-measuring)**: closed loop acks
a data packet every ~50 ms, the header ACK takes ~270 ms. I theorised 40 ms open-loop pacing was too fast
because BLIND/REPLUG samples landed silent (right length, no audio), and added 100 ms pacing + a 400 ms header
gap. **That theory was wrong and is reverted**: a scratch harness driving the real controller against the sampler
(replies suppressed, header re-sent, 40 ms and 100 ms, a queue of 3 blind files, the cable physically unplugged
and confirmed out) landed with full audio EVERY time, 12-bit generic included (verified by receiving the sample
back and checking its peak). The original silent samples were never explained; the one scenario not
reproduced (a sample following a mid-send-loss sample in the same queue) can't occur any more since the queue is
cancelled. Also: a new sample is listed at the LOWEST free slot (so `SINE` moved from 0 to 1) - find a sent
sample by NAME in the list, never by the slot you asked for, when verifying.

Tests: `tests/test_sampler_controller.py` (fake QTimer fires immediately - monkeypatch
`controller_module.QTimer.singleShot` to capture delays). Not covered: a real mid-send loss on the generic path.

## Debug logging

`core/debug_log.py` → rotating log at `~/.akaisds/akaisds.log`.
`LoggingBridge` wraps whatever bridge `connect()` returns and logs every
call's START/END/FAILED with timing + a traceback on failure - ask for
this log (and the macOS `.crash` report if it aborted) before guessing at
a real-hardware bug report. App-wide despite the module being named for
the Program Editor - `sampler_controller.py`'s own unhandled-exception
backstops log here too. Any new unhandled-exception backstop anywhere in
this app should log here, not `print()`.

**Bug-report plumbing (added 2026-10-05)** - what a user's `akaisds.log` now carries beyond per-operation lines:
- `core/diagnostics.py`: `install_crash_handlers()` (called first thing in `main.py`) logs unhandled exceptions
  (`sys.excepthook`, `threading.excepthook`) and every Qt warning/critical (`qInstallMessageHandler`, logged as `Qt: ...`)
  at CRITICAL/WARNING, and enables `faulthandler` into `~/.akaisds/crash.log` for native crashes (only a signal
  handler can write those, so they can't go in the main log; a crash in the previous session is flagged in the
  log at next startup). `log_session_config(reason)` writes one `config (...)` line: Sampler Type, ports,
  channel, shared transport, theme - at startup and after each Settings dialog closes (Dashboard AND editor).
- `ui/diagnostics_ui.py`: `install_dialog_logging()` wraps the static `QMessageBox.warning/critical/information/
  question` so every dialog's title/text and the button pressed is logged (`dialog [...]`). It does NOT see
  `QMessageBox(...)` instances (none exist today - if you add one, log it yourself). Also the Help > Open Log
  Folder action (and in the S950 editor's Hardware menu, which has no Help menu).
- `SamplerController` logs every distinct `status_changed` message (`status: ...`) - the text users quote.
- The S950 editor logs what the USER did (`S950Editor: ...`: open/close, tab, program selected, refresh, read
  result, discard, write requested/refused/result); the wire side is `S950Transfers`. Per-keystroke edits are not
  logged on purpose (the change list is logged when a write starts).
- Send ACKs are NOT logged per packet any more (a long sample would be tens of thousands of lines and rotate the
  evidence away): only the header's and first data packet's ACK numbers, plus warnings for stray/mismatched/slow
  (>1 s) ACKs and the per-unit summary. Log rotation is 8 MB x 3 backups.
- Not done: logging the sampler's identity/firmware at editor open, and the S1000 gating state at open.

## MIDI transport consolidation (`core/midi_transport.py`)

Default as of real-hardware validation. The Dashboard
(`SamplerController`/`mido`) and Program Editor (`BridgeWorker`/
`S3kBridge`/`python-rtmidi`) used to open TWO independent connections to
the same physical port whenever the editor was open - caused a confirmed
real race (`SampleList: expected command 0x05, got 0x16`).
`SharedMidiOutput`/`SharedMidiInput` (one real port each, write-locked,
fanned out to both consumer models) fix this;
`core.midi_manager.shared_transport_enabled()` decides (env var override,
else `config.json`'s `shared_midi_transport` key, default true,
manual-edit-only by design - no Settings UI for it on purpose).

**Two confirmed real SIGSEGVs were found reaching this state** - both from
reacting to MIDI-port state DURING a Settings dialog's own diagnostics
instead of only after it fully closes:

1. Wiring straight to `MidiManager.connection_changed` reacted to
   transient, partial port states mid-diagnostic, briefly opening a THIRD
   connection while `BridgeWorker` could be mid-`send_message()` on a port
   the GUI was concurrently closing.
2. Even reacting only once, right after the dialog closed, still crashed
   on the totally unremarkable "open Settings, change nothing, click OK" -
   a deferred event let a new `BridgeWorker` job dispatch against the
   just-deleted old bridge in the gap between dialog-close and
   bridge-swap.

**If you touch `_open_settings_dialog`/`_reconnect_shared_bridge`,
preserve this ordering**: freeze the window (confirm `BridgeWorker` is
actually idle) BEFORE the dialog ever opens, keep it frozen the whole time
Settings is up, and only unfreeze after `_reconnect_shared_bridge`
actually swaps the bridge in - every early return there must still
unfreeze before returning.

`tests/midi_transport_consolidation_test_plan.md` is the real-hardware
test plan this was validated against - re-run relevant tests after
touching `core/midi_transport.py`, `midi_manager.py`'s port-opening, or
`program_editor_bridge.py`'s `connect()`/`BridgeWorker`. **Don't add a
real multi-threaded stress test to this suite** - one holding a real lock
across real `threading.Thread`s reproducibly segfaulted the interpreter
elsewhere in the suite (a confirmed, bisection-proven instability from
mixing raw OS threads with this suite's own Qt threading); mock-based +
sequential tests replaced it.

## Shared editor layout (`ui/editor_layout.py`)

Both program editors - `ProgramEditorWindow` (S1000/S2000/S3000) and `S950ProgramEditorWindow` (S900/S950) -
build from the SAME helpers in `ui/editor_layout.py` (plus `ui/qt_helpers.py`'s `build_section_card`/
`build_scroll_area`): knob columns (`build_knob_column`, `build_knob_value_row`), the labeled combo/knob
columns, `equalize_card_heights`, `build_paired_row`/`build_centered_row`, the Zone card shell
(`build_zone_card`), the Programs | Keygroups | detail skeleton (`build_list_column`, `build_content_row`)
and the keygroup list row with its colored swatch (`add_keygroup_row`). They are plain functions - no `self`,
no hardware knowledge - moved out of `ProgramEditorWindow` unchanged (call the functions; its old
private methods are gone). **Change a measurement there and BOTH editors change - re-check
both.** Moving them was verified by rendering the S3000 editor from the old and new code (Programs program
page, Programs keygroup page, Multi, Samples) and comparing pixel by pixel: identical.

**Card spacing is `editor_layout.CARD_SPACING` (6) and every page of cards is styled with
`style_card_page_layout`** (spacing 6, margins `(0, 0, 8, 0)`): the S3000 editor's program page, keygroup page and
Samples-tab cards column, and both S950 pages. Spacing alone isn't the visible gap: a page inside a scroll area has
Fusion's default 9px left margin, and 6 (the content row's spacing) + 9 made a 15px gap between the keygroup list and
the cards - hence the 0 left margin (8 on the right keeps the cards clear of the scroll bar, as the Samples tab always
did). The paired-card rows inherit the page layout's spacing, because a sub-layout's spacing defaults to its parent's.
It was 12 on the S3000 Programs pages and 10 on its Samples tab; the S950 editor's 6 looked better and everything now
uses it. Spacing INSIDE a card (10) is separate and unchanged. `tests/test_program_editor_window.py` and
`tests/test_s950_program_editor.py` guard all of it.

The windows themselves stay separate classes on purpose - the S3000's edits write each field as it changes
through `BridgeWorker`, the S950's are staged and written whole once (see the S900/S950 section). Swatch
colouring (`_refresh_swatch`, recoloured on `theme.notifier.changed`) is still per-window, because the
S3000's also covers its Samples-tab marker swatches; the row builder takes it as a callback.

## Program Editor UI: section cards, scroll areas, busy indicator

Program/Keygroup tabs are built from `_build_section_card(title,
*row_layouts)` cards inside `_build_scroll_area(page)`. `setMinimumSize`
was tuned by hand: height doesn't need to fit everything (a tab scrolls
past the minimum); width is the real constraint (some cards pair side by
side - re-measure `scroll_area.horizontalScrollBar().maximum() == 0` on
both tabs if you widen anything, don't guess a new minimum). Stretch
ratios between paired cards are measured via `sizeHint()`, not guessed
(Range:Filter is 1:1, not the originally-assumed 1:2). `_build_section_
card` pins each card `setSizePolicy(Preferred, Fixed)` vertically - a bare
`QWidget` otherwise grows past its own `sizeHint()` into any layout
surplus (an `addStretch()` with the default stretch factor 0 doesn't
outrank this). `equalize_card_heights()` (`ui/editor_layout.py`) pins paired cards' heights equal
too - call it for any new paired row.

`BridgeWorker.busy_changed` drives the Refresh progress bar, debounced
through two one-shot `QTimer`s (`_busy_show_timer`/`_busy_hide_timer`) -
a fast bridge (`DemoBridge`/test fakes) can drain the queue to empty
BETWEEN two back-to-back `submit_*()` calls, flickering without the hide
timer's grace period. Don't remove either without re-reading
`test_a_new_busy_true_during_the_hide_grace_period_cancels_the_hide`.

Widget visibility assertions use `.isHidden()`, not `.isVisible()` - the
`editor` test fixture never calls `.show()`, and `.isVisible()` is
unconditionally `False` for a never-shown top-level window.

**`QListWidgetItem` + `setItemWidget` double-paints the item's own text if
you also give the item text** - confirmed with a minimal repro, not just
theory: an item constructed `QListWidgetItem(name)` then handed a custom
row widget via `setItemWidget` renders BOTH the item's own default text
AND the widget's own label, overlapping/offset, looking like stray
strikethrough garbage rather than two legible copies. The fix is to
construct the item with no text at all (`QListWidgetItem()`) - same
convention `add_keygroup_row` (`ui/editor_layout.py`) uses, for this exact reason - and
keep whatever the row actually needs to display in the widget alone. The
Samples tab's list (`_on_samples_loaded`/`build_sample_list_row_widget`,
added to show each sample's duration) hit this: `_sample_name_at_row`
(backed by `self._sample_list`) is the real source of truth for a row's
name everywhere in the file now, not `item.text()`, which is always
empty. Also: a custom item widget's own `sizeHint()` is used as the row's
size AS-IS - it does NOT also inherit `QListWidget::item`'s own QSS
`padding`, so a row widget needs its own matching margins or every row
comes out shorter/more cramped than a plain-text row would.

## Envelope graphs (`ui/envelope_graph.py`)

Each ADSR/ENV2 stage gets a FIXED width budget, scaled only by its own
value within that budget - **never re-normalize one stage's width against
the others' current values**, that's the exact bug this was built to fix
(turning ONE knob visibly resized every OTHER stage even though their
values never changed - no synth's ADSR display works that way).

## LFO2 and the modulation matrix

`LFO2` is real/independent but hardwired to modulate Pan (`PANRAT`/
`PANDEP`/`PANDEL`/`LFO2WAVE`, stored in `program.pan`). `LFO2WAVE` only
offers 3 shapes - no measured 4th like LFO1's "Random," don't assume one
without measuring. `lfo1_sync_combo`'s item labels are deliberately
inverted from `DESYNC`'s own raw meaning (clearer UX: "Sync" On/Off), but
the raw byte written is NOT inverted - same "combo index is the raw byte"
convention as everywhere else.

The modulation matrix (`MODS*`/`MODV*`, a 14-source enum minus env3 - no
Envelope 3 editor page exists yet, offering it as a source would be
confusing) splits across both tabs because that's what the hardware
actually stores: sources are program-region (Program tab), most amounts
too, but `MODVFILT1..3`/`MODVPITCH`/`MODVAMP3`/`L_PTCH` are keygroup-region
(Keygroup tab shows Amount-only + a read-only source-mirror combo for
those). **Confirmed on real hardware** that Filter/Pitch/Amp-mod SOURCE
really is program-wide, not per-keygroup, despite the S2000 manual's
ambiguous prose suggesting otherwise. Mirror combos need syncing on BOTH
live edits (wired after the Program tab's own combos exist) AND program
loads (`blockSignals(True)` during load means the live-edit wire never
fires then) - miss either half and the mirror silently drifts.

**Global tab + external controller (S2000/S3000 only; measured 2026-10-09)**: the GLOBAL page's settings are misc BYTE registers that neither Akai spec nor `s3k` names - found with
`tools/s2000_misc_probe.py` (read-only dumps of every misc byte/word with a setting on 2-4 values; the dumps are in `~/.akaisds/misc_probe/`). `core/global_settings.py` is the registry
(register + raw<->value maps; its docstring says what each measurement pinned down and what is INFERRED - read it before changing a map). Registers: external controller 38 (Breath 0/Foot 1/Volume 2),
output level 15, tune semitones 64 (a signed byte holding semitones **+ 9**), fine tune 65 (cents + 50), program-change channel 71 (Off 0, 1-16, Omni 17), play note/channel/velocity 31/57/54,
SCSI disk ID 11, sector size 14, local SCSI ID 12. **Only the external controller write is confirmed on hardware** (the user: "works amazingly well"); the others were read, not yet written -
the output level's 6 dB step is inferred from three points, and `s3k` deliberately never writes byte 12 (the sampler's own SCSI ID). Not offered: MIDI sysex channel (would cut the connection),
MIDI-via-SCSI (never dumped).
`BridgeWorker.submit_global_settings` (coalesced; reads every register, stops at the first FAILED read so a dead link isn't 11 timeouts, drops a value the panel can't show) /
`submit_set_global_setting(key, value)` (encode -> `S3kBridge._misc_write_verify`, private, write-then-READ-back because misc registers can answer a good write with an error code). Signals
`global_settings_loaded`/`global_setting_written`/`*_failed`. `ui/global_tab.py`'s `GlobalSettingsTab` (Ctrl+4, after Samples) is a view only: it emits `setting_chosen` on USER edits (combos
`activated`, spinboxes debounced 250 ms; loading never emits) and a control stays disabled until its key was read. **External controller is in TWO places** - the Modulation card's combo and the
Global tab's - mirrored by the window both ways (`_on_mod_external_controller_chosen`/`_on_global_setting_chosen`); a failed write re-reads everything. Demo mode (`DemoBridge` has no misc
registers) shows "Not available in demo mode."; an S1000 hides the tab and the Modulation card and sends nothing (no such source, and it ignores byte-addressable misc ops).

**Three Global controls are DISABLED on purpose (`ui/global_tab.py` `DISABLED_SETTINGS`; still show the value read)** - found on the real S2000 on 2026-10-09: **Tune and Fine tune**
(bytes 64/65): the write is accepted and reads back, and the register ends up IDENTICAL to one set on the panel (768-register dump of byte/word/dword banks 0-255, only the panel-cursor byte 49
differs) - yet the sampler doesn't act on it, so something the panel does besides writing the byte triggers it (same class as `s3k`'s SCSI-ID note / `_force_reread`); **Program change channel**
(byte 71): the sampler answers OK and the register keeps its old value whatever is written. **Open work, with the steps for the user: the docstring of `tools/s2000_tune_trigger_check.py`**
(a hardware check of candidate triggers - plain write, rewrite, fine-tune rewrite, page round trip - that saves its answers to `~/.akaisds/misc_probe/tune_trigger_check_*.json`; the
program-change-channel investigation is described there too). Re-enable a control only after a write is shown to take effect on the machine, and add the trigger to
`BridgeWorker._handle_set_global_setting`.

## PRGNUM and Program Change (Multis tab)

No working SysEx write assigns a program to a multi part - only a raw
MIDI Program Change, addressed by the target program's own `PRGNUM` (not
its list position). Freshly-loaded programs commonly share `PRGNUM == 0`;
`BridgeWorker.renumber_programs()` runs once before the first Program
Change and again on every list reload - if "assigning always picks the
wrong program" recurs, check for `PRGNUM` collisions first. The raw-vs-
display (+1) offset now lives in `s3k.params` itself (`display_offset=1`,
since the 2026-09-27 `s3ked` bump); `_handle_program_change` still needs
the raw wire byte for the actual MIDI message, so it explicitly subtracts
the offset back out after reading.

The multi's on-disk FILE format (4096 bytes: 1024-byte header + 16 x 192-byte parts, same offsets as the SysEx `multipart` region) is researched in
`dev_docs/s3000-multi-file-format.md` - read it before building any multi Save/Load.

The 16 Multis-tab part combos hold their OWN copy of every program name -
renaming a program needs an explicit `_update_multi_program_combo_names()`
push; a full list reload alone won't catch it.

## Note names: S3000XL convention, not general MIDI

`core.midi_notes.midi_note_to_name()` calls note 60 **"C3"** (the
S3000XL panel's own convention), not general MIDI's C4. Already fixed
once (the editor showed every note an octave too high) - **don't
"correct" this back**. `ui/note_spinbox.py`'s inverse parser must stay in
sync (`test_note_spinbox.py`'s round-trip test guards this).

## Samples tab: hardware quirks

- **`LOOPAT1` is the loop's END, not its start** - `LLNGTH1` measures
  *backwards* from it (`[LOOPAT1 - LLNGTH1, LOOPAT1]`). `s3k.params`'s own
  notes for `LOOPAT1` don't mention this - reading only that file, the
  natural (wrong) assumption is that it's where the loop starts.
  `loop_start` is always *derived*, never read/written directly.
- **`LLNGTH1` is 32.16 fixed point** (`frames * 65536`), not a plain frame
  count - get this wrong and loops still "work" in-app (read/write both
  naively wrong the same direction) but the hardware reads them ~65536x
  too short. See `_LOOP_LENGTH_FIXED_POINT_SCALE`.
- **`STUNO` (sample tune) is signed 1/256-semitone** despite `s3k.params`
  declaring it unsigned `0..65535` - manually sign-extended on read,
  wrapped to two's-complement on write. Range ±50.00st (semitones, despite
  "tune" sometimes being called "cents" colloquially in this app's own UI
  text). The in-memory cache must always hold the RAW value, never
  display semitones - a live edit that stored semitones instead once made
  `+40.00st` redisplay as `+0.16st` on reselect (display-only bug, the
  actual hardware write was always correct).
- **`PRGNUM`'s +1 display offset** now lives in `s3k.params` itself - see
  "PRGNUM and Program Change" above.

Loading a sample's audio is a real SDS dump, legitimately minutes for a
large sample - the UI is meant to freeze while it happens, not work
around it (`_wait_for_any_signal`, the ONE place in this window that
blocks the calling thread on a signal - don't reach for it elsewhere
without comparably good reason). Audio for the waveform comes from
`main_window.sampler_controller` (the Dashboard's OWN receiver - a
*second*, separate concurrent MIDI connection to the same port); header
fields come through the normal `BridgeWorker` connection.

**Loop marker editing**: both drag (canvas) and spinbox input paths go
through `_schedule_marker_write`/`_flush_marker_write` - one place decides
which field(s) a marker maps to. Markers PUSH each other (Newton's-cradle
cascade) rather than clamping dead at a neighbour; `_schedule_marker_
write` takes the full before/after marker dict and writes only whichever
field(s) actually changed. `_markers_within_hit_radius` + click-to-cycle
handles multiple markers landing on the same pixel after a push.

Loop editing needs only the HEADER, not full audio (`has_header()` true,
`has_waveform()` false is a valid, editable state) - gate new
marker-editing code on `_frame_count > 0`, not `_samples is not None`. If
the user edits a marker with only the header loaded and audio arrives
later, reuse the cached markers (`_markers_from_header()`); re-deriving
silently discards the edit.

**Loop type gating** (`SPTYPE` "No looping"/"One-shot"): loop
markers/spinboxes/tint fully disappear (not just grey out) via
`WaveformView.set_loop_enabled(False)`. Dragging Start/End while the loop
is off freezes (doesn't push) the loop markers, which can violate the
`start<=loop_start<=loop_end<=end` invariant Trim/Reverse/Fade/Detect Root
Note all assume on entry - `WaveformView.markers_with_loop_in_range()` is
a NON-mutating clamped view for exactly this; any code transforming
sample audio should read from this, not `.markers()` directly.

**Zoom**: `_max_zoom()` scales with `frame_count` (not a flat ceiling) so
single-sample resolution is always reachable regardless of sample size.
Trackpad pinch arrives as `QEvent.Type.NativeGesture`, not Ctrl+wheel.
Panning: `angle.x() != 0` always means pan (nothing else produces nonzero
`.x()` except a genuine horizontal swipe/wheel); Shift+scroll repurposes
`.y()` for pan on a vertical-only mouse wheel; unmodified vertical scroll
does nothing on purpose (repurposing it for pan was the root of an
earlier version of this exact bug).

## Sample edit actions (Trim/Reverse/Fade/Normalise/Filter/Duplicate)

No hardware primitive exists for any of this (`s3k`/`s3ked` have zero
sample-audio-editing capability) - the only mechanism is
`SamplerController.send_file_queue` + `BridgeWorker.submit_delete_sample`.
The math lives in `core/sample_editing.py` (pure, no Qt/MIDI; a consistent
5-arg-in/5-tuple-out shape: samples, start, loop_start, loop_end, end).

**Trim/Reverse/Fade/Normalise/Filter send-then-delete under a temp name**
(`_perform_sample_edit_real`): send the replacement FIRST under
`<name>-TMP`, confirm it's resident, delete the original, rename back -
never delete-then-send (risks total loss if the send then fails) or send
under the original's own name (keygroup zones resolve samples by NAME,
live, with no uniqueness enforced on hardware - two samples briefly
sharing a name make every zone using it ambiguous, measured).

**A new sample takes the LOWEST FREE SLOT, so the original's list index goes stale (MEASURED on a real S2000, 2026-10-07).** After any edit
or delete leaves a hole, the next send's replacement lands BELOW the original: `_perform_sample_edit_real` used to delete by the index the original had
before the send, which then pointed at the replacement (the edit silently did nothing - "sent and original deleted, but 'X-TMP' is missing") or, with
the hole further down, at an UNRELATED sample. It now reloads the list and finds both the original and the `-TMP` copy BY NAME (exactly once each, else
nothing is deleted) before deleting. Regression test: `test_reverse_sample_real_mode_deletes_the_original_by_name_when_the_replacement_lands_below_it`
(`FakeSamplerController.insert_new_at`). Proven on the S2000 with `tools/s2000_samples_check.py edits` (fade, normalise, filter, reverse, trim, each
read back over SDS: audio == the computed transform, SSTART/SMPEND = the new markers, SPTYPE/SPITCH/SHLTO/STUNO kept) and `roundtrip` (a sent sample
comes back bit-identical). Never delete a sample by an index remembered from before a send.

**These five transforms used to silently drop the sample's own header
fields on every resend** - a freshly-sent SDS dump lands with the
sampler's own defaults, not the original's, so Trim/Reverse's genuinely
recomputed loop points (and Fade/Normalise/Filter's unchanged-but-still-
known ones) were discarded outright. Fixed: `_perform_sample_edit_real`
now writes back `SPTYPE`/`SPITCH`/`SHLTO`/`STUNO` (from the source cache
entry) and `SSTART`/`SMPEND`/`LOOPAT1`/`LLNGTH1` (from the transform's own
new markers) after the rename - the same 8-field pattern
`_perform_duplicate_sample_real` already used (that one never had this
bug, since duplicating doesn't change the audio's own shape).

Fade fades the lead-in/lead-out AROUND `[start, end]` (linear ramps
reaching gain 1.0 exactly at the markers), NOT a trapezoid inside the
region - **re-read `fade_in_out_samples`'s own docstring before "fixing"
this back**, it looks backwards but isn't. Normalise is whole-buffer scope
(matches Reverse), not `[start, end]`-scoped like Fade - a deliberate
choice; confirm with the user before changing it.

Filter Sample's own dialog (cutoff/slope knobs + Preview) used to BE the
only confirmation - reversed after real use: OK also doubles as "audition,
then commit," so a habitual/muscle-memory OK click right after Preview had
no way to back out. Now has the same second `QMessageBox.question`
confirm every other transform already had. **All five send-confirmation
dialogs default to Yes** (not Qt's usual No) - Enter alone now sends, at
the user's own request, since reaching one of these dialogs is already a
deliberate step.

`_wait_for_any_signal` requires a `start` callable, invoked only AFTER
every listener connects (not `submit_*()` then connect) - unhittable
against real hardware's own latency, but reliably lost every run against a
fast fake/`DemoBridge` in tests (no latency to hide the race).

## Root Note & Tune, and Detect Root Note

`sample_root_note_spinbox` (`SPITCH`, `NoteSpinBox`, `21..127`) and `sample_tune_spinbox` (`STUNO`, a `QDoubleSpinBox` in SEMITONES, not cents despite the common name, +-50.00) live in the "Root Note & Tune" card.

**"Detect Root Note"** (`core/root_note_detection.py` + `_confirm_detect_root_note`): hand-rolled pure-Python time-domain autocorrelation, no new dependency (aubio/librosa/numpy were rejected - pitch
tuning is a matter of taste; same reasoning as `core/transient_detection.py`). Monophonic only.
- **Octave disambiguation uses the CURRENT root note as an anchor, not "lowest"/"loudest"**: a periodic signal's autocorrelation also peaks at every multiple of the true period, so real audio shows
  several genuine candidates. The anchor picks the closest in semitones but only among CREDIBLE peaks (`_CANDIDATE_CONFIDENCE_RATIO`, 0.8 of the strongest) - picking the nearest peak full stop let a bad
  anchor grab a weak spurious one, so the same audio reported 37%-79% confidence. `test_confidence_does_not_swing_low_based_on_anchor_alone` guards this against `tests/test_audio.wav`.
- **Analysis window** (`root_note_detection.analysis_window`, shared with the A4000 editor's Detect Pitch): the loop region (`markers_with_loop_in_range()`, stable, immune to attack transients) if its
  span is at least `_MIN_LOOP_ANALYSIS_FRAMES` (~2 periods of the lowest note), else an attack-skipped chunk of `[start, end]`. **Both are capped to the same short length**
  (`_ROOT_NOTE_FALLBACK_WINDOW_SECONDS`) - an uncapped loop's analysis time scales with its length (~1.5 s of audio took ~1.5 s).
- **Confidence threshold** (`CONFIDENCE_THRESHOLD`, 0.5): below it the button shows a plain acknowledgement with nothing actionable (user request). Needs calibrating on real monophonic material; the
  chord-stab fixture (polyphonic) still clears 50%, so it may need raising.
- **Writing the result does NOT overwrite Tune outright.** `core/akai_sysex.py`'s `compute_bandwidth_and_tuning` (send-time code) bakes a permanent engine-speed compensation into `STUNO` whenever a
  sample was sent at a non-native rate (the engine runs only at 22050/44100). `_confirm_detect_root_note` recomputes that baseline and ADDS the detected residual (0 - a no-op - for native-rate samples).
- **The baseline must come from the sample's own `SBANDW`, never re-derived from its rate (a real bug).** "Nearest native bucket" is only the rule THIS app's 16-bit Akai SDATA send follows; any other
  bit depth goes through generic SDS (no `STUNO`/`SBANDW` in that protocol) and the SAMPLER derives its own values by an undocumented rule: an 11025 Hz sample sent at 16-bit reads back `STUNO` -12.00
  (bandwidth 0), at 8- or 12-bit -24.00 (exactly what always assuming bandwidth 1/44100 gives). Re-deriving the baseline made click-to-preview play such a sample an octave low. `SBANDW` is now read
  directly (`_SAMPLE_DETAIL_FIELDS`) and passed to `baseline_semitones_for_bandwidth(rate, bandwidth)`. `test_preview_requested_uses_real_sbandw_not_nearest_bucket_guess` and
  `test_confirm_detect_root_note_uses_real_sbandw_not_nearest_bucket_guess` use a rate/bandwidth combination where the two computations disagree, so a reversion is caught.

## Slice Editor (Samples tab): manual ReCycle-style breakbeat chopping

"Slice Editor…" opens a modal `SliceEditorWindow` over a sample's already-loaded audio - place slice markers by hand (Equal Slices, or the live Sensitivity slider), export every slice as a new one-shot
sample, optionally also building a whole new program (one keygroup per slice, Const Pitch, one-shot). Same `has_waveform()` gate as other sample edits, but NOT demo-mode-disabled outright - only Export
needs real hardware (`DemoBridge` has no add-sample primitive); placement/preview/detection work without it. `SliceWaveformView` is NOT `WaveformView` reused (two edges + an arbitrary interior list
that CLAMPS at a neighbour, vs four markers that PUSH); it reuses `waveform_view.py`'s free helper functions.

**Export**: every slice forced to one-shot (`SPTYPE=3`); `SPITCH`/`STUNO`/`SHLTO` copied from the source; the whole batch goes in ONE `send_file_queue` call (one combined `transfer_finished`).
**With "create program" ticked, at most 92 slices** (`program_editor_window._MAX_SLICES_PER_PROGRAM`, passed as `max_program_slices`): each slice gets its own key from `_FIRST_SLICE_NOTE` (36, C1) up and
`LONOTE`/`HINOTE` max out at 127 (the old cap of 99 let slices 93-99 queue writes that failed silently). Exporting samples WITHOUT a program isn't capped. Slice names zero-pad to a width computed ONCE
per batch (`<base>-01`..`<base>-16`). Collision checks re-query `existing_names_provider()` live. The dialog is application-modal so the user can't start a conflicting send from the Dashboard mid-export.
**Dual-connection race (confirmed)**: the Samples tab's own second connection for audio means a large batch export's sample-list reload can lose a reply to `SamplerController`'s chatter;
`_reload_sample_list_with_retries` retries up to 3 times - deliberately scoped to `_export_slices` only.

**Click-to-preview** (`core/audio_preview.py`'s `SlicePreviewPlayer`, the app's first non-MIDI audio output - `miniaudio`, not `QAudioSink`, which underran on Linux): a single click (not double,
which adds a marker) on empty space OR a marker/handle schedules a debounced preview (`QApplication.doubleClickInterval() // 2`) - the double-click event sequence is press→release→doubleClick→release,
so firing immediately would blip on every marker-adding double-click. **Clicking a marker/handle uses the TARGET's own frame, not the raw cursor pixel** (a click a pixel or two off, still inside the
hover radius, used to resolve to the wrong neighbouring slice). A real drag cancels the pending preview.
**A running preview device at interpreter exit CRASHES the process on macOS** (SIGTRAP in `_os_workgroup_tsd_cleanup` on CoreAudio's IO thread - the miniaudio callback takes the GIL while Python is
finalizing; exit code 133 every time). `audio_preview` keeps a `WeakSet` of players and an `atexit` hook (`_stop_all_players`); `tests/test_audio_preview.py` has an autouse fixture doing the same (a failed
assert skips a test's own `player.stop()`) and POLLS (`_wait_until`) instead of a fixed sleep (output-device start time varies).

**Sensitivity slider - live transient detection** (`core/transient_detection.py`): hand-rolled energy-flux onset detection (RMS windows, half-wave-rectified flux, threshold + local peak + min gap), no
FFT/numpy/aubio. 0% (rest) is OFF; dragging live-regenerates ALL markers from scratch every tick with **no confirmation dialog** (unusable while dragging), so touching it silently replaces hand-placed
markers. `compute_flux()` (O(n), once per loaded sample) and `markers_from_flux()` (O(peaks), per tick) are split. The slider is `ui/fine_slider.py`'s `FineSlider` (double-click resets, Shift-drag fine
control with the macOS no-cursor-warp tweak, click-then-type via the generic `_TypeEdit` popup) - deliberately NOT a `Knob` subclass (some duplication accepted to leave that class untouched); its QSS
(`style.qss.template`'s `QSlider` rules) uses the hardcoded `#3aa88a` like `Knob`'s value arc, not `${accent}`.

**Layout fixes (confirmed against the real dialog)**: the zoom scrollbar reserves its height even while hidden (collapsing made every row below jump on zoom-threshold crossings); the info label dropped
its per-slice "lengths" list (30+ slices wrapped tall enough to push everything down); the waveform is `Expanding` with a 220px FLOOR, the info label `Preferred,Fixed`, plus
`layout.setStretchFactor(waveform, 1)` (a fixed-height waveform let surplus height land on the label - same failure class as the section cards); a `QWidget` wrapping `export_row` needs
`setContentsMargins(0,0,0,0)` explicitly or its buttons are clipped.

`AKAISDS_DEMO_INSTANT` skips `_fetch_demo_sample_audio`'s pacing. The standalone launcher (bottom of `program_editor_window.py`) needs it AND `AKAISDS_DEMO_SAMPLER=1`; its
`_StandaloneSamplerController` stub needs `cancel_transfer()` too, not just `is_transfer_busy()`, or `_open_slice_editor` raises `AttributeError`. Other bugs found here: `_wait_for_any_signal`
used to `submit_*()` before connecting listeners (see Sample edit actions); a test holding a real lock across real `threading.Thread`s segfaulted the interpreter (see MIDI transport) - don't add another.

## Testing

See `TESTING.md`. `tests/conftest.py` points `debug_log.LOG_PATH` at a temp dir for the whole run (removed at the end),
so the suite never writes to the real `~/.akaisds/akaisds.log` - don't import-time-cache `LOG_PATH` somewhere
that runs before conftest does. `uv run pytest tests/ -v` runs everything in about
40 seconds. Tests force `QT_QPA_PLATFORM=offscreen` via
`tests/conftest.py`'s own unconditional assignment - NOT the per-file
`os.environ.setdefault(...)` calls each test module also has, which are
no-ops if the desktop environment already exports this var globally
(confirmed on an Omarchy/Hyprland machine, where every "offscreen" test
was silently running on the real Wayland platform the whole time; the one
test that `.show()`s a real widget - `test_qt_helpers.py`'s tab-width
tests - was flaky for exactly this reason, a real window manager tiling-
reflowing a genuine on-screen window, not a numeric fluke).
