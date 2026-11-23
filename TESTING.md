# Testing

```
uv sync
uv run python tools/build_velociloops.py   # the REX2 reader; needs a C++ compiler
uv run pytest tests/ -q
```

The whole suite takes about 5 minutes (3741 tests in 5:12 and 5:16 on the dev Mac, 2026-11-23). CI runs the same command with `AKAISDS_CI_NO_AUDIO_DEVICE=1`, which skips the tests that
need a real audio output device. Without the library built, the `.rx2` tests are skipped, not failed. When you fix a bug, add a test that would have caught it.

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

Looking at the app by hand instead of through a test: the demo samplers (fake units on fake ports) are described in [CONTRIBUTING.md](CONTRIBUTING.md#running-the-app-without-a-sampler).

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
- **One full run hung once (2026-10-19) and was not reproduced** (the run straight after passed: 3461 tests in 6:46). `sample <pid>` showed a deadlock: the main thread waiting on
  a Python lock while the `BridgeWorker` thread sat inside `QMainWindow`'s destructor (`QWidget::~QWidget` -> `QWindow::close()`), i.e. a Program Editor window was destroyed on the
  worker thread (which needs the GUI thread) - the same family as the audio-preview player bug (a Qt object whose last reference is dropped on another thread; see AGENTS.md, "Slice
  Editor"). It happened while hardware tools were using the MIDI ports at the same time, which is probably only the timing that exposed it. `pyproject.toml` now sets
  `faulthandler_timeout = 300`, so a stuck test dumps all threads' stacks: if it recurs, the traceback names the test and what the main thread waits for; look for the code that lets
  the worker thread (or a signal it emits) hold the last reference to an editor window.
- **A full run crashed once with SIGBUS (2026-10-19 19:53, the run straight after passed: 3477 tests in 6:44; not reproduced).** The crash report: main thread inside shiboken's `mainThreadDeletionHandler` (a Qt wrapper queued for deletion via `Py_AddPendingCall`, run from `_Py_HandlePending` between bytecodes of a generator `next()`), a `BridgeWorker` thread still alive and idle in the process, the `faulthandler` handler firing on the bad access. Same family as the two entries above (a Qt object whose last Python reference is dropped off the GUI thread, here a leaked `BridgeWorker` whose editor wasn't stopped in teardown); the Python traceback that would name the test was lost because that run's output was cut with `tail`. **When a run crashes, keep the whole output (`pytest ... > file 2>&1`)**: faulthandler prints the Python stack of every thread, and the test is in it. Not related to the multi write-check change that was being tested.
- **The run exits the moment pytest prints its summary** (`tests/conftest.py`, `pytest_unconfigure` -> `os._exit(status)`; `AKAISDS_TEST_NORMAL_EXIT=1` gives the ordinary shutdown back). Python's own shutdown
  destroys every window the tests left alive, and PySide's `SenderSignalDeletionTracker::senderDestroyed` makes that take minutes (2026-10-31, `sample` of the lingering process: 94% in it, under
  `destroyQCoreApplication`): 180 s on Qt 6.11 and ~560 s on 6.8 on the dev Mac, 6 minutes on a CI runner with 6.11, and the Qt 6.8 CI job never finished (cancelled at its limit, 16 minutes after its summary).
  After the change the exit takes 0.1-0.2 s on both. `AKAISDS_TEST_WIDGET_COUNT=1` prints how many windows are still alive at the end (554 on 6.11: 399 `ProgramEditorWindow`, 78 `SliceEditorWindow`; 645 on 6.8),
  which was the leak itself (it is fixed: see the next entry, and the count is 0 now). **Never count widgets with `QApplication.allWidgets()`** (it wraps every widget Qt knows about and segfaulted on a half-destroyed one).
- **Windows are deleted after every test, and the run fails if many survive** (`tests/conftest.py`, `_release_windows` and the end-of-run check). The editor fixtures only stopped their `BridgeWorker`, so
  ~540 windows lived to the end of a run and every later test paid for them (the theme tests restyle every live widget). Measured 2026-11-23, dev Mac: the same 3 window-heavy files took 88 s
  in one process and 28 s with the fix; the whole suite went from 9:17 to 5:12 and 5:16 (two runs, 3741 tests, 0 windows left). Every `QMainWindow`/`QDialog` subclass built is remembered (`QMainWindow.__init__`
  and `QDialog.__init__` are wrapped in conftest, so a bare `QMessageBox` or `QWidget` is NOT seen), its `_worker` stopped, and `deleteLater()` called after the test's own fixtures have torn down. **Not `close()`**: that
  runs `closeEvent`, and an "unsent edits" question blocks offscreen forever (a try with `close()` hung past 10 minutes). If the run ends with more than 25 windows alive
  (`AKAISDS_TEST_WINDOW_LIMIT` changes it) it prints `FAILED: N windows and dialogs are still alive` and exits 1: a window kept in a module global, or a fixture wider than a test, would do it. A scan of
  every live object after each test would find the leaks too, but costs more than it saves (a diagnostic plugin that did it was ~8x slower over a full run).
- One harmless warning: `Wave_write.__del__` from the stdlib `wave`, from a test that writes to an unwritable path.
