# Agent notes for AKAISDS

Cold-start context for AI agents. It exists to stop you "fixing" things that look like bugs but are deliberate, and to say what was
MEASURED on real hardware versus guessed. Human docs: `README.md`, `BUILDING.md`, `TESTING.md`, `CONTRIBUTING.md`. Long-form findings
and plans: `dev_docs/`.

## Start here

| Working on | Read |
|---|---|
| anything that talks to a real sampler | "Real-hardware safety", "House rules" |
| the Akai Program Editor (`program_editor_window.py`) | `BridgeWorker`, "Program Editor UI", "Samples tab", "Global tab" |
| S1000 / S900-S950 / Yamaha A4000 | their own sections, then `dev_docs/s950-notes.md` / `yamaha-a4000-notes.md` |
| Ensoniq EPS/ASR/ASR-X | their sections, `dev_docs/ensoniq-notes.md`, `dev_docs/asrx-notes.md` |
| multi files | `dev_docs/multi-save-load-progress.md`, `dev_docs/s3000-multi-file-format.md` |
| sending samples, Slice Editor, sample edits | "Sample send hardening", "Sample edits and the working copy", "Slice Editor" |
| menus, shortcuts | "Menus and shortcuts" |
| the Macintosh System 6 theme | "Macintosh System 6 theme", `dev_docs/system6-theme-roadmap.md` |
| logs, bug reports | "Debug logging" |
| running with no hardware | "Developing without hardware" (`tools/demo.py`) |
| tests | `TESTING.md` |
| releases, Homebrew tap, AUR | `dev_docs/packaging/PACKAGING.md` (workflow: `.github/workflows/publish-packages.yml`) |

## House rules

- **Never commit, branch, tag or push unless asked.** The owner handles git: a commit is where the human checks the code. Agents may `git add` the files they changed, and should suggest a single-sentence commit message. Say when a good point to commit has been reached, but leave the commit to the owner. Ask before anything outward-facing.
- Never edit the pinned `s3k`/`s3ked` dependency. Wrap or override it here.
- Real hardware: read "Real-hardware safety" first. Back up before writing. Close other MIDI software.
- Keep user-facing text short (a line or two). Detail goes in the log.
- Record what was measured versus guessed, dated, with the unit it was measured on.
- Tests: `uv run pytest tests/ -q` (~8 min, ~3600 tests). The traps (offscreen Qt, threads) are in `TESTING.md`.

## What this app is

Launched from `src/main.py`. The `&Window` menu swaps between windows (one shown at a time).

- **Transfer Dashboard** (`ui/dashboard.py`, `ui/main_window.py`, `controller/sampler_controller.py`): sends and receives samples over MIDI SDS or the Akai/Yamaha/S950 dialects.
- **Program Editor** (`ui/program_editor_window.py`, `core/program_editor_bridge.py`): program, keygroup, sample and multi parameters on S1000 and S2000/S3000. Most of this file is about it.
- **S900/S950 editor** (`ui/s950_*`) and **Yamaha A4000/A5000 editor** (`ui/yamaha_*`): smaller separate windows, opened by the same Open Editor button.

**Sampler Types** (`core/sampler_models.py`): `akai_s1000`, `akai_s2000_s3000`, `akai_s900_s950`, `generic`, `yamaha_a4000`, `ensoniq_eps_asr`, `ensoniq_asrx`. The legacy `"akai"`
is accepted and rewritten at startup. The model is a user setting, never detected (S1000/S2000/S3000 answer identity identically).
`SamplerController.device_type` is the PROTOCOL FAMILY (`akai`/`generic`/`s950`/`ensoniq`) every transfer path branches on; the choice is
`sampler_model`. Yamaha is family `generic` plus `sampler_models.is_yamaha()`.

**Platform:** macOS 14+ (Homebrew cask `depends_on macos: :sonoma`). Wheels: Qt needs 13, the NumPy wheel `uv` picks needs 14.0, everything else <= 11. The bundle's PySide6 and `libvelociloops` files carry a `minos 15.0` stamp that macOS does NOT enforce (a tester ran 1.3.0 on 14.8.9). macOS 13 would need NumPy's `11_0` wheel and `-mmacosx-version-min` on VelociLoops; untested, so not claimed. Notes: `dev_docs/macos12-support-handoff.md`. `scripts/build_macos.sh` writes `LSMinimumSystemVersion` 14.0 into the bundle's Info.plist (Nuitka has no option for it) and then re-signs ad hoc: editing the plist alone makes `codesign --verify` say "invalid Info.plist" (checked 2026-10-20, and the re-signed 1.3.0 copy still launched). The same script builds the DMG (`AKAISDS-macos-{arm,intel}.dmg`, the names CI uploads and the cask downloads) with `dmgbuild`: layout `scripts/dmg_settings.py`, background `assets/dmg/dmg_background.tiff` (regenerate with `assets/dmg/create_dmg_background.py`; keep its window size and icon positions in step with the settings file). The background is a mid-tone slate (`#b4bac8`) with a deep green arrow (the knobs' `#3aa88a` taken darker for contrast): **Finder draws the icon names near-black whatever the appearance and a script can't change that**, and a DMG window has only ONE background picture (no light/dark variants), so it has to stay light enough for those names. A dark background made the names unreadable (seen 2026-10-20 in a dark-mode Finder; light tags behind the names worked but looked heavy). Checked by opening built DMGs in Finder in dark mode only; the window chrome looked light with one mid-tone and dark with another, so don't rely on it.

## Third-party deps (`s3k`, `s3ked`)

Pinned git deps in `.venv/lib/python3.12/site-packages/`: `s3k.bridge.S3kBridge` (the SysEx bridge), `s3k.params` (parameter registry,
`p.lookup(name, region)`), `s3ked.demo.DemoBridge` (a fake that reproduces real quirks). **Read `Parameter.notes` before trusting a
declared range**: they are dated hardware measurements, and past bugs came from trusting the manual's prose. Raise problems upstream.

Where our own measurement overrides `s3k.params` (don't "fix" back without re-measuring):
- `K_FREQ` (filter key-tracking): declared `-30..99`, measured `-24..+24`.
- `B_PTCHD` (bend down): declared `0..12`, measured `0..24`. `program_editor_bridge._HARDWARE_RANGE_OVERRIDES` / `_lookup_for_write` patch a corrected `Parameter` in front of every write.
- `LFO2TRIG`: declared `0..255`, measured a plain boolean.

## Developing without hardware

- **`uv run python tools/demo.py <sampler>`** (`list` shows them; `--instant`, `--editor`, `--smoke` where they apply, and it says so when one doesn't) is the one launcher: `s1000` / `s3000` (the Akai Program Editor alone, via `program_editor_bridge.connect()` with the env var below), `s950`, `a4000`, `ensoniq`, `asrx` (the real Dashboard on a fake; they delegate to `tools/*_demo.py`, which still run on their own). Add a new demo sampler to its `SAMPLERS` table. `--smoke` is a headless self-check (`QT_QPA_PLATFORM=offscreen`).
- `AKAISDS_DEMO_SAMPLER=1` makes `program_editor_bridge.connect()` return a `DemoBridge` (`core/demo_s1000.FakeS1000` for the S1000 model) and puts the editor in demo mode (no sends). Audio comes from `tests/test_audio.wav`, paced like a real receive. `AKAISDS_DEMO_SAMPLER=1 uv run python src/main.py` runs the whole app with the editor on the fake (the Dashboard's own Send/Receive still use the real ports).
- **`--instant` / `AKAISDS_DEMO_INSTANT=1`** (`core/demo_timing.py`) removes the MIDI-speed emulation (a Yamaha edit send: 10.0 s paced, 0.0 s instant). `""`, `0`, `false`, `no`, `off` count as OFF.
- The Settings dialog is unusable in the Dashboard demos (it would reopen real ports), and those demos use a bare `QMainWindow` with no menu bar.
- `uv run python tools/make_screenshots.py` regenerates the manual's pictures (`ui/help/screenshots/`) offscreen at 2x (`--list`, `--only NAME` (one name per run), `--overwrite`, `--out DIR`). It never replaces an existing picture without `--overwrite` and stubs out MIDI and the update check (a real rtmidi client can abort the process: OSStatus -304). Add a picture with a `@shot` recipe in `tools/screenshot_recipes.py`; `tests/test_make_screenshots.py` checks every picture `quickstart.md` mentions has one. Run it with `uv run`, not bare `python3`.
- `uv run python tools/make_diagrams.py` redraws the MIDI cabling diagrams (`ui/help/diagrams/`, needs `rsvg-convert`); not in `screenshots/` on purpose.
- The fakes fake the MIDI PORTS, so the real bridge and session code runs on top. `FakeS1000` ignores 0x20+ ops like the real machine and pads blocks with `0xA5` so dropped trailing bytes show up.

## Real-hardware safety

**Incident, 2026-10-10 (the owner's S2000).** With Ableton open on the same MIDI interface the sampler had its own replies echoed back.
A sampler executes a returned data frame (MISCDATA/MDATA/xHEADER) as a WRITE and acks it with `REPLY OK`. Symptoms: a stray OK beside
every reply, replies running behind, "expected command 0x05, got 0x16". A dump that read misc bytes 6-9 then (the LOAD/DELETE/SAVE
triggers; they read 0, and 0 is itself a command) probably made it load, delete and save: it froze and could no longer boot from its hard
disk (restored from image backups). That is inference from logs. Other suspects were never excluded: whole-block misc writes, a
LOAD-page write, SCSI experiments, a failing drive. Once the drive is healthy, compare a misc dump with
`~/.akaisds/misc_probe/app_tune_plus50.json` (taken before any block write).

Rules from it:
- **Never read byte registers 6-9** (`tools/s2000_misc_probe.NEVER_READ_INDEXES`, tested). Never add a tool that does.
- **Close DAWs and MIDI monitors** before running the app or a tool against a real unit. `tools/midi_echo_check.py` (sends only RSTAT) reports stray frames.
- Request/reply tools keep **one request outstanding**, accept only the reply naming what they asked for, and stop after a few unanswered reads. Never send into silence.
- Don't probe a sampler whose drives are disconnected or not ready: disk registers stall it.
- The SCSI controls are locked "for safety". Nothing here calls select_drive/device/partition/volume, trigger_load/save, clear_memory, or writes registers 0, 2, 4, 6-9.
- Any write path needs a backup first and a read-back that must match.
- **The app detects the condition**: `core/reply_matching.py` makes reads tolerate stray frames, and `BridgeWorker.stray_replies_detected` (3+ skipped strays) shows a dialog. Tolerance is not safety: the echoed frames still reach the sampler.

**Reply matching** (`core/reply_matching.py`) is installed by `connect()` on every real `S3kBridge` (the S1000 adapter sits on top; demo bridges
are untouched). `make_reply_tolerant(bridge)` patches one instance: READ requests skip a stray `REPLY OK` (`REPLY ERROR` still returns) and,
for reads whose reply echoes the item, skip a data frame with a different index/selector/offset. Deletes, SETEX and block writes use the
original method. `bridge.skipped_replies` counts skips. Tests: `tests/test_reply_matching.py` (with a "fails without the layer" control).

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
`midi_transport.py`, `midi_manager.py` port opening or `connect()`, re-run the transport and bridge tests; hardware plan:
`tests/midi_transport_consolidation_test_plan.md`.

## Debug logging

`core/debug_log.py` -> `~/.akaisds/akaisds.log` (8 MB x 3, DEBUG). Ask for it (and the macOS `.crash` report) before guessing at a hardware bug. Unhandled-exception backstops log here, never `print()`. One INFO line per user action is the convention.
- `LoggingBridge` wraps `connect()`'s bridge (every call START/END/FAILED with timing and raw frames). `core/diagnostics.py`: crash handlers (`faulthandler` into `crash.log`) and a `config (...)` line at startup and after Settings (carries `slice_detection=`, `rx2_library=ok|MISSING`). `ui/diagnostics_ui.py` logs every static `QMessageBox` call and the button pressed (log `QMessageBox(...)` instances yourself).
- `SamplerController` logs each distinct `status_changed` text. `SysexTrace` logs every SysEx the Dashboard, S950 and Yamaha editors send or receive (first 16 bytes + length, capped, never the payload).
- **Unexpected SysEx is log-only on the Dashboard** (owner's decision; `_log_unexpected_sysex(..., quiet=True)`): DEBUG, capped, never `status_changed`. A data packet before any header, or an S1000 error REPLY, stays a WARNING.
- Prefixes: `ProgramEditor:`, `S950Editor:`/`S950Transfers`, `YamahaEditor:`/`YamahaSession` (WARNING at the 10th unsolicited SysEx while idle), `SliceEditor:`, `ImportAudio:`, `superflux:`, `rx2:` (a WARNING when VelociLoops fails to load: first thing to check for "can't import / SuperFlux greyed out").
- **`LagProbe:`** (`core/ui_lag_probe.py`): one INFO line after every Program Editor audio load (GUI heartbeat delay; `AKAISDS_PROFILE_LOAD=1` adds where the GUI thread was). Simulated 2026-10-19: ~0.3 ms GUI work per packet, worst heartbeat 35 ms, so per-packet work isn't what lags; **the cause on the owner's Mac was not found**; a worker-thread receive was judged not worth it.
- Rename, duplicate and delete of programs, keygroups and samples log an intent line (`ProgramEditor: rename program 'A' -> 'B'`, ...), and so does the Slice Editor's export (`SliceEditor: export N slice(s) ...` and its result), for every window that opens it. Not logged: the S1000 gating state, Yamaha per-write old -> new values (the write coordinator logs the new one).

## Menus and shortcuts (`ui/standard_menus.py`, `ui/shortcuts_dialog.py`)

Menu bars are kept the same across the Dashboard and the three editors wherever an item applies. Add to the shared helpers, not to one window.
- **Help** (`add_help_menu`, last on every bar): Quick Start Guide..., Keyboard Shortcuts..., Open Log Folder, About, Check for Updates. It sets `window._update_runner`; the window's `closeEvent` must `wait()` on it (the Akai, S950 and Yamaha editors and the Dashboard do). Open Log Folder lives ONLY here.
- **Hardware > Cancel Transfer** (Ctrl+.; `add_cancel_transfer_action`) in the Akai, S950 and Yamaha editors; it's Transfer > Cancel Transfer on the Dashboard. Always enabled: the handler is a no-op when idle.
- **View > Zoom In / Zoom Out / Zoom to Fit** (`build_zoom_actions`: Ctrl+=, Ctrl+-, Ctrl+0) in the three editors, acting on the Samples tab's waveform and enabled only while that tab shows (the S950/Yamaha build the menus BEFORE the tab exists, so the actions take a `get_view` callable). Not on the Dashboard (no waveform).
- Window menu: Ctrl+T / Ctrl+E, and Ctrl+1-4 for the tabs the window has. Top-level titles carry `&` mnemonics.
- **Keyboard Shortcuts dialog** reads the shortcuts straight from the window's menu bar (so it can't drift) and adds hand-written sections for non-menu keys (`audition_section`, `lists_section`, `slice_editor_section`, `preview_dialogs_section`, `dragging_section`), passed per window to `add_help_menu`. A new shortcut that isn't a menu action needs a row there. Dedicated Zoom In/Out media keys are filtered out of the listing.
- Not standardised on purpose: Settings (Dashboard File menu, Akai Hardware menu only; opening it from the S950/Yamaha editors would repeat the Settings-dialog SIGSEGV risk above), Refresh (Dashboard Transfer menu vs editors' Hardware menu), the S950's Write (Ctrl+S).
- Tests: `test_help_menu_is_the_standard_one` in the Akai/S950/Yamaha editor test files, `tests/test_shortcuts_dialog.py`.

## Akai S1000 support (`core/s1000_bridge.py`), written without hardware

- **Why a separate bridge:** `S3kBridge.get_parameter`/`set_parameter` use S3000-only byte-addressable ops (0x27-0x38); an S1000 stays silent. It has only whole-block RPDATA/RKDATA/RSDATA + PDATA/KDATA/SDATA (nibbled), so `S1000Bridge` does read-modify-write of whole blocks. The S3000 header layout is a superset at the same offsets (program 0-71, keygroup 0-148, sample 0-140), so `s3k.params` is reused; anything past that reads as zero and refuses writes.
- **Never assume the block length** (spec: "about 150 bytes"): cache what the device sent (1.5 s, invalidated by list/delete calls and raw frames), patch in place, write back the same length, log raw blocks at DEBUG.
- **UI gating** (`_apply_s1000_gating`, one-way at construction; a model change in Settings closes the editor) hides the modulation matrix, LFO2, portamento, LFO1 shape, bend down, resonance, 4-stage ENV2 (a plain ADSR here), Multi and Global tabs, mute group, velocity start. One bend field (`B_PTCH` 0-12); polyphony 1-16; key range and root 24-127. After hiding widgets in an unshown window, `invalidate()`/`activate()` the layouts before `equalize_card_heights` re-measures.
- **Controller routing is editable**: `s3k.params` declares most as "not used" (`0..0`), so `encode_field` would refuse writes; `_S1000_RANGE_OVERRIDES` patches corrected copies. **Check a new S1000 field's declared range first.**
- **The name rule is destructive:** a PDATA/SDATA whose name matches a DIFFERENT resident item deletes that one first. Duplicate and rename refuse a clash on an S1000 only. `FakeS1000` assumes rewriting an item under its own name is a plain replace; the spec doesn't say: **the first thing a real S1000 must confirm.**
- **DELK** is a plain linked-list unlink (no compaction, no `GROUPS` update; a PDATA with a smaller `GROUPS` is rejected). Standalone Delete Keygroup is DISABLED (`KEYGROUP_DELETE_SUPPORTED = False`) after two failed real tests: don't re-enable or try variants blind. Create-from-slices never deletes. Untested REBUILD alternative: `KEYGROUP_DELETE_BY_REBUILD` (off). Evidence: `dev_docs/s1000-delk-findings.md`.
- Duplicate Program/Keygroup and create-from-slices are enabled so a tester can find out whether they work (one-time warning). Not in either model's UI: `OUTPUT`/`STEREO`, `PLAYLO/HI`, `OSHIFT`, `TEMPER`, `KGTUNO`, per-zone `VZOUT`/`VFREQ`, loops 2-4, `DDATA`/`MDATA`.
- **Observed on a real S1100 (tester, 2026-10-16, app 1.3.0; the owner has none):** program list, sample list and header reads work; after Device ID 0, sample transmission and program editing reportedly work. Untested: `LOOPAT1`/`LLNGTH1`/`STUNO`/`SBANDW` semantics, sample edits through the adapter, RSTAT.

## Program files: Save/Load `.p1`/`.p3`

150-byte header + N x 150-byte keygroups (`.p1`) or 192 + N x 192 (`.p3`), N at header byte 42. `core/akai_program_file.py` is a pure codec; `BridgeWorker.submit_export_program`/`submit_import_program` do the I/O. Samples are NOT saved (zones reference them by name; Load lists the non-resident ones). Blocks are whatever RPDATA/RKDATA return, so every byte round-trips: don't decode them.
- **Pointer bytes** (`FIRSTKG`, `NXTKG`, per-zone `SBADD`, `LVXF/HVXF`, `TPNUM`) are addresses the SAMPLER assigns; a file holds stand-ins. **`_import_program` never sends a pointer the sampler didn't hand us**: after the create PDATA it reads the program back, stops if its `FIRSTKG` equals another program's address, checks each append, then read-compares the new program with the file and every other program with a pre-load snapshot. Any stop sets `program_import_blocked` for the session. No DELK: a failed load can leave a partial program (the message says so).
- Load makes a NEW program; a name matching a resident one is never sent. A `.p1` on an S2000/S3000 is converted after a confirmation (`convert_s1000_to_s3000`, tail bytes measured on a real S2000): keep its wording. Disabled in demo mode. Not yet checked against another tool's file: `tests/akai_program_file_test_plan.md`.
- Delete Keygroup by REBUILD (off): `.p1` backup, drop locally, load as `-TMP`, DELP the original only after checks pass, rename (`tests/test_s1000_keygroup_delete_rebuild.py`).
- **Drag and drop:** IN loads like Programs > Load... from `QTimer.singleShot(0)`, NOT inside the drop (its dialogs would hold the dragging app open); one file at a time; refused while frozen or in demo mode. OUT (`_drag_program_out`) reads the program FROM THE SAMPLER first (blocking, wait cursor, 60 s timeout), writes `<NAME>.p1|.p3` under `dropped_files.session_dir()/program_drag/`, starts a `QDrag`; untested in a real Finder/Explorer drop. Test trap: a `QDropEvent` only POINTS at its `QMimeData`; keep it referenced (`_KEEP_ALIVE`).

## Akai sample files: Save as `.s3` / `.s1` (export only; NOT checked against a real file)

`core/akai_sample_file.py`: the sampler's raw sample header block (192 bytes `.s3`, 150 `.s1`) + SLNGTH signed 16-bit LE words. The header passes through untouched except `SLOCAT` (22) and `SSPAIR` (136), written as zero. Entry points: Dashboard Receive ("Save received as: WAV / Akai sample / both", `config.json` `receive_format`; any failure falls back to the WAV so a receive never ends with nothing), Samples tab **Save as Akai File...** (an UNTOUCHED sample keeps the sampler's header exactly; a working copy or new length gets `with_markers`: SLNGTH, SSTART, SMPEND, LOOPAT1 = loop END, LLNGTH1 32.16 fixed point), and `tools/s2000_sample_check.py` (`show`, `compare INDEX REAL.s3`, `diff A B`; read-only, app closed). `_unique_save_path` checks `.wav`, `.s3` and `.s1`.
- **`.s1` works against `FakeS1000` only**; a header block that isn't 150 bytes falls back to the WAV. Unconfirmed on a real S1000: its SDATA header length, and whether an Akai-mode receive works.
- **Open until the check tool meets a real akaiutil `.s3`**: what a real file has at SLOCAT/SSPAIR, whether it is padded (`parse_file(..., allow_padding=True)`), any wrong header byte. Record the result here and remove "NOT checked".
- **Load Akai File... (NOT tried on hardware)** makes a NEW sample from a `.s1`/`.s3`: strict parse, asks for a name (a clash is refused), runs `_perform_duplicate_sample_real(..., file_fields=plan.writes)`: ordinary 16-bit audio send, THEN the header fields one by one (`akai_sample_file.restore_plan`; loops 2-4 only when in use, never before written from this app). **The file's header block is never sent whole** (the sampler resets the header when audio arrives; an S3000 block isn't an S1000's), which is also how an `.s3` loads on an S1000. STUNO is recomputed for the NEW sample's bandwidth bucket (`_tuning_for_resent_sample`). Fields the sampler can't use stay at its defaults and are reported. The Dashboard's send queue does NOT take `.s1`/`.s3`.

## Multi files: Save / Load Multi (S2000/S3000 only)

`core/akai_multi_file.py` is a pure codec for the 4096-byte multi file. Research and guesses: `dev_docs/s3000-multi-file-format.md`; handover and next steps: `dev_docs/multi-save-load-progress.md`.
- **Save Multi** is one read-only `BridgeWorker` job: the 32-byte header and parts 0-15, one request each, **never above part 15** (16+ silently returns 15). **A part is `RMULTIDATA` selector 1: `get_header_bytes` defaults to 0**, so every part read must pass `selector=1` (`PART_SELECTOR`; the first version forgot and every part came back as the header).
- **Checked against the sampler's own file (2026-10-19, owner's S2000):** byte-identical in every setting of all 16 parts and both headers; only the end link (+0xBE-BF) of ASSIGNED parts differs (sampler: its own link minus 12; us: `FF FF`, which third-party notes say is required). The writer was deliberately NOT changed; whether a sampler loads our variant is untested. A multi loaded from disk takes its name from the FILE NAME.
- **Load Multi** = `plan_multi` (read-only) then `import_multi`: re-plans, saves the resident multi to `~/.akaisds/multi_backups/` FIRST (no backup, no write), writes ONLY `proven`/`measured` fields (`LOAD_EVIDENCE`; `OUTPUT`/`VOSCL`/`PFXCHAN`/`PFXSLEV` are not loaded) one `set_parameter` each (never a whole-block write), puts earlier writes back if one fails (a failed put-back sets `multi_import_blocked`), reads back after `MULTI_LOAD_SETTLE_S` (a mismatch also blocks), then binds programs by **MIDI Program Change on the part's channel**, which reaches EVERY part on that channel: `program_assignments` skips a part sharing its final channel with one that should hold a different program, a part on OMNI, a non-resident or ambiguous program; a part is never unassigned. The worker jobs ran clean headless on 2026-10-19; the window buttons weren't clicked on hardware.
- Measured live over SysEx on the owner's S2000 (2026-10-19, panel on each field's own page): `PRIORT`, `PLAYLO`, `PLAYHI`, `TRANSPOSE`, `PTUNOCM`. UNPROVEN (need output board / FX card): `OUTPUT`, `VOSCL`, `PFXCHAN`, `PFXSLEV`.
- Tools (read-only, app closed, no DAW): `tools/s2000_multi_check.py` (`show`, `save`, `compare`, `diff`, `plan FILE` = dry run of a Load). **`tools/s2000_multi_write_check.py` WRITES: never run it without the owner's explicit go.** Tests: `tests/test_load_multi.py`.

## Akai S2000/S3000: measured behaviour (real S2000, 2026-10-07)

- **A new program does NOT land at the end of the list.** The sampler orders programs by `PRGNUM`; a clone carries its template's number and sorts right after it. **Never address the next KDATA/PDATA at `len(list)`**: `BridgeWorker._locate_new_program(names_before)` re-reads the list. `_handle_create_program` also reads the template's keygroups BEFORE the first write (the insert can shift it).
- Proven: creating a program mid-list, a 4-keygroup clone, `.p3` Save/Load (equal apart from pointers), the whole Slice Editor path (`tools/s2000_check.py`, `tools/s2000_slices_check.py`).
- A tool that talks to the bridge while a `ProgramEditorWindow` is open must wait for `window._worker.is_idle()`.

## Global tab (S2000/S3000 only)

The GLOBAL page's settings are misc registers that neither the Akai specs nor `s3k` name, found with `tools/s2000_misc_probe.py` (read-only dumps in `~/.akaisds/misc_probe/`). **`core/global_settings.py` is the registry; its docstring says what each measurement pinned down and what is inferred. Read it before changing a map.**
- **Byte registers** (`GLOBAL_SETTINGS`): external controller 38, output level 15 (raw 12 = 0 dB, 6 dB steps, inferred), play note/channel/velocity 31/57/54, SCSI disk ID 11, sector 14, local ID 12. Written with `S3kBridge._misc_write_verify` (write, then trust the READ: the ack can lie). Confirmed: the external controller.
- **Whole-block data** (`BLOCK_SETTINGS`): program change channel, tune and fine tune live in the 48-byte `RMDATA`(0x10)/`MDATA`(0x11) block, not their registers (64/65/71 are stale). Offsets: 0-2 program change (channel 0-based, Omni flag, enabled flag; Off `[c,0,0]`, Omni `[c,1,1]`), 6 fine tune (cents+50), 7 tune (semitones+9), 8 output level. `_set_block_setting`: fresh read, encode only that setting's bytes, backup once per session (`~/.akaisds/mdata_backups/`), write (REPLY must be OK), re-read and compare; a wrong length is never edited; reads retry.
- **Spacing matters:** the first in-app block writes were acked and never applied (a write 3 ms after a read, verified 2 ms after the ack). `MISC_BLOCK_BEFORE_WRITE_S` 0.3, `MISC_BLOCK_AFTER_WRITE_S` 1.0, `MISC_BLOCK_VERIFY_ATTEMPTS` 3 via `program_editor_bridge._sleep` (conftest makes it a no-op). Theory, unproven. Confirmed: a block write of offset 0 only (channel 1 -> 6) was acked, read back, and the panel followed; Off/Omni not tried.
- **Locked controls** (`ui/global_tab.py` `DISABLED_SETTINGS`; the tooltip is the reason): tune and fine tune ("Not working right now": they write and read back but the sampler never uses them; untried: a power cycle (CAREFUL, RAM samples are lost), byte 49, an unidentified trigger register); SCSI controls and program change channel ("Disabled for safety": the channel works but writes the whole misc block, one of the uncleared suspects). Unlock only once a misc dump on a healthy drive matches `app_tune_plus50.json`. To re-test: `AKAISDS_UNLOCK_GLOBAL=tune_semitones,tune_cents,program_change_channel` (or `all`).
- `GlobalSettingsTab` (Ctrl+4) emits `setting_chosen` only on USER edits (combos `activated`, spinboxes/knobs debounced 250 ms); a control stays disabled until its value was read. The external controller is in TWO places (Modulation card and this tab), mirrored both ways; a failed write re-reads everything. Demo mode says "Not available"; an S1000 hides the tab. Not offered: MIDI sysex channel (would cut the connection), MIDI-via-SCSI. Hardware steps: docstrings of `tools/s2000_mdata_write_check.py`, `s2000_misc_probe.py`, `s2000_full_dump.py`.

## Memory and OS version (Samples tab bar, Global "Memory & OS" card, Dashboard bar)

- `BridgeWorker.submit_memory_status` (coalesced, `bridge.status()`, read-only), requested on every `_on_samples_loaded`. A failed read CLEARS the bars (`set_unknown`).
- **The OS version is `midi_identity.akai_version_string(version_major, version_minor)`, NOT `DeviceStatus.version`** (the raw byte: a real S2000 on OS 2.00 sends major 17 and showed "v17.00"). Panel number = byte - 15 (measured on 1.30 and 2.00). A major below 15 is shown as is. Read minor and major from `status.raw`. Other models unmeasured.
- One widget, `ui/memory_bar.py`, shared with the Dashboard. The Samples tab's and Dashboard's bars are `collapsible=True` (double-click thins to 8 px; ONE setting, `memory_bar_compact`, all bars follow); the Global tab's never shrinks. RSTAT on a real S1000 is untried; the S1000 shows the Samples tab bar only.

## Keygroup mute group and velocity start (S2000/S3000 only)

- **"Mute Group" combo (`KGMUTE`).** Raw 0xFF = OFF, raw 0-31 = a real group, and **raw 0 is a real group, not "off"**: a zero-filled header is an ACTIVE group 0, and two keygroups over the same notes in group 0 mute each other. The panel numbers groups 1-32; the combo shows raw + 1 and maps by item DATA, never by index; an unknown raw value shows as a temporary "Raw N" item. Showing a keygroup never writes.
- **"Vel. start" knob (`VSS1`-`VSS4`, +-9999 sample points)**, written through the debounced `_schedule_write`. Both are hidden on an S1000.
- The Slice Editor and duplicate flows clone a template keygroup, so new keygroups inherit its `KGMUTE`: a template left at 0 makes all slices choke each other.
- Deliberately not done (owner's call): Envelope 3 (needs the expansion board), `PLAYLO/HI`/`TRANSPOSE` per keygroup, voice toggles, `KGTUNO`, per-zone `VFREQ`/`VZOUT`, loops 2-4.

## Akai S900/S950 support (written without hardware)

**Nothing has run against hardware.** Own protocol family `"s950"`: nothing may fall through to the akai/generic branches (`SamplerController` delegates to `controller/s950_transfers.py` and refuses the rest). There is no delete opcode (don't invent one); uploads go only to EMPTY slots; every write is staged, needs a `.syx` backup in `~/.akaisds/s950_backups` first, and is read back and compared; only a sample's replay mode and direction can be written. Never test Generic SDS on an S950 (its ACK collides with the S950's own request-sample-dump). s950tools is MIT (keep the notice in `THIRD_PARTY_NOTICES.md`); `dxzl/akai-s950` has no licence: don't copy it. Full notes (transfers, editor, Samples tab, s950tools' caveats, pitfalls): `dev_docs/s950-notes.md`; protocol and open questions: `dev_docs/s950-support-plan.md`; hardware test plan: `tests/s950_test_plan.md`. **Sample editing is not built**: it could only ever make NEW samples (no delete, no overwrite), so build it only after a tester confirms receive, send and naming; analysis in `dev_docs/s950-sample-editing.md`.

## Yamaha A4000/A5000 editor

The owner has an A4000 only. **Read `dev_docs/yamaha-a4000-notes.md` (wire rules, writes, edits, native loading, UI decisions), `dev_docs/a4000-editor-roadmap.md` (every measured fact, open items, hardware tools) and `dev_docs/a4000-native-load-findings.md` before touching `core/yamaha_*`, `controller/yamaha_session.py`, `yamaha_transfers.py` or `ui/yamaha_*`.** The rules that bite: `YamahaSession` is event-driven on the GUI thread, ONE op at a time, and **never connect `controller.on_sysex_received` to `MidiManager.sysex_received` yourself** (a second connection delivers every message twice); audio uses the native wave dump, not SDS; a bulk dump can't be aborted; **no `.syx` backup, no write**; edits and slices make NEW samples (nothing is overwritten); Bulk Protect ON makes the unit ignore edits; `tests/fixtures/a4000/*.syx` are real captures, never regenerate them.

## Ensoniq EPS/EPS-16+/ASR-10 (written without hardware)

No tester yet. Own protocol family `"ensoniq"`: nothing may fall through to the akai/generic branches (`SamplerController` delegates list, receive, send and cancel to `controller/ensoniq_transfers.py` and refuses the rest). **Never put `ensoniq_sysex.NEVER_SEND` (delete instrument/layer/wavesample, SAVE INSTRUMENT) on the wire; never PUT INSTRUMENT / PUT LAYER** (they carry RAM pointers): a send only creates a NEW instrument. `FakeEnsoniq` is built from the same reading of the spec as the engine, so it can't catch a misreading. Engine, sending sequence, assumptions a real unit must confirm and tester knobs: `dev_docs/ensoniq-notes.md`; research: `dev_docs/ensoniq-support-plan.md`; spec corrections (read before coding): `dev_docs/ensoniq-spec-crosscheck.md`; tester steps: `tests/ensoniq_test_plan.md`.

## Ensoniq ASR-X (written without hardware, from the v2.67 ROM)

**Not covered by the EPS/ASR code**: no MIDI SDS, its own `F0 0F 10 01` dialect (`controller/asrx_transfers.py`, chosen by `sampler_model`; nothing has run on an ASR-X). **Never send commands 05, 07, 10, 11, 12, 13, 15 or command 02 with size 0; uploads never go to device 127; a slot is free only if a range read of it gets SILENCE** (a slot with no name can still hold something). Keep the `AsrxTransfers:` logging when refactoring: it is the debugging channel. Every wire fact and its confidence: `dev_docs/asrx-rom-findings.md` and the header of `core/asrx_sysex.py` (read before changing a byte); behaviour, device-ID fallback and unknowns: `dev_docs/asrx-notes.md`; tester steps: `tests/asrx_test_plan.md`.

## Transfer Dashboard

- **Device ID** (Settings, `config.json` `midi_channel`, `SamplerController.channel`) is the raw SysEx device byte, NOT a MIDI channel, and a sampler silently ignores any other ID. The Program Editor used to ignore it and use s3k's 0 (S1100, 2026-10-16); `connect()` now reads it (`exclusive_channel=`) and changing it in Settings reconnects the editor's bridge. A reply timeout on an Akai with a non-zero ID appends "(it is N; most Akais use 0)". Keep the label/tooltip wording ("not its MIDI channel", "usually 0"). Panel numbering differs by model (S900/S950: Basic MIDI channel minus 1; S2000/S3000: 1-128; S1000/S1100 probably 1-128): `dev_docs/akai-sysex-channel-rom-findings.md`.
- **Open Editor** enables only with both ports selected, family `akai`/`s950` or a Yamaha model, and no transfer in flight.
- A short child in a `QHBoxLayout` next to something much taller gets stretched: fix with `setAlignment(child, AlignVCenter)`, not spacing.
- **Dropped/opened files get a stable local copy** (`core/dropped_files.py`, `~/.akaisds/dropped_files/<pid>/`), because an editor renders a crop to a temp file and deletes it before Send. Orphaned sessions are swept at next launch.
- **Compressed audio** (`.mp3`, `.ogg`, `.oga`, `.opus`) goes through soundfile (libsndfile 1.2.2); AAC/M4A fails; a lossy file has no native bit depth, so `read_wav_native_bit_depth` falls back to 16. **32-bit float WAVs read as silence** through `sf.read(dtype="int16")`; `sds_encoder` reads float and scales by hand.

## ReCycle `.rx2` import (VelociLoops)

`core/rx2.py` reads `.rx2` through VelociLoops (`third_party/velociloops/`, public domain, vendored UNMODIFIED, commit `f77609e`) over `ctypes`. `tools/build_velociloops.py` compiles it into `src/core/native/` (git-ignored); the three `scripts/build_*` bundle it and `test.yml` builds it first. **Windows/Linux builds and bundling are untested.** No library means `Rx2Unavailable`, a short message, and the `.rx2` tests skip. Only `.rx2` is accepted. Real loops from outside VelociLoops' fixtures are untested. **Writing `.rx2` is possible** (`vl_create_new`/`vl_add_slice`/`vl_save`, needs a tempo and PPQ positions) but is not built and its output is unchecked against ReCycle or a DAW.
- **Slices are RAW length** (`VLSliceInfo.sample_length`), not `vl_get_slice_frame_count()` (which adds a synthetic decay tail). Measured: the raw slices of `120Stereo.rx2` tile exactly 2.0 s. Muted slices are kept.
- **Entry points:** the **Dashboard** (`_import_rx2` queues ONE row with cut points in `RX2_MARKERS_ROLE`; **Send cuts it into one sample per slice**, `_expand_rx2_entry`; stereo base names are 10 chars so `-L`/`-R` fits), and **File > Import Audio** in the Program Editor and Yamaha editor. The S950 editor has none.
- **A stereo loop stays stereo:** each slice is TWO mono samples `<name>-L`/`<name>-R`; the Akai program (`_create_program_from_slices(stereo=True)`) puts left in zone 1 and right in zone 2 (`SNAME1/2`, `CP1/2`=1, `LOVEL/HIVEL` 0..127, `VPANO1`=-50 / `VPANO2`=+50). **Unverified on hardware:** that +-50 pans hard left/right, and the two-zone keygroup writes.
- **Updating VelociLoops:** `uv run python tools/update_velociloops.py` (`--ref`, `--dry-run`); run by hand and review the diff; never from a build (the library parses untrusted files).

## Sample send hardening (`SamplerController._send_current_packet`)

Akai SDATA and generic SDS sends share ONE packet loop; every batch uses it.
- **Timeout = re-send, not skip** (`_send_packet_max_retries` = 3) once the sampler has ACKed at least once; a sampler that never ACKed gets 0 retries (open-loop fallback). Timeout is 500 ms until the first ACK, then `max(2000, 4 x slowest ACK)`.
- ACKs carry the packet number; a different one is ignored (`stray_acks`). Once `_no_response_detected` is set, ACK/NAK/WAIT are ignored (`late_acks`). WAIT re-arms to 30 s; expiry aborts (SDS CANCEL, no DELS).
- Replies stopping MID-send: skip the stuck packet, finish THIS sample open loop at 100 ms/packet, mark it unverified, then CANCEL THE REST OF THE QUEUE and say how many weren't sent (aborting instead left a partial, garbled sample).
- After a user cancel of a receive, SDS packets already on the wire are dropped for 3 s (`_RECEIVE_CANCEL_GRACE_S`).
- Open loop is deliberate with no MIDI IN or Generic SDS. In S2000/S3000 mode with an input and a dead cable a send never starts (the pre-send RSLIST gets no reply): correct.
- **Measured on a real S2000 (don't re-derive or "tune" without re-measuring):** closed loop ACKs a packet every ~50 ms; the header ACK takes ~270 ms. The theory that 40 ms open-loop pacing caused silent samples was disproved and reverted. **A new sample lands in the LOWEST FREE slot**: find a sent sample by NAME, never by the slot you asked for.
- Tests: `tests/test_sampler_controller.py` (a fake `QTimer` fires immediately; monkeypatch `singleShot` to capture delays).

## Program Editor UI

- Program/Keygroup tabs are `_build_section_card(title, *rows)` cards in `_build_scroll_area(page)`. Width is the real constraint: re-measure `horizontalScrollBar().maximum() == 0` on both tabs if you widen anything. Cards pin `Preferred, Fixed` vertically; `equalize_card_heights()` pins paired cards.
- **`ui/editor_layout.py` is shared by the S3000 and S950 editors: change a measurement there and BOTH change; re-check both.** `CARD_SPACING` 6; card page margins `(0, 0, 8, 0)`; spacing inside a card 10. The windows stay separate classes on purpose (live vs staged writes).
- `BridgeWorker.busy_changed` drives the Refresh bar through two debounce timers; don't remove either without reading `test_a_new_busy_true_during_the_hide_grace_period_cancels_the_hide`.
- **`QListWidgetItem` + `setItemWidget` double-paints if the item has text**: build items with NO text; a row widget's `sizeHint()` is used as-is (give it margins). Visibility tests use `.isHidden()`. A QWidget SUBCLASS paints its QSS card frame only with `WA_StyledBackground`.
- **A control that disables ITSELF when clicked must give up focus first** (a disabled widget hands focus on and a `QScrollArea` scrolls to reveal it): use `_set_sample_control_enabled`, `YamahaSamplesTab.update_edit_buttons` or `qt_helpers.set_enabled_without_scrolling` (`tests/test_button_focus_scroll.py`).
- **A fresh `Knob.setValue(0)` emits no `valueChanged`**: fill a readout explicitly when loading and after any `blockSignals`.

**Collapsible cards** (`ui/section_card.py`): every editor card has a painted arrow; the Settings dialog and Dashboard keep plain cards. `qt_helpers.build_editor_card(scope, title, ..., collapsible=True, key=None)`; scopes `program_editor`, `s950_editor`, `yamaha_program`, `yamaha_sample`. **A pair pinned by `equalize_card_heights` is a GROUP** (collapses together, re-pinned to the tallest). State is per `"<scope>/<title>"` in `config.json` `collapsed_cards`; **a title must be unique within its scope** (keygroup "Modulation" has `key="Keygroup Modulation"`; `tests/test_section_cards.py` checks every editor). Not collapsible: the waveform cards and the Zone card. **Edit Sample holds the unsent-edits strip**: amber dot while collapsed, and it OPENS when a working copy first APPEARS. Traps: collapsing clears focus out of the body first; `refresh_layouts()` also invalidates the BODY's layout (a stale hint once pinned a card 426 px tall).

**Envelope graphs** (`ui/envelope_graph.py`; draggable, NOT tried on hardware): each stage has a FIXED width budget scaled only by its own value; never re-normalize one stage against the others. **A graph NEVER writes**: it emits `values_changed` live, `edit_committed` once on RELEASE, `edit_cancelled()`; each graph supplies pure inverse maths (tested to round-trip). **Owner's rule: knobs and graph move live, MIDI goes out only on release.** Inert until `set_editable(True)`. Akai: `values_changed` sets the knob with signals BLOCKED, `edit_committed` calls `_schedule_write` + `_flush_write`. Yamaha: `FieldPanel.set_value_quietly` while dragging, `commit` on release, then the ordinary `WriteCoordinator` (the one-time warning appears at the RELEASE); the AEG graph draws TIMES 0-99 while the unit has RATES 0-127 (`yamaha_adsr_rates` inverts). S950: just sets the knobs of the staged copy. Interaction: handles keep the grab offset; Shift is `_FINE_DIVISOR` slower; Esc cancels; double-click resets to `set_defaults` and commits; `set_values` is IGNORED during a drag; **no tooltip while dragging** (owner's call). **Overlapping handles** (instant attack and decay; Yamaha factory EGs are all rate 127): every handle within 2.5 px of the nearest is a candidate and the first 3 px of movement decides.

## Mouse wheel and controls (`ui/wheel_guard.py`)

One application-level filter (`wheel_guard.install(app)` in `main.py` and the demo launchers; tests install it only in `test_wheel_guard.py`): every spin box, combo, slider and knob answers the wheel only after a CLICK ("armed": accent outline), until a click lands elsewhere; the cursor entering/leaving does NOT disarm (tried first: a combo then needed a second click). **An unarmed control's wheel event is passed up the parent chain by hand** (`_pass_to_parent`): returning True from an application filter does NOT make Qt offer it to the parent (measured, Qt 6.11). Scroll bars and the waveform/loop-preview views are not guarded. Not tried on a real wheel or trackpad.

## Spin boxes: drag and click-to-type (`ui/spinbox_drag.py`; NOT yet felt by hand)

One application-level filter (`spinbox_drag.install(app)` beside `wheel_guard`; remove the lines to undo): a click selects all (type, Enter applies), a vertical drag changes the value (Shift finer). **It never consumes a press** (the click must still focus and arm the wheel guard), only a DRAG's moves and release. "Editing" = the box this filter last selected, focused, with nothing selected; NOT simply "has focus" (a window's first box starts focused and must still drag). Tunables at the top of the module. Unknown: trackpad feel.

## Theme preference (Settings > Appearance)

`config.json` `"theme"`: `system` (default, live OS), `light`, `dark` (`ui/theme.py`; saved and applied when an option is PICKED, so Cancel doesn't undo it). **A pinned theme follows the preference, never `colorScheme()`** (Qt's override is best-effort; offscreen ignores it). Colours baked in at construction can't follow a live switch: use a QSS rule or `theme.notifier.changed` (chevron SVGs are named per colour: Qt caches stylesheet images by path). **Tests that switch theme must not restyle the real `QApplication`** (the suite went from 20 s to minutes): pass a stand-in to `apply_to_app`.

## Macintosh System 6 theme (Settings > Appearance; started 2026-10-29, offscreen only, NOT seen on a real screen)

Plan, lessons from the two sibling ports and the checklist: `dev_docs/system6-theme-roadmap.md` (read it before touching the theme). Third pinned theme `"system6"` (`app_config.THEME_SYSTEM6`, `theme.SYSTEM6_PALETTE`), picked from the preference, never the OS scheme. **The shared `style.qss.template` is untouched**: `ui/style_system6.qss.template` is appended for this palette only (`extra_template`), and `square_corners` rewrites every `border-radius` in the shared text at render time. Dark and light were compared pixel for pixel against the previous commit on all 22 screenshot recipes (`tools/make_screenshots.py --scheme dark|light --out DIR`, a `git worktree` at HEAD for the old side): only the version string differed. **Re-do that comparison after changing any painter, dialog or shared QSS.**
- Palette keys only System 6 has are read with `.get()` by painters (`knob_track`, `knob_arc`, `graph_line`, `key_down_white/black`, `hard_edges` via `theme.antialiasing()`), so the other palettes are untouched. A missing key the template needs still raises at start-up.
- Font ChiKareGo2 (CC BY, credited in `THIRD_PARTY_NOTICES.md` and `ui/fonts/README.txt`), icons `ui/icons/s6_*.svg`; all three build scripts ship the extra template and `ui/fonts` (there is no `pysidedeploy.spec` in the repo: the scripts write it). **A missing data file only shows in a built app; no build was made.**
- **`ui/theme_fit.py`** is one application-level filter, installed by `theme.apply_to_app`, doing three things only when the palette asks: (1) a label/button/combo/spin box pinned to a width below its size hint grows to it (`fit_fixed_widths`; the editors have dozens of `setFixedWidth` that fit the 13 px font, "Velocity Low" clipped to "Velocity Lou"); (2) a window whose scroll area needs horizontal scrolling is widened by what is missing, never past the screen, and its MINIMUM width is raised to match, re-checked after every layout change of the scroll area or its page (`grow_window_for_scroll`; the windows work their minimums out at construction, before the font is applied, e.g. `MidiSettingsDialog` from its pages' size hints; **offscreen's 800 px screen limits how far this can be seen, so it is mostly unit tested**); (3) the labels in a selected row of a list built with `setItemWidget` turn white (`invert_selected_rows`: property `rowSelected`, rule in the extra template). Dark and light are not touched by any of it.
- Push buttons carry 18 px of side padding in this theme (the dashboard's `Settings`/`Open Editor` widths are worked out before the font is applied, so `theme_fit` fits them after). Every tab has a 1 px bottom border (the shared rule drops it). Collapsible section cards (`ui/section_card.py`, palette key `card_titlebar`) draw JV Studio's striped title bar with a close-box arrow, ported as it was; plain cards (Settings dialog, Dashboard, the Zone card) stay plain, as there.
- The waveform view's loop region is akaiutil-GUI's: a light wash (`loop_color` at `loop_shade_alpha` = 38) behind the trace, dashed black loop edges, the trace itself stays black (`WaveformView._draw_loop_wash`; the other themes colour the trace teal instead). The Loop Preview card's zoom buttons (`LoopPreviewZoomControls`, Akai and Yamaha) are small title-bar boxes in this theme (14 px like the disclosure arrow, `1:1` 30 px wide, property `titleBox`) at the bar's RIGHT end, so the bar is as tall as the other cards'; `SectionCard` swaps the roles of its two header spacers for that, and the other themes keep 18 px buttons beside the title (compared pixel for pixel).
- Monospace text (the ASCII logo, About logo, loopback report) carries the object name `monoText`, which the extra template gives a monospace font: the base font rule would replace it otherwise.
- `QMessageBox` icons come from `theme._Style` (a `QProxyStyle` over Fusion, set once per app) and `ui/mono_icons.py`. Markdown viewers (Quick Start, notices, update notes) call `update_dialog.apply_theme_to_markdown` (code font, underlined links).
- Painted widgets: knobs, envelope graphs and the keyboard use palette keys; the keygroup range bar and swatches use `keygroup_color_1..8`, which are eight greys in this palette (dark and light taking turns). The memory bar is a plain `QProgressBar` (solid black fill; its text is transparent, the tooltip says what is free).
- Known gaps: a SELECTED keygroup row's black swatch vanishes on the black row (the swatch has an inline stylesheet); the Dashboard, Yamaha, S950 and Slice Editor were looked at in offscreen pictures only; wide editors depend on item (2); Windows and Linux unseen.
- Tests: `tests/test_system6_theme.py` (use its `_StandInApp`, never restyle the real `QApplication`).

## Update checker

`check_for_update` uses `ssl.create_default_context(cafile=certifi.where())`: the Nuitka macOS build has no system CA store and failed silently with `CERTIFICATE_VERIFY_FAILED` (S1100 user's log, 2026-10-16); the three `scripts/build_*` add `--include-package-data=certifi`. Verified 2026-10-20: the 1.3.0 arm64 `.app` contains `Contents/MacOS/certifi/cacert.pem` (not checked on a Windows/Linux build). It reads GitHub's 30 most recent `/releases`, ignoring drafts and prereleases (an unpublished tagged build reports the previous release: not a bug); the update is the newest by version, and the notes shown are those of every release newer than the installed one, newest first, under a heading each (one newer release: its notes as written). `UpdateCheckRunner` is a `QThread`: `.wait()` before closing.

## PRGNUM and Program Change (Multi tab)

No SysEx write assigns a program to a multi part: only a raw MIDI Program Change addressed by the program's own `PRGNUM`, not its list position. Fresh programs often share `PRGNUM == 0`; `BridgeWorker.renumber_programs()` runs before the first Program Change and on every list reload (if "assigning picks the wrong program" recurs, check for collisions). The +1 display offset lives in `s3k.params`; `_handle_program_change` subtracts it back. The 16 part combos hold their own copy of program names: renaming needs `_update_multi_program_combo_names()`.

## Multi tab: per-part controls

Row: Part | Program | Level | Pan | Channel | Key range (`PLAYLO`/`PLAYHI`, `NoteSpinBox` 21-127) | Semi (`TRANSPOSE`) | Cents (`PTUNOCM`) | Priority (`PRIORT`, raw 0-3). The column order is the owner's choice; header labels and control widths must stay in step (`test_the_columns_run_...`). Written through the debounced `_schedule_write` (`debounce_key` per part and field; knobs flush on release, spin boxes on Enter: `setKeyboardTracking(False)`); loading values blocks signals (`tests/test_multi_part_controls.py`). **Low can't pass High**: `_on_multi_part_range_changed` pushes the other end along and writes THAT one first. Not offered: output, level scale, effects bus and send.

## On-screen keyboard (NOT tried on hardware)

**View > Audition Keyboard (Cmd/Ctrl+K)** in the Akai, S950 and Yamaha editors (`ui/keyboard.py`, `ui/audition_bar.py`, `core/audition.py`); notes named by `midi_note_to_name`, range 24-108. **It always starts CLOSED** and an open bar OWNS the computer keyboard (app-level filter): it stands aside for Ctrl/Cmd/Alt shortcuts, navigation keys, an open drop-down and any other window in front. **Plain channel messages only** (note on/off, Panic = CC 64/120/123): Akai via `BridgeWorker.submit_midi` (the one queue, so a note never lands inside a SysEx exchange; `"midi"` is in `_SILENT_KINDS` so a key press doesn't light the loading bar); S950/Yamaha straight through `MidiManager.send_channel_message` on the GUI thread. Unavailable (nothing routed, so typing is never swallowed) during busy states and Dashboard transfers; the Akai window also LOCKS it then (a greyed-out open bar still lagged an SDS load). Unknown: which channel the multi parts/A4000 listen on (default 1). Tests: `test_audition_keyboard.py`, `test_audition_input.py`.
- **Input drop-down** (2026-10-28, requested by a Windows user whose interface driver lets one program open it; NOT tried on hardware or a real second device): `AuditionBar` forwards a second MIDI input (a keyboard, or a loopMIDI/IAC port a DAW plays into) through the SAME `midi_out`, so all three editors get it and the Akai one still goes through `BridgeWorker.submit_midi`. **`audition.from_input` is a whitelist** (notes, pitch bend, CC 1/64/120/123, each on the channel it arrived on; SysEx, clock, program change, aftertouch all dropped): never widen it to pass the stream through. The sampler's saved in/out ports are never listed or opened (an echoing port is the 2026-10-10 incident). The port is opened only while the bar is open AND available and closed otherwise (a held device is one a DAW can't have); listing happens only when the drop-down opens, never at construction (a bare rtmidi client can abort a headless run). `SharedMidiInput`'s callback fires on rtmidi's thread: `_InputFeed` hops it to the GUI thread with a signal (checked with a plain thread, 2026-10-28). Choice in `config.json` `audition_midi_input`. Held input notes get their note-offs on close, busy and Panic. **Pitch bend** (added 2026-10-28; `MidiManager.send_channel_message` converts it to mido's signed `pitch` on the non-shared path) is thinned to the newest value per channel every 15 ms (`_BEND_INTERVAL_MS`; a wheel sweep is hundreds of messages a second and each one is a queued `BridgeWorker` job and a log line), and a channel left off centre is put back to centre when the input closes or on Panic, or the sampler would stay bent.

## Note names

`core.midi_notes.midi_note_to_name()` calls note 60 **"C3"** (the S3000XL panel's convention). Don't "correct" it to C4. `ui/note_spinbox.py`'s parser must stay in sync (a round-trip test guards it).

## Samples tab: hardware quirks

- **`LOOPAT1` is the loop's END**, and `LLNGTH1` measures backwards from it (`[LOOPAT1 - LLNGTH1, LOOPAT1]`); `s3k.params` doesn't say so. `loop_start` is always derived.
- **`LLNGTH1` is 32.16 fixed point** (`frames * 65536`). Get it wrong and loops "work" in-app but read ~65536x too short on hardware.
- **`STUNO` (sample tune) is signed 1/256-semitone** despite being declared unsigned: sign-extend on read, wrap on write, range +-50.00 st. The cache holds the RAW value.
- Loading a sample's audio is a real SDS dump (minutes for a big one); the UI freezes on purpose. `_wait_for_any_signal` is the ONE place that blocks on a signal; its `start` callable runs only AFTER listeners connect. Audio comes from the Dashboard's `sampler_controller` (a second connection); header fields come through `BridgeWorker`.
- **Loop markers:** drag and spinbox both go through `_schedule_marker_write`/`_flush_marker_write`; markers PUSH each other; only changed fields are written. Loop editing needs only the HEADER (gate on `_frame_count > 0`; reuse cached markers if audio arrives later, `_markers_from_header()`).
- **Overlapping markers are split by cursor HEIGHT** (`WaveformView._markers_within_hit_radius(x, y)`): the upper half of the view grabs a boundary marker (start/end), the lower half a loop marker; a lone marker is grabbed anywhere on its line; click-to-cycle separates two of the SAME kind. A stacked Yamaha stereo pair is ONE display (`_upper_half`). One `WaveformView` serves all three editors; `SliceWaveformView` has no loop markers. Not felt by hand yet.
- **Loop type gating** (`SPTYPE` No looping/One-shot): loop markers disappear (`set_loop_enabled(False)`); dragging Start/End then freezes them, which can break `start <= loop_start <= loop_end <= end`. Code transforming audio must read `markers_with_loop_in_range()`, not `.markers()`.
- **Zoom:** `_max_zoom()` stops at `_MIN_VISIBLE_FRAMES` (32) on screen (a drag is still sample-accurate); the Slice Editor shares it via `waveform_view.max_zoom_for`. Pinch is `NativeGesture`; `angle.x() != 0` always means pan; an unmodified vertical scroll does nothing on purpose.
- **Loop Preview zoom** (`ui/loop_preview_view.py`, shared with Yamaha): `LoopJoinPreview` owns the zoom (`ZOOM_LADDER` 19..2400 frames each side, default 150, session-wide) and emits `zoom_changed`; the owner re-requests `half_window()` frames. Ctrl+scroll and pinch zoom too.

## Sample edits and the working copy (Program Editor; NOT tried on hardware)

Every action is a button AND a `QAction` (`_make_sample_edit_control(key, text, tooltip, slot)`, `_sample_edit_controls[key] = (button, action)`). **A new action = a `_make_sample_edit_control` call + a key in `_SAMPLE_MENU_GROUPS`/`_EDIT_SAMPLE_ROWS` + a line in `_set_sample_edit_buttons_enabled`.** Enable and disable ONLY through `_set_sample_control_enabled`. The Sample menu and the list's right-click menu share those QActions; menu actions are enabled only on the Samples tab and not while `_hardware_busy_ui` or a transfer runs (`_sync_window_menu_busy_state`'s 150 ms timer must not re-enable it mid-operation). Don't name a menu action Settings/About/Quit/Preferences (macOS moves those). No shortcuts for edits: Ctrl+1-4, Ctrl+T/E and Ctrl+Shift+I are taken.

**The working copy.** A sample can hold edited audio that is NOT on the sampler: `_staged_edits[name] = _StagedEdit(samples, markers, original_*, history)`. Trim, Reverse, Fade, Normalise, Filter, Stretch / Pitch, Reverb and Load Edited Audio change it through `_apply_sample_edit` -> `_stage_working_copy` and send NOTHING. Only **Send to Sampler** and **Send as New Sample** write to the unit. (The `_confirm_*` names are historical: they don't confirm.)
- No confirmation on an edit (Undo and Revert are cheap). Confirmations remain before something reaches the sampler (Send to Sampler, default Yes) or discards work (Revert, closing with unsent edits, default No).
- **Undo:** `staged.history` keeps `_UNDO_LIMIT` = 10 previous states (~88 KB per second of mono 44.1 kHz each). With none left, Undo goes back to the sampler's version. Marker/loop drags are not history items.
- **The cache entry IS the working copy while one exists** (`_apply_staged_to_entry`), so every action works on what is shown. The cache is wiped on every sample-list reload, so `_on_sample_detail_loaded` re-applies it over the freshly read header (which still supplies tune, root and loop type).
- **Marker/loop edits stay LOCAL while staged** (`staged.markers`): writing them would put values for a different buffer on the live sample. Root note, tune, loop type and hold are header fields and stay live either way.
- **`_sampler_audio(name, entry)` is what Duplicate and the Slice Editor retain** for the ORIGINAL name; retaining the working copy would hand it back as "the sampler's audio" after reloads.
- **Lifetime:** survives selecting other samples and reloads; follows a rename; a user delete drops it; a sample that vanishes from the list drops it with a status message (`_prune_staged_edits`), **except while `_hardware_busy_ui`** (a replace deletes the original and renames its copy back, so the name is briefly missing). Not kept across restarts.
- **The strip** (`_staged_banner`, `QFrame#workingCopyStrip`) is ALWAYS present so the card never changes height; styled by the dynamic property `unsent` (`_refresh_staged_controls`, `_repolish`; tests read `property("unsent")`). Send to Sampler is `primary=true`; the amber wash is the `warning_tint` palette key. The external hint is INLINE with an Ignored size policy (it once gave the tab a horizontal scroll bar). The list-row marker is a real widget (`row_widget.unsent_dot`).
- **Filter, Stretch / Pitch and Reverb dialogs** (`applies_to_working_copy=True`): OK applies to the working copy; "Send as New Sample..." sends the result at once (`_send_edit_as_new_sample` -> `_perform_duplicate_sample_real(..., samples=, markers=)`; name = name + " FILT"/" STR"/" PITCH"/" REV"; same no-clash rule as Duplicate; off in demo mode).

**Send to Sampler** (`_perform_sample_edit_real`):
- **Send-then-delete under a temp name:** send the replacement as `<name>-TMP`, confirm it's resident, delete the original, rename back. Never delete first, never send under the original's name (zones resolve samples by NAME).
- **A new sample takes the LOWEST FREE slot (measured)**, so find the original and the `-TMP` copy BY NAME (exactly once each, else delete nothing). Never delete by a remembered index.
- A re-sent sample lands with the sampler's defaults, so 8 header fields are written back (`SPTYPE`/`SPITCH`/`SHLTO`/`STUNO` from the source, `SSTART`/`SMPEND`/`LOOPAT1`/`LLNGTH1` from the new markers). Duplicate does the same.
- **Re-sent samples keep their pitch** (found on the owner's S2000 from the log). `STUNO` bakes in the baseline for the sample's `SBANDW` bucket, and the sent sample lands in the bucket the SEND chose (11025 Hz -> SBANDW 0, baseline -12), while a sampler-imported sample sits on SBANDW 1 (baseline -24). Copying `STUNO` across made one play an **octave low**. `_tuning_for_resent_sample` reads the NEW sample's real SBANDW/SSRATE and writes `(source STUNO - source baseline) + new baseline`; a failed read keeps the source's value and logs a WARNING. Slice export does the same; **audio from a FILE gets no `STUNO` write at all** (writing 0 over the baked baseline played a 11025 Hz import an octave high). Dashboard sends and the Yamaha editor are different protocols and unchanged.
- **After a replace the sample is shown at once** (`_show_sent_sample`): relying on the header fetch left the waveform up with the controls off (root cause not found).
- **Open report (not reproduced):** "Trim to Markers after an external edit doesn't trim": the log shows Load Edited Audio then Send to Sampler of the same 30240 frames with SMPEND 15151, no trim applied. Trim on a staged sample works in tests; the `applied to` log lines show whether Trim ran.
- Math is in `core/sample_editing.py` (pure). **Fade** fades the lead-in/out AROUND `[start, end]` (ramps reach gain 1 at the markers), not a trapezoid inside: re-read `fade_in_out_samples`'s docstring before "fixing" it. **Normalise** is whole-buffer (like Reverse), deliberately.

**The Yamaha working copy** (`YamahaSamplesTab._staged[name] = _Staged(...)`): the unit can't overwrite a sample, so the only way out is **Send as New Sample** (`send_working_copy_as_new` -> `_send_copy`, `<name> EDIT`); no "Send to Sampler", no backup (the original is never touched). `_show` makes the working copy's audio the tab's loaded audio; `_overlay(name, payload)` returns a COPY of the unit's payload with the working copy's length, addresses and loop length (`tab._cache[name]` stays what the unit sent). Marker edits stay local while staged; other panel fields write live. Actions work only while the Samples tab is showing (`set_active`). Closing with unsent edits asks (default No); `tests/test_yamaha_program_editor.py` `dispose` clears `_staged` first or teardown would block on that question.

## Edit in External App

Writes the loaded audio to a WAV, opens it in Settings > External Audio Editor (`external_editor`; empty = OS default) and watches the file (`core/external_edit.py` pure; `ui/external_edit_tracker.py` shared by both editors; exports under `dropped_files.session_dir()/external_edit/`; debounced 600 ms; `is_ready` re-reads mtime/size so a missed event never hides a save). The S2000 round trip worked before the working-copy rework; Windows/Linux launching untested.
- **"Load Edited Audio" sends nothing**: the file becomes the working copy (Akai: mono at the sample's rate, lo-fi resample if it differs, stereo averaged; Yamaha: channel count kept). A second Load replaces it (one Undo step). **Markers:** same length = untouched; otherwise `remap_markers` clamps Start/End into the new audio and the loop inside (tell the user to check the loop).
- **Auto-load** (opt-in `external_auto_load`): the tracker decides WHEN (`is_stable`: saved over AND same size/mtime again `STABLE_MS` 400 ms later); each editor acts from its own refresh (`maybe_auto_load`): only the SHOWN sample, never while busy, once at a time, via `QTimer.singleShot(0)`.

## Time Stretch / Pitch Shift (NOT tried on hardware)

`core/time_stretch.py` is a Python port of potenza-time-stretch's `TimeStretch.h` (GPL-3, `THIRD_PARTY_NOTICES.md`) **checked against the real C++**: `tests/fixtures/time_stretch_golden.json` must agree within 1e-5; don't "tidy" its arithmetic without re-running that (`tools/make_time_stretch_golden.py --header ...`, header not in the repo). NOT measured: that it equals the sampler's own stretch (the owner: "very akai-esque"); default grain 40 ms is a guess. Pure Python (~0.7 s per 10 s mono). **Grains are not phase aligned**: sustained tones warble and pitch shift depends on the grain (2026-10-13: +3 st on a 441 Hz tone gave 440 Hz at 10 ms, 515 at 80 ms): 60-100 ms tracks best; drums and loops are the use case. Output over `MAX_OUTPUT_FRAMES` (16 M, the S3000's RAM) is refused in the dialog. Stretch scales the markers; Pitch Shift keeps the length; root note and tune are untouched on purpose. The Yamaha editor makes a NEW sample (`<name> STR`/`PITCH`).

## Reverb (Akai Program Editor Samples tab only; NOT tried by ear in the app)

`core/reverb.py` is a COPY of `prototype/reverb_model.py` in `~/Development/s2000-custom-os` (commit cac8680; a patched S2000 runs it bit for bit). **Keep `Engine.step` and `process` arithmetic identical to the prototype**: `tests/fixtures/reverb_golden.json` came from it and `test_reverb.py` requires sample-for-sample agreement; when the prototype changes, regenerate the fixture and re-copy; never "tidy" the integer maths. Controls are the prototype's four (0..99) through the float `setup`, NOT the hardware's `setup_device`. Mono, any rate; the tail stops at the first 0x800-sample block under 16 (-66 dBFS); over `MAX_OUTPUT_FRAMES` or mix 0 is refused. **Markers:** Start and loop stay; End moves to the new last frame only if it was on the old one (moving it would make cut material audible). Pure Python, 2.0 us/sample (2026-10-19, M1 Air), so it runs on the GUI thread with a wait cursor. Send as New Sample names ` REV`.

## Dialog previews

**Preview / Stop** (`ui/preview_toggle.py`; Filter, Stretch / Pitch and Reverb dialogs, the first two also in the Yamaha editor): **a knob moved during playback does NOT restart the preview** (owner's call). **Space toggles it** whichever control has focus (`PreviewToggle.claim_space_in(dialog)` filters knobs, combos, check boxes and checkable group boxes; push buttons are not claimed): before this, Space on a focused Filter knob flipped the filter's bypass. **Previews play as the sampler would**: audio is shifted by the sample's root note and tune to the fixed note C3 (60) (`SlicePreviewPlayer.play(..., pitch_shift_semitones=)`; Akai `_preview_pitch_shift_semitones`, Yamaha `core.preview_pitch.yamaha_shift_semitones`, **UNVERIFIED** on a unit: the C3 note, Coarse in semitones, sign, Fine step). A FILE gets no shift; the Loop Preview and waveform are not shifted.

## Root Note & Tune, and Detect Root Note

`SPITCH` (21..127) and `STUNO` (+-50.00 st). **Detect Root Note** (`core/root_note_detection.py`) is hand-rolled monophonic autocorrelation. **Octave disambiguation anchors on the CURRENT root note** among credible peaks only (nearest-peak-only made confidence swing 37%-79% on the same audio); the window (`analysis_window`, shared with the A4000's Detect Pitch) is the loop region if long enough, else an attack-skipped chunk; confidence threshold 0.5 (needs calibrating). **The result is ADDED to Tune** (`compute_bandwidth_and_tuning` bakes engine-speed compensation into `STUNO`), and **the baseline comes from the sample's own `SBANDW`, never re-derived from its rate.**

## Slice Editor

A modal `SliceEditorWindow` over already-loaded audio: place markers (Equal Slices, Sensitivity slider), export each slice as a new one-shot sample, optionally build a program (one keygroup per slice, Const Pitch, one-shot). `SliceWaveformView` is NOT `WaveformView` (two edges + an interior list that CLAMPS, vs four markers that PUSH). Only Export needs hardware.
- **Export:** every slice `SPTYPE=3`; `SPITCH`/`STUNO`/`SHLTO` copied (see re-sent pitch); one `send_file_queue` call. "Create program": max 92 slices (keys from 36 up). Names zero-pad to a width computed once per batch. The Samples tab's second connection can lose a reply to `SamplerController` chatter, so `_reload_sample_list_with_retries` retries 3x.
- **Save WAVs...** (`core/slice_wav_export.py`; built 2026-10-20, not tried by hand; needs no sampler, enabled in demo mode): folder picker (last folder in `config.json` `slice_wav_folder`); several slices go in a subfolder named after the base name, one slice straight into the folder; one Replace? question if files exist; result in the status bar; stereo sources save as true stereo. **Always 16-bit at the source rate** (the Bit depth / Rate combos are sampler formats). Names reuse `slice_export_names`; no sampler clash check; `_dirty` untouched.
- **Click-to-preview** (`core/audio_preview.py`, `miniaudio`): single click = debounced preview (half the double-click interval); a marker click uses the TARGET's frame; a drag cancels. **A running preview device at interpreter exit crashes macOS** (SIGTRAP on CoreAudio's thread): `audio_preview` stops all players via `atexit`; its tests have an autouse fixture doing the same. **A playback generator must never hold the player** (`SlicePreviewPlayer` is a QObject with a QTimer): the audio thread keeps the generator, so a closure over `self` made a cycle the GC could free on that thread, leaving a dangling timer that crashed the GUI thread in `QTimerInfoList::activateTimers` (the full suite did, 2026-10-19). Generators share a plain `_PlaybackState`; `test_a_playing_player_is_not_kept_alive_by_its_own_device` guards it. "Timers cannot be stopped from another thread" in a test run = look for another QObject a native-thread callback keeps alive.
- **Stereo:** `set_waveform(..., right=)` draws a second lane; `SliceEditorWindow(extra_channels=[right])` turns it on and ANALYSES the mono MIX (detector, preview, zero-crossing snaps). `-L`/`-R` names cut the BASE to 10 chars (`channel_suffixes`).
- **Sensitivity slider** (`core/transient_detection.py`, hand-rolled energy flux): 0% is OFF; dragging regenerates ALL markers every tick with no confirmation. `FineSlider` is deliberately not a `Knob`.
- **SuperFlux** (`core/superflux.py`, "Detection" combo, `slice_detection`; greyed out if VelociLoops didn't load; an analysis error falls back to Simple with one message): VelociLoops only exposes detection as `vl_create_from_superflux()`, so **every slider value redoes the analysis** (2026-10-12: ~36 ms per second of audio). The dialog debounces (120 ms), analyses on ONE worker thread (`ThreadPoolExecutor` polled by a QTimer: deliberately no cross-thread Qt signals or `QThread`) and applies only the newest result. `threshold` is logarithmic, 4.0 (least) to 0.05 (most). Measured against ReCycle on 7 drum loops: 60/60 hits found with 0 extras up to ~50%, then more each step; Simple found 20-70%.
- Layout: the zoom scrollbar reserves its height even when hidden; the waveform is `Expanding` (220 px floor) with `setStretchFactor(waveform, 1)`; the export row's height and minimum width are computed from its widgets AFTER they're added (a new button goes in before that block). The `tools/demo.py` host stub needs `cancel_transfer()` too.

**File > Import Audio** (`ui/import_audio.py`; Program Editor and Yamaha editor, Ctrl+Shift+I) opens any audio file or `.rx2` in the Slice Editor: `.rx2` with cut points marked, a plain file with NONE (exporting with no markers sends the whole selection as one sample, so it doubles as a trimmer). Stereo stays stereo. `_run_slice_editor` is shared with the resident-sample flow (`resident_source=False` skips audio retention); Yamaha: `YamahaSampleEditor.import_audio` -> `_run_slicer`. **Length guard** (2026-10-12: a 10 min stereo MP3 decodes in ~1.2 s, peak RSS ~2.2 GB, ~3.7 MB per second of stereo; the editor holds Python lists): over `WARN_SECONDS` (60 s) asks first (default No), over `REFUSE_SECONDS` (15 min) refused; checked with `sf.info` BEFORE decoding (`.rx2` skips it); more than 2 channels refused; decoding is on the GUI thread with a wait cursor.

## LFO2 and the modulation matrix

`LFO2` is independent but hardwired to Pan (`PANRAT`/`PANDEP`/`PANDEL`/`LFO2WAVE`, in `program.pan`); `LFO2WAVE` has 3 shapes: don't assume a 4th. `lfo1_sync_combo` labels are inverted from `DESYNC`'s raw meaning but the raw byte is not. The matrix (`MODS*`/`MODV*`) omits Envelope 3. Sources are program-wide; most amounts too, but `MODVFILT1..3`/`MODVPITCH`/`MODVAMP3`/`L_PTCH` are keygroup-region (the Keygroup tab shows amounts plus read-only source mirrors). Mirror combos must sync on BOTH live edits and program loads.

## Testing

See `TESTING.md`. `tests/conftest.py` points the debug log, config, backups and the `_sleep` hook at temp locations, forces `QT_QPA_PLATFORM=offscreen` unconditionally, clears `AKAISDS_UNLOCK_GLOBAL`, and resets the collapsed-card cache per test. One audio-preview test (`test_update_loop_points_with_degenerate_bounds_is_ignored`) is timing-sensitive and has flaked once: re-run it alone before suspecting a change. The shell is fish: quote globs (`--include='*.py'`).
