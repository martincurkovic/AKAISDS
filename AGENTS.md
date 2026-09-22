# Agent notes for AKAISDS

This file is for AI agents (Claude Code, etc.) picking up work in this repo cold.
It exists to save you from re-deriving context that isn't obvious from the code
alone, and especially to stop you from "fixing" a few things that look like bugs
but are actually deliberate, hard-won corrections. For human-facing docs see
`README.md`, `BUILDING.md`, `TESTING.md` and `CONTRIBUTING.md` - this file
doesn't repeat those.

## What this app actually is

Two largely independent windows, both PySide6/Qt, both driven by `src/main.py`:

- **Transfer Dashboard** (`src/ui/dashboard.py`, `src/ui/main_window.py`,
  `src/controller/sampler_controller.py`) - the original/primary feature. Sends
  and receives audio samples to/from Akai S1000/S2000/S3000 samplers (or any
  generic MIDI Sample Dump Standard device) over MIDI SysEx. This is what the
  README documents.
- **Program Editor** (`src/ui/program_editor_window.py`,
  `src/core/program_editor_bridge.py`) - a newer window for editing a
  program/keygroup/multi's *parameters* (filter, envelopes, pan, LFO, keygroup
  ranges, multi part assignments) directly on an S3000-series sampler over
  SysEx. Opened via the dashboard's Window menu. This is the part with the
  most non-obvious history - see below.

Both windows can switch to a `&Window` menu action to open the other one; only
one is ever shown at a time (see `closeEvent`/`open_program_editor` wiring in
`main_window.py` and `program_editor_window.py`).

## Third-party dependencies you should actually go read

`s3k` and `s3ked` are **not part of this repo** - they're pulled in via
`pyproject.toml` (`s3ked` pinned to a specific git rev; `s3k` comes along with
it) and live in `.venv/lib/python3.12/site-packages/s3k(ed)/`. They provide:

- `s3k.bridge.S3kBridge` - the real MIDI/SysEx bridge to actual hardware.
- `s3k.params` - the parameter registry (`p.lookup(name, region)`), which is
  extensively and usefully documented: many `Parameter.notes` fields record
  hardware measurements, corrected assumptions, and dated research notes
  ("Measured 2026-08-14 by...", "RESOLUTION_NOTES §56", etc). **Read these
  before assuming a field's range or meaning** - several past bugs in this
  project came from trusting the Akai manual's prose over what was actually
  measured.
- `s3ked.demo.DemoBridge` - a duck-typed fake sampler used for development
  without hardware (see below). It's deliberately written to reproduce real
  hardware quirks (see e.g. its `arrive()`/`renumber_programs()` docstrings),
  not just return canned values.

Don't "fix" anything in these packages - they're an external dependency pinned
to a specific commit; if something there looks wrong, it's either the wrong
assumption on this project's side, or worth raising upstream, not editing
in-place.

### Three fields where this project's own hardware beats s3k.params' notes

`s3k.params`'s own measured notes are usually the most trustworthy source in
the whole stack (see above) - but on three program-level fields, this
project's user has since measured their own S3000-series unit and gotten a
different answer, confirmed in front of them, not from a manual:

- **`K_FREQ`** (keygroup filter key-tracking) - `s3k.params` declares
  `-30..99` (its own notes cite a 2026-08-24 sweep finding no clamp at 12 or
  24 either, contradicting an older manual's claimed `+/-24`). Measured
  again on this project's own hardware (2026-09-20): the real range is
  `-24..+24` after all. `key_filter_track_knob` in `program_editor_window.py`
  uses `-24..24`, not `s3k.params`'s declared range - this is deliberate, see
  the comment there. This one is a NARROWER subset of `s3k.params`' own
  declared range, so every value the widget can send already passes the
  dependency's own `encode_field` range check - no further work needed.
- **`B_PTCHD`** (pitch-bend-down range) - `s3k.params` declares `0..12`,
  asymmetric with `B_PTCH` (bend-up)'s `0..24`. Measured on the same unit,
  same session, and reconfirmed directly by the user (2026-09-21), both
  directions: it's actually `0..24`, symmetric with bend-up.
  `bend_down_combo` uses `0..24` for the same reason (a combo now, not a
  spinbox - "Bend up"/"Bend down" both moved to combos with the widget's
  index doubling as the raw value, same convention as every other combo on
  this page). Unlike `K_FREQ`,
  this one is WIDER than `s3k.params`' declared range - a raw
  `p.lookup("B_PTCHD", "program")` still fails `encode_field`'s own range
  check for anything above 12, against the currently pinned `s3ked` rev.
  `core/program_editor_bridge.py`'s `_HARDWARE_RANGE_OVERRIDES`/
  `_lookup_for_write` patches a corrected copy of the `Parameter` (a
  `dataclasses.replace`, not an edit to the dependency itself) in front of
  every write, so writes up to 24 actually reach the hardware. See
  `tests/test_program_editor_window_demo_bridge.py`'s
  `test_bend_down_raw_s3k_params_lookup_still_declares_the_narrower_0_to_12`
  (still true - confirms the override is doing real work) and
  `test_bend_down_combo_above_12_reaches_hardware_via_the_range_override`.
- **`LFO2TRIG`** (LFO2's retrigger mode) - `s3k.params` declares the full raw
  byte range `0..255` with no `values={}` enum and no measured note at all
  (unlike `LFO1WAVE`/`LFO2WAVE`'s documented shape enums) - it genuinely
  doesn't say what the real values mean. Measured on this project's own
  hardware (2026-09-21): it's a plain boolean. `lfo2_trig_combo` in
  `program_editor_window.py` offers only Off/On accordingly. No override
  needed here (0/1 always fits `0..255`) - this is a UI-only narrowing, same
  shape as `K_FREQ`.

If you're auditing widget ranges against `s3k.params` and find one of these
three "wrong," it isn't - re-read the comment at the call site (and
`_HARDWARE_RANGE_OVERRIDES` for `B_PTCHD`) before "fixing" it back to match
the dependency. If you get hardware access and can re-verify any of these
(or find another), update the comment with the date and what you found, the
same way these three are documented now.

## Developing without hardware

Set `AKAISDS_DEMO_SAMPLER=1` before launching, and `program_editor_bridge.connect()`
returns a `DemoBridge` instead of opening a real MIDI port. Useful for UI work,
but **cannot** exercise anything that depends on real timing/latency or on
`S3kBridge`'s actual thread-safety constraints - see the next section.

The same env var also fakes the Samples tab's *audio* - `SamplerController`
has no demo mode of its own (it always wants a real MIDI connection), so
`ProgramEditorWindow._fetch_demo_sample_audio` loads real audio from
`tests/test_audio.wav` instead of calling
`main_window.sampler_controller.receive_samples()` when this is set.
`python src/ui/program_editor_window.py` (this file's own `__main__` block)
already runs with a `main_window` that has no `sampler_controller` at all,
so this is the only way that path ever produces anything there. Every
sample index loads the same fixture (there's only the one) - paced with
real `time.sleep()` + several genuine `receive_progress`-shaped updates so
the progress bar is exercised too. Falls back to `_synthesize_demo_sample_audio` (a deterministic fake tone,
not meant to resemble real audio) if that file is ever missing or
unreadable, rather than failing outright - demo mode silently staying
broken until someone notices a moved/renamed fixture is worse than an
obviously-fake tone. `WaveformRenderer/` (the separate reference repo
`WaveformView` was ported from) is the user's own copied-in material, not
an AKAISDS fixture path - `tests/test_audio.wav` is the only one this
window should ever read from.

## `BridgeWorker` - read this before touching anything under `core/program_editor_bridge.py`

`S3kBridge` documents itself as unsafe for concurrent calls on one connection.
An earlier version of the Program Editor spun up a fresh `QThread` per UI
action (one per keygroup click, one per parameter write, etc.) with nothing
stopping two of them from being in flight at once. Against `DemoBridge` this
was invisible (plain GIL-serialised Python calls); against real hardware it
interleaved SysEx frames on the wire **and** crashed the app outright
(`QThread::~QThread()` calling `qFatal()` when a stale loader's thread was
still mid-call while its Python object got garbage-collected). This was
diagnosed from an actual macOS crash report + the debug log described below -
two concurrent `KeygroupLoader` threads were caught red-handed.

The fix, and the current architecture: **`BridgeWorker`** (in
`core/program_editor_bridge.py`) is a single persistent `QThread` that owns the
bridge for the editor's whole lifetime and processes requests off a queue
strictly one at a time. `ProgramEditorWindow` creates exactly one of these in
`__init__`, connects all of its signals once, and every UI action calls one of
its `submit_*()` methods rather than creating a new thread. Requests for
"current state of X" (keygroups, keygroup detail, multi parts) coalesce - a
new one drops any not-yet-started one of the same kind, so rapid clicking
doesn't queue up a backlog of stale reads. Writes and Program Changes are
never coalesced.

**If you're tempted to go back to a thread-per-action pattern here, don't** -
that's the exact bug this architecture exists to prevent. If you need to add a
new kind of hardware request, add a `submit_*()` + `_handle_*()` pair to
`BridgeWorker` following the existing ones, not a new `QThread` subclass.

Tests exercise `BridgeWorker` synchronously via `process_pending()` (drains the
queue without spinning a real thread) rather than `.start()`; window-level
tests use a real thread + `wait_until_idle()` since they go through actual
signal/slot delivery. If you add a test that constructs a `ProgramEditorWindow`
directly, you **must** call `editor._worker.stop(); editor._worker.wait()` in
teardown (see the `editor` fixture in `tests/test_program_editor_window.py`) -
otherwise the worker thread is still running when the test ends and you'll
hit the exact `QThread: Destroyed while thread is still running` this
architecture was built to avoid.

## Update checker (`core/update_checker.py`, `ui/update_helper.py`)

Checks the public GitHub repo's `/repos/.../releases/latest` API for a newer
tagged version than `ui/_version.py`'s `APP_VERSION`, entirely unauthenticated.
This only works unattended because `.github/workflows/build.yml`'s release
job publishes with `draft: true` - `/releases/latest` only ever returns the
latest *published* (non-draft, non-prerelease) release, so an in-progress
draft build is invisible to it and never gets offered to users as "the
latest version". If a tagged build ever fails to get manually published on
GitHub, the checker just keeps reporting the previous release as current -
expected, not a bug.

`UpdateCheckRunner` (`ui/update_helper.py`) is shared by `MainWindow`,
`ProgramEditorWindow`, and `AboutDialog` - each owns its own instance, and
each calls `.wait()` on it before actually closing (`closeEvent`/`done()`).
That's the same root-cause bug class `BridgeWorker` above exists to
prevent: a `QThread` (here, `UpdateCheckWorker`) whose Python wrapper gets
garbage-collected while its underlying OS thread is still running.
`ProgramEditorWindow` is the one most likely to actually hit this -
`dashboard.py`'s `open_program_editor()` replaces `self.editor_window` on
every click, so a just-closed editor with a check still in flight can lose
its last Python reference well before that check (bounded by a 5s network
timeout) actually finishes.

## Debug logging for real-hardware issues

`core/debug_log.py` sets up a rotating log at `~/.akaisds/editor_debug.log`.
`LoggingBridge` (also in `program_editor_bridge.py`) wraps whatever bridge
`connect()` returns and logs every `get_parameter`/`set_parameter`/etc. call's
START/END/FAILED with thread identity, timing, and a full traceback on
failure. This is what made the `BridgeWorker` crash diagnosable in the first
place - if a user reports "frequent errors" or a crash against real hardware,
ask for this log (and the macOS `.crash` report if it actually aborted) before
guessing. Individual log lines are long (they include the full `repr()` of
`Parameter` dataclass objects, notes fields included) - grep/tail rather than
reading the whole file.

## Program Editor UI: section cards, scroll areas, and the busy indicator

The Program and Keygroup tabs are built from `_build_section_card(title,
*row_layouts)` (groups related rows into one titled, bordered card - Volume/
Pan/Velocity, LFO1, LFO2, Pitch, Filter, Envelope 1, Envelope 2, Modulation,
etc. - ENV1/ENV2 used to share one "Envelopes" card and were later split
into two, so don't assume the card list here is exhaustive or stable) and
`_build_scroll_area(page)`
(wraps a whole tab's cards so the window doesn't have to be tall enough to
show every card unscrolled). If you add another control, put it in the most
relevant existing card rather than a new bare row - and if you add a whole
new card, know that this file's `setMinimumSize(...)` was tuned by hand
against the actual measured layout, not a round number:

- **Height** doesn't need to fit everything anymore - past the minimum, a
  tab scrolls instead of its cards compressing into each other and visibly
  overlapping (this happened during development; `QVBoxLayout` will compress
  children below their size hint and eventually let them overlap rather than
  erroring).
- **Width** is the real constraint now: some cards are deliberately paired
  side by side in one row (e.g. Range+Filter on the Keygroup tab) to save
  vertical space. Below a certain width those pairs stop fitting and a
  `QScrollArea` shows an *unwanted horizontal* scrollbar instead of just
  wrapping - found by growing the window until `scroll_area.horizontalScrollBar().maximum()`
  hit zero for both tabs. If you widen a card or add another side-by-side
  pair, re-check this the same way rather than guessing a new minimum.
- **Stretch ratios between paired cards should be measured, not guessed.**
  Range+Filter briefly shipped as a 1:2 split on the assumption that Filter
  (3 knobs) needs more width than Range (a label + two spinboxes) - wrong:
  `card.sizeHint()` showed Filter's own content is actually *narrower*
  (273px vs Range's 299px), so the 2x weight just stretched it wider than
  it needed to be, visibly out of line with Range right above it. Every
  paired row on this page is 1:1 now. Before giving one card more stretch
  than its partner, check `sizeHint()` on both rather than eyeballing which
  one "looks like" it needs more room.
- `_build_section_card`'s layout ends with `addStretch()` - without it, a
  card whose content is shorter than the row it's paired with gets its
  slack space split *before the header too*, not just below the content
  (`QBoxLayout` spreads unclaimed space across every gap when nothing
  claims a stretch), which reads as the card being vertically centered
  instead of top-aligned like its neighbor. If a future card looks
  vertically centered instead of top-aligned, this is almost certainly why.

`BridgeWorker.busy_changed` (a `Signal(bool)`) drives the indeterminate
progress bar next to the Refresh button - `True` from the moment any job is
queued while nothing else is pending, `False` once the queue actually drains
back to empty, so a burst of several jobs (Refresh submits three at once)
reads as one span rather than flickering. `ProgramEditorWindow` doesn't show
or hide the bar on that raw signal directly, though - it debounces through
two one-shot `QTimer`s (`_busy_show_timer`, `_busy_hide_timer`), because on a
**fast** bridge (`DemoBridge`, or any test fake) the worker thread can race
ahead and briefly drain the queue to empty *between* two of Refresh's
back-to-back `submit_*()` calls, which without the hide-timer's grace period
showed up as real, reproducible flicker. On real hardware this specific race
essentially can't happen (submitting a whole batch takes microseconds; the
worker is still busy with job one when the rest land) - it's the fast/fake
bridges that expose it, which matters if you're evaluating this without
hardware in front of you. Don't remove either timer to "simplify" this
without re-reading the comment on them; a test
(`test_a_new_busy_true_during_the_hide_grace_period_cancels_the_hide`)
exercises the exact race that made the hide-timer necessary.

Widget visibility assertions in tests here use `.isHidden()`, not
`.isVisible()` - the `editor` fixture never calls `.show()` on the window, and
`.isVisible()` is unconditionally `False` for any widget whose top-level
window was never shown, regardless of what `setVisible()` was called with.
`.isHidden()` tracks the widget's own explicit shown/hidden state instead.

## Envelope graphs (`ui/envelope_graph.py`): each stage's width must be independent

`_adsr_points()` (ENV1) and `_env2_points()` (ENV2) used to give each stage
(Attack/Decay/Release; R1-R4) an on-screen width *proportional to its share
of the live total* - `attack_frac = attack / (attack+decay+release)`, and
the ENV2 equivalent normalized against `sum(rate for rate, _ in stages)`.
That's a real bug, not just a style choice: it means turning ONE knob
visibly resizes the OTHER stages' segments too, even though their own
values never changed - a user caught this by comparing two screenshots
where only Decay differed and Attack's ramp had visibly changed shape. No
synth's ADSR display works that way; each stage's footprint should depend
only on its own value.

Fixed by giving each stage a **fixed** width budget instead of a shared,
value-dependent one - `(1 - SUSTAIN_HOLD_FRACTION) / 3` per ADSR stage,
`w / 4` per ENV2 stage - scaled only by `own_value / 99` within that fixed
budget. The trade-off: the envelope no longer necessarily traces the full
widget width (it only reaches the right edge when every stage is
individually maxed at 99) - that's correct, not a regression; empty space
after a short envelope is normal in every reference synth's display too.
If you touch this code, **do not re-normalize one stage's width against
the others' current values** to "simplify" it or make it trace the full
width again - that's reintroducing the exact bug above. See
`test_adsr_stage_width_is_a_fixed_share_not_a_relative_one` and
`test_env2_stage_width_is_a_fixed_share_not_a_relative_one` in
`tests/test_envelope_graph.py`, which exist specifically to catch this.

## LFO2 and the modulation matrix (Program/Keygroup tabs)

`LFO2` is a real, independent second LFO - but on this hardware its own
`rate`/`depth`/`delay`/`shape` are hardwired to modulate **Pan** (an
auto-pan effect): they're `PANRAT`/`PANDEP`/`PANDEL`/`LFO2WAVE` in
`s3k.params`, stored in the `program.pan` group despite being LFO2's own
controls, not some pan-specific thing. `LFO2WAVE` only documents 3 shapes
(Triangle/Sawtooth/Square) - unlike `LFO1WAVE`'s measured 4th ("Random"),
LFO2's shape hasn't been measured the same way (LFO1's 4th shape was found
by reading the *pitch* track, since LFO1 drives pitch; LFO2 drives pan, no
equivalent measurement exists), so `lfo2_shape_combo` only offers 3 - don't
assume LFO2 also secretly has a 4th shape without measuring it first.

LFO1 also has its own sync/desync toggle, exposed as `lfo1_sync_combo` next
to `lfo_shape_combo` on the LFO1 card. The underlying field is `DESYNC`
("Enable de-synchronisation of LFO1 across notes"; `s3k.params` values
`{0: "OFF", 1: "ON"}`) - i.e. the field is phrased as its own negation.
`lfo1_sync_combo` is deliberately labeled the positive way round ("Sync":
On/Off) to match how a user actually thinks about it, which means its
*items* are listed in the opposite order from `DESYNC`'s own OFF/ON text,
but the raw byte each combo index writes is **not** inverted - index 0
("On", LFO1 stays synced) is `DESYNC=0`, index 1 ("Off", desynced) is
`DESYNC=1`, same "combo index is the raw byte" convention as everything
else on this page. See the construction comment on `lfo1_sync_combo` before
reordering its items. LFO2 has no equivalent field - only LFO1's sync
behaviour has been measured/exposed; don't assume LFO2 has one too without
checking first (LFO2 does have its own `LFO2TRIG` retrigger boolean, but
that's a different thing - see "Three fields where this project's own
hardware beats s3k.params' notes" above).

The **Modulation** section cards (one on each tab) expose the assignable
modulation matrix (`MODS*`/`MODV*` fields): up to 3 (source, amount) slots
per destination (Pan, Loudness, Filter Frequency), 1 slot each for LFO1's
own Rate/Depth/Delay and for Pitch. The 14-source enum (`s3k.params.
MOD_SOURCES`) is mirrored by hand as `_MOD_SOURCE_LABELS` in
`program_editor_window.py`, **with envelope 3 (value 14) deliberately left
out** - `s3k.params`' own hardware notes say env3 genuinely works on a base
machine with no expansion board (it's the *second filter* it can also
target that needs the IB304F board, not env3 itself), but this app has no
Envelope 3 editor page yet, so offering it as a source with nothing to
shape its ADSR would be confusing rather than useful. `test_mod_source_labels_match_s3k_params_minus_env3`
guards this list against silently drifting from the dependency.

**Why the matrix is split across both tabs**: every destination's *source*
choice (`MODSPAN1..3`, `MODSAMP1..3`, `MODSLFOT/L/D`, `MODSFILT1..3`,
`MODSPITCH`) lives in the `program` SysEx region - one shared choice for
every keygroup in the program - so every source dropdown lives on the
Program tab's card. Most *amounts* are program-level too and sit right next
to their source there. But `MODVFILT1..3`, `MODVPITCH`, `MODVAMP3` (Loudness
slot 3's amount only - slots 1/2 are program-level) and `L_PTCH` (a
separate, always-on LFO1-to-pitch depth, not part of the 3-slot matrix at
all) are stored in the `keygroup` region - a genuinely different value per
keygroup - so those rows show only the source (with a footnote) on the
Program tab, and only the amount knob on the Keygroup tab's own Modulation
card. This split is not a UI choice, it's what the hardware actually
stores - don't "fix" it into one card without re-checking each field's
`region` in `s3k.params` first.

Every slot is built through `_build_mod_slot`/`_build_mod_source_only_column`/
`_build_mod_amount_only_column` (all in `program_editor_window.py`), placed
into a real `QGridLayout` (`mod_grid`/`kg_mod_grid`) with fixed columns per
slot (2 - Source, Amount - on the Program tab's card; 1 - Amount only - on
the Keygroup tab's) via `_build_mod_matrix_row`/`_build_mod_matrix_amount_row`.
Up to 3 slots side by side per row used to mean each individual combo/knob
carried its own "Source"/"Amount" label (repeated on every control, since
otherwise 3 side-by-side knobs read as one thing split three ways rather
than three independent routings) - as of 2026-09-21 that's instead a
"Slot 1/2/3" + "Source"/"Amount" header built once per card
(`_build_mod_matrix_header`/`_build_mod_matrix_amount_header`), with the
amount knob sitting beside its source combo rather than stacked below it.
The grid's own column alignment now carries the "which control belongs to
which slot" job the repeated labels used to - don't reintroduce per-control
"Source"/"Amount" labels without also removing the header, or both will say
it. The amount knobs (`_build_mod_amount_knob`)
are also the one place on this page that enables a `Knob` (see `Knob.__init__` -
it starts disabled) right where it's built rather than in `__init__`'s later
"enable knobs" block - deliberate, not a missed step, since each one is
fully constructed AND wired by that same helper call with nothing else left
to wait for. Don't "fix" these into the big enable block, and don't assume
every knob not listed there is unwired - check whether it came from one of
these three helpers first.

Adding this card's rows is also what pushed `ProgramEditorWindow`'s
`setMinimumSize` from `1040` to `1080` - re-measured the same way this
file's own "section cards, scroll areas, and the busy indicator" section
above describes (grow the window until `horizontalScrollBar().maximum()`
hits zero on both tabs).

## PRGNUM and Program Change (Multis tab)

There's no working SysEx write to assign a program to a multi part - `PRNAME`
in the multipart region is read-only and writing it has no effect on
playback (documented in `s3k` itself). The only real mechanism is sending a
raw MIDI **Program Change** on the part's channel, addressed by the target
program's own `PRGNUM` (its independently-assignable MIDI program number,
*not* its position in the program list).

The catch: freshly created or independently-loaded programs commonly all have
`PRGNUM == 0` (never individually assigned), which makes every Program Change
target whichever program the hardware associates with 0, regardless of which
one was actually picked. `BridgeWorker` calls `renumber_programs()` (a method
on both `S3kBridge` and `DemoBridge` - gives every resident program a distinct
number in list order, no follow-up sampler operation needed per its own
docstring) once before the first Program Change is sent, and again whenever
the program list reloads (`_programs_renumbered` flag). If you see "assigning
a program always picks the wrong one" again, check `PRGNUM` collisions first.

There's also no hardware concept of "no program assigned" for a multi part -
the blank `"-"` combo entry is a UI-only placeholder and deliberately a no-op
when selected, not a bug.

**The 16 part combos hold their own copy of every program's name**, populated
in `_on_programs_loaded` (a full program-list reload) - they don't
automatically follow changes made elsewhere. Renaming a program
(`program_name_edit`) used to update the Programs list but leave all 16
part combos showing the old name until the next full Refresh - a real bug,
fixed by having `_commit_program_name`/`_on_program_name_typed` call
`_update_multi_program_combo_names(program_index, name)` explicitly. If you
add another program-identity field that the Multis tab displays a copy of,
it'll need the same explicit push - `_on_programs_loaded` alone only catches
a full reload, not an in-place edit.

## Note names: S3000XL octave convention, not general MIDI

`core/midi_notes.midi_note_to_name()` calls note 60 (middle C) **"C3"**,
matching the S3000XL's own front panel - **not** the general-MIDI convention
where 60 is C4. This was a real bug (fixed once already) where the editor
showed every note one octave higher than the hardware. `ui/note_spinbox.py`'s
`_note_name_to_midi()` is the exact inverse and must stay in sync if this ever
changes again - see `tests/test_note_spinbox.py`'s full-range round-trip test.
Don't "correct" the octave number back to the general-MIDI convention; C3 is
right for this hardware.

## Samples tab: loop points, and the one place this window deliberately blocks

`s3k` has no bulk sample-audio transfer at all - no RSPACK/ASPACK, only
header reads (`SDATA`/byte-addressable `SHEADER`). So the Samples tab's
waveform (`ui/waveform_view.py`'s `WaveformView`, built into
`program_editor_window.py`'s `_build_samples_tab`) gets its header fields
(loop points, start/end, rate) through the normal `BridgeWorker`/`S3kBridge`
connection like everything else on this page, but gets the actual audio from
`main_window.sampler_controller` - the Transfer Dashboard's own hand-rolled
SDS receiver, reached because `ProgramEditorWindow` already holds a
`main_window` reference. That's a **second, separate MIDI connection** to
the same physical port, open at the same time as this window's own bridge -
not new (the Dashboard's `midi_manager` was already left open whenever
`open_program_editor()` just hides the dashboard rather than closing
anything), but worth knowing about if sample loads and other hardware
actions ever seem to interleave strangely.

**`LOOPAT1` is the loop's END, not its start.** `LLNGTH1` measures
*backwards* from it - the loop region is `[LOOPAT1 - LLNGTH1, LOOPAT1]`.
This is confirmed in `s3ked`'s own `docs/RESOLUTION_NOTES.md` (§136 in the
upstream repo, not bundled in the installed wheel - see the project's git
checkout if you need to re-read it): the S3000XL manual says so directly,
an independent implementation (`ConvertWithMoss`) agrees, and getting it
backwards is exactly the bug that produced silent/degraded loops throughout
that project's own history. **This is NOT mentioned in `s3k.params`' own
`notes=` field for `LOOPAT1`** - reading only that file, the natural
assumption is that `LOOPAT1` is where the loop starts. It isn't.
`program_editor_bridge.py`'s `_SAMPLE_DETAIL_FIELDS` comment carries the
same warning at the point every consumer reads from. `loop_start` is always
*derived* (`LOOPAT1 - LLNGTH1`) in this codebase, never read or written
directly - there is no such raw field.

Loading a sample's audio is a real SDS dump and can legitimately take
minutes for a large sample (see the README's own transfer-time table) - the
UI is meant to freeze while it happens, not work around it; `WaveformView`'s
own placeholder text says so before the user double-clicks. The mechanism
behind that, `ProgramEditorWindow._wait_for_any_signal`, is the **one place
in this whole window** that blocks the calling thread on a signal instead of
returning and letting a result arrive later through a persistently-connected
slot (`BridgeWorker`'s whole design, per its own docstring, is the opposite
of this - connect once in `__init__`, let signals arrive asynchronously).
Don't reach for `_wait_for_any_signal` anywhere else on this page without a
comparably good reason; everywhere else, the async pattern is correct.

### Editing: markers, spinboxes, zoom - one write path for both input methods

Loop points are editable two ways, both feeding the same underlying state:
dragging a marker on `WaveformView`'s canvas (Shift held = fine mode, a
`_FINE_DRAG_DIVISOR`-times-slower relative-delta drag rather than the usual
absolute mouse-position mapping - a float accumulator (`_drag_value`)
carries the sub-frame remainder between mouse-move events so it doesn't
drift over a long drag), or typing/stepping one of the four marker
spinboxes in `program_editor_window.py`. **Both paths go through the same
two helpers**, `_schedule_marker_write`/`_flush_marker_write` - which
single field(s) a given marker maps to (`SSTART`/`SMPEND` are 1:1; either
loop edge moving means writing both `LOOPAT1` and `LLNGTH1` together, since
they jointly encode the region - see the LOOPAT1 note above) lives in
exactly one place. If you add a third input method, wire it through these
two rather than re-deriving the field-pairing logic again.

`WaveformView` also has horizontal zoom/pan (`_zoom`, `_view_start`,
`_view_length()`) - `x_for_frame`/`frame_for_x` take a view window
(`view_start`, `view_length`), not the sample's whole frame count, so a
marker or drag position always resolves relative to what's actually
visible. `clamp_marker` stays whole-sample-bounded regardless of zoom (you
can still drag a marker toward a position currently scrolled off-screen).
Ctrl+wheel zooms centered on the cursor. **A real macOS trackpad pinch
does NOT arrive as a Ctrl+wheel event** - an earlier version of this
comment claimed it did, unverified; it's actually its own event
(`QEvent.Type.NativeGesture` / `Qt.NativeGestureType.ZoomNativeGesture`),
handled separately via `WaveformView.event()` -> `_handle_pinch_zoom`
(`QWidget` has no dedicated virtual for this - overriding `event()` is the
documented Qt way to catch it).

**Panning went through three designs before landing.** All three tried to
fix the same underlying bug class: a trackpad reports pan motion through
`angleDelta().x()` (left/right) or `.y()` (up/down) depending on which way
the fingers actually moved, and naive handling either drops one axis
outright or gets the direction wrong for a moment before "bouncing" onto
the right one.

1. *Prefer y unless it's exactly zero, else x.* Fixed "horizontal swipes
   do nothing" (mouse wheels only ever report `.y()`) but a genuine
   horizontal swipe's small, nonzero stray `.y()` value - present for the
   first event or two before macOS locks the gesture onto one axis - won
   over the real, much larger `.x()` delta. Visible as a brief
   wrong-direction jump right as a swipe began.
2. *Compare magnitudes, pick the larger, per event.* Fixed the start-of-
   gesture case above, but a **slow** swipe's per-event deltas on both
   axes are small and close together, so ordinary hand tremor on the axis
   orthogonal to the intended motion could still occasionally win *at any
   point*, not just the start - visible as continuous bouncing throughout
   a slow drag, not just at the beginning.
3. *Lock the winning axis once per gesture*, via `QWheelEvent.phase()`
   (`ScrollBegin` decides, `ScrollUpdate`/`ScrollMomentum` hold the lock,
   `ScrollEnd` clears it). Better, but still built on the same per-event
   magnitude comparison at the moment the lock is decided - a slow
   gesture's *first* event can itself be tremor-dominated, locking onto
   the wrong axis for the whole gesture.

**What actually shipped, after the user got fed up with chasing this and
suggested it directly**: stop inferring intent from an ambiguous, noisy
signal at all. `angle.x() != 0` always wins for pan, unconditionally, no
comparison against `angle.y()` ever - nothing produces a nonzero `.x()`
except a genuine horizontal swipe or a mouse's own horizontal wheel, so
there is nothing to disambiguate. A plain vertical-only mouse wheel has no
`.x()` to report at all, so Shift+scroll is required to explicitly
repurpose its `.y()` for pan instead (the ordinary "hold Shift to scroll
sideways" convention most other apps already use) - unmodified vertical
scroll does nothing, deliberately, since repurposing it for pan (there is
no vertical content to scroll) was the root of every version of this bug.
`dominant_wheel_axis`/`dominant_wheel_delta` (the per-event and
per-gesture designs' helper functions) and the phase-locking state are
gone entirely, not just superseded - there was nothing left worth keeping
once the ambiguity itself was removed rather than resolved more cleverly.
Zoom (Ctrl+scroll) keeps a simple `x if nonzero else y` for its own delta,
since zoom has no left/right ambiguity to get wrong the way pan did.

The Zoom +/-/Fit buttons and the horizontal `QScrollBar` next to the
waveform are the discoverable/no-modifier equivalents to wheel-driven
zoom/pan - kept in sync via `WaveformView.view_changed` ->
`_on_waveform_view_changed` (view -> scrollbar) and the scrollbar's own
`valueChanged` -> `_on_waveform_scrollbar_moved` (scrollbar -> view), both
`blockSignals`-guarded so neither bounces back into the other. The
scrollbar itself sits in a fixed-height container regardless of whether
it's currently shown - a plain `setVisible(False)` collapses it to zero
height, which shoved the marker spinboxes below it up and down every time
zoom crossed the "needs scrolling" threshold.

### Editing loop points without audio - header and audio load independently

A user pointed out that loading the actual waveform can take *minutes*
over SDS, but the loop points themselves only need the sample's *header* -
a handful of fast `get_parameter` reads over the same `BridgeWorker`
connection every other tab on this page already uses, not the Transfer
Dashboard's slow one. Asking someone to sit through a multi-minute SDS
dump just to nudge a loop point was never necessary.

`WaveformView.set_header(frame_count, start, loop_start, loop_end, end)`
shows and makes the four markers draggable from header data alone, with
**no envelope trace** (`has_header()` is true, `has_waveform()` stays
false until real audio actually arrives, if it ever does).
`_frame_count > 0` is what actually gates dragging/zoom/pan now, not
`_samples is not None` - `_samples` only gates whether there's an
envelope to paint. **Get this distinction wrong in a new method and you
will reintroduce editing being silently blocked without audio** - this
already happened once during development, in `_on_marker_spinbox_changed`
(gated on `has_waveform()`, so typing a value into a spinbox before audio
loaded did nothing at all) - caught by the same smoke test that verifies
the whole feature end to end, not by a code read.

`ProgramEditorWindow._on_sample_selected` now fires an automatic, async
`submit_sample_detail()` the moment a sample is selected (if nothing's
cached yet for it) - persistently connected in `__init__`
(`_on_sample_detail_loaded`/`_on_sample_detail_load_failed`), *not*
through `_wait_for_any_signal` (that blocking helper stays reserved for
the one place a deliberate freeze is correct - the actual audio fetch;
see its own comment above). `_sample_waveform_cache` entries can now exist
in two shapes - header-only (`"samples": None`) and full (`"samples":
[...]`) - both always carry `"frame_count"` regardless. **If the user
edits a marker while only the header is loaded and *then* double-clicks
to load audio, `_load_sample_waveform` must reuse the already-cached
markers, not re-derive fresh ones from a new header read or
`_demo_loop_points`** - re-deriving would silently discard the edit. The
shared `_markers_from_header()` helper (header values -> `(frame_count,
start, loop_start, loop_end, end)`, demo-mode seeding included) exists
specifically so both fetch paths - the automatic one and
`_load_sample_waveform`'s own fallback for the rare case where the
automatic fetch hasn't resolved yet - compute identically, without
duplicating the demo/real branching twice.

`WaveformView.set_waveform` preserves the current zoom/pan when it's
called for the sample already showing header-only markers (rather than
resetting to fully zoomed out) - a user who zoomed in to nail a loop
point precisely shouldn't get snapped back the moment audio happens to
arrive. It still resets for a genuinely different sample, since that
always goes through `clear()`/`set_header()` first.

### Diagnosing a load that silently does nothing

A user hit intermittent "double-click loads nothing" failures in real
interactive use that no scripted or `QTest`-simulated repro could
reproduce. Reading `~/.akaisds/editor_debug.log` from their actual session
(not a synthetic one - see "Debug logging for real-hardware issues" above)
showed the `BridgeWorker`/`DemoBridge` layer itself was never at fault:
zero `FAILED` entries and zero overlapping `START`/`END` pairs across the
whole session - every call that was actually made completed cleanly and
serially. That points upstream of any bridge call, to whether a
double-click is even being recognized/delivered as one in the first place -
not something `debug_log.py`'s existing bridge-call logging could ever
show, since nothing gets that far. `WaveformView.mouseDoubleClickEvent` and
`ProgramEditorWindow._load_sample_waveform` now both log to the same
shared logger at every entry/early-return/fetch-result point specifically
so a future occurrence leaves a trace of exactly where it stopped, instead
of the investigation starting from zero again.

**Follow-up, from that same logging**: a later session hit it again, and
this time the log showed something concrete - `_load_sample_waveform`
kept being entered (double-clicks were being recognized fine, the GUI
thread was completely responsive) but the corresponding `sample_detail`
job never produced a single bridge-layer log line - not even a START.
`BridgeWorker.run()`'s dispatch call sat *outside* any try/except: an
exception escaping `_dispatch()` (from a bug in a handler itself, not the
bridge calls that handler's own try/except already guards - anything not
anticipated by that handler's own `except` clause) propagates all the way
out of `run()` and silently kills the OS thread for the rest of the app's
life. The GUI stays completely responsive throughout (nothing about the
main thread is affected), `submit_*()` keeps right on appending to
`self._queue` from the GUI thread, and nothing ever pops from it again -
every future request just sits there forever, no error, no crash, nothing
in the log. `BridgeWorker._safe_dispatch` is the fix: wraps `_dispatch()`
in a try/except that logs (`BridgeWorker: unhandled exception
dispatching...`) and lets the loop continue instead of dying. See
`test_worker_survives_a_job_whose_dispatch_raises_outside_its_own_handler`
in `tests/test_program_editor_bridge.py`, which forces exactly this by
queueing an unroutable job kind directly and confirming the worker is
still alive and processes the next real one. If you ever see the
"double-click recognized, GUI responsive, zero bridge-layer log lines"
pattern again, check this log line first - it now names which job and
carries the actual traceback, rather than needing to be re-diagnosed from
scratch.

### Progressive waveform loading during a live SDS dump

The audio envelope used to only ever appear once, fully formed, the
instant a whole multi-minute SDS transfer finished - `set_waveform` was
the only way anything landed in `WaveformView._samples`. As of 2026-09-22
it fills in live instead, left-to-right, in step with the transfer that's
still in progress (real hardware or demo mode's own simulated one), not
just a progress bar moving.

`WaveformView.begin_live_capture()` switches from header-only
(`_samples is None`) to an empty, growing list right before a fetch
starts (`ProgramEditorWindow._load_sample_waveform`, called once
regardless of demo/real mode, requires `set_header` to have already run -
see the fallback header-fetch branch there, which now calls `set_header`
itself for the rare case `_on_sample_detail_loaded` hasn't beaten it to
it). `append_live_samples(chunk)` then extends that list and repaints on
every call. `WaveformView._rebuild_envelope` clips to
`min(view_start + view_length, len(self._samples))` and sizes the
envelope's pixel width proportionally to how much of the current view is
actually loaded - **not** the view's full width fed a short sample list,
which would stretch a half-loaded prefix to fill the whole canvas instead
of visibly occupying only its own left fraction. Once a sample is fully
loaded this clip is a no-op (`len(self._samples) == frame_count` is
always `>= view_start + view_length`), so this is the exact same
envelope math the fully-loaded case always had.

Two independent producers feed `append_live_samples`, both driven from
`ProgramEditorWindow`:

- **Real hardware**: `SamplerController.sample_chunk_received` (new
  `Signal(list)`) - `_on_receive_data_packet` decodes each just-accepted
  SDS data packet's own words immediately (rather than waiting for
  `_finish_receiving`'s one all-at-once decode of everything) and emits
  them, scaled to the same 16-bit-equivalent range every other sample
  value on this page uses via `sds_encoder.scale_sample_to_16bit`
  (matches `write_wav_file`'s WAV-round-trip scaling exactly - see its
  own comment for the 8-bit case's algebra). This is purely additive -
  `receive_progress`/`sample_received`/`receive_finished` are emitted
  exactly as before, at the same points, with the same payloads. The
  Transfer Dashboard's own bulk-receive flow (`dashboard.py`) never
  connects to `sample_chunk_received` and is completely unaffected;
  `_fetch_sample_audio_blocking` connects/disconnects it the same
  transient way it already did for `receive_progress`.
- **Demo mode**: `_fetch_demo_sample_audio` has no packets of its own (the
  whole fixture file is already in hand from the read at the top of the
  function) - it just calls `append_live_samples` with the newly-revealed
  slice on the same ~200ms-tick cadence its progress bar already ticks
  on, so evaluating this feature without hardware shows the real thing,
  not just its progress bar.

`WaveformView.set_waveform`'s own `preserve_view` check used to require
`self._samples is None` (proof nothing had been drawn yet) - that's no
longer true the moment `begin_live_capture` runs, so the check is just
`self._frame_count == len(samples)` now (frame_count equality alone was
always the real proof of "still the same sample"; a clear()/set_header()
call for a genuinely different sample resets `_zoom`/`_view_start` to
defaults before `set_waveform` ever runs, so a coincidental frame_count
match there is harmless, not stale).

`WaveformView.paintEvent`'s bottom hint text ("Double-click to load
waveform audio (slow)" / "Loading…") used to sit pinned to the bottom
edge specifically to avoid the big centered placeholder text overlapping
the markers - it's vertically centered now (a user found bottom-alignment
looked wrong once the markers were the only other thing on the canvas);
this was never actually a collision risk since the markers' own triangle
handles only occupy the very top few px. The condition for showing it
also changed from `self._samples is None` to `not self._envelope`, so it
correctly keeps showing "Loading…" through the (typically brief) gap
between `begin_live_capture` running and the first chunk actually
landing, instead of going blank the instant `_samples` becomes `[]`.

**Follow-up bug, caught by a user's screen recording the same day**: the
envelope visibly finished loading well before the progress bar did, then
the whole waveform snapped/rescaled (markers included) the moment the
"real" audio landed. Root cause: `_markers_from_header`'s demo branch
substituted an arbitrary nominal placeholder (`values["SLNGTH"] or
20000`) for `frame_count` whenever `DemoBridge`'s own all-zero header
came back - fine as a placeholder right up until progressive loading gave
it a job to do (`begin_live_capture`'s envelope-proportion math treats it
as the live-capture *total*). `tests/test_audio.wav` (the demo audio
fixture `_fetch_demo_sample_audio` actually loads) is ~31000 frames, not
20000, so the envelope reached "loaded_end >= view_start + view_length"
(i.e. visually 100% full) at roughly 20000/31000 ≈ 64% of the way through
the real transfer, then `set_waveform` replaced everything with the real
31000-frame total once the transfer actually finished, and since
`preserve_view`'s frame_count check no longer matched, every marker's
on-screen x-position recalculated against the new (larger) denominator
mid-view - read as a sudden jump/redraw. Fixed by
`ProgramEditorWindow._demo_sample_frame_count(sample_index)`, which
predicts the REAL length `_fetch_demo_sample_audio` will load (a cheap
`wave.open(...).getnframes()` header peek, or - fixture missing -
`_synthesized_demo_frame_count`, split out of
`_synthesize_demo_sample_audio` specifically so the two can't drift
apart) and is now what `_markers_from_header`'s demo branch uses instead
of the old placeholder. `_markers_from_header` takes `sample_index` now
(both call sites pass it) - it didn't need it before this fix.
`test_demo_header_frame_count_matches_the_eventual_loaded_audio_length`
and `test_progressive_load_never_looks_complete_before_the_last_chunk_in_demo_mode`
in `tests/test_program_editor_window.py` pin this down directly (both
fail against the old placeholder - verified by temporarily reverting the
fix and re-running them). This was demo-mode-only: on real hardware
`_frame_count` and the live transfer's own reported length both trace
back to the same physical sample's `SLNGTH` on the same device, so they
were never expected to disagree the way an arbitrary demo placeholder
could.

### Sample loop type, root note, and rename/delete

Added 2026-09-22 alongside progressive waveform loading. Three additions to
the Samples tab, all mirroring existing patterns elsewhere on this page
rather than inventing new ones:

- **Loop type** (`sample_loop_type_combo`) writes `SPTYPE` (region
  `"sample"`, raw byte 0-3) - the SAMPLE's own playback/loop type, a
  *different* hardware field from `ZPLAY` (the per-keygroup-ZONE override
  of it, built where the Keygroup tab's own loop-type combo is - see
  `_LOOP_TYPE_OPTIONS`'s own comment). `_SAMPLE_PLAYBACK_TYPE_OPTIONS` is built
  from `_LOOP_TYPE_OPTIONS`'s own tooltips (indices 1-4, dropping ZPLAY's
  extra "As sample" choice - meaningless when SPTYPE is the very thing
  being edited) rather than retyped by hand: s3k.params' own SPTYPE
  `values=` labels ("Normal looping"/"Loop until release"/"No
  looping"/"Play to sample end") line up 1:1 with ZPLAY's "Loop in
  release"/"Loop til release"/"No loops"/"Play to sample end", so this
  keeps the two comboboxes' explanatory tooltip text identical without
  retyping it, or letting it silently drift if `_LOOP_TYPE_OPTIONS` is
  ever revised. `test_sample_playback_type_options_match_s3k_params_sptype`
  guards the label alignment the same way
  `test_mod_source_labels_match_s3k_params_minus_env3` guards
  `_MOD_SOURCE_LABELS`.
- **Root note** (`sample_root_note_spinbox`) writes `SPITCH` (region
  `"sample"`) via `NoteSpinBox`, same C3-is-middle-C convention as every
  other note field on this page (`LONOTE`/`HINOTE` etc. - see "Note
  names" above) - **but** ranged `21..127`, not the usual `0..127`:
  s3k.params declares SPITCH's own range that way ("21 to 127 represents
  A1 to G8" in *its* transcription's octave convention, which is NOT the
  same convention `midi_note_to_name` uses - note 21 displays as "A-1"
  here, not "A1"; this is expected, not a bug - see "Note names" above
  for why this app's own convention is the one to trust). `setRange(21,
  127)` must be called explicitly after constructing `NoteSpinBox()`,
  which otherwise defaults to `0..127`.
- **Rename/delete** on `sample_list_widget` mirror `program_list`'s own
  `ActionsContextMenu` + `QAction` shape exactly. `_DELETE_SHORTCUTS`
  is now a module-level constant shared by all three lists' delete
  actions, hoisted out of `__init__` so `_build_samples_tab` - which runs
  later and builds `sample_list_widget` itself - can reuse it.
  `_prompt_program_name`/a new `_prompt_sample_name` both go through a
  shared `_prompt_akai_name(title, label, current_name)` now (same
  `QInputDialog` + `AKAI_CHARSET` validator shape, just different title/
  label text). Rename writes `SHNAME` (region `"sample"`) and also has to
  push the new name into every zone's sample-choice combo
  (`_zone_combos`, populated in `_on_samples_loaded`) via
  `_update_zone_sample_combo_names` - same "a rename leaves a stale copy
  of the name in some other widget until the next full Refresh" bug class
  `_update_multi_program_combo_names` already exists to prevent for
  program renames, same `index + 1` offset for the blank `"-"`
  placeholder at combo index 0. Delete submits `DELS` via a new
  `BridgeWorker.submit_delete_sample`/`sample_deleted`/
  `sample_delete_failed` (mirrors `submit_delete_program`/
  `submit_delete_keygroup` exactly) and, on success, does a full
  `submit_sample_list()` reload rather than a targeted removal - indices
  shift after a delete, same reasoning as program/keygroup delete.
  **Unlike program delete**, there's no "last one is silently ignored by
  the hardware" restriction found in `s3k`/`s3ked` for `DELS` (`s3k.
  bridge.S3kBridge.clear_memory` empties the sample list down to zero the
  same way it empties programs down to one, and `DemoBridge.delete_sample`
  has no last-sample special case either) - so `_delete_sample_action`
  has no `count() > 1` guard the way `_delete_program_action` does. If
  real hardware is ever found to silently ignore deleting the last
  sample too, add the same guard back.

Both `SPTYPE` and `SPITCH` are in `_SAMPLE_DETAIL_FIELDS` now, so they
arrive in the same `sample_detail_loaded` payload every other
header-only field already does (no extra bridge round trip). `DemoBridge`'s
own all-zero header means `SPTYPE` reads back `0` ("Normal looping",
already meaningful) and `SPITCH` reads back `0` (below `21`, silently
clamped up to `21` by `NoteSpinBox`'s own range) - both benign,
neither needed the same kind of nominal-placeholder substitution
`_demo_sample_frame_count` needed for loop-point frame_count (see just
above): those markers actively break when collapsed to `(0,0,0,0)`
(`clamp_marker`'s neighbour bounds collapse too), these two fields don't.

### Trim/Reverse, and duplicate/timestretch/resample (s3k/s3ked capability audit)

Added 2026-09-22. Before building this, checked whether `s3k`/`s3ked`
already provide any of: sample/program/keygroup duplication, hardware
time-stretch, or hardware resampling. **None exist.** `s3k/messages.py`'s
only structural/destructive message classes are `DeleteProgram`/
`DeleteKeygroup`/`DeleteSample` - no `Copy`/`Duplicate`/`Clone` class
anywhere, confirmed by grepping both packages for "copy"/"duplicate"/
"clone" (the only hits are license headers and prose about duplicate
*names*, an unrelated problem - see `s3k/analysis.py`'s `ambiguous()`).
`SSRATE` is a plain metadata byte in the sample header (`s3k/params.py`) -
writing it changes what rate the hardware *declares*, not the actual
audio data; there is no DSP resample or time-stretch operation anywhere
in either package. This repo's own `sds_encoder.resample_to_target_rate`
is the only real resampling in this whole stack, and it's client-side
(runs before a send, already used for generic-device sends) - if
duplicate-with-resample or duplicate-plain is ever wanted, it reuses the
same send pipeline described below; time-stretch (changing speed without
changing pitch) would need genuinely new DSP work, unrelated to anything
here.

**Trim** (cut to the current Start/End markers) and **Reverse** both
needed a way to get modified audio back onto the hardware - something
neither `s3k` nor `s3ked`'s own TUI app (`s3ked/app.py`, pure
parameter/header editor, zero sample-audio capability - checked directly)
has ever done. The only audio-replace mechanism anywhere in this stack is
this repo's own `SamplerController.send_file_queue` (the Transfer
Dashboard's real Send path, already tested against hardware) plus
`BridgeWorker.submit_delete_sample` (added last session).

The math (`core/sample_editing.py`, pure functions, no Qt/MIDI):
`trim_samples`/`reverse_samples` both take/return the same four markers
`WaveformView.markers()` already uses. Trim keeps `samples[start:end+1]`
and re-bases the loop markers into the new, shorter buffer (they're
always inside `[start, end]` on entry - `clamp_marker` guarantees
`start <= loop_start <= loop_end <= end` - so a trim never clips into an
active loop, only shifts it). Reverse mirrors every marker (not just the
loop ones - Start/End too) around `frame_count - 1 - i`; loop *length*
(`loop_end - loop_start`) is invariant under this, only its position
mirrors - a useful self-check, exercised directly in
`tests/test_sample_editing.py`.

**Why send-then-delete, and why a temp name** (`_perform_sample_edit_real`
in `program_editor_window.py`): two failure modes had to be designed
around, not just the "needs a confirmation dialog" the user already knew
about:

- Deleting the original before confirming the replacement sent
  successfully would risk losing the sample outright if the send then
  failed for any reason - unacceptable for what's meant to be a routine
  edit. So the replacement is sent FIRST, and the original is only
  deleted once that's confirmed resident.
- Sending the replacement under the ORIGINAL's own name (skipping a temp
  name) would risk a subtler, worse failure: verified via `s3k/
  analysis.py`'s own docstrings (`s3k` trusts these as measured fact, not
  inferred - see this file's own convention) that **keygroup zones
  resolve a sample by NAME, live, against whatever resident sample
  currently carries it** (`s3k/params.py`'s `SBADD1..4` - the field
  that looks like a stored pointer - is documented as "Calculated ...
  (internal)", i.e. derived from a name lookup, not authoritative
  itself) - and that the hardware enforces **no uniqueness** on that name
  (measured, `analysis.py`'s `ambiguous()`: "renaming one resident sample
  to another's name left ten resident with two carrying it... cannot be
  resolved to one of them"). Two samples briefly sharing the original's
  name during the send/delete window would make every zone using that
  name genuinely ambiguous for however long that window lasts - a worse,
  quieter failure than the data-loss one above. `_sample_edit_temp_name`
  (module-level, `program_editor_window.py`) sidesteps this entirely by
  never using the original's name until the original is already gone:
  truncates to leave room for a fixed `"-TMP"` suffix (`AKAI_CHARSET` has
  no underscore).

So the sequence is: send under `<name>-TMP` -> confirm resident -> delete
the original -> reload and find `<name>-TMP`'s new index (it shifted once
the original, at a lower index, was deleted - looked up by name, the same
way the hardware itself resolves zones, never assumed from the send
above) -> `SHNAME`-only rename back to the original name (doesn't touch
audio - see `akai_sysex.build_rename_sample_request`'s own comment) ->
reload once more and explicitly select the result. Every individual step
(send, delete, SHNAME write) is a previously-tested primitive; this exact
sequence is new and has not been exercised against real hardware - watch
the first real run closely. Demo mode (`_perform_sample_edit_demo`)
bypasses all of this - `SamplerController` has no demo mode of its own
(see "Developing without hardware") - mutating the cache/`WaveformView`
directly instead, paced the same way `_fetch_demo_sample_audio` already
is so evaluating this without hardware still shows the real time cost of
a full resend.

**Real bug found and fixed while building this**: `_wait_for_any_signal`
used to be called as `submit_*(); which, args = self._wait_for_any_signal
(signals, timeout_ms=...)` - connect-*after*-submitting. Every signal it
waits on fires from a background thread (`BridgeWorker`) or async MIDI
callback (`SamplerController`), and nothing guaranteed the GUI thread
reached the `connect()` calls before that thread finished and emitted.
Against real hardware this window is normally unhittable (a real SysEx
round-trip takes real milliseconds-plus); against `tests/test_program_
editor_window.py`'s `FakeBridge.delete_sample` (a plain dict/list
operation with none of that latency) it was **reliably lost, every run** -
found via `test_reverse_sample_real_mode_happy_path_sends_deletes_and_
renames` hanging for a full 20s timeout despite the delete having
actually succeeded (confirmed: `bridge.sample_list()` was already
correct by the time the wait gave up - the emission simply arrived before
anything was listening for it). `_wait_for_any_signal` now takes a
required `start` callable, invoked only after every listener is
connected - every caller (`_fetch_sample_header_blocking`,
`_fetch_sample_audio_blocking`, and the four new waits in
`_perform_sample_edit_real`) was updated to move its `submit_*()`/
`send_*()`/`receive_*()` call into `start=`. A `start()` that returns
exactly `False` (not `None`, not omitted - `send_file_queue` declining to
start is the one real case) makes `_wait_for_any_signal` skip its own
wait outright, rather than hanging until `timeout_ms` - or, for the send
step's own `timeout_ms=None`, forever - waiting for a signal that will
now never come. If you add a new blocking wait anywhere on this page,
this ordering is not optional - see the method's own comment.

### Loop-point markers push each other instead of stopping dead

Added 2026-09-22, per direct user request. `WaveformView` used to have
`clamp_marker(order, index, frame, values, frame_count)` - moving one
marker (Start/Loop Start/Loop End/End) could never cross a neighbour,
just stopped dead at whatever position the neighbour currently held. This
is now `push_marker`, same file: moving a marker far enough to collide
with a neighbour pushes that neighbour along instead, cascading - drag
"end" left far enough and it pushes loop_end, which can in turn push
loop_start, same as a Newton's cradle. `start <= loop_start <= loop_end
<= end` is still always the result, just enforced by shoving neighbours
along rather than refusing to cross them. `clamp_marker` is gone
entirely, not kept alongside - nothing else used the "stop dead" behaviour
once both of its call sites (`WaveformView.mouseMoveEvent`'s drag and
`WaveformView.set_marker`, what the marker spinboxes call on every typed/
stepped value) switched to `push_marker`, so keeping it around would've
just been dead code with misleadingly still-passing tests.

**The write side had to change with it.** A push can move up to all four
markers from ONE user action (one drag, one typed spinbox value), so
`program_editor_window.py`'s `_schedule_marker_write` can no longer infer
which SysEx field(s) need writing from *which marker the user directly
touched* - it now takes the FULL marker dict from both *before* and
*after* the edit and writes exactly the field(s) whose value actually
changed (`SSTART` if `start` moved, `SMPEND` if `end` moved, `LOOPAT1`+
`LLNGTH1` together if EITHER loop edge moved, same pairing reasoning as
before - they jointly encode the loop region). Getting this wrong doesn't
crash anything, it silently desyncs the hardware from what's on screen: a
push that visibly moves `loop_end` right along with `end` but only writes
`SMPEND` would leave the sampler still looping at its old (or, worse, mid-
edit garbage) `LOOPAT1`/`LLNGTH1` position while the UI shows it moved.
`_on_waveform_marker_committed` (drag release) gets "before" from the
sample's own cache entry (`_sample_waveform_cache[sample_index]`) - it's
only ever updated on a COMMIT, never on the live per-mouse-move
`markers_changed` emissions a drag fires continuously throughout, so it's
exactly the state as of whenever the drag started.
`_on_marker_spinbox_changed` gets "before" from `WaveformView.markers()`
read immediately before calling `set_marker`, for the same reason.
`_flush_marker_write` (the `editingFinished`/drag-release "commit now,
don't wait out the debounce" path) dropped its `which` parameter entirely
- it just unconditionally flushes all four marker-related debounce keys
now (`_flush_write` is already a no-op for a key with nothing pending),
simpler than threading "which of the four did `_schedule_marker_write`
actually touch" back out to the caller.
`tests/test_program_editor_window.py`'s `test_marker_spinbox_push_writes_
every_field_that_actually_moved`/`test_drag_release_push_writes_every_
field_that_actually_moved` pin this down directly - both fail if the
write side falls back to inferring fields from `which` alone.

## Testing

`TESTING.md` currently undersells this a little - as of this note there's also
`tests/test_program_editor_bridge.py` (BridgeWorker + LoggingBridge, all pure
logic, no Qt event loop needed) and `tests/test_program_editor_window.py`
(real offscreen `QApplication`, actual widgets). The project's own stated
philosophy (from the user, not just inferred): don't worry about testing the
UI exhaustively, but definitely cover core functionality and any bug you fix -
see `TESTING.md`'s "why several tests exist" section for the precedent.

`uv run pytest tests/ -v` runs everything in well under a second.
