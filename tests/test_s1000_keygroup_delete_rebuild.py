# S1000 Delete Keygroup by REBUILDING the program (BridgeWorker._delete_keygroup_s1000_rebuild),
# behind s1000_bridge.KEYGROUP_DELETE_BY_REBUILD. Run against PointerFake (a fake S1000 that allocates
# real addresses - see test_akai_program_file.py) through the real adapter.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox

import core.s1000_bridge as s1000_bridge
from core import akai_program_file as apf
from core import program_editor_bridge
from core.demo_s1000 import make_keygroup_block, make_program_block
from core.program_editor_bridge import BridgeWorker, LoggingBridge
from core.s1000_bridge import S1000Bridge
from test_akai_program_file import PointerFake
from test_program_editor_window_s1000 import (  # noqa: F401 - fixtures/helpers
    _dispose,
    _make_editor,
    _pump_until,
    qapp,
)


@pytest.fixture(autouse=True)
def _rebuild_on(monkeypatch, tmp_path):
    monkeypatch.setattr(s1000_bridge, "KEYGROUP_DELETE_BY_REBUILD", True)
    monkeypatch.setattr(program_editor_bridge, "PROGRAM_BACKUP_DIR", tmp_path / "backups")


def _fake(cls=PointerFake, **kwargs):
    def program(name, number, kgs):
        return {
            "block": make_program_block(name, number, len(kgs)),
            "keygroups": [make_keygroup_block(s, lo, hi) for s, lo, hi in kgs],
        }

    return cls(
        programs=[
            program("DRUMS", 0, [("KICK", 24, 40), ("SNARE", 41, 80), ("HAT", 81, 127)]),
            program("PAD PROG", 1, [("PAD", 24, 127)]),
        ],
        samples=[],
        **kwargs,
    )


def _worker(fake):
    return BridgeWorker(LoggingBridge(S1000Bridge(fake.bridge(timeout=0.3))), s1000=True)


def _delete(worker, program_index, keygroup_index):
    rebuilt, failed = [], []
    worker.program_rebuilt.connect(lambda *a: rebuilt.append(a))
    worker.keygroup_delete_failed.connect(lambda *a: failed.append(a[2]))
    worker.submit_delete_keygroup(program_index, keygroup_index)
    worker.process_pending()
    return rebuilt, failed


def _names(fake):
    return [bytes(e["block"][3:15]) for e in fake.programs]


def test_the_flag_is_off_by_default():
    import importlib

    assert importlib.reload(s1000_bridge).KEYGROUP_DELETE_BY_REBUILD is False


def test_a_middle_keygroup_is_removed_and_the_program_comes_back_last_under_its_name(tmp_path):
    fake = _fake()
    pad_before = (bytes(fake.programs[1]["block"]), [bytes(k) for k in fake.programs[1]["keygroups"]])
    kicks = [bytes(k) for k in fake.programs[0]["keygroups"]]
    worker = _worker(fake)

    rebuilt, failed = _delete(worker, 0, 1)

    assert failed == []
    assert rebuilt == [(0, 1, 1)]  # the program is now index 1, last
    assert [e["block"][3:15] for e in fake.programs] == [
        fake.programs[0]["block"][3:15],  # PAD PROG
        fake.programs[1]["block"][3:15],
    ]
    assert worker._bridge.program_list() == ["PAD PROG", "DRUMS"]
    rebuilt_entry = fake.programs[1]
    assert len(rebuilt_entry["keygroups"]) == 2
    assert rebuilt_entry["block"][apf._GROUPS_OFFSET] == 2
    # the survivors are the original's keygroups 0 and 2, in order (pointers aside)
    assert [bytes(k[3:]) for k in rebuilt_entry["keygroups"]] == [kicks[0][3:], kicks[2][3:]]
    # the other program is untouched
    assert (bytes(fake.programs[0]["block"]), [bytes(k) for k in fake.programs[0]["keygroups"]]) == pad_before
    assert fake.deleted_by_name_clash == []
    assert not any(op == 0x0C or op == "DELK" for op in fake.received)  # no DELK ever sent
    assert fake.ignored_ops == []


def test_a_backup_of_the_original_is_written_first(tmp_path):
    fake = _fake()
    worker = _worker(fake)
    _delete(worker, 0, 0)
    backups = list((tmp_path / "backups").glob("DRUMS-*.p1"))
    assert len(backups) == 1
    parsed = apf.parse_file(backups[0].read_bytes())
    assert parsed.name == "DRUMS" and len(parsed.keygroups) == 3


def test_the_chain_of_the_rebuilt_program_is_the_samplers_own():
    fake = _fake()
    worker = _worker(fake)
    _delete(worker, 0, 2)
    entry = fake.programs[1]
    base = entry["addr"]
    assert entry["block"][1:3] == (base + fake.block_size).to_bytes(2, "little")
    # the file-relative stand-ins (150, 300) were never what the sampler holds
    held = {bytes(entry["block"][1:3])} | {bytes(k[1:3]) for k in entry["keygroups"]}
    assert not held & {(150).to_bytes(2, "little"), (300).to_bytes(2, "little")}


def test_an_existing_temp_name_is_not_reused():
    fake = _fake()
    fake.programs.append(
        {
            "block": make_program_block("DRUMS-TMP", 5, 1),
            "keygroups": [make_keygroup_block("X", 24, 127)],
        }
    )
    fake.programs[-1]["addr"] = fake._free
    fake._link(fake.programs[-1])
    fake._free += 2 * fake.block_size
    worker = _worker(fake)
    rebuilt, failed = _delete(worker, 0, 0)
    assert failed == []
    assert worker._bridge.program_list() == ["PAD PROG", "DRUMS-TMP", "DRUMS"]


def test_a_single_keygroup_program_is_refused_and_nothing_is_sent():
    fake = _fake()
    worker = _worker(fake)
    before = fake.writes
    rebuilt, failed = _delete(worker, 1, 0)
    assert rebuilt == [] and "only keygroup" in failed[0]
    assert fake.writes == before


def test_a_failed_copy_leaves_the_original_alone_and_says_so():
    class Rejecting(PointerFake):
        def _kdata(self, payload):
            if payload[2] == 1:
                return self._reply_ok(False)
            super()._kdata(payload)

    fake = _fake(Rejecting)
    original = (bytes(fake.programs[0]["block"]), [bytes(k) for k in fake.programs[0]["keygroups"]])
    worker = _worker(fake)

    rebuilt, failed = _delete(worker, 0, 0)

    assert rebuilt == []
    assert "was NOT changed" in failed[0] and "DRUMS-TMP" in failed[0]
    assert (bytes(fake.programs[0]["block"]), [bytes(k) for k in fake.programs[0]["keygroups"]]) == original


def test_a_sampler_that_honours_our_pointer_stops_the_rebuild_before_anything_is_deleted():
    fake = _fake(honor_first_pointer=True)
    worker = _worker(fake)
    names = lambda: [bytes(e["block"][3:15]) for e in fake.programs[:2]]
    before = names()
    rebuilt, failed = _delete(worker, 0, 0)
    assert rebuilt == [] and "NOT changed" in failed[0]
    assert names() == before
    assert worker.program_import_blocked is True
    _r, failed_again = _delete(worker, 0, 0)
    assert "disabled for this session" in failed_again[0]


def test_if_the_delete_of_the_original_damages_another_program_it_is_reported():
    class Damaging(PointerFake):
        def _delp(self, payload):
            super()._delp(payload)
            self.programs[0]["keygroups"][0][40] ^= 0xFF  # whatever follows the deleted one

    fake = _fake(Damaging)
    worker = _worker(fake)
    rebuilt, failed = _delete(worker, 0, 0)
    assert rebuilt == []
    assert "was deleted but" in failed[0] and "backup" in failed[0]
    assert worker.program_import_blocked is True


# --- the window ----------------------------------------------------------------------------------


def test_the_window_confirms_describes_the_rebuild_and_selects_the_rebuilt_program(qapp, monkeypatch):
    fake = _fake()
    editor = _make_editor(
        qapp, "akai_s1000", LoggingBridge(S1000Bridge(fake.bridge(timeout=0.3)))
    )
    try:
        assert editor._keygroup_delete_unavailable_message() is None
        editor.keygroup_list.setCurrentRow(1)
        asked = []
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes,
        )
        editor._confirm_delete_keygroup()
        assert "rebuilds" in asked[0] and "backup" in asked[0]
        _pump_until(qapp, lambda: editor.program_list.count() == 2 and editor.program_list.item(1).text() == "DRUMS")
        editor._worker.wait_until_idle()
        _pump_until(qapp, lambda: editor.program_list.currentRow() == 1)
        editor._worker.wait_until_idle()
        _pump_until(qapp, lambda: editor.keygroup_list.count() == 2)
    finally:
        _dispose(editor)


def test_the_window_still_explains_when_both_flags_are_off(qapp, monkeypatch):
    monkeypatch.setattr(s1000_bridge, "KEYGROUP_DELETE_BY_REBUILD", False)
    monkeypatch.setattr(s1000_bridge, "KEYGROUP_DELETE_SUPPORTED", False)
    fake = _fake()
    editor = _make_editor(
        qapp, "akai_s1000", LoggingBridge(S1000Bridge(fake.bridge(timeout=0.3)))
    )
    try:
        assert "not reliable" in editor._keygroup_delete_unavailable_message()
    finally:
        _dispose(editor)
