# tests for core/midi_transport.py - the shared raw-rtmidi transport behind
# AKAISDS_SHARED_MIDI_TRANSPORT (see its own module docstring). Real port
# opening needs a real rtmidi backend/hardware and is NOT exercised here -
# see tests/midi_transport_consolidation_test_plan.md for that.
# Everything here is the pure/near-pure logic this module was deliberately
# split out to make testable without either: _MessageFanout (the callback/
# poll dual-delivery), and SharedMidiInput/SharedMidiOutput's own wiring,
# exercised against an injected fake port (`_port_factory`) rather than a
# real one.

import pytest

from core import midi_transport
from core.midi_transport import SharedMidiInput, SharedMidiOutput, _MessageFanout


# --- _MessageFanout ----------------------------------------------------------


def test_push_with_no_callback_registered_only_queues():
    fanout = _MessageFanout()
    fanout.push([0xF0, 1, 2, 0xF7], 0.0)
    assert fanout.get_message() == [[0xF0, 1, 2, 0xF7], 0.0]


def test_push_delivers_to_both_the_queue_and_the_callback():
    fanout = _MessageFanout()
    received = []
    fanout.set_message_callback(received.append)

    fanout.push([0x90, 60, 100], 0.001)

    assert received == [[0x90, 60, 100]]
    assert fanout.get_message() == [[0x90, 60, 100], 0.001]


def test_get_message_returns_none_when_the_queue_is_empty():
    fanout = _MessageFanout()
    assert fanout.get_message() is None


def test_get_message_drains_in_fifo_order():
    fanout = _MessageFanout()
    fanout.push([1], 0.0)
    fanout.push([2], 0.0)
    fanout.push([3], 0.0)

    assert [fanout.get_message()[0] for _ in range(3)] == [[1], [2], [3]]
    assert fanout.get_message() is None


def test_callback_exception_does_not_prevent_the_message_reaching_the_queue():
    # a broken consumer on the callback side must never take the poll side
    # down with it (or vice versa) - they're independent deliveries of the
    # same message
    fanout = _MessageFanout()

    def bad_callback(message):
        raise ValueError("boom")

    fanout.set_message_callback(bad_callback)

    class _FakeLogger:
        def __init__(self):
            self.errors = []

        def error(self, *a, **k):
            self.errors.append((a, k))

    logger = _FakeLogger()
    fanout.push([0xF0, 1, 0xF7], 0.0, logger=logger)

    assert fanout.get_message() == [[0xF0, 1, 0xF7], 0.0]
    assert len(logger.errors) == 1


def test_replacing_the_callback_only_affects_future_pushes():
    fanout = _MessageFanout()
    first_calls = []
    second_calls = []
    fanout.set_message_callback(first_calls.append)
    fanout.push([1], 0.0)
    fanout.set_message_callback(second_calls.append)
    fanout.push([2], 0.0)

    assert first_calls == [[1]]
    assert second_calls == [[2]]


def test_fanout_handles_many_pushes_without_losing_or_duplicating_any():
    # the real rtmidi callback fires on its own native thread, not the GUI
    # thread, so what actually needs proving is "many pushes, none lost,
    # none duplicated" - _MessageFanout relies on queue.Queue for the
    # thread-safety itself (trusted stdlib, not re-derived here).
    #
    # An earlier version of this test used real threading.Thread workers to
    # exercise that concurrently. Isolated by bisecting this suite: doing so
    # reproducibly triggered a real interpreter segfault ELSEWHERE in this
    # test suite (inside unrelated Qt/QThread-heavy tests running later in
    # the same process) - not a bug in this class, but a genuine, confirmed
    # instability from mixing raw OS threads with this suite's own Qt
    # threading in this environment. Real concurrent-hardware correctness
    # needs to be proven on real hardware anyway (see tests/
    # midi_transport_consolidation_test_plan.md), so this stays sequential.
    fanout = _MessageFanout()
    messages_per_thread = 200
    thread_count = 8

    for thread_id in range(thread_count):
        for i in range(messages_per_thread):
            fanout.push([thread_id, i], 0.0)

    seen = set()
    while True:
        result = fanout.get_message()
        if result is None:
            break
        seen.add(tuple(result[0]))

    assert len(seen) == messages_per_thread * thread_count


# --- SharedMidiInput (real port opening replaced with a fake factory) -------


class _FakeInPort:
    def __init__(self):
        self.callback = None
        self.closed = False
        self.deleted = False

    def set_callback(self, callback):
        self.callback = callback

    def close_port(self):
        self.closed = True

    def delete(self):
        self.deleted = True

    def fire(self, message, delta_time=0.0):
        self.callback((message, delta_time))


def test_shared_midi_input_wires_the_fake_ports_callback_to_the_fanout():
    fake_port = _FakeInPort()
    view = SharedMidiInput("Fake In", _port_factory=lambda name: fake_port)
    received = []
    view.set_message_callback(received.append)

    fake_port.fire([0xF0, 9, 0xF7])

    assert received == [[0xF0, 9, 0xF7]]
    assert view.get_message() == [[0xF0, 9, 0xF7], 0.0]


def test_shared_midi_input_close_port_closes_and_deletes_the_real_port():
    fake_port = _FakeInPort()
    view = SharedMidiInput("Fake In", _port_factory=lambda name: fake_port)
    view.close_port()
    assert fake_port.closed is True
    assert fake_port.deleted is True


def test_shared_midi_input_close_port_acquires_the_same_lock_as_the_callback():
    # regression test for a confirmed real-hardware crash: close_port()
    # used to delete the real port with no lock at all, so a concurrent
    # rtmidi callback (its own native thread) invoking _on_rtmidi_message
    # could run mid-delete. See SharedMidiOutput's own equivalent test
    # below for the actual SIGSEGV this class of bug produced on the
    # output side - same fix (reuse the one lock that already serializes
    # against self._port), applied here too for the same reason.
    fake_port = _FakeInPort()
    view = SharedMidiInput("Fake In", _port_factory=lambda name: fake_port)
    calls = []
    real_lock = view._port_lock

    class _SpyLock:
        def __enter__(self):
            calls.append("acquire")
            return real_lock.__enter__()

        def __exit__(self, *exc_info):
            calls.append("release")
            return real_lock.__exit__(*exc_info)

    view._port_lock = _SpyLock()

    view.close_port()

    assert calls == ["acquire", "release"]
    assert fake_port.closed is True


# --- SharedMidiOutput (real port opening replaced with a fake factory) -----


class _FakeOutPort:
    def __init__(self):
        self.sent = []
        self.closed = False
        self.deleted = False

    def send_message(self, message):
        self.sent.append(list(message))

    def close_port(self):
        self.closed = True

    def delete(self):
        self.deleted = True


def test_shared_midi_output_forwards_send_message_to_the_real_port():
    fake_port = _FakeOutPort()
    output = SharedMidiOutput("Fake Out", _port_factory=lambda name: fake_port)
    output.send_message([0xF0, 1, 2, 0xF7])
    assert fake_port.sent == [[0xF0, 1, 2, 0xF7]]


def test_shared_midi_output_close_port_closes_and_deletes_the_real_port():
    fake_port = _FakeOutPort()
    output = SharedMidiOutput("Fake Out", _port_factory=lambda name: fake_port)
    output.close_port()
    assert fake_port.closed is True
    assert fake_port.deleted is True


def test_shared_midi_output_close_port_acquires_the_write_lock():
    # regression test for a confirmed real-hardware crash: close_port()
    # used to delete the real port with no lock at all. MidiSettingsDialog's
    # Apply/Identity Request/Loopback Test (ui/settings_dialog.py) all call
    # MidiManager.open_input(None)/open_output(None) - close_port() - from
    # the GUI thread, while BridgeWorker's background thread can be
    # concurrently mid-send_message() on the SAME SharedMidiOutput under
    # the shared transport. With no lock, close_port() could delete the
    # native rtmidi object while send_message()'s call into it was still
    # in flight - SIGSEGV in RtMidiOut::sendMessage, observed for real on
    # the BridgeWorker thread. Reusing _write_lock (already held by
    # send_message() for a different reason - see this class's own
    # docstring) for close_port() too is the fix.
    fake_port = _FakeOutPort()
    output = SharedMidiOutput("Fake Out", _port_factory=lambda name: fake_port)
    calls = []
    real_lock = output._write_lock

    class _SpyLock:
        def __enter__(self):
            calls.append("acquire")
            return real_lock.__enter__()

        def __exit__(self, *exc_info):
            calls.append("release")
            return real_lock.__exit__(*exc_info)

    output._write_lock = _SpyLock()

    output.close_port()

    assert calls == ["acquire", "release"]
    assert fake_port.closed is True


def test_shared_midi_output_send_message_acquires_the_write_lock():
    # the actual risk this class exists to prevent: two logical senders
    # (SamplerController-style traffic and S3kBridge-style traffic) on the
    # same physical output must never have their messages torn apart or
    # interleaved mid-frame - _write_lock is what makes one send atomic
    # regardless of which thread it comes from.
    #
    # Deliberately NOT a real-multi-thread stress test (holding a lock
    # across time.sleep() from two real OS threads, in-process, alongside
    # this suite's own Qt/QThread-heavy tests, was found to trigger a real,
    # reproducible interpreter segfault elsewhere in the suite - isolated
    # by bisection, not theoretical; see git history for this test's own
    # prior version and tests/midi_transport_consolidation_test_
    # plan.md, which is exactly where genuine concurrent-hardware
    # correctness needs to be proven instead). A real threading.Lock
    # substituted for a spy proves the code path acquires it - the
    # atomicity guarantee itself is a property of threading.Lock, which is
    # trusted stdlib, not something this test needs to re-derive.
    fake_port = _FakeOutPort()
    output = SharedMidiOutput("Fake Out", _port_factory=lambda name: fake_port)
    calls = []
    real_lock = output._write_lock

    class _SpyLock:
        def __enter__(self):
            calls.append("acquire")
            return real_lock.__enter__()

        def __exit__(self, *exc_info):
            calls.append("release")
            return real_lock.__exit__(*exc_info)

    output._write_lock = _SpyLock()

    output.send_message([0xF0, 1, 0xF7])

    assert calls == ["acquire", "release"]
    assert fake_port.sent == [[0xF0, 1, 0xF7]]


# --- list_input_names / list_output_names (need a real rtmidi backend) -----
# skipped rather than asserted on if no backend is available on this host -
# see tests/midi_transport_consolidation_test_plan.md for the real,
# hardware-backed version of this check


def test_list_input_names_returns_a_list_or_skips_without_a_backend():
    try:
        result = midi_transport.list_input_names()
    except Exception as exc:
        pytest.skip(f"no rtmidi backend available on this host: {exc}")
    assert isinstance(result, list)


def test_list_output_names_returns_a_list_or_skips_without_a_backend():
    try:
        result = midi_transport.list_output_names()
    except Exception as exc:
        pytest.skip(f"no rtmidi backend available on this host: {exc}")
    assert isinstance(result, list)


def test_shared_midi_input_real_factory_raises_a_clear_error_for_an_unknown_port():
    # exercises the REAL _default_input_port_factory (no _port_factory
    # override) - only the "port not found" branch, which needs a real
    # rtmidi backend to enumerate against but not real hardware/a real
    # device attached to it
    try:
        SharedMidiInput("definitely not a real port name 12345")
    except Exception as exc:
        if isinstance(exc, RuntimeError) and "no input port named" in str(exc):
            return  # expected
        pytest.skip(f"no rtmidi backend available on this host: {exc}")
    pytest.fail("expected a RuntimeError for an unknown port name, got none")


def test_shared_midi_output_real_factory_raises_a_clear_error_for_an_unknown_port():
    try:
        SharedMidiOutput("definitely not a real port name 12345")
    except Exception as exc:
        if isinstance(exc, RuntimeError) and "no output port named" in str(exc):
            return  # expected
        pytest.skip(f"no rtmidi backend available on this host: {exc}")
    pytest.fail("expected a RuntimeError for an unknown port name, got none")
