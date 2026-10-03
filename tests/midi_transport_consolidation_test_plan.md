# MIDI transport consolidation - real-hardware test plan

**Status: validated and now the default** (see the sign-off note at the
bottom). This document is kept as the record of what was tested before that
happened, and as the plan to re-run (at least the relevant tests) after any
future change to `core/midi_transport.py`, `core/midi_manager.py`'s
`shared_transport_enabled()`/port-opening, or `program_editor_bridge.py`'s
`connect()`/`BridgeWorker`.

## What this is testing

Before this, the Transfer Dashboard (`SamplerController`, via `MidiManager`,
via `mido`) and the Program Editor (`BridgeWorker`, via `S3kBridge`, via
`python-rtmidi`) opened two **independent** connections to the same physical
MIDI port whenever the editor was open. This was a real, already-confirmed
source of bugs - see AGENTS.md's "Follow-up, first real-hardware session: a
confirmed dual-connection MIDI race" - and it's also what made it unsafe to
add a "Settings" menu to the Program Editor (`ui/settings_dialog.py`'s own
diagnostics already have to release/reopen `MidiManager`'s ports before
running a test, precisely because two open connections to one port is
unsafe).

`core/midi_transport.py` adds a **consolidated** transport that lets both
sides share ONE real connection instead. It's decided by
`core.midi_manager.shared_transport_enabled()`, now **ON by default**
(config.json's `"shared_midi_transport"` key - manual-edit-only, no
Settings UI for it on purpose). `AKAISDS_SHARED_MIDI_TRANSPORT`, if
explicitly set in the environment, overrides that either direction - this
is your instant way to test the OFF path as a control, or force ON
regardless of config:

```sh
export AKAISDS_SHARED_MIDI_TRANSPORT=1   # force on
export AKAISDS_SHARED_MIDI_TRANSPORT=0   # force off (the pre-default-flip behaviour)
unset AKAISDS_SHARED_MIDI_TRANSPORT      # defer to config.json (now on by default)
```

The automated test suite (`uv run pytest tests/`) already covers the pure
logic - `tests/test_midi_transport.py`, `tests/test_midi_manager.py`,
`tests/test_program_editor_bridge.py`'s `connect()`/`set_bridge()` tests,
`tests/test_app_config.py`'s shared-transport-key tests. **None of that
touches real hardware or a real rtmidi backend for anything
timing-sensitive** - it can't. This document is what closes that gap - the
numbered tests below are what was actually run before flipping the
default.

## Before you start

- [ ] Note your sampler's exact make/model and firmware version if shown on
      its own screen (useful context if something goes wrong).
- [ ] Have `~/.akaisds/akaisds.log` open in a `tail -f` in a terminal the
      whole session - every port open/close/callback/write in the new code
      logs through `core/debug_log.py` (`SharedMidiInput: ...`,
      `SharedMidiOutput: ...`, `MidiManager: ...`). If anything below
      behaves unexpectedly, this log is the first thing to check, and
      worth saving a copy of alongside your notes for that test.
- [ ] Confirm which two things you're actually testing don't get confused
      with each other: (1) the shared-transport connection layer itself
      (this document, Tests 1-6/8), and (2) the Program Editor's own
      `&Hardware > Settings...` menu item, which only exists when the
      shared transport is active and is specifically what Test 7 covers -
      see that test's own description for what it's checking.
- [ ] Run the automated suite once first as a sanity check before touching
      hardware: `uv run pytest tests/ -q` should show all tests passing
      (874 at the time of writing). If this fails, stop - something's
      wrong before hardware is even involved.

## How to launch with the flag on

```sh
export AKAISDS_SHARED_MIDI_TRANSPORT=1
cd /home/martin/Development/AKAISDS
uv run python src/main.py
```

Then in the app: **Settings → Settings tab** (or the dashboard's own MIDI
Settings button), pick your sampler's input and output ports, same as
always. Watch the log for `MidiManager: opened input ...` /
`MidiManager: opened output ...` - with the flag on, these lines come from
`SharedMidiInput`/`SharedMidiOutput` (check `core/midi_transport.py` if you
want to confirm which class actually logged it - both classes log with
their own class name as a prefix, so `grep -i "SharedMidi" ~/.akaisds/akaisds.log`
should show hits once a connection is open).

For every test below, unless it says otherwise: **do it once with the flag
ON, and if anything's ambiguous about whether a symptom is new, repeat the
same steps with the flag OFF (`unset AKAISDS_SHARED_MIDI_TRANSPORT`) as a
control** - some of these tests (loop point edits, sample transfers) may
have their own pre-existing quirks unrelated to this change; the control
run is what tells you which is which.

---

## Test 1 - Basic connectivity sanity check

Confirms the shared transport works AT ALL before testing anything harder.

1. Launch with the flag on, connect MIDI Settings to your sampler.
2. Dashboard: run a basic Identity Request or Loopback Test from Settings
   (whichever this build's Settings dialog offers) against the newly-opened
   connection.
3. Dashboard: send one small file to the sampler (a short one-shot sample),
   confirm it lands and looks correct on the sampler's own screen.
4. Dashboard: receive one sample back (SDS dump), confirm the waveform
   looks correct once loaded.
5. Open the Program Editor (`Ctrl+E` or the Window menu). Confirm the
   Programs list, a Program's Keygroups list, and the Samples tab's own
   sample list all populate correctly.
6. Load a sample's waveform in the Program Editor's Samples tab
   (double-click to load audio) - confirm it loads and looks correct.

**Pass**: all of the above work exactly as they do with the flag off.
**If anything here fails**, stop - don't proceed to the harder tests below
until this baseline works.

---

## Test 2 - The actual race, deliberately reproduced

This is the most important test. It's reproducing the EXACT scenario that
caused the already-documented bug
(`SampleList: expected command 0x05, got 0x16`) - with the flag on, this
should no longer happen, because there's only one connection to cross-wire
replies on.

1. Launch with the flag on, connect both windows (Dashboard connected,
   Program Editor open).
2. In the Program Editor's Samples tab, open the Slice Editor on a sample
   with several slice markers placed (8-16 slices is plenty), and check
   "Create new program with slices" so the export is as large/long-running
   as possible.
3. Click Export. **While that batch export is running** (it can take a
   while for many slices), interact with the Program Editor's OTHER tabs -
   click through several different Keygroups on the Programs tab, or
   change a knob value - so BridgeWorker is also issuing its own requests
   on the SAME connection at the same time as the export's own reload
   chatter.
4. Watch the log for any `SampleList: expected command 0x05, got 0x16`-
   shaped error, or any `_reload_sample_list_with_retries` retry message,
   or any `keygroups_load_failed`/`sample_detail_load_failed` signal in the
   log around the same timestamps as the export.
5. Repeat this test 3-4 times - races are probabilistic, one clean run
   doesn't prove it's fixed.

**Pass**: the export completes successfully every time, with no reply
cross-talk errors in the log, even while you're actively clicking around
the Program Editor during the export.

**For comparison** (optional but valuable): run this exact same test with
the flag OFF once, to confirm you CAN still reproduce the original race
that way (if you can't reproduce it even with the flag off in a quick
attempt, that's fine - it was already known to be intermittent - but if you
easily reproduce it with the flag off and never see it with the flag on
across several attempts, that's strong confirmation this fix works).

---

## Test 3 - Long real audio transfer integrity

The shared input's callback now feeds two consumers instead of one - this
checks nothing gets dropped or corrupted during sustained, real-timing
traffic.

1. Flag on, both windows connected.
2. Pick your LARGEST resident sample (or load a big new one), and do a full
   SDS receive of it into the Program Editor's Samples tab waveform view
   (double-click to load audio) - ideally something that takes at least a
   minute or two to fully transfer.
3. While it's transferring, watch the progressive waveform fill in - it
   should fill in smoothly, left to right, with no visible gaps or
   sudden jumps that would suggest dropped packets.
4. Once loaded, spot-check the waveform against what you know the sample
   sounds like (or against a receive of the same sample done via the
   Dashboard's own transfer with the flag OFF, if you want a byte-level
   comparison) - the two should be identical.
5. Repeat once with a SEND instead (queue a large file from the Dashboard,
   send it to the sampler) while the Program Editor is also open and idle
   (not necessarily doing anything, just connected) - confirm the send
   completes and the sampler's own screen shows the correct sample.

**Pass**: transfer completes, sounds/looks correct, no dropped-packet
symptoms, no errors in the log.

---

## Test 4 - Output writes don't interleave

This is the one thing the consolidation makes *possible* that couldn't
happen before (two logical senders, one wire) - `SharedMidiOutput`'s own
write lock is supposed to prevent it, but only real hardware can actually
show a corrupted/rejected frame if the lock has a bug.

1. Flag on, both windows connected.
2. Start a Dashboard file SEND (pick a reasonably large file, so it takes a
   few seconds) or a batch Slice Editor export from the Program Editor.
3. **While that's actively sending**, from the OTHER window, trigger
   something that also writes to the sampler - e.g. while a Dashboard send
   is in progress, edit a knob value in the Program Editor (a filter
   cutoff, a pan knob - anything that fires a hardware write); or while a
   Slice Editor export is running, use the Dashboard to send a Program
   Change or another small file.
4. Watch the sampler's own screen/behavior for anything that looks like
   corruption - a stuck/frozen SysEx transfer indicator, a garbled program
   name, a value that didn't take, or (worst case) the sampler needing a
   power cycle to recover.
5. Check the log for any decode/parse errors around the same timestamps
   (a corrupted frame would likely show up as a parse failure on
   whichever side received the garbled reply, if it shows up at all).

**Pass**: both writes land correctly, nothing on the sampler's screen looks
corrupted, no decode errors in the log. **This is the test most worth
repeating several times** with different combinations of "what's happening
on each side" - it's specifically probing for a race, and a race that
doesn't show up on the first few tries isn't necessarily absent.

---

## Test 5 - Editor open/close/reopen cycles

The Editor's connection now has a different lifetime than the Dashboard's
(which stays open for the whole session) but shares the same underlying
ports.

1. Flag on. Connect the Dashboard, open the Program Editor, close it
   (`Ctrl+T` or the Window menu back to Dashboard), reopen it. Repeat this
   open/close/reopen cycle 5-10 times in a row.
2. After each reopen, confirm the Program/Keygroup/Samples lists still
   populate correctly (not stale, not empty, not erroring).
3. On the LAST reopen, do a real action (load a sample, edit a value) to
   confirm the connection is still fully functional, not just "looks
   connected but doesn't actually work."
4. Watch the log across all these cycles for anything like a repeated
   "port already open" error, a growing pile of `SharedMidiInput: opened
   input` lines without matching `closed input` lines (a leak), or any
   delay that gets progressively worse each cycle (would suggest something
   isn't being cleaned up).

**Pass**: every reopen works identically to the first one, log shows a
clean open/close pair for the editor's own lifecycle each time (or, if the
editor shares the Dashboard's already-open ports rather than opening its
own per AGENTS.md's existing "reuses main_window.sampler_controller for
sample audio" pattern, then there should be no NEW open/close pair per
editor cycle at all - note which behavior you actually observe here, since
it's useful information either way).

---

## Test 6 - App quit / crash-class regression check

AGENTS.md documents a real macOS crash this app already fixed once
(`QThread::~QThread()` calling `qFatal()` when a stale thread was still
mid-call during GC - see "BridgeWorker" section). The shared-port lifecycle
is new surface for the same CLASS of bug to reappear in, since now multiple
things reference one underlying connection with different lifetimes.

1. Flag on. Connect, open the Program Editor, start something that takes a
   moment (loading a big sample's waveform, or a Slice Editor export), and
   **while it's still in progress**, quit the whole app (Cmd+Q / close the
   main window) - don't wait for the operation to finish first.
2. Confirm the app exits cleanly - no crash dialog, no force-quit needed,
   no zombie process left running (`ps aux | grep -i akaisds` afterward,
   or check Activity Monitor).
3. Repeat 5+ times, varying WHAT was in progress when you quit (a receive,
   a send, a keygroup load, an idle connection with nothing happening).
4. Also test just closing the Program Editor window (back to Dashboard)
   mid-operation, without quitting the whole app, same way.

**Pass**: every quit/close is clean, no crash, no hang, no leftover
process. **This is a macOS-specific concern per AGENTS.md** - if you're
testing on Windows/Linux this specific crash class is less likely to
reproduce there, but the open/close-cleanliness check itself is still
worth doing on any platform.

---

## Test 7 - Settings dialog against the shared connection

The Settings dialog's Apply/OK and its Identity Request/Loopback Test
diagnostics all call `MidiManager.open_input`/`open_output` again (the
diagnostics via their own release-then-restore pattern - see
`ui/settings_dialog.py`), which under the shared transport REPLACES
`midi_manager.raw_input`/`raw_output` with new objects rather than mutating
the old ones. The Program Editor's own `S3kBridge` was built from whichever
objects were open at connect() time, so without a fix it would silently
keep talking to now-closed ports the instant any of this happens.
`ProgramEditorWindow._open_settings_dialog` calls `_reconnect_shared_bridge`
once, unconditionally, right after the dialog closes - that's the fix.
Only reachable from the Program Editor's own `&Hardware > Settings...`
menu, since Dashboard and Program Editor are mutually exclusive (only one
visible at a time), so the Dashboard's own Settings action is never
actually reachable while the editor is the active window.

**This is also a regression test for TWO real, confirmed crashes** (not
hypothetical - both happened during this feature's own real-hardware
testing, same `RtMidiOut::sendMessage` SIGSEGV signature on the
`BridgeWorker` thread, two different triggers):

- **Crash 1**: an earlier version reacted to `MidiManager.connection_
  changed` directly instead of once after the dialog closes, which
  rebuilt the bridge from a transient PARTIAL port state mid-diagnostic
  and briefly opened a THIRD connection to the same port while
  `BridgeWorker` could still be mid-send on the one being closed - fixed
  by reacting once, after the dialog fully closes (`_reconnect_shared_
  bridge`), plus `core/midi_transport.py`'s `SharedMidiOutput`/
  `SharedMidiInput` `close_port()` now taking the same lock `send_
  message()`/the message callback do. Also fixed the UI freezing solid
  during a diagnostic (a raw blocking wait with no event-loop pumping) -
  the reconnect now waits via `busy_changed`, not `wait_until_idle()`.
- **Crash 2**: found even AFTER fixing Crash 1, from the most ordinary
  path possible - open Settings, change nothing, click OK. `main_tabs`
  wasn't frozen while Settings was merely open, only during the
  reconnect afterward - closing the dialog let a deferred sample-list
  selection event through, submitting a new `BridgeWorker` job that
  dispatched against the OLD (already-deleted) bridge in the gap before
  the reconnect got a chance to run. Fixed by freezing the window (and
  confirming `BridgeWorker` is actually idle) BEFORE the dialog ever
  opens, not just after it closes - see `_open_settings_dialog`'s own
  comment.

1. Flag on (or just the new default), Program Editor open.
2. **The exact Crash 2 repro - do this first**: open a sample's waveform
   in the Samples tab, open `&Hardware > Settings...`, change NOTHING,
   click OK. No crash is the pass condition - this is the simplest
   possible case and the one that actually segfaulted.
3. Open `&Hardware > Settings...`, run the Identity Request test (or
   whichever hardware test this build offers). Confirm the UI stays
   RESPONSIVE the whole time (this used to freeze solid - if it does
   again, that's Crash 1 regressing), then close Settings. Confirm the
   editor is STILL working immediately after (load a sample, browse
   keygroups) - watch for the brief "Waiting for pending hardware
   requests..."/"Reconnecting to the sampler..." status messages.
4. Repeat step 3 but with Apply/OK (change nothing, or actually change a
   port) instead of a diagnostic.
5. Repeat both with the Loopback Test too, and repeat the whole sequence
   2-3 times. No crash, ever, is the actual pass condition here.

**Pass**: no crash, no freeze, diagnostics succeed, and the Program
Editor's own connection comes back fully working afterward every time.

---

## Test 8 - Generic MIDI SDS device (if you have one)

Only if you have access to a generic (non-Akai) MIDI SDS device to test
against - the app has a separate `device_type` path for this
(`_update_open_editor_enabled` in `ui/dashboard.py` gates the Program
Editor to Akai-only, so this is really just testing the DASHBOARD's own
`SamplerController`/`MidiManager` path in isolation against the shared
transport, without the Program Editor in the picture at all).

1. Flag on, select "Generic MIDI SDS" as the device type, connect.
2. Send and receive a sample, confirm it works identically to the flag-off
   case.

**Pass**: no different from testing against the Akai sampler - this is
mostly confirming `MidiManager`'s own send/receive path (used by both
device types) isn't somehow Akai-specific in a way that breaks here.

---

## If something fails

1. **Set `unset AKAISDS_SHARED_MIDI_TRANSPORT`** (or just don't export it)
   and confirm the SAME test passes with the flag off - this confirms
   whether the failure is actually caused by this change, or a pre-existing
   issue you've just now noticed.
2. Save a copy of `~/.akaisds/akaisds.log` from the failing run before it
   rotates/grows further, and note the exact wall-clock time the failure
   happened so the relevant log lines can be found.
3. Note exactly what you were doing on BOTH windows at the time (this is
   almost always a concurrency issue if it's real, so "what else was
   happening at the same time" is the most important detail).
4. Don't try to work around it by hand-editing the shared connection
   state - report it as-is so the actual root cause can be found, the same
   way every other real-hardware bug in this app's history has been
   diagnosed (see AGENTS.md's own "Debug logging for real-hardware issues"
   section - this is exactly that same process, applied to a new area).

## Results log

Copy this table and fill it in as you go tonight:

| Test | Pass/Fail | Notes | Log excerpt saved? |
|------|-----------|-------|---------------------|
| 1. Basic connectivity | | | |
| 2. Dual-activity race (x3-4 attempts) | | | |
| 3. Long transfer integrity | | | |
| 4. Output write interleaving (x3+ attempts) | | | |
| 5. Editor open/close cycles | | | |
| 6. App quit mid-operation (x5+) | | | |
| 7. Settings diagnostics | | | |
| 8. Generic MIDI SDS (if available) | | | |

**Sign-off**: the default flipped to on (`app_config`'s
`_DEFAULT_SHARED_MIDI_TRANSPORT`/`"shared_midi_transport"`, see
`core/midi_manager.shared_transport_enabled()`) once Tests 1-6/8 came back
clean. The Program Editor's own `&Hardware > Settings...` menu item
(Test 7) went through TWO real-hardware rounds, each finding a genuine
SIGSEGV (plus a UI freeze on the first) - see that test's own writeup for
both. Fixed in `core/midi_transport.py`'s `close_port()` methods and
`ProgramEditorWindow._open_settings_dialog`/`_reconnect_shared_bridge`.
**Test 7 needs a clean re-run against both fixes before this whole
feature is actually signed off** - if you're reading this before that
re-run happened, treat the Settings-in-editor menu item as unverified
even though the default is already on.
