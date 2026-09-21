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
  `bend_down_spinbox` uses `0..24` for the same reason. Unlike `K_FREQ`,
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
  `test_bend_down_spinbox_above_12_reaches_hardware_via_the_range_override`.
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

## Testing

`TESTING.md` currently undersells this a little - as of this note there's also
`tests/test_program_editor_bridge.py` (BridgeWorker + LoggingBridge, all pure
logic, no Qt event loop needed) and `tests/test_program_editor_window.py`
(real offscreen `QApplication`, actual widgets). The project's own stated
philosophy (from the user, not just inferred): don't worry about testing the
UI exhaustively, but definitely cover core functionality and any bug you fix -
see `TESTING.md`'s "why several tests exist" section for the precedent.

`uv run pytest tests/ -v` runs everything in well under a second.
