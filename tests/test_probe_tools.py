# the read-only probe tools must never READ the load/delete/save trigger registers (byte indices 6-9) - see tools/s2000_misc_probe.py NEVER_READ_INDEXES

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import s2000_full_dump as full_dump
import s2000_misc_probe as probe


def test_the_trigger_registers_are_listed():
    assert probe.NEVER_READ_INDEXES == {6, 7, 8, 9}


def test_the_misc_probe_never_requests_them(monkeypatch, tmp_path):
    asked = []
    monkeypatch.setattr(probe, "SNAPSHOT_DIR", str(tmp_path))
    monkeypatch.setattr(probe, "read_register", lambda bridge, bank, index, timeout: asked.append((bank, index)) or 1)
    monkeypatch.setattr(probe, "open_bridge", lambda: (None, object()))
    monkeypatch.setattr(probe.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    try:
        probe.stage_dump("t", max_index=20, banks=(1, 2, 3), timeout=0.1)
    except SystemExit:
        pass
    assert asked and not [a for a in asked if a[1] in (6, 7, 8, 9)]
    assert (1, 5) in asked and (1, 10) in asked


def test_the_full_dump_never_requests_them(monkeypatch):
    asked = []
    monkeypatch.setattr(probe, "read_register", lambda bridge, bank, index, timeout: asked.append((bank, index)) or 1)
    full_dump.dump_misc(object(), 20, 0.1)
    assert asked and not [a for a in asked if a[1] in (6, 7, 8, 9)]
    assert {(b, i) for b in (1, 2, 3) for i in (5, 10)} <= set(asked)
