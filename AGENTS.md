# Agent notes for AKAISDS

Cold-start context for AI agents. It exists to stop you "fixing" things that look like bugs but are deliberate, and to say what was
MEASURED on real hardware versus guessed. Human docs: `README.md`, `BUILDING.md`, `TESTING.md`, `CONTRIBUTING.md`. Long-form findings
and plans: `dev_docs/`.

## Start here

| Working on | Read |
|---|---|
| anything that talks to a real sampler | "Real-hardware safety", "House rules" |
| the Akai Program Editor (`program_editor_window.py`) | `BridgeWorker`, "Program Editor UI", "Samples tab", "Global tab" |
| S1000 / S900-S950 / Yamaha A4000 | their own sections, then `dev_docs/` |
| Ensoniq EPS/ASR (list, receive, send; Generic SDS can't work) | "Ensoniq EPS/ASR", `dev_docs/ensoniq-support-plan.md`, `dev_docs/ensoniq-spec-crosscheck.md` |
| sending samples, the Slice Editor, sample edits | "Sample send hardening", "Sample edits and the working copy", "Slice Editor" |
| logs, bug reports | "Debug logging" |
| running with no hardware | "Developing without hardware" (contributor version: `CONTRIBUTING.md`) |
| tests | `TESTING.md` |
| releases, Homebrew tap, AUR | `dev_docs/packaging/PACKAGING.md` (workflow: `.github/workflows/publish-packages.yml`) |

## House rules

- Don't commit, branch, tag or push unless asked. The owner handles git. Ask before anything outward-facing.
- Never edit the pinned `s3k`/`s3ked` dependency. Wrap or override it here.
- Real hardware: read "Real-hardware safety" first. Back up before writing. Close other MIDI software.
- Keep user-facing text short (a line or two). Detail goes in the log.
- Record what was measured versus guessed, dated, with the unit it was measured on.
- Tests: `uv run pytest tests/ -q` (~5 min, ~2900 tests). The traps (offscreen Qt, threads) are in `TESTING.md`.

## What this app is

Launched from `src/main.py`. The `&Window` menu swaps between windows (one shown at a time).

- **Transfer Dashboard** (`ui/dashboard.py`, `ui/main_window.py`, `controller/sampler_controller.py`): sends and receives samples over MIDI SDS or the Akai/Yamaha/S950 dialects.
- **Program Editor** (`ui/program_editor_window.py`, `core/program_editor_bridge.py`): program, keygroup, sample and multi parameters on S1000 and S2000/S3000. Most of this file is about it.
- **S900/S950 editor** (`ui/s950_*`) and **Yamaha A4000/A5000 editor** (`ui/yamaha_*`): smaller separate windows, opened by the same Open Editor button.

**Sampler Types** (`core/sampler_models.py`): `akai_s1000`, `akai_s2000_s3000`, `akai_s900_s950`, `generic`, `yamaha_a4000`, `ensoniq_eps_asr`. The legacy `"akai"`
is accepted and rewritten at startup. The model is a user setting, never detected (S1000/S2000/S3000 answer identity identically).
`SamplerController.device_type` is the PROTOCOL FAMILY (`akai`/`generic`/`s950`/`ensoniq`) every transfer path branches on; the choice is
`sampler_model`. Yamaha is family `generic` plus `sampler_models.is_yamaha()`.

## Third-party deps (`s3k`, `s3ked`)

Pinned git deps in `.venv/lib/python3.12/site-packages/`: `s3k.bridge.S3kBridge` (the SysEx bridge), `s3k.params` (parameter registry,
`p.lookup(name, region)`), `s3ked.demo.DemoBridge` (a fake that reproduces real quirks). **Read `Parameter.notes` before trusting a
declared range**: they are dated hardware measurements, and past bugs came from trusting the manual's prose. Raise problems upstream.

Where our own measurement overrides `s3k.params` (don't "fix" back without re-measuring):
- `K_FREQ` (filter key-tracking): declared `-30..99`, measured `-24..+24`.
- `B_PTCHD` (bend down): declared `0..12`, measured `0..24`. `program_editor_bridge._HARDWARE_RANGE_OVERRIDES` / `_lookup_for_write` patch a corrected `Parameter` in front of every write.
- `LFO2TRIG`: declared `0..255`, measured a plain boolean.

## Developing without hardware

- `AKAISDS_DEMO_SAMPLER=1`: `program_editor_bridge.connect()` returns a `DemoBridge` (`core/demo_s1000.FakeS1000` for the S1000 model). Audio comes from `tests/test_audio.wav`, paced like a real receive. The standalone launcher at the bottom of `program_editor_window.py` needs this and `AKAISDS_DEMO_INSTANT=1`.
- **`--instant` / `AKAISDS_DEMO_INSTANT=1`** (`core/demo_timing.py`) removes the MIDI-speed emulation. The fakes answer at once, but the app's own engines pace themselves: the Yamaha session waits out each bulk message's wire time (31250 baud + 400 ms gap) plus a 4 s link settle and a 2.5 s verify; the S950 engine waits wire time plus a NAK window; the DemoBridge paces loads at `_DEMO_MS_PER_WORD`. Measured: one Yamaha edit send, 10.0 s paced, 0.0 s instant. `""`, `0`, `false`, `no`, `off` count as OFF. Accepted by `tools/a4000_demo.py`, `tools/s950_demo.py` and the launcher.
- `uv run python tools/a4000_demo.py` (`--editor`, `--smoke`) runs the real Dashboard on `core/demo_a4000.FakeA4000`. `tools/s950_demo.py` does the same for `FakeS950`.
- `uv run python tools/make_screenshots.py` regenerates the manual's pictures (`ui/help/screenshots/`) offscreen at 2x (`--list`, `--only NAME`, `--overwrite`, `--out DIR`). It never replaces an existing picture without `--overwrite` (hand-captured ones have the macOS title bar) and stubs out MIDI and the update check: creating a real rtmidi client can abort the process (OSStatus -304, see `midi_transport.list_input_names`). Add a picture with a `@shot` recipe in `tools/screenshot_recipes.py`; `tests/test_make_screenshots.py` checks every picture `quickstart.md` mentions has one.
- `uv run python tools/make_diagrams.py` redraws the MIDI cabling diagrams (`ui/help/diagrams/`, needs `rsvg-convert`). They are NOT in `screenshots/` on purpose.
- The fakes fake the MIDI PORTS, so the real bridge and session code runs on top. `FakeS1000` ignores 0x20+ ops like the real machine and pads blocks with `0xA5` so dropped trailing bytes show up.

## Real-hardware safety

**Incident, 2026-10-10 (the owner's S2000).** With Ableton open on the same MIDI interface the sampler had its own replies echoed back.
A sampler executes a returned data frame (MISCDATA/MDATA/xHEADER) as a WRITE and acks it with `REPLY OK`. Symptoms: a stray OK beside
every reply, replies running behind, "expected command 0x05, got 0x16". A dump that read misc bytes 6-9 then (the LOAD/DELETE/SAVE
triggers; they read 0, and 0 is itself a command) probably made it load, delete and save: it froze and could no longer boot from its hard
disk (restored from image backups). That is inference from logs; which Ableton setting echoes is unconfirmed. Other suspects were never
excluded: whole-block misc writes (a block read shows zeros for fields the sampler doesn't fill, and a write sends all 48 bytes back), a
LOAD-page write, the owner's SCSI experiments, a failing drive. Once the drive is healthy, compare a misc dump with
`~/.akaisds/misc_probe/app_tune_plus50.json` (taken before any block write).

Rules from it:
- **Never read byte registers 6-9** (`tools/s2000_misc_probe.NEVER_READ_INDEXES`, tested). Never add a tool that does.
- **Close DAWs and MIDI monitors** before running the app or a tool against a real unit. `tools/midi_echo_check.py` (sends only RSTAT) reports stray frames; run it with the DAW open, one setting at a time.
- Request/reply tools keep **one request outstanding**, accept only the reply naming what they asked for, and stop after a few unanswered reads. Never send into silence.
- Don't probe a sampler whose drives are disconnected or not ready: disk registers stall it.
- The SCSI controls are locked "for safety". Nothing here calls select_drive/device/partition/volume, trigger_load/save, clear_memory, or writes registers 0, 2, 4, 6-9.
- Any write path needs a backup first and a read-back that must match.
- **The app detects the condition**: `core/reply_matching.py` makes reads tolerate stray frames, and `BridgeWorker.stray_replies_detected` (3+ skipped strays) shows a dialog. Tolerance is not safety: the echoed frames still reach the sampler.

**Reply matching** (`core/reply_matching.py`) is installed by `connect()` on every real `S3kBridge` (the S1000 adapter sits on top; demo bridges
are untouched). `S3kBridge.send_and_receive` takes the first frame whose opcode could answer. `make_reply_tolerant(bridge)` patches one
instance: for READ requests it skips a stray `REPLY OK` (`REPLY ERROR` still returns) and, for reads whose reply echoes the item
(headers, misc bytes, multi), skips a data frame with a different index/selector/offset. Deletes, SETEX and block writes use the original
method. `bridge.skipped_replies` counts skips. Tests: `tests/test_reply_matching.py`, with a "fails without the layer" control.

## `BridgeWorker` (read before touching `core/program_editor_bridge.py`)

`S3kBridge` is unsafe for concurrent calls. A per-action `QThread` version crashed real hardware (`qFatal` when a stale thread was
mid-call during GC) and interleaved SysEx frames. `BridgeWorker` is ONE persistent `QThread` per editor, one job at a time. "Current
state" requests (keygroups, sample/multi detail, global settings, memory status) coalesce; writes and Program Changes never do. **Don't go
back to thread-per-action**: add a `submit_*()`/`_handle_*()` pair. Tests drain with `process_pending()`; window tests use the real
thread plus `wait_until_idle()` and must call `editor._worker.stop(); editor._worker.wait()` in teardown.

## MIDI transport consolidation (`core/midi_transport.py`)

The Dashboard (`mido`) and the Program Editor (`S3kBridge`/`python-rtmidi`) once opened two connections to one port, which raced.
`SharedMidiOutput`/`SharedMidiInput` (one real port each, write-locked, fanned out) fix it. `shared_midi_transport` (config; env
`AKAISDS_SHARED_MIDI_TRANSPORT`) defaults true, with no Settings UI on purpose.

Two real SIGSEGVs came from reacting to port state DURING a Settings dialog's diagnostics. **If you touch `_open_settings_dialog` or
`_reconnect_shared_bridge`, keep this order**: freeze the window (confirm `BridgeWorker` is idle) BEFORE the dialog opens, keep it frozen
while Settings is up, unfreeze only after the bridge is swapped in. Every early return must still unfreeze. After touching
`midi_transport.py`, `midi_manager.py` port opening or `connect()`, re-run the transport and bridge tests. The hardware test plan is
`tests/midi_transport_consolidation_test_plan.md`.

## Debug logging

`core/debug_log.py` -> `~/.akaisds/akaisds.log` (8 MB x 3, DEBUG). Ask for it (and the macOS `.crash` report) before guessing at a
hardware bug. Unhandled-exception backstops log here, never `print()`. One INFO line per user action is the convention.

- `LoggingBridge` wraps what `connect()` returns: every call START/END/FAILED with timing and raw frames.
- `core/diagnostics.py`: crash handlers (`sys.excepthook`, Qt messages, `faulthandler` into `crash.log`) and a `config (...)` line at startup and after each Settings dialog. `ui/diagnostics_ui.py` logs every static `QMessageBox` call and the button pressed (not `QMessageBox(...)` instances: log those yourself).
- `SamplerController` logs each distinct `status_changed` text. `SysexTrace` (owned by `MidiManager`) logs every SysEx the Dashboard, S950 and Yamaha editors send or receive (first 16 bytes + length, capped at 300 lines per 10 s per direction, never the payload).
- Send ACKs are not logged per packet: only the header, first data packet, stray/slow ACKs and a per-unit summary.
- **Unexpected SysEx is log-only on the Dashboard** (owner's decision; `_log_unexpected_sysex(..., quiet=True)`): DEBUG, capped at 50 lines per 10 s, never `status_changed`. A data packet before any header, or an S1000 error REPLY, stays a WARNING.
- S950: `S950Editor:` lines for what the USER did; `S950Transfers` logs `begin <op>`. Yamaha: `YamahaSession` logs each op's start and `done|FAILED in N ms`, and counts unsolicited SysEx while idle (WARNING at the 10th); `YamahaEditor:` lines cover selection, markers, audio loads, edits, sends, pitch detection.
- Imports: `rx2:` (a WARNING when VelociLoops fails to load: the first thing to check for "can't import / SuperFlux greyed out"), `ImportAudio:`, `superflux:`, `TransferDashboard: queued ...`, `SliceEditor: detection mode ->`. The startup `config (...)` line carries `slice_detection=` and `rx2_library=ok|MISSING`.
- **Akai Program Editor** (`ProgramEditor:` unless noted): drag and drop of program files, imports, stereo exports, program creation; sampler OS and memory once per window (the per-call RSTAT lines are DEBUG); each edit applied (`<edit> applied to <name>: N -> M frames, markers a -> b`), `Filter:` and `TimeStretch:` options, Undo, Revert, `Staged edit:` (backup, send), `ExternalEdit:` (open, auto-load), Detect Root Note (result and whether it was applied), envelope graph commits (`envelope graph edit on keygroup N: FIELD=value`), `tuning:` corrections for re-sent samples.
- Config changes are logged by `app_config.save_config`, so the collapsed-card and memory-bar settings need no line of their own.
- Not logged: the S1000 gating state, Yamaha per-write old -> new values (the write coordinator logs the new one).

## Akai S1000 support (`core/s1000_bridge.py`), written without hardware

- **Why a separate bridge:** `S3kBridge.get_parameter`/`set_parameter` use S3000-only byte-addressable ops (0x27-0x38); an S1000 stays silent. It has only whole-block RPDATA/RKDATA/RSDATA + PDATA/KDATA/SDATA (nibbled), so `S1000Bridge` does read-modify-write of whole blocks behind the same surface. The S3000 header layout is a superset at the same offsets (program 0-71, keygroup 0-148, sample 0-140), so `s3k.params` is reused; anything past that reads as a neutral zero and refuses writes.
- **Never assume the block length** (the spec says "about 150 bytes"): cache what the device sent, patch in place, write back the same length, log every raw block at DEBUG. Blocks are cached 1.5 s and invalidated by list/delete calls and raw frames.
- **UI gating** (`_apply_s1000_gating`, one-way at construction; a model change in Settings closes the editor) hides the modulation matrix, LFO2, portamento, LFO1 shape, bend down, resonance, 4-stage ENV2, the Multi and Global tabs, mute group and velocity start. ENV2 is a plain ADSR there (`_build_s1000_env2_adsr`). One bend field (`B_PTCH` 0-12). Narrower ranges: polyphony 1-16, key range and root note 24-127. After hiding widgets in an unshown window, `invalidate()`/`activate()` the layouts before `equalize_card_heights` re-measures.
- **Controller routing is editable** (`_build_s1000_controller_cards`): `s3k.params` declares most of these fields "not used" (range `0..0`), so `encode_field` would refuse writes and `decode_field` would misread negatives. `_S1000_RANGE_OVERRIDES` patches corrected copies on every read/write. **Check a new S1000 field's declared range first.**
- **The S1000 name rule is destructive:** a PDATA/SDATA whose name matches a DIFFERENT resident item deletes that one first. Duplicate and rename refuse a clashing name on an S1000 only; an UNCHANGED name writes nothing. `FakeS1000` assumes rewriting an item under its own name is a plain replace. The spec doesn't say: **the first thing a real S1000 must confirm.**
- **DELK** is a plain linked-list unlink (no compaction, no `GROUPS` update); a PDATA with a smaller `GROUPS` is rejected. Standalone Delete Keygroup is DISABLED (`KEYGROUP_DELETE_SUPPORTED = False`) after two failed real tests: don't re-enable or try variants blind. Create-from-slices never deletes (`first_keygroup_only=True`). The untested REBUILD alternative is behind `KEYGROUP_DELETE_BY_REBUILD` (off). Evidence: `dev_docs/s1000-delk-findings.md`.
- Duplicate Program/Keygroup and create-from-slices are enabled so a tester can find out whether they work (the Dashboard shows a one-time experimental warning). Not in either model's UI: `OUTPUT`/`STEREO`, `PLAYLO/HI`, `OSHIFT`, `TEMPER`, `KGTUNO`, per-zone `VZOUT`/`VFREQ`, loops 2-4, `DDATA`/`MDATA` blocks.
- Untested on a real S1000: `LOOPAT1`/`LLNGTH1`/`STUNO`/`SBANDW` semantics, the sample-edit actions through the adapter, and RSTAT (the memory bar and OS version).

## Program files: Save/Load `.p1`/`.p3`

Akai disk format: 150-byte header + N x 150-byte keygroups (`.p1`) or 192 + N x 192 (`.p3`), N at header byte 42. `core/akai_program_file.py` is
a pure codec; `BridgeWorker.submit_export_program`/`submit_import_program` do the I/O. Samples are NOT saved (zones reference them by name;
Load lists the ones not resident). The blocks are whatever RPDATA/RKDATA return, so every byte round-trips: don't decode them for this.
- **Pointer bytes** (`FIRSTKG`, `NXTKG`, per-zone `SBADD`, `LVXF/HVXF`, `TPNUM`) are addresses the SAMPLER assigns; a file holds stand-ins. **`_import_program` never sends a pointer the sampler didn't hand us.** After the create PDATA it reads the program back and stops if its `FIRSTKG` equals another program's address, checks each append for collisions, then read-compares the new program with the file and every other program with a pre-load snapshot. Any stop sets `program_import_blocked` for the session. No DELK: a failed load can leave a partial program (the message says so).
- Load makes a NEW program in the duplicate flow's order. A name matching a resident program is never sent. A `.p1` on an S2000/S3000 is converted after a confirmation (`convert_s1000_to_s3000`, tail bytes measured on a real S2000); the sampler's own conversion isn't replicated, so keep the confirmation's wording. Disabled in demo mode.
- Delete Keygroup by REBUILD (`_delete_keygroup_s1000_rebuild`, off): `.p1` backup, drop the keygroup locally, load as `-TMP`, DELP the original only after checks pass, rename. Tests: `tests/test_s1000_keygroup_delete_rebuild.py`.
- Layout and pointers are not yet checked against a file from another tool: `tests/akai_program_file_test_plan.md`.

**Drag and drop (Program Editor):**
- **In:** a `.p1`/`.p3` dropped on the window loads like Programs > Load... (same checks, conversion prompt and name rules). The load runs from `QTimer.singleShot(0)`, NOT inside the drop (its dialogs would hold the dragging app open). **One file at a time**: each load checks names against the sampler NOW. Refused while frozen or in demo mode; other files are ignored.
- **Out:** `_drag_program_out` reads the program FROM THE SAMPLER first (the same safe reads as Save, blocking with a wait cursor, 60 s timeout), writes `<NAME>.p1|.p3` under `dropped_files.session_dir()/program_drag/` and starts a `QDrag` with that file URL. A big program can pause the drag for seconds. Untested in a real Finder/Explorer drop.
- Test trap: a `QDropEvent` only POINTS at its `QMimeData`. Keep the mime object referenced or the next read segfaults (`_KEEP_ALIVE`).

## Akai S2000/S3000: measured behaviour (real S2000, 2026-10-07)

- **A new program does NOT land at the end of the list.** The sampler orders programs by `PRGNUM`; a clone carries its template's number and sorts right after it. **Never address the next KDATA/PDATA at `len(list)`**: `BridgeWorker._locate_new_program(names_before)` re-reads the list. `_handle_create_program` also reads the template's keygroups BEFORE the first write (the insert can shift it).
- Proven: creating a program mid-list, a 4-keygroup clone, `.p3` Save/Load (equal apart from pointers, which the sampler recomputes), the whole Slice Editor path (`tools/s2000_check.py`, `tools/s2000_slices_check.py`).
- A tool that talks to the bridge while a `ProgramEditorWindow` is open must wait for `window._worker.is_idle()`.

## Global tab (S2000/S3000 only)

The GLOBAL page's settings are misc registers that neither the Akai specs nor `s3k` name. They were found with `tools/s2000_misc_probe.py`
(read-only dumps, in `~/.akaisds/misc_probe/`). **`core/global_settings.py` is the registry; its docstring says what each measurement
pinned down and what is inferred. Read it before changing a map.**
- **Byte registers** (`GLOBAL_SETTINGS`): external controller 38 (Breath 0/Foot 1/Volume 2), output level 15 (raw 12 = 0 dB, 6 dB steps, inferred from 3 points), play note/channel/velocity 31/57/54, SCSI disk ID 11, sector 14, local ID 12. Written with `S3kBridge._misc_write_verify` (write, then trust the READ: the ack can lie). Confirmed on hardware: the external controller.
- **Whole-block data** (`BLOCK_SETTINGS`): program change channel, tune and fine tune live in the 48-byte `RMDATA`(0x10)/`MDATA`(0x11) block, not their registers (64/65/71 are stale copies). Offsets: 0-2 program change (channel 0-based, Omni flag, enabled flag; Off `[c,0,0]`, Omni `[c,1,1]`), 6 fine tune (cents+50), 7 tune (semitones+9), 8 output level. `BridgeWorker._set_block_setting`: fresh read, encode only that setting's bytes, backup once per session (`~/.akaisds/mdata_backups/`), write (REPLY must be OK), re-read and compare. A wrong length is never edited. Block reads retry (the sampler sometimes answers with an undecodable frame).
- **Spacing matters:** the first in-app block writes were acked and never applied (a write 3 ms after a read, verified 2 ms after the ack). `MISC_BLOCK_BEFORE_WRITE_S` (0.3), `MISC_BLOCK_AFTER_WRITE_S` (1.0), `MISC_BLOCK_VERIFY_ATTEMPTS` (3), through the hook `program_editor_bridge._sleep` (conftest makes it a no-op). Working theory, unproven.
- Confirmed on hardware: a block write changing only offset 0 (channel 1 -> 6) was acked, read back, and the panel followed. Off/Omni were not tried from the app.
- **Locked controls** (`ui/global_tab.py` `DISABLED_SETTINGS`; the whole tooltip is the reason):
  - Tune and fine tune ("Not working right now."): they write and read back but the sampler never uses them (the panel's TUNE screen and the pitch don't change, also after a page round trip and in a clean retest). Untried: a power cycle (CAREFUL, RAM samples are lost), byte 49 / item-cursor word 7, an unidentified trigger register.
  - SCSI controls and program change channel ("Disabled for safety."). The channel works but writes the whole misc block, one of the uncleared suspects above. Unlock only once a misc dump on a healthy drive matches `app_tune_plus50.json`.
  - To re-test without editing code: `AKAISDS_UNLOCK_GLOBAL=tune_semitones,tune_cents,program_change_channel` (any of those names, or `all`).
- `GlobalSettingsTab` (Ctrl+4) only emits `setting_chosen` on USER edits (combos `activated`, spinboxes/knobs debounced 250 ms). A control stays disabled until its value was read. The external controller is in TWO places (the Modulation card and this tab), mirrored both ways; a failed write re-reads everything. Demo mode says "Not available"; an S1000 hides the tab. Not offered: MIDI sysex channel (would cut the connection), MIDI-via-SCSI (never dumped).
- Hardware checks and the owner's steps are in the docstrings of `tools/s2000_mdata_write_check.py`, `tools/s2000_misc_probe.py` and `tools/s2000_full_dump.py`.

## Memory and OS version (Samples tab bar, Global tab "Memory & OS" card, Dashboard bar)

- The editor reads RSTAT through `BridgeWorker.submit_memory_status` (coalesced, `bridge.status()`, read-only). It is requested on every `_on_samples_loaded`, so sends, deletes, renames and Refresh update it. A failed read CLEARS the bars (`set_unknown`) rather than leave a stale figure.
- **The OS version is `midi_identity.akai_version_string(version_major, version_minor)`, NOT `DeviceStatus.version`**, which is the raw byte: a real S2000 on OS 2.00 sends major 17 and showed "v17.00". The panel number is byte - 15 (measured on 1.30 and 2.00; same helper as the Settings hardware test). A major below 15 is shown as is (the DemoBridge sends a plain one). Read the minor and major from `status.raw`: `DeviceStatus` has no `version_major`. Other models are unmeasured.
- One widget, `ui/memory_bar.py` (`MemoryBar`, `apply_memory_status`), shared by the Dashboard. The Samples tab's and Dashboard's bars are `collapsible=True`: a double-click thins them to 8 px without text (the percentage moves to the tooltip). ONE setting (`config.json` `memory_bar_compact`); a module-level notifier makes every bar follow a change. The Global tab's bar never shrinks. A disabled Dashboard bar (no MIDI input) takes no clicks.
- RSTAT on a real S1000 is untried (the spec lists it; `FakeS1000` answers it). The S1000 shows the Samples tab bar only.

## Keygroup mute group and velocity start (S2000/S3000 only)

- **Range card, "Mute Group" combo (`KGMUTE`).** Raw 0xFF = OFF, raw 0-31 = a real group, and **raw 0 is a real group, not "off"**: a zero-filled header is an ACTIVE group 0, and two keygroups over the same notes in group 0 mute each other (`s3k.params` notes, measured). The panel numbers groups 1-32, so the combo shows raw + 1 and maps by item DATA, never by index. A raw value it has no entry for (32-254) shows as a temporary "Raw N" item. Showing a keygroup never writes.
- **Zone page, "Vel. start" knob (`VSS1`-`VSS4`, signed +-9999 sample points).** Both are read with the rest of a keygroup (`_KEYGROUP_DETAIL_FIELDS`) and written through the debounced `_schedule_write`. Both are hidden on an S1000 (`KGMUTE` is at offset 160, past its 150-byte block; `VSS` unverified there).
- The Slice Editor and duplicate flows clone a template keygroup, so new keygroups inherit its `KGMUTE`: a template left at 0 makes all slices choke each other.
- Deliberately not done (owner's call): Envelope 3 (needs the expansion board that also adds the second filter; untestable), `PLAYLO/HI`/`TRANSPOSE`, voice toggles, `KGTUNO`, per-zone `VFREQ`/`VZOUT`, loops 2-4.

## Akai S900/S950 support (written without hardware)

A DIFFERENT protocol from the S1000 family: device byte `0x40`, function codes 0-11, every 8-bit value as two MIDI bytes, 10-char ASCII
names, XOR checksums, and a sample dump that is ONE SysEx with 4-byte handshakes (`F0 7E code F7`; the standard 6-byte SDS ACK is silently
ignored). Plan: `dev_docs/s950-support-plan.md`. **Nothing has run against hardware**; every guess is in `tests/s950_test_plan.md`. Ported
from [s950tools](https://github.com/diemonster/s950tools) (MIT; notice in `src/ui/help/THIRD_PARTY_NOTICES.md`, shown from About >
Licences: keep it when porting more). `dxzl/akai-s950` has NO licence: don't copy it.

- `core/s950_sysex.py` (codecs, framing, catalog, SPRM, sample dump) and `core/s950_program.py` (76-byte header + 1-31 x 140-byte keygroups). Only the program HEADER offsets are pinned by real captures. Unmodelled bytes stay in `raw` and are written back untouched; `to_bytes`/`to_payload` re-encode a field only if it no longer matches `raw`. There is no delete opcode: don't invent one.
- **Own protocol family `"s950"`: nothing may fall through to the akai/generic branches** (they'd put wrong bytes on the wire). `SamplerController` delegates S950 entry points to `controller/s950_transfers.py` and REFUSES the rest via `_s950_not_supported`. Add a path by delegating.
- `S950Transfers`: ONE operation at a time. An RCAT reply is the "device ready" barrier before every upload. Uploads are open loop (one SysEx, wait wire time, listen for NAKs) and ALWAYS go to an EMPTY slot (overwriting triggers a NAK storm), then the sample is named by SPRM read-modify-write. A receive streams a 4-byte ACK every 50 ms. Names are uppercase, 10 chars, unique. Always 12-bit; stereo averaged to mono. Needs a MIDI INPUT for everything. No memory bar, sparse slot numbers, no delete. Unmeasured risk: a receive is one message up to ~1 MB, and a backend that splits long SysEx would lose it (`MidiManager._on_raw_message` drops fragments not starting with F0).
- **Editor** (`S950ProgramEditorWindow`; shares the Dashboard's controller, no `BridgeWorker`). **Writes are STAGED**: edits change a copy, then `S950Transfers.write_program` does a fresh RPRGM, **refuses unless a `.syx` backup saved** (`~/.akaisds/s950_backups`), applies ONLY the changes onto the fresh copy, PRGM, a NAK window, then an RPRGM read-back compare. It never creates a program or changes the keygroup count. One-time experimental warnings before the first Send and first write. "Write Program Back Unchanged (test)" is a tester's first step. Not done: create/duplicate/delete program, add/remove keygroups; renaming a sample doesn't rewrite programs using it.
- The Samples tab is read-only: per-sample SPRM reads run one at a time, only while the tab is active; audio arrives asynchronously into a temp WAV and shows only if still selected.
- Pitfalls: a standard SDS ACK on channel 0 (`F0 7E 00 pp F7`) collides with the S950's own request-sample-dump: never test Generic SDS against an S950 casually. `QListWidget.clear()` defers deleting row widgets: tests that repopulate lists must flush deferred deletes (the `dashboard` fixture does) or they segfault later.

## Yamaha A4000/A5000 editor

The owner has an A4000 only. **Every measured protocol fact, the open-verification list and hardware tools are in
`dev_docs/a4000-editor-roadmap.md`; loading is in `dev_docs/a4000-native-load-findings.md`. Read them before touching this.** Code:
`core/yamaha_*` (codec, 204 param rows, wave/load/markers/edit/restore), `controller/yamaha_session.py` (conversation engine),
`yamaha_transfers.py`, `ui/yamaha_*`.

**Wire rules (don't regress):**
- **No worker thread.** `YamahaSession` is event-driven on the GUI thread, ONE op at a time (a select is stateful) via a FIFO. **Never connect `controller.on_sysex_received` to `MidiManager.sysex_received` yourself**: it already is, and a second connection delivers every message twice. A value is trusted only after a fresh announce naming the right object.
- A select gets NO reply (the unit announces its object first). Bulk byte count is MSB-first and a dump is several blocks in one F0..F7: don't rewrite `parse_bulk_dump` to the manual's wording. `tests/fixtures/a4000/*.syx` are REAL captures: never regenerate them from the codec. `tools/a4000_discovery.py` is read-only; `a4000_write_verify.py` WRITES (throwaway units only, app closed).
- **Audio uses the native wave dump ("WD"), not SDS** (SDS stalls after a stereo recording and can't reach the right channel). **A bulk dump can't be aborted**, so a cancelled wave makes the session DRAIN (up to ~70 s). `is_sds_transfer_busy()` is what the session waits out; `is_transfer_busy()` also counts a Yamaha receive: never make the session wait on that.
- **Bulk Protect ON makes the unit cancel SDS sends and silently ignore edits.** Check it first when something "does nothing".
- Manual deviations: sample `P2=66` (AEG sustain) is `P3=2`; wave/loop addresses are coupled; `sampling_frequency`/`wave_length`/`wave_end_address` are ignored on a BUILT-IN sample; every edit sets the object's "edited" flag.

**Writes:** `write_parameter(row, value, object_name, cb, slot=)`, one parameter per op. The first write per object per session dumps it and saves a
`.syx` backup (`~/.akaisds/a4000_backups`): **no backup, no write**. It is sent only after an announce names the right object, and a read-back
must equal the value. `WriteCoordinator` throttles (150 ms), shows the one-time warning and re-reads when writes settle. Restore is guarded
writes of differing rows (NOT a bulk load), snapshot first. Assign/Remove is `change_link` (the answer to a link request is the verification).
`FieldPanel.set_editable` enables only writable rows. The window refuses to close or refresh under a write.

**Edits and slices make NEW samples; nothing is overwritten** (no delete/overwrite over MIDI, and a re-sent name resets ALL parameters).
Copies are named `<name> EDIT` (working copy), `TRIM`/`REV`/`FADE`/`NORM`/`FILT`/`STR`/`PITCH` (a dialog's own "Send as New Sample"), 16
chars, a clash adds ` 2`, and go through the native bulk load (`yamaha_edit.params_for_copy`). "Also fill a program with the slices"
ASSIGNS slices to an EMPTY program from key 36 up, max 92.

**Native loading** (`core/yamaha_load.py`, `YamahaTransfers.send_file_queue`): wave dump(s) + one SP, paced by wire time + `send_gap_ms`,
then VERIFIED. It never overwrites (a clash gets ` 2`); wave names are random `SMP nnnnnn` (a wave under an existing wave's name IS
overwritten in place). **Silent unit after bulk loads:** it can show "MIDI Bulk Received" and answer nothing until a person presses OK
(Knob 5), most often at the first link after loads. The app copes with `_recover_silent_link` and asks before assigning a sample this run
loaded (`midi_loaded_names`). Watch the unit's display during loads and assigns.

**UI decisions:**
- Cards in aligned pairs are pinned equal (`equalize_card_heights`). `Knob` arcs grow from zero for ranges spanning zero (`setBipolar(False)` opts out). The stereo waveform is ONE display (two views via `set_stack_position`). The loading bar follows `YamahaSession.working`.
- Sample list rows have NO item text (name in `Qt.UserRole`; use `tab.sample_names()`). **No per-sample durations or memory bar** (each needed a slow scan): don't bring them back without a cheap source.
- **Long operations freeze navigation** (`_apply_lock`/`_is_busy`), deliberately not `main_tabs.setEnabled(False)`.
- Markers: `core/yamaha_markers.py` models the unit's FOUR addresses and orders writes (it silently ignores a write that breaks start <= loop_start <= loop_end <= end). Never write the view's loop markers back wholesale.
- Dropdowns whose raw order is assumed from the manual: roadmap item 4.
- **Detect Pitch** shares Detect Root Note's engine. Two guesses, one-line fixes: `FINE_TUNE_STEPS_PER_CENT = 1.0` and the sign. **A5000 gating** is unverified: one identity request per session, and only an A5000 gets effect4/5/6 outputs.
- Testing: replies arriving over time use `rig.midi.paced = True` on the shared `_Midi` fake. Never swap a test object's `__class__` (intermittent segfault), and never let a bulk string replace touch more than one place in `yamaha_session.py`.

## Ensoniq EPS/EPS-16+/ASR-10 (written without hardware)

Nobody involved owns an Ensoniq unit; the plan is to ship an experimental build to forum testers. Research: `dev_docs/ensoniq-support-plan.md`;
the EPS-spec cross-check (corrections to it, read it before coding): `dev_docs/ensoniq-spec-crosscheck.md`. The specs themselves are copyright and not in the repo.
- Built so far: `core/ensoniq_sysex.py` (pure codec; `tests/test_ensoniq_sysex.py`), `core/demo_ensoniq.py` (`FakeEnsoniq`, fakes the MIDI ports like `FakeS950`; **built from the same reading of the spec as the engine, so it catches our inconsistencies, not a misreading or a real unit's quirks**), `controller/ensoniq_transfers.py` (`EnsoniqTransfers`: list, receive and send), the Sampler Type `ensoniq_eps_asr` ("(untested, experimental)" in Settings), `tools/ensoniq_demo.py` (`--smoke`; the real Dashboard on the fake) and `tests/ensoniq_test_plan.md` (what a tester does). Not in `quickstart.md` yet.
- **Engine rules:** ONE GET conversation at a time (RESPONSE ACK -> the PUT's first message, which we ACK within 2 s -> the data-only message, which we ACK; the unit never retransmits, so a damaged message is NAKed and the GET repeated). Errors are RESPONSE statuses (05 = instrument absent is a normal answer). No list command, so the list is PROBED: GET INSTRUMENT 0-7, GET LAYER 0-7, GET WAVESAMPLE PARAMETERS for every number in the layer's key map (layer block words 19-106). The Dashboard "sample number" is `ensoniq_transfers.pack_number(instrument, layer, wavesample)`. The audio range is `[start, end)` from the parameter block in `chunk_words` pieces; the unit's end may be inclusive (the engine accepts both and keeps the last word only then). It needs a MIDI input and a Device ID of 0-15 (sent as the wire channel byte).
- **Own protocol family `"ensoniq"`: nothing may fall through to the akai/generic branches.** `SamplerController` delegates `refresh_sample_list`, `receive_samples`, `send_file_queue`, incoming SysEx and `cancel_transfer` to the engine and REFUSES everything else with `_ensoniq_not_supported` (the legacy per-file senders, rename, delete, sample info, the by-number receive); the Dashboard disables Delete/Edit/Open Editor with `tooltips.ENSONIQ_*` and shows a one-time experimental warning before the first send (`_confirm_ensoniq_experimental`); Settings > Run Hardware Test sends the universal MIDI identity request (optional answer, logged with its version) and then ONE GET INSTRUMENT 0, one request outstanding at a time, and accepts only a RESPONSE or PUT (our own request echoed back by a DAW is ignored); every byte sent and received is logged at INFO.
- **Memory bar:** after each list the engine reads free blocks (system parameter 0) and every instrument's size (instrument parameter 7, read only; the instrument block's own size word is one byte, too small) and emits `memory_status_updated` with total = free + used, 256 words a block (GUESS; the percentage doesn't depend on it). Those reads are `optional` exchanges: a unit that doesn't answer them still lists. A zero total in the emitted dict means "unknown" to the Dashboard (`set_unknown`).
- **Sending (WRITES, the most guessed part):** each file becomes a NEW instrument in the first free slot (found by probing GET INSTRUMENT: an existing instrument is never touched), one layer per channel (stereo = two layers, Pan 1 / 8, unverified), one wavesample per layer: free-blocks check (`34 00`) -> CREATE INSTRUMENT -> CREATE LAYER -> GET its parameters -> PUT them with name / root key 60 / rate code / one-shot -> TRUNCATE -> PUT WAVESAMPLE DATA in `put_chunk_words` pieces -> GET/PUT parameters again to set start 0, end `len + end_adjust` (-1, see below), loop -> [pan] -> read back the parameters and two stretches of audio. **Never PUT INSTRUMENT / PUT LAYER**: they carry RAM pointers (the S1000 pointer lesson), so the instrument keeps the unit's default name. A failed send can leave a half-built instrument and nothing deletes it: `_partial_note` says which. Rates: the unit's rate is a CODE (period = code x 1.6 us), so audio is resampled to the nearest code's exact rate (44.1 kHz -> 44,643 Hz); more than 1,048,575 words is refused. Tester knobs on the engine (attributes, not Settings): `pace_ms`, `put_chunk_words`, `truncate_placeholder`, `end_adjust`, `put_end_inclusive` (the first audio chunk flips it once if the unit refuses the exclusive range), `silent_wait_ms` (PUT PARAMETER gets no answer but the 2 s timer is still waited out).
- **The assumptions a real unit must confirm** (all of them are in the fake too, so the tests can't): that PUT WAVESAMPLE DATA at the current length grows the wavesample, that TRUNCATE after the first parameter write is right, that end is written one short, the Pan values, and everything under "open" below.
- Never put `ensoniq_sysex.NEVER_SEND` (delete instrument/layer/wavesample, SAVE INSTRUMENT) on the wire from a send or receive. The offset example `00 00 40 00` in two of the specs is a typo (`00 00 08 00`); a parameter number is ONE 12-bit word, not a page byte and an item byte.
- **The ASR-X is NOT covered by any of this**: it has no MIDI SDS, speaks `F0 0F 10 01 <dev> <cmd>` (not `0F 03`) and silently drops the ASR-10 dialect (ROM v2.67 read statically, 2026-10-17, `dev_docs/asrx-rom-findings.md`). It has its own Sampler Type, see "Ensoniq ASR-X" below.
- Unmeasured and open (all in the plan): the channel byte (base channel or SysEx Device ID), the sample-rate code (44.1 kHz isn't representable), how a wavesample grows past its placeholder, the ASR-X (no spec at all).

## Ensoniq ASR-X (written without hardware, from the v2.67 ROM)

Sampler Type `ensoniq_asrx`. Same protocol FAMILY `ensoniq` as the EPS/ASR entry (so every Dashboard branch is shared), a different DIALECT: `SamplerController._ensoniq` returns
`controller/asrx_transfers.py` (`AsrxTransfers`, `IS_ASRX`) instead of `EnsoniqTransfers`, by `sampler_model`. Codec `core/asrx_sysex.py`, fake `core/demo_asrx.py` (`FakeASRX`), demo
`tools/asrx_demo.py` (`--smoke`), tests `test_asrx_sysex.py`/`test_asrx_transfers.py`, tester steps `tests/asrx_test_plan.md`. **Nothing has run on an ASR-X**; the fake is built from the same ROM reading as the codec, so it
catches our inconsistencies, not a misreading. Every wire fact and its confidence is in `dev_docs/asrx-rom-findings.md` and the header of `core/asrx_sysex.py`: read them before changing a byte.
- **What it does** (Transfer Dashboard only, no editor): LIST by `7E <dev> 05 04 <slot>` name requests (stops after `scan_empty_run` empty slots; `7F 41` = empty), RECEIVE the `SMP1` object (52-byte header + signed 16-bit BIG-ENDIAN samples) with command 04 range reads
  (header first, then 640-byte pieces, then a re-read of the first piece), falling back to the whole-object command 01 if command 04 is never answered, SEND each file (stereo = two samples `NAME-L`/`-R`) with ONE command 02 message into the lowest free slot, then verify by name request, header read and tail read.
- **The rate lives in the header's pitch word** (+0x26, `-(round(3072 log2(29762/rate))) - 256 root`), so nothing is resampled unless the Dashboard's target rate says so. The WAV rate on receive is derived from it. The formula and the field offsets come from the ROM; the rounding differs by a few 1/256-semitone steps from the unit's own default `0xCACD`.
- **Free-slot rule (don't simplify):** a slot with no NAME can still hold something (the unit's recording buffers answer name requests with "empty"), so a candidate slot is accepted only if a range read of it gets SILENCE. An answer means "in use": it is skipped and remembered in `engine.hidden`. Never upload into a slot just because the name scan called it empty.
- **Never sent from here:** command 05 (partial write), 07, 10, 11, 12, 13, 15 (bank clears/replaces, a service jump) and command 02 with size 0 (clears a slot). Nothing is ever overwritten; a failed send can leave a half-written slot (`_partial_note`).
- **Logging is the debugging channel** (the tester is in another time zone): every request/reply goes to the log under `AsrxTransfers:` (DEBUG, capped per 10 s, plus `AKAISDS_ASRX_TRACE=1` for every message in full), the 52-byte header of everything received/sent is logged as hex AND decoded at INFO, each op ends with a stats line, and a failure dumps the last 12 messages in both directions at WARNING (`last messages before`). Keep those lines when refactoring.
- Unknowns left on purpose: header bytes 0x18/0x19/0x1C/0x22/0x24 are sent as the constructor's defaults; loops are not sent (loop mode 0) and not written into a received WAV; no memory figure (the bar shows "unknown"); the unit's reply to a memory-full upload is not known (no ack, so the app reports "nothing arrived").

## Transfer Dashboard

- **Open Editor** enables only with both ports selected, family `akai`/`s950` or a Yamaha model, and no transfer in flight.
- `Ctrl+T`/`Ctrl+E` switch windows. `Ctrl+1/2/3/4` are the Program Editor's tabs (Multi/Programs/Samples/Global); `Ctrl+Shift+I` is File > Import Audio.
- A short child in a `QHBoxLayout` next to something much taller gets stretched: fix with `setAlignment(child, AlignVCenter)`, not spacing.
- **Dropped/opened files get a stable local copy** (`core/dropped_files.py`, `~/.akaisds/dropped_files/<pid>/`), because an editor renders a crop to a temp file and deletes it before Send. Orphaned sessions are swept at next launch.
- **Compressed audio** (`.mp3`, `.ogg`, `.oga`, `.opus`) is just more extensions in `sds_encoder.SUPPORTED_AUDIO_EXTENSIONS`; everything reads through soundfile (libsndfile 1.2.2; frame counts exact on an ffmpeg-made MP3, checked 2026-10-12). AAC/M4A fails. A lossy file has no native bit depth, so `read_wav_native_bit_depth` falls back to 16.
- **32-bit float WAVs read as silence** through `sf.read(dtype="int16")`; `sds_encoder` reads float and scales by hand.

## ReCycle `.rx2` import (VelociLoops)

`core/rx2.py` reads `.rx2` through VelociLoops (`third_party/velociloops/`, public domain, vendored UNMODIFIED, commit `f77609e`) over `ctypes`.
`tools/build_velociloops.py` compiles it into `src/core/native/` (git-ignored); the three `scripts/build_*` bundle it and `test.yml` builds it
before the tests. **Windows/Linux builds and bundling are untested** (written on macOS arm64). No library means `Rx2Unavailable`, a short
message, and the `.rx2` tests skip. Only `.rx2` is accepted (the library also reads `.rex`/`.rcy`). Real loops from outside VelociLoops'
fixtures are untested.
- **Slices are RAW length** (`VLSliceInfo.sample_length`), not `vl_get_slice_frame_count()`, which adds a synthetic decay tail when transient-stretch is on (wrong for a sampler playing slices at their own pitch). Measured: the raw slices of `120Stereo.rx2` tile exactly 2.0 s (one bar at 120 BPM). Muted slices are kept.
- **Entry points** (all over the same decode):
  - **Dashboard** (drop or Open): `_import_rx2` queues ONE row (the slices laid end to end as a WAV, cut points in `RX2_MARKERS_ROLE`, a "N slices" badge). **Send cuts it into one sample per slice** (`_expand_rx2_entry`, using the row's CURRENT name and settings; files under `rx2_slices/`). A stereo slice's base name is 10 chars so `-L`/`-R` fits (12 if sent mono or to a Yamaha). Its Slice Editor opens with the cut points marked; exporting replaces the row with ordinary slice rows.
  - **File > Import Audio** in the Program Editor and Yamaha editor (below).
  - The S950 editor has no entry point.
- **A stereo loop stays stereo:** each slice is sent as TWO mono samples, `<name>-L`/`<name>-R` (`_STEREO_SUFFIXES`; the dialog's `channel_suffixes` shortens names to 10 chars and checks the suffixed ones for clashes). The Akai program (`_create_program_from_slices(stereo=True)`) puts left in zone 1 and right in zone 2 of each keygroup: `SNAME1/2`, `CP1/2`=1, `LOVEL/HIVEL` 0..127, `VPANO1`=-50 / `VPANO2`=+50, zones 3-4 empty. **Unverified on hardware:** whether +-50 pans a zone hard left/right, and the two-zone keygroup writes (the single-zone builder it extends is proven).
- **Updating VelociLoops:** `uv run python tools/update_velociloops.py` (`--ref`, `--dry-run`) fetches all four files before writing any, records the sha in `third_party/velociloops/VERSION` and the docs that quote it, rebuilds and runs `tests/test_rx2.py`. Run by hand and review the diff; never from a build (the library parses untrusted files).

## Sample send hardening (`SamplerController._send_current_packet`)

Akai SDATA and generic SDS sends share ONE packet loop; every batch (Dashboard Send, editor Duplicate/Send, Slice export) uses it.
- **Timeout = re-send, not skip** (`_send_packet_max_retries` = 3) once the sampler has ACKed at least once; a sampler that never ACKed gets 0 retries (open-loop fallback). Timeout is 500 ms until the first ACK, then `max(2000, 4 x slowest ACK)`.
- ACKs carry the packet number; an ACK for a different packet is ignored (`stray_acks`). Once `_no_response_detected` is set, ACK/NAK/WAIT are ignored (`late_acks`). WAIT re-arms to 30 s; expiry aborts (SDS CANCEL, no DELS).
- Replies stopping MID-send: skip the stuck packet, finish THIS sample open loop at 100 ms/packet, mark it unverified, then CANCEL THE REST OF THE QUEUE and say how many weren't sent (aborting instead left a partial, garbled sample).
- After a user cancel of a receive, SDS packets already on the wire are dropped for 3 s (`_RECEIVE_CANCEL_GRACE_S`).
- Open loop is deliberate with no MIDI IN or Generic SDS. In S2000/S3000 mode with an input and a dead cable a send never starts (the pre-send RSLIST gets no reply): correct.
- **Measured on a real S2000 (don't re-derive or "tune" without re-measuring):** closed loop ACKs a packet every ~50 ms; the header ACK takes ~270 ms. The theory that 40 ms open-loop pacing caused silent samples was disproved and reverted. **A new sample lands in the LOWEST FREE slot**: find a sent sample by NAME, never by the slot you asked for.
- Tests: `tests/test_sampler_controller.py` (a fake `QTimer` fires immediately; monkeypatch `singleShot` to capture delays).

## Program Editor UI

- Program/Keygroup tabs are `_build_section_card(title, *rows)` cards in `_build_scroll_area(page)`. Width is the real constraint: re-measure `horizontalScrollBar().maximum() == 0` on both tabs if you widen anything. Cards pin `Preferred, Fixed` vertically; `equalize_card_heights()` pins paired cards (call it for any new pair).
- **Shared layout (`ui/editor_layout.py`)**: the S3000 and S950 editors build from the same functions. **Change a measurement there and BOTH editors change: re-check both.** `CARD_SPACING` is 6; a card page uses margins `(0, 0, 8, 0)` (a page in a scroll area has Fusion's 9px left margin); spacing inside a card is 10. The windows stay separate classes on purpose (live writes vs staged writes).
- `BridgeWorker.busy_changed` drives the Refresh bar through two debounce timers; don't remove either without reading `test_a_new_busy_true_during_the_hide_grace_period_cancels_the_hide`.
- **`QListWidgetItem` + `setItemWidget` double-paints if the item also has text.** Build items with NO text and keep the display in the widget. A row widget's `sizeHint()` is used as-is, so give it its own margins.
- Visibility tests use `.isHidden()`, not `.isVisible()` (a never-shown window is never visible).
- **A control that disables ITSELF when clicked must give up focus first.** A disabled widget hands focus to the next one and a `QScrollArea` scrolls to reveal it (Load Edited Audio threw the Yamaha page ~220 px down). `_set_sample_control_enabled` (Akai), `YamahaSamplesTab.update_edit_buttons` and `qt_helpers.set_enabled_without_scrolling` do it. Any new self-disabling control needs the same (`tests/test_button_focus_scroll.py`).
- **A fresh `Knob.setValue(0)` emits no `valueChanged`**, so a readout wired only to that signal stays "-". Fill it explicitly when loading and after any `blockSignals`.

**Collapsible cards** (`ui/section_card.py`): every editor card has a painted Finder-style arrow beside its title; the Settings dialog and Dashboard keep plain cards.
- `qt_helpers.build_editor_card(scope, title, ..., collapsible=True, key=None)` returns a `SectionCard`. Editors reach it through `ProgramEditorWindow._build_section_card` or a per-file `_card = partial(build_editor_card, "<scope>")`. Scopes: `program_editor` (Akai, including the Global tab), `s950_editor`, `yamaha_program`, `yamaha_sample`. Only the body hides; `header_widgets` (Loop Preview's zoom buttons) hide with it.
- **A pair pinned by `equalize_card_heights` is a GROUP**: it collapses and expands together and is re-pinned to the tallest member (`pin_equal_heights`). A lone card follows its size hint.
- State is remembered per `"<scope>/<title>"` in `config.json` `collapsed_cards` (cached per process; `section_card.reset_state()` is autouse in `conftest.py`). **A title must be unique within its scope**: the keygroup "Modulation" card has `key="Keygroup Modulation"`. `tests/test_section_cards.py` checks every editor. Not collapsible: the waveform cards (Loop Controls on the Akai and Yamaha Samples tabs, Waveform on the S950's) and the Zone card.
- **Edit Sample holds the unsent-edits strip**, so `_sync_edit_sample_card` shows an amber dot while it is collapsed and OPENS it (`set_collapsed(False, remember=False)`) when a working copy first APPEARS. Only on that change, so collapsing it while editing isn't fought.
- Traps: collapsing clears focus out of the body first; `refresh_layouts()` also invalidates the BODY's layout (the S1000 gating hides widgets in an unshown window and a stale body hint pinned a card 426 px tall); a QWidget SUBCLASS paints its QSS card frame only with `WA_StyledBackground`.

**Envelope graphs** (`ui/envelope_graph.py`): each stage has a FIXED width budget scaled only by its own value. Never re-normalize one stage against the others.
They are also draggable (NOT tried on hardware): Akai ENV1/ENV2 (the S1000's ENV2 is the ADSR graph), S950 amp/filter, Yamaha AEG/FEG/PEG.
- `_EditableGraph` owns frame, curve, handles and dragging; each graph supplies its curve points, which are handles, and pure inverse maths (`adsr_/env2_/level_values_for_handle`, tested to round-trip with the drawing). **A graph NEVER writes.** It emits `values_changed({name: value})` live, `edit_committed({...})` once on RELEASE (changed values only) and `edit_cancelled()`. **Owner's rule: knobs and graph move live, MIDI goes out only on release.**
- **Inert until `set_editable(True)`** (values loaded, writable, not frozen; S950: a program shown). Akai: off in `_on_keygroup_selected`, on at the end of `_on_detail_loaded`. Yamaha: `_sync_graph_editable`.
- **Akai** (`_wire_envelope_graph`): `values_changed` sets the knob with signals BLOCKED (which also keeps `_schedule_write` from firing) plus its readout; `edit_committed` calls `_schedule_write` + `_flush_write`, the path a knob release uses. **Yamaha**: `FieldPanel.set_value_quietly` while dragging, `FieldPanel.commit` on release, then the ordinary `_on_edited` and `WriteCoordinator` (so the one-time warning appears at the RELEASE, and declining puts knobs and graph back to what the unit holds). The AEG graph draws TIMES 0-99 while the unit has RATES 0-127 (higher is faster): `yamaha_adsr_rates` inverts `yamaha_adsr_values`. **S950**: `values_changed` just sets the knobs (an edit of the staged copy).
- Interaction: handles keep the grab offset (no jump); Shift is `_FINE_DIVISOR` slower from the press (relative, no cursor warp); Esc cancels (the keyboard is grabbed for the drag instead of taking focus, which could scroll the page); double-click resets what a handle controls to `set_defaults` (the knobs' defaults) and commits; hiding the graph mid-drag cancels; `set_values` is IGNORED during a drag. **No tooltip while dragging** (owner's call: it got in the way).
- The flat sustain line is a drag target too (`SEGMENT_POINTS`; ADSR graphs and the Yamaha filter/pitch EGs): up/down only, sustain alone. Handles win where they meet it. ENV2's 4-stage graph has no flat part.
- **Overlapping handles** (instant attack and decay; Yamaha factory EGs are all rate 127): every handle within 2.5 px of the nearest is a candidate and the first 3 px of movement decides (sideways takes the first that can move sideways, up/down the first that can move vertically). Without this the later handles of a default envelope are unreachable.
- Graphs are `GRAPH_SIZE` (280 x 110); the curve is inset 8 px so an edge handle is whole.

## Theme preference (Settings > Appearance)

`config.json` `"theme"` is `system` (default, follows the OS live), `light` or `dark`; unknown values read as `system`. `ui/theme.py`:
`apply_to_app()` at startup, `set_theme_preference()` live. The Settings dialog saves and applies when an option is PICKED
(`combo_theme.activated`), not on OK, so Cancel doesn't undo it.
- **A pinned theme follows the preference, never `colorScheme()`** (Qt's override is best-effort and offscreen ignores it). Only "System" asks the OS.
- **Colours baked in at widget construction can't follow a live switch.** Use a QSS rule (`QLabel#mutedLabel`) or hook `theme.notifier.changed`. Paint-time reads of `current_palette()` are fine.
- Chevron SVGs are named per colour (`down_arrow_<hex>.svg`): Qt caches stylesheet images by path.
- **Tests that switch theme must not restyle the real `QApplication`** (the suite keeps many windows; it went from 20 s to minutes). Pass a stand-in app to `apply_to_app`; `deleteLater()` editor fixtures.

## Update checker

`core/update_checker.py`/`ui/update_helper.py` poll GitHub `/releases/latest`, which ignores drafts: an unpublished tagged build means
the checker reports the previous release, not a bug. `UpdateCheckRunner` is a `QThread`: `.wait()` it before closing.

## PRGNUM and Program Change (Multis tab)

No SysEx write assigns a program to a multi part: only a raw MIDI Program Change addressed by the program's own `PRGNUM`, not its list
position. Fresh programs often share `PRGNUM == 0`; `BridgeWorker.renumber_programs()` runs before the first Program Change and on every
list reload (if "assigning picks the wrong program" recurs, check for collisions). The +1 display offset lives in `s3k.params`
(`display_offset=1`); `_handle_program_change` subtracts it back for the raw byte. The 16 part combos hold their own copy of program
names: renaming needs `_update_multi_program_combo_names()`. Multi FILE format research: `dev_docs/s3000-multi-file-format.md`.

## Note names

`core.midi_notes.midi_note_to_name()` calls note 60 **"C3"** (the S3000XL panel's convention). Don't "correct" it to C4. `ui/note_spinbox.py`'s parser
must stay in sync (a round-trip test guards it).

## Samples tab: hardware quirks

- **`LOOPAT1` is the loop's END**, and `LLNGTH1` measures backwards from it (`[LOOPAT1 - LLNGTH1, LOOPAT1]`). `s3k.params` doesn't say so. `loop_start` is always derived.
- **`LLNGTH1` is 32.16 fixed point** (`frames * 65536`, `_LOOP_LENGTH_FIXED_POINT_SCALE`). Get it wrong and loops "work" in-app but read ~65536x too short on hardware.
- **`STUNO` (sample tune) is signed 1/256-semitone** despite being declared unsigned: sign-extend on read, wrap on write, range +-50.00 st. The cache holds the RAW value, never display semitones.
- Loading a sample's audio is a real SDS dump (minutes for a big one); the UI freezes on purpose. `_wait_for_any_signal` is the ONE place that blocks on a signal; its `start` callable runs only AFTER listeners connect. Audio comes from the Dashboard's `sampler_controller` (a second connection); header fields come through `BridgeWorker`.
- **Loop markers:** drag and spinbox both go through `_schedule_marker_write`/`_flush_marker_write`; markers PUSH each other; only changed fields are written. Loop editing needs only the HEADER (`has_header()` true, `has_waveform()` false is editable): gate on `_frame_count > 0`, and reuse cached markers if audio arrives later (`_markers_from_header()`).
- **Loop type gating** (`SPTYPE` No looping/One-shot): loop markers disappear (`set_loop_enabled(False)`); dragging Start/End then freezes them, which can break `start <= loop_start <= loop_end <= end`. Code transforming audio must read `markers_with_loop_in_range()`, not `.markers()`.
- **Zoom:** `_max_zoom()` stops at `_MIN_VISIBLE_FRAMES` (32) on screen, not one frame (a drag is still sample-accurate there); the Slice Editor's `SliceWaveformView` shares it via `waveform_view.max_zoom_for`. Pinch is `NativeGesture`; `angle.x() != 0` always means pan; an unmodified vertical scroll does nothing on purpose.
- **Loop Preview zoom** (`ui/loop_preview_view.py`, shared with the Yamaha editor): `LoopJoinPreview` owns the zoom (`ZOOM_LADDER` 19..2400 frames each side, default 150, session-wide, not persisted) and emits `zoom_changed(frames)`; the owner re-requests `half_window()` frames (`_refresh_loop_preview_for_zoom`). Drag sensitivity follows (frames per pixel is measured from what is shown). Three tiny buttons sit beside the card title through `build_section_card(header_widgets=...)`; Ctrl+scroll and pinch zoom too (a plain scroll is left for the page).

## Sample edits and the working copy (Program Editor; NOT tried on hardware)

Every action is a button AND a `QAction` (`_make_sample_edit_control(key, text, tooltip, slot)`, kept in `_sample_edit_controls[key] = (button, action)`).
**A new action = a `_make_sample_edit_control` call + a key in `_SAMPLE_MENU_GROUPS`/`_EDIT_SAMPLE_ROWS` + a line in `_set_sample_edit_buttons_enabled`.**
Enable and disable ONLY through `_set_sample_control_enabled`, so the two never disagree. The Sample menu and the sample list's right-click menu
share those QActions. Menu actions are enabled only on the Samples tab (a destructive item must not act on a sample the user can't see) and
the menu is disabled while `_hardware_busy_ui` or a transfer runs (`_sync_window_menu_busy_state`: its 150 ms timer must not re-enable it
mid-operation). Don't name a menu action Settings/About/Quit/Preferences (macOS moves those). No shortcuts: Ctrl+1-4, Ctrl+T/E and Ctrl+Shift+I are taken.

**The working copy.** A sample can hold edited audio that is NOT on the sampler: `_staged_edits[name] = _StagedEdit(samples, markers, original_*, history)`.
Trim, Reverse, Fade, Normalise, Filter, Stretch / Pitch and Load Edited Audio change the working copy through `_apply_sample_edit` ->
`_stage_working_copy` and send NOTHING. Only **Send to Sampler** and **Send as New Sample** write to the unit. (The `_confirm_*` names are
historical: they don't confirm.)
- No confirmation on an edit (Undo and Revert make it cheap). Confirmations remain before something reaches the sampler (Send to Sampler, default Yes) or discards work (Revert, closing with unsent edits, default No).
- **Undo:** `staged.history` keeps up to `_UNDO_LIMIT` = 10 previous states (each a whole copy of the audio, ~88 KB per second of mono 44.1 kHz). With none left, Undo goes back to the sampler's version. Marker/loop drags are not history items.
- **The cache entry IS the working copy while one exists** (`_apply_staged_to_entry`), so every action (preview, Filter, Stretch, Duplicate, Detect Root Note, Slice Editor, external edit) works on what is shown. The cache is wiped on every sample-list reload, so `_on_sample_detail_loaded` re-applies it over the freshly read header (which still supplies tune, root and loop type).
- **Marker/loop edits stay LOCAL while staged** (`_schedule_marker_write` stores them in `staged.markers`): writing them would put values for a different buffer on the live sample. Without a working copy they write at once. Root note, tune, loop type and hold are header fields and stay live either way.
- **`_sampler_audio(name, entry)` is what Duplicate and the Slice Editor retain** for the ORIGINAL name; retaining the working copy would hand it back as "the sampler's audio" after the reloads.
- **Lifetime:** survives selecting other samples and reloads; follows a rename; a user delete drops it; a sample that vanishes from the list drops it with a status message (`_prune_staged_edits`), **except while `_hardware_busy_ui`**, because a replace deletes the original and renames its copy back, so the name is briefly missing. Not kept across restarts.
- **The strip** (`_staged_banner`, `QFrame#workingCopyStrip`) is ALWAYS present so the card never changes height. It is styled by the dynamic property `unsent` (set in `_refresh_staged_controls`, re-applied with `_repolish`; tests read `property("unsent")`, not `isHidden()`). Send to Sampler is `primary=true` (QSS accent fill); the amber wash is the `warning_tint` palette key (`#AARRGGBB`). The external hint is INLINE with an Ignored size policy (it once gave the tab a horizontal scroll bar). The list-row marker is a real widget (`build_sample_list_row_widget` -> `row_widget.unsent_dot`; `_sample_row_dots`).
- **Filter and Stretch / Pitch dialogs** (`applies_to_working_copy=True`): OK applies to the working copy; their "Send as New Sample..." sends the result at once as a new sample (`_send_edit_as_new_sample` -> `_perform_duplicate_sample_real(..., samples=, markers=)`; name default is name + " FILT"/" STR"/" PITCH"; same no-clash rule as Duplicate; off in demo mode, which can't add a sample).

**Send to Sampler** runs the old replace pipeline (`_perform_sample_edit_real`):
- **Send-then-delete under a temp name:** send the replacement as `<name>-TMP`, confirm it's resident, delete the original, rename back. Never delete first, never send under the original's name (zones resolve samples by NAME; two samples sharing a name make every zone using it ambiguous).
- **A new sample takes the LOWEST FREE slot (measured)**, so the original's index goes stale: find the original and the `-TMP` copy BY NAME (exactly once each, else delete nothing). Never delete by a remembered index.
- A re-sent sample lands with the sampler's defaults, so 8 header fields are written back (`SPTYPE`/`SPITCH`/`SHLTO`/`STUNO` from the source, `SSTART`/`SMPEND`/`LOOPAT1`/`LLNGTH1` from the new markers). Duplicate does the same.
- **Re-sent samples keep their pitch** (found on the owner's S2000 from the log). `STUNO` bakes in the baseline for the sample's `SBANDW` bucket, and the sent sample lands in the bucket the SEND chose (the nearest for a 16-bit Akai send: 11025 Hz -> SBANDW 0, baseline -12), while a sample the sampler imported itself sits on SBANDW 1 (baseline -24). Copying `STUNO` across made one play an **octave low** (source `SBANDW 1, STUNO 59392` = -24.00 written onto a bucket-0 header). `_tuning_for_resent_sample` reads the NEW sample's real SBANDW/SSRATE and writes `(source STUNO - source baseline) + new baseline`; a failed read keeps the source's value and logs a WARNING. Slice export does the same (`source_sbandw=`, `source_rate=`); **audio from a FILE gets no `STUNO` write at all** (writing 0 over the baked baseline played a 11025 Hz import an octave high). Dashboard sends and the Yamaha editor are different protocols and unchanged.
- **After a replace the sample is shown at once** (`_show_sent_sample`): the cache entry is built from what was written and selected explicitly. Relying on the header fetch left the waveform up with the controls off (that fetch queues behind every write; the owner's log shows no `sample_detail` request at all, root cause not found).
- **Open report (not reproduced):** "Trim to Markers after an external edit doesn't trim". The log shows Load Edited Audio (30240 frames, markers 0/30239/30239/30239) then Send to Sampler of the same 30240 frames with SMPEND 15151: markers moved, no trim applied. Trim on a staged sample works in tests; the `applied to` log lines will show whether Trim ran.
- Math is in `core/sample_editing.py` (pure; samples, start, loop_start, loop_end, end in and out). **Fade** fades the lead-in/out AROUND `[start, end]` (ramps reach gain 1 at the markers), not a trapezoid inside: re-read `fade_in_out_samples`'s docstring before "fixing" it. **Normalise** is whole-buffer (like Reverse), deliberately.

**The Yamaha working copy** works the same way (`YamahaSamplesTab._staged[name] = _Staged(channels, markers, original_*, history)`), adapted to what the A4000 can do:
- The unit can't overwrite a sample, so the only way out is **Send as New Sample** (`send_working_copy_as_new` -> `_send_copy`, `<name> EDIT`). There is no "Send to Sampler" and no backup (the original is never touched).
- `_show` makes the working copy's audio the tab's loaded audio (one sample at a time, so a staged sample re-asserts it when selected). `_overlay(name, payload)` returns a COPY of the unit's payload with the working copy's `wave_length`, addresses and loop length. `tab._cache[name]` stays what the unit sent (`ye.params_for_copy` carries the ORIGINAL's key, tuning and loop mode onto the copy).
- Marker edits stay local while staged (`_on_marker_committed`); other panel fields write live. Undo/Revert as on the Akai side. A dialog's own "Send as New Sample..." drops the working copy on success.
- Actions work only while the Samples tab is showing (`set_active`); the menu is disabled while `_is_busy()`. Closing with unsent edits asks (default No); `tests/test_yamaha_program_editor.py` `dispose` clears `_staged` first or teardown would block on that question.

## Edit in External App

"Edit in External App..." writes the loaded audio to a WAV, opens it in the app from Settings > External Audio Editor (`config.json` `external_editor`;
empty = the OS default for .wav) and watches the file. `core/external_edit.py` is pure; `ui/external_edit_tracker.py` is shared by both
editors (exports to `dropped_files.session_dir()/external_edit/<n>/<name>.wav`; a `QFileSystemWatcher` on the directory AND the file, debounced 600 ms;
`is_ready` re-reads mtime/size itself so a missed event never hides a save). The S2000 round trip worked on the owner's unit; the working-copy rework has not been tried. Tests stub the app by patching `external_edit.launch`.
- **"Load Edited Audio" sends nothing**: the file becomes the working copy (Akai: read back mono at the sample's own rate, resampled lo-fi if it differs, stereo averaged; Yamaha: channel count kept, a mono file for a stereo sample goes on both sides). A second Load replaces it (one Undo step). The exported file stays and the button waits for the NEXT save.
- **Markers:** same length = untouched; otherwise `remap_markers` clamps Start/End into the new audio (markers covering the whole sample cover the whole new one) and clamps the loop inside. Tell the user to check the loop if you extend this.
- **Auto-load** (opt-in: `external_auto_load`, off by default). The tracker decides WHEN: `is_stable(key)` = saved over AND the same size/mtime seen again `STABLE_MS` (400 ms) later, so a multi-write save or autosave is never read half-written. Each editor then acts from its own refresh (`maybe_auto_load`): only for the SHOWN sample (a save for another waits until it is selected), never while busy (the refresh at the end of the operation retries), once at a time (`_auto_load_scheduled`), through `QTimer.singleShot(0)` so it never runs inside the refresh that triggered it. Safe because a Load only changes the working copy.
- Windows/Linux launching (`os.startfile`, `xdg-open`) is untested; macOS uses `open [-a app]`.

## Time Stretch / Pitch Shift (NOT tried on hardware)

"Stretch / Pitch..." opens `ui/time_stretch_dialog.py`: Length (LogKnob 25-400 %) or Semitones (+-24) and Cents (+-50) in Pitch Shift mode,
plus Grain (LogKnob 5-500 ms). OK is disabled at 100 % / 0 st 0 ct and above `MAX_OUTPUT_FRAMES` = 16 M (the S3000's RAM). The dialog keeps ONE
size across its modes (fixed-width knob columns, the taller warning reserved); switching modes once widened it by ~30 px. `dialog.edit()`
returns `(kind, options)` for `time_stretch.EDITS`/`transform_for`. The Yamaha editor makes a NEW sample (`<name> STR` / `<name> PITCH`).
- The engine (`core/time_stretch.py`) is a Python port of potenza-time-stretch's `TimeStretch.h` (GPL-3, see `THIRD_PARTY_NOTICES.md`). **It is checked against the real C++**: `tests/fixtures/time_stretch_golden.json` came from compiling the header (`tools/make_time_stretch_golden.py --header ...`; the header is not in the repo; 5 stretch + 4 pitch cases) and `test_time_stretch.py` needs agreement within 1e-5. Don't "tidy" `TimeStretch`'s arithmetic without re-running that.
- **NOT measured: that it equals the sampler's own time stretch.** Upstream only says "Akai style". The default grain (40 ms) is a guess. (The owner listened and found it "very akai-esque".)
- It is a playback engine driven offline (~0.7 s per 10 s of mono audio, pure Python). **Grains are not phase aligned**, so a sustained tone warbles (a 100-sample sine stretched x1.5 drifts 92-114 samples per cycle) unless the grain step is a whole number of cycles. **Pitch shift on a pure 441 Hz tone is approximate and depends on the grain** (measured 2026-10-13: +3 st at 10 ms -> 440 Hz instead of 524, at 80 ms -> 515; +12 st at 10 ms -> 770 instead of 882, at 80 ms -> 899): short grains pull a tone back toward its original pitch, 60-100 ms tracks best. Drums and loops are the use case.
- Stretch scales the markers in proportion (`stretch_with_markers`); Pitch Shift keeps the length. Both act on the whole buffer. Root note and tune are untouched on purpose (the shifted sample should now play at its new pitch on the same key).

**Previews play as the sampler would:** every PC preview shifts the audio by the sample's root note and tune (`SlicePreviewPlayer.play(..., pitch_shift_semitones=)`), to the fixed preview note C3 (60).
- **Akai:** `_preview_pitch_shift_semitones(entry)` = Tune (`STUNO` minus the `SBANDW` baseline) + root note to C3. Used by click-to-preview, the Filter and Stretch dialogs and the Slice Editor.
- **Yamaha:** `preview_pitch_shift_semitones(data)` = `core.preview_pitch.yamaha_shift_semitones(...)`. **UNVERIFIED on a unit:** the C3 preview note, that Coarse is in semitones and a positive Coarse/Fine raises the pitch, and the Fine step size (the same guess as Detect Pitch). Each is one line in `core/preview_pitch.py`.
- Audio from a FILE gets no shift (no root note or tune). The Stretch dialog's result line says how long the preview lasts when the shift changes it. The Loop Preview pane and the waveform are not shifted (they show the data, not the sound).

## Root Note & Tune, and Detect Root Note

`sample_root_note_spinbox` (`SPITCH`, 21..127) and `sample_tune_spinbox` (`STUNO`, +-50.00 st). **Detect Root Note** (`core/root_note_detection.py`)
is hand-rolled time-domain autocorrelation, monophonic only (no aubio/librosa/numpy).
- **Octave disambiguation anchors on the CURRENT root note**, among credible peaks only (`_CANDIDATE_CONFIDENCE_RATIO` 0.8). Nearest-peak-only made confidence swing 37%-79% on the same audio (`test_confidence_does_not_swing_low_based_on_anchor_alone`).
- The analysis window (`analysis_window`, shared with the A4000's Detect Pitch) is the loop region if long enough, else an attack-skipped chunk; both are capped short because analysis time scales with length. Confidence threshold 0.5 (needs calibrating on monophonic material).
- **The result is ADDED to Tune, not written over it**: `compute_bandwidth_and_tuning` bakes engine-speed compensation into `STUNO` for non-native rates. **The baseline comes from the sample's own `SBANDW`, never re-derived from its rate** (the sampler derives its own for non-16-bit sends; re-deriving played such samples an octave low).

## Slice Editor

A modal `SliceEditorWindow` over already-loaded audio: place markers (Equal Slices, or the Sensitivity slider), export each slice as a new
one-shot sample, optionally build a program (one keygroup per slice, Const Pitch, one-shot). `SliceWaveformView` is NOT `WaveformView` (two
edges + an interior list that CLAMPS, vs four markers that PUSH). Only Export needs real hardware.
- **Export:** every slice `SPTYPE=3`; `SPITCH`/`STUNO`/`SHLTO` copied (see re-sent pitch above); one `send_file_queue` call. With "create program": max 92 slices (keys from 36 up, `LONOTE`/`HINOTE` cap 127). Names zero-pad to a width computed once per batch. The Samples tab's second connection can lose a reply to `SamplerController` chatter, so `_reload_sample_list_with_retries` retries 3x.
- **Click-to-preview** (`core/audio_preview.py`, `miniaudio`): a single click schedules a debounced preview (half the double-click interval, so a marker-adding double-click doesn't blip); a marker click uses the TARGET's frame, not the cursor pixel; a drag cancels. **A running preview device at interpreter exit crashes macOS** (SIGTRAP on CoreAudio's thread): `audio_preview` stops all players via `atexit`, and its tests have an autouse fixture doing the same and poll instead of sleeping.
- **Stereo:** `SliceWaveformView.set_waveform(..., right=)` draws a second channel in its own lane; `SliceEditorWindow(extra_channels=[right])` turns it on and ANALYSES the mono MIX (`_analysis`) for the detector, click-to-preview and zero-crossing snaps, so a hit loud on one side still counts. The send path builds `-L`/`-R` names by cutting the BASE to 10 chars, so the Dashboard passes `channel_suffixes`; without it 11-12 char names (`120STEREO-01`, `-02`) collapsed to one `120STEREO--L`/`--R` pair.
- **Sensitivity slider** (`core/transient_detection.py`, hand-rolled energy flux): 0% is OFF; dragging regenerates ALL markers every tick with no confirmation, silently replacing hand-placed ones. `FineSlider` is deliberately not a `Knob`.
- **SuperFlux** (`core/superflux.py`, a "Detection" combo, remembered as `slice_detection`; greyed out if VelociLoops didn't load; an analysis error falls back to Simple with one message). VelociLoops only exposes detection as `vl_create_from_superflux()`, which builds a whole RX2, so **every slider value redoes the analysis** (measured 2026-10-12: ~36 ms per second of audio; 0.09 s for 3 s, 2.8 s for 77 s). The dialog debounces (120 ms), analyses on ONE worker thread (`ThreadPoolExecutor` polled by a QTimer: deliberately no cross-thread Qt signals or `QThread`, so closing mid-analysis can't touch a deleted object) and applies only the newest result. `threshold` is logarithmic, 4.0 (least) to 0.05 (most); ~30% is VelociLoops' default 1.1. Measured against ReCycle's cut points on 7 drum loops: 60/60 found with 0 extra markers up to ~50%, then more each step (3 extra at 60%, 35 at 100%); Simple found 20-70% at its best. Real-time dragging on long samples would need VelociLoops to expose its activation curve separately from peak picking.
- Layout: the zoom scrollbar reserves its height even when hidden; the waveform is `Expanding` with a 220 px floor plus `setStretchFactor(waveform, 1)`; a `QWidget` wrapping `export_row` needs zero margins. The standalone launcher's `_StandaloneSamplerController` stub needs `cancel_transfer()` as well as `is_transfer_busy()`.

**File > Import Audio** (`ui/import_audio.py`; Program Editor and Yamaha editor, Ctrl+Shift+I) opens any audio file (WAV/AIFF/FLAC/MP3/Ogg/Opus) or `.rx2` in the
Slice Editor: an `.rx2` with its cut points marked, a plain file with NONE (exporting with no markers sends the whole selection as one sample, so it doubles as a trimmer). Stereo stays stereo as for `.rx2`.
`pick_and_load` is shared; `_run_slice_editor` is shared with the resident-sample flow (`resident_source=False` skips audio retention); the Yamaha path is `YamahaSampleEditor.import_audio` -> `_run_slicer` (root key 60, the unit's defaults).
- **Length guard:** the Slice Editor holds the file as Python lists. Measured 2026-10-12 on a 10 min stereo MP3: decode ~1.2 s, peak RSS ~2.2 GB (~3 GB with the editor's mixes), about 3.7 MB per second of stereo. A file over `WARN_SECONDS` (60 s, the owner's choice) asks first (default No) and over `REFUSE_SECONDS` (15 min) is not opened. Checked with `sf.info` BEFORE decoding; `.rx2` skips it. More than 2 channels is refused by `read_wav_channels`. Decoding runs on the GUI thread with a wait cursor.

## LFO2 and the modulation matrix

`LFO2` is independent but hardwired to Pan (`PANRAT`/`PANDEP`/`PANDEL`/`LFO2WAVE`, in `program.pan`); `LFO2WAVE` has 3 shapes: don't assume a 4th.
`lfo1_sync_combo` labels are inverted from `DESYNC`'s raw meaning but the raw byte is not. The matrix (`MODS*`/`MODV*`) omits Envelope 3 (no ENV3
page). Sources are program-wide (confirmed on hardware); most amounts too, but `MODVFILT1..3`/`MODVPITCH`/`MODVAMP3`/`L_PTCH` are keygroup-region (the
Keygroup tab shows amounts plus read-only source mirrors). Mirror combos must sync on BOTH live edits and program loads (`blockSignals` during
a load skips the live wire).

## Testing

See `TESTING.md`. `tests/conftest.py` points the debug log, config, backups and the `_sleep` hook at temp locations, forces
`QT_QPA_PLATFORM=offscreen` unconditionally (the per-file `setdefault` is a no-op if the desktop exports the variable, which once ran
"offscreen" tests on a real Wayland session), clears `AKAISDS_UNLOCK_GLOBAL`, and resets the collapsed-card cache per test. One audio-preview
test (`test_update_loop_points_with_degenerate_bounds_is_ignored`) is timing-sensitive and has flaked once: re-run it alone before
suspecting a change.
