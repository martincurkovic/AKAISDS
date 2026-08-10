# tests for sampler_controller.py
# this DOES need Qt to test so it may take a bit longer than the other tests
# using monkeypatch to run a FAKE QtCore for QTimer.singleShot

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


class _FakeQTimer:
    @staticmethod
    def singleShot(_ms, fn):
        fn()  # fires immediately - no real waiting, fully deterministic


class _FakeMidiManager:
    def __init__(self):
        self.sysex_received = _FakeBoundSignal()
        self.sent = []
        self.input_name = "Fake Input"  # not open-loop by default

    def send_sysex(self, data):
        self.sent.append(bytes(data))

    def send_control_change(self, *_a, **_k):
        pass


@pytest.fixture
def controller_module(monkeypatch):
    # provides the sampler_controller with a fake QtCore
    # basically any test in here gets fully isolated from the REAL Qt
    fake_qtcore = types.ModuleType("PySide6.QtCore")
    fake_qtcore.Signal = _FakeSignal
    fake_qtcore.QObject = _FakeQObject
    fake_qtcore.QTimer = _FakeQTimer

    monkeypatch.setitem(sys.modules, "PySide6.QtCore", fake_qtcore)
    # force a fresh import under the fake QtCore, even if a previous
    # test (or the real app) already imported this module once
    monkeypatch.delitem(sys.modules, "controller.sampler_controller", raising=False)

    module = importlib.import_module("controller.sampler_controller")
    yield module

    # undo the forced re-import so anything importing this module AFTER
    # this test runs gets a normal, freshly-imported copy again too
    monkeypatch.delitem(sys.modules, "controller.sampler_controller", raising=False)


@pytest.fixture
def controller(controller_module):
    midi = _FakeMidiManager()
    return controller_module.SamplerController(midi)


def test_channel_propagates_into_rstat_request(controller):
    controller.set_channel(5)
    controller.refresh_sample_list()
    sent = controller.midi_manager.sent[-1]
    assert sent[1] == 5  # channel byte


def test_refresh_sample_list_sends_rstat_and_sets_flag(controller):
    controller.refresh_sample_list()
    sent = controller.midi_manager.sent[-1]
    assert sent[2] == 0x00
    assert controller._awaiting_memory_status is True


def test_stat_reply_updates_memory_status_and_chains_into_slist(controller):
    controller._awaiting_memory_status = True
    stat_reply = [
        0x47,
        0x00,
        0x01,
        0x48,
        0x00,
        0x11,
        0x6E,
        0x07,
        0x68,
        0x07,
        0x00,
        0x00,
        0x40,
        0x02,
        0x00,
        0x76,
        0x3B,
        0x02,
        0x00,
    ]
    captured = []
    controller.memory_status_updated.connect(lambda info: captured.append(info))
    controller.on_sysex_received(stat_reply)
    assert controller._awaiting_memory_status is False
    assert len(captured) == 1
    assert captured[0]["version_string"] == "2.00"
    sent = controller.midi_manager.sent[-1]
    assert sent[2] == 0x04


def test_unexpected_stat_falls_through_without_crashing(controller):
    stat_reply = [
        0x47,
        0x00,
        0x01,
        0x48,
        0x00,
        0x11,
        0x6E,
        0x07,
        0x68,
        0x07,
        0x00,
        0x00,
        0x40,
        0x02,
        0x00,
        0x76,
        0x3B,
        0x02,
        0x00,
    ]

    captured = []
    controller.memory_status_updated.connect(lambda info: captured.append(info))
    controller.on_sysex_received(stat_reply)
    assert controller._awaiting_memory_status is False
    assert len(captured) == 0


def test_channel_can_be_overridden_per_call(controller):
    controller.set_channel(5)
    controller.delete_sample(3, channel=0)
    # delete_sample also schedules a follow-up refresh_sample_list(),
    # which our fake QTimer.singleShot fires immediately - sent[0] is
    # the actual delete request, sent[-1] would be that trailing refresh
    sent = controller.midi_manager.sent[0]
    assert sent[1] == 0  # explicit override wins over the global setting


def test_generic_device_send_uses_universal_protocol_not_akai(controller, monkeypatch):
    from core import sds_encoder

    controller.set_device_type("generic")
    monkeypatch.setattr(
        sds_encoder, "read_wav_channels", lambda _path: ([(1, 2, 3)], 44100)
    )

    controller.send_file_queue(
        [
            {
                "filepath": "/fake/a.wav",
                "name": None,
                "bit_depth": 16,
                "sample_rate": None,
                "mono": False,
            }
        ],
        starting_sample_number=10,
    )

    first_message = controller.midi_manager.sent[0]
    assert first_message[0] == 0x7E  # universal manufacturer ID, NOT Akai's 0x47
    assert first_message[2] == 0x01  # Dump Header sub-ID


def test_generic_device_send_requires_starting_slot(controller):
    controller.set_device_type("generic")
    started = controller.send_file_queue(
        [
            {
                "filepath": "/fake/a.wav",
                "name": None,
                "bit_depth": 16,
                "sample_rate": None,
                "mono": False,
            }
        ]
    )
    assert started is False
    assert not controller._file_queue  # nothing should have actually started


def test_awaiting_count_flag_not_set_in_generic_mode(controller, monkeypatch):
    # regression test: this flag being set unconditionally (regardless
    # of device type) permanently blocked every send after the first
    # one in generic mode, since nothing ever cleared it
    from core import sds_encoder

    controller.set_device_type("generic")
    monkeypatch.setattr(
        sds_encoder, "read_wav_channels", lambda _path: ([(1, 2, 3)], 44100)
    )
    controller.send_file_queue(
        [
            {
                "filepath": "/fake/a.wav",
                "name": None,
                "bit_depth": 16,
                "sample_rate": None,
                "mono": False,
            }
        ],
        starting_sample_number=10,
    )
    assert controller._awaiting_count_for_queue is False


def test_sample_number_counter_updates_after_rename_discovered_slot(controller):
    # regression test: after a generic-SDS-and-rename send discovers
    # its actual landed slot via the pre/post SLIST diff, the counter
    # must advance past that slot - otherwise the NEXT file (using the
    # counter directly, eg a bit_depth==16 Akai send) collides with it
    from core.akai_sysex import encode_name, decode_name

    # names must be in the SAME padded format decode_name actually
    # produces from a real SLIST response - plain unpadded strings
    # would falsely look different at every index
    before_names = [decode_name(encode_name(n)) for n in ["A", "B", "C", "D"]]

    controller._next_file_sample_number = 4
    controller._rename_after_send = ("NEW NAME", 0)
    controller._pre_send_names = before_names
    controller._awaiting_post_send_slist_for_rename = True

    names_after = before_names + [
        decode_name(encode_name("NEW ONE"))
    ]  # landed at index 4
    payload = [0x47, 0x00, 0x05, 0x48, len(names_after), 0x00]
    for name in names_after:
        payload.extend(encode_name(name))

    controller.on_sysex_received(payload)

    # slot 4 landed -> counter must advance to AT LEAST 5, so the next
    # file that needs a real counter-based number doesn't collide with it
    assert controller._next_file_sample_number == 5


def test_sample_number_counter_stays_when_nothing_new_found(controller):
    # if the diff finds no new/changed slot, the counter should be left
    # untouched rather than reset or advanced incorrectly
    from core.akai_sysex import encode_name, decode_name

    before_names = [decode_name(encode_name(n)) for n in ["A", "B", "C", "D"]]

    controller._next_file_sample_number = 4
    controller._rename_after_send = ("NEW NAME", 0)
    controller._pre_send_names = before_names
    controller._awaiting_post_send_slist_for_rename = True

    names_after = before_names  # identical - nothing changed
    payload = [0x47, 0x00, 0x05, 0x48, len(names_after), 0x00]
    for name in names_after:
        payload.extend(encode_name(name))

    controller.on_sysex_received(payload)

    assert controller._next_file_sample_number == 4


def test_open_loop_detected_when_no_input_port(controller):
    controller.midi_manager.input_name = None
    assert controller.is_open_loop() is True


def test_not_open_loop_when_input_port_present(controller):
    controller.midi_manager.input_name = "Some Real Interface"
    assert controller.is_open_loop() is False
