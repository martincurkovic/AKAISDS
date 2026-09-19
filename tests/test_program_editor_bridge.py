# tests for core/program_editor_bridge.py - the loaders/writer QThreads and
# connect() that ProgramEditorWindow drives. No Qt event loop is needed here:
# each loader's run() is called directly (never .start()), so its signals
# fire as plain direct connections on the test thread - fast and deterministic.

import s3k.params as p

from core import program_editor_bridge
from core.program_editor_bridge import (
    KeygroupDetailLoader,
    KeygroupLoader,
    ParameterWriter,
    ProgramListLoader,
    SampleListLoader,
)


# --- connect() ---------------------------------------------------------------


def test_connect_returns_demo_bridge_when_env_var_set(monkeypatch):
    from s3ked.demo import DemoBridge

    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")

    bridge = program_editor_bridge.connect()

    assert isinstance(bridge, DemoBridge)


def test_connect_opens_saved_output_port_when_not_demo(monkeypatch):
    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    monkeypatch.setattr(
        program_editor_bridge.app_config,
        "get_saved_ports",
        lambda: ("Some Input", "Some Output"),
    )
    calls = []
    sentinel = object()

    def fake_standard(output_name):
        calls.append(output_name)
        return sentinel

    monkeypatch.setattr(program_editor_bridge.S3kBridge, "standard", fake_standard)

    bridge = program_editor_bridge.connect()

    assert bridge is sentinel
    assert calls == ["Some Output"]


# --- ProgramListLoader / SampleListLoader -------------------------------------


class _ListBridge:
    def __init__(self, items=None, error=None):
        self._items = items
        self._error = error

    def program_list(self):
        if self._error:
            raise self._error
        return self._items

    def sample_list(self):
        if self._error:
            raise self._error
        return self._items


def test_program_list_loader_emits_programs_on_success():
    loader = ProgramListLoader(_ListBridge(items=["Bass stab", "EPiano warm"]))
    loaded, failed = [], []
    loader.programs_loaded.connect(loaded.append)
    loader.load_failed.connect(failed.append)

    loader.run()

    assert loaded == [["Bass stab", "EPiano warm"]]
    assert failed == []


def test_program_list_loader_emits_load_failed_on_error():
    loader = ProgramListLoader(_ListBridge(error=RuntimeError("port closed")))
    loaded, failed = [], []
    loader.programs_loaded.connect(loaded.append)
    loader.load_failed.connect(failed.append)

    loader.run()

    assert loaded == []
    assert failed == ["port closed"]


def test_sample_list_loader_emits_samples_on_success():
    loader = SampleListLoader(_ListBridge(items=["SQUARE", "SAWTOOTH"]))
    loaded = []
    loader.samples_loaded.connect(loaded.append)

    loader.run()

    assert loaded == [["SQUARE", "SAWTOOTH"]]


def test_sample_list_loader_emits_load_failed_on_error():
    loader = SampleListLoader(_ListBridge(error=RuntimeError("no reply")))
    failed = []
    loader.load_failed.connect(failed.append)

    loader.run()

    assert failed == ["no reply"]


# --- KeygroupLoader ------------------------------------------------------------


class _OutOfRange(RuntimeError):
    """Stands in for s3ked.demo.DemoError: a RuntimeError, not a ValueError.

    KeygroupLoader used to discover the end of a program's keygroups by
    probing indices until a ValueError arrived - which matched the real
    S3kBridge but not DemoBridge, so every program looked keygroup-less
    against the demo sampler. It now reads GROUPS instead of probing, so
    this exception should never even be raised in the tests below - if it
    is, the loader regressed back to probing.
    """


class _KeygroupFakeBridge:
    def __init__(self, group_count, *, program_values=None, fail_on=None):
        self._group_count = group_count
        self._ranges = [(21 + i * 10, 30 + i * 10) for i in range(group_count)]
        self._program_values = program_values or {}
        self._fail_on = fail_on  # param name that should raise

    def get_parameter(self, param, _program_index, *, keygroup=0, **_kwargs):
        if self._fail_on == param.name:
            raise RuntimeError(f"could not read {param.name}")
        if param.name == "GROUPS":
            return self._group_count
        if param.name in self._program_values:
            return self._program_values[param.name]
        if param.name in ("PANPOS", "LFORAT", "LFODEP", "LFODEL", "LFO1WAVE", "POLYPH"):
            return 0
        if param.name in ("LONOTE", "HINOTE"):
            if not 0 <= keygroup < self._group_count:
                raise _OutOfRange(f"no keygroup {keygroup}")
            lo, hi = self._ranges[keygroup]
            return lo if param.name == "LONOTE" else hi
        raise AssertionError(f"unexpected parameter {param.name}")


def test_keygroup_loader_reads_exactly_groups_count_keygroups():
    bridge = _KeygroupFakeBridge(group_count=3)
    loader = KeygroupLoader(bridge, program_index=0)
    loaded, failed = [], []
    loader.keygroups_loaded.connect(lambda *a: loaded.append(a))
    loader.load_failed.connect(lambda *a: failed.append(a))

    loader.run()

    assert failed == []
    program_index, ranges, _values = loaded[0]
    assert program_index == 0
    assert ranges == [(21, 30), (31, 40), (41, 50)]


def test_keygroup_loader_handles_a_program_with_no_keygroups():
    bridge = _KeygroupFakeBridge(group_count=0)
    loader = KeygroupLoader(bridge, program_index=1)
    loaded = []
    loader.keygroups_loaded.connect(lambda *a: loaded.append(a))

    loader.run()

    assert loaded[0][1] == []


def test_keygroup_loader_reports_program_level_values():
    values = {
        "PANPOS": -5,
        "LFORAT": 1,
        "LFODEP": 2,
        "LFODEL": 3,
        "LFO1WAVE": 4,
        "POLYPH": 16,
    }
    bridge = _KeygroupFakeBridge(group_count=0, program_values=values)
    loader = KeygroupLoader(bridge, program_index=0)
    loaded = []
    loader.keygroups_loaded.connect(lambda *a: loaded.append(a))

    loader.run()

    assert loaded[0][2] == values


def test_keygroup_loader_emits_load_failed_when_groups_cannot_be_read():
    bridge = _KeygroupFakeBridge(group_count=2, fail_on="GROUPS")
    loader = KeygroupLoader(bridge, program_index=7)
    failed = []
    loader.load_failed.connect(lambda *a: failed.append(a))

    loader.run()

    assert failed == [(7, "could not read GROUPS")]


# --- KeygroupDetailLoader ------------------------------------------------------


class _EchoDetailBridge:
    """Returns each field's own name as its value, so assertions are trivial."""

    def get_parameter(self, param, _program_index, *, keygroup=0, **_kwargs):
        return param.name


def test_keygroup_detail_loader_reads_every_expected_field():
    loader = KeygroupDetailLoader(_EchoDetailBridge(), program_index=0, keygroup_index=1)
    loaded = []
    loader.detail_loaded.connect(lambda *a: loaded.append(a))

    loader.run()

    program_index, keygroup_index, values = loaded[0]
    assert (program_index, keygroup_index) == (0, 1)
    assert set(values) == set(KeygroupDetailLoader._FIELDS)
    assert all(values[field] == field for field in KeygroupDetailLoader._FIELDS)


def test_keygroup_detail_loader_emits_load_failed_on_error():
    class _FailingBridge:
        def get_parameter(self, *_a, **_kw):
            raise RuntimeError("device timed out")

    loader = KeygroupDetailLoader(_FailingBridge(), program_index=3, keygroup_index=2)
    failed = []
    loader.load_failed.connect(lambda *a: failed.append(a))

    loader.run()

    assert failed == [(3, 2, "device timed out")]


# --- ParameterWriter -----------------------------------------------------------


class _RecordingBridge:
    def __init__(self, error=None):
        self.calls = []
        self._error = error

    def set_parameter(self, param, program_index, value, *, keygroup=0):
        if self._error:
            raise self._error
        self.calls.append((param.name, program_index, value, keygroup))


def test_parameter_writer_writes_and_reports_the_new_value():
    bridge = _RecordingBridge()
    writer = ParameterWriter(
        bridge, "FILFRQ", "keygroup", program_index=2, new_value=500, keygroup_index=1
    )
    succeeded, failed = [], []
    writer.write_succeeded.connect(succeeded.append)
    writer.write_failed.connect(failed.append)

    writer.run()

    assert bridge.calls == [("FILFRQ", 2, 500, 1)]
    assert succeeded == [500]
    assert failed == []


def test_parameter_writer_defaults_keygroup_index_to_zero_for_program_params():
    bridge = _RecordingBridge()
    writer = ParameterWriter(bridge, "PANPOS", "program", program_index=4, new_value=-10)

    writer.run()

    assert bridge.calls == [("PANPOS", 4, -10, 0)]


def test_parameter_writer_emits_write_failed_on_error():
    bridge = _RecordingBridge(error=ValueError("out of range"))
    writer = ParameterWriter(bridge, "PANPOS", "program", program_index=0, new_value=99)
    succeeded, failed = [], []
    writer.write_succeeded.connect(succeeded.append)
    writer.write_failed.connect(failed.append)

    writer.run()

    assert succeeded == []
    assert failed == ["out of range"]
