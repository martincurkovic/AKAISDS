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

The Settings "Sampler Type" combo has four entries (`core/sampler_models.py`,
alphabetical): "Akai S1000" (`akai_s1000`), "Akai S2000/S3000"
(`akai_s2000_s3000`), "Akai S900/S950 (experimental)" (`akai_s900_s950` - a different
protocol, see its own section), "Generic SDS" (`generic`). Before the S1000 existed the
S2000/S3000 was persisted as plain `"akai"`; that legacy value is still accepted
everywhere (`sampler_models.normalize`) and `app_config.ensure_defaults_saved()`
rewrites it in `config.json` at startup so the file names the real hardware
(only that exact legacy string - an unrecognised value from a newer version is
left alone). Note `"akai"` also remains the PROTOCOL FAMILY name below - a
different thing from the saved selection. The S1000, S2000 and S3000 all answer a MIDI identity/status request
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
`invalidate()`d/`activate()`d before `equalize_card_heights` re-measured.
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
`s1000_keygroup_delete_blocked` (action disabled for the session).

**Standalone Delete Keygroup on an S1000: the ORIGINAL flow failed on real hardware (2026-10-06 tester log,
`akaisds-5.log`); a REDESIGN is ON in the test build sent after that (`s1000_bridge.KEYGROUP_DELETE_SUPPORTED = True`),
UNMEASURED.** What failed, in order, on a throwaway 10-keygroup copy (program 2, program at 0x1068, `FIRSTKG`
0x10fe): (1) DELK(program 2, keygroup 0) was acknowledged; (2) the sampler did NOT touch `GROUPS` (10 -> 10, as
before) and did NOT compact memory - it only advanced the program's `FIRSTKG` by 150 (0x10fe -> 0x1194, i.e.
kg 1 is now first; the old kg 0 block is orphaned) and left the last keygroup's stale `NXTKG` (0x0672, a sample
header) alone; (3) our PDATA rewriting the program with `GROUPS=9` (and the sampler's new `FIRSTKG`) got
`REPLY` error `47 00 16 48 01` (code 01; the tester's status bar read "device rejected updating program 2
groups=9 (code 1)"); (4) the program was left with 9 reachable keygroups but `GROUPS=10`, so every read of
keygroup 9 hit "block identifier is 3, expected 2" and the editor couldn't load it. Other programs were untouched.
Two later delete attempts were stopped harmlessly by the pre-DELK snapshot failing on that broken program.
**Why the PDATA was rejected is NOT known.** Hypothesis (unmeasured): a PDATA is only accepted when
`FIRSTKG == the program's own address + 150` (every PDATA that has worked - creates, editor writes, GROUPS+1
after a KDATA - satisfied that), and DELK of keygroup 0 broke it. (Other candidates: the sampler validates
`GROUPS` against a count of physical blocks that still includes the orphan; unknowable from this log.)
**The redesign** (`BridgeWorker._delete_keygroup_s1000`): read keygroups k..N-1 up front; overwrite slot i with
slot i+1's content for i = k..N-2 by KDATA, ascending, each slot KEEPING ITS OWN bytes 1-2 (`NXTKG`) so no
address moves (`_shift_keygroups_down_s1000`); DELK the LAST keygroup (index N-1) so `FIRSTKG` should not
move; then PDATA `GROUPS=N-1` (`_repair_groups_after_delk`) and the existing before/after snapshot compare.
Deleting the last keygroup shifts nothing. Any failure once the first write is sent sets
`s1000_keygroup_delete_blocked` (before this, only a failed *verify* did, so a tester could retry) and shows
"only partly applied"; **no recovery is attempted** (a blind KDATA against a stale `GROUPS` is what linked a
keygroup into another program on 2026-10-05). Until the DELK, a failure just leaves a program with a repeated
keygroup. A "probe" PDATA with `GROUPS=N-1` first was considered and dropped: if the sampler validates `GROUPS`
against the chain (the spec says "GROUPS must be correct"; `FakeS1000` models that), the probe would fail even
when the real sequence would work. **What to look for in the next log**: the `S1000 delete keygroup ... Program
pointers before:` line, the `GROUPS a -> b after DELK` line, and whether the program's `FIRSTKG` changed after
DELK of the last keygroup. If it fails again, set the flag False and rewrite this paragraph with the new
measurement - don't try variants blind. The sampler's front panel is the way to remove a program left broken.
`FakeS1000` reproduces (a) (`delk_updates_groups=False` default) and refuses a
KDATA past the end of the chain, but does NOT model the address pointers themselves (so it cannot show
the real rejection - it needs an address model before a redesign can be tested offline).
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

## Akai S900/S950 support (written without hardware)

A DIFFERENT protocol from every other Akai entry, not a variant of the S1000
family: device byte `0x40` (not `0x48`), function codes 0-11, every 8-bit value as
TWO MIDI bytes, 10-char plain-ASCII names, XOR checksums, and a sample dump that is
ONE SysEx (header + all blocks + a single `F7`) with 4-byte handshakes
(`F0 7E code F7`) - the standard 6-byte SDS ACK is silently ignored. Plan and
staging: `dev_docs/s950-support-plan.md`.
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
- Sampler Type "Akai S900/S950 (experimental)" (`akai_s900_s950`) has its OWN protocol family, `"s950"`
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
  one-time experimental warning before the first Send. Its Program Editor is described below.
- **Program editor** - `ui/s950_program_editor.py`'s `S950ProgramEditorWindow`, opened by the Dashboard's
  Open Editor button (`open_program_editor` branches to it for the S950; the button needs both ports,
  and `open_program_editor` refuses with no MIDI input). A deliberately separate small window, NOT
  `ProgramEditorWindow` (built on `s3k.params`). It shares the Dashboard's `SamplerController` - same
  connection, no `S3kBridge`/`BridgeWorker`, no second thread.
  - **Layout deliberately mirrors the S1000/S2000/S3000 editor's Programs tab** (the user asked for
    consistency; keep them in step through `editor_layout`): Programs column | Keygroups column (`KeygroupRangeBar` + `keygroupList` rows with colored
    swatches, recolored on `theme.notifier.changed`, item with NO text because it has a row widget) |
    `detail_stack` of a program page and a keygroup page (program click -> page 0, keygroup click ->
    page 1), section cards via `build_section_card` (Range, Filter, Amplitude/Filter Envelope =
    `ADSREnvelopeGraph` over four `Knob`s, a `zoneCard` with Soft/Loud buttons where the S3000 has
    Zone 1-4, Velocity, LFO, Pitch Warp, Output), and a bottom bar with Refresh left / Close right plus
    the write buttons. The Zone card's Sample combo/Filter/Loud row uses the S3000 editor's shape (`build_labeled_combo_column` +
    `build_labeled_knob_value_column`: bold label above each, value readout to the RIGHT of the knob), and the LFO
    card is Rate/Depth (52px, `_centered_knob_row`) over Build-up/Aftertouch/Mod Wheel (40px). The per-sample
    **Filter** knob (`soft_filter`/`loud_filter`, 0..99, 99 brightest) is the only cutoff-like control the keygroup
    block holds - the Filter card is key track/velocity/envelope amount/filter envelope. Tune shows 4 decimals on
    purpose: the raw unit is 1/16 semitone (0.0625), which 2 decimals would round; the unit itself is s950tools',
    unverified. Knobs are used for the same kind of field the S3000 editor uses them for
    (`_KNOB_ATTRS`: 0..99 amounts and +-50 offsets); spinboxes for note range/velocity switch/MIDI
    offset/program number, combos for samples/output. **A fresh `Knob.setValue(0)` emits nothing**, so
    its value readout is filled explicitly in `_set_widget` (a knob at 0 once showed "-").
  - **Tabs, like the S3000 editor (Programs | Samples; no Multi)**, Ctrl+2/Ctrl+3. The program-write buttons are
    hidden on the Samples tab. **The first program is auto-selected** when the editor opens (and whenever the chosen
    one vanishes from the catalog) - the placeholder before the catalog arrives says "Reading the program list...",
    never "select a program". The "N keygroups" note under the keygroup list was removed at the user's request.
  - **Samples tab = `ui/s950_samples_tab.py`'s `S950SamplesTab`, a self-contained widget, READ-ONLY** (list with
    durations, a `WaveformView`, a details card). It is built from the same `editor_layout` pieces (sample list row,
    `build_samples_page`, `build_waveform_scrollbar`/`sync_waveform_scrollbar`, now shared with the S3000 editor, which
    was verified pixel-identical after moving them). It only ever READS: no sample-parameter write exists for the S950
    because start/end/loop semantics are inferred and some SPRM changes sit in an "active edit buffer" (see the test
    plan's guesses). Rename a sample from the Dashboard. Mechanics worth knowing: (1) per-sample SPRM reads
    (`S950Transfers.request_sample_params` -> `s950_sample_params_received(slot, SampleParams | None)`, op
    `"sample_read"`, in `_BUSY_OPS`) run one at a time, ONLY while the tab is active (`set_active`), selected sample
    first, and a failed slot is remembered and never retried until `refresh()`; (2) audio is received ASYNCHRONOUSLY
    through `receive_samples` into a temp WAV (the S3000 editor freezes the window for this; the S950 engine is
    event-driven so nothing here blocks), shown only if its sample is still selected when it lands, temp file always
    removed; the Dashboard's own receive handlers see the same signals and are harmless while it is hidden; (3) a load
    or read that finds the wire busy RETRIES (`_RETRY_MS`) rather than failing; (4) a changed catalog name drops that
    sample's cached SPRM/audio; (5) `WaveformView.set_markers_locked(True)` (new, opt-in) keeps the markers drawn but
    un-grabbable, and `set_placeholder_text()` replaces the S3000 default that promises a freezing load.
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
- `tools/s950_demo.py` opens the real Dashboard on a `FakeS950` (no hardware; Settings
  doesn't work in it). `tests/test_s950_transfers.py` drives the real engine + controller
  against the fake over a fake MidiManager that delivers replies asynchronously. **Tests that
  repopulate the hardware list must flush deferred deletes** (the `dashboard` fixture does):
  `QListWidget.clear()` defers deleting row widgets, which otherwise fire inside another test's
  nested event loop (`_wait_for_any_signal`) and segfault (confirmed from a crash report).
- Byte-for-byte collision to remember: a standard SDS ACK on channel 0 (`F0 7E 00 pp F7`...)
  looks like the S950's own request-sample-dump (`F0 7E 00 nn 00 F7`). Generic SDS mode against
  an S950 gives nonsense, not a clean failure.

## Yamaha A4000/A5000 editing (IN PROGRESS - parameter editing, native wave dumps, Dashboard list/receive, restore-from-backup and assign/remove samples work; no create/delete yet)

Goal: a program/sample editor for the user's Yamaha A4000 (sending samples works through plain SDS, which the Yamaha
Sampler Type keeps; listing/receiving samples and the editor use the unit's own protocol). Plan, handoff and every measured fact: **`dev_docs/a4000-editor-roadmap.md`** (read it before
touching anything here). State: `core/yamaha_sysex.py` (codec) + `core/yamaha_params.py` (204 program/Easy Edit/sample
rows: P-address + bulk offset) + `core/demo_a4000.py` (`FakeA4000`) + `controller/yamaha_session.py` (conversation
engine) + `ui/yamaha_program_editor.py`/`yamaha_samples_tab.py`/`yamaha_fields.py`/`yamaha_writer.py` (Programs | assigned
samples | cards, and a Samples tab; every writable control edits the unit) all exist with tests and were checked against the real unit. The Sampler Type is
`yamaha_a4000` ("Yamaha A4000/A5000 (experimental)"): its PROTOCOL FAMILY is `generic` (sample SENDS are plain SDS) but
`sampler_models.is_yamaha()` makes the Dashboard offer the Yamaha editor, list and receive. The Samples tab shows the WAVEFORM
(double-click; the native wave dump, both channels). Not done: effects/controls/system params, delete objects (creating samples = the native bulk load, below).

- **Audio comes over the unit's NATIVE wave dump ("WD"), not SDS** (`core/yamaha_wave.py`, `YamahaSession.request_wave`; layout
  measured and verified byte for byte against SDS dumps - read that module's docstring before touching it): a sample links a left
  wave object (SP payload @64) and, if stereo, a right one (@80); each is requested by ITS OWN name and arrives as several complete
  ~4 KB bulk messages (block number, then the audio words), ~620 frames/s. Why: SDS stalled on the real unit after a stereo
  recording (even for mono samples) while WD kept working, WD is faster, and SDS can't reach a stereo sample's right channel.
  **A bulk dump CAN'T be aborted** (measured: neither an identity request nor an SDS CANCEL stops it), so a cancelled wave makes
  the session DRAIN - it stays busy (`idle` False) until the stream has been quiet for `drain_idle_ms`, which after a cancel at 7 s
  of a 2 s stereo sample was ~70 s. Everything queued behind it (the Programs tab's reads, a Dashboard refresh) waits that long.
- **The Samples tab shows both channels** (two `WaveformView`s at 90 px each for a stereo sample, one at 180 for mono, kept in step
  by `set_view_state`, and drawn as ONE display: zero layout spacing, `WaveformView.set_stack_position("top"/"bottom")` drops the seam border,
  continues the dashed marker lines across it and gives boundary-marker handles to the top half and loop-marker handles to the bottom), draws progressively (`begin_live_capture`/`append_live_samples`, fed by the session's `on_chunk`) - SMOOTHLY: the unit's ~1.5 s
  lumps are queued and released ~30x/s at a pace that would just drain them by the next chunk (gap estimate refined from the real
  ones; measured on the unit: 11 chunks -> 451 growth steps, biggest jump 30 frames; display only, the load finishes once the
  queue has drained) - and has a
  Cancel button. The loaded audio is checked against the sample's own `wave_length` and refused if it doesn't match.
- **Samples tab list + envelope graphs**: rows use the shared `build_sample_list_row_widget` (name left, grey `0.45s` duration right,
  taller than a plain row; the item has NO text of its own, the name is in `Qt.UserRole` - use `tab.sample_names()`). Durations come
  from each sample's SP dump, read by a background scan that only issues a request while `session.idle` (so it never gets in front
  of a user action) and skips what the host already cached (`note_cached`). The three envelope cards each have a shape-only graph:
  amplitude reuses the S3000's `ADSREnvelopeGraph` through `envelope_graph.yamaha_adsr_values` (the A4000's attack/decay/release are
  RATES - higher is FASTER, owner's manual - so they become times; factory sample = instant stages), filter and pitch use the new
  bipolar `LevelEnvelopeGraph` (init/attack/sustain/release levels -127..+127). No calibrated timing, like the originals.
- **Layout/styling pass** (Yamaha pages): cards sit in ALIGNED side-by-side pairs, each pair pinned to equal heights
  (`equalize_card_heights`), so left and right line up top and bottom - the user rejected independent columns ("un-aligned") and
  collapsible cards (which can't be pinned equal) after trying both. Pairs are chosen so a row's cards are about the same height
  (Programs: Program/Portamento, LFO/Audio Input, Step Wave full width; Samples: Pitch/Key, Level/Loop, Filter/Filter Envelope,
  Amplitude/Pitch Envelope, LFO/Controllers, Output/EQ). `Knob`'s value arc grows from ZERO for any range that spans zero - in EVERY editor (S3000/S1000/S950/Yamaha;
  the user loves it; `setBipolar(False)` opts a knob out; no tick mark - the user didn't want one), and from the minimum otherwise, exactly as before; `=Sample` combos/spinboxes get an `inherited` property that a QSS rule
  dims; read-only text rows (`Field(muted=True)`) are grey; every labelled row uses `yamaha_fields.LABEL_WIDTH` so values line up;
  hovering a knob's NAME shows the same tooltip as the knob.
- **The Transfer Dashboard lists and receives natively too** (`controller/yamaha_transfers.py`, built lazily by `SamplerController`):
  Refresh = the object list's samples (`sample_list_updated`, index == number == SDS position - measured to stay true after a
  delete); Receive = SP dump + the wave dump(s) -> a mono or STEREO WAV at the sample's rate. Delete/rename/info are refused (no
  known opcode - front panel). **SENDING samples is still plain SDS** (`device_type` "generic"; the native route for loading
  audio is unmeasured). `is_sds_transfer_busy()` (what the session waits out) vs `is_transfer_busy()` (also counts a running
  Yamaha receive, which goes THROUGH the session - never make the session wait on that one).
- SDS facts that still hold (the Dashboard's sends use it): number == the sample's CURRENT list position, also after a delete
  (measured by audio fingerprint, `tools/a4000_sds_numbering.py`); the unit trims 4 frames off a sent sample; a Dashboard stereo
  send becomes two unrelated mono samples; **Bulk Protect ON makes the unit CANCEL incoming SDS sends**.
- **No worker thread.** `SamplerController.yamaha_session()` -> `YamahaSession`, event-driven on the GUI thread, fed incoming
  0x43 SysEx by the controller. **Never connect `controller.on_sysex_received` to `MidiManager.sysex_received` yourself** - the
  controller already does, and a second connection delivers every message twice (that once made the next program's read
  return the previous program's value). The session also refuses any parameter value not preceded by a fresh announce of the
  right object, so a stray duplicate can't be mistaken for a reply.
- Verified on a real A4000 (2026-10-06, device number 0): the service manual (MIDI DATA FORMAT, p.32-42) is accurate,
  with one correction - the bulk byte count is **MSB-first** and a dump is **several blocks inside one F0..F7**
  (`count(2) span xor-checksum` each; only the first span carries the 26-byte header; XOR, not a sum). Don't rewrite
  `parse_bulk_dump` to the manual's wording. A select gets NO reply; the unit announces its current object (a select-shaped message) just before answering a parameter request.
- `tests/fixtures/a4000/*.syx` are REAL captures; the tests rebuild them byte for byte. Never regenerate them from
  the codec. `tools/a4000_discovery.py` is the read-only probe
  that captured them; it refuses to send anything but identity / dump / parameter requests and object select.
- `tools/a4000_verify_params.py` (read-only) and `tools/a4000_write_verify.py` (writes a value per row to a throwaway
  object and diffs the dump - **only run on a unit with nothing of value in it, app closed**) have proven every program,
  Easy Edit and sample row's offset/bit position. The manual is wrong about sample `P2=66` (AEG sustain is `P3=2`, not
  0-1); don't "fix" that back. Real behaviours to remember: every edit sets bit 0 of byte 1 of the object's common block
  (an "edited" flag); sample controls are mirrored into the first 24 bytes of the sample block; EQ writes update derived
  coefficient bytes; `sampling_frequency`/`wave_length`/`wave_end_address` writes are accepted and ignored (on a built-in
  sample); wave/loop addresses are coupled (writing one moves others). Details: `dev_docs/a4000-editor-roadmap.md`.
- House rules the Yamaha work follows (same intent as the Akai editors, different mechanism): ONE operation on the wire at a time
  because a select is stateful - done by `YamahaSession`'s FIFO queue on the GUI thread, NOT a worker thread (don't add one); the
  shared MIDI transport; a `.syx` backup before any write; an experimental warning.

- **Writes (2026-10-06)**: `YamahaSession.write_parameter(row, value, object_name, cb, slot=)` - ONE parameter per op, guarded:
  (1) the first write to an object per session dumps it and saves a `.syx` backup (`backup_dir`, default
  `~/.akaisds/a4000_backups`; conftest points `controller.yamaha_session.BACKUP_DIR` at a temp dir) - **no backup, no write**;
  (2) an edit applies to whichever object was selected LAST and a select gets no reply, so the edit is sent only after a
  parameter request (the "probe", which also reads the old value) was answered with an announce naming the right object;
  (3) after the edit (no reply exists) a read-back must equal the value. Refuses read-only/bulk-only/`write_ignored`/A5000-only
  rows and out-of-range values without touching the unit. `WriteCoordinator` (`ui/yamaha_writer.py`) throttles widget edits
  (150 ms, latest value per row), shows the one-time experimental warning (`yamaha_write_warning_acknowledged`), and re-reads
  the object once its writes settle (side effects the read-back can't show). `FieldPanel.set_editable` enables only rows the
  table says are writable; spinboxes have keyboard tracking OFF (typing "100" is one edit). The window refuses to close/refresh
  under a write. Hardware menu: "Write Program Back Unchanged (test)" and "Open Backup Folder".
  **Proven on the real A4000** with `tools/a4000_session_write_check.py` (the session's own write path): 13/13 program + sample
  rows EXACT, a write of a row's own value leaves the dump identical, every original restored, backups equal the pre-write object.
  Easy Edit rows through the session: 6/6 EXACT on program 001 slot 0 too.
- **A write is refused silently by Bulk Protect** (UTILITY > MIDI bulk page): edits get no reply, the read-back shows the old value
  and the message says so (it also makes the unit cancel incoming SDS sends - check it first when a send "does nothing").
- The controller drops SDS header/data packets that were already on the wire for 3 s after a user cancel of an SDS receive
  (`_RECEIVE_CANCEL_GRACE_S`) instead of reporting "unrecognised SysEx" over the "cancelled" status. A stereo sample (non-empty
  right wave name at payload @80, `yp.is_stereo`) is labelled "stereo" in the Samples tab.

- **Restore from backup (session 3)**: Hardware > "Restore from Backup..." (`RestoreDialog` picks a `.syx` from `~/.akaisds/a4000_backups`
  or Browse). `core/yamaha_restore.plan_restore` diffs the backup against the object as it is NOW and `controller/yamaha_restore.RestoreJob`
  writes only the differing WRITABLE rows through `write_parameter` (so every write keeps its guards) - **a restore is NOT a bulk load**
  (unmeasured). It saves a SNAPSHOT of the current state first (no snapshot -> nothing written), writes the coupled wave/loop address rows
  LAST then re-reads and makes up to 3 passes, stops at once on a write the unit swallowed (Bulk Protect), and reports what it cannot put
  back (`residual_offsets`; assignments are never restored - only slots still holding the same sample). Proven on the real unit
  (`tools/a4000_restore_check.py`). Details: the roadmap's "Restore from backup" section.
- **Assigning samples to programs (session 3)**: the OBJECT LINK CHANGE message (`build_object_link_change`, program upper / sample lower,
  1 link 0 unlink), via `YamahaSession.change_link` (backup once, send, ASK the unit with the link request - the answer is the verification,
  a change gets no reply). MEASURED: linking appends the next Easy Edit slot with defaults (receive channel -1), unlinking a middle slot
  COMPACTS the rest (they keep their values), linking twice / an unknown name change nothing. UI: "Assign Sample..." / "Remove" under the
  assigned-samples column (`AssignDialog`; removal asks first); both lock the window like a restore. `_select_sample_after_load` selects the
  new slot once the program is re-read; `_counts` follows so the "has samples" filter stays right. Untested on hardware: stereo samples,
  sample banks (the UI only assigns samples), a full program.
- **Editable markers + click-to-preview (session 4, 2026-10-06)**: the Samples tab's four waveform markers are editable and a single
  click on the waveform previews the sample (like the S3000 editor). `core/yamaha_markers.py` is the model: the unit keeps FOUR independent
  addresses (wave start, wave end, loop start, loop end) and derives the two lengths; it SILENTLY IGNORES a write that would break
  start <= loop_start <= loop_end <= end (or an end past the wave's own size), so `plan_marker_writes` orders a change (widen the wave,
  widen the loop, narrow the loop, narrow the wave) and `target_for_edit` builds the target from only the markers that MOVED (the view
  draws a non-looping sample's loop at the wave end, which is NOT what the unit stores - never write the view's loop markers back
  wholesale). MEASURED with `tools/a4000_marker_probe.py` and proven with `tools/a4000_marker_check.py` (the real tab + write path on a
  user sample): `wave_length`/`wave_end_address` are NOT ignored on a USER sample (they were only ever flagged `write_ignored` from a
  built-in; the flag is gone, a built-in simply ignores an end change and the tab says so); while the loop mode does not loop (0/3/4/5)
  the loop END follows the wave end (and the loop can end up with start > end and an underflowed length - move the loop first, which the
  planner does); a loop start may sit AT the wave end (the default of a fresh user sample - a drag can't reach it, the view's last
  frame is end-1). The tab writes the plan one step at a time (`_run_marker_step`, through `WriteCoordinator`, each step guarded and
  read back), re-reads the sample, and puts the markers back from the cache if a step fails; a drag made meanwhile waits and is
  re-planned from a fresh read. The two channel views are linked (`_on_markers_changed`/`WaveformView.apply_markers`): one set of
  addresses on the unit. **STEREO IS UNMEASURED**: no stereo sample was on the unit - does the unit really move the right channel's
  twin address bytes with a write? (the tab only ever reads/writes the left addresses). The restore plan now writes the four ADDRESS rows
  in planner order and never the two length rows (derived). `FakeA4000._edit_geometry` models all of the above. Preview: `SlicePreviewPlayer.play/
  play_loop` take an optional `right_samples` (real two-channel output; mono callers are untouched); loop modes: 0/4 plain run, 1 held
  loop (click again to stop), 2 loop for `_RELEASE_PREVIEW_MS`, 3/5 a reversed copy with the playhead mapped back. No pitch shift (the
  sample plays at its own rate/key). Audio must be loaded first; `audio_matches` now accepts a wave LONGER than the end address (a
  trimmed end is legal).
- **NATIVE SAMPLE LOADING (session 4, 2026-10-06; written from `dev_docs/a4000-native-load-findings.md` - read it)**: Dashboard "Send Samples" for the
  Yamaha Sampler Type no longer uses SDS. `core/yamaha_load.py` builds a load = the wave dump(s) (`yamaha_wave.build_wave_messages`) THEN one sample
  dump (`SP`, from a real user sample's captured parameters with name/wave names/rate/addresses set); `YamahaSession.send_messages` sends them
  paced by wire time + `send_gap_ms` (400); `YamahaTransfers.send_file_queue` drives it (WAV -> channels, mono/stereo, rate override) and VERIFIES
  (wait `verify_delay_ms`, object list holds the sample + waves, SP read-back matches frames/rate/stereo; each read retried - the first read after a
  load is sometimes unanswered). MEASURED: a bulk dump sent to the unit is never answered ("MIDI Bulk Received" on its LCD; it needs no OK pressed);
  a WD alone is dropped - it only counts with the SP that names it (either order); a missing/short/duplicated/reordered wave message creates
  NOTHING; a wave under an existing wave's name is overwritten IN PLACE (so our wave names are random `SMP nnnnnn`, never derived from the sample
  name); a sample under an existing SAMPLE name is replaced in place with ALL its parameters reset (so the app never overwrites: a clash gets
  ` 2` added - `yamaha_load.unique_name`); no frames trimmed (SDS trims 4); rates 1..65535 verbatim; ~440-590 frames/s per channel (stereo twice).
  Stereo is two wave objects + the right name at SP @80 (works, user listened). **Open**: linking a freshly loaded sample to a program once left the
  unit silent for 28 s / ~8 min (cause unknown - give link generous timeouts); pacing minimum, odd names, wave-memory-full and a failed REPLACE are
  untested. `FakeA4000._bulk_load` models the measured rules (`same_name_replaces` is a guess flag). **The unit cannot report free memory over
  MIDI**: the Dashboard's memory bar is an ESTIMATE (`YamahaTransfers._estimate_memory`: sum of the samples' word counts vs
  `app_config.get_yamaha_wave_memory_kb`, entered in Settings > Wave memory, in kB as the unit's PLAY > PROGRAM > FREE MEMORY page shows it; the
  user's is 102400); hidden while unknown. Sending needs a MIDI input (the check reads the unit).
- **HANDOFF (end of session 3, 2026-10-06):** sessions 1-2 are committed on `s1000-support`; session 3's work (restore + assign) may be
  UNCOMMITTED - check `git status` and offer a commit; the suite had 2060 passing tests. Read `dev_docs/a4000-editor-roadmap.md` "HANDOFF"
  and "Next steps" before continuing: (markers + preview are DONE, see above - stereo markers still need a real stereo sample), then the missing tables
  (effects/controllers/system/banks), then native audio loading; a stereo sample is needed to test assigning/restoring one (the unit was
  cold-booted, only factory samples remain). The user's look-and-feel decisions are listed there too.
- **Testing the Yamaha code - gotchas:** replies that must arrive over time (cancel mid-stream, progressive fill) use
  `rig.midi.paced = True` on the shared `_Midi` fake (tests/test_s950_transfers.py) - NEVER swap a test object's `__class__` (an
  intermittent PySide segfault; the one older `Twice` test still does and works, don't copy it). `dispose(window)` in
  tests/test_yamaha_program_editor.py calls `samples_tab.disconnect_controller()`; without it a pending duration-scan timer touches
  deleted row widgets. The `window`/`win` fixtures shorten `_REVEAL_INITIAL_INTERVAL_S`/`_REVEAL_FINISH_S` (real chunks are ~1.5 s
  apart, the fake's are instant) and an autouse fixture patches the one-time write warning so no test opens a dialog or writes the
  real config. An unshown window has no layout geometry: `resize()` then `layout().activate()` before measuring positions. A pixel
  test of a custom-painted widget must call `set_view_height` (WaveformView is fixed at 180 px; `resize` alone does nothing).
  Never let a bulk string replace touch more than one place in `yamaha_session.py` (one did, and silently cleared `_backups` in
  `cancel()` - there is a regression test now).

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
