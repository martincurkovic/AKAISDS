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
  Re-measured: real range is `-24..+24`. `key_filter_track_knob` uses
  `-24..24` - narrower, so it already passes `encode_field`'s own range
  check, no override machinery needed.
- **`B_PTCHD`** (pitch-bend-down range): `s3k.params` says `0..12`
  (asymmetric with bend-up's `0..24`). Re-measured: actually `0..24`,
  symmetric. Unlike `K_FREQ` this is WIDER than the declared range, so a
  raw write above 12 fails `encode_field`'s check against the pinned
  `s3ked` rev - `core/program_editor_bridge.py`'s
  `_HARDWARE_RANGE_OVERRIDES`/`_lookup_for_write` patches a corrected
  `dataclasses.replace` copy of the `Parameter` in front of every write.
  See `tests/test_program_editor_window_demo_bridge.py`'s bend-down tests.
- **`LFO2TRIG`** (LFO2 retrigger mode): `s3k.params` declares raw `0..255`
  with no enum/notes at all. Measured: it's a plain boolean.
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

## Transfer Dashboard: window/tab shortcuts and "Open Editor" gating

`btn_open_editor` and the `&Window > Program Editor` menu action are only
enabled when BOTH a MIDI input and output port are selected AND the
sampler type is "Akai Sampler" (`device_type == "akai"` -
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

**Shortcuts**: `Ctrl+T` (⌘T) switches to the Transfer Dashboard, `Ctrl+E`
opens/switches to the Program Editor - both live in each window's own
`&Window` menu, mirrored so the same shortcut works regardless of which
window has focus. `Ctrl+1/2/3` are NOT window-switch shortcuts - they're
the Program Editor's own tab shortcuts (Multi/Programs/Samples), a
separate numbering scheme scoped to that window only.

## Transfer Dashboard: dropped/opened files get a stable local copy

Dragging a "cropped" region out of a sample browser into the queue added
the row fine, but Send later failed with "No such file or directory".
Root cause: `create_local_row` only stored the dropped file's PATH; the
actual read happens later, at send time. Sample editors render the crop
to a real OS temp file for the drag and delete it shortly after drop -
well before Send is clicked, and Qt's cross-platform `QMimeData.urls()`
has no way to tell "temporary" from "permanent" paths (that's an
AppKit-level file-promise protocol), so the row added fine and only the
later, lazy read lost the race.

**Fix**: `core/dropped_files.py` (pure filesystem logic, no Qt) copies
every dropped/opened file into a stable, app-owned location right where
`create_local_row` already opens it once for its bit-depth probe - the
queue row is built around THAT copy's path from then on. Applies
uniformly to drag-and-drop and File > Open (both funnel through
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

Once the stable-copy fix let a real sample-editor export survive long
enough to actually send, it played as silence. Root cause:
`sds_encoder.read_wav_samples`/`read_wav_channels` called `sf.read(path,
dtype="int16", always_2d=True)` - `soundfile`'s direct float→int16
coercion is not reliable for every source. Confirmed against a real
affected file: read as `int16` it peaked at 1 (of 32767); the same file
read as `float64` and scaled by hand (`round(x * 32768)`, clipped to
`[-32768, 32767]`) peaked at 22724, matching its real content. Not
source-specific - a `libsndfile` behavior that can affect any 32-bit
float WAV.

Fixed by `_read_float_scaled_to_int16` (both functions funnel through
it) - always reads as float (reliable, since `libsndfile` normalizes any
subtype to `[-1.0, 1.0]`) and scales to int16 by hand.
`test_read_wav_samples_32bit_float_scales_to_real_int16_values` now
asserts actual scaled values, not just "didn't crash" - the old test
only checked length/rate, which is why this shipped unnoticed.

### Follow-up: renaming a queued file to something long didn't scroll the field to the start

`open_edit_dialog` calls `setText(new_name)` then `setCursorPosition(0)`,
meant to show a long filename's START rather than Qt's own end-of-text
default. Extensive reproduction (bare `QLineEdit`, one embedded via
`setItemWidget`, a simulated dialog stealing focus, the real
`open_edit_dialog` flow end to end) could not reproduce a failure -
`cursorRect()` (the real visible scroll position, not just the logical
`cursorPosition()`) consistently showed it correctly scrolled every time.
Rather than claim an unconfirmed root cause, fixed with the standard,
low-risk Qt workaround for this class of bug regardless: `QLineEdit`
only recomputes its scroll offset lazily, inside its own `paintEvent`,
based on the cursor position *at paint time* - nothing guarantees a
repaint happens before something else in the same call stack touches the
field again first. `QTimer.singleShot(0, lambda: edit_field.setCursorPosition(0))`
defers the call to a fresh event-loop turn instead. If this resurfaces,
it's a genuinely different bug - this fix was verified as a correct
mitigation for the suspected race, not a confirmed root-cause fix.
`test_renaming_to_a_long_name_scrolls_the_field_back_to_the_start` in
`tests/test_dashboard.py` asserts on `cursorRect().x()`, not just
`cursorPosition()`, since the logical index was never actually wrong.

## Debug logging for real-hardware issues

`core/debug_log.py` sets up a rotating log at `~/.akaisds/akaisds.log` (renamed from `editor_debug.log` when it became app-wide; users on older builds may still have the old file).
`LoggingBridge` (in `program_editor_bridge.py`) wraps whatever bridge
`connect()` returns and logs every call's START/END/FAILED with thread
identity, timing, and a traceback on failure. If a user reports frequent
errors or a crash against real hardware, ask for this log (and the macOS
`.crash` report if it aborted) before guessing. Lines are long (full
`repr()` of `Parameter` objects) - grep/tail rather than reading whole.

Despite the module's own name/docstring being written for the Program
Editor's bridge layer, it's app-wide infrastructure: `sampler_controller.py`'s
two top-level unhandled-exception backstops (`on_sysex_received`,
`_send_next_queued_file`) also log here (`debug_log.get_logger().error(msg,
exc_info=True)`) rather than `print()`/`traceback.print_exc()`, which used
to be their only trace - invisible in a packaged GUI app with no attached
console. If you add a new unhandled-exception backstop anywhere in this
app, log it here too rather than reaching for `print()`.

## MIDI transport consolidation (`core/midi_transport.py`) - opt-in, not yet hardware-validated

The Transfer Dashboard (`SamplerController`, via `MidiManager`, via `mido`)
and the Program Editor (`BridgeWorker`, via `S3kBridge`, via `python-rtmidi`
directly) normally open **two independent connections** to the same
physical MIDI port whenever the editor is open - see "Samples tab: loop
points..." above for the already-confirmed real-hardware race this causes
(`SampleList: expected command 0x05, got 0x16`). It's also why the Program
Editor has no Settings menu of its own yet: `ui/settings_dialog.py`'s own
diagnostics already have to release/restore `MidiManager`'s ports before
running a hardware test, precisely because two open connections to one port
is already known to be unsafe - opening that same dialog from the editor
today would reproduce exactly the hazard that release/restore dance exists
to avoid.

`core/midi_transport.py` adds a **consolidated** transport so both windows
can share ONE real connection instead - `SharedMidiOutput` (one real
`rtmidi.MidiOut`, with a hard lock around every write, since two logical
senders on one wire can otherwise interleave a SysEx frame mid-transmission
and corrupt it) and `SharedMidiInput` (one real `rtmidi.MidiIn`, fanned out
via `_MessageFanout` to both a poll-style consumer - `S3kBridge`'s own
`get_message()` model - and a callback-style consumer - `MidiManager`'s own
mido-callback model - since rtmidi only supports one delivery mode per real
port instance). `core/midi_manager.py` branches to this transport, and
`core/program_editor_bridge.py`'s `connect()` builds the Program Editor's
`S3kBridge` from the Dashboard's already-open shared ports when available,
instead of opening its own second connection.

**Gated behind `AKAISDS_SHARED_MIDI_TRANSPORT` (unset by default)** - with
the flag unset, every line of this is dead code and the app behaves exactly
as it always has. This is new, not-yet-hardware-validated code: the
automated suite (`tests/test_midi_transport.py`, `tests/test_midi_manager.py`,
`tests/test_program_editor_bridge.py`'s `connect()` tests) covers the pure
logic, but nothing timing-sensitive - a real dual-connection race, real
sustained SDS transfer integrity, real output-write interleaving - can be
proven without actual hardware. **`test_scripts/midi_transport_
consolidation_test_plan.md`** (gitignored) is the real-hardware test plan
for this - 8 numbered tests covering exactly those things, plus editor
open/close cycles and the macOS `QThread`-crash class this app has already
been bitten by once (see `BridgeWorker` above). Don't flip the default to
on, and don't build an actual Settings-menu-in-the-editor UI on top of this,
until that document's tests are all a clean pass.

**A real segfault was found and fixed while writing the automated tests for
this**, worth knowing before adding similar tests anywhere in this repo:
a test that held a real `threading.Lock` across a `time.sleep()` from two
real `threading.Thread`s, run in-process alongside this suite's own
Qt/`QThread`-heavy tests (`test_program_editor_window.py` in particular),
reproducibly crashed the interpreter with a segfault **elsewhere** in the
suite, not in the offending test itself - a genuine, bisection-confirmed
instability from mixing raw OS threads with Qt's own threading in this
environment, not a bug in the code under test. Fixed by replacing the
real-thread stress test with a mock-based lock-usage check (`tests/
test_midi_transport.py`'s `test_shared_midi_output_send_message_acquires_
the_write_lock`) and a sequential (not multi-threaded) version of the
fan-out queue test - real concurrent correctness for this code needs
proving on real hardware anyway (see the test plan above), so the
automated suite doesn't need to re-fight that battle with real OS threads.
If a test genuinely needs multiple real threads running concurrently in
this repo's suite again, check this note first and be prepared to isolate
it (run just that file, then progressively more of the suite alongside it)
before trusting a single clean run.

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
- **Cards used to grow taller than their content as the window grew
  vertically.** A bare `QWidget`'s default vertical size policy
  (`Preferred`) can grow past its own `sizeHint()` when a layout has
  surplus space - the trailing `addStretch()`s above are supposed to
  claim that surplus instead, but `addStretch()`'s own default stretch
  factor (0) doesn't actually outrank a sibling widget's willingness to
  grow (also 0), so Qt's layout could still split extra space across the
  cards themselves. Fixed by `setSizePolicy(Preferred, Fixed)` vertically
  in `_build_section_card` - pins each card to its `sizeHint()`, can't
  grow OR compress below it (compressing is the earlier overlap bug
  above), rather than relying on winning a stretch-factor tie.
- **Paired cards' heights are pinned equal, not just their widths**, via
  `_equalize_card_heights(*cards)` - `setFixedHeight`s every card in a
  pair to `max(sizeHint().height() for card in pair)`, called once both
  are fully built (needs real final content to measure correctly).
  Applied to every paired row on both tabs (Range/Filter, Envelope 1/2,
  Volume Pan & Velocity/Pitch, LFO1/LFO2, Voice & MIDI/Portamento) even
  where two cards already happen to match today, so a later edit to just
  one side can't silently throw them out of sync. Call this for any new
  paired row too.

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

**Raw-vs-display offset now lives in `s3ked`, not here.** Until the
2026-09-27 `s3ked` bump, `s3k.params`' `PRGNUM` had no `display_offset`,
so this app did its own +1 (display) / -1 (write) conversion by hand in
`program_editor_window.py`. Upstream's §267 declared `display_offset=1`
on `PRGNUM` itself (confirmed on hardware: the panel is 1-based, the
register is 0-based - the same finding this app had already made
independently), so `get_parameter`/`set_parameter` now do that conversion
inside `s3k.params.decode_field`/`encode_field`. `program_number_spinbox`
was updated to show/write the panel's 1..128 value straight through, with
no converter of its own - re-adding one would double-offset it.
`_handle_program_change` in `program_editor_bridge.py` still needs the
raw wire-native byte for the actual MIDI Program Change message, so it
explicitly subtracts `p.lookup("PRGNUM", "program").display_offset` back
out after calling `get_parameter` rather than assuming the two now agree.
If you bump `s3ked` again and `PRGNUM`'s handling changes further,
recheck all three sites: the spinbox load, `_wire_spinbox_write`'s (lack
of) converter, and this subtraction.

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
  to unsigned two's-complement on write. Range is ±50.00st. **The
  in-memory cache (`_sample_waveform_cache[i]["stuno"]`) must always hold
  the RAW value, never the display semitones** - a live tune edit used to
  store the raw spinbox value (semitones) into that key while a header
  load stored the true raw byte, so reselecting a previously-edited
  sample fed semitones back through the raw→semitones converter a second
  time and redisplayed garbage (`+40.00st` came back as `+0.16st`,
  confirmed on hardware - the actual hardware write was always correct,
  this was display-only). `_on_sample_tune_changed` now stores
  `_semitones_to_sample_tune_offset(value)`, matching the load path.
  `test_sample_tune_survives_a_reselect_after_a_live_edit` guards this.
- `PRGNUM`'s raw value is off by one from the panel's own numbering (raw
  8 shows as program 9) - since the 2026-09-27 `s3ked` bump this
  conversion happens inside `s3k.params` itself (`display_offset=1`), not
  in this app's spinbox code. See "PRGNUM and Program Change" above for
  the full detail and why the MIDI Program Change send still needs the
  raw byte back.

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
one. What shipped, after a couple of per-event/per-gesture heuristics
each fixed one symptom but left another: stop inferring intent from an
ambiguous signal at all - `angle.x() != 0` always wins for pan,
unconditionally; nothing produces nonzero `.x()` except a genuine
horizontal swipe or a mouse's own horizontal wheel, so there's nothing to
disambiguate. A vertical-only mouse wheel has no `.x()` at all, so
Shift+scroll explicitly repurposes its `.y()` for pan (the usual "hold
Shift to scroll sideways" convention) - unmodified vertical scroll does
nothing on purpose, since repurposing it for pan was the root of every
earlier version of this bug. Zoom (Ctrl+scroll) keeps a simple `x if
nonzero else y`, since zoom has no left/right ambiguity.

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
repro could reproduce. Reading `~/.akaisds/akaisds.log` from their
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

The envelope fills in live, left-to-right, in step with an in-progress
transfer, instead of appearing all at once when it finishes.
`WaveformView.begin_live_capture()` switches from header-only to an
empty growing list right before a fetch starts; `append_live_samples(chunk)`
extends it and repaints each call. `_rebuild_envelope` clips to
`min(view_start + view_length, len(self._samples))` and sizes the
envelope's pixel width proportionally to how much of the current view is
actually loaded, so a half-loaded prefix occupies only its own left
fraction rather than stretching to fill the canvas.

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
transfer, then jumped when the real 31000-frame total replaced it
mid-view. Fixed by `_demo_sample_frame_count(sample_index)` (predicts the
real length via a cheap `wave.open().getnframes()` header peek, or
`_synthesized_demo_frame_count` if the fixture is missing) replacing the
placeholder. Demo-mode-only: on real hardware both values trace back to
the same physical sample's `SLNGTH`, so they can't disagree.

### Sample loop type, root note, and rename/delete

Three additions, all mirroring existing patterns:

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

### Trim/Reverse/Duplicate, and timestretch/resample (capability audit)

Checked first whether `s3k`/`s3ked` already provide sample/program/keygroup
duplication, hardware time-stretch, or hardware resampling - **none
exist** (grepped both packages; only structural messages are Delete*, no
Copy/Duplicate/Clone anywhere). `SSRATE` is a plain metadata byte -
writing it changes the declared rate, not the audio. This repo's own
`sds_encoder.resample_to_target_rate` is the only real resampling in the
stack, client-side; time-stretch would need new DSP work.

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

**Why Trim/Reverse send-then-delete under a temp name**
(`_perform_sample_edit_real`): two failure modes to design around:

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

**Duplicate Sample** (button, right-aligned on `sample_edit_row` next to
Trim/Reverse/Fade/Normalise) reuses this same send pipeline but is
simpler: nothing is deleted or renamed, so no temp-name step - the new
name is confirmed distinct from every resident sample up front (same
name-collision hazard as above), then sent directly.
`_perform_duplicate_sample_real` looks up the new sample's index by name
once resident, then copies every header field `send_file_queue` doesn't
set (`SPTYPE`/`SPITCH`/`SHLTO`/`STUNO` and the four loop/start/end
fields) across from the source - otherwise "duplicate" would silently
mean "audio copy with default metadata." Unlike Trim/Reverse/Fade/
Normalise, it never touches the source's own audio, so it isn't
"destructive" in the same sense - no "cannot be undone" warning needed,
just the name prompt. **Demo-mode-disabled**, same reasoning as Duplicate
Program/Duplicate Keygroup (`DemoBridge` has no add-sample primitive
either) - gated through `_set_sample_edit_buttons_enabled` (which already
reacts to `has_waveform()`, which this button also needs), not the list
context-menu's own enable method.

### Loop-point markers push each other, and click-to-cycle when stacked

`WaveformView`'s old `clamp_marker` stopped a dragged marker dead at its
neighbour. Now `push_marker`: moving a marker far enough pushes the
neighbour along instead, cascading (Newton's cradle). `start <=
loop_start <= loop_end <= end` still always holds, just enforced by
shoving rather than refusing to cross. `clamp_marker` is gone entirely.

**The write side had to change with it**: a push can move up to all four
markers from one action, so `_schedule_marker_write` takes the FULL
marker dict from both before and after the edit and writes exactly the
field(s) whose value actually changed (`SSTART` if start moved, `SMPEND`
if end moved, `LOOPAT1`+`LLNGTH1` together if either loop edge moved) -
get this wrong and the hardware silently desyncs from what's on screen
(e.g. a push that visibly moves `loop_end` but only writes `SMPEND`
leaves the sampler looping at its old position). `_on_waveform_marker_committed`
(drag release) gets "before" from the cache entry (only updated on
commit); `_on_marker_spinbox_changed` gets it from `WaveformView.markers()`
read immediately before `set_marker`. `_flush_marker_write` unconditionally
flushes all four marker debounce keys now, no `which` parameter needed.

**Follow-up**: once a push lands several markers on the same frame, they
sit at the same pixel column - the first version's `_marker_near(x)`
always broke ties the same way (effectively always "start"), so only the
frontmost marker could ever be grabbed again. Fixed the usual way
overlapping-selection is solved: `_markers_within_hit_radius(x)` (every
visible marker within `_HIT_RADIUS_PX`, closest first) replaces
`_marker_near`, and `mousePressEvent` cycles through the candidate list
on repeated clicks at the same spot (compared by the actual sorted name
list, not just pixel proximity) rather than always grabbing the closest.
Marker spinboxes need none of this - each is directly clickable
regardless of canvas position, an unambiguous fallback for separating a
stack.

### Waveform rendering details: zone tint, zero-crossing, high-zoom, gap-bridging

`_waveform_zone_color(frame, palette)` picks a color per envelope column:
`text_disabled` (greyed) outside `[start, end]` (resident but never
plays), `keygroup_color_3` (the same teal the loop markers use) inside
`[loop_start, loop_end]` inclusive, plain `accent` elsewhere within
`[start, end]`. Frame comes from `frame_for_x`, the same mapping marker
positioning uses, so boundaries line up with the markers pixel for pixel.

`_draw_zero_crossing_line` is a plain horizontal guide at `mid_y`, drawn
even in header-only mode. `_draw_connected_samples` fixes a real bug at
high zoom: the per-column min/max bar mode degenerates once zoomed past
1:1 (each column maps to ≤1 sample, so min == max, every bar collapses to
a dot, reading as disconnected dashes). `paintEvent` switches to an
actual point-to-point polyline whenever `view_length <= self.width()` -
cheap, since that condition bounds sample count by canvas width. Below
that threshold the bar rendering is unchanged; both paths share
`_waveform_zone_color`. Crash-guarded by
`test_paint_does_not_crash_at_high_zoom_in_connected_sample_mode`, since
the progressive-loading interaction (`self._samples` still filling in,
shorter than the zoomed-in view) is easy to get wrong here.

The bar mode itself also used to leave real gaps between adjacent
columns whose samples all sit in a narrow, low-variance range (a quiet
passage) even though the audio is continuous - the classic min/max-bar
artifact. `_bridge_envelope_gaps` (`build_envelope` always runs output
through it) walks the envelope once and nudges the nearer edge of any
two non-overlapping adjacent columns just far enough to touch - never
shrinks a genuine peak/trough, no-op once already overlapping. Operates
on amplitude `(lo, hi)` pairs, not pixels (the y-mapping is strictly
monotonic linear, so amplitude-space gaps are y-space gaps).

### Zoom ceiling: a flat 500x could never reach single-sample resolution

Dragging a marker "as zoomed in as possible" still wasn't sample-accurate.
Root cause: `_MAX_ZOOM = 500.0` was a flat ceiling, but `_view_length()`
is `round(frame_count / zoom)` - for any sample bigger than roughly
`500 * canvas_width_px` frames, max zoom still left more samples visible
than canvas pixels, so every pixel of mouse movement skipped several
frames permanently on larger samples (real S3000-series samples can be
several hundred thousand to a few million frames).

Fixed by `WaveformView._max_zoom()` returning `max(_MIN_ZOOM,
float(self._frame_count))` - the exact zoom at which `_view_length()`
bottoms out at 1 - so single-sample resolution is always reachable
regardless of sample size; `set_zoom` clamps against this instead of the
old flat constant. This also means zoom now naturally reaches
`_draw_connected_samples`' rendering mode for every sample, previously
geometrically unreachable for big samples. No change to `_ZOOM_STEP`.
See `test_max_zoom_reaches_single_sample_resolution_for_a_huge_sample`.

### Loop type gating: markers/spinboxes/tint disappear, don't just grey out

When `SPTYPE` is "No looping"/"One-shot" (`_SPTYPE_VALUES_WITHOUT_LOOP =
{2, 3}`), the loop has no meaning: `_set_loop_markers_enabled(enabled)`
greys the loop spinboxes + legend swatches and calls
`WaveformView.set_loop_enabled(enabled)`, which makes `loop_start`/
`loop_end` **not draw at all** (paintEvent skips them outright) and
excludes them from `_markers_within_hit_radius`. The loop-region teal
tint reverts to plain accent. Getting the disabled *look* right needed
its own fix: `style.qss.template`'s `QComboBox, QSpinBox, QDoubleSpinBox`
rule hardcodes colors, silently defeating Fusion's automatic
disabled-greying unless `:disabled` is spelled out too - added alongside
the existing `QPushButton:disabled`/`QLineEdit:disabled` rules, app-wide.

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
for ordinary sample *loading* - redundant with the waveform's own
progressive fill and the status bar (which already showed frame counts).
`_on_sample_receive_progress` now takes a `label` and writes
`"{label} - {percentage}% ({current}/{total} frames)"` to the status bar
instead of driving a bar.

**Trim/Reverse/Fade got a small bar back**: unlike loading, sending
already-loaded audio back out has no progressively-filling waveform of
its own - the audio sits static until send/delete/rename finishes.
`sample_edit_progress` (140px, hidden by default) lives next to the Zoom
controls, driven by `_on_sample_edit_progress` (which also calls
`_on_sample_receive_progress` for the shared status-bar text) - ordinary
loading never calls this, so the bar stays hidden for a plain
double-click load.

### Fade In/Out: fades the lead-in/lead-out AROUND [start, end], not inside it

One "Fade In/Out" button (next to Trim/Reverse) applies both directions
in one pass. `core/sample_editing.py`'s `fade_in_out_samples(samples,
start, loop_start, loop_end, end)` - same 5-arg-in/5-tuple-out shape as
`trim_samples`/`reverse_samples`.

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

`core/sample_editing.py`'s `normalize_samples(samples, start, loop_start,
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

**Confirmed on real hardware**: the S2000 manual's prose ("Each keygroup
has these modulation facilities separately available") raised the
question of whether Filter Mod/Pitch Mod/Amp Mod 3's SOURCE is
per-keygroup rather than program-wide as implemented. A read-only
diagnostic script (`test_scripts/keygroup_mod_source_diagnostic.py` -
gitignored) read a program's header and two keygroups' headers before/
after changing Filter Mod 1's source on the front panel: only the
PROGRAM header's byte changed, neither keygroup moved a byte. Confirmed
program-wide, as implemented. See
`test_scripts/MOD_SOURCE_KEYGROUP_INVESTIGATION.md` for the full
writeup if this question resurfaces - don't re-litigate from the manual's
prose alone without reading that first.

The Keygroup tab's Modulation card is Amount-only (see "LFO2 and the
modulation matrix" above); previously it gave no hint which source was
assigned program-wide. `_build_mod_amount_column_with_source_mirror`
places a disabled, read-only combo (`_build_mod_source_mirror_combo`,
same items as the real one) to the LEFT of the amount knob, for Filter
Freq (all 3 slots), Pitch slot 2, and Amplitude slot 3 -
`self.mod_filt1_source_mirror` etc. Pitch slot 1 (`L_PTCH`, always LFO1,
never assignable) gets no mirror combo - see below.

Side by side (mirror, then knob), matching the Program tab's own
(source, amount) left-to-right order - re-measured via
`horizontalScrollBar().maximum()` staying 0 on both tabs after switching
from an earlier stacked layout.

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

**Row labels/slots** - 3 rows, not 4:

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

## Slice Editor (Samples tab): manual ReCycle-style breakbeat chopping

"Slice Editor…" (next to Duplicate Sample) opens a modal
`SliceEditorWindow` (`ui/slice_editor_window.py`) over a sample's already-
loaded audio, letting the user place slice markers by hand and export every
resulting slice back to the sampler as a new one-shot sample. Same
`has_waveform()`/demo-mode gate as Duplicate Sample in
`_set_sample_edit_buttons_enabled` - DemoBridge has no add-sample primitive
either, so the button is fully disabled in demo mode rather than offering a
partial fake (same reasoning already established for Duplicate
Program/Keygroup/Sample - see "Trim/Reverse..." above).

**No transient detection** - manual markers only, plus an "Equal Slices"
quick-start (even spacing via `core.sample_slicing.equal_slice_markers`) and
zero-crossing snapping (`find_nearest_zero_crossing`) on every add/drag-
release. Automatic transient detection was discussed and deliberately cut
as a separate, much larger undertaking - if it's ever added, it should
produce the same plain frame-index marker list this already consumes, not
a parallel code path.

**`SliceWaveformView` (`ui/slice_waveform_view.py`) is NOT a reuse of the
Samples tab's own `WaveformView`** - genuinely different marker model:
`WaveformView` has a FIXED four named markers that PUSH each other past a
neighbour (Newton's-cradle cascade, see `push_marker`); this widget has two
edge handles (start/end - "shave off dead space") plus an ARBITRARY-length,
user-managed interior marker list, and a drag just CLAMPS at whichever
neighbour (marker or edge) is nearest - there's no meaningful "shove the
next slice along" behaviour here, since slices are independent chunks, not
one continuous loop region. It does reuse `waveform_view.py`'s free,
Qt-independent helpers (`build_envelope`, `frame_for_x`, `x_for_frame`,
`_MIN_ZOOM`) rather than re-deriving that math.

**Zero-crossing snapping is snap-ON-RELEASE, not continuous during the
drag** - the same "redraw live, commit on release" split
`WaveformView.marker_committed` already uses for its own hardware writes.
Snapping on every mouse-move would make a marker visibly jump around
mid-drag instead of tracking the cursor smoothly; it also isn't free
(`find_nearest_zero_crossing` searches outward from a target frame,
`O(search_radius)` per call). `core.sample_slicing.find_nearest_zero_crossing`
picks the nearest of two samples straddling a genuine sign change (or a
sample that's exactly 0) - there's no continuous zero to land on in
discrete audio, so "nearest to zero amplitude among the two frames the
crossing sits between" is the practical definition used throughout.

**Every exported slice is forced to one-shot (`SPTYPE = 3`)** regardless of
the source sample's own loop settings - a chopped drum break slice isn't
meant to loop. `SPITCH`/`STUNO`/`SHLTO` are still copied from the source
(same fields `_perform_duplicate_sample_real` copies), so at least pitch/
tuning carry over; `SSTART`/`SMPEND` are written to cover the whole sent
buffer (0 to the slice's own length - 1).

**Export sends the WHOLE batch in ONE `send_file_queue` call**, not one
call per slice the way `_perform_duplicate_sample_real` sends its single
file - `SamplerController._finish_unit` only emits `transfer_finished` once
`self._file_queue` is fully drained (see its own code), so a single
`send_file_queue(file_entries)` with all N slices already gets "send these,
tell me once they're ALL done," including per-file progress
(`transfer_progress`) and per-file status text (`status_changed`, "Sending
file X/N") for free - looping `_wait_for_any_signal` N times would be
strictly more code for a worse result. `_export_slices` in
`program_editor_window.py` is the hardware-talking half that does this
(temp WAVs, the batch send, then one header-field fixup pass per landed
sample); `SliceEditorWindow` itself only owns naming/collision/confirmation
UI and calls into it - same layering `BridgeWorker`'s own section above
describes for the rest of this window's hardware access.
`tests/test_program_editor_window.py`'s own `FakeSamplerController` used to
only read `file_entries[0]` (every existing caller only ever sent one file)
- extended to iterate the whole list for this, backward-compatible with
every single-file caller.

**Slice names are zero-padded to a FIXED width computed once per batch**
(`ui.slice_editor_window.slice_export_names`) - `<base>-01`.."<base>-16"`,
not `-1`.."-16"` - so every name in the same export truncates its base
identically. Getting this wrong (each name computing its own suffix width)
would silently truncate slice 1's base one character longer than slice
10's the moment a batch crosses 10 slices, since `NAME_LENGTH` (12) always
needs to leave room for the suffix. Collisions are checked against a live
`existing_names_provider()` callback (re-queried right before export), not
a snapshot taken when the dialog opened - the same "stale copy" bug class
`_update_multi_program_combo_names`/`_update_zone_sample_combo_names`
already exist to avoid elsewhere on this page.

**`SliceEditorWindow` is deliberately application-modal** (`.exec()`, not
`.show()`) - there's no `is_transfer_busy()` re-check once export is
actually underway, so modality is what actually prevents the user tabbing
back to the Transfer Dashboard and starting a conflicting send while the
window is open, rather than a race this window would otherwise have to
poll for. `_wait_for_any_signal` (in `ProgramEditorWindow`, which is what
performs the real export) already pumps its own nested `QEventLoop` while
blocking on a send, so nesting `QDialog.exec()` on top of that is nothing
new for this codebase. `reject()`/`closeEvent()` both ignore Close/Esc/
window-X while an export is actually in flight (`self._exporting`) - that
nested event loop means a Close click COULD otherwise be delivered and
processed mid-batch-send.

**Deferred to a later version, deliberately out of scope for this pass**:
click-to-preview playback through the computer's own speakers (needs an
audio output driver this app has never opened before - a separate,
larger piece of work); real macOS trackpad pinch-to-zoom
(`WaveformView._handle_pinch_zoom`'s own `QEvent.Type.NativeGesture`
handling was not duplicated onto `SliceWaveformView` - Ctrl+wheel zoom and
the Zoom +/-/Fit buttons cover it for now). Mono only - stereo sample
editing was out of scope for this pass too, though in practice every
resident S3000-series sample is already a single mono buffer on this
hardware regardless (stereo only ever exists as a pair of mono sample
slots, `-L`/`-R` - see `sampler_controller.py`'s own
`build_stereo_channel_name`), so this was never actually a gap to guard
against on the input side.

Test split: pure math (`equal_slice_markers`/`find_nearest_zero_crossing`/
`slice_bounds`/`slice_samples`) in `tests/test_sample_slicing.py`, the
widget's own marker/drag/zoom behaviour in
`tests/test_slice_waveform_view.py`, the dialog's naming/collision/
confirmation UI (with `export_callback` entirely faked, never a real send)
in `tests/test_slice_editor_window.py`, and the real hardware-talking half
(`_export_slices`, plus `_open_slice_editor`'s own early guards) in
`tests/test_program_editor_window.py` - the same split
`core/sample_editing.py` vs. `_perform_sample_edit_real` already
established for Trim/Reverse/Fade/Normalise. `SliceEditorWindow.exec()`
itself is never called in a test (would hang an offscreen test with
nothing to close a real modal loop) - `test_slice_editor_window.py`
constructs the dialog directly and drives its methods without showing it.

### Follow-up, first real-hardware session: a confirmed dual-connection MIDI race

A user's real `akaisds.log` showed an export report "N slices were sent,
but the sample list couldn't be refreshed to set their header fields" -
not a timeout. The actual traceback: `s3k.bridge.sample_list()` raised
`ValueError("SampleList: expected command 0x05, got 0x16")` on
`ProgramEditorWindow`'s own BridgeWorker connection, at the exact moment
the log showed `SamplerController` (the Transfer Dashboard's own,
separate connection to the same physical MIDI port - see "Samples tab:
loop points..." above) doing its own post-send RSTAT/RSLIST chatter and a
further queued send. Two independent software listeners on the same wire
occasionally each grab a reply meant for the other - confirmed, not
theoretical. `_reload_sample_list_with_retries` (used by both of
`_export_slices`'s own reload points, replacing two direct
`_wait_for_any_signal([samples_loaded, samples_load_failed], ...)` calls)
retries `submit_sample_list()` up to 3 times with a short pause before
reporting real failure - a large batch export is exactly the case most
likely to overlap with `sampler_controller`'s own chatter, since it's the
biggest, longest-running send this window ever triggers on that shared
connection. **Deliberately scoped to `_export_slices` only** -
`_perform_duplicate_sample_real`/`_perform_sample_edit_real` have the
identical latent race in their own single "one more reload" calls, but
weren't reported failing and are more heavily-relied-on paths; don't
retrofit the retry there without a reason to believe they're actually
hitting it.

### Follow-up: trackpad pinch-zoom, terse zoom buttons, and Fit collapsing the scrollbar

`SliceWaveformView` initially shipped without `WaveformView`'s trackpad
pinch-to-zoom (`QEvent.Type.NativeGesture` handling) - added afterward
once asked for, same `event()`/`_handle_pinch_zoom` mechanism, mirrored
rather than shared (this widget doesn't subclass `WaveformView` - see its
own class docstring for why). Zoom buttons read bare "−"/"+" (with
`setToolTip` picking up the discoverability a text label used to provide),
not "Zoom −"/"Zoom +".

**The horizontal scrollbar collapses to zero height at "Fit", unlike the
Samples tab's own `WaveformView` scrollbar.** That one is deliberately
wrapped in a fixed-height container even while hidden (see "Editing:
markers, spinboxes, zoom" above) because it sits in a permanently-visible
panel where neighbouring rows jumping on every zoom change would be
worse. `SliceEditorWindow` is a short-lived modal dialog with nothing
below the scrollbar that mockup/user testing found jarring about a bit of
reflow - `self.scrollbar` is added directly to the dialog's layout with no
such wrapper, so `setVisible(view_length < frame_count)` actually reclaims
the space instead of just hiding the handle inside a reserved strip.

**Close/Esc/window-X now asks for confirmation if there are unsaved slice
edits.** `self._dirty` starts `False`, is set by `_mark_dirty` (wired to
`SliceWaveformView.markers_changed`, connected only AFTER the initial
`set_waveform` call so that call's own emit doesn't mark a freshly-opened
window dirty), and is cleared again only after a SUCCESSFUL export (a
failed one leaves it set - nothing actually landed on the sampler, so
there's still something to lose). `_confirm_discard` is the single method
both `reject()` and `closeEvent()` call through - checked in this order:
still exporting (never allowed to close, no prompt - see the modality
section above) → not dirty (close immediately, no prompt - nothing to
lose) → dirty (ask). Zoom/pan changes don't count as "edits" (they use
`SliceWaveformView`'s separate `view_changed` signal, never
`markers_changed`), only start/end/marker changes do.

### Follow-up: click-to-preview playback, and the audio output driver this app never had before

The "deferred to a later version" note above (click-to-preview needing an
audio output driver this app had never opened) is now implemented: a plain
single click on empty waveform space (no handle nearby) previews that
slice's audio through the computer's own speakers/interface -
`core/audio_preview.py`'s `SlicePreviewPlayer`, the first thing in this
codebase to open a `QAudioSink` rather than a MIDI port. The device/buffer
size it uses are chosen in `MidiSettingsDialog`'s new "Audio Preview" tab
(`ui/settings_dialog.py`) and persisted via `app_config.save_audio_output_device`/
`save_audio_buffer_ms` - unrelated to MIDI, but kept in the same dialog
since it's still "I/O hardware settings," just for the computer's own
output instead of the sampler's.

**Single click previews, double click still adds a marker** (unlike real
ReCycle, which uses double-click to preview) - deliberate, confirmed with
the user: this app had already committed double-click to "add a slice
marker" before this feature existed, and re-purposing it would break the
existing marker workflow. The two don't actually collide today (a plain
click on empty space is currently a no-op in `SliceWaveformView`'s own
`mousePressEvent` - only clicks on a handle do anything), **except** for
the literal event sequence Qt delivers for every double-click: `mousePress`
→ `mouseRelease` → `mouseDoubleClick` → `mouseRelease` - there IS a real
single-click press before Qt recognizes the second one as a double-click.
Firing preview playback immediately on that first press would mean every
"add a marker" double-click also plays a brief blip of the wrong (pre-split)
slice a moment before the marker lands.

Fixed with the standard single-vs-double-click disambiguation pattern:
`SliceWaveformView._schedule_preview_click` doesn't preview immediately -
it starts a single-shot `QTimer` for `QApplication.doubleClickInterval()`ms
(the platform's own double-click timing, not a made-up constant) and only
actually emits `slice_preview_requested` when that timer fires
(`_fire_preview`). `mouseDoubleClickEvent` and grabbing an actual handle
(`mousePressEvent`'s drag-start branch) both call `_cancel_pending_preview()`
first - so the ~300-400ms most users will never consciously notice buys a
guarantee that a marker-adding double-click never also triggers a preview.
See `tests/test_slice_waveform_view.py`'s click-to-preview tests, which
call `_fire_preview()` directly to simulate the timer elapsing rather than
sleeping a real test.

**`SlicePreviewPlayer` retriggers, it doesn't queue** - a new `play()` call
always `stop()`s whatever's already playing first, matching how a user
actually scans through slices while chopping a break (click, click, click
- each one should cut off the last and start immediately, not queue up a
backlog). Both the `QAudioSink` and its backing `QBuffer` are kept alive on
`self` for exactly this reason - Qt does not take ownership of the
`QIODevice*` passed to `QAudioSink.start()`, and a local reference falling
out of scope mid-playback is a real crash, not just a leak (same class of
gotcha `BridgeWorker`'s section above warns about for a different Qt
object). `SliceEditorWindow` calls `stop()` before a real export starts
(the batch send freezes the UI for a while - nothing should still be
playing under that) and in both `reject()`/`closeEvent()`, after
`_confirm_discard()` says it's actually OK to close.

Mono 16-bit PCM only, matching every sample this app ever handles
(`struct.pack("<" + "h" * len(chunk), *chunk)` - same packing
`sds_encoder.py` already uses, not `array.array`, to avoid introducing a
second convention for the same job). `QAudioDevice.isFormatSupported()` is
checked before construction; an unsupported combination falls back to the
device's own `preferredFormat()` (pitch will be off, logged, rather than
refusing to preview at all) - realistically only reachable via an unusual
audio interface, not the default output on a Mac/Windows/Linux machine.

**Buffer size is a `QComboBox` of raw sample-frame counts (32/64/128/256/
512/1024/2048), not milliseconds** - deliberately mirrors the unit a DAW's
own audio buffer-size setting uses (e.g. Ableton Live) rather than
inventing a different one, at the user's own request. `app_config`'s
`audio_buffer_samples` key stores the raw frame count; converted to actual
bytes at playback time via `QAudioFormat.bytesForFrames()` (depends on the
format actually in use, so this can't be precomputed once).

**The playhead**: while a slice is sounding, `SlicePreviewPlayer` polls
`QAudioSink.processedUSecs()` on a 30ms `QTimer` and emits
`position_changed(frame)` - converted via the ORIGINAL sample's own
framerate (not whatever `fmt` ended up being, in case
`isFormatSupported()` above forced a fallback - see that method's own
comment), since `processedUSecs()` measures real elapsed time regardless
of which format is actually playing. `finished()` fires once when playback
actually stops, for any reason (ran off the slice's own end, `IdleState`,
or an explicit `stop()`/retrigger) - `SliceWaveformView.set_playhead`/
`clear_playhead` are wired straight to these two signals in
`SliceEditorWindow.__init__` and don't know anything about `QAudioSink`
themselves. Drawn as two things, both gated on `self._playhead_frame`
being non-`None`: `_draw_playhead_highlight` (a low-alpha `accent`-colored
tint across the whole currently-playing slice band, drawn early so the
waveform/markers paint on top of it) and `_draw_playhead_line` (a solid,
undashed line at the exact current frame, drawn last so it's never
obscured - deliberately undashed, unlike every other marker line on this
widget, so it doesn't read as just another slice boundary).

**The Slice Editor is now openable in demo mode, not fully disabled like
Duplicate Sample/Program/Keygroup.** Marker placement and click-to-preview
need only the audio already in memory - only the Export step is the same
add-new-resident-sample primitive `DemoBridge` genuinely lacks. So
`_set_sample_edit_buttons_enabled` no longer gates `slice_editor_button` on
`demo_mode` at all; instead `SliceEditorWindow` takes its own `demo_mode`
param (passed by `_open_slice_editor`) and disables just `export_button`
(with a tooltip explaining why) when set. **Don't re-merge this back into
one all-or-nothing gate** - unlike Duplicate Sample, most of this dialog's
value has nothing to do with hardware at all, and disabling the whole
thing again would silently regress the only way to evaluate/demo this
feature without real hardware in front of you.

**`AKAISDS_DEMO_INSTANT`** (new env var, checked live like
`AKAISDS_DEMO_SAMPLER` itself, not cached) skips `_fetch_demo_sample_audio`'s
own realistic-transfer-speed pacing entirely (`steps = 1`, no `time.sleep`)
- for iterating on UI work away from hardware (the Slice Editor above all)
without waiting out a simulated SDS transfer on every sample selected.
Doesn't touch `_DEMO_MS_PER_WORD` at all when set, deliberately (see
`test_akaisds_demo_instant_skips_the_pacing_loop_entirely`'s own comment on
why that's asserted, not just assumed).

**The bottom-of-file standalone launcher** (`if __name__ == "__main__":`)
now does two more things than it used to: `_StandaloneHost` gained a
`sampler_controller` stub (just enough for the Slice Editor's own
busy-check - `is_transfer_busy() -> False` - Export itself still isn't
reachable this way, its button is disabled by `demo_mode` regardless), and
the script now `os.environ.setdefault("AKAISDS_DEMO_SAMPLER", "1")` before
constructing anything. That second part fixes a real latent bug: this
script always passes `bridge=DemoBridge()` two lines down regardless, but
every `demo_mode` check elsewhere in this file re-reads the env var
independently - so running this script without ALSO manually exporting
`AKAISDS_DEMO_SAMPLER=1` used to leave every one of those checks disagreeing
with the actual bridge in use (e.g. `_fetch_demo_sample_audio` never firing,
falling through to a real-hardware code path that `_StandaloneHost` can't
support). `setdefault` means an explicit `AKAISDS_DEMO_INSTANT=1` alongside
it on the command line still works normally.

### Follow-up: faster preview debounce, double-click deletes a marker, Settings dialog reorganized

**The click-to-preview debounce is `QApplication.doubleClickInterval() // 2`**,
not the full interval - halved at the user's own request (the full delay
felt sluggish for quickly scrubbing through slices). Still derived from
the platform's own interval rather than a hardcoded constant, just at half
of it. Trade-off, worth knowing if "double-click to add a marker also
plays a stray blip of audio" ever gets reported again: halving narrows,
but doesn't eliminate, the window described in `_schedule_preview_click`'s
own comment - an unusually slow double-click can still land outside it.

**Double-clicking an existing slice marker now deletes it** - a faster
alternative to the right-click context menu (`_show_marker_context_menu`),
not a replacement for it; both stay wired. Double-clicking start/end does
nothing, same as before this existed - they're the region's own edges,
not slice boundaries, and were never deletable any way. Safe against the
same first-click-before-the-double-click-fires event ordering
`_cancel_pending_preview` already has to account for elsewhere on this
widget: Qt's documented sequence for a double-click is press → release →
doubleClick → release, so `self._dragging` (set by the first press,
targeting the marker about to be deleted) is already cleared by the first
release *before* `mouseDoubleClickEvent` ever runs - no stale reference to
guard against in the second `mouseReleaseEvent`.

**`build_section_card`/`build_scroll_area` moved from `ProgramEditorWindow`
methods into free functions in `ui/qt_helpers.py`**, unchanged apart from
no longer taking `self` - `ui/settings_dialog.py` needed the exact same
card look and the exact same "past the minimum, a tab scrolls instead of
the window growing" scroll-area behavior, and duplicating either (both
carry real, easy-to-get-wrong subtlety - see this file's own "section
cards, scroll areas" notes above) would have meant keeping two copies of
the same gotchas in sync by hand. `ProgramEditorWindow._build_section_card`/
`_build_scroll_area` are now one-line delegates kept only so every existing
`self._build_section_card(...)` call site elsewhere in that (huge) file
didn't need touching.

**`MidiSettingsDialog` (`ui/settings_dialog.py`) collapsed from 4 flat tabs
to 2, each built from section cards**, at the user's own request once 4
tabs made the dialog uncomfortably narrow: "Audio/MIDI" (MIDI Input/Output
card + Audio Output card - the Slice Editor preview device/buffer size
added alongside that feature) and "Troubleshooting" (Hardware Test card +
Interface Test card - what used to be "MIDI Hardware Test"/"MIDI Interface
Test"). Window title changed from "MIDI Settings" to plain "Settings" to
match - **grep for the literal string `"MIDI Settings"` before assuming
it's gone everywhere**; a couple of tooltip/log strings in
`dashboard.py`/`settings_dialog.py` that referenced the dialog by its old
name were updated alongside it, but this is exactly the kind of string a
future addition could reintroduce without realizing the dialog was
renamed.

**`btn_settings`/`btn_open_editor` (`ui/dashboard.py`) renamed** from
"⚙ MIDI Settings"/"Open Editor" to "⚙ Settings..."/"Open Editor..." (the
trailing `...` signals both open another window, matching the Settings
menu action's own existing "Settings..." wording, which the button had
drifted out of sync with). The fixed-width-column sizing next to the logo
(see "Transfer Dashboard: window/tab shortcuts" above) is computed from
each button's own live `sizeHint()`, not a hardcoded width, so this rename
needed no follow-up fix there.

## Testing

`TESTING.md` undersells this slightly - there's also
`tests/test_program_editor_bridge.py` (BridgeWorker + LoggingBridge, pure
logic, no Qt event loop) and `tests/test_program_editor_window.py` (real
offscreen `QApplication`, actual widgets). Stated philosophy (from the
user): don't test the UI exhaustively, but cover core functionality and
any bug you fix - see `TESTING.md`'s "why several tests exist" section.

`uv run pytest tests/ -v` runs everything in well under 10 seconds.
