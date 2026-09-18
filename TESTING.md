# Testing

AKAISDS has a small pytest suite covering the bare minimum (`core/` and `controller/`). It doesn't yet cover the UI layer or any real hardware.

## Running the tests

```
uv sync
uv run pytest tests/ -v
```

This is the same command that the CI pipeline runs. The whole testing suite takes less than a second. Again, very minimal and very quickly added. No UI or hardware testing just yet.

## What's actually tested and why

`tests/test_akai_sysex.py` is the Akai specific SysEx protocol (SLIST, DELS, SDATA, rename, tuning offset).

`tests/test_sds_encoder.py` is the universal SDS byte encoding. Also contains real WAV/AIFF file I/O against some very small dummy files. Confirms for both AIFF and WAV.

`tests/test_midi_identity.py` is the universal MIDI identity request AND the Akai specific RSTAT and STAT request and response pair.

`tests/test_app_config.py` is the settings persistence testing. Uses `monkeypatch`/`tmp_path` to redirect `CONFIG_PATH` to a temp file, so that way it never touches the user's real config file.

`tests/test_theme.py` is the stylesheet renderer which generates both light and dark themes. Also uses `monkeypatch/tmp_path`.

`tests/test_sampler_controller.py` is the big dawg test. This one might require some explanation cos it's very much not like the others. See below for details

#### Why several tests exist

A number of tests in the suite are based on chasing down bugs during development, not just randomly "testing everything for funsies":

- `build_slist_request` once upon a time hard-coded the channel byte to `0x00`, this silently ignored the configured device ID which took me much longer than I care to admit to debug...
- Generic-device sends could get permanently stuck after the first send because `_awaiting_count_for_queue` was being set even when the generic SDS device never sends the SLIST request that would clear it
- A sample number counter bug that could cause two different files in the same batch to collide on the same hardware slot, causing many a headache
- A silly indentation bug that left ~40 lines of controller state initialisation nested in `set_device_type()` instead of `__init__`, meaning it just silently reset every time a user saved their MIDI settings

If you're fixing a bug, consider whether it's worth adding a test in here too.

#### Fake QtCore technique

`test_sampler_controller.py` doesn't user the real PySide6 `QtCore` but instead uses a `_FakeQObject`, `_FakeSignal` and `_FakeQTimer.singleShot` which fires immediately instead of waiting for a real event loop. This is one of the reasons why the controller tests are so fast, because there is no actual Qt code in the way and no timers to wait for.

## What's not covered (yet)

- The UI layer isn't being tested at all.
- Real hardware testing. None of the current tests talk to any real MIDI hardware

If you're considering adding a new feature, it might be a good idea to add some tests for it.
