# Agent notes for AKAISDS

This file is for AI agents picking up work in this repo cold. It exists to
save you from re-deriving context that isn't obvious from the code alone,
and especially to stop you from "fixing" things that look like bugs but are
actually deliberate, hard-won corrections. For human-facing docs see
`README.md`, `BUILDING.md`, `TESTING.md`, `CONTRIBUTING.md` - not repeated
here.

## What this app actually is

Two largely independent windows, both PySide6/Qt, both driven by `src/main.py`:

- **Transfer Dashboard** (`src/ui/dashboard.py`, `src/ui/main_window.py`,
  `src/controller/sampler_controller.py`) - sends/receives audio samples to
  Akai S1000/S2000/S3000 samplers (or generic MIDI SDS devices) over SysEx.
  This is what the README documents.
- **Program Editor** (`src/ui/program_editor_window.py`,
  `src/core/program_editor_bridge.py`) - edits a program/keygroup/multi's
  *parameters* (filter, envelopes, pan, LFO, keygroup ranges, multi part
  assignments) on an S3000-series sampler over SysEx. Opened via the
  dashboard's Window menu. Most of this file is about this window.

Both windows swap via a `&Window` menu action; only one is ever shown at a
time (`closeEvent`/`open_program_editor` in `main_window.py` /
`program_editor_window.py`).

## Third-party dependencies you should actually go read

`s3k` and `s3ked` are **not part of this repo** - pulled in via
`pyproject.toml` (`s3ked` pinned to a git rev; `s3k` comes with it), living
in `.venv/lib/python3.12/site-packages/s3k(ed)/`:

- `s3k.bridge.S3kBridge` - the real MIDI/SysEx bridge to hardware.
- `s3k.params` - the parameter registry (`p.lookup(name, region)`). Its
  `Parameter.notes` fields record hardware measurements and dated research
  notes - **read these before assuming a field's range/meaning**; several
  past bugs here came from trusting the Akai manual's prose over what was
  actually measured.
- `s3ked.demo.DemoBridge` - a duck-typed fake sampler for dev without
  hardware; deliberately reproduces real hardware quirks, not just canned
  values.

Don't "fix" anything in these packages - pinned external dependency; raise
upstream if something looks wrong, don't edit in place.

### Three fields where this project's own hardware beats s3k.params' notes

`s3k.params`' notes are usually the most trustworthy source in the stack -
but on these three, this project's own hardware measurement (confirmed
in front of the user) overrides it:

- **`K_FREQ`** (keygroup filter key-tracking): `s3k.params` says `-30..99`.
  Re-measured on hardware (2026-09-20): real range is `-24..+24`.
  `key_filter_track_knob` uses `-24..24` - a narrower subset, so it already
  passes `encode_field`'s own range check, no override machinery needed.
- **`B_PTCHD`** (pitch-bend-down range): `s3k.params` says `0..12`
  (asymmetric with bend-up's `0..24`). Re-measured (2026-09-21): actually
  `0..24`, symmetric. Unlike `K_FREQ` this is WIDER than the declared range,
  so a raw write above 12 fails `encode_field`'s check against the pinned
  `s3ked` rev - `core/program_editor_bridge.py`'s
  `_HARDWARE_RANGE_OVERRIDES`/`_lookup_for_write` patches a corrected
  `dataclasses.replace` copy of the `Parameter` in front of every write.
  See `tests/test_program_editor_window_demo_bridge.py`'s bend-down tests.
- **`LFO2TRIG`** (LFO2 retrigger mode): `s3k.params` declares raw `0..255`
  with no enum/notes at all. Measured (2026-09-21): it's a plain boolean.
  `lfo2_trig_combo` offers only Off/On - UI-only narrowing, same shape as
  `K_FREQ`, no override needed (0/1 always fits 0..255).

If you're auditing widget ranges against `s3k.params` and find one of these
three "wrong," it isn't - re-read the comment at the call site before
"fixing" it back. If you get hardware access and can re-verify any of
these (or find another), update the comment with date + finding.

## Developing without hardware

Set `AKAISDS_DEMO_SAMPLER=1` before launching; `program_editor_bridge.connect()`
returns a `DemoBridge` instead of a real MIDI port. Good for UI work, but
can't exercise real timing/latency or `S3kBridge`'s thread-safety
constraints (see BridgeWorker below).

`SamplerController` has no demo mode of its own (always wants a real MIDI
connection), so `ProgramEditorWindow._fetch_demo_sample_audio` loads real
audio from `tests/test_audio.wav` instead of calling
`sampler_controller.receive_samples()` when the env var is set - paced with
real `time.sleep()` + genuine `receive_progress`-shaped updates so the
progress bar is exercised too. Falls back to `_synthesize_demo_sample_audio`
(a deterministic fake tone) if that fixture is ever missing, rather than
failing outright. `WaveformRenderer/` is unrelated copied-in reference
material, not a fixture path.

## `BridgeWorker` - read this before touching `core/program_editor_bridge.py`

`S3kBridge` is unsafe for concurrent calls on one connection. An earlier
version spun up a fresh `QThread` per UI action, with nothing stopping two
from being in flight at once - invisible against `DemoBridge` (serialised
by the GIL), but against real hardware it interleaved SysEx frames on the
wire **and** crashed the app (`QThread::~QThread()` calling `qFatal()`
when a stale thread was still mid-call during GC). Diagnosed from an
actual macOS crash report + the debug log described below.

The fix: **`BridgeWorker`** is a single persistent `QThread` that owns the
bridge for the editor's whole lifetime, processing requests off a queue
strictly one at a time. `ProgramEditorWindow` creates exactly one in
`__init__`; every UI action calls a `submit_*()` method. "Current state of
X" requests (keygroups, keygroup detail, multi parts) coalesce - a new one
drops any not-yet-started one of the same kind. Writes and Program Changes
are never coalesced.

**If you're tempted to go back to thread-per-action, don't.** To add a new
kind of hardware request, add a `submit_*()` + `_handle_*()` pair to
`BridgeWorker`, not a new `QThread` subclass.

Tests exercise `BridgeWorker` synchronously via `process_pending()` (no
real thread); window-level tests use a real thread + `wait_until_idle()`.
Any test that constructs a `ProgramEditorWindow` directly **must** call
`editor._worker.stop(); editor._worker.wait()` in teardown (see the
`editor` fixture in `tests/test_program_editor_window.py`) or you'll hit
`QThread: Destroyed while thread is still running`.

## Update checker (`core/update_checker.py`, `ui/update_helper.py`)

Checks GitHub's `/repos/.../releases/latest` for a newer tag than
`ui/_version.py`'s `APP_VERSION`, unauthenticated. Works unattended only
because `.github/workflows/build.yml` publishes releases with
`draft: true` - `/releases/latest` ignores drafts, so an in-progress build
is invisible to it. If a tagged build never gets manually published, the
checker just keeps reporting the previous release - expected, not a bug.

`UpdateCheckRunner` is shared by `MainWindow`, `ProgramEditorWindow`, and
`AboutDialog` - each owns its own instance and calls `.wait()` on it before
closing. Same root-cause bug class as `BridgeWorker` above (a `QThread`
whose Python wrapper is GC'd while the OS thread still runs).
`ProgramEditorWindow` is most likely to hit this, since
`dashboard.py`'s `open_program_editor()` replaces `self.editor_window` on
every click.

## Transfer Dashboard: "Open Editor" only enabled with a real connection

Added 2026-09-23. `btn_open_editor` and the `&Window > Program Editor`
menu action are only enabled when BOTH a MIDI input and output port are
selected AND the sampler type is "Akai Sampler" (`device_type == "akai"` -
`program_editor_bridge.connect()` needs Akai-specific SysEx extensions
Generic SDS lacks). `_update_open_editor_enabled` computes this (with a
tooltip explaining what's missing) at construction and after the MIDI
Settings dialog closes. The menu action needs no separate wiring -
`register_menu_actions` already polls button `isEnabled()` on a timer and
mirrors it onto the paired menu action.

`btn_open_editor`/`btn_settings` are stacked vertically (a `QVBoxLayout`
inside the top bar's `QHBoxLayout`) at equal fixed width (the wider
button's own `sizeHint()`).

**Gotcha**: a small `setSpacing()` on that column alone did NOT keep the
buttons close - `top_bar` (the outer `QHBoxLayout`) stretches a short
nested child layout to match the row's full height (set by the tall
ASCII logo beside it), dumping unclaimed slack into whichever gap exists
inside the child. Fixed with
`top_bar.setAlignment(settings_editor_column, Qt.AlignmentFlag.AlignVCenter)`
right after `addLayout` - makes `top_bar` use the column's natural
sizeHint instead of stretching it. If a short child layout/widget ever
gets added into a `QHBoxLayout` next to something much taller and its
children look spread apart more than their spacing says, check for this
before assuming the spacing value is wrong.

`tests/test_dashboard.py` covers the enabled-state logic against a real
`TransferDashboard`/`MidiManager`/`SamplerController`, never a full
`ApplicationWindow` (which reads/writes the user's real
`~/.akaisds/config.json`).

## Transfer Dashboard: dropped/opened files get a stable local copy

Added 2026-09-23. Dragging a "cropped" region out of a sample browser
into the queue added the row fine, but Send later
failed with "No such file or directory". Root cause: `create_local_row`
only stored the dropped file's PATH; the actual read happens later, at
send time. Sample editors render the crop to a real OS temp file for the drag
and deletes it shortly after drop - well before Send is clicked. Qt's
cross-platform `QMimeData.urls()` can't distinguish "temporary" from
"permanent" paths (that's an AppKit-level file-promise protocol), so the
row appeared to add fine (it reads fine at drop time) and only the later,
lazy read lost the race.

**Fix**: `core/dropped_files.py` (new, pure filesystem logic, no Qt)
copies every dropped/opened file into a stable, app-owned location right
where `create_local_row` already opens it once for its bit-depth probe -
the queue row is built around THAT copy's path from then on. Applies
uniformly to drag-and-drop and File > Open (both already funnel through
`on_files_dropped` → `create_local_row`).

Copies live under `~/.akaisds/dropped_files/<pid>/`. Three points already
in `dashboard.py` delete a copy when its row stops existing:
`on_file_transferred`, `_remove_local_row`, `clear_local_queue`.
`dropped_files.remove_copy` only ever deletes a path that resolves under
its own base directory.

**Crash-safety**: `dropped_files.sweep_orphaned_sessions()` runs once,
early, in `TransferDashboard.__init__`, removing every OTHER PID-named
subdirectory whose PID isn't a running process (`os.kill(pid, 0)`; any
ambiguous result defaults to "still running" - don't delete). Self-heals
at the next launch, not real-time. `dropped_files.cleanup_session()` is
also wired into `ApplicationWindow.closeEvent` for the normal-exit case.

`tests/test_dropped_files.py` covers the module in isolation
(monkeypatched `_BASE_DIR`/`_session_dir`, never the real
`~/.akaisds/dropped_files`). `tests/test_dashboard.py` covers the
regression end to end: drop a file, delete the original, confirm the
queued copy still reads.

### Follow-up: 32-bit float WAVs read as silence

Found 2026-09-23, once the stable-copy fix let a real sample editor export
survive long enough to actually send - it played as silence. Root cause:
`sds_encoder.read_wav_samples`/`read_wav_channels` called `sf.read(path,
dtype="int16", always_2d=True)` - `soundfile`'s direct float→int16
coercion is not reliable for every source. Confirmed against the user's
actual file: read as `int16` it peaked at 1 (of 32767); the same file
read as `float64` and scaled by hand (`round(x * 32768)`, clipped to
`[-32768, 32767]`) peaked at 22724, matching its real content. Not
sample editor-specific - a `libsndfile` behavior that can affect any 32-bit
float WAV.

Fixed by `_read_float_scaled_to_int16` (new; both functions funnel
through it) - always reads as float (reliable, since `libsndfile`
normalizes any subtype to `[-1.0, 1.0]`) and scales to int16 by hand.
`test_read_wav_samples_32bit_float_scales_to_real_int16_values` in
`tests/test_sds_encoder.py` now asserts actual scaled values, not just
"didn't crash" - the old float test only checked length/rate, which is
why this shipped unnoticed.

## Debug logging for real-hardware issues

`core/debug_log.py` sets up a rotating log at `~/.akaisds/editor_debug.log`.
`LoggingBridge` (in `program_editor_bridge.py`) wraps whatever bridge
`connect()` returns and logs every call's START/END/FAILED with thread
identity, timing, and a traceback on failure. If a user reports frequent
errors or a crash against real hardware, ask for this log (and the macOS
`.crash` report if it aborted) before guessing. Lines are long (full
`repr()` of `Parameter` objects) - grep/tail rather than reading whole.

## Program Editor UI: section cards, scroll areas, and the busy indicator

Program/Keygroup tabs are built from `_build_section_card(title,
*row_layouts)` and `_build_scroll_area(page)` (wraps a whole tab so the
window needn't be tall enough to show every card unscrolled). Add new
controls to the most relevant existing card rather than a new bare row.
This page's `setMinimumSize(...)` was tuned by hand, not a round number:

- **Height** doesn't need to fit everything - past the minimum, a tab
  scrolls rather than compressing/overlapping cards.
- **Width** is the real constraint - some cards are paired side by side
  (e.g. Range+Filter on Keygroup tab); below a certain width the pair
  stops fitting and an unwanted horizontal scrollbar appears. If you
  widen a card or add a pair, grow the window until
  `scroll_area.horizontalScrollBar().maximum()` hits zero on both tabs
  rather than guessing a new minimum.
- **Stretch ratios between paired cards should be measured via
  `sizeHint()`, not guessed** - Range+Filter briefly shipped 1:2 on the
  assumption Filter needs more width; wrong (Filter's content is actually
  narrower, 273px vs 299px). Every paired row is 1:1 now.
- `_build_section_card`'s layout ends with `addStretch()` - without it, a
  card shorter than its row partner gets slack space split before the
  header too (reads as vertically centered instead of top-aligned).

`BridgeWorker.busy_changed` drives the indeterminate progress bar next to
Refresh - `True` from the moment any job queues while nothing else is
pending, `False` once the queue drains, so a burst of jobs reads as one
span. `ProgramEditorWindow` debounces this through two one-shot
`QTimer`s (`_busy_show_timer`/`_busy_hide_timer`) because a **fast**
bridge (`DemoBridge`/test fakes) can drain the queue to empty *between*
two back-to-back `submit_*()` calls, causing real flicker without the
hide-timer's grace period. Real hardware essentially can't hit this race
(submitting a batch takes microseconds). Don't remove either timer without
re-reading the comment; see
`test_a_new_busy_true_during_the_hide_grace_period_cancels_the_hide`.

Widget visibility assertions in tests use `.isHidden()`, not
`.isVisible()` - the `editor` fixture never calls `.show()`, and
`.isVisible()` is unconditionally `False` for a never-shown top-level
window regardless of `setVisible()`.

## Envelope graphs (`ui/envelope_graph.py`): each stage's width must be independent

`_adsr_points()`/`_env2_points()` used to give each stage a width
*proportional to its share of the live total* - a real bug: turning ONE
knob visibly resized the OTHER stages even though their values never
changed. No synth's ADSR display works that way.

Fixed with a **fixed** width budget per stage - `(1 -
SUSTAIN_HOLD_FRACTION) / 3` per ADSR stage, `w / 4` per ENV2 stage -
scaled only by `own_value / 99` within that budget. Trade-off: the
envelope no longer necessarily reaches the full widget width - correct,
not a regression. **Do not re-normalize one stage's width against the
others' current values** to "simplify" this - that reintroduces the bug.
See `test_adsr_stage_width_is_a_fixed_share_not_a_relative_one` /
`test_env2_stage_width_is_a_fixed_share_not_a_relative_one`.

## LFO2 and the modulation matrix (Program/Keygroup tabs)

`LFO2` is real and independent but hardwired to modulate **Pan**
(auto-pan): its rate/depth/delay/shape are `PANRAT`/`PANDEP`/`PANDEL`/
`LFO2WAVE`, stored in `program.pan` despite being LFO2's own controls.
`LFO2WAVE` only documents 3 shapes (Triangle/Sawtooth/Square) - unlike
`LFO1WAVE`'s measured 4th ("Random", found by reading the pitch track
LFO1 drives); LFO2 drives pan, no equivalent measurement exists, so
`lfo2_shape_combo` only offers 3 - don't assume a 4th without measuring.

LFO1 has its own sync/desync toggle (`lfo1_sync_combo`, field `DESYNC`:
`{0: "OFF", 1: "ON"}` - the field is phrased as its own negation).
`lfo1_sync_combo` is deliberately labeled the positive way ("Sync":
On/Off), so its item order is inverted from `DESYNC`'s own text, but the
raw byte written is NOT inverted (index 0 "On" = `DESYNC=0`, index 1
"Off" = `DESYNC=1`) - same "combo index is the raw byte" convention as
everything else. See the construction comment before reordering items.
LFO2 has no equivalent sync field (it does have its own `LFO2TRIG`
retrigger boolean - a different thing, see the hardware-override section
above).

The **Modulation** cards (one per tab) expose the assignable matrix
(`MODS*`/`MODV*`): up to 3 (source, amount) slots per destination (Pan,
Loudness, Filter Frequency), 1 slot each for LFO1's Rate/Depth/Delay and
for Pitch. The 14-source enum (`s3k.params.MOD_SOURCES`) is hand-mirrored
as `_MOD_SOURCE_LABELS`, **with envelope 3 (value 14) deliberately left
out** - env3 works on base hardware (only the *second filter* target
needs the IB304F board), but this app has no Envelope 3 editor page yet,
so offering it as a source would be confusing.
`test_mod_source_labels_match_s3k_params_minus_env3` guards this.

**Why the matrix splits across both tabs**: every destination's *source*
choice lives in the `program` region (one shared choice per program), so
every source dropdown lives on the Program tab. Most *amounts* are also
program-level and sit next to their source there. But `MODVFILT1..3`,
`MODVPITCH`, `MODVAMP3` (Loudness slot 3's amount only) and `L_PTCH` (a
separate always-on LFO1→pitch depth, not part of the 3-slot matrix) are
in the `keygroup` region - genuinely per-keygroup - so those rows show
only source (+ footnote) on the Program tab, and only the amount knob on
the Keygroup tab. This is what the hardware actually stores, not a UI
choice - don't merge it into one card without re-checking each field's
`region` in `s3k.params`.

Slots are built through `_build_mod_slot`/`_build_mod_source_only_column`/
`_build_mod_amount_only_column`, placed in a `QGridLayout` via
`_build_mod_matrix_row`/`_build_mod_matrix_amount_row`. Each card has one
"Slot 1/2/3" + "Source"/"Amount" header (`_build_mod_matrix_header`/
`_build_mod_matrix_amount_header`) rather than a repeated label per
control - the grid's column alignment carries that meaning now; don't
reintroduce per-control labels without removing the header, or both will
say it. The amount knobs (`_build_mod_amount_knob`) are enabled right
where they're built rather than in `__init__`'s later "enable knobs"
block - deliberate; don't assume every knob not listed there is unwired,
check whether it came from one of these helpers first.

This card's rows are what pushed `setMinimumSize` from `1040` to `1080`
(re-measured the same `horizontalScrollBar().maximum()` way described
above).

## PRGNUM and Program Change (Multis tab)

No working SysEx write assigns a program to a multi part - `PRNAME` in
the multipart region is read-only (documented in `s3k` itself). The only
real mechanism is a raw MIDI **Program Change** on the part's channel,
addressed by the target program's own `PRGNUM` (not its list position).

Freshly-loaded programs commonly all have `PRGNUM == 0`, which makes
every Program Change target whichever program the hardware associates
with 0. `BridgeWorker` calls `renumber_programs()` (gives every resident
program a distinct number in list order) once before the first Program
Change, and again whenever the program list reloads. If "assigning a
program always picks the wrong one" recurs, check `PRGNUM` collisions
first.

There's no hardware concept of "no program assigned" for a part - the
blank `"-"` combo entry is a UI-only no-op placeholder, not a bug.

**The 16 part combos hold their own copy of every program's name**
(populated in `_on_programs_loaded`, a full reload) - they don't
automatically follow changes elsewhere. Renaming a program used to leave
stale names in all 16 combos until the next Refresh; fixed by
`_commit_program_name`/`_on_program_name_typed` calling
`_update_multi_program_combo_names(program_index, name)` explicitly. Any
new program-identity field the Multis tab mirrors needs the same explicit
push - `_on_programs_loaded` alone only catches a full reload.

## Note names: S3000XL octave convention, not general MIDI

`core/midi_notes.midi_note_to_name()` calls note 60 (middle C) **"C3"**,
matching the S3000XL's own panel - **not** general MIDI's C4. Fixed once
already (editor showed every note an octave too high).
`ui/note_spinbox.py`'s `_note_name_to_midi()` is the exact inverse and
must stay in sync (see `tests/test_note_spinbox.py`'s round-trip test).
Don't "correct" this back to general-MIDI convention; C3 is right here.

## Samples tab: loop points, and the one place this window deliberately blocks

`s3k` has no bulk sample-audio transfer - no RSPACK/ASPACK, only header
reads. So the Samples tab's waveform gets header fields (loop points,
start/end, rate) through the normal `BridgeWorker` connection, but gets
actual audio from `main_window.sampler_controller` - the Transfer
Dashboard's own SDS receiver, reached via the `main_window` reference
`ProgramEditorWindow` already holds. That's a **second, separate MIDI
connection** to the same port, open concurrently with this window's own
bridge - not new, but worth knowing if hardware actions ever seem to
interleave strangely.

**`LOOPAT1` is the loop's END, not its start.** `LLNGTH1` measures
*backwards* from it - the loop region is `[LOOPAT1 - LLNGTH1, LOOPAT1]`.
Confirmed in `s3ked`'s own `docs/RESOLUTION_NOTES.md` (§136 upstream, not
in the installed wheel): the manual says so directly, an independent
implementation (`ConvertWithMoss`) agrees, and getting it backwards is
exactly the bug that produced silent/degraded loops in that project's own
history. **`s3k.params`' own `notes=` for `LOOPAT1` does NOT mention
this** - reading only that file, the natural (wrong) assumption is that
`LOOPAT1` is where the loop starts. `loop_start` is always *derived*
(`LOOPAT1 - LLNGTH1`) here, never read/written directly.

**`LLNGTH1`'s raw value is 32.16 fixed point, not a plain frame count** -
the real field is `frames * 65536` (confirmed against `s3000editor`'s own
`writeFixed32_16` and independently against `RESOLUTION_NOTES.md`'s akaiutil
cross-check). Get this wrong and loops still "work" in-app (read/write
both naively wrong the same direction) but the hardware reads a loop
~65536x too short. See `_LOOP_LENGTH_FIXED_POINT_SCALE`.

**`STUNO`/`PRGNUM` raw-vs-display quirks, confirmed on hardware:**

- `STUNO` (sample tune) is signed 1/256-semitone, same scale as `VTUNO` -
  but `s3k.params` declares it unsigned `0..65535`, so `encode_field`
  rejects a negative raw value. `_sample_tune_offset_to_semitones`/
  `_semitones_to_sample_tune_offset` manually sign-extend on read and wrap
  to unsigned two's-complement on write. Range is ±50.00st.
- `PRGNUM`'s raw value is off by one from the panel's own numbering (raw
  8 shows as program 9) - `program_number_spinbox` displays `raw + 1`,
  writes `displayed - 1`.

Loading a sample's audio is a real SDS dump, legitimately minutes for a
large sample - the UI is meant to freeze while it happens, not work
around it. The mechanism, `ProgramEditorWindow._wait_for_any_signal`, is
the **one place in this window** that blocks the calling thread on a
signal instead of connecting once and letting results arrive async
(`BridgeWorker`'s whole design is the opposite). Don't reach for it
elsewhere without a comparably good reason.

### Editing: markers, spinboxes, zoom - one write path for both input methods

Loop points are editable by dragging a marker on `WaveformView`'s canvas
(Shift = fine mode, slower relative-delta drag with a float accumulator
`_drag_value` carrying sub-frame remainder) or via the four marker
spinboxes. **Both paths go through the same two helpers**,
`_schedule_marker_write`/`_flush_marker_write` - which field(s) a marker
maps to (`SSTART`/`SMPEND` 1:1; either loop edge writes `LOOPAT1`+
`LLNGTH1` together) lives in exactly one place. Wire a third input method
through these two rather than re-deriving the pairing.

`WaveformView` also has horizontal zoom/pan - `x_for_frame`/`frame_for_x`
take a view window, not the whole frame count. `clamp_marker` stays
whole-sample-bounded regardless of zoom. Ctrl+wheel zooms centered on the
cursor. A real macOS trackpad pinch arrives as its own event
(`QEvent.Type.NativeGesture`), not Ctrl+wheel - handled via
`WaveformView.event()` → `_handle_pinch_zoom` (no dedicated Qt virtual for
this gesture).

**Panning**: a trackpad reports pan motion through `angleDelta().x()` or
`.y()` depending on swipe direction, and naive handling either drops an
axis or picks the wrong one momentarily before "bouncing" onto the right
one. Went through several designs (preferring the larger-magnitude axis
per-event, then locking the winning axis once per gesture via
`QWheelEvent.phase()`) that each fixed one symptom but left another (slow
swipes bouncing from hand tremor, or a tremor-dominated first event
locking the wrong axis for a whole gesture). **What shipped**: stop
inferring intent from an ambiguous signal at all - `angle.x() != 0`
always wins for pan, unconditionally; nothing produces nonzero `.x()`
except a genuine horizontal swipe or a mouse's own horizontal wheel, so
there's nothing to disambiguate. A vertical-only mouse wheel has no `.x()`
at all, so Shift+scroll explicitly repurposes its `.y()` for pan (the
usual "hold Shift to scroll sideways" convention) - unmodified vertical
scroll does nothing on purpose, since repurposing it for pan (nothing to
scroll vertically) was the root of every earlier version of this bug.
Zoom (Ctrl+scroll) keeps a simple `x if nonzero else y`, since zoom has no
left/right ambiguity.

The Zoom +/-/Fit buttons and the horizontal `QScrollBar` are the
discoverable/no-modifier equivalents, synced via `WaveformView.view_changed`
↔ the scrollbar's `valueChanged`, both `blockSignals`-guarded against
bouncing. The scrollbar sits in a fixed-height container even when hidden
(`setVisible(False)` alone collapses it to zero height, which shoved the
marker spinboxes up/down every time zoom crossed the scrolling threshold).

### Editing loop points without audio - header and audio load independently

Loading full waveform audio can take minutes over SDS, but loop points
only need the sample's *header* - a handful of fast `get_parameter` reads
over the same fast `BridgeWorker` connection every other tab uses.
`WaveformView.set_header(...)` shows/makes the four markers draggable
from header data alone, no envelope trace (`has_header()` true,
`has_waveform()` false until real audio arrives). `_frame_count > 0`
gates dragging/zoom/pan now, not `_samples is not None` (`_samples` only
gates whether there's an envelope to paint). **Get this distinction wrong
in a new method and editing silently blocks without audio again** - this
happened once in `_on_marker_spinbox_changed` (gated on `has_waveform()`,
so typing before audio loaded did nothing).

`ProgramEditorWindow._on_sample_selected` fires an automatic async
`submit_sample_detail()` on selection if nothing's cached - persistently
connected in `__init__`, not through `_wait_for_any_signal` (reserved for
the actual audio fetch). `_sample_waveform_cache` entries can be
header-only (`"samples": None`) or full; both always carry
`"frame_count"`. **If the user edits a marker with only the header loaded
and then loads audio, `_load_sample_waveform` must reuse the cached
markers, not re-derive fresh ones** - re-deriving silently discards the
edit. `_markers_from_header()` is the shared helper both fetch paths use
so they compute identically.

`WaveformView.set_waveform` preserves zoom/pan when called for the
already-showing sample (rather than resetting to fully zoomed out) - it
still resets for a genuinely different sample (always goes through
`clear()`/`set_header()` first).

### Diagnosing a load that silently does nothing

A user hit intermittent "double-click loads nothing" that no scripted
repro could reproduce. Reading `~/.akaisds/editor_debug.log` from their
actual session showed zero `FAILED` entries and no overlapping calls -
the bridge layer itself was never at fault, meaning the problem was
upstream of any bridge call. `WaveformView.mouseDoubleClickEvent` and
`_load_sample_waveform` now log at every entry/early-return/fetch-result
point for exactly this reason.

**Follow-up, same mechanism, later session**: the log showed
`_load_sample_waveform` being entered fine, but the `sample_detail` job
never produced even a START log line. Root cause: `BridgeWorker.run()`'s
dispatch call sat outside any try/except - an exception escaping
`_dispatch()` (from a bug in a handler, not the bridge calls a handler's
own try/except already guards) silently killed the OS thread for the
rest of the app's life. The GUI stayed fully responsive throughout
(`submit_*()` keeps appending to the queue), and nothing ever popped from
it again. `BridgeWorker._safe_dispatch` wraps `_dispatch()` in a
try/except that logs and lets the loop continue instead of dying. See
`test_worker_survives_a_job_whose_dispatch_raises_outside_its_own_handler`.
If you see "double-click recognized, GUI responsive, zero bridge-layer
log lines" again, check this log line first.

### Progressive waveform loading during a live SDS dump

As of 2026-09-22 the envelope fills in live, left-to-right, in step with
an in-progress transfer, instead of appearing all at once when it
finishes. `WaveformView.begin_live_capture()` switches from header-only
to an empty growing list right before a fetch starts;
`append_live_samples(chunk)` extends it and repaints each call.
`_rebuild_envelope` clips to `min(view_start + view_length,
len(self._samples))` and sizes the envelope's pixel width proportionally
to how much of the current view is actually loaded, so a half-loaded
prefix occupies only its own left fraction rather than stretching to fill
the canvas.

Two producers feed `append_live_samples`: real hardware via
`SamplerController.sample_chunk_received` (decodes each accepted SDS
packet immediately, scaled via `sds_encoder.scale_sample_to_16bit`); demo
mode via `_fetch_demo_sample_audio` calling it with newly-revealed slices
on the same ~200ms tick its progress bar already uses, so evaluating this
without hardware shows the real thing.

**Follow-up bug**: the envelope visibly finished loading well before the
progress bar did, then the whole waveform snapped/rescaled when real
audio landed. Root cause: `_markers_from_header`'s demo branch used an
arbitrary placeholder (`values["SLNGTH"] or 20000`) for `frame_count`
against `DemoBridge`'s all-zero header - harmless before progressive
loading needed it as the live-capture *total*, but `tests/test_audio.wav`
is ~31000 frames, so the envelope read "100% loaded" at ~64% of the real
transfer, then jumped when the real 31000-frame total replaced it mid-
view. Fixed by `_demo_sample_frame_count(sample_index)` (predicts the
real length via a cheap `wave.open().getnframes()` header peek, or
`_synthesized_demo_frame_count` if the fixture is missing) replacing the
placeholder. Demo-mode-only: on real hardware both values trace back to
the same physical sample's `SLNGTH`, so they can't disagree.

### Sample loop type, root note, and rename/delete

Added 2026-09-22. Three additions, all mirroring existing patterns:

- **Loop type** (`sample_loop_type_combo`) writes `SPTYPE` (sample
  region) - the SAMPLE's own type, distinct from `ZPLAY` (the
  per-keygroup-zone override, on the Keygroup tab).
  `_SAMPLE_PLAYBACK_TYPE_OPTIONS` reuses `_LOOP_TYPE_OPTIONS`'s own
  tooltips (indices 1-4) rather than retyping them, since s3k.params'
  SPTYPE labels line up 1:1 with ZPLAY's. `test_sample_playback_type_options_match_s3k_params_sptype`
  guards this.
- **Root note** (`sample_root_note_spinbox`) writes `SPITCH` via
  `NoteSpinBox`, same C3-convention as elsewhere, but ranged `21..127`
  (s3k.params' own declared range) - `setRange(21, 127)` must be called
  explicitly, `NoteSpinBox()` otherwise defaults to `0..127`.
- **Rename/delete** on `sample_list_widget` mirrors `program_list`'s
  `ActionsContextMenu`/`QAction` shape. `_DELETE_SHORTCUTS` is now a
  shared module-level constant. Rename writes `SHNAME` and pushes the new
  name into every zone's sample-choice combo via
  `_update_zone_sample_combo_names` (same stale-copy problem class
  `_update_multi_program_combo_names` already solves for programs).
  Delete submits `DELS` via `BridgeWorker.submit_delete_sample`, then does
  a full `submit_sample_list()` reload (indices shift after delete).
  **Unlike program delete**, there's no "last one is silently ignored"
  restriction for `DELS` in `s3k`/`s3ked` - no `count() > 1` guard here.
  If real hardware is ever found to ignore deleting the last sample too,
  add the guard back.

Both `SPTYPE`/`SPITCH` are in `_SAMPLE_DETAIL_FIELDS`, arriving in the
normal header payload. `DemoBridge`'s all-zero header means both read back
benign defaults (0 → "Normal looping"; 0 → clamped up to 21 by
`NoteSpinBox`'s range) - neither needed the placeholder treatment
`_demo_sample_frame_count` needed, since neither collapses markers the
way `(0,0,0,0)` does.

### Trim/Reverse, and duplicate/timestretch/resample (capability audit)

Added 2026-09-22. Checked first whether `s3k`/`s3ked` already provide
sample/program/keygroup duplication, hardware time-stretch, or hardware
resampling - **none exist** (grepped both packages; only structural
messages are Delete*, no Copy/Duplicate/Clone anywhere). `SSRATE` is a
plain metadata byte - writing it changes the declared rate, not the
audio. This repo's own `sds_encoder.resample_to_target_rate` is the only
real resampling in the stack, client-side; time-stretch would need new
DSP work.

**Trim**/**Reverse** needed a way to get modified audio back onto the
hardware - something neither `s3k` nor `s3ked`'s own TUI app has ever
done (it's a pure parameter editor, zero sample-audio capability). The
only mechanism is this repo's own `SamplerController.send_file_queue`
plus `BridgeWorker.submit_delete_sample`.

The math (`core/sample_editing.py`, pure, no Qt/MIDI): `trim_samples`/
`reverse_samples` take/return the same four markers `WaveformView.markers()`
uses. Trim keeps `samples[start:end+1]` and re-bases loop markers into the
shorter buffer (never clips into an active loop - `clamp_marker`
guarantees `start <= loop_start <= loop_end <= end` on entry). Reverse
mirrors every marker around `frame_count - 1 - i`; loop length is
invariant under this.

**Why send-then-delete under a temp name** (`_perform_sample_edit_real`):
two failure modes to design around:

- Deleting the original before confirming the replacement sent would risk
  losing the sample if the send then failed - so replacement sends FIRST,
  original deletes only once confirmed resident.
- Sending under the ORIGINAL's own name risks a subtler failure: verified
  via `s3k/analysis.py`'s own docstrings that **keygroup zones resolve a
  sample by NAME, live**, and the hardware enforces no uniqueness on that
  name (measured: renaming one resident sample to another's name left
  both unresolvably ambiguous). Two samples briefly sharing a name during
  the send/delete window would make every zone using that name ambiguous.
  `_sample_edit_temp_name` sidesteps this by appending a fixed `"-TMP"`
  suffix, never touching the original's name until the original is gone.

Sequence: send under `<name>-TMP` → confirm resident → delete original →
reload, find `<name>-TMP`'s new index by name (indices shift after
delete) → `SHNAME`-only rename back → reload once more, select result.
Every step is a previously-tested primitive; this exact sequence hasn't
been exercised against real hardware - watch the first real run closely.
Demo mode (`_perform_sample_edit_demo`) bypasses all of this, mutating
the cache/`WaveformView` directly, paced the same way
`_fetch_demo_sample_audio` is.

**Real bug found while building this**: `_wait_for_any_signal` used to
`submit_*()` THEN connect listeners - every signal it waits on fires from
a background thread/async callback, and nothing guaranteed the GUI thread
reached `connect()` first. Unhittable against real hardware (real SysEx
latency), but **reliably lost every run** against `FakeBridge` in tests
(plain dict ops, no latency) - a test hung for a full 20s timeout despite
the delete having already succeeded. Fixed: `_wait_for_any_signal` now
takes a required `start` callable, invoked only after every listener is
connected; every caller moved its `submit_*()`/`send_*()`/`receive_*()`
call into `start=`. A `start()` returning exactly `False` (not `None`)
makes it skip the wait outright (used by `send_file_queue` declining to
start) rather than hanging until timeout (or forever, for the send step's
`timeout_ms=None`). Any new blocking wait added to this page must follow
this ordering.

### Duplicate Sample: adds a new sample, reuses the send pipeline above

Added 2026-09-23, initially as a right-click context menu item to match
Duplicate Program/Duplicate Keygroup - moved to a button on the same row
as Trim/Reverse/Fade/Normalise the same day, per direct user follow-up
(a disabled context menu item reads as "nothing happened" when clicked
before audio is loaded; a visibly-disabled button next to the other four
sample-edit buttons doesn't). Right-aligned on `sample_edit_row` (its own
`addStretch()` before it, not grouped with the destructive four -
Duplicate never touches the source's own audio, it only ever ADDS a new
sample, so it isn't "cannot be undone" in the same sense they are).

The mechanism is a different one from Duplicate Program/Duplicate
Keygroup, though: those two build a PDATA/KDATA header directly (a
"create" primitive `DemoBridge` doesn't have either, so both are
`not demo_mode`-gated); samples have no header-only duplicate (no bulk
sample-audio transfer of any kind exists in `s3k` - see the capability
audit above), so `_perform_duplicate_sample_real` sends the already-loaded
audio under a brand new, collision-checked name via
`SamplerController.send_file_queue` - the same mechanism Trim/Reverse/
Fade/Normalise use, not a new one. **Simpler than their replace-in-place
dance**: nothing is deleted or renamed, so there's no temp-name step -
`new_name` is confirmed distinct from every resident sample up front (same
hazard as the temp-name comment above: two samples sharing a name makes
keygroup zone resolution ambiguous), so it's safe to send under directly.
After the send confirms resident, the new sample's index is looked up by
name (same convention, not assumed) and every header field
`send_file_queue` doesn't set - `SPTYPE`/`SPITCH`/`SHLTO`/`STUNO` and the
four loop/start/end fields - is copied across from the source, since
otherwise "duplicate" would silently mean "audio copy with default
metadata," not a real duplicate. **Demo-mode-gated the same way as
Duplicate Program/Keygroup**, just through `_set_sample_edit_buttons_enabled`
rather than `_update_list_context_actions_enabled` - this button needs
`has_waveform()` too, which only that method already reacts to.

### Sample tune redisplaying wrong after a reselect: `entry["stuno"]` wasn't consistently raw

Found while building Duplicate Sample above, and **confirmed on real
hardware the same day**: `entry["stuno"]` in `_sample_waveform_cache` was
NOT consistently raw. `_on_sample_detail_loaded` stores the raw `STUNO`
byte; `_on_sample_tune_changed` (a live tune edit) overwrote the same key
with the spinbox's SEMITONES value instead. Reselecting a previously-
tune-edited sample (`_on_sample_selected`'s cache-restore branch) fed that
semitones value back into `_update_sample_meta_controls`, which
unconditionally treats it as raw and re-converts it through
`_sample_tune_offset_to_semitones` a second time. Reproduced on hardware
exactly as predicted: set Tune to `+40.00st`, click a different sample
then back, and it redisplayed as `+0.16st` - the actual hardware write was
always correct (`_schedule_write` never used the cache), this was a
display-only bug. `SPTYPE`/`SPITCH`/`SHLTO` never had this problem (their
live-edit handlers already stored the same raw representation the load
handler does - only `STUNO` has an actual unit conversion in between).

**Fixed** by making `_on_sample_tune_changed` store the raw encoded value
(`_semitones_to_sample_tune_offset(value)`) into `entry["stuno"]` instead
of the bare spinbox value - same representation the header-load path
already used, so the cache key means one thing everywhere now.
`_perform_duplicate_sample_real`'s own `STUNO` read went back to
`entry["stuno"]` directly (matching `SPTYPE`/`SPITCH`/`SHLTO`) now that
it's trustworthy - no longer needs its own special-cased live-widget
workaround. `test_sample_tune_survives_a_reselect_after_a_live_edit`
reproduces the exact hardware repro (edit -> reselect away -> reselect
back -> still correct) as a regression test;
`test_changing_sample_tune_writes_stuno_in_raw_units` was also updated -
it used to assert the OLD (buggy) semitones-in-the-cache behavior as if
it were correct.

### Loop-point markers push each other instead of stopping dead

Added 2026-09-22. `WaveformView`'s old `clamp_marker` stopped a dragged
marker dead at its neighbour. Now `push_marker`: moving a marker far
enough pushes the neighbour along instead, cascading (Newton's cradle).
`start <= loop_start <= loop_end <= end` still always holds, just
enforced by shoving rather than refusing to cross. `clamp_marker` is gone
entirely (no remaining callers once drag + `set_marker` both switched).

**The write side had to change with it**: a push can move up to all four
markers from one action, so `_schedule_marker_write` now takes the FULL
marker dict from both before and after the edit and writes exactly the
field(s) whose value actually changed (`SSTART` if start moved, `SMPEND`
if end moved, `LOOPAT1`+`LLNGTH1` together if either loop edge moved).
Getting this wrong silently desyncs the hardware from what's on screen -
e.g. a push that visibly moves `loop_end` but only writes `SMPEND` leaves
the sampler looping at its old position while the UI shows it moved.
`_on_waveform_marker_committed` (drag release) gets "before" from the
cache entry (only updated on commit, not on live per-move
`markers_changed`); `_on_marker_spinbox_changed` gets "before" from
`WaveformView.markers()` read immediately before `set_marker`.
`_flush_marker_write` dropped its `which` parameter - it unconditionally
flushes all four marker debounce keys now (`_flush_write` no-ops for
nothing pending).

### Click-to-cycle for markers stacked on the same frame

Added 2026-09-22, follow-up to push. Once several markers land on the
same frame (easy with push), they sit at the same pixel column - and the
first push version's `_marker_near(x)` always broke ties in
`_MARKER_ORDER`'s order (effectively always "start"), so only the
frontmost marker could ever be grabbed again.

Fixed the usual way overlapping-selection is solved: repeated clicks
cycle through every marker in the stack. `_marker_near` is replaced by
`_markers_within_hit_radius(x)` (every visible marker within
`_HIT_RADIUS_PX`, closest first, stable-sorted). `mousePressEvent`
remembers the candidate list + a rotating index - a press whose candidate
list is *exactly* the same as last press's (compared by actual sorted
name list, not just "near the same pixel") advances to the next
candidate; anything else resets to closest. Reset explicitly in
`clear()`/`set_header()` too. Marker spinboxes need none of this - each
is directly clickable regardless of canvas position, an unambiguous
fallback for separating a stack.

### Waveform trace tinted by region

Added 2026-09-22. `_waveform_zone_color(frame, palette)` picks a color
per envelope column: `text_disabled` (greyed) outside `[start, end]`
(resident but never plays), `keygroup_color_3` (the same teal the loop
markers themselves use) inside `[loop_start, loop_end]` inclusive, plain
`accent` elsewhere within `[start, end]`. Each column's frame comes from
`frame_for_x` - the same mapping `x_for_frame` (marker positioning)
inverts, so boundaries line up with the markers pixel for pixel. Color
can be briefly, harmlessly approximate while a sample is still
progressively loading (`frame_for_x` uses the full canvas width
regardless of how much has loaded) - accepted, not a bug.

### Zero-crossing line, and connected samples at high zoom

Added 2026-09-22, screenshot-driven: zoomed in far enough that
`build_envelope`'s one-min/max-pair-per-column approach produced
disconnected dashes, with nothing marking zero amplitude.

`_draw_zero_crossing_line` is a plain horizontal guide at `mid_y` in
`palette["border"]`, drawn even in header-only mode.

`_draw_connected_samples` fixes the dashes: the per-column min/max bar
degenerates once zoomed in past 1:1 (each column maps to ≤1 sample, so
min == max, every bar collapses to a dot). `paintEvent` switches to an
actual point-to-point polyline whenever `view_length <= self.width()` -
cheap because that condition bounds sample count by canvas width, same
order of work as the bar loop it replaces. Below that threshold, the
existing bar rendering is unchanged; both paths share
`_waveform_zone_color` per segment.

`test_paint_does_not_crash_at_high_zoom_in_connected_sample_mode`/
`test_paint_does_not_crash_at_high_zoom_with_only_one_sample_loaded` in
`tests/test_waveform_view.py` are crash-guards for this path, especially
the progressive-loading interaction where `self._samples` is still
filling in and shorter than the zoomed-in view.

### Zoom ceiling: a flat 500x could never reach single-sample resolution

Added 2026-09-22: dragging a marker "as zoomed in as possible" still
wasn't sample-accurate. Root cause: `_MAX_ZOOM = 500.0` was a flat
ceiling, but `_view_length()` is `round(frame_count / zoom)` - for any
sample bigger than roughly `500 * canvas_width_px` frames, max zoom still
left more samples visible than canvas pixels, so every pixel of mouse
movement skipped several frames permanently on larger samples (real
S3000-series samples can be several hundred thousand to a few million
frames).

Fixed by `WaveformView._max_zoom()` returning `max(_MIN_ZOOM,
float(self._frame_count))` - the exact zoom at which `_view_length()`
bottoms out at 1 - so single-sample resolution is always reachable
regardless of sample size; `set_zoom` clamps against this instead of the
old flat constant. This also means zoom now naturally reaches
`_draw_connected_samples`' rendering mode for every sample, previously
geometrically unreachable for big samples. No change to `_ZOOM_STEP`.
See `test_max_zoom_reaches_single_sample_resolution_for_a_huge_sample`.

### Loop type gating: markers/spinboxes/tint disappear, don't just grey out

Added 2026-09-23. When `SPTYPE` is "No looping"/"One-shot"
(`_SPTYPE_VALUES_WITHOUT_LOOP = {2, 3}`), the loop has no meaning:
`_set_loop_markers_enabled(enabled)` greys the loop spinboxes + legend
swatches and calls `WaveformView.set_loop_enabled(enabled)`, which makes
`loop_start`/`loop_end` **not draw at all** (paintEvent skips them
outright) and excludes them from `_markers_within_hit_radius`. The
loop-region teal tint reverts to plain accent. Getting the disabled
*look* right needed its own fix: `style.qss.template`'s
`QComboBox, QSpinBox, QDoubleSpinBox` rule hardcodes colors, silently
defeating Fusion's automatic disabled-greying unless `:disabled` is
spelled out too - added alongside the existing `QPushButton:disabled`/
`QLineEdit:disabled` rules, app-wide.

**Dragging Start/End while the loop is off freezes `loop_start`/
`loop_end` instead of pushing them.** `WaveformView._push_marker` wraps
`push_marker`: when the dragged/typed marker is `start`/`end` and
`self._loop_enabled` is `False`, it pushes against the reduced order
`("start", "end")` only, leaving the loop markers untouched even if the
moved marker crosses them - deliberately breaking `push_marker`'s own
invariant while the loop is off, since loop points shouldn't get dragged
by an unrelated trim while invisible. Re-enabling
(`set_loop_enabled(True)`) calls `_reconcile_loop_into_range` once
(`min(max(x, start), end)` per edge, monotonic) and emits
`markers_changed`; `_set_loop_markers_enabled` diffs old vs. new around
that call and schedules the `LOOPAT1`/`LLNGTH1` write only if something
actually moved.

**This reintroduced the exact bug class Trim/Reverse assumed couldn't
happen** (their docstrings state loop markers are always within `[start,
end]` on entry, which the freeze above can violate while the loop is
off). Fixed by `WaveformView.markers_with_loop_in_range()` - a
*non-mutating* clamped view - `_perform_sample_edit` reads from this
instead of `.markers()` directly, so Trim/Reverse/Fade always get sane
input without permanently un-freezing the actual stored loop points.

### Sample-load progress: removed, then partly brought back

The full-width `sample_load_progress` bar under Trim/Reverse was removed
2026-09-23 for ordinary sample *loading* - redundant with the waveform's
own progressive fill and the status bar (which already showed frame
counts). `_on_sample_receive_progress` now takes a `label` and writes
`"{label} - {percentage}% ({current}/{total} frames)"` to the status bar
instead of driving a bar.

**Trim/Reverse/Fade got a small bar back**, per follow-up request: unlike
loading, sending already-loaded audio back out has no progressively-
filling waveform of its own - the audio sits static until send/delete/
rename finishes. `sample_edit_progress` (140px, hidden by default) lives
next to the Zoom controls, driven by `_on_sample_edit_progress` (which
also calls `_on_sample_receive_progress` for the shared status-bar text)

- ordinary loading never calls this, so the bar stays hidden for a plain
double-click load.

### Waveform bar rendering: bridging gaps between low-variance columns

Added 2026-09-23: a decaying/quiet tail rendered as disconnected dashes.
`paintEvent`'s min/max bar mode (`view_length > width`) draws only a
single vertical line per column with nothing connecting columns - a
column whose samples all sit in a narrow, low-variance range (a quiet
passage) can leave real daylight to the next column's bar even though
the audio is continuous.

Fixed by `_bridge_envelope_gaps` (plain function, `build_envelope` always
runs output through it): walks the envelope once, and whenever a column's
range doesn't overlap the *previous* column's, nudges the one edge nearer
the gap just far enough to touch it. Operates on amplitude `(lo, hi)`
pairs, not pixels (`paintEvent`'s y-mapping is strictly monotonic linear,
so amplitude-space gaps are y-space gaps). Never shrinks a genuine
peak/trough, no-op for anything already overlapping.
`_draw_connected_samples` (the other rendering path) is unaffected since
it never reads `self._envelope`.

### Fade In/Out: fades the lead-in/lead-out AROUND [start, end], not inside it

Added 2026-09-23. One "Fade In/Out" button (next to Trim/Reverse) applies
both directions in one pass. `core/sample_editing.py`'s
`fade_in_out_samples(samples, start, loop_start, loop_end, end)` - same
5-arg-in/5-tuple-out shape as `trim_samples`/`reverse_samples`.

**First version faded the first/last 10% of `[start, end]` itself** (a
trapezoid inside the marked region) - wrong; corrected after real use.
Actual intent: a linear ramp from silence at frame 0 up to full volume
exactly AT the Start marker (the lead-in, otherwise untouched by anything
else on this page), and a mirror ramp from full volume AT the End marker
down to silence at the buffer's last frame (the lead-out).
**`[start, end]` itself is left completely untouched** - both ramps
merely reach gain 1.0 at its edges. `start == 0` or `end == frame_count -
1` skips that ramp rather than dividing by zero. Markers are always
returned unchanged - fading only scales values in place.

**If this looks backwards from what a "fade the marked region" feature
should do, it isn't - re-read this section before "fixing" it.**

### Normalise Sample: whole-buffer gain, not [start, end]-scoped

Added 2026-09-23. `normalize_samples(samples, start, loop_start,
loop_end, end)` - same 5-arg shape as the other three transforms -
applies ONE uniform gain to the ENTIRE buffer so its loudest sample hits
`_MAX_AMPLITUDE` (32767, the conventional safe ceiling; -32768 is
technically representable but not used). Deliberate scope choice, not an
oversight: unlike Trim/Fade (`[start, end]`-relative), Normalise matches
Reverse's whole-buffer scope - "loudest point of the sample" was read as
the whole buffer, matching how normalizing conventionally works. If a
user ever wants it scoped to `[start, end]` instead, that's a one-line
change to what `normalize_samples` iterates over - confirm with the user
first, the same way Fade's region got corrected above after guessing
wrong.

`_confirm_normalize_sample`'s "nothing to do" guards read the actual peak
(unlike Trim/Fade's marker-only guards): silent (`peak == 0`, nothing to
gain up) or already at `_MAX_AMPLITUDE`.

### Keygroup tab's Modulation card: read-only source mirrors

**Confirmed on real hardware, 2026-09-23**: the S2000 manual's prose
("Each keygroup has these modulation facilities separately available")
raised the question of whether Filter Mod/Pitch Mod/Amp Mod 3's SOURCE is
per-keygroup rather than program-wide as implemented. A read-only
diagnostic script (`test_scripts/keygroup_mod_source_diagnostic.py` -
gitignored) read a program's header and two keygroups' headers before/
after changing Filter Mod 1's source on the front panel: only the
PROGRAM header's byte changed, neither keygroup moved a byte. Confirmed
program-wide, as implemented. See
`test_scripts/MOD_SOURCE_KEYGROUP_INVESTIGATION.md` for the full
writeup if this question resurfaces - don't re-litigate from the manual's
prose alone without reading that first.

Added 2026-09-23. The Keygroup tab's Modulation card is Amount-only (see
"LFO2 and the modulation matrix" above); previously it gave no hint which
source was assigned program-wide. `_build_mod_amount_column_with_source_mirror`
places a disabled, read-only combo (`_build_mod_source_mirror_combo`,
same items as the real one) to the LEFT of the amount knob, for Filter
Freq (all 3 slots), Pitch slot 2, and Amplitude slot 3 -
`self.mod_filt1_source_mirror` etc. Pitch slot 1 (`L_PTCH`, always LFO1,
never assignable) gets no mirror combo - see below.

Side by side (mirror, then knob), matching the Program tab's own
(source, amount) left-to-right order - a first version stacked the
mirror above the knob to avoid widening the slot column, but the user
asked for side-by-side after seeing it; re-measured via
`horizontalScrollBar().maximum()` staying 0 on both tabs, so nothing else
needed adjusting.

**Keeping the mirror in sync needs two mechanisms**, because of build
order and how program loads avoid write-back loops:

- **Live edits**: the Keygroup tab is built before the Program tab's
  source combos exist in `__init__`, so
  `combo.currentIndexChanged.connect(mirror.setCurrentIndex)` is wired
  later, right after the Program tab's 5 source combos are constructed.
- **Loading a program** sets every `MODS*` combo via `blockSignals(True)`
  (avoiding a redundant write-back), which also means the live-edit
  connection never fires during a load - so the `program_mod_combos` load
  loop carries a `mod_source_mirrors` dict and sets each mirror's index
  explicitly alongside the real combo's, inside the same blockSignals
  block.

Miss either half and the mirror silently drifts - the live connection
alone misses every load, the load-time sync alone misses live edits.
`tests/test_program_editor_window.py` has one test per path plus
`test_keygroup_mod_source_mirror_is_never_a_write_target` (the mirror is
never wired through `_wire_combo_write`).

**Row labels/slots, tightened 2026-09-23** - now 3 rows, not 4:

- "Filter Frequency" → **"Filter Freq."**, matching the Program tab's own
  label for the same destination.
- **"Pitch"** is one row, two slots (was two separate rows): Slot 1 is
  the fixed always-on LFO1 route (`L_PTCH`, no source combo - see
  below), Slot 2 is the assignable one (`MODVPITCH`, mirrored). Matches
  the hardware's own PITCHMOD1/PITCHMOD2 order.
- "Loudness (slot 3)" → **"Amplitude"**, moved into **Slot 3**
  specifically (was Slot 1) - matches exactly where `MODSAMP3`/`MODVAMP3`
  sits on the Program tab's own "Loudness" row (slots 1/2 there are
  program-level `MODSAMP1`/`MODSAMP2`).

`_ModMatrixGrid`'s `data_row_count` for this card dropped from 4 to 3 -
bump it again if you add a row back (nothing else catches a mismatch,
the card just draws one shading band short or long).

**Pitch Slot 1 shows a plain "LFO1" label, not a disabled combo** -
`_build_mod_fixed_source_label` (new). A disabled combo there would still
read as "a choice that isn't available right now," misleading for a slot
whose source was never assignable at all. Same fixed width (130px) as
the real mirror combos so the row lines up, same muted `text_disabled`
color, but a bare `QLabel` with no border/background so it looks
unmistakably unclickable. Use this helper for any future never-assignable
mod slot rather than a disabled combo.

## Testing

`TESTING.md` undersells this slightly - there's also
`tests/test_program_editor_bridge.py` (BridgeWorker + LoggingBridge, pure
logic, no Qt event loop) and `tests/test_program_editor_window.py` (real
offscreen `QApplication`, actual widgets). Stated philosophy (from the
user): don't test the UI exhaustively, but cover core functionality and
any bug you fix - see `TESTING.md`'s "why several tests exist" section.

`uv run pytest tests/ -v` runs everything in well under a second.
