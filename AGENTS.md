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
