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

    module = importlib.import_module("core.midi_manager")
    yield module, fake_mido

    monkeypatch.delitem(sys.modules, "core.midi_manager", raising=False)


@pytest.fixture
def manager(midi_manager_module):
    module, fake_mido = midi_manager_module
    return module.MidiManager(), fake_mido


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
