# tests for core/program_editor_bridge.py - the loaders/writer QThreads and
# connect() that ProgramEditorWindow drives. No Qt event loop is needed here:
# each loader's run() is called directly (never .start()), so its signals
# fire as plain direct connections on the test thread - fast and deterministic.

import s3k.params as p

import pytest

from core import program_editor_bridge
from core.program_editor_bridge import (
    KeygroupDetailLoader,
    KeygroupLoader,
    LoggingBridge,
    MultiPartsLoader,
    ParameterWriter,
    ProgramChangeSender,
    ProgramListLoader,
    SampleListLoader,
)


# --- connect() ---------------------------------------------------------------
# connect() always wraps whatever bridge it builds in LoggingBridge (see
# core/debug_log.py's docstring for why) - these check the underlying
# bridge it wrapped, since that's what "returns a demo bridge"/"opens the
# saved port" actually mean now.


def test_connect_returns_demo_bridge_when_env_var_set(monkeypatch):
    from s3ked.demo import DemoBridge

    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")

    bridge = program_editor_bridge.connect()

    assert isinstance(bridge, LoggingBridge)
    assert isinstance(bridge._bridge, DemoBridge)


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

    assert isinstance(bridge, LoggingBridge)
    assert bridge._bridge is sentinel
    assert calls == ["Some Output"]
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


# --- MultiPartsLoader ----------------------------------------------------------


class _MultiPartsFakeBridge:
    def __init__(self, parts=None, error=None):
        # parts: list of (name, channel) tuples, one per part, in order
        self._parts = parts
        self._error = error

    def get_header(self, region, index, **kwargs):
        if self._error:
            raise self._error
        assert region == "multipart"
        name, channel = self._parts[index]
        return {"PRNAME": name, "PMCHAN": channel}


def test_multi_parts_loader_reads_all_sixteen_parts_in_order():
    parts = [(f"Program {i}", i) for i in range(MultiPartsLoader.PART_COUNT)]
    loader = MultiPartsLoader(_MultiPartsFakeBridge(parts=parts))
    loaded = []
    loader.parts_loaded.connect(loaded.append)

    loader.run()

    assert loaded == [parts]


def test_multi_parts_loader_strips_padded_program_names():
    # PRNAME is a fixed-width, space-padded text field on real hardware
    parts = [("BASS ROUND   ", 0)] + [
        ("X", i) for i in range(1, MultiPartsLoader.PART_COUNT)
    ]
    loader = MultiPartsLoader(_MultiPartsFakeBridge(parts=parts))
    loaded = []
    loader.parts_loaded.connect(loaded.append)

    loader.run()

    assert loaded[0][0] == ("BASS ROUND", 0)


def test_multi_parts_loader_emits_load_failed_on_error():
    loader = MultiPartsLoader(_MultiPartsFakeBridge(error=RuntimeError("no reply")))
    failed = []
    loader.load_failed.connect(failed.append)

    loader.run()

    assert failed == ["no reply"]


# --- ProgramChangeSender --------------------------------------------------------


class _NoOutBridge:
    """Stands in for DemoBridge: no live MIDI connection to send anything on."""


class _RecordingOut:
    def __init__(self, error=None):
        self.sent = []
        self._error = error

    def send_message(self, message):
        if self._error:
            raise self._error
        self.sent.append(message)


class _RealBridgeForProgramChange:
    def __init__(self, prgnum, *, out_error=None, prgnum_error=None):
        self.out = _RecordingOut(error=out_error)
        self._prgnum = prgnum
        self._prgnum_error = prgnum_error

    def get_parameter(self, param, program_index, **kwargs):
        if self._prgnum_error:
            raise self._prgnum_error
        assert param.name == "PRGNUM"
        return self._prgnum


def test_program_change_sender_noops_without_a_live_midi_connection():
    # DemoBridge has no .out - nothing to send this on, but that's not a
    # failure: the UI should still show it as "sent" in demo mode
    bridge = _NoOutBridge()
    sender = ProgramChangeSender(
        bridge, part_index=2, program_index=0, program_name="Bass stab", channel=3
    )
    sent, failed = [], []
    sender.change_sent.connect(lambda *a: sent.append(a))
    sender.send_failed.connect(lambda *a: failed.append(a))

    sender.run()

    assert sent == [(2, "Bass stab")]
    assert failed == []


def test_program_change_sender_sends_the_programs_own_midi_program_number():
    bridge = _RealBridgeForProgramChange(prgnum=42)
    sender = ProgramChangeSender(
        bridge, part_index=5, program_index=1, program_name="EPiano warm", channel=3
    )
    sent = []
    sender.change_sent.connect(lambda *a: sent.append(a))

    sender.run()

    assert bridge.out.sent == [[0xC0 | 3, 42]]
    assert sent == [(5, "EPiano warm")]


def test_program_change_sender_uses_the_programs_own_number_not_its_list_index():
    # PRGNUM is independently assignable per program - never assume it
    # equals the program's position in the program list (program_index)
    bridge = _RealBridgeForProgramChange(prgnum=99)
    sender = ProgramChangeSender(
        bridge, part_index=0, program_index=3, program_name="Whatever", channel=0
    )

    sender.run()

    assert bridge.out.sent == [[0xC0, 99]]


def test_program_change_sender_emits_send_failed_when_prgnum_cant_be_read():
    bridge = _RealBridgeForProgramChange(
        prgnum=0, prgnum_error=RuntimeError("device timed out")
    )
    sender = ProgramChangeSender(
        bridge, part_index=4, program_index=0, program_name="X", channel=0
    )
    failed = []
    sender.send_failed.connect(lambda *a: failed.append(a))

    sender.run()

    assert failed == [(4, "device timed out")]


def test_program_change_sender_emits_send_failed_when_the_message_cant_be_sent():
    bridge = _RealBridgeForProgramChange(prgnum=1, out_error=RuntimeError("port closed"))
    sender = ProgramChangeSender(
        bridge, part_index=6, program_index=0, program_name="X", channel=0
    )
    failed = []
    sender.send_failed.connect(lambda *a: failed.append(a))

    sender.run()

    assert failed == [(6, "port closed")]


def test_program_change_sender_masks_channel_and_program_to_valid_midi_ranges():
    # a stray value outside 0-15/0-127 must never corrupt the MIDI byte
    # stream - mask rather than trust the caller
    bridge = _RealBridgeForProgramChange(prgnum=200)  # out of the 0-127 range
    sender = ProgramChangeSender(
        bridge, part_index=0, program_index=0, program_name="X", channel=20  # out of 0-15
    )

    sender.run()

    status_byte, program_byte = bridge.out.sent[0]
    assert status_byte == 0xC4  # 0xC0 | (20 & 0x0F)
    assert program_byte == 72  # 200 & 0x7F


# --- LoggingBridge ---------------------------------------------------------


class _FakeLogger:
    def __init__(self):
        self.debug_calls = []
        self.error_calls = []

    def debug(self, message):
        self.debug_calls.append(message)

    def error(self, message, exc_info=False):
        self.error_calls.append((message, exc_info))


class _EchoBridge:
    def get_parameter(self, name, index):
        return f"{name}:{index}"


def test_logging_bridge_delegates_and_returns_the_real_result():
    bridge = LoggingBridge(_EchoBridge(), _FakeLogger())

    assert bridge.get_parameter("FILFRQ", 3) == "FILFRQ:3"


def test_logging_bridge_logs_start_and_end_on_success():
    logger = _FakeLogger()
    bridge = LoggingBridge(_EchoBridge(), logger)

    bridge.get_parameter("FILFRQ", 3)

    assert any("START get_parameter" in m for m in logger.debug_calls)
    assert any(
        "END get_parameter" in m and "FILFRQ:3" in m for m in logger.debug_calls
    )
    assert logger.error_calls == []


class _FailingBridge:
    def set_parameter(self, *args, **kwargs):
        raise RuntimeError("port closed")


def test_logging_bridge_logs_with_traceback_and_reraises_on_failure():
    # the exception must reach the caller unchanged (loaders/writers still
    # need it for their own load_failed/write_failed signals) and the log
    # entry must carry the full traceback (exc_info=True), not just str(e)
    logger = _FakeLogger()
    bridge = LoggingBridge(_FailingBridge(), logger)

    with pytest.raises(RuntimeError, match="port closed"):
        bridge.set_parameter("PANPOS", 0, 10)

    assert len(logger.error_calls) == 1
    message, exc_info = logger.error_calls[0]
    assert "FAILED set_parameter" in message
    assert exc_info is True


class _OutBridge:
    def __init__(self, out):
        self.out = out


def test_logging_bridge_wraps_out_and_logs_send_message():
    # ProgramChangeSender talks to bridge.out directly (bypassing
    # get_parameter/set_parameter) - it needs the same logging coverage,
    # since a raw MIDI send races against the exact same connection
    logger = _FakeLogger()
    real_out = _RecordingOut()  # the ProgramChangeSender fake, above
    bridge = LoggingBridge(_OutBridge(real_out), logger)

    bridge.out.send_message([0xC0, 5])

    assert real_out.sent == [[0xC0, 5]]
    assert any("START out.send_message" in m for m in logger.debug_calls)


class _NoOutBridge:
    pass


def test_logging_bridge_out_access_resolves_to_none_when_wrapped_bridge_has_none():
    # ProgramChangeSender relies on getattr(bridge, "out", None) resolving
    # to None for DemoBridge (which has no .out at all) - the wrapper must
    # not turn that missing attribute into something else
    bridge = LoggingBridge(_NoOutBridge(), _FakeLogger())

    assert getattr(bridge, "out", None) is None
