# tests for core/program_editor_bridge.py - the single BridgeWorker thread
# and connect() that ProgramEditorWindow drives. No Qt event loop or real
# background thread is needed here: submit_*() queues a job and
# process_pending() drains it synchronously on the test thread, so its
# signals fire as plain direct connections - fast and deterministic.

import s3k.params as p

import pytest

from core import program_editor_bridge
from core.program_editor_bridge import (
    MULTI_PART_COUNT,
    BridgeWorker,
    LoggingBridge,
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


# --- BridgeWorker: program/sample lists ---------------------------------------


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


def test_worker_emits_programs_loaded_on_success():
    worker = BridgeWorker(_ListBridge(items=["Bass stab", "EPiano warm"]))
    loaded, failed = [], []
    worker.programs_loaded.connect(loaded.append)
    worker.programs_load_failed.connect(failed.append)

    worker.submit_program_list()
    worker.process_pending()

    assert loaded == [["Bass stab", "EPiano warm"]]
    assert failed == []


def test_worker_emits_programs_load_failed_on_error():
    worker = BridgeWorker(_ListBridge(error=RuntimeError("port closed")))
    loaded, failed = [], []
    worker.programs_loaded.connect(loaded.append)
    worker.programs_load_failed.connect(failed.append)

    worker.submit_program_list()
    worker.process_pending()

    assert loaded == []
    assert failed == ["port closed"]


def test_worker_reports_busy_true_then_false_around_one_job():
    worker = BridgeWorker(_ListBridge(items=["Bass stab"]))
    busy_states = []
    worker.busy_changed.connect(busy_states.append)

    worker.submit_program_list()
    # busy_changed(True) fires from submit_*() itself, synchronously, before
    # process_pending() ever runs a job - a real caller (the GUI thread)
    # needs to see "busy" the instant it asks for something, not once the
    # worker thread gets around to picking the job up
    assert busy_states == [True]

    worker.process_pending()

    assert busy_states == [True, False]


def test_worker_reports_one_continuous_busy_span_across_several_queued_jobs():
    # Refresh submits several jobs at once (program list, multi parts,
    # keygroups) - this should read as one busy period, not flicker
    # True/False/True/False between each one
    worker = BridgeWorker(_ListBridge(items=["Bass stab"]))
    busy_states = []
    worker.busy_changed.connect(busy_states.append)

    worker.submit_program_list()
    worker.submit_sample_list()
    worker.submit_program_list()
    assert busy_states == [True]  # still just the one rising edge

    worker.process_pending()

    assert busy_states == [True, False]


def test_worker_emits_samples_loaded_on_success():
    worker = BridgeWorker(_ListBridge(items=["SQUARE", "SAWTOOTH"]))
    loaded = []
    worker.samples_loaded.connect(loaded.append)

    worker.submit_sample_list()
    worker.process_pending()

    assert loaded == [["SQUARE", "SAWTOOTH"]]


def test_worker_emits_samples_load_failed_on_error():
    worker = BridgeWorker(_ListBridge(error=RuntimeError("no reply")))
    failed = []
    worker.samples_load_failed.connect(failed.append)

    worker.submit_sample_list()
    worker.process_pending()

    assert failed == ["no reply"]


# --- BridgeWorker: keygroups ---------------------------------------------------


class _OutOfRange(RuntimeError):
    """Stands in for s3ked.demo.DemoError: a RuntimeError, not a ValueError.

    The keygroups job used to discover the end of a program's keygroups by
    probing indices until a ValueError arrived - which matched the real
    S3kBridge but not DemoBridge, so every program looked keygroup-less
    against the demo sampler. It now reads GROUPS instead of probing, so
    this exception should never even be raised in the tests below - if it
    is, the job regressed back to probing.
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
        if param.name in (
            "PANPOS",
            "PRLOUD",
            "V_LOUD",
            "LFORAT",
            "LFODEP",
            "LFODEL",
            "LFO1WAVE",
            "POLYPH",
            "PMCHAN",
            "PTUNO",
            "PRIORT",
            "B_PTCH",
            "B_PTCHD",
            "PORTEN",
            "PORTIME",
            "PORTYPE",
        ):
            return 0
        if param.name in ("LONOTE", "HINOTE"):
            if not 0 <= keygroup < self._group_count:
                raise _OutOfRange(f"no keygroup {keygroup}")
            lo, hi = self._ranges[keygroup]
            return lo if param.name == "LONOTE" else hi
        raise AssertionError(f"unexpected parameter {param.name}")


def test_worker_reads_exactly_groups_count_keygroups():
    bridge = _KeygroupFakeBridge(group_count=3)
    worker = BridgeWorker(bridge)
    loaded, failed = [], []
    worker.keygroups_loaded.connect(lambda *a: loaded.append(a))
    worker.keygroups_load_failed.connect(lambda *a: failed.append(a))

    worker.submit_keygroups(0)
    worker.process_pending()

    assert failed == []
    program_index, ranges, _values = loaded[0]
    assert program_index == 0
    assert ranges == [(21, 30), (31, 40), (41, 50)]


def test_worker_handles_a_program_with_no_keygroups():
    bridge = _KeygroupFakeBridge(group_count=0)
    worker = BridgeWorker(bridge)
    loaded = []
    worker.keygroups_loaded.connect(lambda *a: loaded.append(a))

    worker.submit_keygroups(1)
    worker.process_pending()

    assert loaded[0][1] == []


def test_worker_reports_program_level_values():
    values = {
        "PANPOS": -5,
        "PRLOUD": 80,
        "V_LOUD": 20,
        "LFORAT": 1,
        "LFODEP": 2,
        "LFODEL": 3,
        "LFO1WAVE": 4,
        "POLYPH": 16,
        "PMCHAN": 9,
        "PTUNO": 256,
        "PRIORT": 2,
        "B_PTCH": 5,
        "B_PTCHD": 3,
        "PORTEN": 1,
        "PORTIME": 40,
        "PORTYPE": 1,
    }
    bridge = _KeygroupFakeBridge(group_count=0, program_values=values)
    worker = BridgeWorker(bridge)
    loaded = []
    worker.keygroups_loaded.connect(lambda *a: loaded.append(a))

    worker.submit_keygroups(0)
    worker.process_pending()

    assert loaded[0][2] == values


def test_worker_emits_keygroups_load_failed_when_groups_cannot_be_read():
    bridge = _KeygroupFakeBridge(group_count=2, fail_on="GROUPS")
    worker = BridgeWorker(bridge)
    failed = []
    worker.keygroups_load_failed.connect(lambda *a: failed.append(a))

    worker.submit_keygroups(7)
    worker.process_pending()

    assert failed == [(7, "could not read GROUPS")]


def test_worker_coalesces_queued_keygroups_requests_to_the_latest():
    # rapid clicking through programs used to queue a load per click - only
    # the last one selected should actually get processed once it's this
    # job's turn, not every intermediate one
    bridge = _KeygroupFakeBridge(group_count=0)
    worker = BridgeWorker(bridge)
    loaded = []
    worker.keygroups_loaded.connect(lambda *a: loaded.append(a))

    worker.submit_keygroups(0)
    worker.submit_keygroups(1)
    worker.submit_keygroups(2)
    worker.process_pending()

    assert [program_index for program_index, _, _ in loaded] == [2]


# --- BridgeWorker: keygroup detail ----------------------------------------------


class _EchoDetailBridge:
    """Returns each field's own name as its value, so assertions are trivial."""

    def get_parameter(self, param, _program_index, *, keygroup=0, **_kwargs):
        return param.name


def test_worker_reads_every_expected_detail_field():
    worker = BridgeWorker(_EchoDetailBridge())
    loaded = []
    worker.detail_loaded.connect(lambda *a: loaded.append(a))

    worker.submit_detail(0, 1)
    worker.process_pending()

    program_index, keygroup_index, values = loaded[0]
    assert (program_index, keygroup_index) == (0, 1)
    assert set(values) == set(program_editor_bridge._KEYGROUP_DETAIL_FIELDS)
    assert all(
        values[field] == field for field in program_editor_bridge._KEYGROUP_DETAIL_FIELDS
    )


def test_worker_emits_detail_load_failed_on_error():
    class _FailingBridge:
        def get_parameter(self, *_a, **_kw):
            raise RuntimeError("device timed out")

    worker = BridgeWorker(_FailingBridge())
    failed = []
    worker.detail_load_failed.connect(lambda *a: failed.append(a))

    worker.submit_detail(3, 2)
    worker.process_pending()

    assert failed == [(3, 2, "device timed out")]


# --- BridgeWorker: writes --------------------------------------------------------


class _RecordingBridge:
    def __init__(self, error=None):
        self.calls = []
        self._error = error

    def set_parameter(self, param, program_index, value, *, keygroup=0):
        if self._error:
            raise self._error
        self.calls.append((param.name, program_index, value, keygroup))


def test_worker_writes_and_reports_the_new_value():
    bridge = _RecordingBridge()
    worker = BridgeWorker(bridge)
    succeeded, failed = [], []
    worker.write_succeeded.connect(lambda *a: succeeded.append(a))
    worker.write_failed.connect(lambda *a: failed.append(a))

    worker.submit_write("FILFRQ", "FILFRQ", "keygroup", 2, 500, 1)
    worker.process_pending()

    assert bridge.calls == [("FILFRQ", 2, 500, 1)]
    assert succeeded == [("FILFRQ", "FILFRQ", 500)]
    assert failed == []


def test_worker_defaults_keygroup_index_to_zero_for_program_params():
    bridge = _RecordingBridge()
    worker = BridgeWorker(bridge)

    worker.submit_write("PANPOS", "PANPOS", "program", 4, -10, 0)
    worker.process_pending()

    assert bridge.calls == [("PANPOS", 4, -10, 0)]


def test_worker_emits_write_failed_on_error():
    bridge = _RecordingBridge(error=ValueError("out of range"))
    worker = BridgeWorker(bridge)
    succeeded, failed = [], []
    worker.write_succeeded.connect(lambda *a: succeeded.append(a))
    worker.write_failed.connect(lambda *a: failed.append(a))

    worker.submit_write("PANPOS", "PANPOS", "program", 0, 99, 0)
    worker.process_pending()

    assert succeeded == []
    assert failed == [("PANPOS", "PANPOS", "out of range")]


def test_worker_writes_are_never_coalesced():
    # unlike keygroups/detail/multi_parts reads, every write must reach the
    # hardware - queuing several in a row (distinct writer_keys, same as the
    # Multis tab's per-part PMCHAN writes) must not drop any of them
    bridge = _RecordingBridge()
    worker = BridgeWorker(bridge)

    worker.submit_write("multipart_channel_0", "PMCHAN", "multipart", 0, 5, 0)
    worker.submit_write("multipart_channel_1", "PMCHAN", "multipart", 1, 9, 0)
    worker.process_pending()

    assert ("PMCHAN", 0, 5, 0) in bridge.calls
    assert ("PMCHAN", 1, 9, 0) in bridge.calls


# --- BridgeWorker: multi parts ----------------------------------------------------


class _MultiPartsFakeBridge:
    def __init__(self, parts=None, error=None):
        # parts: list of (name, channel, level, pan) tuples, one per part,
        # in order
        self._parts = parts
        self._error = error

    def get_header(self, region, index, **kwargs):
        if self._error:
            raise self._error
        assert region == "multipart"
        name, channel, level, pan = self._parts[index]
        return {"PRNAME": name, "PMCHAN": channel, "STEREO": level, "PANPOS": pan}


def test_worker_reads_all_sixteen_parts_in_order():
    parts = [(f"Program {i}", i, 99, 0) for i in range(MULTI_PART_COUNT)]
    worker = BridgeWorker(_MultiPartsFakeBridge(parts=parts))
    loaded = []
    worker.parts_loaded.connect(loaded.append)

    worker.submit_multi_parts()
    worker.process_pending()

    assert loaded == [parts]


def test_worker_strips_padded_program_names():
    # PRNAME is a fixed-width, space-padded text field on real hardware
    parts = [("BASS ROUND   ", 0, 99, 0)] + [
        ("X", i, 99, 0) for i in range(1, MULTI_PART_COUNT)
    ]
    worker = BridgeWorker(_MultiPartsFakeBridge(parts=parts))
    loaded = []
    worker.parts_loaded.connect(loaded.append)

    worker.submit_multi_parts()
    worker.process_pending()

    assert loaded[0][0] == ("BASS ROUND", 0, 99, 0)


def test_worker_emits_multi_parts_load_failed_on_error():
    worker = BridgeWorker(_MultiPartsFakeBridge(error=RuntimeError("no reply")))
    failed = []
    worker.parts_load_failed.connect(failed.append)

    worker.submit_multi_parts()
    worker.process_pending()

    assert failed == ["no reply"]


# --- BridgeWorker: program change --------------------------------------------------


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


def test_worker_program_change_noops_without_a_live_midi_connection():
    # DemoBridge has no .out - nothing to send this on, but that's not a
    # failure: the UI should still show it as "sent" in demo mode
    worker = BridgeWorker(_NoOutBridge())
    sent, failed = [], []
    worker.change_sent.connect(lambda *a: sent.append(a))
    worker.change_send_failed.connect(lambda *a: failed.append(a))

    worker.submit_program_change(2, 0, "Bass stab", 3)
    worker.process_pending()

    assert sent == [(2, "Bass stab")]
    assert failed == []


def test_worker_program_change_sends_the_programs_own_midi_program_number():
    bridge = _RealBridgeForProgramChange(prgnum=42)
    worker = BridgeWorker(bridge)
    sent = []
    worker.change_sent.connect(lambda *a: sent.append(a))

    worker.submit_program_change(5, 1, "EPiano warm", 3)
    worker.process_pending()

    assert bridge.out.sent == [[0xC0 | 3, 42]]
    assert sent == [(5, "EPiano warm")]


def test_worker_program_change_uses_the_programs_own_number_not_its_list_index():
    # PRGNUM is independently assignable per program - never assume it
    # equals the program's position in the program list (program_index)
    bridge = _RealBridgeForProgramChange(prgnum=99)
    worker = BridgeWorker(bridge)

    worker.submit_program_change(0, 3, "Whatever", 0)
    worker.process_pending()

    assert bridge.out.sent == [[0xC0, 99]]


def test_worker_emits_change_send_failed_when_prgnum_cant_be_read():
    bridge = _RealBridgeForProgramChange(
        prgnum=0, prgnum_error=RuntimeError("device timed out")
    )
    worker = BridgeWorker(bridge)
    failed = []
    worker.change_send_failed.connect(lambda *a: failed.append(a))

    worker.submit_program_change(4, 0, "X", 0)
    worker.process_pending()

    assert failed == [(4, "device timed out")]


def test_worker_emits_change_send_failed_when_the_message_cant_be_sent():
    bridge = _RealBridgeForProgramChange(prgnum=1, out_error=RuntimeError("port closed"))
    worker = BridgeWorker(bridge)
    failed = []
    worker.change_send_failed.connect(lambda *a: failed.append(a))

    worker.submit_program_change(6, 0, "X", 0)
    worker.process_pending()

    assert failed == [(6, "port closed")]


def test_worker_masks_channel_and_program_to_valid_midi_ranges():
    # a stray value outside 0-15/0-127 must never corrupt the MIDI byte
    # stream - mask rather than trust the caller
    bridge = _RealBridgeForProgramChange(prgnum=200)  # out of the 0-127 range
    worker = BridgeWorker(bridge)

    worker.submit_program_change(0, 0, "X", 20)  # channel out of 0-15
    worker.process_pending()

    status_byte, program_byte = bridge.out.sent[0]
    assert status_byte == 0xC4  # 0xC0 | (20 & 0x0F)
    assert program_byte == 72  # 200 & 0x7F


class _RenumberingBridge(_RealBridgeForProgramChange):
    # stands in for the real S3kBridge/DemoBridge, both of which expose
    # renumber_programs() - see BridgeWorker._programs_renumbered
    def __init__(self, *, program_list=None, **kwargs):
        super().__init__(**kwargs)
        self.renumber_calls = 0
        self._program_list = program_list or []

    def renumber_programs(self):
        self.renumber_calls += 1

    def program_list(self):
        return self._program_list


def test_worker_renumbers_programs_once_before_the_first_program_change():
    # regression test for a real bug: multiple programs sharing PRGNUM 0
    # (the common case for freshly created/independently loaded programs)
    # made every part assignment send a Program Change for whichever
    # program the hardware associates with 0, regardless of which one was
    # actually picked - renumber_programs() gives each one a distinct
    # number first, so the right one is addressed
    bridge = _RenumberingBridge(prgnum=99)
    worker = BridgeWorker(bridge)

    worker.submit_program_change(0, 1, "Program B", 0)
    worker.process_pending()

    assert bridge.renumber_calls == 1
    assert bridge.out.sent == [[0xC0, 99]]


def test_worker_does_not_renumber_again_for_a_second_program_change():
    # renumbering rewrites every resident program's PRGNUM - repeating it
    # on every single part assignment would be needless SysEx traffic once
    # the roster is already known to be numbered distinctly
    bridge = _RenumberingBridge(prgnum=99)
    worker = BridgeWorker(bridge)

    worker.submit_program_change(0, 0, "A", 0)
    worker.submit_program_change(1, 1, "B", 0)
    worker.process_pending()

    assert bridge.renumber_calls == 1


def test_worker_renumbers_again_after_the_program_list_reloads():
    # a program created/loaded on the hardware since the last renumber may
    # not carry a number distinct from the rest - reloading the program
    # list (e.g. via Refresh) must make the next program change renumber
    # again rather than trusting a now-stale assumption
    bridge = _RenumberingBridge(prgnum=99, program_list=["A", "B"])
    worker = BridgeWorker(bridge)

    worker.submit_program_change(0, 0, "A", 0)
    worker.process_pending()
    worker.submit_program_list()
    worker.process_pending()
    worker.submit_program_change(0, 1, "B", 0)
    worker.process_pending()

    assert bridge.renumber_calls == 2


def test_worker_program_change_tolerates_a_bridge_without_renumber_programs():
    # DemoBridge and S3kBridge both implement renumber_programs(), but a
    # bridge that doesn't (e.g. a minimal test double) must not break the
    # program change itself
    bridge = _RealBridgeForProgramChange(prgnum=7)
    worker = BridgeWorker(bridge)

    worker.submit_program_change(0, 0, "X", 0)
    worker.process_pending()

    assert bridge.out.sent == [[0xC0, 7]]


def test_worker_processes_mixed_jobs_strictly_one_at_a_time_in_order():
    # the whole point of BridgeWorker: no two bridge calls are ever
    # in flight together, regardless of what kinds of requests piled up -
    # this is the direct regression test for the real-hardware crash (two
    # concurrent per-action QThreads interleaving SysEx frames on the wire)
    calls = []

    class _SlowBridge:
        def program_list(self):
            calls.append("program_list")
            return ["A"]

        def sample_list(self):
            calls.append("sample_list")
            return ["S"]

    worker = BridgeWorker(_SlowBridge())
    worker.submit_program_list()
    worker.submit_sample_list()
    worker.process_pending()

    assert calls == ["program_list", "sample_list"]


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
    # the exception must reach the caller unchanged (BridgeWorker's own
    # try/except still needs it for the right *_load_failed/write_failed
    # signal) and the log entry must carry the full traceback
    # (exc_info=True), not just str(e)
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
    # the program-change job talks to bridge.out directly (bypassing
    # get_parameter/set_parameter) - it needs the same logging coverage,
    # since a raw MIDI send races against the exact same connection
    logger = _FakeLogger()
    real_out = _RecordingOut()
    bridge = LoggingBridge(_OutBridge(real_out), logger)

    bridge.out.send_message([0xC0, 5])

    assert real_out.sent == [[0xC0, 5]]
    assert any("START out.send_message" in m for m in logger.debug_calls)


class _NoOutBridgeForLogging:
    pass


def test_logging_bridge_out_access_resolves_to_none_when_wrapped_bridge_has_none():
    # the program-change job relies on getattr(bridge, "out", None)
    # resolving to None for DemoBridge (which has no .out at all) - the
    # wrapper must not turn that missing attribute into something else
    bridge = LoggingBridge(_NoOutBridgeForLogging(), _FakeLogger())

    assert getattr(bridge, "out", None) is None
