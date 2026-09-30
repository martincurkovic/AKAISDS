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
