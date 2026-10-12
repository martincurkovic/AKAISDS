# Testing

```
uv sync
uv run pytest tests/ -q
```

The whole suite takes about 3-4 minutes. CI runs the same command with `AKAISDS_CI_NO_AUDIO_DEVICE=1`, which skips the tests that
need a real audio output device. When you fix a bug, add a test that would have caught it.

## What's covered

- **Protocol and math (`core/`)**: the Akai, S950, Yamaha and SDS codecs, sample-editing and pitch/onset detection, program files,
  global-settings maps, settings persistence, the stylesheet renderer.
- **Controllers**: `SamplerController` (send hardening, receive, status), `S950Transfers` and `YamahaSession` against their fakes.
- **Program Editor**: `BridgeWorker` and the window, once against a hand-written `FakeBridge` and once against the real
  `s3ked.demo.DemoBridge` (see below), plus the S1000 window, the Global tab and the S950/Yamaha editors.
- **Pure logic pulled out of widgets** (envelope math, knob drag math, dropped-file helpers, dialog helpers) so it needs no rendering.
- **Reply matching and the debug trace**: the real `S3kBridge` over scripted ports with stray frames mixed in.

Not covered: painted pixels, and real hardware. Hardware test plans live next to the tests: `tests/s1000_test_plan.md`,
`s950_test_plan.md`, `akai_program_file_test_plan.md`, `midi_transport_consolidation_test_plan.md`.

## Techniques worth knowing

- **Fake QtCore**: `test_sampler_controller.py` and `test_midi_manager.py` replace `QObject`/`Signal` and make `QTimer.singleShot` fire
  immediately, so there is no event loop and "2 seconds of silence" costs nothing. Monkeypatch `singleShot` to capture delays.
- **`BridgeWorker`**: `submit_*()` queues a job and `process_pending()` drains it synchronously on the test thread (no real thread).
  `ProgramEditorWindow` tests use a real worker, `wait_until_idle()` and an event-loop pump (`_pump_until`), and **must stop the worker
  in teardown** (`editor._worker.stop(); editor._worker.wait()`) or Qt aborts with "Destroyed while thread is still running".
- **`DemoBridge` is the safety net for bumping the `s3ked` pin.** `FakeBridge` never calls `s3k.params`, so it can't see a renamed
  parameter, a moved region or a narrowed range. Run `test_program_editor_window_demo_bridge.py` before and after any pin bump.
- **`tests/conftest.py` keeps the suite off your real files**: it forces `QT_QPA_PLATFORM=offscreen` (the per-file `setdefault` is a no-op
  if your desktop exports the variable), points the debug log and the A4000/program/misc-block backups at a temp directory, makes the
  block-write `_sleep` a no-op and clears `AKAISDS_UNLOCK_GLOBAL`.
- **Fakes should answer asynchronously** (`QTimer.singleShot(0, ...)`) and with shrunken waits, like `test_s950_transfers.py`'s `_Midi`:
  a synchronous fake hides ordering bugs. `FakeS1000`/`FakeS950`/`FakeA4000` fake the MIDI ports, so the real code runs on top.
- **Fixtures**: `tests/fixtures/a4000/*.syx` are REAL captures - never regenerate them from the codec. `tests/test_audio.wav` is a short
  mono chord stab used for pitch detection and the demo.
- An unshown window has no layout geometry (`resize()`, then `layout().activate()`); assert visibility with `.isHidden()`.

## Traps (each one cost time)

- **No real multi-threaded stress tests.** A test holding a real lock across real `threading.Thread`s segfaulted the interpreter in
  unrelated `QThread` tests later in the same process. Use mocks and sequential tests.
- **`QListWidget.clear()` defers deleting row widgets**; flush deferred deletes after repopulating (the `dashboard` fixture does) or a
  later test segfaults.
- Never swap a test object's `__class__` (intermittent PySide segfault).
- Theme tests must not restyle the real `QApplication` (the suite keeps many windows alive; it went from 20 s to minutes). Pass a
  stand-in app object to `apply_to_app` and `deleteLater()` window fixtures.
- Audio-preview tests poll (`_wait_until`) instead of sleeping, and stop their players in an autouse fixture (a running device at
  exit crashes macOS). `test_update_loop_points_with_degenerate_bounds_is_ignored` has flaked once on timing; re-run it alone.
- One harmless warning: `Wave_write.__del__` from the stdlib `wave`, from a test that writes to an unwritable path.
