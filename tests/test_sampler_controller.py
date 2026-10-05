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
    ctrl = controller_module.SamplerController(midi)

    # _FakeQTimer fires immediately, which would trip the reply watchdog the
    # instant it's armed - keep its bookkeeping but leave firing to the
    # tests that exercise it (via ctrl._on_reply_timeout(ctrl._reply_generation))
    def _arm_without_timer(label, timeout_ms=None):
        ctrl._reply_generation += 1
        ctrl._reply_wait_label = label

    ctrl._arm_reply_timeout = _arm_without_timer
    return ctrl


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


# -----------------------------------------------------------------
# CANCEL TRANSFER
# -----------------------------------------------------------------


def test_cancel_transfer_during_send_sends_cancel_and_dels(controller):
    from core import sds_encoder

    controller._send_queue = [b"\xf0fake\xf7"]
    controller._send_index = 2
    controller._active_channel = 3
    controller._active_sample_number = 9

    finished = []
    controller.transfer_finished.connect(lambda ok: finished.append(ok))

    controller.cancel_transfer()

    cancel_msg = controller.midi_manager.sent[0]
    assert cancel_msg[0] == 0x7E
    assert cancel_msg[1] == 3
    assert cancel_msg[2] == sds_encoder.CANCEL
    assert cancel_msg[3] == 2

    dels_msg = controller.midi_manager.sent[1]
    assert dels_msg[2] == 0x14  # DELS
    assert dels_msg[4] == 9  # sample number LSB

    assert controller._send_queue == []
    assert finished == [False]


def test_cancel_transfer_during_receive_sends_cancel_and_emits_receive_finished(controller):
    from core import sds_encoder

    controller._receiving = True
    controller._receive_channel = 4

    finished = []
    controller.receive_finished.connect(lambda ok: finished.append(ok))

    controller.cancel_transfer()

    cancel_msg = controller.midi_manager.sent[-1]
    assert cancel_msg[0] == 0x7E
    assert cancel_msg[1] == 4
    assert cancel_msg[2] == sds_encoder.CANCEL

    assert controller._receiving is False
    assert finished == [False]


def test_cancel_transfer_is_a_noop_when_nothing_in_progress(controller):
    controller.cancel_transfer()
    assert controller.midi_manager.sent == []


# -----------------------------------------------------------------
# HANDSHAKE DISPATCH (_on_handshake_message)
# monkeypatching _send_current_packet here to isolate the dispatch
# logic itself from the packet-pacing/timeout machinery below
# -----------------------------------------------------------------


def test_handshake_ack_advances_send_index_and_sends_next_packet(controller):
    controller._send_queue = [b"pkt0", b"pkt1", b"pkt2"]
    controller._send_index = 0
    calls = []
    controller._send_current_packet = lambda: calls.append(controller._send_index)

    progress = []
    controller.transfer_progress.connect(lambda sent, total: progress.append((sent, total)))

    controller._on_handshake_message("ack")

    assert controller._send_index == 1
    assert progress == [(1, 3)]
    assert calls == [1]


def test_handshake_nak_resends_current_packet_without_advancing(controller):
    controller._send_queue = [b"pkt0", b"pkt1"]
    controller._send_index = 0
    calls = []
    controller._send_current_packet = lambda: calls.append(controller._send_index)

    controller._on_handshake_message("nak")

    assert controller._send_index == 0
    assert calls == [0]


def test_handshake_wait_does_nothing(controller):
    controller._send_queue = [b"pkt0"]
    controller._send_index = 0
    calls = []
    controller._send_current_packet = lambda: calls.append("called")

    status = []
    controller.status_changed.connect(lambda msg: status.append(msg))

    controller._on_handshake_message("wait")

    assert controller._send_index == 0
    assert calls == []
    assert any("wait" in s.lower() for s in status)


def test_handshake_cancel_aborts_transfer(controller):
    controller._send_queue = [b"pkt0"]
    controller._send_index = 0

    finished = []
    controller.transfer_finished.connect(lambda ok: finished.append(ok))

    controller._on_handshake_message("cancel")

    assert controller._send_queue == []
    assert finished == [False]


def test_handshake_ignored_when_no_send_in_progress(controller):
    controller._send_queue = []
    controller._on_handshake_message("ack")  # should not raise


# -----------------------------------------------------------------
# ACK-TIMEOUT -> OPEN LOOP FALLBACK (_check_packet_timeout)
# -----------------------------------------------------------------


def test_check_packet_timeout_switches_to_open_loop_when_generation_matches(controller):
    controller._send_queue = [b"pkt0", b"pkt1"]
    controller._send_index = 0
    controller._packet_send_generation = 5
    calls = []
    controller._send_current_packet = lambda: calls.append(controller._send_index)

    controller._check_packet_timeout(5)

    assert controller._no_response_detected is True
    assert controller._send_index == 1
    assert calls == [1]


def test_check_packet_timeout_ignored_when_generation_is_stale(controller):
    # a real ACK/NAK already arrived and moved things on since the timeout was scheduled
    controller._send_queue = [b"pkt0"]
    controller._send_index = 0
    controller._packet_send_generation = 7
    calls = []
    controller._send_current_packet = lambda: calls.append("called")

    controller._check_packet_timeout(5)

    assert controller._no_response_detected is False
    assert calls == []


def test_check_packet_timeout_ignored_when_transfer_already_finished(controller):
    controller._send_queue = []
    controller._check_packet_timeout(1)  # should not raise
    assert controller._no_response_detected is False


def test_open_loop_send_paces_through_whole_queue_without_waiting_for_ack(controller):
    controller.set_device_type("generic")  # keeps the post-completion refresh a no-op
    controller.midi_manager.input_name = None  # open loop - no input port selected
    controller._send_queue = [b"\xf0hdr\xf7", b"\xf0d1\xf7", b"\xf0d2\xf7"]
    controller._send_index = 0

    finished = []
    controller.transfer_finished.connect(lambda ok: finished.append(ok))

    controller._send_current_packet()

    assert finished == [True]
    assert controller._send_index == 0  # reset by _abort_transfer on completion
    assert len(controller.midi_manager.sent) == 3


# -----------------------------------------------------------------
# STEREO CONTINUATION
# -----------------------------------------------------------------


def test_stereo_send_continues_to_right_channel_after_left_completes(controller, monkeypatch):
    from core import sds_encoder

    monkeypatch.setattr(
        sds_encoder, "read_wav_channels", lambda _p: ([[1, 2], [3, 4]], 44100)
    )

    status_messages = []
    controller.status_changed.connect(lambda msg: status_messages.append(msg))
    finished = []
    controller.transfer_finished.connect(lambda ok: finished.append(ok))

    controller.send_stereo_sample_file(
        "/fake/stereo.wav", sample_number_left=10, sample_number_right=11, channel=0
    )

    assert any("left channel first" in m.lower() for m in status_messages)
    assert any("right channel" in m.lower() for m in status_messages)
    assert finished == [True]
    assert controller._stereo_queue == []

    right_sdata_messages = [
        m
        for m in controller.midi_manager.sent
        if len(m) > 4 and m[0] == 0x47 and m[2] == 0x0B and m[4] == 11
    ]
    assert right_sdata_messages


# -----------------------------------------------------------------
# SAMPLE INFO REQUEST
# -----------------------------------------------------------------


def test_request_sample_info_sends_rsdata_and_sets_flag(controller):
    controller.request_sample_info(7, channel=2)
    sent = controller.midi_manager.sent[-1]
    assert sent[1] == 2
    assert sent[2] == 0x0A  # RSDATA
    assert controller._awaiting_sample_info is True


def test_request_sample_info_blocked_when_transfer_in_progress(controller):
    controller._send_queue = [b"pkt"]
    controller.request_sample_info(7)
    assert controller.midi_manager.sent == []
    assert controller._awaiting_sample_info is False


def test_sample_info_response_emits_parsed_info(controller):
    from core import akai_sysex

    controller._awaiting_sample_info = True
    msg = akai_sysex.build_sdata_message(
        name="INFO TEST", sample_length=123, sample_rate=22050, sample_number=9, channel=0
    )
    captured = []
    controller.sample_info_received.connect(lambda info: captured.append(info))

    controller.on_sysex_received(list(msg[1:-1]))

    assert controller._awaiting_sample_info is False
    assert captured[0]["name"] == "INFO TEST"
    assert captured[0]["sample_length"] == 123
    assert captured[0]["sample_rate"] == 22050


# -----------------------------------------------------------------
# FILE QUEUE - SKIPPING UNREADABLE FILES
# -----------------------------------------------------------------


def test_file_queue_skips_unreadable_file_and_continues_with_the_rest(controller, monkeypatch):
    from core import sds_encoder

    def fake_read_wav_channels(path):
        if "bad" in path:
            raise ValueError("corrupt file")
        return [[1, 2, 3]], 44100

    monkeypatch.setattr(sds_encoder, "read_wav_channels", fake_read_wav_channels)
    controller.set_device_type("generic")  # skip the SLIST round trip, go straight to sending

    transferred = []
    controller.file_transferred.connect(lambda path: transferred.append(path))
    finished = []
    controller.transfer_finished.connect(lambda ok: finished.append(ok))
    status_messages = []
    controller.status_changed.connect(lambda msg: status_messages.append(msg))

    controller.send_file_queue(
        [
            {
                "filepath": "/fake/bad.wav",
                "name": None,
                "bit_depth": 16,
                "sample_rate": None,
                "mono": False,
            },
            {
                "filepath": "/fake/good.wav",
                "name": None,
                "bit_depth": 16,
                "sample_rate": None,
                "mono": False,
            },
        ],
        starting_sample_number=0,
    )

    # bad file still gets reported as "transferred" so the UI removes it from the queue
    assert "/fake/bad.wav" in transferred
    assert controller._file_queue_skipped == 1
    assert finished == [True]
    assert any("skip" in m.lower() for m in status_messages)


# -----------------------------------------------------------------
# IN-PROGRESS GUARDS
# -----------------------------------------------------------------


def test_send_sample_file_generic_blocked_when_transfer_in_progress(controller, monkeypatch):
    from core import sds_encoder

    monkeypatch.setattr(sds_encoder, "read_wav_samples", lambda _p: ([1, 2, 3], 44100))

    controller._send_queue = [b"pkt"]
    controller.send_sample_file_generic("/fake/a.wav", sample_number=0)

    assert controller.midi_manager.sent == []


def test_receive_sample_generic_blocked_when_transfer_in_progress(controller):
    controller._send_queue = [b"pkt"]
    controller.receive_sample_generic(1, "/fake/out.wav")
    assert controller.midi_manager.sent == []
    assert controller._receiving is False


def test_receive_samples_blocked_when_transfer_in_progress(controller):
    controller._receiving = True
    controller.receive_samples([(1, "/fake/out.wav")])
    assert controller._receive_queue == []


# -----------------------------------------------------------------
# ERROR RECOVERY (on_sysex_received catching unexpected exceptions)
# -----------------------------------------------------------------


def test_unhandled_exception_during_send_recovers_state_and_emits_finished(
    controller, monkeypatch
):
    from core import akai_sysex

    def boom(_data_bytes):
        raise RuntimeError("boom")

    monkeypatch.setattr(akai_sysex, "parse_slist_response", boom)

    controller._send_queue = [b"pkt"]
    finished = []
    controller.transfer_finished.connect(lambda ok: finished.append(ok))
    status_messages = []
    controller.status_changed.connect(lambda msg: status_messages.append(msg))

    controller.on_sysex_received([0x47, 0x00, 0x05, 0x48, 0x00, 0x00])

    assert controller._send_queue == []
    assert finished == [False]
    assert any("unexpected error" in m.lower() for m in status_messages)


def test_unhandled_exception_during_receive_emits_receive_finished(controller, monkeypatch):
    from core import akai_sysex

    def boom(_data_bytes):
        raise RuntimeError("boom")

    monkeypatch.setattr(akai_sysex, "parse_slist_response", boom)

    controller._receiving = True
    finished = []
    controller.receive_finished.connect(lambda ok: finished.append(ok))

    controller.on_sysex_received([0x47, 0x00, 0x05, 0x48, 0x00, 0x00])

    assert controller._receiving is False
    assert finished == [False]


# -----------------------------------------------------------------
# RECEIVING - GENERIC SDS (universal dump header/data packets)
# -----------------------------------------------------------------


def test_generic_receive_full_round_trip_writes_wav(controller, tmp_path):
    from core import sds_encoder

    samples = [100, -100, 200, -200, 300]
    framerate = 44100
    save_path = str(tmp_path / "received.wav")

    controller.receive_sample_generic(sample_number=5, save_path=save_path, channel=0)
    assert controller._receiving is True

    header_packet = sds_encoder.build_dump_header(
        samples, framerate, sample_number=5, channel=0, bit_depth=16
    )
    controller.on_sysex_received(list(header_packet[1:-1]))

    assert controller._receive_header_info is not None
    ack_msg = controller.midi_manager.sent[-1]
    assert ack_msg[0] == 0x7E
    assert ack_msg[2] == sds_encoder.ACK

    data_packets = sds_encoder.build_data_packets(samples, channel=0, bit_depth=16)
    received = []
    controller.sample_received.connect(lambda path: received.append(path))

    for packet in data_packets:
        controller.on_sysex_received(list(packet[1:-1]))

    assert received == [save_path]
    assert controller._receiving is False

    read_back, rate = sds_encoder.read_wav_samples(save_path)
    assert list(read_back) == samples
    assert abs(rate - framerate) <= 1  # tiny precision loss from the ns-period round trip


def test_receive_data_packet_checksum_failure_sends_nak_and_holds_the_packet(controller):
    from core import sds_encoder

    controller._receiving = True
    controller._receive_channel = 0
    controller._receive_header_info = {
        "bit_depth": 16,
        "sample_length": 5,
        "sample_number": 1,
        "sample_rate": 44100,
    }
    controller._receive_expected_packets = 1
    controller._receive_packets = []

    packets = sds_encoder.build_data_packets([1, 2, 3], channel=0, bit_depth=16)
    corrupted = bytearray(packets[0][1:-1])
    corrupted[5] ^= 0x7F  # flip a data byte so the checksum no longer matches

    controller.on_sysex_received(list(corrupted))

    assert controller._receive_packets == []  # rejected, not accepted
    nak_msg = controller.midi_manager.sent[-1]
    assert nak_msg[0] == 0x7E
    assert nak_msg[2] == sds_encoder.NAK


def test_receive_data_packet_emits_decoded_chunks_progressively(controller, tmp_path):
    # sample_chunk_received - purely additive, but the actual point of the
    # feature (program_editor_window.py's progressive waveform): a
    # listener should see the same words _finish_receiving's own one-shot
    # decode would produce, in order, packet by packet as they arrive -
    # not just at the very end
    from core import sds_encoder

    samples = list(range(-45, 45))  # 90 words -> spans 3 packets at 40 words/packet (16-bit)
    save_path = str(tmp_path / "chunked.wav")

    controller.receive_sample_generic(sample_number=1, save_path=save_path, channel=0)
    header_packet = sds_encoder.build_dump_header(
        samples, framerate=44100, sample_number=1, channel=0, bit_depth=16
    )
    controller.on_sysex_received(list(header_packet[1:-1]))

    data_packets = sds_encoder.build_data_packets(samples, channel=0, bit_depth=16)
    assert len(data_packets) == 3  # otherwise this isn't actually exercising multiple chunks

    chunks = []
    controller.sample_chunk_received.connect(lambda c: chunks.append(c))

    for packet in data_packets:
        controller.on_sysex_received(list(packet[1:-1]))

    # one chunk per accepted packet, the last one trimmed to the real
    # remainder rather than including that packet's zero padding
    assert [len(c) for c in chunks] == [40, 40, 10]
    assert [s for chunk in chunks for s in chunk] == samples


def test_receive_data_packet_chunk_is_scaled_to_16bit_for_non_16bit_depths(controller, tmp_path):
    from core import sds_encoder

    samples = list(range(-10, 10))  # fits in a single 8-bit packet
    save_path = str(tmp_path / "chunked8.wav")

    controller.receive_sample_generic(sample_number=1, save_path=save_path, channel=0)
    header_packet = sds_encoder.build_dump_header(
        samples, framerate=44100, sample_number=1, channel=0, bit_depth=8
    )
    controller.on_sysex_received(list(header_packet[1:-1]))

    data_packets = sds_encoder.build_data_packets(samples, channel=0, bit_depth=8)

    chunks = []
    controller.sample_chunk_received.connect(lambda c: chunks.append(c))
    for packet in data_packets:
        controller.on_sysex_received(list(packet[1:-1]))

    assert len(chunks) == 1
    assert chunks[0] == [s << 8 for s in samples]


def test_checksum_failure_does_not_emit_a_chunk(controller):
    from core import sds_encoder

    controller._receiving = True
    controller._receive_channel = 0
    controller._receive_header_info = {
        "bit_depth": 16, "sample_length": 5, "sample_number": 1, "sample_rate": 44100,
    }
    controller._receive_expected_packets = 1
    controller._receive_packets = []

    packets = sds_encoder.build_data_packets([1, 2, 3], channel=0, bit_depth=16)
    corrupted = bytearray(packets[0][1:-1])
    corrupted[5] ^= 0x7F

    chunks = []
    controller.sample_chunk_received.connect(lambda c: chunks.append(c))
    controller.on_sysex_received(list(corrupted))

    assert chunks == []


@pytest.mark.parametrize(
    "sample_length,bit_depth",
    [
        (1, 16),  # shorter than a single packet - all padding but 1 word
        (40, 16),  # exactly one full 16-bit packet (120 bytes / 3 bytes/word)
        (41, 16),  # one word into a second packet
        (127, 12),  # doesn't divide evenly into 12-bit's 60 words/packet
        (500, 8),  # several full 8-bit packets (60 words/packet)
        (3000, 16),  # a genuinely large multi-packet transfer
    ],
)
def test_receive_data_packet_chunks_sum_to_the_real_length_at_any_size(
    controller, tmp_path, sample_length, bit_depth
):
    # the progressive-chunk path has no length baked in anywhere - it
    # decodes whatever arrives, packet by packet, trimming only the very
    # last packet's own zero padding - this pins that down across sample
    # lengths from shorter-than-one-packet up to several thousand words,
    # and across bit depths with differently-sized packet boundaries
    from core import sds_encoder

    # generate in the full 16-bit range, then reduce to fit bit_depth -
    # same convention test_sds_encoder.py's own round-trip tests use;
    # skipping this for bit_depth=8 previously fed the encoder values
    # outside its valid -128..127 range, which isn't a real-world case
    samples = [
        sds_encoder.reduce_bit_depth(((i * 3079) % 65536) - 32768, bit_depth)
        for i in range(sample_length)
    ]
    save_path = str(tmp_path / f"chunked_{sample_length}_{bit_depth}.wav")

    controller.receive_sample_generic(sample_number=1, save_path=save_path, channel=0)
    header_packet = sds_encoder.build_dump_header(
        samples, framerate=44100, sample_number=1, channel=0, bit_depth=bit_depth
    )
    controller.on_sysex_received(list(header_packet[1:-1]))

    data_packets = sds_encoder.build_data_packets(samples, channel=0, bit_depth=bit_depth)

    chunks = []
    controller.sample_chunk_received.connect(lambda c: chunks.append(c))
    for packet in data_packets:
        controller.on_sysex_received(list(packet[1:-1]))

    expected = [sds_encoder.scale_sample_to_16bit(s, bit_depth) for s in samples]
    assert [s for chunk in chunks for s in chunk] == expected
    assert len(chunks) == len(data_packets)  # one chunk per accepted packet, no batching


# -----------------------------------------------------------------
# RECEIVING - AKAI SDATA/RSPACK FLOW
# -----------------------------------------------------------------


def test_akai_receive_flow_requests_rspack_and_saves_wav(controller, tmp_path):
    from core import akai_sysex, sds_encoder

    save_path = str(tmp_path / "akai_received.wav")
    controller.receive_samples([(3, save_path)], channel=0)

    request = controller.midi_manager.sent[-1]
    assert request[2] == 0x0A  # RSDATA

    sdata_msg = akai_sysex.build_sdata_message(
        name="AKAI SAMP", sample_length=5, sample_rate=44100, sample_number=3, channel=0
    )
    controller.on_sysex_received(list(sdata_msg[1:-1]))

    rspack_msg = controller.midi_manager.sent[-1]
    assert rspack_msg[2] == 0x0C  # RSPACK

    samples = [10, -10, 20, -20, 30]
    data_packets = sds_encoder.build_data_packets(samples, channel=0, bit_depth=16)

    finished = []
    controller.receive_finished.connect(lambda ok: finished.append(ok))

    for packet in data_packets:
        controller.on_sysex_received(list(packet[1:-1]))

    assert finished == [True]  # queue now empty after this one sample

    read_back, rate = sds_encoder.read_wav_samples(save_path)
    assert list(read_back) == samples
    assert rate == 44100


def _statuses(controller):
    out = []
    controller.status_changed.connect(out.append)
    return out


def test_post_send_check_warns_when_sample_never_appears(controller):
    # 3 samples before the batch, 1 unit sent, but the sampler still lists 3
    statuses = _statuses(controller)
    controller._verify_pending = (3, 1)
    controller._verify_sent_samples_arrived(["A", "B", "C"])
    assert any("didn't receive" in s for s in statuses)
    assert controller._verify_pending is None


def test_post_send_check_is_quiet_when_sample_appears(controller):
    statuses = _statuses(controller)
    controller._verify_pending = (3, 1)
    controller._verify_sent_samples_arrived(["A", "B", "C", "D"])
    assert not any("didn't receive" in s for s in statuses)


def test_completed_send_without_any_ack_is_reported_as_unverified(controller):
    statuses = _statuses(controller)
    controller._send_queue = [b"pkt0"]
    controller._no_response_detected = True  # input port selected but sampler silent
    controller._abort_transfer(completed=True)
    controller._finish_unit(True)
    assert any("unverified" in s for s in statuses)


def test_completed_send_with_acks_is_not_flagged_unverified(controller):
    statuses = _statuses(controller)
    controller._send_queue = [b"pkt0"]
    controller._abort_transfer(completed=True)
    controller._finish_unit(True)
    assert "Transfer complete" in statuses


def test_refresh_with_no_reply_times_out_and_clears_the_wait(controller):
    statuses = _statuses(controller)
    controller.refresh_sample_list()
    assert controller._awaiting_memory_status is True
    controller._on_reply_timeout(controller._reply_generation)
    assert controller._awaiting_memory_status is False
    assert any("No reply from the sampler" in s for s in statuses)


def test_reply_arriving_cancels_the_watchdog(controller):
    statuses = _statuses(controller)
    controller.refresh_sample_list()
    stale_generation = controller._reply_generation
    controller._disarm_reply_timeout()  # what a real STAT/SLIST reply does
    controller._on_reply_timeout(stale_generation)
    assert not any("No reply" in s for s in statuses)


def test_pre_send_slist_timeout_aborts_the_batch(controller):
    statuses = _statuses(controller)
    finished = []
    controller.transfer_finished.connect(finished.append)
    controller._file_queue = [{"filepath": "x.wav"}]
    controller._awaiting_count_for_queue = True
    controller._arm_reply_timeout("sample list (RSLIST)")
    controller._on_reply_timeout(controller._reply_generation)
    assert controller._awaiting_count_for_queue is False
    assert controller._file_queue == []
    assert finished == [False]
    assert any("No reply from the sampler" in s for s in statuses)


def test_refresh_without_output_port_reports_instead_of_raising(controller):
    statuses = _statuses(controller)

    def boom(_data):
        raise RuntimeError("No MIDI output port is open")

    controller.midi_manager.send_sysex = boom
    controller.refresh_sample_list()  # must not raise
    assert controller._awaiting_memory_status is False
    assert any("No MIDI output port" in s for s in statuses)


def test_receive_stall_aborts_the_receive_and_reports_failure(controller):
    statuses = _statuses(controller)
    finished = []
    controller.receive_finished.connect(finished.append)
    controller.receive_samples([(0, "/tmp/x.wav")])
    assert controller._receiving is True
    controller._on_reply_timeout(controller._reply_generation)
    assert controller._receiving is False
    assert finished == [False]
    assert any("No reply from the sampler" in s for s in statuses)


def test_mid_receive_stall_nudges_the_sampler_before_giving_up(controller):
    from core import sds_encoder

    finished = []
    controller.receive_finished.connect(finished.append)
    controller._receiving = True
    controller._receive_channel = 0
    controller._receive_header_info = {
        "bit_depth": 16,
        "sample_length": 500,
        "sample_number": 1,
        "sample_rate": 44100,
    }
    controller._receive_expected_packets = 5
    controller._receive_packets = [b"x"] * 2
    controller._receive_last_packet_num = 1
    controller._arm_reply_timeout("next data packet", 3000)

    sent_before = len(controller.midi_manager.sent)
    controller._on_reply_timeout(controller._reply_generation)
    assert controller._receiving is True
    assert list(controller.midi_manager.sent[sent_before][2:]) == [sds_encoder.ACK, 1]
    controller._on_reply_timeout(controller._reply_generation)
    assert list(controller.midi_manager.sent[sent_before + 1][2:]) == [sds_encoder.NAK, 2]
    controller._on_reply_timeout(controller._reply_generation)
    assert controller._receiving is True
    controller._on_reply_timeout(controller._reply_generation)  # retries exhausted
    assert controller._receiving is False
    assert finished == [False]


def test_duplicate_data_packet_is_acked_but_not_appended_twice(controller):
    from core import sds_encoder

    controller._receiving = True
    controller._receive_channel = 0
    controller._receive_header_info = {
        "bit_depth": 16,
        "sample_length": 500,
        "sample_number": 1,
        "sample_rate": 44100,
    }
    controller._receive_expected_packets = 5
    controller._receive_packets = []
    packet = sds_encoder.build_data_packets([1, 2, 3], channel=0, bit_depth=16)[0]
    controller.on_sysex_received(list(packet[1:-1]))
    controller.on_sysex_received(list(packet[1:-1]))
    assert len(controller._receive_packets) == 1
    assert controller.midi_manager.sent[-1][2] == sds_encoder.ACK


def _start_fake_akai_receive(controller, expected_packets=5):
    controller._receiving = True
    controller._receive_channel = 0
    controller._receive_sample_number = 1
    controller._receive_header_info = {
        "bit_depth": 16,
        "sample_length": 500,
        "sample_number": 1,
        "sample_rate": 44100,
        "name": "TEST",
    }
    controller._receive_expected_packets = expected_packets
    controller._receive_packets = []
    controller._reset_receive_diagnostics()


def test_cancel_from_sampler_during_receive_fails_fast(controller, caplog):
    import logging
    from core import sds_encoder

    statuses = _statuses(controller)
    finished = []
    controller.receive_finished.connect(finished.append)
    _start_fake_akai_receive(controller)
    with caplog.at_level(logging.WARNING, logger="akaisds"):
        controller.on_sysex_received([0x7E, 0x00, sds_encoder.CANCEL, 0x02])
    assert controller._receiving is False
    assert finished == [False]
    assert any("cancelled the transfer" in s for s in statuses)
    assert any("sent CANCEL during receive" in r.message for r in caplog.records)


def test_stray_ack_nak_wait_during_receive_are_logged_not_acted_on(controller, caplog):
    import logging
    from core import sds_encoder

    _start_fake_akai_receive(controller)
    sent_before = len(controller.midi_manager.sent)
    with caplog.at_level(logging.WARNING, logger="akaisds"):
        for sub_id in (sds_encoder.ACK, sds_encoder.NAK, sds_encoder.WAIT):
            controller.on_sysex_received([0x7E, 0x00, sub_id, 0x01])
    assert controller._receiving is True
    assert len(controller.midi_manager.sent) == sent_before
    assert controller._receive_stray_handshakes == 3
    for name in ("ACK", "NAK", "WAIT"):
        assert any(f"unexpected {name} from the sampler" in r.message for r in caplog.records)


def test_stray_handshake_logging_is_capped(controller, caplog):
    import logging
    from core import sds_encoder

    _start_fake_akai_receive(controller)
    with caplog.at_level(logging.WARNING, logger="akaisds"):
        for _ in range(50):
            controller.on_sysex_received([0x7E, 0x00, sds_encoder.ACK, 0x01])
    logged = [r for r in caplog.records if "unexpected ACK" in r.message]
    assert len(logged) == controller._RECEIVE_LOG_MAX_STRAY_HANDSHAKES
    assert controller._receive_stray_handshakes == 50


def test_handshake_during_a_send_still_reaches_the_send_logic(controller):
    from core import sds_encoder

    controller._receiving = True  # stale flag must not steal a send's ACKs
    controller._send_queue = [b"a", b"b"]
    controller._send_index = 0
    sent = []
    controller._send_current_packet = lambda: sent.append(controller._send_index)
    controller.on_sysex_received([0x7E, 0x00, sds_encoder.ACK, 0x00])
    assert controller._send_index == 1
    assert sent == [1]


def test_stall_error_log_includes_receive_diagnostics(controller, caplog):
    import logging

    _start_fake_akai_receive(controller)
    controller._receive_packet_retries = controller._receive_packet_max_retries
    controller._arm_reply_timeout("next data packet", 3000)
    with caplog.at_level(logging.ERROR, logger="akaisds"):
        controller._on_reply_timeout(controller._reply_generation)
    assert any(
        "no data packet received yet" in r.message
        and "unexpected handshake message(s)" in r.message
        for r in caplog.records
    )


def test_data_packet_timing_is_logged_for_the_first_packets(controller, caplog):
    import logging
    from core import sds_encoder

    _start_fake_akai_receive(controller)
    packet = sds_encoder.build_data_packets([1, 2, 3], channel=0, bit_depth=16)[0]
    with caplog.at_level(logging.DEBUG, logger="akaisds"):
        controller.on_sysex_received(list(packet[1:-1]))
    assert any("receive data packet #1" in r.message for r in caplog.records)
    assert controller._receive_last_packet_at is not None


def _start_akai_receive_of(controller, samples, tmp_path):
    # drives the real RSDATA -> SDATA -> RSPACK exchange, returns the packets
    # the sampler would send and the save path
    from core import akai_sysex, sds_encoder

    save_path = str(tmp_path / "resumed.wav")
    controller.receive_samples([(3, save_path)], channel=0)
    sdata_msg = akai_sysex.build_sdata_message(
        name="AKAI SAMP",
        sample_length=len(samples),
        sample_rate=44100,
        sample_number=3,
        channel=0,
    )
    controller.on_sysex_received(list(sdata_msg[1:-1]))
    return save_path, sds_encoder.build_data_packets(samples, channel=0, bit_depth=16)


def _rspack_fields(message):
    # [0x47, cc, 0x0C, 0x48, ss, ss, o*4, n*4, ii, ff]
    def _value(b):
        return b[0] | (b[1] << 7) | (b[2] << 14) | (b[3] << 21)

    return _value(message[6:10]), _value(message[10:14])


def _timeout(controller, times=1):
    for _ in range(times):
        controller._on_reply_timeout(controller._reply_generation)


def test_akai_stall_resumes_with_a_new_rspack_from_the_received_offset(
    controller, tmp_path
):
    from core import sds_encoder

    samples = list(range(1, 121))  # 3 packets of 40 words
    save_path, packets = _start_akai_receive_of(controller, samples, tmp_path)
    for packet in packets[:2]:
        controller.on_sysex_received(list(packet[1:-1]))

    # three ACK/NAK nudges first, exactly as before
    sent_before = len(controller.midi_manager.sent)
    _timeout(controller, 3)
    assert len(controller.midi_manager.sent) == sent_before + 3
    assert controller._receive_resume_attempts == 0

    # the fourth stall resumes: CANCEL, then a new RSPACK from word 80
    _timeout(controller)
    assert controller._receiving is True
    assert controller._receive_resume_attempts == 1
    cancel, rspack = controller.midi_manager.sent[-2:]
    assert list(cancel[2:3]) == [sds_encoder.CANCEL]
    assert rspack[2] == 0x0C
    assert _rspack_fields(rspack) == (80, 40)

    # the resumed stream restarts at packet 0 - its words finish the sample
    resumed = sds_encoder.build_data_packets(samples[80:], channel=0, bit_depth=16)
    finished = []
    controller.receive_finished.connect(finished.append)
    controller.on_sysex_received(list(resumed[0][1:-1]))

    assert finished == [True]
    read_back, _ = sds_encoder.read_wav_samples(save_path)
    assert list(read_back) == samples


def test_resume_skips_the_nudges_after_the_first_resume(controller, tmp_path):
    samples = list(range(1, 241))
    _, packets = _start_akai_receive_of(controller, samples, tmp_path)
    controller.on_sysex_received(list(packets[0][1:-1]))
    _timeout(controller, 3)  # nudges
    _timeout(controller)  # resume 1
    sent = len(controller.midi_manager.sent)
    _timeout(controller)  # next stall: straight to resume 2, no ACK/NAK nudge
    assert controller._receive_resume_attempts == 2
    assert controller.midi_manager.sent[sent][2] == 0x7D  # CANCEL


def test_stalled_receive_gives_up_after_the_resume_cap_and_cancels(
    controller, tmp_path
):
    from core import sds_encoder

    statuses = _statuses(controller)
    finished = []
    controller.receive_finished.connect(finished.append)
    samples = list(range(1, 241))
    _, packets = _start_akai_receive_of(controller, samples, tmp_path)
    controller.on_sysex_received(list(packets[0][1:-1]))
    _timeout(controller, 3 + controller._receive_max_resumes)
    assert controller._receiving is True
    sent_before = len(controller.midi_manager.sent)
    _timeout(controller)  # cap reached
    assert controller._receiving is False
    assert finished == [False]
    assert any("No reply from the sampler" in s for s in statuses)
    assert list(controller.midi_manager.sent[sent_before][2:3]) == [sds_encoder.CANCEL]


def test_generic_receive_stall_is_not_resumed_but_still_cancels(controller):
    # only the Akai RSPACK path has an offset to resume from
    from core import sds_encoder

    finished = []
    controller.receive_finished.connect(finished.append)
    controller._receiving = True
    controller._receive_channel = 0
    controller._receive_header_info = {
        "bit_depth": 16,
        "sample_length": 500,
        "sample_number": 1,
        "sample_rate": 44100,
    }
    controller._receive_expected_packets = 5
    controller._receive_packets = [b"x"] * 2
    controller._receive_packet_retries = controller._receive_packet_max_retries
    controller._arm_reply_timeout("next data packet", 3000)
    controller._on_reply_timeout(controller._reply_generation)
    assert controller._receive_resume_attempts == 0
    assert controller._receiving is False
    assert finished == [False]
    assert controller.midi_manager.sent[-1][2] == sds_encoder.CANCEL


def test_dump_header_answering_a_resume_does_not_wipe_received_packets(
    controller, tmp_path
):
    from core import sds_encoder

    samples = list(range(1, 121))
    _, packets = _start_akai_receive_of(controller, samples, tmp_path)
    for packet in packets[:2]:
        controller.on_sysex_received(list(packet[1:-1]))
    _timeout(controller, 4)  # nudges, then the resume
    assert controller._receive_resuming is True

    header = sds_encoder.build_dump_header(
        samples[80:], 44100, sample_number=3, channel=0, bit_depth=16
    )
    controller.on_sysex_received(list(header[1:-1]))
    assert len(controller._receive_packets) == 2
    assert list(controller.midi_manager.sent[-1][2:]) == [sds_encoder.ACK, 0]


def test_resume_does_not_fire_if_the_receive_ended_during_the_delay(
    controller, controller_module, monkeypatch, tmp_path
):
    samples = list(range(1, 241))
    _, packets = _start_akai_receive_of(controller, samples, tmp_path)
    controller.on_sysex_received(list(packets[0][1:-1]))
    _timeout(controller, 3)

    # hold the delayed RSPACK instead of firing it immediately
    delayed = []
    monkeypatch.setattr(
        controller_module.QTimer, "singleShot", staticmethod(lambda _ms, fn: delayed.append(fn))
    )
    _timeout(controller)  # resume: CANCEL goes out now, RSPACK is delayed
    assert len(delayed) == 1
    controller.cancel_transfer()  # the user cancels during the delay
    sent_before = len(controller.midi_manager.sent)
    delayed[0]()
    assert len(controller.midi_manager.sent) == sent_before  # no stray RSPACK


def test_sample_info_timeout_clears_the_busy_flag(controller):
    controller.request_sample_info(0)
    assert controller.is_transfer_busy() is True
    controller._on_reply_timeout(controller._reply_generation)
    assert controller.is_transfer_busy() is False


def test_cancel_disarms_the_watchdog(controller):
    statuses = _statuses(controller)
    controller.request_sample_info(0)
    stale = controller._reply_generation
    controller.cancel_transfer()
    controller._on_reply_timeout(stale)
    assert not any("No reply" in s for s in statuses)


def test_unexpected_sysex_is_logged(controller, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="akaisds"):
        controller._on_sysex_received_impl(bytes([0x47, 0, 0x55, 1, 2]))
    assert any("unrecognised Akai message" in r.message for r in caplog.records)


# --- Sampler Type: S1000 vs S2000/S3000 vs Generic ------------------------------


def test_set_device_type_s1000_keeps_the_akai_protocol_family(controller):
    # transfers only care about the protocol family - an S1000's SDS/RSTAT/
    # list traffic is the same as an S2000/S3000's - while the full model
    # stays available for the Program Editor
    controller.set_device_type("akai_s1000")
    assert controller.device_type == "akai"
    assert controller.sampler_model == "akai_s1000"


def test_set_device_type_s2000_s3000_is_the_akai_family(controller):
    controller.set_device_type("akai_s1000")
    controller.set_device_type("akai_s2000_s3000")
    assert controller.device_type == "akai"
    assert controller.sampler_model == "akai_s2000_s3000"


def test_set_device_type_accepts_the_legacy_akai_key(controller):
    # a config.json from before the S1000 existed
    controller.set_device_type("akai")
    assert controller.device_type == "akai"
    assert controller.sampler_model == "akai_s2000_s3000"


def test_set_device_type_generic_is_its_own_family(controller):
    controller.set_device_type("generic")
    assert controller.device_type == "generic"
    assert controller.sampler_model == "generic"


# --- Sampler Type: Akai S900/S950 (its own protocol family, no transfers yet) ---


def test_set_device_type_s900_s950_is_its_own_family(controller):
    controller.set_device_type("akai_s900_s950")
    assert controller.device_type == "s950"
    assert controller.sampler_model == "akai_s900_s950"


class _StubS950Engine:
    # stands in for controller/s950_transfers.S950Transfers - which needs real
    # Qt timers, so it's tested on its own (test_s950_transfers.py); this file
    # runs under a fake QtCore and only checks the controller's DELEGATION
    def __init__(self):
        self.calls = []
        self.busy = False
        self.cancel_result = False

    def refresh_catalog(self, silent=False):
        self.calls.append(("refresh_catalog", silent))

    def send_file_queue(self, entries):
        self.calls.append(("send_file_queue", entries))
        return True

    def receive_samples(self, requests):
        self.calls.append(("receive_samples", requests))

    def rename_sample(self, slot, name):
        self.calls.append(("rename_sample", slot, name))

    def request_sample_info(self, slot):
        self.calls.append(("request_sample_info", slot))

    def handle_sysex(self, data):
        self.calls.append(("handle_sysex", bytes(data)))

    def cancel(self):
        self.calls.append(("cancel",))
        return self.cancel_result


@pytest.fixture
def s950_controller(controller):
    controller.set_device_type("akai_s900_s950")
    controller._s950_engine = _StubS950Engine()
    statuses = []
    controller.status_changed.connect(statuses.append)
    controller.statuses = statuses
    return controller


def test_s950_entry_points_delegate_to_the_engine(s950_controller):
    c = s950_controller
    c.refresh_sample_list(silent=True)
    assert c.send_file_queue([{"filepath": "a.wav"}], starting_sample_number=5) is True
    c.receive_samples([(1, "x.wav")])
    c.rename_sample(4, "NEW")
    c.request_sample_info(4)
    assert c._s950_engine.calls == [
        ("refresh_catalog", True),
        ("send_file_queue", [{"filepath": "a.wav"}]),
        ("receive_samples", [(1, "x.wav")]),
        ("rename_sample", 4, "NEW"),
        ("request_sample_info", 4),
    ]
    # none of it went through the Akai/generic paths
    assert c.midi_manager.sent == []


def test_s950_sysex_goes_to_the_engine_not_the_akai_parser(s950_controller):
    c = s950_controller
    updates = []
    c.sample_list_updated.connect(updates.append)
    # function 0x05 is SLIST to the S1000 family
    c.on_sysex_received([0x47, 0x00, 0x05, 0x40, 0x00, 0x00, 0x00])
    assert updates == []
    assert c._s950_engine.calls == [("handle_sysex", bytes([0x47, 0, 5, 0x40, 0, 0, 0]))]


def test_s950_refuses_what_it_has_no_protocol_for(s950_controller, tmp_path):
    # delete has no S900/S950 opcode, and the legacy per-file / by-number
    # entry points are Akai-family and generic-SDS only: none may put bytes
    # on the wire for an S900/S950
    c = s950_controller
    wav = str(tmp_path / "x.wav")
    c.delete_sample(1)
    c.send_sample_file(wav)
    c.send_stereo_sample_file(wav, 0, 1)
    c.send_sample_file_generic(wav)
    c.send_sample_file_generic_and_rename(wav, "NEW")
    c.receive_sample_generic(1, wav)
    assert c.midi_manager.sent == []
    assert not c._receiving and not c._file_queue and not c._receive_queue
    assert len(c.statuses) == 6
    assert all("S900/S950" in s for s in c.statuses)
    assert c._s950_engine.calls == []


def test_cancel_and_busy_include_the_s950_engine(s950_controller):
    c = s950_controller
    assert c.is_transfer_busy() is False
    c._s950_engine.busy = True
    assert c.is_transfer_busy() is True
    c._s950_engine.cancel_result = True
    c.cancel_transfer()
    assert c._s950_engine.calls == [("cancel",)]


def test_the_s950_engine_is_not_built_for_other_sampler_types(controller):
    controller.refresh_sample_list()
    controller.set_device_type("generic")
    controller.cancel_transfer()
    assert controller.is_transfer_busy() is False
    assert controller._s950_engine is None


def test_leaving_the_s950_family_restores_normal_behaviour(s950_controller):
    c = s950_controller
    c.set_device_type("akai_s2000_s3000")
    c.refresh_sample_list()
    assert len(c.midi_manager.sent) == 1  # an RSTAT again
