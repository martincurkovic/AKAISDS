# Testing

AKAISDS has a pytest suite covering `core/`, `controller/`, and - since the
Program Editor window was added - a decent chunk of `ui/` too: both its
bridge/threading layer and a bunch of pure math pulled out of widgets
specifically so it could be tested. It still doesn't cover actual rendering
(painted pixels) or real hardware.

## Running the tests

```
uv sync
uv run pytest tests/ -v
```

This is the same command that the CI pipeline runs. Running the whole suite takes about 10 seconds.

## What's actually tested and why

`tests/test_akai_sysex.py` is the Akai specific SysEx protocol (SLIST, DELS, SDATA, rename, tuning offset).

`tests/test_sds_encoder.py` is the universal SDS byte encoding. Also contains real WAV/AIFF file I/O against some very small dummy files. Confirms for both AIFF and WAV.

`tests/test_midi_identity.py` is the universal MIDI identity request AND the Akai specific RSTAT and STAT request and response pair.

`tests/test_midi_manager.py` fakes out `mido` (and `PySide6.QtCore`, same trick as `test_sampler_controller.py` below) so the MIDI port-handling layer can be tested without any real MIDI hardware/ports.

`tests/test_app_config.py` is the settings persistence testing. Uses `monkeypatch`/`tmp_path` to redirect `CONFIG_PATH` to a temp file, so that way it never touches the user's real config file.

`tests/test_theme.py` is the stylesheet renderer which generates both light and dark themes. Also uses `monkeypatch/tmp_path`.

`tests/test_sampler_controller.py` is the big dawg test. This one might require some explanation cos it's very much not like the others. See below for details

`tests/test_midi_notes.py` and `tests/test_note_spinbox.py` are the MIDI note number <-> note name convention (e.g. `midi_note_to_name(60) == "C3"`) and its exact inverse parser, tested together since the two silently drifting apart is exactly what caused a real bug (see below).

`tests/test_program_editor_bridge.py` is `BridgeWorker` and `LoggingBridge` - the single-threaded, queued worker that owns every SysEx call the Program Editor makes. No Qt event loop needed; see "Program Editor testing" below for how.

`tests/test_program_editor_window.py` is the Program Editor window itself, against a `FakeBridge`. Needs a real (offscreen) `QApplication` since these are actual widgets, not pure logic.

`tests/test_program_editor_window_demo_bridge.py` drives the same window against the REAL `s3ked.demo.DemoBridge` instead of `FakeBridge` - see its own module docstring. `FakeBridge` duck-types whatever the app currently expects from s3k/s3ked, so it can never catch the pinned dependency (see `pyproject.toml`'s `s3ked` rev) renaming/removing a parameter, moving it to a different region, or narrowing a declared min/max - none of that touches a single `p.lookup()`/`encode_field()`/`decode_field()` call when the bridge underneath is hand-authored. This file is the actual safety net for bumping the `s3ked` pin: run it (and the two range-mismatch tests inside it, see below) before and after any pin bump.

`tests/test_dashboard_helpers.py`, `tests/test_knob.py`, `tests/test_keygroup_range_bar.py`, `tests/test_sample_info_dialog.py`, `tests/test_sample_settings_dialog.py`, `tests/test_qt_helpers.py` and `tests/test_envelope_graph.py` are pure logic pulled out of otherwise UI-heavy files - see "Pulling logic out of widgets" below.

`tests/test_dashboard.py` is `TransferDashboard` itself (widget-level, a real `MidiManager`/`SamplerController` pair, never a full `ApplicationWindow` since that touches the user's real `~/.akaisds/config.json`) - the Open Editor enabled-state logic and the dropped-file stable-copy fix below both live here.

`tests/test_dropped_files.py` is `core/dropped_files.py` in isolation (`tmp_path`-scoped, never the real `~/.akaisds/dropped_files`) - the session-scoped copy/cleanup mechanism a dropped or opened file goes through so a later Send doesn't lose it to some other app's own temp-file cleanup.

#### Why several tests exist

A number of tests in the suite are based on chasing down bugs during development, not just randomly "testing everything for funsies":

- `build_slist_request` once upon a time hard-coded the channel byte to `0x00`, this silently ignored the configured device ID which took me much longer than I care to admit to debug...
- Generic-device sends could get permanently stuck after the first send because `_awaiting_count_for_queue` was being set even when the generic SDS device never sends the SLIST request that would clear it
- A sample number counter bug that could cause two different files in the same batch to collide on the same hardware slot, causing many a headache
- A silly indentation bug that left ~40 lines of controller state initialisation nested in `set_device_type()` instead of `__init__`, meaning it just silently reset every time a user saved their MIDI settings
- The Program Editor used to spin up a fresh `QThread` per UI action against the sampler - fine against the demo bridge, but real hardware doesn't tolerate two SysEx calls in flight at once, and a stale thread's own Python object could get garbage-collected while it was still running, which Qt treats as fatal. Confirmed from an actual crash report before rewriting it as `BridgeWorker` - see the class's own docstring in `core/program_editor_bridge.py` and `AGENTS.md` for the full story.
- Refresh reloaded the current program's keygroups but never re-fetched the program list itself, so a program created on the hardware after the editor opened only ever showed up after closing and reopening the window
- Assigning a program to a Multis-tab part always played the first program in the list, regardless of which one was picked - traced to every program sharing MIDI program number (`PRGNUM`) 0, which is the common case for freshly created/independently loaded programs. Fixed by renumbering before the first Program Change is sent.
- The keygroup note-range display was one octave off (`C2` where the hardware's own front panel shows `C1`) - it was using the general-MIDI convention (note 60 = C4) instead of the S3000XL's own (note 60 = C3).
- Writing `bend_down_spinbox` (a `B_PTCHD` field) above 12 semitones used to fail against the pinned `s3k.params` rev's own declared range check (`0..12`), even though the widget itself allows up to 24 - AGENTS.md documented *why* the widget uses 0..24 (a hardware measurement contradicting the dependency's transcribed manual value), but the dependency itself hadn't caught up to that measurement, so real writes above 12 failed silently to a status-bar message with no UI rollback. Found while building `test_program_editor_window_demo_bridge.py` against the real dependency rather than `FakeBridge` (which can't see range checks at all), then fixed (not just documented) once the user reconfirmed the hardware measurement directly: `core/program_editor_bridge.py`'s `_HARDWARE_RANGE_OVERRIDES`/`_lookup_for_write` now patches a corrected copy of `B_PTCHD`'s `Parameter` (`dataclasses.replace`, not an edit to the dependency) in front of every write. See `test_program_editor_window_demo_bridge.py`'s `test_bend_down_raw_s3k_params_lookup_still_declares_the_narrower_0_to_12` (confirms the raw dependency is still unfixed, so the override is doing real work) and `test_bend_down_spinbox_above_12_reaches_hardware_via_the_range_override` (confirms the app-level fix).

If you're fixing a bug, consider whether it's worth adding a test in here too.

#### Fake QtCore technique

`test_sampler_controller.py` and `test_midi_manager.py` don't use the real PySide6 `QtCore` but instead use a `_FakeQObject`, `_FakeSignal` and `_FakeQTimer.singleShot` which fires immediately instead of waiting for a real event loop. This is one of the reasons why those tests are so fast, because there is no actual Qt code in the way and no timers to wait for.

#### Program Editor testing

The Program Editor's tests use two different techniques depending on what they're exercising:

- `BridgeWorker` (in `test_program_editor_bridge.py`) never needs a real background thread in tests - `submit_*()` queues a job and `process_pending()` drains it synchronously on the test thread, so its signals fire as plain direct connections. Fast and deterministic, same spirit as the fake-QtCore trick above but without needing to fake anything.
- `ProgramEditorWindow` (in `test_program_editor_window.py`) does use a real `BridgeWorker` thread, since it's testing actual widgets reacting to actual queued cross-thread signals. `wait_until_idle()` blocks until the worker has caught up with everything submitted so far; a delivered signal's slot still needs an event-loop pump after that (`_pump_until()` in that file). Any test that constructs a `ProgramEditorWindow` directly must call `editor._worker.stop(); editor._worker.wait()` in teardown, or the worker thread is still running when the test ends - exactly the kind of thing `BridgeWorker` exists to prevent.

#### Pulling logic out of widgets

A handful of widgets had real, bug-prone math sitting inline inside `paintEvent`/`mouseMoveEvent`/etc, with no way to test it without rendering something. Where that math was self-contained, it got pulled out into a plain function the widget just calls - same behaviour, but now testable without a `QApplication` at all in most cases:

- `ui/envelope_graph.py`'s `_adsr_points()`/`_env2_points()` - the ADSR and 4-stage rate/level envelope coordinate math.
- `ui/knob.py`'s drag-to-value math is still inline in `mouseMoveEvent`, but `test_knob.py` exercises it directly using a tiny duck-typed fake mouse event instead (`Knob` only ever calls `.button()`/`.position()` on whatever it's given) rather than fighting `QMouseEvent`'s constructor.
- `ui/dashboard.py`'s `_expand_dropped_paths`/`_sanitize_filename`/`_unique_save_path` were already plain `@staticmethod`s with no Qt involved at all - just previously untested. `_unique_save_path` in particular is the receive-side equivalent of the sample-number collision bug above.
- `ui/keygroup_range_bar.py`'s `keygroup_color()`, `ui/sample_info_dialog.py`'s `_format_size()`, and the closest-bit-depth snapping in `ui/sample_settings_dialog.py` were all already standalone functions/`__init__` logic, just also previously untested.
- `ui/qt_helpers.py`'s `FullWidthTabBar` tab-width distribution needs a real (offscreen) `QApplication` and actual `QTabWidget`/tab bar, since the thing being tested is Qt's own layout response to `tabSizeHint()` - there was no pure function to pull out here, just no test.

## What's not covered (yet)

- Actual rendering - nothing paints pixels and checks them. The math several `paintEvent`s depend on is tested (see above); the drawing itself isn't.
- Real hardware testing. None of the current tests talk to any real MIDI hardware.

If you're considering adding a new feature, it might be a good idea to add some tests for it.
