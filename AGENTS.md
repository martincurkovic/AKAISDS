# Agent notes for AKAISDS

Cold-start context for AI agents. Its job: stop you "fixing" things that look like bugs but are deliberate, hard-won
corrections, and say what was MEASURED on real hardware versus guessed. Human docs: `README.md`, `BUILDING.md`,
`TESTING.md`, `CONTRIBUTING.md`. Long-form hardware findings and plans: `dev_docs/`.

## Start here

| Working on | Read |
|---|---|
| anything that talks to a real sampler | "Real-hardware safety", "House rules" |
| the S3000-family Program Editor (`program_editor_window.py`) | `BridgeWorker`, "Program Editor UI", "Samples tab", "Global tab" |
| S1000 / S900-S950 / Yamaha A4000 | their own sections here, then `dev_docs/` |
| sample sending, Slice Editor, sample edits | "Sample send hardening", "Sample edit actions", "Slice Editor" |
| logs, bug reports | "Debug logging" |
| tests | `TESTING.md` |
| releases, Homebrew tap, AUR | `dev_docs/packaging/PACKAGING.md` (workflow: `.github/workflows/publish-packages.yml`) |

## House rules

- Don't commit, branch, tag or push unless asked - the owner handles git. Ask before anything outward-facing.
- Never edit the pinned `s3k`/`s3ked` dependency. Wrap it or override it here (examples below).
- Real hardware: read "Real-hardware safety" first. Back up before writing; close other MIDI software.
- Keep user-facing text SHORT (dialogs, tooltips, status bar: a line or two). Put detail in the log.
- Record what was measured vs guessed, here or in `dev_docs/`. Dated, with the unit it was measured on.
- Tests: `uv run pytest tests/ -q` (~3.5 min, ~2500 tests). See `TESTING.md` for the traps (offscreen Qt, threads).

## What this app is

Launched from `src/main.py`; the windows are swapped from the `&Window` menu (one shown at a time).

- **Transfer Dashboard** (`ui/dashboard.py`, `ui/main_window.py`, `controller/sampler_controller.py`): sends/receives
  samples over MIDI SDS or the Akai/Yamaha/S950 dialects.
- **Program Editor** (`ui/program_editor_window.py`, `core/program_editor_bridge.py`): program/keygroup/sample/multi
  parameters on S1000 and S2000/S3000 samplers. Most of this file is about it.
- **S900/S950 editor** (`ui/s950_program_editor.py`, `ui/s950_samples_tab.py`) and **Yamaha A4000/A5000 editor**
  (`ui/yamaha_program_editor.py`, `ui/yamaha_samples_tab.py`): separate, smaller windows opened by the same
  Open Editor button when that Sampler Type is selected.

**Sampler Types** (`core/sampler_models.py`): `akai_s1000`, `akai_s2000_s3000`, `akai_s900_s950`, `generic`,
`yamaha_a4000`. The legacy saved string `"akai"` is still accepted and rewritten at startup. The model is a user
setting, never detected (S1000/S2000/S3000 answer identity identically). `SamplerController.device_type` is the PROTOCOL
FAMILY (`akai` / `generic` / `s950`) that every transfer path branches on; the full choice is `sampler_model`. Yamaha is
family `generic` plus `sampler_models.is_yamaha()`.

## Third-party deps (`s3k`, `s3ked`)

Pinned git deps living in `.venv/lib/python3.12/site-packages/`: `s3k.bridge.S3kBridge` (the real SysEx bridge),
`s3k.params` (parameter registry, `p.lookup(name, region)`), `s3ked.demo.DemoBridge` (a fake that reproduces real
quirks). **Read `Parameter.notes` before trusting a field's declared range** - they are dated hardware measurements, and
several past bugs came from trusting the manual's prose. If something in the dependency looks wrong, raise it upstream.

Where this project's own measurement overrides `s3k.params` (don't "fix" back without re-measuring):
- `K_FREQ` (filter key-tracking): declared `-30..99`, measured `-24..+24`; the widget uses the narrow range.
- `B_PTCHD` (bend down): declared `0..12`, measured `0..24`. `program_editor_bridge._HARDWARE_RANGE_OVERRIDES` /
  `_lookup_for_write` patches a corrected `Parameter` copy in front of every write.
- `LFO2TRIG`: declared `0..255`, measured a plain boolean (`lfo2_trig_combo` is Off/On).

## Developing without hardware

- `AKAISDS_DEMO_SAMPLER=1`: `program_editor_bridge.connect()` returns a `DemoBridge` (or `core/demo_s1000.FakeS1000`
  for the S1000 model). Sample audio comes from `tests/test_audio.wav`, paced like a real receive;
  `AKAISDS_DEMO_INSTANT=1` skips the pacing. The standalone launcher at the bottom of `program_editor_window.py` needs both.
- `uv run python tools/a4000_demo.py` runs the real Dashboard on `core/demo_a4000.FakeA4000` (`--editor`, `--smoke`).
  `tools/s950_demo.py` does the same for `core/demo_s950.FakeS950`.
- `uv run python tools/make_screenshots.py` regenerates the manual's pictures (`ui/help/screenshots/`) from these demo fakes, offscreen
  at 2x: `--list`, `--only NAME`, `--overwrite`, `--out DIR`. It never replaces an existing picture without `--overwrite` (the
  hand-captured ones have the macOS title bar), and it points config, log, backups and queue copies at a temp dir and stubs out
  MIDI and the update check: creating a real rtmidi client can abort the process (OSStatus -304, see `midi_transport.list_input_names`).
  Add a picture by writing a `@shot` recipe in `tools/screenshot_recipes.py`; `tests/test_make_screenshots.py` checks every picture
  `quickstart.md` mentions has one.
- `uv run python tools/make_diagrams.py` redraws the MIDI cabling diagrams (`ui/help/diagrams/`, needs `rsvg-convert`) used by the manual and the
  Settings > Troubleshooting cards. They are NOT in `screenshots/` on purpose: `test_make_screenshots.py` wants a recipe for every picture there.
- The fakes fake the MIDI PORTS, so the real bridge/session code runs on top. `FakeS1000` ignores 0x20+ ops like the
  real machine and pads blocks with `0xA5` junk so dropped or misread trailing bytes are caught.

## Real-hardware safety (read before touching a real sampler)

**Incident, 2026-10-10 (the owner's S2000).** With Ableton open on the same MIDI interface, the sampler had its own
replies echoed back to it. A sampler executes a returned data frame (MISCDATA/MDATA/xHEADER) as a WRITE and acknowledges
it with `REPLY OK`. Symptoms: a stray OK next to every reply, replies running behind, "expected command 0x05, got 0x16".
A dump that read misc bytes 6-9 at that moment (the LOAD/DELETE/SAVE-NEW/SAVE-SELECTED triggers; they read 0, and 0 is
itself a command) probably made the sampler load, delete and save: it froze, then could no longer boot from its hard
disk (the owner restored from image backups). This is inference from the logs; which Ableton setting echoes is unconfirmed.
Closing Ableton fixed the editor. Other suspects for the disk trouble were never excluded: the whole-block misc writes
(a block read shows zeros for fields the sampler does not fill, and a write sends all 48 bytes back), a LOAD-page write,
the owner's own SCSI experiments, the drive failing. Once the drive is healthy, compare a misc dump against
`~/.akaisds/misc_probe/app_tune_plus50.json` (taken before any block write) to see whether any setting changed.

Rules that came out of it:
- **Never read byte registers 6-9** (`tools/s2000_misc_probe.NEVER_READ_INDEXES`, tested). Never add a tool that does.
- **Close DAWs and MIDI monitors** before running the app or any tool against a real unit. `tools/midi_echo_check.py`
  (sends only RSTAT) reports stray frames and idle clock traffic; run it with the DAW open, one setting at a time.
- Request/reply tools keep **one request outstanding**, accept only the reply naming what they asked for, and stop after a
  few unanswered reads (`tools/s2000_misc_probe.read_register`, `tools/s2000_full_dump.py`). Never send into silence.
- Don't probe a sampler whose drives are disconnected or not ready: disk-related registers stall it and it hangs.
- **The app detects the condition**: `core/reply_matching.py` makes reads tolerant of stray frames, and
  `BridgeWorker.stray_replies_detected` (3+ new skipped strays) shows a dialog each time it recurs ("Close or re-configure
  its MIDI settings before editing."). Tolerance is not safety - the echoed frames still reach the sampler.
- The SCSI controls (disk ID, sector size, local ID) are locked "for safety". Nothing in this repo calls
  select_drive/device/partition/volume, trigger_load/save, clear_memory, or writes registers 0, 2, 4, 6-9.
- Any write path needs a backup first and a read-back that must match.

**Reply matching (`core/reply_matching.py`)**, installed by `program_editor_bridge.connect()` on every real `S3kBridge`
(demo bridges untouched; the S1000 adapter sits on top of the patched bridge). `S3kBridge.send_and_receive` takes the first
frame whose opcode could answer, and a REPLY is "always allowed". `make_reply_tolerant(bridge)` patches one instance: for
READ requests it skips a stray `REPLY OK` (a `REPLY ERROR` is still returned) and, for the reads whose reply is verified to
echo the item (program/keygroup/sample headers, misc bytes, multi), skips a data frame with a different index/selector/
offset. Deletes, SETEX and block writes go to the original method. `bridge.skipped_replies` counts skips. Tests:
`tests/test_reply_matching.py` (real `S3kBridge` over scripted ports, with a "fails without the layer" control).

## `BridgeWorker` - read before touching `core/program_editor_bridge.py`

`S3kBridge` is unsafe for concurrent calls on one connection. A per-action `QThread` version crashed real hardware
(`QThread::~QThread()` -> `qFatal()` when a stale thread was mid-call during GC) and interleaved SysEx frames.
`BridgeWorker` is ONE persistent `QThread` per editor lifetime, one job at a time. "Current state" requests (keygroups,
sample/multi detail, global settings) coalesce; writes and Program Changes never do. **Don't go back to
thread-per-action** - add a `submit_*()`/`_handle_*()` pair. Tests: `process_pending()` drains synchronously; window tests
use a real thread plus `wait_until_idle()` and must call `editor._worker.stop(); editor._worker.wait()` in teardown.

## MIDI transport consolidation (`core/midi_transport.py`)

The Dashboard (`SamplerController`/`mido`) and the Program Editor (`BridgeWorker`/`S3kBridge`/`python-rtmidi`) used to open
two connections to one port, which raced. `SharedMidiOutput`/`SharedMidiInput` (one real port each, write-locked, fanned out
to both) fix it; `core.midi_manager.shared_transport_enabled()` decides (env `AKAISDS_SHARED_MIDI_TRANSPORT`, else
config `shared_midi_transport`, default true, no Settings UI on purpose).

Two real SIGSEGVs came from reacting to port state DURING a Settings dialog's diagnostics. **If you touch
`_open_settings_dialog`/`_reconnect_shared_bridge`, keep this order**: freeze the window (confirm `BridgeWorker` is idle)
BEFORE the dialog opens, keep it frozen while Settings is up, unfreeze only after the bridge is swapped in - every early
return must still unfreeze. The real-hardware test plan is `tests/midi_transport_consolidation_test_plan.md`; re-run the
transport and bridge tests after touching `midi_transport.py`, `midi_manager.py` port opening or `connect()`.

## Debug logging

`core/debug_log.py` -> `~/.akaisds/akaisds.log` (8 MB x 3, DEBUG level). Ask for this log (and the macOS `.crash` report)
before guessing at a hardware bug. New unhandled-exception backstops log here, never `print()`.

- `LoggingBridge` wraps what `connect()` returns: every call START/END/FAILED with timing and raw frames.
- `core/diagnostics.py`: crash handlers (`sys.excepthook`, Qt messages, `faulthandler` into `crash.log`) and a `config (...)`
  line at startup and after each Settings dialog. `ui/diagnostics_ui.py`: logs every static `QMessageBox` call and the button
  pressed (it can't see `QMessageBox(...)` instances - log those yourself).
- `SamplerController` logs each distinct `status_changed` text (`status: ...`).
- `core/sysex_trace.py`'s `SysexTrace` (owned by `MidiManager`) logs every SysEx the Dashboard, S950 and Yamaha editors send
  or receive: first 16 bytes + length, capped at 300 lines per 10 s per direction (`LineBudget`), never the payload.
- The S950 editor logs what the USER did (`S950Editor: ...`: open/close, tab, program selected, read result, write requested/refused/result).
- `YamahaSession` logs each operation's start and `done|FAILED in N ms` (reads at DEBUG) and counts unsolicited SysEx while
  idle (WARNING at the 10th; front-panel edits cause some). `S950Transfers` logs `begin <op>` and counts unexpected Akai messages.
- Send ACKs are not logged per packet (tens of thousands of lines); only the header, first data packet, stray/slow ACKs and
  a per-unit summary line.
- **Unexpected SysEx is log-only on the Dashboard** (owner's decision): `_log_unexpected_sysex(..., quiet=True)` writes DEBUG,
  capped at 50 lines per 10 s, never `status_changed`. A data packet before any header or an S1000 error REPLY stays a
  WARNING. Still shown: "Sampler confirmed: OK" / "ERROR creating/replacing sample" (they also fire for editor writes).
- **Imports** log a trail: `rx2: VelociLoops loaded from <path>` / `didn't load from <path>: <why>` (WARNING - the first thing to look for in "can't import / SuperFlux greyed out"), `rx2: read '<file>': N slices, ch, Hz, BPM, stretch, ms`,
  `ImportAudio:` (format/subtype/frames, the length question and the answer, refusals, decode time), `superflux:` (sensitivity -> threshold -> cut points, ms), `TransferDashboard: queued ... / cut queued loop ... into N slices`,
  `SliceEditor: detection mode ->`, `ProgramEditor:` import / stereo export / program creation (`stereo: left in zone 1...`) and the drag-and-drop lines. The startup `config (...)` line carries `slice_detection=` and
  `rx2_library=ok|MISSING`. `yamaha_backups.list_backups` now warns about an unreadable backup it skips (it used to skip silently). The static `QMessageBox` calls these features make are logged by `diagnostics_ui` like any other.
- Not logged: sampler identity/firmware at editor open, the S1000 gating state, Yamaha per-write old->new values.

## Akai S1000 support (`core/s1000_bridge.py`) - written without hardware

- **Why a separate bridge:** `S3kBridge.get_parameter`/`set_parameter` use S3000-only byte-addressable ops (0x27-0x38); an
  S1000 stays silent. It has only whole-block RPDATA/RKDATA/RSDATA + PDATA/KDATA/SDATA (nibbled), so `S1000Bridge` does
  read-modify-write of whole blocks behind the same surface. The S3000 header layout is a superset at the same offsets
  (program 0-71, keygroup 0-148, sample 0-140), so `s3k.params` is reused; anything past that reads as a neutral zero and
  refuses writes.
- **Never assume the block length** (the spec says "about 150 bytes"): cache what the device sent, patch in place, write back
  the same length, log every raw block at DEBUG. Blocks are cached 1.5 s and invalidated by list/delete calls and raw frames.
- **UI gating** (`_apply_s1000_gating`, one-way at construction; a model change in Settings closes the editor): hides the
  modulation matrix, LFO2, portamento, LFO1 shape, bend down, resonance, 4-stage ENV2, the Multi tab, the Global tab, the mute
  group and velocity start. ENV2 is a plain ADSR there (`_build_s1000_env2_adsr`). One bend field (`B_PTCH` 0-12, "Bend
  range"). Narrower ranges: polyphony 1-16, key range and root note 24-127. After hiding widgets in an unshown window,
  `invalidate()`/`activate()` the layouts before `equalize_card_heights` re-measures.
- **Controller routing is editable** (`_build_s1000_controller_cards`): `s3k.params` declares most of these fields "not used",
  range `0..0`, so `encode_field` would refuse writes and `decode_field` would misread negatives. `_S1000_RANGE_OVERRIDES`
  patches corrected copies on every read/write. **Check a new S1000 field's declared range in `s3k.params` first.**
- **The S1000 name rule is destructive:** a PDATA/SDATA whose name matches a DIFFERENT resident item deletes that one first.
  Duplicate and rename flows refuse a clashing name on an S1000 only. An UNCHANGED name writes nothing. `FakeS1000` assumes
  rewriting an item under its own name is a plain replace - the spec doesn't say; **the first thing a real S1000 must confirm.**
- **DELK:** a plain linked-list unlink (no compaction, no `GROUPS` update); a PDATA with the smaller `GROUPS` is rejected.
  Standalone Delete Keygroup is DISABLED (`KEYGROUP_DELETE_SUPPORTED = False`) after two failed real tests - don't re-enable
  or try variants blind. Create-from-slices never deletes (`first_keygroup_only=True`). The untested REBUILD alternative is
  behind `KEYGROUP_DELETE_BY_REBUILD` (off). Evidence: `dev_docs/s1000-delk-findings.md`.
- Duplicate Program/Keygroup and create-from-slices are enabled so a tester can find out whether they work; the Dashboard shows
  a one-time experimental warning. Not in either model's UI: `OUTPUT`/`STEREO`, `PLAYLO/HI`, `OSHIFT`, `TEMPER`, `KGTUNO`,
  per-zone `VZOUT`/`VFREQ`; the Samples tab edits loop 1 only; `DDATA`/`MDATA` blocks have no UI.
- Untested on a real S1000: `LOOPAT1`/`LLNGTH1`/`STUNO`/`SBANDW` semantics, the sample-edit actions through the adapter.

## Program files: Save/Load `.p1`/`.p3` (Programs tab, S1000 and S2000/S3000)

Akai disk format: 150-byte header + N x 150-byte keygroups (`.p1`) or 192 + N x 192 (`.p3`), N at header byte 42.
`core/akai_program_file.py` (pure codec) + `BridgeWorker.submit_export_program`/`submit_import_program`. Samples are NOT saved
(zones reference them by name; Load lists the ones not resident).
- No field list: the blocks are what RPDATA/RKDATA return, so every byte round-trips. Don't decode blocks for this.
- **Pointer bytes** (`FIRSTKG`, `NXTKG`, per-zone `SBADD`, `LVXF/HVXF`, `TPNUM`) are addresses the SAMPLER assigns; a file holds
  stand-ins. **`_import_program` never sends a pointer the sampler didn't hand us**: after the create PDATA it reads the program
  back and stops if its `FIRSTKG` equals another program's address, checks each append for collisions, then read-compares the
  new program with the file and every other program with a pre-load snapshot. Any stop sets `program_import_blocked` for the
  session. No DELK; a failed load can leave a partial program (the message says so).
- Load = a NEW program through the duplicate flow's order. A name matching a resident program is never sent. A `.p1` on an
  S2000/S3000 is converted after a confirmation (`convert_s1000_to_s3000`, tail bytes measured on a real S2000); the sampler's
  own parameter conversion is not replicated - keep the confirmation's wording. Disabled in demo mode.
- Delete Keygroup by REBUILD (`_delete_keygroup_s1000_rebuild`, off): `.p1` backup -> drop the keygroup locally -> load as
  `-TMP` -> DELP the original only after checks pass -> rename. Tests: `tests/test_s1000_keygroup_delete_rebuild.py`.
- Layout and pointers are not yet checked against a file from another tool: `tests/akai_program_file_test_plan.md`.

### Drag and drop of program files (Program Editor)

- **In:** a `.p1`/`.p3` dropped anywhere on the editor window loads like Programs > Load... (`dropEvent` -> `_load_dropped_program_files` -> `_load_program_path`, the same checks, conversion prompt and name rules as the dialog).
  The load runs from a `QTimer.singleShot(0)`, NOT inside the drop (its dialogs would hold the dragging app open). **One file at a time**: each load checks names against the programs on the sampler NOW, and a second would be checked before the first landed.
  Refused while the window is frozen (`main_tabs` disabled) or in demo mode. Non-program files are left alone (the event is ignored).
- **Out:** `ProgramListWidget` (DragOnly) emits `drag_out_requested(row)`; `_drag_program_out` reads the program FROM THE SAMPLER first (`_export_program_to_temp_file`: the same safe reads as Save, blocking with a wait cursor via `_wait_for_any_signal`, 60 s timeout),
  writes `<NAME>.p1|.p3` under `dropped_files.session_dir()/program_drag/` and starts a `QDrag` carrying that file URL (copy). The file has to exist before the drag can start, so a big program can pause the drag for a few seconds. Untested in a real Finder/Explorer drop (tests stub `QDrag.exec`).
- Test trap: a `QDropEvent` only POINTS at its `QMimeData` - keep the mime object referenced or the next read segfaults (`_KEEP_ALIVE` in `tests/test_program_editor_window_program_files.py`).

## Akai S2000/S3000: measured behaviour (real S2000, 2026-10-07)

- **A new program does NOT land at the end of the list.** The sampler orders programs by `PRGNUM`; a clone carries its
  template's number and sorts right after it. **Never address the next KDATA/PDATA at `len(list)`**:
  `BridgeWorker._locate_new_program(names_before)` re-reads the list. `_handle_create_program` also reads the template's
  keygroups BEFORE the first write (the insert can shift the template).
- Proven: creating a program mid-list, a 4-keygroup clone, `.p3` Save/Load (equal to the original apart from pointers), and the
  whole Slice Editor path (`tools/s2000_check.py`, `tools/s2000_slices_check.py`). The sampler recomputes pointers itself.
- A tool that talks to the bridge while a `ProgramEditorWindow` is open must wait for `window._worker.is_idle()`.

## Global tab (S2000/S3000 only)

The GLOBAL page's settings, as misc registers neither the Akai specs nor `s3k` names - found with `tools/s2000_misc_probe.py`
(read-only dumps with a setting on 2-4 values; dumps in `~/.akaisds/misc_probe/`). **`core/global_settings.py` is the registry
and its docstring says what each measurement pinned down and what is inferred - read it before changing a map.**
- **Byte registers** (`GLOBAL_SETTINGS`): external controller 38 (Breath 0/Foot 1/Volume 2), output level 15 (raw 12 = 0 dB, 6 dB
  steps - inferred from 3 points), play note/channel/velocity 31/57/54, SCSI disk ID 11, sector 14, local ID 12. Written with
  `S3kBridge._misc_write_verify` (write, then trust the READ - the ack can lie). Confirmed on hardware: the external controller.
- **Whole-block misc data** (`BLOCK_SETTINGS`): program change channel, tune and fine tune live in the 48-byte `RMDATA`(0x10)/
  `MDATA`(0x11) block, not their registers (64/65/71 are stale copies). Offsets: 0-2 program change (channel 0-based, Omni flag,
  enabled flag; Off `[c,0,0]`, Omni `[c,1,1]`), 6 fine tune (cents+50), 7 tune (semitones+9), 8 output level (by value only).
  `BridgeWorker._set_block_setting`: fresh read -> encode (only that setting's bytes) -> backup once per session
  (`~/.akaisds/mdata_backups/`) -> write (REPLY must be OK) -> re-read, must equal what was written. Wrong length is never
  edited. Block reads retry (the sampler sometimes answers with an undecodable frame).
- **Spacing matters:** the first in-app block writes were acked and never applied - a write 3 ms after a read, verified 2 ms
  after the ack. `MISC_BLOCK_BEFORE_WRITE_S` (0.3) / `MISC_BLOCK_AFTER_WRITE_S` (1.0) / `MISC_BLOCK_VERIFY_ATTEMPTS` (3), through
  the module hook `program_editor_bridge._sleep` (conftest makes it a no-op). Working theory, unproven.
- **Confirmed on hardware:** a block write changing only offset 0 (channel 1 -> 6) was acked, read back, and the panel followed.
  Off/Omni writes were not tried from the app.
- **Program change channel is LOCKED too ("Disabled for safety.", before 1.3.0)**: it works (see above) but writes the whole misc block, one of the
  uncleared suspects in "Real-hardware safety". Unlock with `AKAISDS_UNLOCK_GLOBAL=program_change_channel` or by deleting its `DISABLED_SETTINGS` entry once
  a misc dump on a healthy drive matches `app_tune_plus50.json`. The block-write path (`_set_block_setting`) is kept and tested.
- **Locked (`ui/global_tab.py` `DISABLED_SETTINGS`, the whole tooltip is the reason):** tune and fine tune ("Not working right now.")
  - they write and read back but the sampler never uses them: the panel's TUNE screen and the pitch don't change, also after a real
  page round trip, and again in a clean retest with no echo. SCSI controls ("Disabled for safety."). Untried: a power cycle (the
  stored image may go live at boot; CAREFUL, RAM samples are lost), byte 49 / item-cursor word 7 (emulating a panel edit), an
  unidentified trigger register. To re-test without editing code: `AKAISDS_UNLOCK_GLOBAL=tune_semitones,tune_cents` (or `all`).
- The window: `GlobalSettingsTab` (Ctrl+4) is a view that emits `setting_chosen` on USER edits only (combos `activated`, spinboxes/
  knobs debounced 250 ms); a control stays disabled until its value was read. The external controller is in TWO places (the
  Modulation card and this tab), mirrored both ways; a failed write re-reads everything. Demo mode shows "Not available";
  an S1000 hides the tab. Not offered: MIDI sysex channel (would cut the connection), MIDI-via-SCSI (never dumped).
- Hardware checks and the owner's steps are in the docstrings of `tools/s2000_mdata_write_check.py` (`run`, `run --setting pc`),
  `tools/s2000_misc_probe.py` (`dump`/`diff`/`mdump`/`mdiff`) and `tools/s2000_full_dump.py`. `tools/s2000_tune_trigger_check.py`
  is superseded.

## Keygroup mute group and velocity-driven sample start (S2000/S3000 only)

- **Range card -> "Mute Group" combo (`KGMUTE`).** Raw 0xFF = OFF, raw 0-31 = a real group, and **raw 0 is a real group, not
  "off"**: a zero-filled header is an ACTIVE group 0 and two keygroups over the same notes in group 0 mute each other
  (`s3k.params` notes, measured). The panel numbers groups 1-32 (confirmed), so the combo shows raw + 1 and maps by item DATA,
  never by index. A raw value it has no entry for (32-254) shows as a temporary "Raw N" item. Showing a keygroup never writes.
- **Each zone page -> "Vel. start" 28px bipolar knob right of Velocity High (`VSS1`-`VSS4`, signed +-9999 sample points).**
- Both are read with the rest of a keygroup (`_KEYGROUP_DETAIL_FIELDS`) and written through the debounced `_schedule_write`.
- Both hidden on an S1000 (`KGMUTE` is at offset 160, past its 150-byte block; `VSS` unverified there).
- The Slice Editor and duplicate flows clone a template keygroup, so new keygroups inherit its `KGMUTE`: a template left at 0
  makes all slices choke each other.
- Deliberately not done (owner's call): Envelope 3 (needs the expansion board that also adds the second filter - untestable),
  `PLAYLO/HI`/`TRANSPOSE`, voice toggles, `KGTUNO`, per-zone `VFREQ`/`VZOUT`, loops 2-4.

## Akai S900/S950 support (written without hardware)

A DIFFERENT protocol from the S1000 family: device byte `0x40`, function codes 0-11, every 8-bit value as two MIDI bytes,
10-char ASCII names, XOR checksums, a sample dump that is ONE SysEx with 4-byte handshakes (`F0 7E code F7`; the standard 6-byte
SDS ACK is silently ignored). Plan, staging and the long handoff: `dev_docs/s950-support-plan.md`. **Nothing has run against
hardware**; every guess is listed in `tests/s950_test_plan.md`. Ported from [s950tools](https://github.com/diemonster/s950tools)
(MIT, `src/ui/help/THIRD_PARTY_NOTICES.md`, bundled and shown from About > Licences - keep the notice when porting more). `dxzl/akai-s950` has NO licence: don't copy it.

- `core/s950_sysex.py` (codecs, framing, catalog, SPRM, sample dump) and `core/s950_program.py` (76-byte header + 1-31 x 140-byte
  keygroups). Only the program HEADER offsets are pinned by real captures; keygroup and SPRM offsets are as good as s950tools'.
  Unmodelled bytes stay in `raw` and are written back untouched; `to_bytes`/`to_payload` re-encode a field only if it no longer
  matches `raw`. No delete opcode - don't invent one.
- **Own protocol family `"s950"`: nothing may fall through to the akai/generic branches** (they'd put wrong bytes on the wire).
  `SamplerController` delegates the S950 entry points to `controller/s950_transfers.py`'s `S950Transfers` and REFUSES the rest
  via `_s950_not_supported`. Add a path by delegating.
- `S950Transfers`: ONE operation at a time; an RCAT reply is the "device ready" barrier before every upload; uploads are open loop
  (one SysEx, wait wire time, listen for NAKs) and ALWAYS go to an EMPTY slot (overwriting triggers a NAK storm), then name the
  sample by SPRM read-modify-write; a receive streams a 4-byte ACK every 50 ms. Names uppercase, 10 chars, unique. Bit depth
  ignored (always 12-bit); stereo averaged to mono. Needs a MIDI INPUT for everything (no open loop). No memory bar, sparse slot
  numbers (`sample_slots_updated`), no delete. Unmeasured risk: a receive is one message up to ~1 MB; a backend that splits long
  SysEx would lose it (`MidiManager._on_raw_message` drops fragments not starting with F0).
- **Editor** (`S950ProgramEditorWindow`, shares the Dashboard's controller; no `BridgeWorker`): layout mirrors the S3000 Programs
  tab through `editor_layout`. Tabs Programs | Samples (Ctrl+2/3). The first program auto-selects. **Writes are STAGED**: edit a
  copy, then `S950Transfers.write_program` does fresh RPRGM -> **refuse unless a `.syx` backup saved**
  (`~/.akaisds/s950_backups`) -> apply ONLY the changes onto the fresh copy -> PRGM -> NAK window -> RPRGM read-back compare.
  Never creates a program or changes the keygroup count. One-time experimental warnings before the first Send and the first write. Hardware menu "Write Program Back Unchanged (test)" is a tester's
  first step. Not done: create/duplicate/delete program, add/remove keygroups; renaming a sample doesn't rewrite programs using it.
- **Samples tab is read-only** (`S950SamplesTab`): per-sample SPRM reads run one at a time, only while the tab is active; audio is
  received asynchronously into a temp WAV and shown only if still selected.
- Pitfalls: a standard SDS ACK on channel 0 (`F0 7E 00 pp F7`) collides with the S950's own request-sample-dump - never test
  Generic SDS against an S950 casually. `QListWidget.clear()` defers deleting row widgets: tests that repopulate lists must flush
  deferred deletes (the `dashboard` fixture does) or they segfault in a later test.

## Yamaha A4000/A5000 editor (parameter editing, native wave dumps/loading, restore, assign/remove; no create/delete)

The owner has an A4000 only. **Every measured protocol fact, the open-verification list, hardware tools and the manual page map
are in `dev_docs/a4000-editor-roadmap.md`; loading in `dev_docs/a4000-native-load-findings.md` - read them before touching this.**
Code: `core/yamaha_sysex.py` (codec), `yamaha_params.py` (204 rows: P-address + bulk offset), `yamaha_wave/load/markers/edit/
restore.py`, `demo_a4000.py`, `controller/yamaha_session.py` (conversation engine), `yamaha_transfers.py`, `ui/yamaha_*`.

**Wire rules (don't regress):**
- **No worker thread.** `YamahaSession` is event-driven on the GUI thread, ONE op at a time (a select is stateful) via a FIFO.
  **Never connect `controller.on_sysex_received` to `MidiManager.sysex_received` yourself** - it already is, and a second
  connection delivers every message twice. A value is trusted only after a fresh announce naming the right object.
- A select gets NO reply (the unit announces its object before answering). Bulk byte count is MSB-first and a dump is several
  blocks in one F0..F7 - don't rewrite `parse_bulk_dump` to the manual's wording. `tests/fixtures/a4000/*.syx` are REAL captures:
  never regenerate them from the codec. `tools/a4000_discovery.py` is read-only; `a4000_write_verify.py` WRITES (throwaway
  units only, app closed).
- **Audio uses the native wave dump ("WD"), not SDS** (SDS stalls after a stereo recording and can't reach the right channel).
  **A bulk dump can't be aborted**, so a cancelled wave makes the session DRAIN (up to ~70 s). `is_sds_transfer_busy()` (what the
  session waits out) vs `is_transfer_busy()` (also counts a Yamaha receive - never make the session wait on that).
- **Bulk Protect ON makes the unit cancel SDS sends and silently ignore edits** - check it first when something "does nothing".
- Manual deviations: sample `P2=66` (AEG sustain) is `P3=2`; wave/loop addresses are coupled; `sampling_frequency`/`wave_length`/
  `wave_end_address` are ignored on a BUILT-IN sample; every edit sets the object's "edited" flag.

**Writes:** `write_parameter(row, value, object_name, cb, slot=)`, one parameter per op: first write per object per session dumps it
and saves a `.syx` backup (`~/.akaisds/a4000_backups`) - **no backup, no write**; sent only after an announce names the right object;
a read-back must equal the value. `WriteCoordinator` throttles (150 ms), shows the one-time warning, re-reads when writes settle.
Restore = guarded writes of differing rows (NOT a bulk load), snapshot first. Assign/Remove = `change_link` (the answer to a link
request is the verification). `FieldPanel.set_editable` enables only writable rows; spinboxes have keyboard tracking OFF; the window refuses
to close or refresh under a write.

**UI decisions:** cards in aligned side-by-side pairs pinned equal (`equalize_card_heights`); the stereo waveform is ONE display
(two views via `set_stack_position`); `Knob` arcs grow from zero for ranges spanning zero (`setBipolar(False)` opts out); the loading bar
follows `YamahaSession.working` (wave loads and bulk sends have their own Cancel/progress; `idle` still counts them); sample
list rows have NO item text (name in `Qt.UserRole`, use `tab.sample_names()`); **no per-sample durations or memory bar** (each
needed a slow scan - don't bring them back without a cheap source). **Long operations freeze navigation** (`_apply_lock`/`_is_busy`),
deliberately not `main_tabs.setEnabled(False)`. **A disabled widget hands focus to the next one and a `QScrollArea` scrolls to it** -
clear focus BEFORE disabling (`YamahaSamplesTab._drop_focus`). Dropdowns whose raw order is assumed from the manual: roadmap item 4.
Markers: `core/yamaha_markers.py` models the unit's FOUR addresses and orders writes (it silently ignores a write that breaks
start <= loop_start <= loop_end <= end); never write the view's loop markers back wholesale.

**Edits and slices make NEW samples - nothing is overwritten** (no delete/overwrite over MIDI; a re-sent name resets ALL parameters):
copies are named `<name> TRIM`/`REV`/`FADE`/`NORM`/`FILT` (16 chars, a clash adds ` 2`) and go through the native bulk load
(`yamaha_edit.params_for_copy`). "Also fill a program with the slices" ASSIGNS slices to an EMPTY program (its name is read only)
from key 36 up, max 92.

**Native loading** (`core/yamaha_load.py`, `YamahaTransfers.send_file_queue`): wave dump(s) + one SP, paced by wire time +
`send_gap_ms`, then VERIFIED; never overwrites (a clash gets ` 2`); wave names are random `SMP nnnnnn` (a wave under an existing
wave's name is overwritten in place). **Silent unit after bulk loads:** it can show "MIDI Bulk Received" and answer nothing until a
person presses OK (Knob 5), most often at the first link after loads. The app copes with `_recover_silent_link` and asks before
assigning a sample this run loaded (`midi_loaded_names`). Watch the unit's display during loads/assigns.

**Detect Pitch:** same engine as Detect Root Note. Two guesses, one-line fixes: `FINE_TUNE_STEPS_PER_CENT = 1.0` and the sign.
**A5000 gating** is unverified (the owner has an A4000): one identity request per session; only an A5000 gets effect4/5/6 outputs.
**Testing:** replies arriving over time use `rig.midi.paced = True` on the shared `_Midi` fake - never swap a test object's `__class__`
(intermittent segfault); never let a bulk string replace touch more than one place in `yamaha_session.py`.

## Transfer Dashboard

- **Open Editor gating:** `btn_open_editor` enables only with both ports selected, family `akai`/`s950` or a Yamaha model, and no transfer in flight.
- **Shortcuts:** `Ctrl+T`/`Ctrl+E` switch windows. `Ctrl+1/2/3/4` are the Program Editor's own tabs (Multi/Programs/Samples/Global).
- **Layout gotcha:** a short child in a `QHBoxLayout` next to something much taller is stretched; fix with
  `setAlignment(child, AlignVCenter)`, not spacing.
- **Dropped/opened files get a stable local copy** (`core/dropped_files.py`, `~/.akaisds/dropped_files/<pid>/`): an editor renders a
  crop to a temp file and deletes it before Send. Orphaned sessions are swept at next launch.
- **Compressed audio** (`.mp3`, `.ogg`, `.oga`, `.opus`) is just more extensions in `sds_encoder.SUPPORTED_AUDIO_EXTENSIONS`: everything reads through soundfile, whose libsndfile 1.2.2 decodes them (checked 2026-10-12 on an ffmpeg-made MP3: frame count exact, no padding trimmed). AAC/M4A fails (libsndfile can't) - it would need ffmpeg or a platform decoder. A lossy file has no native bit depth, so `read_wav_native_bit_depth` falls back to 16.
- **32-bit float WAVs read as silence** through `sf.read(dtype="int16")`; `sds_encoder` always reads float and scales by hand.

## ReCycle `.rx2` import (VelociLoops)

`core/rx2.py` reads `.rx2` through VelociLoops (`third_party/velociloops/`, public domain, vendored UNMODIFIED, commit `f77609e`) over
`ctypes`. The library is compiled by `tools/build_velociloops.py` into `src/core/native/` (git-ignored); the three `scripts/build_*`
run it and bundle the result, `test.yml` runs it before the tests. **Windows/Linux builds of it and the bundling are untested** (written on
macOS arm64); upstream marks no exports, so on MSVC the tool writes a `.def` from the header. No library = `Rx2Unavailable`, a short message, and the
`.rx2` tests skip.
- **Slices are RAW length** (`VLSliceInfo.sample_length`), not `vl_get_slice_frame_count()`: with the file's transient-stretch on, that adds a synthetic
  decay tail for slower playback, wrong for a sampler playing slices at their own pitch. Measured: the raw slices of `120Stereo.rx2` tile exactly 2.0 s (one bar at
  120 BPM). Muted slices are kept (mute is ReCycle editing state).
- Three entry points, all over the same decode: **Dashboard** (drop or Open an `.rx2`): `TransferDashboard._import_rx2` queues ONE row - the slices laid end to end as an ordinary WAV
  (stereo stays stereo), the cut points in `RX2_MARKERS_ROLE`, a "N slices" badge. **Send cuts it into one sample per slice** (`_expand_rx2_entry`: writes
  `<row name>-01..` WAVs under `dropped_files.session_dir()/rx2_slices/` at Send time, using the row's CURRENT name and settings; a stereo slice's base is 10 chars so `-L`/`-R` fits, 12 if
  sent mono or to a Yamaha). The row's Slice Editor opens with the cut points marked (`initial_markers`); exporting from it replaces the row with ordinary slice rows (no role, not cut again).
  **Program Editor File > "Import Audio..." (Ctrl+Shift+I)**: opens the Slice Editor on the slices laid end to end
  (`Rx2Loop.channel_buffers`) with the cut points as `initial_markers`, so Export and "Create new program" are the Slice Editor's own paths.
  `_run_slice_editor` is shared with the resident-sample flow (the menu item is `_import_audio`; it is the one entry for audio files AND `.rx2`, see below); `resident_source=False` skips the audio retention. **A stereo loop stays stereo:** each slice is
  sent as TWO mono samples, `<name>-L` / `<name>-R` (`_STEREO_SUFFIXES`; the dialog's `channel_suffixes` shortens the slice names to 10 chars and checks the
  suffixed ones for clashes), and the program (`_create_program_from_slices(stereo=True)`) puts left in zone 1 and right in zone 2 of each keygroup: `SNAME1/2`,
  `CP1/2`=1, `LOVEL/HIVEL` 0..127 on both, `VPANO1`=-50 / `VPANO2`=+50 (zone pan offset), zones 3-4 empty. **Unverified on hardware** (S2000 or S1000): whether
  +-50 pans a zone hard left/right, and the two-zone keygroup writes; the single-zone builder it extends is proven.
  **Yamaha editor File > "Import Audio..." (Ctrl+Shift+I)**: `YamahaSampleEditor.import_audio` -> `_run_slicer` (shared with the resident-sample Slice Editor); a stereo loop STAYS stereo
  (the dialog slices the right channel at the same frames), root key 60, the unit's defaults for the rest, "fill a program" is the existing empty-program assignment.
  The S950 editor has no entry point (read-only Samples tab, no Slice Editor); the Dashboard route still queues slices for it.
- **Updating VelociLoops:** `uv run python tools/update_velociloops.py` (`--ref`, `--dry-run`): fetches all four files before writing any, records the sha in `third_party/velociloops/VERSION` and the two docs that quote it, rebuilds and runs `tests/test_rx2.py`. Run by hand, review the diff; never from a build (the library parses untrusted files).
- Only `.rx2` is accepted; the library also reads `.rex`/`.rcy` (not wired up). Float -> int16 is a plain Python loop (no numpy at runtime).
- **SuperFlux detection** is in the Slice Editor, see the section below. Real `.rx2` loops from outside VelociLoops' fixtures are untested.

## Sample send hardening (`SamplerController._send_current_packet` and friends)

Akai SDATA and generic SDS sends share ONE packet loop; every batch (Dashboard Send, editor Trim/Duplicate, Slice export) uses it.
- **Timeout = re-send, not skip** (`_send_packet_max_retries` = 3) once the sampler has ACKed at least once; a sampler that never
  ACKed gets 0 retries (open-loop fallback). Timeout is 500 ms until the first ACK, then `max(2000, 4 x slowest ACK)`.
- ACKs carry the packet number; an ACK for a different packet is ignored (`stray_acks`). Once `_no_response_detected` is set,
  ACK/NAK/WAIT are ignored (`late_acks`). WAIT re-arms to 30 s; expiry aborts (SDS CANCEL, no DELS).
- Replies stopping MID-send: skip the stuck packet, finish THIS sample open loop at 100 ms/packet, mark it unverified, then CANCEL
  THE REST OF THE QUEUE and say how many weren't sent (aborting instead left a partial, garbled sample).
- After a user cancel of a receive, SDS packets already on the wire are dropped for 3 s (`_RECEIVE_CANCEL_GRACE_S`).
- Open loop also exists on purpose with no MIDI IN / Generic SDS. In S2000/S3000 mode with an input and a dead cable a send never
  starts (the pre-send RSLIST gets no reply) - correct.
- **Measured on a real S2000 (don't re-derive or "tune" without re-measuring):** closed loop ACKs a packet every ~50 ms, the header
  ACK takes ~270 ms. An earlier theory that 40 ms open-loop pacing caused silent samples was disproved and reverted. **A new sample
  lands in the LOWEST FREE slot** - find a sent sample by NAME, never by the slot you asked for.
- Tests: `tests/test_sampler_controller.py` (a fake `QTimer` fires immediately; monkeypatch `singleShot` to capture delays).

## Program Editor UI

- Program/Keygroup tabs are `_build_section_card(title, *rows)` cards in `_build_scroll_area(page)`. Width is the real constraint
  (re-measure `horizontalScrollBar().maximum() == 0` on both tabs if you widen anything). Cards pin `Preferred, Fixed` vertically;
  `equalize_card_heights()` pins paired cards - call it for any new pair. Paired stretch ratios are measured via `sizeHint()`.
- **Shared layout (`ui/editor_layout.py`)**: the S3000 and S950 editors build from the same plain functions (knob columns, paired
  rows, zone card shell, keygroup row, `style_card_page_layout`). **Change a measurement there and BOTH editors change - re-check
  both.** `CARD_SPACING` is 6; a card page uses margins `(0, 0, 8, 0)` because a page in a scroll area has Fusion's 9px left margin.
  Spacing INSIDE a card is 10. The windows stay separate classes on purpose (live writes vs staged writes).
- `BridgeWorker.busy_changed` drives the Refresh bar through two debounce timers (show/hide); don't remove either without reading
  `test_a_new_busy_true_during_the_hide_grace_period_cancels_the_hide`.
- **`QListWidgetItem` + `setItemWidget` double-paints if the item also has text** - build items with NO text and keep the display in
  the widget. A row widget's `sizeHint()` is used as-is, so give it its own margins.
- Widget visibility tests use `.isHidden()`, not `.isVisible()` (a never-shown window is never visible).
- **A fresh `Knob.setValue(0)` emits no `valueChanged`**, so a readout wired only to that signal stays "-": fill it explicitly when loading
  (and after any `blockSignals`).
- **Envelope graphs** (`ui/envelope_graph.py`): each stage has a FIXED width budget scaled only by its own value - never re-normalize
  one stage against the others (that was the bug).

## Theme preference (Settings > Appearance)

`config.json` `"theme"` is `system` (default, follows the OS live), `light` or `dark`; unknown values read as `system`.
`ui/theme.py`: `apply_to_app()` at startup, `set_theme_preference()` live. The Settings dialog saves and applies when an option is
PICKED (`combo_theme.activated`), not on OK, so Cancel doesn't undo it.
- **A pinned theme follows the preference itself, never `colorScheme()`** (Qt's override is best-effort and the offscreen platform
  ignores it). Only "System" asks the OS.
- **Colors baked in at widget construction can't follow a live switch.** Use a QSS rule (`QLabel#mutedLabel`) or hook
  `theme.notifier.changed` (swatches: `_refresh_themed_swatches`). Paint-time reads of `current_palette()` are fine.
- Chevron SVGs are named per color (`down_arrow_<hex>.svg`): Qt caches stylesheet images by path.
- **Tests that switch theme must not restyle the real `QApplication`** (the suite keeps many windows; it went from 20 s to minutes).
  Pass a stand-in app object to `apply_to_app`; `deleteLater()` editor fixtures.

## Update checker

`core/update_checker.py`/`ui/update_helper.py` polls GitHub `/releases/latest`, which ignores drafts: an unpublished tagged build
means the checker reports the previous release, not a bug. `UpdateCheckRunner` is a `QThread` - `.wait()` it before closing.

## PRGNUM and Program Change (Multis tab)

No SysEx write assigns a program to a multi part - only a raw MIDI Program Change addressed by the program's own `PRGNUM`, not its
list position. Fresh programs often share `PRGNUM == 0`; `BridgeWorker.renumber_programs()` runs before the first Program Change and
on every list reload (if "assigning picks the wrong program" recurs, check `PRGNUM` collisions). The +1 display offset lives in
`s3k.params` (`display_offset=1`); `_handle_program_change` subtracts it back for the raw MIDI byte. The 16 part combos hold their
own copy of program names - renaming needs `_update_multi_program_combo_names()`. The multi FILE format is researched in
`dev_docs/s3000-multi-file-format.md` (read before building Save/Load Multi).

## Note names: S3000XL convention, not general MIDI

`core.midi_notes.midi_note_to_name()` calls note 60 **"C3"** (the S3000XL panel's convention). Don't "correct" it to C4.
`ui/note_spinbox.py`'s parser must stay in sync (a round-trip test guards it).

## Samples tab: hardware quirks

- **`LOOPAT1` is the loop's END**, and `LLNGTH1` measures backwards from it (`[LOOPAT1 - LLNGTH1, LOOPAT1]`). `s3k.params` doesn't
  say so. `loop_start` is always derived.
- **`LLNGTH1` is 32.16 fixed point** (`frames * 65536`, `_LOOP_LENGTH_FIXED_POINT_SCALE`) - get it wrong and loops "work" in-app but
  read ~65536x too short on hardware.
- **`STUNO` (sample tune) is signed 1/256-semitone** despite being declared unsigned; sign-extend on read, wrap on write; range
  +-50.00 st. The cache holds the RAW value, never display semitones.
- Loading a sample's audio is a real SDS dump (minutes for a big one); the UI freezes on purpose. `_wait_for_any_signal` is the ONE
  place that blocks on a signal and needs a `start` callable invoked only AFTER listeners connect. Audio comes from the Dashboard's
  `sampler_controller` (a second connection); header fields come through `BridgeWorker`.
- **Loop markers:** drag and spinbox both go through `_schedule_marker_write`/`_flush_marker_write`; markers PUSH each other; only
  the changed fields are written. Loop editing needs only the HEADER (`has_header()` true, `has_waveform()` false is editable) - gate
  on `_frame_count > 0`; if audio arrives later, reuse cached markers (`_markers_from_header()`).
- **Loop type gating** (`SPTYPE` No looping/One-shot): loop markers disappear (`set_loop_enabled(False)`); dragging Start/End then
  freezes the loop markers, which can break `start <= loop_start <= loop_end <= end`. Code transforming audio must read
  `markers_with_loop_in_range()`, not `.markers()`.
- Zoom: `_max_zoom()` scales with `frame_count`; trackpad pinch is `NativeGesture`; `angle.x() != 0` always means pan; unmodified
  vertical scroll does nothing on purpose.

## Sample edit actions (Trim/Reverse/Fade/Normalise/Filter/Duplicate)

No hardware primitive exists - only `SamplerController.send_file_queue` + `BridgeWorker.submit_delete_sample`. Math: `core/sample_editing.py`
(pure; samples, start, loop_start, loop_end, end in/out).
- **Send-then-delete under a temp name** (`_perform_sample_edit_real`): send the replacement as `<name>-TMP`, confirm it's resident,
  delete the original, rename back. Never delete first, never send under the original's name (zones resolve samples by NAME; two
  samples sharing a name make every zone using it ambiguous).
- **A new sample takes the LOWEST FREE slot (measured)**, so the original's index goes stale: find the original and the `-TMP` copy BY
  NAME (exactly once each, else delete nothing). Never delete by an index remembered from before a send.
- A resent sample lands with the sampler's defaults, so the transforms write 8 header fields back afterward (`SPTYPE`/`SPITCH`/`SHLTO`/
  `STUNO` from the source, `SSTART`/`SMPEND`/`LOOPAT1`/`LLNGTH1` from the new markers); Duplicate uses the same pattern.
- Fade fades the lead-in/out AROUND `[start, end]` (ramps reach gain 1 at the markers), not a trapezoid inside - re-read
  `fade_in_out_samples`'s docstring before "fixing" it. Normalise is whole-buffer (like Reverse), deliberately.
- (Superseded 2026-10-13: the edits no longer send - they change the sample's working copy, see "The working copy" below. The pipeline described here is what **Send to Sampler** runs. Its confirmation defaults to Yes, like the old ones.)

## Samples tab layout and actions (S1000 + S2000/S3000 editor, 2026-10-13)

Cards in the scroll area, top to bottom: Loop Controls, **Edit Sample**, then **Loop Preview | Root Note & Tune** side by side (`build_paired_row`, heights equalised; Root Note & Tune is two rows -
Root Note + Detect Root Note, then Tune). Edit Sample has one labelled row per KIND of action (`_EDIT_SAMPLE_ROWS`): "Edit audio" (Trim, Reverse, Fade, Normalise, Filter, Stretch / Pitch - these OVERWRITE the sample),
"New samples" (Duplicate, Slice Editor), "External app" (Edit in External App, Send Edited Audio). Nothing else belongs on Loop Controls.
- **Every action is a button AND a QAction**, made by `_make_sample_edit_control(key, text, tooltip, slot)` and kept in `_sample_edit_controls[key] = (button, action)`. The old `*_sample_button` attributes
  still exist (tests use them). Enable/disable ONLY through `_set_sample_control_enabled` (via `_set_sample_edit_buttons_enabled` / `_refresh_external_edit_buttons`) so the two never disagree.
  **A new action = a `_make_sample_edit_control` call + a key in `_SAMPLE_MENU_GROUPS`/`_EDIT_SAMPLE_ROWS` + a line in `_set_sample_edit_buttons_enabled`.**
- **The Sample menu (between File and Hardware) and the sample list's right-click menu share those same QActions** (`_build_sample_menu`; the list uses `ActionsContextMenu` with the actions inserted above its own
  Rename/Delete). The menu also has "Load Audio" (= a double-click on the waveform; every edit needs the audio in memory, and a right-click only selects the row) and menu-only Rename/Delete twins (the list's own
  keep their list-scoped Delete shortcut). No shortcuts on purpose (Ctrl+1-4, Ctrl+T/E, Ctrl+Shift+I are taken).
- **Menu actions are enabled only on the Samples tab** (`_update_sample_actions_enabled`, from `_on_main_tab_changed`): from another tab a destructive item would act on a sample the user can't see. The Sample MENU is
  disabled while `_set_hardware_busy_ui` is on (`_hardware_busy_ui`) or any transfer runs (`_sync_window_menu_busy_state`; its 150 ms timer must not re-enable the menu mid-operation - this window's own
  blocking operations pump events). Don't name a menu action Settings/About/Quit/Preferences (macOS moves those to the app menu).
- Tests: `tests/test_sample_actions.py`. The Yamaha tab is NOT converted yet (its edit buttons are still in the Loop Controls card).

## Edit in External App (Samples tabs, written 2026-10-13; S2000 round trip WORKED on the owner's unit, the working-copy rework below is NOT yet tried on hardware)

"Edit in External App..." (Edit Sample card, "External app" row) writes the loaded audio to a WAV, opens it in the app chosen in Settings > External Audio Editor (`config.json` `external_editor`;
empty = the OS default for .wav). Pure parts: `core/external_edit.py`; files and watching, shared by both editors: `ui/external_edit_tracker.py` (exports to
`dropped_files.session_dir()/external_edit/<n>/<sample name>.wav`; a `QFileSystemWatcher` on the directory AND the file, debounced 600 ms; `is_ready` re-reads the file's mtime/size itself so a missed event never
hides a save).
- **Program Editor (S1000 + S2000/S3000, same code): "Load Edited Audio" (enabled once the file was saved over) does NOT send anything** - the edited file becomes the sample's WORKING COPY (next section).
  It is read back mono at the sample's own rate: another rate is resampled to it (`resample_to_target_rate`, lo-fi), stereo is averaged. A second Load replaces the working copy (the sampler's original is kept).
  Until 2026-10-13 this button was "Send Edited Audio" and replaced the sample immediately.
- **Markers:** same length = untouched; otherwise `remap_markers` keeps Start/End positions clamped into the new audio (markers that covered the whole sample cover the whole new
  one) and clamps the loop inside, in order. Tell the user to check the loop if you extend this.
- **Yamaha:** "Load Edited Audio" stages the file as the sample's working copy too (see "The Yamaha working copy" below); the channel count is kept (a mono file for a stereo sample goes on both sides, a stereo file for a mono sample is
  mixed). "Send as New Sample" then makes `<name> EDIT` (`EXTERNAL_SUFFIX`) through `_send_copy`, carrying the original's parameters. The original is untouched, so no backup.
- After a Load the exported file stays and the button waits for the NEXT save, so the user can keep editing. Windows/Linux launching (`os.startfile`, `xdg-open`) is untested; macOS uses `open [-a app]`.
- Tests: `tests/test_external_edit.py` (pure + tracker), `tests/test_external_edit_window.py` (Program Editor in demo mode, Yamaha against `FakeA4000`; the app is stubbed by patching `external_edit.launch`).

## The working copy: unsent edits (Program Editor, 2026-10-13 - NOT tried on hardware; step 1's banner/menu confirmed by the owner)

A sample can hold edited audio that is NOT on the sampler: `ProgramEditorWindow._staged_edits[name] = _StagedEdit(samples, markers, original_samples, original_framerate, original_markers, original_frame_count, history)`.
**Everything that changes audio now edits the working copy and sends nothing:** "Load Edited Audio" (`_load_edited_audio`) and the six edit buttons - Trim, Reverse, Fade, Normalise, Filter, Stretch / Pitch - through
`_apply_sample_edit(sample_index, transform, label)` -> `_stage_working_copy`. Only **Send to Sampler** and **Send as New Sample** write to the unit. (The `_confirm_*` method names are historical: they no longer confirm anything.)
Before 2026-10-13 each edit button was a full send/delete/rename replace on the spot; that pipeline (`_perform_sample_edit`, `_perform_sample_edit_real/demo`) still exists and is now Send to Sampler's.
- **No confirmation on an edit** (Undo/Revert make it cheap). The confirmations that remain are the ones before something reaches the sampler (Send to Sampler) or discards work (Revert, closing with unsent edits).
- **Undo:** `staged.history` holds the previous (samples, markers) before each change, up to `_UNDO_LIMIT` = 10 (each is a whole copy of the audio). Undo pops one; with none left it goes back to the sampler's version (same as Revert,
  no prompt). Marker/loop drags are not history items. A second "Load Edited Audio" is one (undoable).
- **The cache entry IS the working copy while one exists** (`_apply_staged_to_entry`), so every action (preview, Filter, Stretch, Duplicate, Detect Root Note, Slice Editor, Edit in External App) works on what is shown.
  The cache is wiped on every sample-list reload, so `_on_sample_detail_loaded` re-applies the working copy over the freshly read header (the header still supplies tune/root/loop type).
- **Marker/loop edits stay LOCAL while a sample is staged** (`_schedule_marker_write` returns early after storing them in `staged.markers`): writing them would put values for a different buffer on the live sample. With no working copy they
  write immediately, as always (live loop tweaking on the unit is deliberate). Root note, tune, loop type and hold are header fields and stay live either way.
- **Controls:** a banner at the top of the Edit Sample card ("Edited - not on the sampler yet": Undo, Send to Sampler, Send as New Sample..., Revert; hidden otherwise), the same four in the Sample menu / right-click menu, and a "●" on
  the sample's list row. **Send to Sampler** = backup of `staged.original_samples` (`external_edit.backup_original`; no backup, no send) then `_perform_sample_edit` (the usual send-as-`-TMP`/delete/rename replace) with a
  closure returning the working copy; success clears it, a failure KEEPS it so it can be sent again. **Send as New Sample** = `_send_edit_as_new_sample` (Duplicate flow) and then reverts the ORIGINAL to the sampler's version
  (the work lives on as the new sample). **Revert** (confirmed, default No) restores the entry from `original_*`.
- **The Filter and Stretch / Pitch dialogs** get `applies_to_working_copy=True` here: OK applies the edit to the working copy (the warning text says nothing is sent until Send to Sampler) and "Send as New Sample..." still sends the
  result at once as a new sample. The Yamaha editor passes neither flag, so its dialogs keep the old wording (its OK still makes a new sample) - it has no working copy yet.
- **The sampler's audio vs the working copy:** `_sampler_audio(name, entry)` is what `_perform_duplicate_sample_real` and `_run_slice_editor` retain (`_retain_sample_audio`) for the ORIGINAL name. Retaining the working copy there would
  hand it back as "the sampler's audio" after the reloads (retained audio attaches when the length matches).
- **Lifetime:** survives selecting other samples and reloads; follows a rename (`_confirm_rename_sample` moves the key); a user delete drops it; a sample that vanishes from the list drops it with a status message
  (`_prune_staged_edits`) - **except while `_hardware_busy_ui`**, because a replace deletes the original and renames its copy back, so the name is briefly missing. Closing the editor with unsent edits asks (default No).
  Not persisted across app restarts. Memory: ~88 KB per second of mono 44.1 kHz audio, held for the original, the working copy and each undo step.
- Tests: `tests/test_staged_edits.py` (the working copy), `tests/test_local_edits.py` (the edits, Undo, the dialogs).

## The Yamaha working copy (Yamaha Samples tab, 2026-10-13 - NOT tried on a real A4000)

The same idea as the Akai working copy (previous section), adapted to what the A4000 can do. `YamahaSamplesTab._staged[name] = _Staged(channels, markers, original_channels, original_markers, history)`; the six edits (Trim, Reverse,
Fade, Normalise, Filter, Stretch / Pitch) and "Load Edited Audio" change it (`YamahaSampleEditor._apply` -> `tab.stage_working_copy`) and send nothing. **The unit can't overwrite a sample, so the only way out is "Send as New Sample"**
(banner / Sample menu, `send_working_copy_as_new` -> `_send_copy`, name `<name> EDIT`, a clash gets ` 2`): there is no "Send to Sampler" and no backup (the original is never touched). Before 2026-10-13 every edit made its own
new sample at once (` TRIM`/` REV`/`FADE`/` NORM`/` FILT`/` STR`/` PITCH`); those suffixes survive only on the dialogs' own "Send as New Sample..." (below).
- **What is shown:** `_show` makes the working copy's audio the tab's "loaded" audio (`_audio_name/_audio_samples/_audio_samples_right`) - the tab holds audio for ONE sample at a time, so a staged sample re-asserts it whenever it is selected.
  `_overlay(name, payload)` returns a COPY of the unit's payload with the working copy's `wave_length`, wave/loop addresses and loop length, and `_show_values`/`_show_waveform` read that copy (the summary, the Wave card and the
  markers follow the edit). `tab._cache[name]` stays what the unit sent: `ye.params_for_copy(ctx["data"], ...)` carries the ORIGINAL's key/tuning/loop mode onto the new sample.
- **Marker edits stay local while staged** (`_on_marker_committed` stores them in `staged.markers` and returns - no `_start_marker_edit`, nothing written). Without a working copy they write at once, as always. The other panel fields
  (loop mode, tuning, ...) write live either way.
- **Controls:** every action is a button AND a `QAction` (`_make_control`; the editor connects both with `tab.connect_control`), in the new **Edit Sample** card (rows Edit audio / New samples / External app, plus a banner with Undo, Send as New
  Sample..., Revert while the shown sample is staged), the **Sample** menu (between File and Hardware, `YamahaProgramEditorWindow._sample_menu`) and the sample list's right-click menu (all one QAction each). Actions work only while the
  Samples tab is showing (`set_active`) and the menu is disabled while `_is_busy()` (`_apply_lock`). A "●" marks a staged sample's list row. "Load Audio" = a double-click on the waveform.
- **Dialogs:** Filter and Stretch / Pitch get `applies_to_working_copy=True, allow_new_sample=True, send_name="Send as New Sample"`: OK applies the edit to the working copy; the dialog's own "Send as New Sample..." makes the unit's
  copy at once (`_run`, named by the edit: ` FILT`/` STR`/` PITCH`) - and if the source was staged, the working copy is dropped on success (`_sending_name` -> `_on_send_finished` -> `discard_working_copy`).
- **Undo** (`_UNDO_LIMIT` = 10) and **Revert** (confirmed, default No) as on the Akai side; the first Undo goes back to the unit's version. `discard_working_copy` puts the original audio (still in memory) back on screen.
- **Lifetime:** survives selecting other samples, `set_samples` and Refresh; `_prune_staged` drops the edits of a sample that is no longer in the list (with a status message). Closing the editor window with unsent edits asks (default No) -
  `tests/test_yamaha_program_editor.py` `dispose` clears `_staged` first, or the teardown would block on that modal question. Not kept across restarts.
- **Layout (same day):** Loop Controls (no edit buttons any more) -> Edit Sample -> **Loop Preview | Wave** side by side (`build_sample_cards` exposes `panel.wave_card`) -> Pitch|Key & Velocity, Level & Pan|Output, Filter|Filter Envelope,
  Amplitude|Pitch Envelope, LFO|Controllers, EQ alone at the end.
- Tests: `tests/test_yamaha_working_copy.py`; the older Yamaha edit tests were rewritten to stage first and then use the banner's Send as New Sample.

## Re-sent samples keep their pitch (2026-10-13, found on the owner's S2000 from akaisds.log)

**A sample sent FROM another one's audio (Send to Sampler, a new sample from an edit / Duplicate, slice export) must not get the source's STUNO copied across as it was.** STUNO bakes in the baseline for the sample's
SBANDW bucket (`baseline_semitones_for_bandwidth`), and the sent sample lands on the bucket the SEND chose - the nearest one for a 16-bit Akai send (11025 Hz -> SBANDW 0, baseline -12) - while a sample the sampler imported itself
(generic SDS) sits on SBANDW 1 whatever its rate (11025 Hz -> baseline -24). Measured in the log: source `SBANDW 1, STUNO 59392` (= -24.00); the replace wrote 59392 onto the new sample's bucket-0 header and it played an
**octave low**. `_tuning_for_resent_sample(new_index, source_stuno, source_sbandw, source_rate)` reads the NEW sample's real SBANDW/SSRATE (`_read_sample_header_blocking`) and writes `(source STUNO - source baseline) + new
baseline`, so what the user dialled in survives and the pair stays consistent. A failed read keeps the source's STUNO (as before) and logs a WARNING; every correction logs an INFO `tuning:` line with both sides.
- Slice export: `_export_slices(..., source_sbandw=, source_rate=)` (bound by `_run_slice_editor` from the source entry). **Audio from a FILE (no `source_sbandw`) no longer gets STUNO written at all** - it used to write 0 over the
  baseline the send baked in, which played a 11025 Hz import an octave high.
- Not changed: Dashboard sends and the Yamaha editor (different protocols/tuning). The preview shift (`_preview_pitch_shift_semitones`) already reads the real SBANDW.
- **After a replace the sample is shown at once** (`_show_sent_sample`): the cache entry is built from what was just written and selected explicitly (by hand if the row is already current). Relying on the header fetch a plain
  selection starts left the waveform up with the controls off until the user clicked the sample again: that fetch queues behind every write and length read, and the owner's log shows no `sample_detail` request for the replaced
  sample at all. (Root cause of the missing request not identified; the explicit path doesn't need it.)
- Logging added: `ProgramEditor: <edit> applied to <name>: N -> M frames, markers a -> b` for every edit and `Staged edit: sending <name>: ...` for every Send to Sampler. **Open report (not reproduced): "Trim to Markers after an
  external edit doesn't trim".** The log of that session shows Load Edited Audio (30240 frames, markers 0/30239/30239/30239) then Send to Sampler of 30240 frames with SMPEND 15151 / loop end 7023 - i.e. markers moved, no trim applied
  to the audio. Trim on a staged sample works in tests (`tests/test_local_edits.py`); the new log lines will show whether Trim ran.
- Tests: `tests/test_resent_tuning.py` (the fake sampler's `new_sample_sbandw` picks the bucket a send lands on).

## Time Stretch / Pitch Shift (Samples tabs, written 2026-10-13, NOT tried on hardware)

"Stretch / Pitch..." (Edit Sample card, "Edit audio" row) opens `ui/time_stretch_dialog.py`: a Mode dropdown and Filter-style knobs - Length (LogKnob 25-400 %, 100 % in the
middle), or Semitones (+-24) and Cents (+-50) in Pitch Shift mode, and Grain (LogKnob 5-500 ms) in both. A double-click resets a knob. Preview plays through the speakers (a settled knob restarts a
playing preview after 350 ms). OK is disabled at 100 % / 0 st 0 ct and above `MAX_OUTPUT_FRAMES` = 16 M (the S3000's RAM). Then a confirmation, then `_perform_sample_edit` (Program Editor:
the usual replace, label "Time Stretch"/"Pitch Shift") or `YamahaSampleEditor.time_stretch` (a NEW sample `<name> STR` / `<name> PITCH`, stereo channels edited identically). `dialog.edit()`
returns `(kind, options)` for `time_stretch.EDITS` / `transform_for`; the Yamaha `ye.EDITS` carries the same two kinds. The engine is `core/time_stretch.py`, a Python port of
potenza-time-stretch's `TimeStretch.h` (GPL-3, see `THIRD_PARTY_NOTICES.md`).
- **The port is checked against the real C++**: `tests/fixtures/time_stretch_golden.json` came from compiling the header (`tools/make_time_stretch_golden.py --header ...`, header kept out of
  the repo; 5 stretch + 4 pitch cases); `test_time_stretch.py` needs agreement within 1e-5. Don't "tidy" `TimeStretch`'s arithmetic without re-running that.
- **NOT measured: that it equals the sampler's own time stretch.** The upstream project only says "Akai style". The default grain (40 ms) is a guess. A real comparison = stretch one sample
  with the S2000's own TIME STRETCH and with this, and listen/diff. (The owner listened to the stretch and found it "very akai-esque".)
- It is a playback engine driven offline (`_render`: one output sample per step, pitch delta 1; stretch = `round(n x factor)` samples, pitch shift = the engine's `comp` mode, `n` samples with the
  grains reading the source `ratio` times faster; ~0.7 s per 10 s of mono audio, pure Python).
- **Grains are not phase aligned**, so a sustained tone phases/warbles (measured: a 100-sample sine stretched x1.5 drifts 92-114 samples per cycle) unless the step between grain starts is
  a whole number of cycles. **Pitch shift on a pure 441 Hz tone is only approximate and depends on the grain** (measured 2026-10-13, dominant FFT peak vs expected: +3 st at 10 ms -> 440 Hz instead of 524,
  at 80 ms -> 515; +12 st at 10 ms -> 770 instead of 882, at 80 ms -> 899): short grains pull a tone back toward its original pitch, 60-100 ms tracks it best. Drums and loops are the use case.
- Time Stretch scales the markers in proportion (`stretch_with_markers`); Pitch Shift keeps the length, so the markers don't move. Both act on the whole buffer (Start/End don't limit them, like
  Normalise/Reverse). Root note/tune are untouched on purpose (a pitch-shifted sample should now play at its new pitch on the same key).

**"Send as New Sample" (Filter and Stretch / Pitch dialogs, Program Editor only, 2026-10-13):** next to OK the dialogs have a "Send as New Sample..." button (`allow_new_sample=True`; off in demo mode, like the Duplicate
button - DemoBridge can't add a sample). `dialog.send_as_new()` tells the caller which was chosen; the caller then runs `_send_edit_as_new_sample` instead of the replace: name prompt (default = name + " FILT" / " STR" / " PITCH",
`_duplicate_default_name(name, suffix)`), the same no-clash rule as Duplicate Sample (an S1000 send under a resident name DELETES that sample), then `_perform_duplicate_sample_real(..., samples=, markers=, label=)` - the Duplicate flow,
generalised to send EDITED audio and the markers that go with it (`stretch_with_markers` scales them). The original is never touched, so none of the replace path's send/delete/rename applies. Yamaha dialogs don't have the button
(every Yamaha edit already makes a new sample). `TimeStretchDialog` keeps ONE size across its modes: fixed-width knob columns in a row always wide enough for three, the dialog's width fixed, and the taller of the two warning
texts reserved - switching to Pitch Shift and back used to widen it by ~30 px. Tests: `tests/test_edit_as_new_sample.py`.

**Previews play as the sampler would (2026-10-13):** every PC preview shifts the audio by the sample's root note and tune (`SlicePreviewPlayer.play(..., pitch_shift_semitones=)`), to the fixed preview note C3 (60).
- **Akai** (Program Editor): `ProgramEditorWindow._preview_pitch_shift_semitones(entry)` = Tune (STUNO minus the SBANDW baseline) + root note to C3 (see "Root Note & Tune" below). Used by the Samples tab's click-to-preview, the Filter and
  Stretch / Pitch dialogs' Preview (`pitch_shift_semitones=`) and the Slice Editor (`SliceEditorWindow(preview_pitch_shift_semitones=)`, passed by `_open_slice_editor` -> `_run_slice_editor(preview_pitch_shift=)`).
- **Yamaha**: `YamahaSamplesTab.preview_pitch_shift_semitones(data)` = `core.preview_pitch.yamaha_shift_semitones(original_key_l, coarse_tune, fine_tune_l, FINE_TUNE_STEPS_PER_CENT)`, used by its click-to-preview (all
  loop modes, incl. the reversed copy), both dialogs and its Slice Editor (`open_slicer`). **UNVERIFIED on a unit** (the owner has an A4000 but hasn't checked): the C3 preview note, that Coarse is in semitones and a positive Coarse/Fine
  raises the pitch, and the Fine step size (the same unmeasured guess as Detect Pitch). Each is one line in `core/preview_pitch.py`.
- **Audio from a FILE gets no shift** (Dashboard Slice Editor, Import Audio / `.rx2`, `_run_slicer` for an import): it has no root note or tune. The default is 0.
- **The Stretch / Pitch dialog's result line** says how long the preview lasts when the shift changes it: "88,200 frames, 2.00 s (1.00 s as previewed)" (`preview_pitch.played_seconds`); nothing extra under 0.005 semitones.
- Not shifted on purpose: the Loop Preview pane and the waveform (they show the data, not the sound). Tests: `tests/test_preview_pitch.py`, `tests/test_preview_tuning.py`.

## Root Note & Tune, and Detect Root Note

`sample_root_note_spinbox` (`SPITCH`, `NoteSpinBox` 21..127) and `sample_tune_spinbox` (`STUNO`, semitones +-50.00).
**Detect Root Note** (`core/root_note_detection.py`): hand-rolled time-domain autocorrelation, monophonic only (no aubio/librosa/numpy).
- **Octave disambiguation anchors on the CURRENT root note**, among credible peaks only (`_CANDIDATE_CONFIDENCE_RATIO` 0.8) - nearest-
  peak-full-stop made confidence swing 37%-79% on the same audio. Guarded by `test_confidence_does_not_swing_low_based_on_anchor_alone`.
- Analysis window (`analysis_window`, shared with the A4000 Detect Pitch): the loop region if long enough, else an attack-skipped chunk;
  both capped short (analysis time scales with length). Confidence threshold 0.5 (needs calibrating on monophonic material).
- **The result is ADDED to Tune, not written over it**: `compute_bandwidth_and_tuning` bakes engine-speed compensation into `STUNO` for
  non-native rates. **The baseline comes from the sample's own `SBANDW`, never re-derived from its rate** (the sampler derives its own
  values for non-16-bit sends; re-deriving played such samples an octave low).

## Slice Editor (Samples tab): ReCycle-style chopping

A modal `SliceEditorWindow` over already-loaded audio: place slice markers (Equal Slices, or the live Sensitivity slider), export each
slice as a new one-shot sample, optionally build a program (one keygroup per slice, Const Pitch, one-shot). `SliceWaveformView` is NOT
`WaveformView` (two edges + an interior list that CLAMPS, vs four markers that PUSH). Only Export needs real hardware.
- **Export:** every slice `SPTYPE=3`; `SPITCH`/`STUNO`/`SHLTO` copied; one `send_file_queue` call. With "create program": max 92 slices
  (keys from 36 up, `LONOTE`/`HINOTE` cap 127). Names zero-pad to a width computed once per batch. The dialog is application-modal.
  The Samples tab's second connection can lose a reply to `SamplerController` chatter, so `_reload_sample_list_with_retries` retries (3x).
- **Click-to-preview** (`core/audio_preview.py`, `miniaudio`): a single click schedules a debounced preview (half the double-click
  interval - a marker-adding double-click must not blip); a marker click uses the TARGET's frame, not the cursor pixel; a drag cancels.
  **A running preview device at interpreter exit crashes macOS** (SIGTRAP on CoreAudio's thread): `audio_preview` stops all players via
  `atexit`; its tests have an autouse fixture doing the same and poll instead of sleeping.
- **Sensitivity slider** (`core/transient_detection.py`): hand-rolled energy-flux onset detection. 0% is OFF; dragging regenerates ALL
  markers every tick with no confirmation, so it silently replaces hand-placed markers. `FineSlider` is deliberately not a `Knob`.
- Layout: the zoom scrollbar reserves its height even when hidden; the waveform is `Expanding` with a 220 px floor plus
  `setStretchFactor(waveform, 1)`; a `QWidget` wrapping `export_row` needs zero margins.
- The standalone launcher's `_StandaloneSamplerController` stub needs `cancel_transfer()` as well as `is_transfer_busy()`.

## LFO2 and the modulation matrix

`LFO2` is independent but hardwired to Pan (`PANRAT`/`PANDEP`/`PANDEL`/`LFO2WAVE`, in `program.pan`); `LFO2WAVE` offers 3 shapes - don't
assume a 4th. `lfo1_sync_combo` labels are inverted from `DESYNC`'s raw meaning but the raw byte is not. The matrix (`MODS*`/`MODV*`)
omits Envelope 3 (no ENV3 page; the expansion board is untestable). Sources are program-wide (confirmed on hardware); most amounts too,
but `MODVFILT1..3`/`MODVPITCH`/`MODVAMP3`/`L_PTCH` are keygroup-region (the Keygroup tab shows amounts plus read-only source mirrors).
Mirror combos must sync on BOTH live edits and program loads (`blockSignals` during a load skips the live wire).

## Testing

See `TESTING.md`. `tests/conftest.py` points the debug log, backups and the `_sleep` hook at temp locations, forces
`QT_QPA_PLATFORM=offscreen` unconditionally (the per-file `setdefault` is a no-op if the desktop exports the variable - that once made
"offscreen" tests run on a real Wayland session), and clears `AKAISDS_UNLOCK_GLOBAL`. One audio-preview test
(`test_update_loop_points_with_degenerate_bounds_is_ignored`) is timing-sensitive and has flaked once; re-run it alone before suspecting a change.

## Slice Editor: stereo display (2026-10-12)

`SliceWaveformView.set_waveform(..., right=)` draws a second channel in its own lane under the first (L/R labels, one divider); markers, labels and the playhead span both
(a slice boundary is one frame index). `SliceEditorWindow(extra_channels=[right])` turns it on for exactly one extra channel and ANALYSES the mono MIX (`_analysis`): the Sensitivity
detector, click-to-preview and zero-crossing snaps all read it, so a hit loud on one side only still counts (this changed the Yamaha editor's stereo slicing too, which read the left
channel only). The Dashboard's Slice Editor passes the right channel as well and now takes it from `extra_slices` rather than re-cutting it; it also passes `channel_suffixes`, which fixes
a latent clash: the send path builds `-L`/`-R` names by cutting the BASE to 10 chars, so 11-12 character slice names (e.g. `120STEREO-01`, `-02`...) all collapsed to one `120STEREO--L`/`--R` pair.

## Slice Editor: SuperFlux detection (`core/superflux.py`)

A "Detection" combo beside the Sensitivity slider: **Simple (fast)** = `core/transient_detection.py` (the default, unchanged: analyse once, re-pick per tick) or **SuperFlux (high quality)**
(VelociLoops' onset detector). The choice is remembered (`app_config` `slice_detection`); SuperFlux is greyed out if the library didn't load, and an analysis error falls back to Simple with one message.
- VelociLoops only exposes detection as `vl_create_from_superflux()`, which builds a whole RX2; `superflux.detect` reads the cut points back from its slices. So **every slider value redoes the
  whole analysis** (measured 2026-10-12: about 36 ms per second of audio - 0.09 s for a 3 s loop, 2.8 s for 77 s). The dialog therefore debounces (120 ms), analyses on ONE worker thread
  (`ThreadPoolExecutor`, polled by a QTimer - deliberately no cross-thread Qt signals or QThread: closing the dialog mid-analysis can't touch a deleted object), and applies only the newest
  request's result. 0% is off and clears at once. It analyses the mono mix (`_analysis`).
- Sensitivity -> `threshold` is logarithmic, 4.0 (least) to 0.05 (most); ~30% is VelociLoops' default 1.1. Measured against ReCycle's own cut points on 7 of VelociLoops' drum loops:
  60/60 found with 0 extra markers up to ~50%, then more markers each step (3 extra at 60%, 35 at 100%); the simple detector found 20-70% at its best setting.
- To get real-time dragging on long samples, VelociLoops would need to expose its activation curve (the FFT stage) separately from peak picking; then analyse once and re-pick per tick like Simple does.

## File > Import Audio (`ui/import_audio.py`)

One File item in the Program Editor and the Yamaha editor (Ctrl+Shift+I) for any audio file (WAV/AIFF/FLAC/MP3/Ogg/Opus) or `.rx2`, opened in the Slice Editor: an `.rx2` with its cut points marked, a plain
file with NONE (exporting with no markers sends the whole selection as one sample, so it doubles as a trimmer). Stereo stays stereo exactly as for an `.rx2` (see above). `pick_and_load` is shared by both editors.
- **Length guard:** the Slice Editor holds the file as Python lists. Measured 2026-10-12, 10 min stereo MP3: decode ~1.2 s, peak RSS ~2.2 GB (~3 GB with the editor's mixes) = ~3.7 MB per second of stereo. So a file over
  `WARN_SECONDS` (60 s, the owner's choice) asks first (default No), and over `REFUSE_SECONDS` (15 min) is not opened. Checked with `sf.info` BEFORE decoding. `.rx2` loops skip the check (short).
- More than 2 channels is refused by `read_wav_channels` with a message. Decoding runs on the GUI thread (wait cursor), not a worker.
