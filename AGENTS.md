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

The Settings "Sampler Type" combo has three entries (`core/sampler_models.py`,
alphabetical): "Akai S1000" (`akai_s1000`), "Akai S2000/S3000"
(`akai_s2000_s3000`), "Generic SDS" (`generic`). Before the S1000 existed the
S2000/S3000 was persisted as plain `"akai"`; that legacy value is still accepted
everywhere (`sampler_models.normalize`) and `app_config.ensure_defaults_saved()`
rewrites it in `config.json` at startup so the file names the real hardware
(only that exact legacy string - an unrecognised value from a newer version is
left alone). Note `"akai"` also remains the PROTOCOL FAMILY name below - a
different thing from the saved selection. All three Akai families answer a MIDI identity/status request
identically, so the model can only be a user setting, never detected.
`SamplerController.device_type` is still the PROTOCOL FAMILY (`akai`/`generic`)
every transfer path branches on (an S1000's SDS/RSTAT/list traffic is the same
as an S2000/S3000's); the full choice is `SamplerController.sampler_model`.
Only the Program Editor cares about the S1000/S2000+ split.

**Why a separate bridge**: `S3kBridge.get_parameter`/`set_parameter` use the
S3000-only byte-addressable header ops (0x27-0x38). The S1000 doesn't implement
them and stays silent - a real S1000 user's log showed lists working (base ops)
and every `get_parameter` timing out at 2.0s. The S1000 only has whole-block
RPDATA/RKDATA/RSDATA + PDATA/KDATA/SDATA (0x06-0x0B, nibbled), so
`S1000Bridge` is a wrapper that does read-modify-write of whole blocks while
exposing the same duck-typed surface (`program_editor_bridge.connect(
midi_manager, sampler_model)` picks it). The S3000 header layout is a superset
of the S1000's at the same offsets (program 0-71, keygroup 0-148, sample 0-140,
checked against the S1000 spec), so `s3k.params` is reused as-is for those
fields (`_S1000_BLOCK_SIZES`); anything past them reads as a neutral zero and
refuses writes, so `BridgeWorker`'s fixed field lists work unmodified.

**Never assume the block length** (the spec says "about 150 bytes", no exact
figure): the adapter caches whatever length the device sent, patches in place,
writes back that same length, and logs every raw block (hex) at DEBUG - ask a
real S1000 user for `~/.akaisds/akaisds.log` to learn the real sizes. Blocks are
cached 1.5s (reading ~50 fields = one fetch) and invalidated by every
`program_list`/`sample_list`/`delete_*`.

**UI gating** (`ProgramEditorWindow._apply_s1000_gating`, one-way at construction -
a model change in Settings while the editor is open closes it): hides
Modulation (both tabs), LFO2, Portamento, LFO1 shape, Bend down, Resonance and the
S3000's 4-stage ENV2 grid/graph; bend up limited to 0-12; Multi tab hidden/never
loaded. **ENV2 on an S1000 is a plain ADSR** (`ATTAK2`/`DECAY2`/`SUSTN2`/`RELSE2`,
in the same KDATA block as ENV1's four - the S1000 spec lists both), so the
Envelope 2 card gets an ENV1-style graph + 4 knobs (`_build_s1000_env2_adsr`,
loaded in `_on_detail_loaded`) instead. Gotcha found building that: hiding widgets
in a not-yet-shown window leaves nested layouts' cached size hints stale, so the
swapped card stayed pinned 406px tall (vs 225) until its layout was
`invalidate()`d/`activate()`d before `_equalize_card_heights` re-measured.
**The S1000 has ONE bend field** (`B_PTCH`, 0-12 st; the S3000 splits it into `B_PTCH`
"increase" 0-24 + `B_PTCHD` "decrease" at offset 73, past the S1000's block), so the
combo is relabelled "Bend range" there. Other S1000-narrower ranges applied in
gating: polyphony 1-16 (S3000 1-32), keygroup note range and sample root note 24-127
(S3000 21-127).

**S1000 controller routing is now editable** (the S1000's fixed equivalent of the S3000's
mod matrix - `ProgramEditorWindow._build_s1000_controller_cards`): a program-tab
"Controllers" grid (Loudness/Pan/Pitch/LFO1 depth/rate/delay x Velocity/Key/Pressure/
Modwheel), keygroup-tab "Controllers" (Filter freq, Pitch, Loudness, Env 2 level x
Velocity/Pressure/Envelope 2 - `E_FREQ` is the filter envelope's depth) and "Envelope
response" (V_ATT/V_REL/O_REL/K_DAR for both envelopes) cards, and the hidden LFO2 card is
reused as the **Pan LFO** (`PANRAT`/`PANDEP`/`PANDEL` - same three fields, minus LFO2's
shape/retrigger). **Gotcha that nearly broke all of it**: `s3k.params` declares most
of these fields "Not used - fixed value in the specification", range **0..0**, because
the S3000 spec says so. The S1000 spec lists each as "+/-50". Left alone,
`encode_field` refuses every non-zero write AND `decode_field` (which sign-extends
only when the DECLARED range goes negative) reads a stored -20 back as 236.
`S1000Bridge` therefore applies `_S1000_RANGE_OVERRIDES` (corrected copies via
`s1000_param`, by name+region, to whatever `Parameter` the caller passes - never
editing the dependency) on every read, write and `get_header`. Don't add a new
S1000-only field to the UI without checking its declared range in `s3k.params` first.
The controller grids reuse `_ModMatrixGrid` (zebra rows + column separators painted
behind a `QGridLayout`) like the S3000 mod cards, but with stretch-1 data columns so
they track window width (the S3000 cards' columns hug their content), and a label
column sized per grid from its longest label. They must be built AFTER the stylesheet
fonts exist only in the sense that `fontMetrics()` is read at construction - it is,
since `apply_to_app` runs before any window is made. (Screenshot gotcha: a preview
script that skips `theme.apply_to_app` renders the dark-palette zebra stripes on a
light window - they look black.)
These fields are only READ when the model is an S1000 (`BridgeWorker`'s
`extra_program_fields`/`extra_keygroup_fields`) - an S2000/S3000 never pays the round
trips. `V_LOUD` deliberately has no grid cell (it already has the Velocity knob in
Volume, Pan & Velocity - two controls for one field would need syncing).

**Remaining S1000 gaps (audited against the S1000 spec, 2026-10-03)**: not in either
model's UI - `OUTPUT`/`STEREO`, `PLAYLO`/`PLAYHI`, `OSHIFT`, `TEMPER`, `KXFADE`/
`VXFADE`, `KGTUNO`, per-zone `VZOUT`/`VSS`/`VFREQ`. The Samples tab edits loop 1 only;
the S1000 has 8 loops and `SALOOP` ("first active loop") says which one plays - loop 1
isn't guaranteed to be it. Drum-trigger (`DDATA`) and misc/MIDI-channel (`MDATA`)
blocks exist on an S1000 and have no UI at all.

**The S1000 spec's name rule is destructive, so renames are guarded**: a `PDATA`/`SDATA`
whose name matches a DIFFERENT resident program/sample deletes that one first. Duplicate
Program/Sample already refused a clashing name; renaming (`_confirm_rename_program`, the
program-name field's `_commit_program_name`, `_confirm_rename_sample`) now does too, on an
S1000 only (on an S2000/S3000 a duplicate name is ambiguous, not destructive). The name
field's `editingFinished` also fires on any click-away, so on an S1000 an UNCHANGED name
writes nothing (every S1000 write re-sends the whole block); the baseline is
`_program_name_on_sampler`, because `_on_program_name_typed` overwrites the list item's text
live while typing. `FakeS1000` models the delete-on-clash rule (`deleted_by_name_clash`), and
treats a rewrite under an item's OWN name as a plain replace - an assumption the spec doesn't
state and every editor write depends on: **the first thing a real S1000 must confirm.**

**Duplicate Program/Keygroup and "create program from slices" are ENABLED** (same
`PDATA`/`KDATA` clone flows as the S2000/S3000, `BridgeWorker._handle_create_*`),
specifically so the first S1000 tester can find out whether they work - the spec's
"GROUPS must be correct" wording suggests the existing order (KDATA for the new
keygroup first, THEN PDATA with `GROUPS+1`) is consistent with it (the KDATA
creates the keygroup, so the count already matches by the time the PDATA lands),
but that's reading, not measurement. Clones are sent at the block's real length
(`build_pdata_request`'s "192 bytes" docstring is S3000-specific; it nibbles
whatever it's given). Raw frames passed through `S1000Bridge.send_and_receive`
invalidate the block cache, or the reload after a duplicate shows the old
`GROUPS`. `TransferDashboard.open_program_editor` shows a one-time experimental
warning.

**S1000 memory layout and DELK (measured, 2026-10-05 tester log) - why create-from-slices
never deletes**: programs, keygroups and sample headers are 150-byte blocks in ONE linked
memory area; bytes 1-2 of a program block (`FIRSTKG`) and of a keygroup block (`NXTKG`) are
ABSOLUTE addresses (program 0 at 0 with 10 keygroups ends at 0x672, the 12 sample headers
that follow end at 0xd7a where the next program starts). When a keygroup is appended the
sampler repoints the PREVIOUS keygroup's `NXTKG`, but the last one keeps whatever we sent -
a clone's last keygroup carries the template's stale terminator. A real `DELK` was
acknowledged but (a) left the program's `GROUPS` unchanged and (b) left the chain ending in
that stale pointer (into a sample header); the next "add keygroup" used the stale `GROUPS` as
its index, walked off the chain and linked the new keygroup into ANOTHER program (program 0
grew a keygroup 6 = the new clone, its 7-9 orphaned, "block identifier is 3, expected 2" on
every later read). That is what "Create new program with slices" did with a multi-keygroup
template (clone N, DELK N-1). So: `_handle_create_program(first_keygroup_only=True)` clones
only keygroup 0 on an S1000 and `_create_program_from_slices` refuses to continue if the clone
has more than one - **no DELK anywhere in that flow, don't reintroduce one**. The standalone
Delete Keygroup goes through `BridgeWorker._delete_keygroup_s1000`: snapshot (target program
in full + other programs within `_S1000_VERIFY_READ_BUDGET` reads), DELK, rewrite `GROUPS` via
PDATA if the sampler left it, snapshot again and compare keygroup CONTENT (pointer bytes
ignored, logged as `S1000 delete check:` lines). Any mismatch raises, shows a dialog, and sets
`s1000_keygroup_delete_blocked` (action disabled for the session). It is EXPERIMENTAL -
whether the sampler tolerates the repaired `GROUPS` is unmeasured; if a tester's log shows it
failing, flip `s1000_bridge.KEYGROUP_DELETE_SUPPORTED` to False (the action is then disabled
on an S1000). `FakeS1000` reproduces (a) (`delk_updates_groups=False` default) and refuses a
KDATA past the end of the chain, but does NOT model the address pointers themselves.
Duplicate Program/Keygroup still send the template's stale `NXTKG` terminator (they never
failed in the logs); rewriting it to `FIRSTKG + 150*(n+1)` (the value a pristine program has)
is a held-back hypothesis, not implemented.

**Known/untested, flagged for the first real-S1000 report**: whether re-sending a
program/sample block with its own unchanged name behaves as an in-place replace
(the spec: a duplicate name "should be avoided"; it's read as meaning a name that
collides with a DIFFERENT item); whether `LOOPAT1`/`LLNGTH1`/`STUNO`/`SBANDW` have
the same semantics as the S2000 notes above; the sample edit actions
(Trim/Reverse/...) write 8 header fields back through this adapter; the editor's
SDATA/PDATA/KDATA replies also reach `SamplerController` on the shared port
(logs "unexpected SDATA"/"unrecognised Akai message" - noise, not a bug, unless
a Samples-tab audio receive happens to be mid-flight).

**`core/demo_s1000.py`'s `FakeS1000`** fakes the MIDI PORTS (not the bridge's
Python surface, unlike s3ked's `DemoBridge`): real `S3kBridge` + real adapter run
on top of it, and it ignores 0x20+ ops like the real machine (so a stray S3000
op times out and lands in `ignored_ops`). `AKAISDS_DEMO_SAMPLER=1` with the S1000
model selected uses it. Its blocks are 150 bytes with `0xA5` junk past the spec
fields (tests also try 72..192) specifically so wrongly-read or dropped trailing
bytes are caught. Also: **`PRGNUM`'s +1 display offset applies on the S1000 too**
(unverified there - same `s3k.params` entry).

## Akai S900/S950 support (Stages 1-5 done, written without hardware)

A DIFFERENT protocol from every other Akai entry, not a variant of the S1000
family: device byte `0x40` (not `0x48`), function codes 0-11, every 8-bit value as
TWO MIDI bytes, 10-char plain-ASCII names, XOR checksums, and a sample dump that is
ONE SysEx (header + all blocks + a single `F7`) with 4-byte handshakes
(`F0 7E code F7`) - the standard 6-byte SDS ACK is silently ignored. Plan and
staging: `~/Desktop/AKAISDS-S900-S950-support-plan.md` (not in this repo).
**Nothing here has been run against hardware.** Ported from
[s950tools](https://github.com/diemonster/s950tools) (MIT - see
`THIRD_PARTY_NOTICES.md`; keep the notice when porting more). Its comments say which
behaviours were hardware-verified; `dxzl/akai-s950` has NO licence, so don't copy from it.

- `core/s950_sysex.py` - codecs (DB/DW/DD/TB/SW), framing, catalog, SPRM, sample dump,
  16->12-bit conversion. `core/s950_program.py` - PRGM (76-byte header + 1-31 140-byte
  keygroups). Messages are the bytes BETWEEN F0 and F7, like `core/akai_sysex.py`.
  Only the program HEADER offsets are pinned by real captures (in `tests/test_s950_program.py`);
  SPRM and keygroup offsets are as good as s950tools' copy. Unmodelled bytes are kept in `raw`
  and written back untouched; `raw` is excluded from dataclass equality on purpose.
  There is NO delete opcode - don't invent one.
- `core/demo_s950.py` - `FakeS950`, a fake on rtmidi-style ports (like `FakeS1000`). Its
  docstring separates what comes from s950tools' hardware notes from what is GUESSED
  (catalog order, default name of an uploaded sample, ACK count per dump, NAK behaviour...).
- Sampler Type "Akai S900/S950" (`akai_s900_s950`) has its OWN protocol family, `"s950"`
  (`sampler_models.FAMILY_S950`) - not "akai", not "generic". **Nothing may fall through to
  the akai/generic branches for it** (they'd put S1000-family or standard-SDS bytes on the
  wire). `SamplerController` delegates the S950 entry points (refresh, send_file_queue,
  receive_samples, rename, sample info, cancel, incoming SysEx) to
  `controller/s950_transfers.py`'s `S950Transfers` (built lazily, reports through the
  controller's own signals) and REFUSES the rest via `_s950_not_supported` (delete - there's
  no opcode - and the legacy per-file / by-number senders). Add a new S950 path by delegating,
  never by letting it reach the Akai code.
- `S950Transfers` rules (from s950tools' hardware-verified comments; none run on a real unit by
  this project): ONE operation at a time; an RCAT reply is the "device ready" barrier before every
  upload (a stale ACK from the last dump satisfies "any reply" and green-lit a second dump
  mid-bookkeeping); uploads are open loop - one SysEx, wait out the wire time (3125 B/s + 10%),
  listen for NAKs, and ALWAYS go to an EMPTY slot (overwriting triggers a NAK storm; the boot
  "TONE" placeholder counts as empty), then the sample is named by SPRM read-modify-write; a
  receive streams a 4-byte ACK every 50 ms from just after the RSD (the unit stalls between
  blocks and `sysex_received` only surfaces whole messages) and ignores a header-only message
  the unit sometimes flushes first. Names are uppercased, 10 chars, made unique (zones find
  samples by name). The Transmission Settings' bit depth is ignored (always 12-bit); stereo is
  averaged to mono (left only with "mono"); the rate is clamped to the dump header's 2-65.5 kHz,
  not what the hardware plays (S950 7.5-48 kHz, S900 40 kHz - unconfirmed which a given unit
  honours). Received rate prefers SPRM's exact Hz over the header's whole-nanosecond period
  (44100 would come back 44099). Root note/detune are shown as unavailable: SNOMP's direction
  is only inferred.
- The Dashboard needs a MIDI INPUT for everything S950 (the unit answers on it - no open-loop
  send), has no memory bar (no RSTAT), shows sparse slot numbers via `sample_slots_updated`
  (list index != sample number there, unlike `sample_list_updated`), can't delete, and shows a
  one-time experimental warning before the first Send. Program editing is Stage 5, below.
- **Program editor (Stages 4-5)** - `ui/s950_program_editor.py`'s `S950ProgramEditorWindow` (was the
  read-only "viewer" for a while; the old name may linger in old notes), opened by the Dashboard's
  Open Editor button (`open_program_editor` branches to it for the S950; the button needs both ports,
  and `open_program_editor` refuses with no MIDI input). A deliberately separate small window, NOT
  `ProgramEditorWindow` (built on `s3k.params`). It shares the Dashboard's `SamplerController` - same
  connection, no `S3kBridge`/`BridgeWorker`, no second thread.
  - **Layout deliberately mirrors the S1000/S2000/S3000 editor's Programs tab** (consistency was a user
    request): Programs column | Keygroups column (`KeygroupRangeBar` + `keygroupList` rows with colored
    swatches, recolored on `theme.notifier.changed`, item with NO text because it has a row widget) |
    `detail_stack` of a program page and a keygroup page (program click -> page 0, keygroup click ->
    page 1), section cards via `build_section_card` (Range, Filter, Amplitude/Filter Envelope =
    `ADSREnvelopeGraph` over four `Knob`s, a `zoneCard` with Soft/Loud buttons where the S3000 has
    Zone 1-4, Velocity, LFO, Pitch Warp, Output), and a bottom bar with Refresh left / Close right plus
    the write buttons. Knobs are used for the same kind of field the S3000 editor uses them for
    (`_KNOB_ATTRS`: 0..99 amounts and +-50 offsets); spinboxes for note range/velocity switch/MIDI
    offset/program number, combos for samples/output. **A fresh `Knob.setValue(0)` emits nothing**, so
    its value readout is filled explicitly in `_set_widget` (a knob at 0 once showed "-"). The one
    deliberate difference: no Multi/Programs/Samples tab bar (there is only one page - a one-tab bar
    would be noise); if an S950 Samples tab is ever built, add `FullWidthTabBar` then.
  - **Reads**: `S950Transfers.request_program(slot)` (op `"program"`) -> `SamplerController.
    s950_program_received(slot, Program | None)` - **`None` on ANY failure** so a waiting window never
    hangs. Every catalog read also emits `program_slots_updated`. A catalog read is NOT "busy" to
    `is_transfer_busy()`, so the window waits on the stricter `is_s950_idle()`. Only the latest wanted
    slot is read; a failed slot is marked shown so it never retries in a loop (Refresh clears that).
  - **Writes are STAGED, not live** (s950tools live-syncs every 400 ms; we send once, on "Write to
    Sampler"). The window edits a deep copy (`_working`) of what the unit holds (`_baseline`).
    `core/s950_params.py` is the foundation: `diff_programs(baseline, edited)` -> `Change`s (only the
    fields the editor offers - NOT `control_bits`, whose meaning is inferred), `validate_changes`
    (limits are s950tools' documented ones, unverified; the +-24 st transpose limit is OURS; only
    CHANGED fields are range-checked so a value the unit already holds out of range survives),
    `apply_changes`. `S950Transfers.write_program(slot, baseline, edited)` (op `"program_write"`, in
    `_BUSY_OPS`): fresh RPRGM -> **refuse unless a `.syx` backup of that reply saved** to
    `~/.akaisds/s950_backups` (`backup_dir` attribute; tests point it at tmp_path) -> apply ONLY the
    changes onto the FRESH copy (so a front-panel edit made since loading survives, and unmodelled raw
    bytes pass through) -> PRGM -> wire time + 500 ms NAK window (NAK = fail, names the backup) + 200 ms
    settle -> RPRGM read-back -> compare. Reports `s950_program_written(slot, verified, Program|None,
    message)`; on a mismatch the Program is what the unit reports and the window shows IT (and a
    warning), keeping nothing stale. It never creates a program (a pre-read of an empty slot just
    times out - the fake stays silent for one; that is itself a guess), never changes the keygroup
    count, and a no-change write is allowed on purpose (Hardware menu > "Write Program Back Unchanged
    (test)" - the first thing a tester should try).
  - **`Keygroup.to_bytes`/`Program.to_payload` now re-encode a field only if it no longer matches what
    `raw` already holds** - without that, a name the unit padded with NULs would silently come back
    space-padded on every write even if untouched. Don't go back to unconditional re-encoding.
  - Also: "Restore Previous" (writes back the pre-write baseline, session only), a one-time experimental
    warning before the first write (`app_config.get_s950_program_write_warning_acknowledged`), a refusal
    to give a program another program's name (what the unit does is unknown), discard-confirmation on
    switching program/refresh/close, controls locked while a write is in flight, and the old program is
    dropped (`_working = None`) the moment the next read starts so nothing can be written to it.
  - **Not done**: create/duplicate/delete program (no delete opcode; create needs slot picking), adding/
    removing keygroups, and **renaming a sample does not rewrite the programs that use it** (the
    Dashboard rename warns nobody - say so in the UI if that bites).
  - Everything unverified and every guess is listed in `tests/s950_test_plan.md` ("What is a guess").
- **Known unmeasured risk**: a receive is ONE message of up to ~1 MB. macOS CoreMIDI assembles
  it; backends that split long SysEx would lose it, because `MidiManager._on_raw_message` drops
  any fragment not starting with F0. A timeout there says so; ask for `~/.akaisds/akaisds.log`.
- `test_scripts/s950_demo.py` opens the real Dashboard on a `FakeS950` (no hardware; Settings
  doesn't work in it). `tests/test_s950_transfers.py` drives the real engine + controller
  against the fake over a fake MidiManager that delivers replies asynchronously. **Tests that
  repopulate the hardware list must flush deferred deletes** (the `dashboard` fixture does):
  `QListWidget.clear()` defers deleting row widgets, which otherwise fire inside another test's
  nested event loop (`_wait_for_any_signal`) and segfault (confirmed from a crash report).
- Byte-for-byte collision to remember: a standard SDS ACK on channel 0 (`F0 7E 00 pp F7`...)
  looks like the S950's own request-sample-dump (`F0 7E 00 nn 00 F7`). Generic SDS mode against
  an S950 gives nonsense, not a clean failure.

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
only enables with both MIDI ports selected AND `device_type == "akai"`
(the Program Editor needs Akai-only SysEx extensions Generic SDS lacks).
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

## Debug logging

`core/debug_log.py` → rotating log at `~/.akaisds/akaisds.log`.
`LoggingBridge` wraps whatever bridge `connect()` returns and logs every
call's START/END/FAILED with timing + a traceback on failure - ask for
this log (and the macOS `.crash` report if it aborted) before guessing at
a real-hardware bug report. App-wide despite the module being named for
the Program Editor - `sampler_controller.py`'s own unhandled-exception
backstops log here too. Any new unhandled-exception backstop anywhere in
this app should log here, not `print()`.

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
no hardware knowledge - moved out of `ProgramEditorWindow` unchanged (its old `_build_knob_column` etc.
methods are gone; call the functions). **Change a measurement there and BOTH editors change - re-check
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
outrank this). `_equalize_card_heights()` pins paired cards' heights equal
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
convention `_add_keygroup_row` already used, for this exact reason - and
keep whatever the row actually needs to display in the widget alone. The
Samples tab's list (`_on_samples_loaded`/`_build_sample_list_row_widget`,
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

`sample_root_note_spinbox` (`SPITCH`, `NoteSpinBox`, ranged `21..127`) and
`sample_tune_spinbox` (`STUNO`, a `QDoubleSpinBox` in SEMITONES not
literally cents despite the common name, ±50.00st) live in the "Root Note
& Tune" card.

**"Detect Root Note" button** (`core/root_note_detection.py` +
`_confirm_detect_root_note`) - hand-rolled, pure-Python time-domain
autocorrelation pitch detection, no new dependency (aubio/librosa/numpy
were all considered and rejected - pitch tuning is a matter of taste, not
correctness this app needs to nail perfectly; same reasoning as
`core/transient_detection.py`'s onset detection). Monophonic only.

**Octave disambiguation is via the CURRENT root note as an anchor, not
"lowest"/"loudest"**: a periodic signal's autocorrelation is ALSO strongly
peaked at every integer multiple of the true period (trivially, since
periodic-with-T implies periodic-with-2T/3T/...), so real audio typically
shows several genuine candidate peaks, not one. The anchor picks whichever
candidate is closest in semitones - never biases the measurement itself.

**Real bug found and fixed**: originally picked whichever peak was
nearest the anchor, full stop - a badly-set (or default) anchor could
reach past a strong peak and grab a much weaker, spurious one instead, so
the SAME audio reported wildly different "confidence" (37%-79%, confirmed
against `tests/test_audio.wav`, a chord stab) purely from the anchor,
nothing about the audio changing. Fixed: the anchor now only picks among
CREDIBLE peaks (`_CANDIDATE_CONFIDENCE_RATIO`, 0.8 of the strongest one
found) - still disambiguates genuine octave ambiguity, can no longer
manufacture false confidence in noise.
`test_confidence_does_not_swing_low_based_on_anchor_alone` regression-
guards this against the real fixture.

**Analysis window**: the loop region (`markers_with_loop_in_range()`,
already-stable/repeating, immune to attack-transient bias) is preferred if
its span is at least `_MIN_LOOP_ANALYSIS_FRAMES` (≈2 periods of the lowest
supported note); otherwise an attack-skipped, capped chunk of
`[start, end]`. **Both paths are capped to the same short length budget**
(`_ROOT_NOTE_FALLBACK_WINDOW_SECONDS`) - an uncapped loop region's own
analysis time scales with its length (confirmed: ~1.5s of real audio took
~1.5 SECONDS to analyse before this cap existed), which would make a
sustained-pad sample's own loop region noticeably slow to click.

**Confidence threshold** (`CONFIDENCE_THRESHOLD`, currently 0.5): below
this, the button shows a plain acknowledgement with no note/confidence/
choice offered - per direct user request, nothing actionable for an
unreliable guess. Needs real-hardware/real-material validation to actually
calibrate; the chord-stab fixture still clears 50% (it's polyphonic, not
pure noise), so this may need raising once tested against real
monophonic samples.

**Writing the result does NOT overwrite Tune outright.**
`core/akai_sysex.py`'s `compute_bandwidth_and_tuning` (Transfer Dashboard
send-time code, an unrelated file) bakes a permanent hardware-engine-speed
compensation into `STUNO` whenever a sample was originally sent at a
non-native rate (the Akai playback engine only physically runs at
22050/44100 Hz) - completely independent of the sample's own recorded
pitch. `_confirm_detect_root_note` recomputes that baseline fresh from the
sample's own declared rate and ADDS the detected residual on top, rather
than blindly discarding it (comes out to 0 - a no-op - for the
overwhelmingly common native-rate case).

**That baseline must come from the sample's own `SBANDW`, never
re-derived from its rate - a real bug, found and fixed.** Both
`_confirm_detect_root_note` and `_on_waveform_preview_requested` used to
recompute the baseline via `compute_bandwidth_and_tuning(framerate)`,
which picks whichever native bucket (22050/44100) is numerically NEAREST
the sample's rate. That's only the rule THIS app's own 16-bit Akai SDATA
send path actually follows (`sampler_controller._start_unit`,
`bit_depth == 16`) - a sample sent at any OTHER bit depth goes through
the generic/universal MIDI SDS path instead (no `STUNO`/`SBANDW` fields
in that protocol at all), and the SAMPLER ITSELF derives its own
`SBANDW`/`STUNO` from the incoming dump using an undocumented rule that
does NOT pick the nearest bucket. Confirmed on real hardware: an 11025 Hz
sample sent at 16-bit reads back `STUNO` -12.00 (bandwidth=0/22050, the
nearest bucket); the SAME sample sent at either 8-bit or 12-bit reads
back -24.00 both times - not "double" by some bit-depth-dependent
scaling, but exactly what you get from always assuming bandwidth=1/44100
regardless of which bucket is actually nearest. Re-deriving "nearest
bucket" for a sample that arrived via the generic path silently computes
against the WRONG baseline - the real, reported symptom was the Samples
tab's own click-to-preview playing such a sample back a full octave low
despite it playing correctly on actual hardware (and round-tripping
correctly through the Transfer Dashboard's own SDS receive → WAV save).
Fixed: `SBANDW` is now read directly (`_SAMPLE_DETAIL_FIELDS`, hardware's
own real value for THIS sample) and passed to
`baseline_semitones_for_bandwidth(rate, bandwidth)` instead of guessed -
correct regardless of which path actually produced the sample's `STUNO`.
`test_preview_requested_uses_real_sbandw_not_nearest_bucket_guess`/
`test_confirm_detect_root_note_uses_real_sbandw_not_nearest_bucket_guess`
both deliberately use a rate/bandwidth combination where the two
computations disagree, so a reversion to "nearest bucket" would actually
be caught.

## Slice Editor (Samples tab): manual ReCycle-style breakbeat chopping

"Slice Editor…" opens a modal `SliceEditorWindow` over a sample's
already-loaded audio - place slice markers by hand (Equal Slices, or the
live Sensitivity slider below), export every slice as a new one-shot
sample, optionally also building a whole new program (one keygroup per
slice, Const Pitch, one-shot). Same `has_waveform()` gate as other
sample-edit actions, but NOT demo-mode-disabled outright like Duplicate
Sample - only Export needs real hardware (`DemoBridge` has no add-sample
primitive); marker placement/click-to-preview/detection all work fine
without it.

`SliceWaveformView` is NOT `WaveformView` reused - genuinely different
marker model (two edges + an arbitrary-length interior list that CLAMPS
at a neighbour on drag, vs. `WaveformView`'s fixed four markers that PUSH
each other). Reuses `waveform_view.py`'s free helper functions, not its
class.

**Export**: every slice forced to one-shot (`SPTYPE=3`) regardless of the
source's own loop settings; `SPITCH`/`STUNO`/`SHLTO` copied from source;
the whole batch goes in ONE `send_file_queue` call (not one per slice) -
`SamplerController` already reports one combined `transfer_finished` for
a whole queue. Slice names zero-pad to a width computed ONCE per batch
(`<base>-01`..`<base>-16`, not `-1`..`-16`) so truncation stays consistent
across it. Collision checks re-query `existing_names_provider()` live,
never a stale snapshot. The dialog is application-modal specifically so
the user can't tab back to the Dashboard and start a conflicting send
mid-export - not a race this window otherwise has to poll for.

**Confirmed real dual-connection MIDI race** (the Samples tab's OWN second
connection, to fetch audio via `sampler_controller` - see above): a large
batch export's own sample-list reload can lose a reply to
`SamplerController`'s concurrent chatter on the same wire.
`_reload_sample_list_with_retries` retries up to 3 times - deliberately
scoped to `_export_slices` only (the biggest/longest send this window
ever triggers), not retrofitted onto Duplicate/Trim's own single reload
points without evidence they need it too.

**Click-to-preview** (`core/audio_preview.py`'s `SlicePreviewPlayer`, this
app's first non-MIDI audio output - `miniaudio`, not `QAudioSink`, after
`QAudioSink` produced measured real buffer underruns on Linux): a plain
single click (not double, which still adds a marker) on empty space OR a
marker/handle schedules a debounced preview
(`QApplication.doubleClickInterval() // 2`) rather than firing
immediately - the real Qt event sequence for a double-click is
press→release→doubleClick→release, so an immediate-fire would make every
marker-adding double-click also blip a stray preview. **Clicking a
marker/handle uses the TARGET's own frame, not the raw cursor pixel** - a
real bug: a click a pixel or two either side of a marker (still within its
own hover/hit radius) used to resolve to the wrong neighbouring slice,
even though the hover highlight visually said "you're on this marker." A
real drag (movement, not just a press) cancels the pending preview.

**Sensitivity slider - live transient detection, no separate button**
(`core/transient_detection.py`): hand-rolled energy-flux onset detection
(RMS over fixed windows, half-wave-rectified frame-to-frame flux,
threshold+local-peak+min-gap picking) - deliberately simple, no
FFT/numpy/aubio. 0% (the default/rest position) is fully OFF; dragging
the slider live-regenerates ALL slice markers from scratch on every tick,
same feel as a real ReCycle sensitivity knob - **no confirmation dialog**
(would be unusable while dragging), so touching it away from 0 silently
replaces whatever markers exist, hand-placed or not. `compute_flux()`/
`markers_from_flux()` are split so the expensive O(n) analysis pass runs
ONCE per loaded sample and only the cheap O(peaks) picking re-runs per
slider tick.

The slider itself is `ui/fine_slider.py`'s `FineSlider` (not a plain
`QSlider`) - double-click resets to default, Shift-drag is fine control
(same macOS no-cursor-warp tweak as `Knob`/`WaveformView`), click-then-
type opens the same generic `_TypeEdit` popup `Knob` uses. Deliberately
NOT a `Knob` subclass or refactored to share a base with it (zero risk to
that already-tested, widely-used class) - some real duplication accepted
instead. QSS styling (`style.qss.template`'s `QSlider` rules, previously
nonexistent - it rendered as a plain unstyled Fusion slider) uses
`#3aa88a`, the SAME hardcoded green `Knob`'s own value arc paints with,
not `${accent}` (which differs between themes) - deliberately
theme-independent, matching precedent.

**Layout fixes, all confirmed against the actual dialog, not just
reasoned about**:

- The zoom scrollbar now reserves its height even while hidden, matching
  the Samples tab's own `WaveformView` scrollbar - the original
  "collapse to zero height, no wrapper" choice was reversed after real use
  showed every row below it visibly jumping on every zoom-threshold
  crossing.
- The info label dropped its per-slice "lengths (frames): ..." list - at
  30+ slices (easy via the sensitivity slider) the wrapped text grew tall
  enough to push every row below it down the window.
- The waveform view used to be `setFixedHeight(220)` - resizing the
  dialog taller did nothing for it; the surplus vertical space instead
  silently landed on the info label (the only OTHER non-Fixed-policy
  widget in the layout - same failure class the Program Editor's own
  section cards needed `Preferred,Fixed` to avoid, see above). Fixed:
  `Expanding` + a 220px floor (not ceiling) on the waveform,
  `Preferred,Fixed` explicitly on the info label,
  `layout.setStretchFactor(waveform, 1)` as a third belt-and-suspenders
  guarantee.
- Wrapping `export_row` in a `QWidget` (for the height fix above) needs
  `setContentsMargins(0,0,0,0)` explicitly - a bare top-level layout picks
  up Qt's own default margins that a nested `addLayout()`'d one never had,
  which silently clipped the row's own buttons at the bottom until caught
  from a screenshot.

`AKAISDS_DEMO_INSTANT` skips `_fetch_demo_sample_audio`'s realistic
pacing entirely - for iterating on this window without waiting out a
simulated transfer per sample selected. The standalone launcher (bottom of
`program_editor_window.py`) needs this AND `AKAISDS_DEMO_SAMPLER=1` both
set - its `_StandaloneSamplerController` stub needs `cancel_transfer()`
too, not just `is_transfer_busy()`, or `_open_slice_editor` raises
`AttributeError` (a real bug, found and fixed).

**Other real bugs found building this window**: `_wait_for_any_signal`
used to `submit_*()` before connecting listeners (same class as the
sample-edit bug above); a test holding a real lock across real
`threading.Thread`s here reproducibly segfaulted the interpreter elsewhere
in the suite (same instability class the MIDI transport section above
describes - don't add another).

## Testing

See `TESTING.md`. `uv run pytest tests/ -v` runs everything in well under
10 seconds. Tests force `QT_QPA_PLATFORM=offscreen` via
`tests/conftest.py`'s own unconditional assignment - NOT the per-file
`os.environ.setdefault(...)` calls each test module also has, which are
no-ops if the desktop environment already exports this var globally
(confirmed on an Omarchy/Hyprland machine, where every "offscreen" test
was silently running on the real Wayland platform the whole time; the one
test that `.show()`s a real widget - `test_qt_helpers.py`'s tab-width
tests - was flaky for exactly this reason, a real window manager tiling-
reflowing a genuine on-screen window, not a numeric fluke).
