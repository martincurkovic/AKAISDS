# tests for core/midi_manager.py
# fakes out the real 'mido' module (and PySide6.QtCore, same trick as
# test_sampler_controller.py) so these run without any real MIDI hardware/ports

import sys
import types
import importlib

import pytest


class _FakeBoundSignal:
    def __init__(self):
        self._handlers = []

    def connect(self, fn):
        self._handlers.append(fn)

    def emit(self, *args, **kwargs):
        for fn in self._handlers:
            fn(*args, **kwargs)


class _FakeSignal:
    def __init__(self, *_a):
        pass

    def __get__(self, obj, _owner=None):
        if obj is None:
            return self
        key = f"_sig_{id(self)}"
        if not hasattr(obj, key):
            setattr(obj, key, _FakeBoundSignal())
        return getattr(obj, key)


class _FakeQObject:
    def __init__(self, *_a, **_k):
        pass


class _FakeMessage:
    def __init__(self, type, **kwargs):
        self.type = type
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakePort:
    def __init__(self, name):
        self.name = name
        self.closed = False
        self.sent = []

    def close(self):
        self.closed = True

    def send(self, message):
        self.sent.append(message)


class _FakeInputPort(_FakePort):
    def __init__(self, name, callback):
        super().__init__(name)
        self.callback = callback


class _FakeMido:
    # stands in for the real 'mido' module - just enough surface area for MidiManager
    def __init__(self):
        self.input_names = ["Fake In A", "Fake In B"]
        self.output_names = ["Fake Out A", "Fake Out B"]
        self.opened_inputs = []
        self.opened_outputs = []

    def get_input_names(self):
        return list(self.input_names)

    def get_output_names(self):
        return list(self.output_names)

    def open_input(self, name, callback=None):
        port = _FakeInputPort(name, callback)
        self.opened_inputs.append(port)
        return port

    def open_output(self, name):
        port = _FakePort(name)
        self.opened_outputs.append(port)
        return port

    def Message(self, type, **kwargs):
        return _FakeMessage(type, **kwargs)


@pytest.fixture
def midi_manager_module(monkeypatch):
    fake_mido = _FakeMido()
    fake_qtcore = types.ModuleType("PySide6.QtCore")
    fake_qtcore.QObject = _FakeQObject
    fake_qtcore.Signal = _FakeSignal

    monkeypatch.setitem(sys.modules, "mido", fake_mido)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", fake_qtcore)
    monkeypatch.delitem(sys.modules, "core.midi_manager", raising=False)
    # these tests exercise the mido-backed path and assume shared-transport
    # is off; shared_transport_enabled() reads the env var live, falling
    # back to config.json's "shared_midi_transport" (default True as of
    # the shared transport becoming the default) if the env var is unset -
    # so an ambient AKAISDS_SHARED_MIDI_TRANSPORT=1 in the caller's shell,
    # OR simply the new default with nothing set, would otherwise leak in
    # and send these through the real rtmidi path instead. Explicitly "0"
    # (not delenv) so this is deterministic regardless of either one.
    monkeypatch.setenv("AKAISDS_SHARED_MIDI_TRANSPORT", "0")

    module = importlib.import_module("core.midi_manager")
    yield module, fake_mido

    monkeypatch.delitem(sys.modules, "core.midi_manager", raising=False)


@pytest.fixture
def manager(midi_manager_module):
    module, fake_mido = midi_manager_module
    return module.MidiManager(), fake_mido


# --- shared_transport_enabled(): env var vs config.json precedence ---------


@pytest.mark.parametrize(
    "env_value,expected",
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("0", False),
        ("false", False),
        ("FALSE", False),
        ("no", False),
        ("off", False),
    ],
)
def test_shared_transport_enabled_env_var_overrides_config(
    midi_manager_module, monkeypatch, env_value, expected
):
    module, _fake_mido = midi_manager_module
    monkeypatch.setenv("AKAISDS_SHARED_MIDI_TRANSPORT", env_value)
    # opposite of expected, to prove the env var - not this - decided it
    monkeypatch.setattr(
        module.app_config, "get_shared_midi_transport_enabled", lambda: not expected
    )
    assert module.shared_transport_enabled() is expected


def test_shared_transport_enabled_falls_back_to_config_when_env_var_unset(
    midi_manager_module, monkeypatch
):
    module, _fake_mido = midi_manager_module
    monkeypatch.delenv("AKAISDS_SHARED_MIDI_TRANSPORT", raising=False)
    monkeypatch.setattr(
        module.app_config, "get_shared_midi_transport_enabled", lambda: False
    )
    assert module.shared_transport_enabled() is False

    monkeypatch.setattr(
        module.app_config, "get_shared_midi_transport_enabled", lambda: True
    )
    assert module.shared_transport_enabled() is True


def test_list_inputs_and_outputs(manager):
    mgr, fake_mido = manager
    assert mgr.list_inputs() == fake_mido.input_names
    assert mgr.list_outputs() == fake_mido.output_names


def test_open_input_opens_port_and_emits_connection_changed(manager):
    mgr, fake_mido = manager
    changed = []
    mgr.connection_changed.connect(lambda: changed.append(True))

    mgr.open_input("Fake In A")

    assert mgr.input_name == "Fake In A"
    assert mgr.input_port is fake_mido.opened_inputs[-1]
    assert changed == [True]


def test_open_input_with_no_name_only_closes_existing(manager):
    mgr, _fake_mido = manager
    mgr.open_input("Fake In A")
    first_port = mgr.input_port

    mgr.open_input(None)

    assert first_port.closed is True
    assert mgr.input_port is None
    assert mgr.input_name is None


def test_reopening_input_closes_previous_port(manager):
    mgr, _fake_mido = manager
    mgr.open_input("Fake In A")
    first_port = mgr.input_port

    mgr.open_input("Fake In B")

    assert first_port.closed is True
    assert mgr.input_name == "Fake In B"


def test_close_input_when_nothing_open_is_a_noop(manager):
    mgr, _fake_mido = manager
    mgr.close_input()  # should not raise
    assert mgr.input_port is None


def test_open_output_opens_port_and_emits_connection_changed(manager):
    mgr, fake_mido = manager
    changed = []
    mgr.connection_changed.connect(lambda: changed.append(True))

    mgr.open_output("Fake Out A")

    assert mgr.output_name == "Fake Out A"
    assert mgr.output_port is fake_mido.opened_outputs[-1]
    assert changed == [True]


def test_close_output_when_nothing_open_is_a_noop(manager):
    mgr, _fake_mido = manager
    mgr.close_output()  # should not raise
    assert mgr.output_port is None


def test_send_sysex_without_output_port_raises(manager):
    mgr, _fake_mido = manager
    with pytest.raises(RuntimeError):
        mgr.send_sysex([0x01, 0x02])


def test_send_sysex_sends_via_output_port(manager):
    mgr, _fake_mido = manager
    mgr.open_output("Fake Out A")
    mgr.send_sysex([0x01, 0x02, 0x03])

    sent = mgr.output_port.sent[-1]
    assert sent.type == "sysex"
    assert sent.data == [0x01, 0x02, 0x03]


def test_send_control_change_without_output_port_raises(manager):
    mgr, _fake_mido = manager
    with pytest.raises(RuntimeError):
        mgr.send_control_change(0, 1, 127)


def test_send_control_change_sends_correct_message(manager):
    mgr, _fake_mido = manager
    mgr.open_output("Fake Out A")
    mgr.send_control_change(channel=2, control=7, value=100)

    sent = mgr.output_port.sent[-1]
    assert sent.type == "control_change"
    assert sent.channel == 2
    assert sent.control == 7
    assert sent.value == 100


def test_on_message_sysex_emits_sysex_received(manager):
    mgr, _fake_mido = manager
    received = []
    mgr.sysex_received.connect(lambda data: received.append(data))

    mgr.open_input("Fake In A")
    callback = mgr.input_port.callback  # what MidiManager registered with mido

    fake_message = types.SimpleNamespace(type="sysex", data=[0x7E, 0x00, 0x01])
    callback(fake_message)

    assert received == [bytes([0x7E, 0x00, 0x01])]


def test_on_message_ignores_non_sysex_messages(manager):
    mgr, _fake_mido = manager
    received = []
    mgr.sysex_received.connect(lambda data: received.append(data))

    mgr.open_input("Fake In A")
    callback = mgr.input_port.callback

    fake_message = types.SimpleNamespace(type="note_on", data=[])
    callback(fake_message)

    assert received == []


# --- AKAISDS_SHARED_MIDI_TRANSPORT=1 (see core/midi_transport.py) ----------
# core.midi_transport itself is NOT faked out here (unlike mido above) -
# it's real, dependency-light code already covered directly by
# tests/test_midi_transport.py. Only SharedMidiInput/SharedMidiOutput/
# list_input_names/list_output_names are monkeypatched with fakes, so these
# tests never need a real MIDI backend/port either - they're checking
# MidiManager's OWN wiring (which branch it takes, how it frames/unframes
# SysEx bytes by hand), not core.midi_transport's own internals again.


class _FakeSharedInput:
    instances = []

    def __init__(self, name):
        self.name = name
        self.callback = None
        self.closed = False
        _FakeSharedInput.instances.append(self)

    def set_message_callback(self, callback):
        self.callback = callback

    def close_port(self):
        self.closed = True


class _FakeSharedOutput:
    instances = []

    def __init__(self, name):
        self.name = name
        self.sent = []
        self.closed = False
        _FakeSharedOutput.instances.append(self)

    def send_message(self, message):
        self.sent.append(list(message))

    def close_port(self):
        self.closed = True


@pytest.fixture
def shared_manager(midi_manager_module, monkeypatch):
    module, _fake_mido = midi_manager_module
    monkeypatch.setenv("AKAISDS_SHARED_MIDI_TRANSPORT", "1")
    _FakeSharedInput.instances = []
    _FakeSharedOutput.instances = []
    monkeypatch.setattr(module.midi_transport, "SharedMidiInput", _FakeSharedInput)
    monkeypatch.setattr(module.midi_transport, "SharedMidiOutput", _FakeSharedOutput)
    monkeypatch.setattr(
        module.midi_transport, "list_input_names", lambda: ["Shared In A"]
    )
    monkeypatch.setattr(
        module.midi_transport, "list_output_names", lambda: ["Shared Out A"]
    )
    return module.MidiManager()


def test_shared_transport_list_inputs_and_outputs_use_midi_transport(shared_manager):
    mgr = shared_manager
    assert mgr.list_inputs() == ["Shared In A"]
    assert mgr.list_outputs() == ["Shared Out A"]


def test_shared_transport_open_input_builds_a_shared_midi_input(shared_manager):
    mgr = shared_manager
    mgr.open_input("Shared In A")
    assert mgr.raw_input is _FakeSharedInput.instances[-1]
    assert mgr.raw_input.name == "Shared In A"
    assert mgr.input_name == "Shared In A"
    assert mgr.input_port is None  # the mido-backed slot stays unused


def test_shared_transport_open_output_builds_a_shared_midi_output(shared_manager):
    mgr = shared_manager
    mgr.open_output("Shared Out A")
    assert mgr.raw_output is _FakeSharedOutput.instances[-1]
    assert mgr.output_name == "Shared Out A"
    assert mgr.output_port is None


def test_shared_transport_close_input_closes_the_raw_port(shared_manager):
    mgr = shared_manager
    mgr.open_input("Shared In A")
    raw = mgr.raw_input
    mgr.close_input()
    assert raw.closed is True
    assert mgr.raw_input is None
    assert mgr.input_name is None


def test_shared_transport_close_output_closes_the_raw_port(shared_manager):
    mgr = shared_manager
    mgr.open_output("Shared Out A")
    raw = mgr.raw_output
    mgr.close_output()
    assert raw.closed is True
    assert mgr.raw_output is None


def test_shared_transport_send_sysex_frames_with_sox_and_eox(shared_manager):
    mgr = shared_manager
    mgr.open_output("Shared Out A")
    mgr.send_sysex([0x01, 0x02, 0x03])
    assert mgr.raw_output.sent == [[0xF0, 0x01, 0x02, 0x03, 0xF7]]


def test_shared_transport_send_sysex_without_output_raises(shared_manager):
    with pytest.raises(RuntimeError):
        shared_manager.send_sysex([0x01])


def test_shared_transport_send_control_change_frames_correctly(shared_manager):
    mgr = shared_manager
    mgr.open_output("Shared Out A")
    mgr.send_control_change(channel=2, control=7, value=100)
    assert mgr.raw_output.sent == [[0xB2, 7, 100]]


def test_shared_transport_send_control_change_without_output_raises(shared_manager):
    with pytest.raises(RuntimeError):
        shared_manager.send_control_change(0, 1, 127)


def test_shared_transport_on_raw_message_strips_sox_and_eox(shared_manager):
    mgr = shared_manager
    received = []
    mgr.sysex_received.connect(lambda data: received.append(data))
    mgr.open_input("Shared In A")

    mgr.raw_input.callback([0xF0, 0x7E, 0x00, 0x01, 0xF7])

    assert received == [bytes([0x7E, 0x00, 0x01])]


def test_shared_transport_on_raw_message_without_trailing_eox_still_strips_sox(
    shared_manager,
):
    # rtmidi callbacks are not guaranteed to deliver a trimmed-to-exactly-
    # one-frame buffer - tolerate a missing EOX rather than mis-framing
    mgr = shared_manager
    received = []
    mgr.sysex_received.connect(lambda data: received.append(data))
    mgr.open_input("Shared In A")

    mgr.raw_input.callback([0xF0, 0x7E, 0x00, 0x01])

    assert received == [bytes([0x7E, 0x00, 0x01])]


def test_shared_transport_on_raw_message_ignores_non_sysex(shared_manager):
    mgr = shared_manager
    received = []
    mgr.sysex_received.connect(lambda data: received.append(data))
    mgr.open_input("Shared In A")

    mgr.raw_input.callback([0x90, 60, 100])

    assert received == []


def test_shared_transport_on_raw_message_ignores_empty_message(shared_manager):
    mgr = shared_manager
    received = []
    mgr.sysex_received.connect(lambda data: received.append(data))
    mgr.open_input("Shared In A")

    mgr.raw_input.callback([])

    assert received == []
