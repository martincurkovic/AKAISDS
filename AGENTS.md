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
(MIT, `THIRD_PARTY_NOTICES.md` - keep the notice when porting more). `dxzl/akai-s950` has NO licence: don't copy it.

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
- **32-bit float WAVs read as silence** through `sf.read(dtype="int16")`; `sds_encoder` always reads float and scales by hand.

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
- All five send-confirmation dialogs default to Yes (owner's request); Filter Sample has a second confirmation after Preview.

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
