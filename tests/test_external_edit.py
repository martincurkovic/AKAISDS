# Tests for "Edit in External App": the pure helpers (core/external_edit.py), the config accessor and the file tracker
# (ui/external_edit_tracker.py). The window round trips are in test_external_edit_window.py.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication

from core import app_config, external_edit as ee, sds_encoder


@pytest.fixture(scope="session")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_safe_filename_keeps_a_normal_name_and_replaces_what_a_file_system_dislikes():
    assert ee.safe_filename("DRUM LOOP 1") == "DRUM LOOP 1"
    assert ee.safe_filename('A/B:C*D?"E') == "A_B_C_D__E"
    assert ee.safe_filename("  ..  ") == "sample"


def test_launch_command_per_platform():
    assert ee.launch_command("/t/a.wav", None, "darwin") == ["open", "/t/a.wav"]
    assert ee.launch_command("/t/a.wav", "/Applications/Audacity.app", "darwin") == ["open", "-a", "/Applications/Audacity.app", "/t/a.wav"]
    assert ee.launch_command("/t/a.wav", "/usr/bin/audacity", "linux") == ["/usr/bin/audacity", "/t/a.wav"]
    assert ee.launch_command("/t/a.wav", None, "linux") == ["xdg-open", "/t/a.wav"]
    assert ee.launch_command("C:\\a.wav", None, "win32") is None  # os.startfile
    assert ee.launch_command("C:\\a.wav", "C:\\x.exe", "win32") == ["C:\\x.exe", "C:\\a.wav"]


def test_launch_reports_why_it_failed(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("no such app")

    monkeypatch.setattr(ee.subprocess, "run", boom)
    monkeypatch.setattr(ee.subprocess, "Popen", boom)
    assert "no such app" in ee.launch("/t/a.wav", "/nope")


# --- reading the edited file back -----------------------------------------------------------------------------------


def _write(path, samples, rate=44100):
    sds_encoder.write_wav_file(str(path), samples, rate, bit_depth=16)


def test_read_edited_returns_the_audio_at_the_sampler_rate(tmp_path):
    path = tmp_path / "a.wav"
    _write(path, list(range(1000)), 44100)
    channels, rate = ee.read_edited(str(path), 44100)
    assert rate == 44100 and channels == [list(range(1000))]


def test_read_edited_resamples_when_the_editor_changed_the_rate(tmp_path):
    path = tmp_path / "a.wav"
    _write(path, [100] * 4800, 48000)
    channels, rate = ee.read_edited(str(path), 24000)
    assert rate == 48000 and len(channels[0]) == 2400


def test_read_edited_refuses_an_empty_or_unreadable_file(tmp_path):
    empty = tmp_path / "empty.wav"
    _write(empty, [])
    with pytest.raises(ValueError):
        ee.read_edited(str(empty), 44100)
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not audio")
    with pytest.raises(ValueError):
        ee.read_edited(str(junk), 44100)


def test_mix_to_mono_averages_two_channels():
    assert ee.mix_to_mono([[1, 2, 3]]) == [1, 2, 3]
    assert ee.mix_to_mono([[100, -100, 7], [300, -300, 8]]) == [200, -200, 7]


# --- markers ---------------------------------------------------------------------------------------------------------


def test_markers_are_unchanged_when_the_length_is_the_same():
    assert ee.remap_markers(1000, 1000, 10, 100, 200, 900) == (10, 100, 200, 900)


def test_markers_covering_the_whole_sample_cover_the_whole_new_one():
    assert ee.remap_markers(1000, 1500, 0, 100, 200, 999) == (0, 100, 200, 1499)
    assert ee.remap_markers(1000, 400, 0, 100, 200, 999) == (0, 100, 200, 399)


def test_shortened_audio_clamps_markers_and_keeps_the_loop_inside_in_order():
    start, loop_start, loop_end, end = ee.remap_markers(1000, 500, 100, 300, 800, 900)
    assert (start, end) == (100, 499)
    assert start <= loop_start <= loop_end <= end and loop_end == 499


def test_lengthened_audio_keeps_positions_that_were_not_the_whole_sample():
    assert ee.remap_markers(1000, 2000, 100, 300, 600, 800) == (100, 300, 600, 800)


# --- backup, fingerprint, config -------------------------------------------------------------------------------------


def test_backup_original_writes_a_wav_with_the_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, "BACKUP_DIR", tmp_path / "bk")
    path = ee.backup_original("KICK/1", list(range(50)), 22050)
    assert os.path.dirname(path) == str(tmp_path / "bk") and "KICK_1" in os.path.basename(path)
    assert sds_encoder.read_wav_samples(path) == (list(range(50)), 22050)


def test_fingerprint_changes_when_the_file_is_saved_over_and_is_none_when_gone(tmp_path):
    path = tmp_path / "a.wav"
    assert ee.fingerprint(str(path)) is None
    path.write_bytes(b"1234")
    first = ee.fingerprint(str(path))
    path.write_bytes(b"12345")
    assert ee.fingerprint(str(path)) != first


def test_external_editor_setting_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "config.json")
    assert app_config.get_saved_external_editor() == ""
    app_config.save_external_editor("  /Applications/Audacity.app ")
    assert app_config.get_saved_external_editor() == "/Applications/Audacity.app"
    app_config.save_external_editor("")
    assert app_config.get_saved_external_editor() == ""


# --- the tracker -----------------------------------------------------------------------------------------------------


@pytest.fixture
def tracker(qapp, tmp_path, monkeypatch):
    from core import dropped_files
    from ui.external_edit_tracker import ExternalEditTracker

    monkeypatch.setattr(dropped_files, "_session_dir", tmp_path)
    launched = []
    monkeypatch.setattr(ee, "launch", lambda path, editor=None: launched.append((path, editor)))
    tracker = ExternalEditTracker()
    tracker.launched = launched
    yield tracker
    tracker.close()


def test_open_exports_the_file_launches_the_editor_and_waits_for_a_save(tracker):
    assert tracker.open("KICK", "KICK", lambda path: _write(path, [1, 2, 3])) is None
    path = tracker.path("KICK")
    assert os.path.basename(path) == "KICK.wav" and tracker.launched[0][0] == path
    assert tracker.has("KICK") and not tracker.is_ready("KICK")  # not saved yet
    _write(path, [1, 2, 3, 4, 5])
    assert tracker.is_ready("KICK")


def test_mark_sent_waits_for_the_next_save(tracker):
    tracker.open("KICK", "KICK", lambda path: _write(path, [1, 2, 3]))
    path = tracker.path("KICK")
    _write(path, [9] * 10)
    tracker.mark_sent("KICK")
    assert not tracker.is_ready("KICK") and tracker.has("KICK")
    _write(path, [9] * 20)
    assert tracker.is_ready("KICK")


def test_the_watcher_tells_the_window_when_the_file_is_saved(tracker, qapp):
    import time

    from PySide6.QtCore import QEventLoop, QTimer

    tracker.open("KICK", "KICK", lambda path: _write(path, [1, 2, 3]))
    fired = []
    tracker.changed.connect(lambda: fired.append(True))
    _write(tracker.path("KICK"), [1] * 100)
    deadline = time.time() + 5
    while not fired and time.time() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(50, loop.quit)
        loop.exec()
    assert fired


def test_a_failed_launch_removes_the_export_and_says_why(tracker, monkeypatch):
    monkeypatch.setattr(ee, "launch", lambda path, editor=None: "no such app")
    problem = tracker.open("KICK", "KICK", lambda path: _write(path, [1, 2, 3]))
    assert "no such app" in problem and not tracker.has("KICK")


def test_a_failed_write_is_reported_and_nothing_is_launched(tracker):
    def bad(path):
        raise OSError("disk full")

    assert "disk full" in tracker.open("KICK", "KICK", bad)
    assert not tracker.launched and not tracker.has("KICK")


def test_discard_removes_the_directory(tracker):
    tracker.open("KICK", "KICK", lambda path: _write(path, [1, 2, 3]))
    directory = os.path.dirname(tracker.path("KICK"))
    tracker.discard("KICK")
    assert not os.path.exists(directory) and not tracker.has("KICK")
