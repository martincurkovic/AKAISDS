# tests for TransferDashboard's pure filesystem-handling staticmethods
# (dashboard.py) - no QApplication needed, they don't touch Qt at all.
#
# _unique_save_path in particular protects against exactly the class of bug
# TESTING.md already documents once for the send side ("a sample number
# counter bug that could cause two different files in the same batch to
# collide on the same hardware slot") - this is the same risk on the
# receive side: two samples in one batch landing on the same local file.

import os

from ui.dashboard import TransferDashboard


# --- _sanitize_filename -------------------------------------------------------


def test_sanitize_filename_replaces_forward_and_back_slashes():
    assert TransferDashboard._sanitize_filename("a/b\\c") == "a-b-c"


def test_sanitize_filename_strips_surrounding_whitespace():
    assert TransferDashboard._sanitize_filename("  padded  ") == "padded"


def test_sanitize_filename_leaves_an_ordinary_name_untouched():
    assert TransferDashboard._sanitize_filename("Bass Stab 01") == "Bass Stab 01"


# --- _expand_dropped_paths -----------------------------------------------------


def test_expand_dropped_paths_leaves_plain_files_alone():
    paths = ["/some/file1.wav", "/some/file2.wav"]
    assert TransferDashboard._expand_dropped_paths(paths) == paths


def test_expand_dropped_paths_recurses_into_directories(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "b.wav").write_bytes(b"")
    (tmp_path / "a.wav").write_bytes(b"")
    (tmp_path / "sub" / "c.wav").write_bytes(b"")

    result = TransferDashboard._expand_dropped_paths([str(tmp_path)])

    assert sorted(os.path.basename(p) for p in result) == ["a.wav", "b.wav", "c.wav"]


def test_expand_dropped_paths_handles_a_mix_of_files_and_directories(tmp_path):
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "inside.wav").write_bytes(b"")
    plain_file = "/some/plain.wav"

    result = TransferDashboard._expand_dropped_paths([plain_file, str(tmp_path / "dir")])

    assert plain_file in result
    assert any(p.endswith("inside.wav") for p in result)


# --- _unique_save_path -----------------------------------------------------------


def test_unique_save_path_uses_the_plain_name_when_nothing_collides(tmp_path):
    path = TransferDashboard._unique_save_path(str(tmp_path), "Bass Stab")
    assert path == os.path.join(str(tmp_path), "Bass Stab.wav")


def test_unique_save_path_numbers_around_a_file_already_on_disk(tmp_path):
    (tmp_path / "Bass Stab.wav").write_bytes(b"")

    path = TransferDashboard._unique_save_path(str(tmp_path), "Bass Stab")

    assert path == os.path.join(str(tmp_path), "Bass Stab (2).wav")


def test_unique_save_path_keeps_counting_past_several_existing_files(tmp_path):
    (tmp_path / "Bass Stab.wav").write_bytes(b"")
    (tmp_path / "Bass Stab (2).wav").write_bytes(b"")
    (tmp_path / "Bass Stab (3).wav").write_bytes(b"")

    path = TransferDashboard._unique_save_path(str(tmp_path), "Bass Stab")

    assert path == os.path.join(str(tmp_path), "Bass Stab (4).wav")


def test_unique_save_path_avoids_claimed_paths_not_yet_written_to_disk(tmp_path):
    # this is the receive-side equivalent of the send-side sample-number
    # collision bug in TESTING.md - two samples in the same batch must not
    # both resolve to the same not-yet-existing path before either is
    # actually written
    first = TransferDashboard._unique_save_path(str(tmp_path), "Kick")
    second = TransferDashboard._unique_save_path(
        str(tmp_path), "Kick", claimed_paths={first}
    )

    assert first != second
    assert second == os.path.join(str(tmp_path), "Kick (2).wav")
