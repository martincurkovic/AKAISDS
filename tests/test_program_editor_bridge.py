# tests for core/program_editor_bridge.py - the single BridgeWorker thread
# and connect() that ProgramEditorWindow drives. No Qt event loop or real
# background thread is needed here: submit_*() queues a job and
# process_pending() drains it synchronously on the test thread, so its
# signals fire as plain direct connections - fast and deterministic.

import json

import s3k.messages as m
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


# --- connect(): AKAISDS_SHARED_MIDI_TRANSPORT=1 (see core/midi_transport.py) -
# builds the Program Editor's own S3kBridge from a MidiManager's already-open
# raw ports instead of opening a second, independent connection - one real
# connection instead of two. See tests/midi_transport_consolidation_
# test_plan.md - validated on real hardware and now the default.


class _FakeMidiManagerWithRawPorts:
    def __init__(self, raw_input=None, raw_output=None, output_name="Shared Out"):
        self.raw_input = raw_input
        self.raw_output = raw_output
        self.output_name = output_name


def test_connect_uses_shared_transport_when_flag_set_and_ports_open(monkeypatch):
    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    monkeypatch.setenv("AKAISDS_SHARED_MIDI_TRANSPORT", "1")
    raw_in = object()
    raw_out = object()
    midi_manager = _FakeMidiManagerWithRawPorts(raw_in, raw_out, "Shared Out")

    calls = []
    sentinel = object()

    def fake_s3kbridge(midi_out, midi_in, description, **kwargs):
        calls.append((midi_out, midi_in, description))
        return sentinel

    def fake_throttled_out(port):
        return ("throttled", port)

    monkeypatch.setattr(program_editor_bridge, "S3kBridge", fake_s3kbridge)
    monkeypatch.setattr(program_editor_bridge, "ThrottledOut", fake_throttled_out)

    bridge = program_editor_bridge.connect(midi_manager)

    assert isinstance(bridge, LoggingBridge)
    assert bridge._bridge is sentinel
    assert len(calls) == 1
    midi_out, midi_in, description = calls[0]
    assert midi_out == ("throttled", raw_out)
    assert midi_in is raw_in
    assert description == "Shared Out (shared)"


def test_connect_falls_back_to_standard_when_flag_set_but_ports_not_open(monkeypatch):
    # AKAISDS_SHARED_MIDI_TRANSPORT=1 alone isn't enough - the MidiManager
    # passed in must actually have its raw ports open already (e.g. the
    # Dashboard's own connection hasn't been established yet)
    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    monkeypatch.setenv("AKAISDS_SHARED_MIDI_TRANSPORT", "1")
    monkeypatch.setattr(
        program_editor_bridge.app_config,
        "get_saved_ports",
        lambda: ("Some Input", "Some Output"),
    )
    sentinel = object()
    monkeypatch.setattr(
        program_editor_bridge.S3kBridge, "standard", lambda output_name: sentinel
    )
    midi_manager = _FakeMidiManagerWithRawPorts(raw_input=None, raw_output=None)

    bridge = program_editor_bridge.connect(midi_manager)

    assert bridge._bridge is sentinel


def test_connect_falls_back_to_standard_when_flag_set_but_no_midi_manager_given(
    monkeypatch,
):
    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    monkeypatch.setenv("AKAISDS_SHARED_MIDI_TRANSPORT", "1")
    monkeypatch.setattr(
        program_editor_bridge.app_config,
        "get_saved_ports",
        lambda: ("Some Input", "Some Output"),
    )
    sentinel = object()
    monkeypatch.setattr(
        program_editor_bridge.S3kBridge, "standard", lambda output_name: sentinel
    )

    bridge = program_editor_bridge.connect()  # no midi_manager at all

    assert bridge._bridge is sentinel


def test_connect_ignores_shared_transport_when_flag_is_off(monkeypatch):
    # a MidiManager with open raw ports handed in, but the flag itself is
    # off - must still take the ordinary S3kBridge.standard() path.
    # Explicitly "0" (not delenv) - shared_transport_enabled() now falls
    # back to config.json's "shared_midi_transport" (default True) when
    # the env var is unset, so merely unsetting it here would no longer be
    # deterministic (leaks in whatever the test machine's own config.json,
    # or the new hardcoded default, happens to say)
    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    monkeypatch.setenv("AKAISDS_SHARED_MIDI_TRANSPORT", "0")
    monkeypatch.setattr(
        program_editor_bridge.app_config,
        "get_saved_ports",
        lambda: ("Some Input", "Some Output"),
    )
    sentinel = object()
    monkeypatch.setattr(
        program_editor_bridge.S3kBridge, "standard", lambda output_name: sentinel
    )
    midi_manager = _FakeMidiManagerWithRawPorts(object(), object())

    bridge = program_editor_bridge.connect(midi_manager)

    assert bridge._bridge is sentinel


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


def test_set_bridge_swaps_which_bridge_later_jobs_dispatch_to():
    # ui/program_editor_window.py's own reconnect-in-place path
    # (_reconnect_shared_bridge) - rebuilding the bridge after the
    # shared MIDI ports get reopened out from under this worker, without
    # tearing down and recreating the whole QThread/its Signal connections
    old_bridge = _ListBridge(items=["Old list"])
    new_bridge = _ListBridge(items=["New list"])
    worker = BridgeWorker(old_bridge)
    loaded = []
    worker.programs_loaded.connect(loaded.append)

    worker.submit_program_list()
    worker.process_pending()
    assert loaded == [["Old list"]]

    worker.set_bridge(new_bridge)
    worker.submit_program_list()
    worker.process_pending()

    assert loaded == [["Old list"], ["New list"]]


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


def test_is_idle_reflects_queued_and_in_flight_work():
    # ui/program_editor_window.py's own _reconnect_shared_bridge uses this
    # to decide whether there's anything worth waiting for at all before
    # it ever waits for busy_changed(False) - wait_until_idle() would
    # freeze the GUI thread (see its own docstring), so a caller on that
    # thread needs a non-blocking check first
    worker = BridgeWorker(_ListBridge(items=["Bass stab"]))
    assert worker.is_idle() is True

    worker.submit_program_list()
    assert worker.is_idle() is False

    worker.process_pending()
    assert worker.is_idle() is True


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


def test_worker_survives_a_job_whose_dispatch_raises_outside_its_own_handler():
    # a real incident: an exception escaping _dispatch() (not caught by
    # the handler's own try/except - e.g. a bug in the handler itself, or
    # a malformed job tuple) used to propagate straight out of run(),
    # silently killing the whole worker thread for the rest of the app's
    # life. The GUI thread stayed completely responsive (submit_*() just
    # keeps appending to the queue), but nothing ever processed a request
    # again - no error, no crash, just "loading" that never completes.
    # _safe_dispatch's job is to make sure ONE bad job can't do that -
    # queue an unroutable job kind directly (bypassing the normal
    # submit_*() methods, which only ever produce valid kinds) to force
    # exactly the AttributeError _dispatch's getattr() would raise, then
    # confirm the worker is still alive and processes the next real job.
    worker = BridgeWorker(_ListBridge(items=["Bass stab"]))
    loaded = []
    worker.programs_loaded.connect(loaded.append)

    worker._submit(("not_a_real_job_kind",))
    worker.submit_program_list()
    worker.process_pending()

    assert loaded == [["Bass stab"]]


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
            "PRGNUM",
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
            "LEGATO",
            "PANRAT",
            "PANDEP",
            "PANDEL",
            "LFO2WAVE",
            "LFO2TRIG",
            "DESYNC",
            "MODSPAN1",
            "MODSPAN2",
            "MODSPAN3",
            "MODVPAN1",
            "MODVPAN2",
            "MODVPAN3",
            "MODSAMP1",
            "MODSAMP2",
            "MODSAMP3",
            "MODVAMP1",
            "MODVAMP2",
            "MODSLFOT",
            "MODSLFOL",
            "MODSLFOD",
            "MODVLFOR",
            "MODVLVOL",
            "MODVLFOD",
            "MODSFILT1",
            "MODSFILT2",
            "MODSFILT3",
            "MODSPITCH",
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
        "PRGNUM": 12,
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
        "LEGATO": 1,
        "PANRAT": 6,
        "PANDEP": 7,
        "PANDEL": 8,
        "LFO2WAVE": 2,
        "LFO2TRIG": 1,
        "DESYNC": 1,
        "MODSPAN1": 1,
        "MODSPAN2": 2,
        "MODSPAN3": 3,
        "MODVPAN1": 10,
        "MODVPAN2": -10,
        "MODVPAN3": 15,
        "MODSAMP1": 5,
        "MODSAMP2": 6,
        "MODSAMP3": 7,
        "MODVAMP1": 20,
        "MODVAMP2": -20,
        "MODSLFOT": 7,
        "MODSLFOL": 8,
        "MODSLFOD": 9,
        "MODVLFOR": 25,
        "MODVLVOL": -25,
        "MODVLFOD": 30,
        "MODSFILT1": 9,
        "MODSFILT2": 10,
        "MODSFILT3": 11,
        "MODSPITCH": 8,
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


# --- BridgeWorker: sample detail (Samples tab / loop points) -----------------


def test_worker_reads_every_expected_sample_detail_field():
    worker = BridgeWorker(_EchoDetailBridge())
    loaded = []
    worker.sample_detail_loaded.connect(lambda *a: loaded.append(a))

    worker.submit_sample_detail(5)
    worker.process_pending()

    sample_index, values = loaded[0]
    assert sample_index == 5
    assert set(values) == set(program_editor_bridge._SAMPLE_DETAIL_FIELDS)
    assert all(
        values[field] == field
        for field in program_editor_bridge._SAMPLE_DETAIL_FIELDS
    )


def test_worker_emits_sample_detail_load_failed_on_error():
    class _FailingBridge:
        def get_parameter(self, *_a, **_kw):
            raise RuntimeError("device timed out")

    worker = BridgeWorker(_FailingBridge())
    failed = []
    worker.sample_detail_load_failed.connect(lambda *a: failed.append(a))

    worker.submit_sample_detail(7)
    worker.process_pending()

    assert failed == [(7, "device timed out")]


def test_worker_coalesces_queued_sample_detail_requests_to_the_latest():
    worker = BridgeWorker(_EchoDetailBridge())
    loaded = []
    worker.sample_detail_loaded.connect(lambda *a: loaded.append(a))

    # same "get me the current state of X" reasoning as keygroups/detail/
    # multi_parts - rapid clicking through the sample list shouldn't queue
    # up a backlog of stale header reads
    worker.submit_sample_detail(0)
    worker.submit_sample_detail(1)
    worker.submit_sample_detail(2)
    worker.process_pending()

    assert [sample_index for sample_index, _values in loaded] == [2]


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


# --- BridgeWorker: delete sample ---------------------------------------------


class _DeleteSampleBridge:
    def __init__(self, error=None):
        self.calls = []
        self._error = error

    def delete_sample(self, sample_index, *, confirm=True):
        if self._error:
            raise self._error
        self.calls.append(sample_index)


def test_worker_deletes_sample_and_reports_success():
    bridge = _DeleteSampleBridge()
    worker = BridgeWorker(bridge)
    deleted, failed = [], []
    worker.sample_deleted.connect(deleted.append)
    worker.sample_delete_failed.connect(lambda *a: failed.append(a))

    worker.submit_delete_sample(3)
    worker.process_pending()

    assert bridge.calls == [3]
    assert deleted == [3]
    assert failed == []


def test_worker_emits_sample_delete_failed_on_error():
    bridge = _DeleteSampleBridge(error=RuntimeError("device timed out"))
    worker = BridgeWorker(bridge)
    deleted, failed = [], []
    worker.sample_deleted.connect(deleted.append)
    worker.sample_delete_failed.connect(lambda *a: failed.append(a))

    worker.submit_delete_sample(2)
    worker.process_pending()

    assert deleted == []
    assert failed == [(2, "device timed out")]


def test_worker_deletes_samples_are_never_coalesced():
    # same reasoning as writes - every confirmed delete must reach the
    # hardware, unlike the coalesced "current state of X" read kinds
    bridge = _DeleteSampleBridge()
    worker = BridgeWorker(bridge)

    worker.submit_delete_sample(0)
    worker.submit_delete_sample(1)
    worker.process_pending()

    assert bridge.calls == [0, 1]


# --- BridgeWorker: create program/keygroup (PDATA/KDATA) --------------------
#
# Unlike every other fake bridge in this file, send_and_receive here decodes
# every frame for REAL via s3k.messages (parse_frame + decode_nibbles) -
# a bug in program_editor_bridge's own frame-building or reply-reading would
# show up as a decode failure or a wrong recorded value here, not just get
# rubber-stamped by a bridge that trusts whatever it's handed. This is the
# same protocol these two handlers were written against - see
# akai_sysex.build_pdata_request/build_kdata_request's own docstrings and
# program_editor_bridge.py's "create program/keygroup" section comment.


class _CreateBridge:
    def __init__(self, program_names, program_keygroups, error=None,
                 error_on_step=None, reply_error_on_step=None, insert_at=None):
        # insert_at: where a program created by a PDATA past the end really
        # lands. None = appended; an int simulates the sampler's PRGNUM
        # ordering (measured on a real S2000: a clone sorts right after its
        # template, i.e. BETWEEN two existing programs)
        self._insert_at = insert_at
        # program_names: list[str], index = program index
        # program_keygroups: {program_index: [(lonote, hinote), ...]} - one
        # tuple per keygroup, used to build distinguishable fake keygroup
        # headers so a test can tell which source keygroup a KDATA write
        # actually cloned
        self._program_names = list(program_names)
        self._program_keygroups = program_keygroups
        self._error = error
        self._error_on_step = error_on_step  # 1-indexed send_and_receive call to fail on
        # like error_on_step, but instead of raising, answers with a
        # well-formed REPLY whose code is ReplyCode.ERROR - distinct code
        # path from a raised exception (a real device can reject a write
        # without the transport itself failing)
        self._reply_error_on_step = reply_error_on_step
        self.pdata_writes = []  # [(program_index, groups, name), ...]
        self.kdata_writes = []  # [(program_index, keygroup_index, lonote, hinote), ...]
        self._send_count = 0

    def program_list(self):
        return list(self._program_names)

    def get_parameter(self, param, index, **kwargs):
        assert (param.region, param.name) == ("program", "GROUPS")
        return len(self._program_keygroups[index])

    def get_header_bytes(self, region, index, offset, count, selector=0, **kwargs):
        assert offset == 0 and count == 192
        header = bytearray(192)
        if region == "program":
            header[0] = 0x01  # BLOCK_IDENT["program"], see s3k.bridge
            name_param = p.lookup("PRNAME", "program")
            header[name_param.offset : name_param.offset + name_param.size] = (
                p.encode_field(name_param, self._program_names[index])
            )
            groups_param = p.lookup("GROUPS", "program")
            header[groups_param.offset : groups_param.offset + groups_param.size] = (
                p.encode_field(groups_param, len(self._program_keygroups[index]))
            )
            return bytes(header)
        if region == "keygroup":
            header[0] = 0x02  # BLOCK_IDENT["keygroup"]
            lo, hi = self._program_keygroups[index][selector]
            lo_param = p.lookup("LONOTE", "keygroup")
            hi_param = p.lookup("HINOTE", "keygroup")
            header[lo_param.offset : lo_param.offset + lo_param.size] = (
                p.encode_field(lo_param, lo)
            )
            header[hi_param.offset : hi_param.offset + hi_param.size] = (
                p.encode_field(hi_param, hi)
            )
            return bytes(header)
        raise AssertionError(f"unexpected region {region!r}")

    def send_and_receive(self, frame, timeout=None):
        self._send_count += 1
        if self._error is not None and self._send_count == self._error_on_step:
            raise self._error
        if self._send_count == self._reply_error_on_step:
            return m.Reply(code=int(m.ReplyCode.ERROR)).encode()
        _channel, command, payload = m.parse_frame(frame)
        if command == m.Command.PDATA:
            program_index = payload[0] | (payload[1] << 7)
            header = m.decode_nibbles(payload[2:])
            name_param = p.lookup("PRNAME", "program")
            groups_param = p.lookup("GROUPS", "program")
            name = p.decode_field(
                name_param,
                header[name_param.offset : name_param.offset + name_param.size],
            ).strip()
            groups = p.decode_field(
                groups_param,
                header[groups_param.offset : groups_param.offset + groups_param.size],
            )
            self.pdata_writes.append((program_index, groups, name))
            if program_index >= len(self._program_names):
                at = len(self._program_names) if self._insert_at is None else self._insert_at
                self._program_names.insert(at, name)
                shifted = {
                    (i + 1 if i >= at else i): kgs
                    for i, kgs in self._program_keygroups.items()
                }
                shifted[at] = []
                self._program_keygroups = shifted
        elif command == m.Command.KDATA:
            program_index = payload[0] | (payload[1] << 7)
            keygroup_index = payload[2]
            header = m.decode_nibbles(payload[3:])
            lo_param = p.lookup("LONOTE", "keygroup")
            hi_param = p.lookup("HINOTE", "keygroup")
            lo = p.decode_field(
                lo_param, header[lo_param.offset : lo_param.offset + lo_param.size]
            )
            hi = p.decode_field(
                hi_param, header[hi_param.offset : hi_param.offset + hi_param.size]
            )
            self.kdata_writes.append((program_index, keygroup_index, lo, hi))
            self._program_keygroups.setdefault(program_index, []).append((lo, hi))
        else:
            raise AssertionError(f"unexpected command {command:#04x}")
        return m.Reply(code=int(m.ReplyCode.OK)).encode()


def test_worker_creates_program_with_every_source_keygroup_cloned():
    bridge = _CreateBridge(
        program_names=["BASS STAB"],
        program_keygroups={0: [(24, 60), (61, 96), (97, 108)]},
    )
    worker = BridgeWorker(bridge)
    created, failed = [], []
    worker.program_created.connect(lambda *a: created.append(a))
    worker.program_create_failed.connect(lambda *a: failed.append(a))

    worker.submit_create_program(0, "NEW PROG")
    worker.process_pending()

    assert failed == []
    assert created == [(0, 1)]  # source_index, new_index (appended at index 1)
    # keygroup 0 bootstrapped with groups=1, then groups bumped to 2 and 3
    # as keygroups 1 and 2 are added - every PDATA write, in order
    assert bridge.pdata_writes == [
        (1, 1, "NEW PROG"),
        (1, 2, "NEW PROG"),
        (1, 3, "NEW PROG"),
    ]
    # every source keygroup cloned, at the matching new index, in order
    assert bridge.kdata_writes == [
        (1, 0, 24, 60),
        (1, 1, 61, 96),
        (1, 2, 97, 108),
    ]


def test_worker_creates_program_at_the_index_the_sampler_gave_it():
    # real S2000 log (2026-10-07): the clone landed BETWEEN TEST PROGRAM and
    # SKIBIDI, so the old "new_index = len(list)" sent every KDATA/PDATA to
    # SKIBIDI and left the new program with an unwritten keygroup
    bridge = _CreateBridge(
        program_names=["TEST PROGRAM", "SKIBIDI"],
        program_keygroups={0: [(24, 60), (61, 96)], 1: [(0, 127)]},
        insert_at=1,
    )
    worker = BridgeWorker(bridge)
    created, failed = [], []
    worker.program_created.connect(lambda *a: created.append(a))
    worker.program_create_failed.connect(lambda *a: failed.append(a))

    worker.submit_create_program(0, "CW AMEN CHOP")
    worker.process_pending()

    assert failed == []
    assert created == [(0, 1)]
    # the first PDATA is addressed past the end; everything after it follows
    # the new program to index 1 and never touches SKIBIDI (now index 2)
    assert bridge.pdata_writes == [
        (2, 1, "CW AMEN CHOP"),
        (1, 2, "CW AMEN CHOP"),
    ]
    assert bridge.kdata_writes == [(1, 0, 24, 60), (1, 1, 61, 96)]
    assert bridge.program_list() == ["TEST PROGRAM", "CW AMEN CHOP", "SKIBIDI"]


def test_worker_refuses_to_guess_when_the_program_list_did_not_grow():
    bridge = _CreateBridge(
        program_names=["BASS STAB"], program_keygroups={0: [(24, 60)]}
    )
    bridge.program_list = lambda: ["BASS STAB"]  # the sampler "lost" the PDATA
    worker = BridgeWorker(bridge)
    created, failed = [], []
    worker.program_created.connect(lambda *a: created.append(a))
    worker.program_create_failed.connect(lambda *a: failed.append(a))

    worker.submit_create_program(0, "NEW PROG")
    worker.process_pending()

    assert created == []
    assert len(failed) == 1 and "can't tell where" in failed[0][1]
    assert bridge.kdata_writes == []


def test_worker_emits_program_create_failed_on_error_mid_sequence():
    # fails on the 2nd send_and_receive call (keygroup 0's KDATA, right
    # after the bootstrap PDATA succeeded) - nothing after that point
    # should have been sent, and no program_created should fire
    bridge = _CreateBridge(
        program_names=["BASS STAB"],
        program_keygroups={0: [(24, 60), (61, 96)]},
        error=RuntimeError("device rejected it"),
        error_on_step=2,
    )
    worker = BridgeWorker(bridge)
    created, failed = [], []
    worker.program_created.connect(lambda *a: created.append(a))
    worker.program_create_failed.connect(lambda *a: failed.append(a))

    worker.submit_create_program(0, "NEW PROG")
    worker.process_pending()

    assert created == []
    assert failed == [(0, "device rejected it")]
    assert bridge.pdata_writes == [(1, 1, "NEW PROG")]  # the bootstrap only
    assert bridge.kdata_writes == []


def test_worker_creates_keygroup_kdata_then_pdata_order():
    bridge = _CreateBridge(
        program_names=["BASS STAB"],
        program_keygroups={0: [(24, 60), (61, 96)]},
    )
    worker = BridgeWorker(bridge)
    created, failed = [], []
    worker.keygroup_created.connect(lambda *a: created.append(a))
    worker.keygroup_create_failed.connect(lambda *a: failed.append(a))

    worker.submit_create_keygroup(0, 1)  # clone keygroup 1 (61-96)
    worker.process_pending()

    assert failed == []
    assert created == [(0, 2)]  # program_index, new_keygroup_index (appended)
    # KDATA (the clone) FIRST, THEN the whole-program PDATA with groups+1 -
    # s3000editor's own "add keygroup to an existing program" order; the
    # program already exists here so it's safe to write the new keygroup's
    # storage before officially raising the group count
    assert bridge.kdata_writes == [(0, 2, 61, 96)]
    assert bridge.pdata_writes == [(0, 3, "BASS STAB")]


def test_worker_emits_keygroup_create_failed_on_error():
    bridge = _CreateBridge(
        program_names=["BASS STAB"],
        program_keygroups={0: [(24, 60)]},
        error=RuntimeError("device timed out"),
        error_on_step=1,
    )
    worker = BridgeWorker(bridge)
    created, failed = [], []
    worker.keygroup_created.connect(lambda *a: created.append(a))
    worker.keygroup_create_failed.connect(lambda *a: failed.append(a))

    worker.submit_create_keygroup(0, 0)
    worker.process_pending()

    assert created == []
    assert failed == [(0, "device timed out")]
    assert bridge.kdata_writes == []
    assert bridge.pdata_writes == []


def test_worker_refuses_a_keygroup_beyond_the_ninety_nine_cap():
    bridge = _CreateBridge(
        program_names=["FULL PROG"],
        program_keygroups={0: [(24, 24)] * 99},
    )
    worker = BridgeWorker(bridge)
    created, failed = [], []
    worker.keygroup_created.connect(lambda *a: created.append(a))
    worker.keygroup_create_failed.connect(lambda *a: failed.append(a))

    worker.submit_create_keygroup(0, 0)
    worker.process_pending()

    assert created == []
    assert len(failed) == 1
    assert failed[0][0] == 0
    assert "99" in failed[0][1]
    # refused before ever touching the wire
    assert bridge.kdata_writes == []
    assert bridge.pdata_writes == []


def test_worker_create_program_fails_cleanly_without_send_and_receive():
    # the defensive backstop for demo mode (s3ked's own DemoBridge has no
    # add-program primitive at all) - a bridge with no send_and_receive at
    # all should fail with a clear message, not a raw AttributeError, even
    # though the UI layer already disables the action in demo mode before
    # this can normally be reached
    class _NoSendBridge:
        def program_list(self):
            return ["BASS STAB"]

        def get_parameter(self, param, index, **kwargs):
            return 1

    worker = BridgeWorker(_NoSendBridge())
    failed = []
    worker.program_create_failed.connect(lambda *a: failed.append(a))

    worker.submit_create_program(0, "NEW PROG")
    worker.process_pending()

    assert len(failed) == 1
    assert failed[0][0] == 0
    assert "demo mode" in failed[0][1]


def test_worker_creates_program_with_a_single_keygroup_source():
    # group_count=1 means _handle_create_program's "every further keygroup"
    # loop (range(1, group_count)) never runs at all - only the bootstrap
    # PDATA(groups=1) + KDATA(0) pair should hit the wire
    bridge = _CreateBridge(
        program_names=["BASS STAB"],
        program_keygroups={0: [(24, 60)]},
    )
    worker = BridgeWorker(bridge)
    created, failed = [], []
    worker.program_created.connect(lambda *a: created.append(a))
    worker.program_create_failed.connect(lambda *a: failed.append(a))

    worker.submit_create_program(0, "NEW PROG")
    worker.process_pending()

    assert failed == []
    assert created == [(0, 1)]
    assert bridge.pdata_writes == [(1, 1, "NEW PROG")]
    assert bridge.kdata_writes == [(1, 0, 24, 60)]


def test_worker_emits_program_create_failed_when_device_replies_error_code():
    # distinct from the raised-exception path above - here the transport
    # itself succeeds and the device answers with a well-formed REPLY whose
    # own code says ERROR, which _send_and_check must also treat as failure
    bridge = _CreateBridge(
        program_names=["BASS STAB"],
        program_keygroups={0: [(24, 60)]},
        reply_error_on_step=1,
    )
    worker = BridgeWorker(bridge)
    created, failed = [], []
    worker.program_created.connect(lambda *a: created.append(a))
    worker.program_create_failed.connect(lambda *a: failed.append(a))

    worker.submit_create_program(0, "NEW PROG")
    worker.process_pending()

    assert created == []
    assert len(failed) == 1
    assert failed[0][0] == 0
    assert "rejected" in failed[0][1]
    assert bridge.kdata_writes == []


def test_worker_emits_keygroup_create_failed_when_device_replies_error_code():
    bridge = _CreateBridge(
        program_names=["BASS STAB"],
        program_keygroups={0: [(24, 60)]},
        reply_error_on_step=1,
    )
    worker = BridgeWorker(bridge)
    created, failed = [], []
    worker.keygroup_created.connect(lambda *a: created.append(a))
    worker.keygroup_create_failed.connect(lambda *a: failed.append(a))

    worker.submit_create_keygroup(0, 0)
    worker.process_pending()

    assert created == []
    assert len(failed) == 1
    assert failed[0][0] == 0
    assert "rejected" in failed[0][1]
    assert bridge.pdata_writes == []


def test_worker_creates_keygroup_when_source_program_has_only_one_keygroup():
    bridge = _CreateBridge(
        program_names=["BASS STAB"],
        program_keygroups={0: [(24, 60)]},
    )
    worker = BridgeWorker(bridge)
    created, failed = [], []
    worker.keygroup_created.connect(lambda *a: created.append(a))
    worker.keygroup_create_failed.connect(lambda *a: failed.append(a))

    worker.submit_create_keygroup(0, 0)
    worker.process_pending()

    assert failed == []
    assert created == [(0, 1)]
    assert bridge.kdata_writes == [(0, 1, 24, 60)]
    assert bridge.pdata_writes == [(0, 2, "BASS STAB")]


def test_worker_create_jobs_are_never_coalesced():
    # "create_program"/"create_keygroup" aren't in _COALESCE_KINDS - unlike
    # the "give me the current state of X" read kinds, every confirmed
    # duplication must actually reach the hardware, not get dropped in
    # favour of a later one queued before the first even started. Submit
    # two before processing anything, same shape as
    # test_worker_deletes_samples_are_never_coalesced above.
    bridge = _CreateBridge(
        program_names=["A", "B"],
        program_keygroups={0: [(24, 60)], 1: [(24, 60)]},
    )
    worker = BridgeWorker(bridge)
    created = []
    worker.program_created.connect(lambda *a: created.append(a))

    worker.submit_create_program(0, "COPY A")
    worker.submit_create_program(1, "COPY B")
    worker.process_pending()

    # both jobs actually ran - neither was dropped in favour of the other -
    # two PDATA bootstraps and two program_created signals, one per job
    assert len(created) == 2
    assert [name for _index, _groups, name in bridge.pdata_writes if _groups == 1] == [
        "COPY A",
        "COPY B",
    ]


# --- BridgeWorker: multi parts ----------------------------------------------------


class _MultiPartsFakeBridge:
    def __init__(self, parts=None, error=None, multi_name="DEMO MULTI"):
        # parts: list of (name, channel, level, pan) tuples, one per part,
        # in order
        self._parts = parts
        self._error = error
        self._multi_name = multi_name

    def get_header(self, region, index, **kwargs):
        if self._error:
            raise self._error
        assert region == "multipart"
        name, channel, level, pan = self._parts[index]
        return {"PRNAME": name, "PMCHAN": channel, "STEREO": level, "PANPOS": pan}

    def get_parameter(self, param, _index, **kwargs):
        if self._error:
            raise self._error
        assert (param.region, param.name) == ("multi", "MULTINAME")
        return self._multi_name


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


def test_worker_emits_multi_name_loaded_alongside_parts():
    parts = [(f"Program {i}", i, 99, 0) for i in range(MULTI_PART_COUNT)]
    worker = BridgeWorker(
        _MultiPartsFakeBridge(parts=parts, multi_name="MY MULTI   ")  # padded
    )
    names = []
    worker.multi_name_loaded.connect(names.append)

    worker.submit_multi_parts()
    worker.process_pending()

    assert names == ["MY MULTI"]  # .strip()'d, same as PRNAME above


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
    # _prgnum is what get_parameter returns - i.e. the PANEL's 1-based
    # number, since s3ked's PRGNUM carries display_offset=1 (§267) and
    # get_parameter applies it. BridgeWorker subtracts the offset back out
    # before sending, so the wire byte in every test below is _prgnum - 1.
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

    assert bridge.out.sent == [[0xC0 | 3, 41]]  # 42 (panel) - display_offset 1
    assert sent == [(5, "EPiano warm")]


def test_worker_program_change_uses_the_programs_own_number_not_its_list_index():
    # PRGNUM is independently assignable per program - never assume it
    # equals the program's position in the program list (program_index)
    bridge = _RealBridgeForProgramChange(prgnum=99)
    worker = BridgeWorker(bridge)

    worker.submit_program_change(0, 3, "Whatever", 0)
    worker.process_pending()

    assert bridge.out.sent == [[0xC0, 98]]  # 99 (panel) - display_offset 1


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
    assert program_byte == 71  # (200 - display_offset 1) & 0x7F == 199 & 0x7F


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
    assert bridge.out.sent == [[0xC0, 98]]  # 99 (panel) - display_offset 1


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

    assert bridge.out.sent == [[0xC0, 6]]  # 7 (panel) - display_offset 1


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


class _CreateEchoBridge:
    def get_header_bytes(self, region, index, offset, count, **kwargs):
        return b"\x00" * count

    def send_and_receive(self, frame, timeout=None):
        return b"reply"


def test_logging_bridge_wraps_get_header_bytes_and_send_and_receive():
    # both are brand new to _WRAPPED_METHODS, added alongside the create
    # program/keygroup feature - every step of that previously-untested-on-
    # hardware path should get the same START/END/FAILED debug-log entries
    # as everything else (see AGENTS.md's own "Debug logging for real-
    # hardware issues" section)
    logger = _FakeLogger()
    bridge = LoggingBridge(_CreateEchoBridge(), logger)

    header = bridge.get_header_bytes("program", 0, 0, 192)
    reply = bridge.send_and_receive(b"\xf0...\xf7")

    assert header == b"\x00" * 192
    assert reply == b"reply"
    assert any("START get_header_bytes" in m for m in logger.debug_calls)
    assert any("END get_header_bytes" in m for m in logger.debug_calls)
    assert any("START send_and_receive" in m for m in logger.debug_calls)
    assert any("END send_and_receive" in m for m in logger.debug_calls)
    assert logger.error_calls == []


# --- LoggingBridge logs a Parameter as its name, not its whole table entry ------------------------


class _RecordingLogger:
    def __init__(self):
        self.lines = []

    def debug(self, message, *a, **k):
        self.lines.append(message)

    def error(self, message, *a, **k):
        self.lines.append(message)


class _OneFieldBridge:
    def get_parameter(self, param, index, *, keygroup=0, region=None):
        return 7

    def set_parameter(self, param, index, value, *, keygroup=0, region=None):
        pass


def test_logging_bridge_logs_a_parameter_as_region_dot_name():
    logger = _RecordingLogger()
    bridge = LoggingBridge(_OneFieldBridge(), logger=logger)
    param = p.lookup("GROUPS", "program")

    bridge.get_parameter(param, 3, keygroup=2)

    start, end = logger.lines
    assert "START get_parameter(program.GROUPS, 3, keygroup=2)" in start
    assert "END get_parameter(program.GROUPS, 3, keygroup=2) -> 7" in end


def test_logging_bridge_no_longer_dumps_the_whole_parameter_entry():
    logger = _RecordingLogger()
    bridge = LoggingBridge(_OneFieldBridge(), logger=logger)
    bridge.get_parameter(p.lookup("GROUPS", "program"), 0)
    bridge.set_parameter(p.lookup("PRLOUD", "program"), 0, 40)
    text = "\n".join(logger.lines)
    # none of the Parameter dataclass's own fields leak into the log
    for leaked in ("Parameter(", "minimum=", "maximum=", "notes=", "desc=", "group="):
        assert leaked not in text
    assert all(len(line) < 120 for line in logger.lines)


def test_logging_bridge_still_logs_plain_arguments_and_values_in_full():
    logger = _RecordingLogger()
    bridge = LoggingBridge(_OneFieldBridge(), logger=logger)
    bridge.set_parameter(p.lookup("PRNAME", "program"), 5, "MY PROGRAM")
    assert "set_parameter(program.PRNAME, 5, 'MY PROGRAM')" in logger.lines[0]


def test_logging_bridge_failures_name_the_field_too():
    class _Failing:
        def get_parameter(self, param, index, **kwargs):
            raise TimeoutError("no reply within 2.0s")

    logger = _RecordingLogger()
    bridge = LoggingBridge(_Failing(), logger=logger)
    with pytest.raises(TimeoutError):
        bridge.get_parameter(p.lookup("SLNGTH", "sample"), 0)
    failed = [line for line in logger.lines if "FAILED" in line]
    assert failed and "get_parameter(sample.SLNGTH, 0)" in failed[0]
    assert "Parameter(" not in failed[0]


# --- global settings (misc byte registers + the whole-block misc data, core/global_settings.py) --


# the raw bytes the real S2000 held when the probe dumped it (2026-10-09). The program change / tune / fine tune registers (71, 64, 65) are
# STALE copies - the app reads those three from the block, below - so they hold deliberately wrong values here.
_RAW_GLOBALS = {
    38: 1, 15: 12, 64: 99, 65: 99, 71: 99, 31: 60, 57: 0, 54: 100, 11: 5, 14: 1, 12: 6,
}
# the 48-byte block: [channel, omni, enabled, 0, 0, 0, cents+50, semitones+9, level raw 12, 0...] - program change Omni, tune 0, fine tune 0
_BLOCK = bytes([15, 1, 1, 0, 0, 0, 50, 9, 12] + [0] * 39)
_DECODED_GLOBALS = {
    "external_controller": 1,
    "output_level": 0,
    "tune_semitones": 0,
    "tune_cents": 0,
    "program_change_channel": 17,
    "play_note": 60,
    "play_channel": 1,
    "play_velocity": 100,
    "scsi_disk_id": 5,
    "scsi_sector": 1,
    "scsi_local_id": 6,
}


class _MiscBridge:
    """Duck-types S3kBridge's private misc-byte calls AND its raw frame calls, with real RMDATA/MDATA/REPLY frames."""

    exclusive_channel = 0

    def __init__(self, registers=None, error=None, echo=True, block=_BLOCK):
        self.registers = dict(_RAW_GLOBALS if registers is None else registers)
        self.error = error
        self.echo = echo  # False: the sampler ignores a register write
        self.block = bytes(block)
        self.block_error = None  # an exception to raise from the next block reads
        self.block_flaky = 0  # this many block reads fail first
        self.block_reply_code = 0  # REPLY code for a block write
        self.block_applies = True  # False: the sampler acks a block write but keeps the old block
        self.reads = []
        self.write_verify_calls = []
        self.block_writes = []
        self._replies = []

    def _misc_byte(self, index, value=None, **kwargs):
        self.reads.append(index)
        if self.error:
            raise self.error
        return self.registers[index]

    def _misc_write_verify(self, index, value, what, **kwargs):
        self.write_verify_calls.append((index, value))
        if self.error:
            raise self.error
        if self.echo:
            self.registers[index] = value
        if self.registers[index] != value:
            raise program_editor_bridge.DeviceError(
                f"{what}: asked for {value}, register reads {self.registers[index]}"
            )
        return value

    def send_and_receive(self, frame, timeout=None):
        _c, command, _p = m.parse_frame(frame)
        assert command == m.Command.RMDATA
        if self.block_error:
            raise self.block_error
        if self.block_flaky:
            self.block_flaky -= 1
            raise ValueError("extended data: expected at least a 7-byte body, got 1")
        return m.build_frame(m.Command.MDATA, m.encode_nibbles(self.block))

    def _drain(self):
        self._replies.clear()

    def _send(self, frame, write=False):
        _c, command, payload = m.parse_frame(frame)
        assert command == m.Command.MDATA and write
        block = bytes(m.decode_nibbles(list(payload)))
        self.block_writes.append(block)
        if self.block_reply_code == 0 and self.block_applies:
            self.block = block
        self._replies.append(m.Reply(code=self.block_reply_code).encode())

    def _receive(self, timeout=None, accept=None):
        if not self._replies:
            raise TimeoutError("no reply within 3.0s")
        return self._replies.pop(0)


def _global_signals(worker):
    seen = {"loaded": [], "load_failed": [], "written": [], "write_failed": []}
    worker.global_settings_loaded.connect(seen["loaded"].append)
    worker.global_settings_load_failed.connect(seen["load_failed"].append)
    worker.global_setting_written.connect(lambda k, v: seen["written"].append((k, v)))
    worker.global_setting_write_failed.connect(
        lambda k, e: seen["write_failed"].append((k, e))
    )
    return seen


def test_worker_reads_every_global_setting():
    worker = BridgeWorker(_MiscBridge())
    seen = _global_signals(worker)

    worker.submit_global_settings()
    worker.process_pending()

    assert seen["loaded"] == [_DECODED_GLOBALS]
    assert seen["load_failed"] == []


def test_the_block_not_the_stale_registers_decides_program_change_and_tune():
    block = bytes([5, 0, 1, 0, 0, 0, 100, 59, 12] + [0] * 39)  # channel 6, +50 cents, +50 st
    worker = BridgeWorker(_MiscBridge(block=block))
    seen = _global_signals(worker)

    worker.submit_global_settings()
    worker.process_pending()

    (loaded,) = seen["loaded"]
    assert loaded["program_change_channel"] == 6
    assert loaded["tune_cents"] == 50 and loaded["tune_semitones"] == 50
    # the stale registers (71, 64, 65) are never even read
    bridge = _MiscBridge(block=block)
    worker2 = BridgeWorker(bridge)
    worker2.submit_global_settings()
    worker2.process_pending()
    assert not {71, 64, 65} & set(bridge.reads)


@pytest.mark.parametrize(
    "block_bytes, expected",
    [([0, 0, 1], 1), ([5, 0, 1], 6), ([0, 0, 0], 0), ([15, 1, 1], 17)],
)
def test_program_change_states_decode_from_the_block(block_bytes, expected):
    block = bytes(block_bytes + [0, 0, 0, 50, 9, 12] + [0] * 39)
    worker = BridgeWorker(_MiscBridge(block=block))
    seen = _global_signals(worker)
    worker.submit_global_settings()
    worker.process_pending()
    assert seen["loaded"][0]["program_change_channel"] == expected


def test_worker_leaves_out_a_setting_whose_raw_value_the_panel_cannot_show():
    # a firmware that keeps something else there: no value, not a wrong one -
    # and the other settings still load
    block = bytes([0, 2, 1] + [0] * 45)  # an Omni flag of 2 is no panel state
    worker = BridgeWorker(_MiscBridge({**_RAW_GLOBALS, 38: 9, 15: 200}, block=block))
    seen = _global_signals(worker)

    worker.submit_global_settings()
    worker.process_pending()

    (loaded,) = seen["loaded"]
    assert "external_controller" not in loaded and "output_level" not in loaded
    assert "program_change_channel" not in loaded
    assert loaded["scsi_disk_id"] == 5


def test_a_block_that_cannot_be_read_leaves_only_the_block_settings_out():
    bridge = _MiscBridge()
    bridge.block_error = TimeoutError("no reply")
    worker = BridgeWorker(bridge)
    seen = _global_signals(worker)

    worker.submit_global_settings()
    worker.process_pending()

    (loaded,) = seen["loaded"]
    assert not {"program_change_channel", "tune_semitones", "tune_cents"} & set(loaded)
    assert loaded["external_controller"] == 1


def test_worker_stops_reading_at_the_first_failed_read():
    # a dead connection must not cost one timeout per setting
    bridge = _MiscBridge(error=TimeoutError("no reply"))
    worker = BridgeWorker(bridge)
    seen = _global_signals(worker)

    worker.submit_global_settings()
    worker.process_pending()

    assert len(bridge.reads) == 1
    assert seen["loaded"] == []
    assert seen["load_failed"] == ["no reply"]


def test_worker_reports_a_bridge_without_misc_registers_as_unavailable():
    # DemoBridge has no _misc_byte
    worker = BridgeWorker(object())
    seen = _global_signals(worker)

    worker.submit_global_settings()
    worker.submit_set_global_setting("external_controller", 1)
    worker.submit_set_global_setting("program_change_channel", 6)
    worker.process_pending()

    assert seen["loaded"] == [] and len(seen["load_failed"]) == 1
    assert seen["written"] == [] and len(seen["write_failed"]) == 2


@pytest.mark.parametrize(
    "key, value, register, raw",
    [
        ("external_controller", 2, 38, 2),
        ("output_level", -18, 15, 9),
        ("output_level", 18, 15, 15),
        ("play_note", 127, 31, 127),
        ("play_channel", 16, 57, 15),
        ("play_velocity", 127, 54, 127),
        ("scsi_disk_id", 7, 11, 7),
        ("scsi_sector", 0, 14, 0),
        ("scsi_local_id", 0, 12, 0),
    ],
)
def test_worker_writes_each_register_setting_to_its_register_verified(key, value, register, raw):
    bridge = _MiscBridge()
    worker = BridgeWorker(bridge)
    seen = _global_signals(worker)

    worker.submit_set_global_setting(key, value)
    worker.process_pending()

    assert bridge.write_verify_calls == [(register, raw)]
    assert bridge.block_writes == []  # a register setting never touches the block
    assert seen["written"] == [(key, value)]
    assert seen["write_failed"] == []


def test_worker_reports_a_write_the_sampler_did_not_take():
    bridge = _MiscBridge(echo=False)
    worker = BridgeWorker(bridge)
    seen = _global_signals(worker)

    worker.submit_set_global_setting("external_controller", 2)
    worker.process_pending()

    assert seen["written"] == []
    assert [k for k, _e in seen["write_failed"]] == ["external_controller"]


@pytest.mark.parametrize(
    "key, value",
    [
        ("external_controller", 3),
        ("output_level", 5),  # not a 6 dB step
        ("output_level", 24),
        ("tune_semitones", 51),
        ("tune_cents", -51),
        ("program_change_channel", 18),
        ("program_change_channel", -1),
        ("play_note", 20),
        ("play_channel", 0),
        ("play_velocity", 128),
        ("scsi_disk_id", 8),
        ("scsi_sector", 2),
        ("scsi_local_id", -1),
        ("no_such_setting", 0),
    ],
)
def test_worker_never_sends_an_out_of_range_global_setting(key, value):
    bridge = _MiscBridge()
    worker = BridgeWorker(bridge)
    seen = _global_signals(worker)

    worker.submit_set_global_setting(key, value)
    worker.process_pending()

    assert bridge.write_verify_calls == [] and bridge.block_writes == []
    assert len(seen["write_failed"]) == 1


def test_global_settings_are_never_touched_on_an_s1000():
    bridge = _MiscBridge()
    worker = BridgeWorker(bridge, s1000=True)
    seen = _global_signals(worker)

    worker.submit_global_settings()
    worker.submit_set_global_setting("external_controller", 1)
    worker.submit_set_global_setting("program_change_channel", 6)
    worker.process_pending()

    assert bridge.reads == [] and bridge.write_verify_calls == [] and bridge.block_writes == []
    assert len(seen["load_failed"]) == 1 and len(seen["write_failed"]) == 2


def test_queued_global_reads_coalesce_but_writes_do_not():
    worker = BridgeWorker(_MiscBridge())
    seen = _global_signals(worker)

    worker.submit_global_settings()
    worker.submit_global_settings()
    worker.submit_set_global_setting("external_controller", 1)
    worker.submit_set_global_setting("external_controller", 2)
    worker.process_pending()

    assert len(seen["loaded"]) == 1
    assert seen["written"] == [("external_controller", 1), ("external_controller", 2)]


# --- the whole-block write (program change channel, tune, fine tune) ----------


def _block_write(bridge, key, value):
    worker = BridgeWorker(bridge)
    seen = _global_signals(worker)
    worker.submit_set_global_setting(key, value)
    worker.process_pending()
    return worker, seen


def test_a_program_change_channel_write_changes_only_the_channel_byte():
    # measured on the real S2000: channel 1 -> 6 changed offset 0 and nothing else
    bridge = _MiscBridge(block=bytes([0, 0, 1, 0, 0, 0, 50, 9, 12] + [0] * 39))
    original = bridge.block

    _worker, seen = _block_write(bridge, "program_change_channel", 6)

    assert len(bridge.block_writes) == 1
    written = bridge.block_writes[0]
    assert [i for i in range(48) if written[i] != original[i]] == [0]
    assert written[0] == 5
    assert seen["written"] == [("program_change_channel", 6)]
    assert bridge.write_verify_calls == []  # never through the stale byte register


@pytest.mark.parametrize(
    "value, start, expected_first_three",
    [
        (0, [5, 0, 1], [5, 0, 0]),  # Off keeps the channel byte
        (17, [5, 0, 1], [5, 1, 1]),  # Omni keeps it too
        (3, [15, 1, 1], [2, 0, 1]),  # a channel from Omni clears the Omni flag
        (3, [5, 0, 0], [2, 0, 1]),  # ...and from Off re-enables
    ],
)
def test_program_change_off_omni_and_channels_encode_the_three_bytes(value, start, expected_first_three):
    bridge = _MiscBridge(block=bytes(start + [0, 0, 0, 50, 9, 12] + [0] * 39))
    _worker, seen = _block_write(bridge, "program_change_channel", value)
    assert list(bridge.block_writes[0][:3]) == expected_first_three
    assert bridge.block_writes[0][3:] == bytes([0, 0, 0, 50, 9, 12] + [0] * 39)
    assert seen["write_failed"] == []


@pytest.mark.parametrize("key, value, offset, raw", [("tune_semitones", 12, 7, 21), ("tune_semitones", -50, 7, 215), ("tune_cents", 50, 6, 100), ("tune_cents", -50, 6, 0)])
def test_tune_and_fine_tune_write_their_block_offsets(key, value, offset, raw):
    bridge = _MiscBridge()
    original = bridge.block
    _worker, seen = _block_write(bridge, key, value)
    written = bridge.block_writes[0]
    assert written[offset] == raw
    assert [i for i in range(48) if written[i] != original[i]] == [offset]
    assert seen["written"] == [(key, value)]


def test_the_block_is_backed_up_once_before_the_first_write(tmp_path, monkeypatch):
    monkeypatch.setattr(program_editor_bridge, "MDATA_BACKUP_DIR", tmp_path)
    bridge = _MiscBridge()
    original = bridge.block
    worker = BridgeWorker(bridge)
    worker.submit_set_global_setting("program_change_channel", 6)
    worker.submit_set_global_setting("program_change_channel", 7)
    worker.process_pending()

    files = list(tmp_path.glob("mdata_*.json"))
    assert len(files) == 1  # once per session, not per write
    saved = json.loads(files[0].read_text())
    assert bytes.fromhex(saved["block_hex"]) == original  # what was there BEFORE the first write
    assert len(bridge.block_writes) == 2


def test_no_backup_means_no_block_write(tmp_path, monkeypatch):
    # the directory can't be created (a file is in the way): the block must not be written
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    monkeypatch.setattr(program_editor_bridge, "MDATA_BACKUP_DIR", blocker / "sub")
    bridge = _MiscBridge()
    _worker, seen = _block_write(bridge, "program_change_channel", 6)
    assert bridge.block_writes == []
    assert len(seen["write_failed"]) == 1 and seen["written"] == []


def test_a_refused_block_write_is_reported():
    bridge = _MiscBridge()
    bridge.block_reply_code = 1
    _worker, seen = _block_write(bridge, "program_change_channel", 6)
    assert seen["written"] == []
    assert "refused" in seen["write_failed"][0][1]


def test_an_acked_block_write_the_sampler_did_not_keep_is_reported():
    # believe the READ, not the ack
    bridge = _MiscBridge()
    bridge.block_applies = False
    _worker, seen = _block_write(bridge, "program_change_channel", 6)
    assert seen["written"] == []
    assert "differs" in seen["write_failed"][0][1]


def test_a_block_of_the_wrong_length_is_never_edited():
    bridge = _MiscBridge(block=bytes(47))
    _worker, seen = _block_write(bridge, "program_change_channel", 6)
    assert bridge.block_writes == []
    assert "length" in seen["write_failed"][0][1]


def test_block_reads_retry_the_undecodable_frame():
    # measured: the sampler intermittently answers with a frame s3k cannot decode
    bridge = _MiscBridge()
    bridge.block_flaky = 2
    worker = BridgeWorker(bridge)
    seen = _global_signals(worker)
    worker.submit_global_settings()
    worker.process_pending()
    assert seen["loaded"][0]["program_change_channel"] == 17


def test_a_block_that_keeps_failing_to_read_fails_the_write_without_writing():
    bridge = _MiscBridge()
    bridge.block_flaky = 99
    _worker, seen = _block_write(bridge, "program_change_channel", 6)
    assert bridge.block_writes == []
    assert len(seen["write_failed"]) == 1


# --- spacing around a block write (measured 2026-10-10: acked, then never applied when the read-back came 2 ms later) ---


class _SlowApplyBridge(_MiscBridge):
    """Models the working theory for the real S2000: a block write is acked at once but only APPLIED after APPLY_S of quiet - any message
    that arrives sooner (the verifying read, say) cancels it. `clock` is virtual: the patched `_sleep` advances it."""

    APPLY_S = 0.5

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.clock = 0.0
        self.pending = None  # (block, time written)
        self.events = []  # ("read" | "write" | ("sleep", seconds)) in order

    def _settle(self):
        if self.pending is not None:
            block, written_at = self.pending
            self.pending = None
            if self.clock - written_at >= self.APPLY_S:
                self.block = block  # quiet long enough: applied
            # else: cancelled by this message

    def send_and_receive(self, frame, timeout=None):
        self._settle()
        self.events.append("read")
        return super().send_and_receive(frame, timeout)

    def _send(self, frame, write=False):
        self._settle()
        self.events.append("write")
        _c, _command, payload = m.parse_frame(frame)
        self.block_writes.append(bytes(m.decode_nibbles(list(payload))))
        self.pending = (self.block_writes[-1], self.clock)
        self._replies.append(m.Reply(code=0).encode())


@pytest.fixture
def real_spacing(monkeypatch):
    """Run the block-write spacing for real against a virtual clock (conftest makes `_sleep` a no-op for every other test)."""
    holder = {}

    def fake_sleep(seconds):
        bridge = holder["bridge"]
        bridge.events.append(("sleep", seconds))
        bridge.clock += seconds

    monkeypatch.setattr(program_editor_bridge, "_sleep", fake_sleep)
    return holder


def test_the_block_write_survives_a_sampler_that_applies_after_a_pause(real_spacing):
    bridge = _SlowApplyBridge(block=bytes([0, 0, 1, 0, 0, 0, 50, 9, 12] + [0] * 39))
    real_spacing["bridge"] = bridge

    _worker, seen = _block_write(bridge, "program_change_channel", 6)

    assert seen["write_failed"] == []
    assert seen["written"] == [("program_change_channel", 6)]
    assert bridge.block[0] == 5  # really applied


def test_the_same_sampler_loses_the_write_without_the_pauses(real_spacing, monkeypatch):
    # proves the fake models the failure the user saw: a write followed at once by a read is acked and never applied
    monkeypatch.setattr(program_editor_bridge, "MISC_BLOCK_BEFORE_WRITE_S", 0)
    monkeypatch.setattr(program_editor_bridge, "MISC_BLOCK_AFTER_WRITE_S", 0)
    bridge = _SlowApplyBridge(block=bytes([0, 0, 1, 0, 0, 0, 50, 9, 12] + [0] * 39))
    real_spacing["bridge"] = bridge

    _worker, seen = _block_write(bridge, "program_change_channel", 6)

    assert seen["written"] == []
    assert "differs" in seen["write_failed"][0][1]
    assert bridge.block[0] == 0


def test_the_block_write_is_paced_around_the_write(real_spacing):
    bridge = _SlowApplyBridge()
    real_spacing["bridge"] = bridge

    _block_write(bridge, "tune_semitones", 12)

    # fresh read, pause, the write, pause, then the verifying read - never a message right next to the write
    assert bridge.events[:5] == [
        "read",
        ("sleep", program_editor_bridge.MISC_BLOCK_BEFORE_WRITE_S),
        "write",
        ("sleep", program_editor_bridge.MISC_BLOCK_AFTER_WRITE_S),
        "read",
    ]
    assert program_editor_bridge.MISC_BLOCK_BEFORE_WRITE_S >= 0.3
    assert program_editor_bridge.MISC_BLOCK_AFTER_WRITE_S >= 1.0


def test_a_slow_apply_is_verified_over_several_attempts(monkeypatch):
    # a sampler that shows the new block only from the SECOND read-back on: the verify retries before giving up
    class _LateApply(_MiscBridge):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.pending_block = None
            self.reads_since_write = 0

        def _send(self, frame, write=False):
            _c, _command, payload = m.parse_frame(frame)
            self.pending_block = bytes(m.decode_nibbles(list(payload)))
            self.block_writes.append(self.pending_block)
            self.reads_since_write = 0
            self._replies.append(m.Reply(code=0).encode())

        def send_and_receive(self, frame, timeout=None):
            if self.pending_block is not None:
                self.reads_since_write += 1
                if self.reads_since_write >= 2:
                    self.block, self.pending_block = self.pending_block, None
            return super().send_and_receive(frame, timeout)

    bridge = _LateApply()
    _worker, seen = _block_write(bridge, "program_change_channel", 6)
    assert seen["written"] == [("program_change_channel", 6)]
    assert bridge.block[0] == 5

    # with a single attempt the same sampler would have been reported as failed
    monkeypatch.setattr(program_editor_bridge, "MISC_BLOCK_VERIFY_ATTEMPTS", 1)
    _worker, seen = _block_write(_LateApply(), "program_change_channel", 6)
    assert seen["written"] == [] and "differs" in seen["write_failed"][0][1]


# --- stray replies: the warning the editor shows (something is echoing the sampler's replies back to it) ----


class _StrayBridge:
    def __init__(self):
        self.skipped_replies = 0

    def program_list(self):
        self.skipped_replies += 2  # each read skips two strays
        return ["A"]


def test_the_worker_warns_again_whenever_new_strays_pile_up():
    bridge = _StrayBridge()  # each read skips two strays
    worker = BridgeWorker(bridge)
    warned = []
    worker.stray_replies_detected.connect(warned.append)

    def read():
        worker.submit_program_list()
        worker.process_pending()

    read()
    assert warned == []  # 2 strays: below the threshold of 3
    read()
    assert warned == [4]  # 4 >= 3: the first warning
    read()
    assert warned == [4]  # only 2 NEW strays since the warning
    read()
    assert warned == [4, 8]  # 4 new strays: it warns AGAIN, in the same session
    read()
    read()
    assert warned == [4, 8, 12]


def test_an_echo_that_stops_does_not_keep_warning():
    bridge = _StrayBridge()
    worker = BridgeWorker(bridge)
    warned = []
    worker.stray_replies_detected.connect(warned.append)
    worker.submit_program_list()
    worker.submit_program_list()
    worker.process_pending()
    assert warned == [4]

    bridge.program_list = lambda: ["A"]  # the user fixed the setup: no more strays
    for _ in range(5):
        worker.submit_program_list()
        worker.process_pending()
    assert warned == [4]


def test_no_warning_without_a_reply_matching_counter():
    # demo/fake bridges have no skipped_replies
    worker = BridgeWorker(_ListBridge(items=["A"]))
    warned = []
    worker.stray_replies_detected.connect(warned.append)
    worker.submit_program_list()
    worker.process_pending()
    assert warned == []
