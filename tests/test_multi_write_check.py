# tools/s2000_multi_write_check.py: the hardware check that writes single multi-part fields. The fake sampler records every write so the tests can prove what the tool may and
# may not send, and that it backs up first, reads back, restores and stops on a surprise.

import importlib.util
import os

import pytest
from s3k import params as p

from core import akai_multi_file as amf
from test_akai_multi_file import make_blocks

_spec = importlib.util.spec_from_file_location("s2000_multi_write_check", os.path.join(os.path.dirname(__file__), "..", "tools", "s2000_multi_write_check.py"))
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

NAMES = {name: p.lookup(name, "multipart") for name in tool.SAFE_FIELDS}


class WritableBridge:
    """RMULTIDATA like the real sampler (selector 1 = a part) plus set_parameter on the multipart region. Misbehaviours are switches."""

    def __init__(self, backup_dir=None, refuse=None, drop=None, spill=None):
        header, parts = make_blocks()
        self.header = header
        self.parts = [bytearray(b) for b in parts]
        for block in self.parts:
            block[NAMES["PRIORT"].offset] = 1
            block[NAMES["PLAYLO"].offset] = 24
            block[NAMES["PLAYHI"].offset] = 108
            block[NAMES["TRANSPOSE"].offset] = 0
            block[NAMES["PTUNOCM"].offset] = 0
        self.writes = []
        self.backup_dir = backup_dir
        self.backups_at_first_write = None
        self.refuse, self.drop, self.spill = refuse, drop, spill

    def get_header_bytes(self, region, index, offset, count, selector=0, **kwargs):
        if region == "multi" or (region == "multipart" and selector == 0):
            return (self.header + bytes(160))[offset : offset + count]
        return bytes(self.parts[index])[offset : offset + count]

    def set_parameter(self, param, index, value, **kwargs):
        if self.backups_at_first_write is None:
            self.backups_at_first_write = sorted(os.listdir(self.backup_dir)) if self.backup_dir and os.path.isdir(self.backup_dir) else []
        self.writes.append((param.name, param.offset, index, value))
        if self.refuse == param.name:
            raise RuntimeError("REPLY ERROR")
        if self.drop == param.name:
            return  # acknowledged, never applied
        self.parts[index][param.offset : param.offset + param.size] = p.encode_field(param, value)
        if self.spill == param.name:
            self.parts[index][0x40] ^= 0xFF  # a side effect on a neighbouring byte


def run(bridge, tmp_path, part=16, fields=tool.SAFE_FIELDS, hold=0):
    lines = []
    results, backup = tool.run_check(bridge, part, fields, hold=hold, backup_dir=str(tmp_path), say=lambda k, t: lines.append((k, t)), sleep=lambda s: None)
    return results, backup, lines


def test_every_field_passes_is_restored_and_the_multi_ends_as_it_began(tmp_path):
    bridge = WritableBridge(str(tmp_path))
    before = [bytes(b) for b in bridge.parts]
    results, backup, lines = run(bridge, tmp_path)
    assert [r["result"] for r in results] == ["pass"] * 5 and all(r["restore"] == "ok" for r in results)
    assert [bytes(b) for b in bridge.parts] == before
    assert len(bridge.writes) == 10  # one write and one restore per field
    assert ("PASS", "the whole multi (header and all 16 parts) is exactly as it was before the run") in lines
    assert amf.parse_file(open(backup, "rb").read()).name == amf.read_from_bridge(bridge).name


def test_the_backup_is_on_disk_before_the_first_write(tmp_path):
    bridge = WritableBridge(str(tmp_path))
    run(bridge, tmp_path)
    assert any(name.endswith(".m3") for name in bridge.backups_at_first_write)


def test_only_the_five_fields_are_written_and_never_a_pointer_or_the_part_above_15(tmp_path):
    bridge = WritableBridge(str(tmp_path))
    run(bridge, tmp_path, part=3)
    assert {w[0] for w in bridge.writes} == set(tool.SAFE_FIELDS)
    assert {w[2] for w in bridge.writes} == {2}
    assert not {w[1] for w in bridge.writes} & amf.IGNORED_PART_OFFSETS
    for name in tool.SAFE_FIELDS:
        assert not set(range(NAMES[name].offset, NAMES[name].offset + NAMES[name].size)) & amf.IGNORED_PART_OFFSETS


def test_a_field_outside_the_five_is_refused_before_anything_is_sent(tmp_path):
    bridge = WritableBridge(str(tmp_path))
    for name in ("FX1", "PMCHAN", "OUTPUT"):
        with pytest.raises(ValueError, match="not one of the fields"):
            run(bridge, tmp_path, fields=(name,))
    assert bridge.writes == [] and os.listdir(tmp_path) == []
    with pytest.raises(ValueError, match="refusing to write"):
        tool.write_field(bridge, "FX1", 0, 1)
    assert bridge.writes == []


def test_a_part_above_16_is_never_read(tmp_path):
    with pytest.raises(ValueError, match="doesn't exist"):
        tool.read_part(WritableBridge(), 16)


def test_the_test_values_differ_from_the_current_and_keep_the_play_range_ordered():
    block = bytearray(amf.PART_SIZE)
    for name, off in (("PLAYLO", 24), ("PLAYHI", 108)):
        block[NAMES[name].offset] = off
    for name in tool.SAFE_FIELDS:
        value = tool.test_value(name, block)
        assert value != int(tool.decode(name, block))
        assert NAMES[name].minimum <= value <= NAMES[name].maximum
    block[NAMES["PLAYHI"].offset] = 30
    assert tool.test_value("PLAYLO", block) is None  # 48 or 36 would pass the high note


def test_a_refused_write_stops_the_run_and_the_value_is_written_back(tmp_path):
    bridge = WritableBridge(str(tmp_path), refuse="PLAYLO")
    with pytest.raises(tool.CheckStop, match="PLAYLO write failed") as stop:
        run(bridge, tmp_path)
    assert [r["field"] for r in stop.value.results] == ["PRIORT", "PLAYLO"]
    assert stop.value.results[1]["result"] == "write refused"
    assert bridge.writes[-1][0] == "PLAYLO"  # a restore attempt followed the refused write


def test_a_write_that_is_acknowledged_but_does_not_stick_stops(tmp_path):
    bridge = WritableBridge(str(tmp_path), drop="TRANSPOSE")
    with pytest.raises(tool.CheckStop, match="reads back 0, not 7") as stop:
        run(bridge, tmp_path)
    assert stop.value.results[-1]["field"] == "TRANSPOSE" and stop.value.results[-1]["result"] == "failed"
    assert [r["result"] for r in stop.value.results[:-1]] == ["pass"] * 3  # PRIORT, PLAYLO, PLAYHI


def test_a_write_that_moves_another_byte_stops_and_the_value_is_put_back(tmp_path):
    bridge = WritableBridge(str(tmp_path), spill="PRIORT")
    with pytest.raises(tool.CheckStop, match="so did 1 other byte"):
        run(bridge, tmp_path)
    assert [w[3] for w in bridge.writes] == [2, 1]  # written, then restored to the original; nothing after it


def test_a_restore_that_does_not_match_stops(tmp_path):
    class NoRestore(WritableBridge):
        def set_parameter(self, param, index, value, **kwargs):
            super().set_parameter(param, index, value, **kwargs)
            if len(self.writes) == 2:  # the restore of the first field
                self.parts[index][param.offset] = 3  # ...lands on something else

    with pytest.raises(tool.CheckStop, match="restore did not match") as stop:
        run(NoRestore(str(tmp_path)), tmp_path)
    assert stop.value.results[0]["restore"] == "differs"


def test_ctrl_c_during_the_pause_still_puts_the_value_back(tmp_path):
    bridge = WritableBridge(str(tmp_path))
    before = [bytes(b) for b in bridge.parts]

    def sleep(seconds):
        if seconds >= 1:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        tool.run_check(bridge, 16, tool.SAFE_FIELDS, hold=5, backup_dir=str(tmp_path), say=lambda k, t: None, sleep=sleep)
    assert [bytes(b) for b in bridge.parts] == before and len(bridge.writes) == 2


def test_the_hold_prompt_names_the_field_and_part(tmp_path):
    _results, _backup, lines = run(WritableBridge(str(tmp_path)), tmp_path, part=3, fields=("TRANSPOSE",), hold=4)
    assert ("LOOK", "TRANSPOSE is 7 on part 3 now: does the panel show it? (4 s)") in lines


def test_restore_writes_back_only_the_fields_that_differ(tmp_path):
    bridge = WritableBridge(str(tmp_path))
    backup = tool.save_backup(amf.read_from_bridge(bridge), str(tmp_path))
    bridge.parts[15][NAMES["TRANSPOSE"].offset] = 5
    bridge.parts[15][NAMES["PRIORT"].offset] = 3
    bridge.writes.clear()
    assert tool.restore_fields(bridge, backup, 16, say=lambda k, t: None, sleep=lambda s: None) == 2
    assert {w[0] for w in bridge.writes} == {"TRANSPOSE", "PRIORT"} and bridge.parts[15][NAMES["TRANSPOSE"].offset] == 0


def test_results_are_saved_as_json(tmp_path):
    import json

    results, backup, _ = run(WritableBridge(str(tmp_path)), tmp_path, fields=("PRIORT",))
    path = tool.save_results(results, backup, 16, backup_dir=str(tmp_path))
    assert json.load(open(path))["results"][0]["field"] == "PRIORT"


def test_the_command_line_needs_yes_for_anything_that_writes(capsys):
    assert tool.main(["run"]) == 2 and "WRITES" in capsys.readouterr().out
    assert tool.main(["restore", "x.m3"]) == 2
    assert tool.main(["run", "--yes", "--part", "17"]) == 2


def test_plan_sends_nothing_and_says_what_it_would_do(capsys, monkeypatch):
    monkeypatch.setattr(tool, "open_bridge", lambda: pytest.fail("plan must not open MIDI"))
    assert tool.main(["plan", "--part", "3", "--fields", "priort,transpose", "--hold", "2"]) == 0
    out = capsys.readouterr().out
    assert "part 3" in out and "PRIORT, TRANSPOSE" in out and "holding 2 s" in out
