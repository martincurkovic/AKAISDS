# tests for core/dropped_files.py - the stable local copies dropped/opened
# files into the Transfer Dashboard's queue get, and the crash-safety
# sweep for sessions that never got to clean their own copies up. Pure
# filesystem logic, no Qt needed. _BASE_DIR is monkeypatched to a pytest
# tmp_path in every test so nothing here ever touches the real
# ~/.akaisds/dropped_files.

import os
from pathlib import Path

import pytest

from core import dropped_files


@pytest.fixture(autouse=True)
def _isolated_base_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(dropped_files, "_BASE_DIR", tmp_path / "dropped_files")
    monkeypatch.setattr(dropped_files, "_session_dir", None)
    yield


# --- session_dir --------------------------------------------------------------


def test_session_dir_is_named_after_this_process(tmp_path):
    path = dropped_files.session_dir()
    assert path.name == str(os.getpid())
    assert path.is_dir()


def test_session_dir_is_cached_across_calls():
    first = dropped_files.session_dir()
    second = dropped_files.session_dir()
    assert first == second


# --- copy_into_session ---------------------------------------------------------


def test_copy_into_session_copies_content_and_returns_a_path_under_base_dir(
    tmp_path,
):
    source = tmp_path / "source" / "cropped.wav"
    source.parent.mkdir()
    source.write_bytes(b"fake wav bytes")

    result = dropped_files.copy_into_session(str(source))

    assert os.path.exists(result)
    with open(result, "rb") as f:
        assert f.read() == b"fake wav bytes"
    assert dropped_files._BASE_DIR.resolve() in Path(result).resolve().parents


def test_copy_into_session_raises_for_a_missing_source(tmp_path):
    missing = tmp_path / "gone.wav"
    with pytest.raises(OSError):
        dropped_files.copy_into_session(str(missing))


def test_copy_into_session_disambiguates_same_basename_from_different_sources(
    tmp_path,
):
    source_a = tmp_path / "a" / "crop.wav"
    source_b = tmp_path / "b" / "crop.wav"
    source_a.parent.mkdir()
    source_b.parent.mkdir()
    source_a.write_bytes(b"AAAA")
    source_b.write_bytes(b"BBBB")

    result_a = dropped_files.copy_into_session(str(source_a))
    result_b = dropped_files.copy_into_session(str(source_b))

    assert result_a != result_b
    with open(result_a, "rb") as f:
        assert f.read() == b"AAAA"
    with open(result_b, "rb") as f:
        assert f.read() == b"BBBB"


# --- remove_copy ----------------------------------------------------------------


def test_remove_copy_deletes_a_file_under_base_dir(tmp_path):
    source = tmp_path / "src.wav"
    source.write_bytes(b"x")
    copy_path = dropped_files.copy_into_session(str(source))
    assert os.path.exists(copy_path)

    dropped_files.remove_copy(copy_path)

    assert not os.path.exists(copy_path)


def test_remove_copy_ignores_a_path_outside_base_dir(tmp_path):
    # the one function here that takes a path from OUTSIDE the module -
    # must never delete something it didn't create, even if a caller ever
    # passes something unexpected
    outside_file = tmp_path / "not_ours.wav"
    outside_file.write_bytes(b"x")

    dropped_files.remove_copy(str(outside_file))

    assert outside_file.exists()


def test_remove_copy_does_not_raise_for_an_already_removed_path(tmp_path):
    source = tmp_path / "src.wav"
    source.write_bytes(b"x")
    copy_path = dropped_files.copy_into_session(str(source))
    dropped_files.remove_copy(copy_path)

    dropped_files.remove_copy(copy_path)  # already gone - must not raise


def test_remove_copy_ignores_the_base_dir_itself(tmp_path):
    # base_resolved == resolved (passing the session/base dir path itself,
    # not a file under it) must not attempt to remove the directory
    dropped_files.remove_copy(str(dropped_files._BASE_DIR))
    # no exception, and nothing to assert further - _BASE_DIR may not
    # even exist yet at this point, which is fine


# --- cleanup_session -------------------------------------------------------------


def test_cleanup_session_removes_the_whole_session_directory():
    source_dir = dropped_files.session_dir()
    (source_dir / "leftover.wav").write_bytes(b"x")
    assert source_dir.exists()

    dropped_files.cleanup_session()

    assert not source_dir.exists()


def test_cleanup_session_resets_state_so_a_later_call_gets_a_fresh_directory():
    first = dropped_files.session_dir()
    dropped_files.cleanup_session()
    second = dropped_files.session_dir()

    assert second.exists()
    assert first == second  # same PID, same name - but freshly recreated


def test_cleanup_session_before_any_session_dir_use_does_not_raise():
    dropped_files.cleanup_session()  # _session_dir is still None - must not crash


# --- sweep_orphaned_sessions -----------------------------------------------------


def test_sweep_removes_a_directory_for_a_pid_that_is_not_running(monkeypatch):
    dead_dir = dropped_files._BASE_DIR / "424242"
    dead_dir.mkdir(parents=True)
    (dead_dir / "orphaned.wav").write_bytes(b"x")
    monkeypatch.setattr(dropped_files, "_pid_is_running", lambda pid: pid != 424242)

    dropped_files.sweep_orphaned_sessions()

    assert not dead_dir.exists()


def test_sweep_leaves_a_directory_for_a_pid_that_is_still_running(monkeypatch):
    live_dir = dropped_files._BASE_DIR / "555555"
    live_dir.mkdir(parents=True)
    monkeypatch.setattr(dropped_files, "_pid_is_running", lambda pid: True)

    dropped_files.sweep_orphaned_sessions()

    assert live_dir.exists()


def test_sweep_never_touches_its_own_pid_directory(monkeypatch):
    own_dir = dropped_files._BASE_DIR / str(os.getpid())
    own_dir.mkdir(parents=True)
    # if sweep ever asked _pid_is_running about its own pid and got a
    # wrong answer, this would catch it - own-pid must be skipped outright
    monkeypatch.setattr(dropped_files, "_pid_is_running", lambda pid: False)

    dropped_files.sweep_orphaned_sessions()

    assert own_dir.exists()


def test_sweep_ignores_non_pid_directories_and_stray_files(monkeypatch):
    dropped_files._BASE_DIR.mkdir(parents=True)
    not_a_pid_dir = dropped_files._BASE_DIR / "not_a_pid"
    not_a_pid_dir.mkdir()
    stray_file = dropped_files._BASE_DIR / "stray.txt"
    stray_file.write_bytes(b"x")
    monkeypatch.setattr(dropped_files, "_pid_is_running", lambda pid: False)

    dropped_files.sweep_orphaned_sessions()  # must not raise

    assert not_a_pid_dir.exists()
    assert stray_file.exists()


def test_sweep_handles_a_missing_base_dir():
    assert not dropped_files._BASE_DIR.exists()
    dropped_files.sweep_orphaned_sessions()  # must not raise


# --- _pid_is_running --------------------------------------------------------------


def test_pid_is_running_true_for_the_current_process():
    assert dropped_files._pid_is_running(os.getpid()) is True


def test_pid_is_running_false_when_the_process_does_not_exist(monkeypatch):
    def _raise_lookup_error(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(dropped_files.os, "kill", _raise_lookup_error)
    assert dropped_files._pid_is_running(123456) is False
