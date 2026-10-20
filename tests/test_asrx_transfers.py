# tests for controller/asrx_transfers.py - the ASR-X list, receive and send choreography.
#
# The real AsrxTransfers and the real SamplerController run against core/demo_asrx.FakeASRX over a fake MidiManager that delivers replies the way the real one
# does - asynchronously, via the event loop, never from inside the send call. Needs a real (offscreen) QApplication for the timers. The fake is built from the same
# reading of the ASR-X ROM as the codec, so this checks the choreography, not that a real ASR-X agrees.

import logging
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from controller import asrx_transfers as at
from controller.sampler_controller import SamplerController
from core import asrx_sysex as a
from core import debug_log, sds_encoder
from core.demo_asrx import FakeASRX, make_sample_object, triangle

SOX, EOX = 0xF0, 0xF7


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Midi(QObject):
    sysex_received = Signal(bytes)

    def __init__(self, fake, input_name="Fake In"):
        super().__init__()
        self.fake = fake
        self.input_name = input_name
        self.output_name = "Fake Out"
        self.sent = []
        self.fail_sends = False
        self.paced = False

    def send_sysex(self, data):
        if self.fail_sends:
            raise RuntimeError("port closed")
        self.sent.append(bytes(data))
        self.fake.out.send_message([SOX, *data, EOX])
        QTimer.singleShot(0, self._deliver)

    def _deliver(self):
        if self.paced:
            got = self.fake.inp.get_message()
            if got is not None:
                self.sysex_received.emit(bytes(got[0][1:-1]))
                QTimer.singleShot(0, self._deliver)
            return
        while (got := self.fake.inp.get_message()) is not None:
            self.sysex_received.emit(bytes(got[0][1:-1]))


class _Recorder:
    def __init__(self, controller):
        self.statuses = []
        self.slots = []
        self.memory = []
        self.receive_finished = []
        self.transfer_finished = []
        self.sample_received = []
        self.file_transferred = []
        self.receive_progress = []
        self.unit_progress = []
        controller.status_changed.connect(self.statuses.append)
        controller.sample_slots_updated.connect(self.slots.append)
        controller.memory_status_updated.connect(self.memory.append)
        controller.receive_finished.connect(self.receive_finished.append)
        controller.transfer_finished.connect(self.transfer_finished.append)
        controller.sample_received.connect(self.sample_received.append)
        controller.file_transferred.connect(self.file_transferred.append)
        controller.receive_progress.connect(lambda x, y: self.receive_progress.append((x, y)))
        controller.unit_progress.connect(self.unit_progress.append)

    def status_has(self, text):
        return any(text in message for message in self.statuses)


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append((record.levelno, record.getMessage()))

    def has(self, text, level=None):
        return any(text in m and (level is None or lv == level) for lv, m in self.lines)


def wait_until(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.001)
    QCoreApplication.processEvents()
    return bool(predicate())


class _Rig:
    pass


def _build(qapp, monkeypatch, fake, input_name="Fake In"):
    monkeypatch.setattr(at, "MIDI_BYTES_PER_SECOND", 5_000_000)
    rig = _Rig()
    rig.log = _LogCapture()  # attached first: the engine logs while it is being built
    debug_log.get_logger().addHandler(rig.log)
    rig.fake = fake
    rig.midi = _Midi(fake, input_name)
    rig.controller = SamplerController(rig.midi)
    rig.controller.set_device_type("ensoniq_asrx")
    rig.engine = rig.controller._ensoniq
    rig.engine.reply_timeout_ms = 400
    rig.engine.probe_timeout_ms = 150
    rig.engine.pace_ms = 0
    rig.engine.ack_margin_s = 1.0
    rig.rec = _Recorder(rig.controller)
    return rig


def _teardown(rig):
    debug_log.get_logger().removeHandler(rig.log)
    rig.engine.cancel()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


@pytest.fixture
def rig(qapp, monkeypatch):
    r = _build(qapp, monkeypatch, FakeASRX())
    yield r
    _teardown(r)


def make_rig(qapp, monkeypatch, **fake_kwargs):
    return _build(qapp, monkeypatch, FakeASRX(**fake_kwargs))


def list_samples(rig):
    rig.controller.refresh_sample_list()
    assert wait_until(lambda: rig.engine.idle), rig.rec.statuses
    return rig.rec.slots[-1] if rig.rec.slots else None


def receive(rig, slot, path):
    rig.controller.receive_samples([(slot, str(path))])
    return wait_until(lambda: rig.rec.receive_finished, 20)


def send(rig, entries):
    started = rig.controller.send_file_queue(entries)
    assert started
    return wait_until(lambda: rig.rec.transfer_finished, 30)


def wav(tmp_path, name, samples, rate=44100, stereo_right=None):
    path = tmp_path / f"{name}.wav"
    if stereo_right is None:
        sds_encoder.write_wav_file(str(path), samples, rate, 16)
    else:
        sds_encoder.write_wav_file_stereo(str(path), samples, stereo_right, rate, 16)
    return str(path)


def entry(path, **extra):
    return {"filepath": path, "name": None, "bit_depth": 16, "sample_rate": None, "mono": False, **extra}


# --- engine choice -----------------------------------------------------------------


def test_the_asrx_type_uses_the_asrx_engine_and_the_other_type_the_old_one(qapp, monkeypatch):
    rig = _build(qapp, monkeypatch, FakeASRX())
    try:
        assert rig.controller.device_type == "ensoniq"
        assert isinstance(rig.engine, at.AsrxTransfers)
        rig.controller.set_device_type("ensoniq_eps_asr")
        from controller.ensoniq_transfers import EnsoniqTransfers

        assert isinstance(rig.controller._ensoniq, EnsoniqTransfers)
        rig.controller.set_device_type("ensoniq_asrx")
        assert isinstance(rig.controller._ensoniq, at.AsrxTransfers)
    finally:
        _teardown(rig)


def test_other_entry_points_still_refuse_for_the_asrx(rig):
    rig.controller.delete_sample(1)
    assert rig.rec.status_has("isn't available for the Ensoniq ASR-X")
    assert rig.midi.sent == []


# --- the list ------------------------------------------------------------------------


def test_the_list_scans_names_until_an_empty_run(rig):
    rig.engine.scan_empty_run = 5
    slots = list_samples(rig)
    assert slots == [(0, "KICK"), (1, "SNARE"), (2, "PAD C3")]
    assert rig.rec.status_has("3 samples on the ASR-X")
    # slots 0-2 answered, then 5 empty ones
    assert rig.fake.name_requests == list(range(8))
    assert rig.engine.occupied == {0, 1, 2}
    assert rig.rec.memory[-1]["max_num_samp_words"] == 0  # "unknown" to the Dashboard


def test_the_list_sends_nothing_but_name_requests(rig):
    rig.engine.scan_empty_run = 3
    list_samples(rig)
    assert all(m[0] == 0x7E and m[2:4] == bytes((5, 4)) for m in rig.midi.sent)


def test_a_gap_shorter_than_the_empty_run_does_not_hide_later_samples(qapp, monkeypatch):
    fake = FakeASRX()
    fake.slots[6] = make_sample_object(6, "LATE", frames=500)
    rig = _build(qapp, monkeypatch, fake)
    try:
        rig.engine.scan_empty_run = 8
        assert list_samples(rig)[-1] == (6, "LATE")
    finally:
        _teardown(rig)


def test_duplicate_names_get_their_slot_in_the_label(qapp, monkeypatch):
    fake = FakeASRX()
    fake.slots[1] = make_sample_object(1, "KICK", frames=500)
    rig = _build(qapp, monkeypatch, fake)
    try:
        rig.engine.scan_empty_run = 4
        assert [label for _s, label in list_samples(rig)][:2] == ["KICK (0)", "KICK (1)"]
    finally:
        _teardown(rig)


def test_hidden_slots_are_not_listed(qapp, monkeypatch):
    fake = FakeASRX(hidden={1})
    rig = _build(qapp, monkeypatch, fake)
    try:
        rig.engine.scan_empty_run = 4
        assert [slot for slot, _ in list_samples(rig)] == [0, 2]
    finally:
        _teardown(rig)


def _deaf(fake):
    """A unit that answers nobody: off, SysEx Recv off, or not on this cable."""
    fake._addressed = lambda device: False
    return fake


def test_silence_fails_with_advice(qapp, monkeypatch):
    rig = _build(qapp, monkeypatch, _deaf(FakeASRX()))
    try:
        rig.controller.refresh_sample_list()
        assert wait_until(lambda: rig.engine.idle)
        assert rig.rec.status_has("No reply from the ASR-X")
        assert rig.rec.status_has("Device ID")
        assert rig.rec.status_has("127")
        assert rig.log.has("FAILED", logging.WARNING)
        assert rig.log.has("last messages before", logging.WARNING)
    finally:
        _teardown(rig)


def test_silence_asks_twice_on_the_setting_then_once_as_127(qapp, monkeypatch):
    rig = _build(qapp, monkeypatch, _deaf(FakeASRX()))
    try:
        rig.controller.refresh_sample_list()
        assert wait_until(lambda: rig.engine.idle)
        devices = [m[1] for m in rig.midi.sent]
        assert devices == [0x00, 0x00, 0x7F]
    finally:
        _teardown(rig)


def test_a_silent_refresh_says_nothing(qapp, monkeypatch):
    rig = _build(qapp, monkeypatch, _deaf(FakeASRX()))
    try:
        rig.controller.refresh_sample_list(silent=True)
        assert wait_until(lambda: rig.engine.idle)
        assert rig.rec.statuses == []
    finally:
        _teardown(rig)


def test_a_unit_with_another_device_id_is_found_by_the_fallback(qapp, monkeypatch):
    rig = _build(qapp, monkeypatch, FakeASRX(device=9))  # Settings say 0
    try:
        rig.engine.scan_empty_run = 3
        assert [s for s, _ in list_samples(rig)] == [0, 1, 2]
        assert rig.rec.status_has("Device ID is 9, not 0")
        assert rig.log.has("with its own Device ID 9", logging.WARNING)
        # the 127 request was a read; everything after it goes to the unit's own ID
        devices = [m[1] for m in rig.midi.sent]
        assert devices.count(0x7F) == 1
        assert devices[-1] == 9
    finally:
        _teardown(rig)


def test_a_silent_refresh_finds_the_unit_without_a_message(qapp, monkeypatch):
    rig = _build(qapp, monkeypatch, FakeASRX(device=9))
    try:
        rig.engine.scan_empty_run = 3
        rig.controller.refresh_sample_list(silent=True)
        assert wait_until(lambda: rig.engine.idle)
        assert rig.rec.statuses == []
        assert rig.engine._device() == 9
    finally:
        _teardown(rig)


def test_the_found_device_id_is_dropped_when_the_setting_changes(qapp, monkeypatch):
    rig = _build(qapp, monkeypatch, FakeASRX(device=9))
    try:
        rig.engine.scan_empty_run = 3
        list_samples(rig)
        assert rig.engine._device() == 9
        rig.controller.set_channel(5)
        assert rig.engine._device() == 5
        rig.controller.set_channel(0)
        assert rig.engine._device() == 9  # same setting again: the unit is still the one that answered
    finally:
        _teardown(rig)


def test_a_send_to_a_unit_with_another_id_never_uses_127_for_the_upload(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeASRX(device=9))
    try:
        send(rig, [entry(wav(tmp_path, "kick", [1000, -1000] * 500))])
        assert wait_until(lambda: rig.engine.idle)
        puts = [m for m in rig.midi.sent if len(m) > 4 and m[:3] == bytes(a.HEADER) and m[4] == a.CMD_PUT_OBJECT]
        assert puts and all(m[3] == 9 for m in puts)
    finally:
        _teardown(rig)


def test_the_broadcast_device_id_finds_the_unit(qapp, monkeypatch):
    rig = _build(qapp, monkeypatch, FakeASRX(device=9))
    try:
        rig.controller.set_channel(0x7F)
        rig.engine.scan_empty_run = 3
        assert [s for s, _ in list_samples(rig)] == [0, 1, 2]
        assert rig.log.has("answers with device id 9")
    finally:
        _teardown(rig)


def test_no_midi_input_refuses(qapp, monkeypatch):
    rig = _build(qapp, monkeypatch, FakeASRX(), input_name=None)
    try:
        rig.controller.refresh_sample_list()
        assert rig.rec.status_has("needs a MIDI input")
        assert rig.midi.sent == []
    finally:
        _teardown(rig)


def test_a_failed_midi_send_is_reported(rig):
    rig.midi.fail_sends = True
    rig.controller.refresh_sample_list()
    assert rig.rec.status_has("Couldn't send to the ASR-X")
    assert rig.engine.idle


# --- receiving -------------------------------------------------------------------------


def test_receive_reads_the_header_then_the_audio_in_pieces(rig, tmp_path):
    list_samples(rig)
    path = tmp_path / "kick.wav"
    assert receive(rig, 0, path)
    assert rig.rec.receive_finished == [True]
    samples, rate = sds_encoder.read_wav_samples(str(path))
    assert samples == rig.fake.audio_of(0)
    assert rate == 44100 or abs(rate - 44100) <= 2
    reads = rig.fake.range_reads
    assert reads[0] == (0, 0, 52)  # the header first
    chunk = rig.engine.range_chunk_bytes
    assert all(length <= chunk for _s, _o, length in reads)
    assert reads[-1] == (0, 52, min(chunk, len(rig.fake.slots[0]) - 52))  # the re-read of the first audio piece
    assert rig.fake.commands.count((a.CMD_GET_OBJECT, 0)) == 0  # never fell back to a whole download
    assert rig.rec.sample_received == [str(path)]
    assert rig.rec.receive_progress[-1][0] == rig.rec.receive_progress[-1][1] > 0


def test_receive_uses_the_rate_in_the_pitch_word(rig, tmp_path):
    list_samples(rig)
    path = tmp_path / "pad.wav"
    assert receive(rig, 2, path)
    _samples, rate = sds_encoder.read_wav_samples(str(path))
    assert abs(rate - 22050) <= 25


def test_receive_a_long_sample_in_many_pieces(qapp, monkeypatch, tmp_path):
    fake = FakeASRX(slots={0: make_sample_object(0, "LONG", frames=20000)})
    rig = _build(qapp, monkeypatch, fake)
    try:
        rig.engine.scan_empty_run = 2
        list_samples(rig)
        assert receive(rig, 0, tmp_path / "long.wav")
        assert sds_encoder.read_wav_samples(str(tmp_path / "long.wav"))[0] == fake.audio_of(0)
        assert len(fake.range_reads) > 50
    finally:
        _teardown(rig)


def test_receive_falls_back_to_the_whole_object_when_command_04_is_never_answered(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeASRX(support_range=False))
    try:
        rig.engine.scan_empty_run = 3
        list_samples(rig)
        assert receive(rig, 1, tmp_path / "snare.wav")
        assert rig.rec.receive_finished == [True]
        assert sds_encoder.read_wav_samples(str(tmp_path / "snare.wav"))[0] == rig.fake.audio_of(1)
        assert (a.CMD_GET_OBJECT, 1) in rig.fake.commands
        assert rig.log.has("falling back to a whole-object download", logging.WARNING)
    finally:
        _teardown(rig)


def test_receive_mode_whole_never_uses_command_04(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeASRX())
    try:
        rig.engine.scan_empty_run = 3
        rig.engine.receive_mode = "whole"
        list_samples(rig)
        assert receive(rig, 0, tmp_path / "k.wav")
        assert rig.fake.range_reads == []
        assert sds_encoder.read_wav_samples(str(tmp_path / "k.wav"))[0] == rig.fake.audio_of(0)
    finally:
        _teardown(rig)


def test_receive_mode_chunked_does_not_fall_back(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeASRX(support_range=False))
    try:
        rig.engine.scan_empty_run = 3
        rig.engine.receive_mode = "chunked"
        list_samples(rig)
        receive(rig, 0, tmp_path / "k.wav")
        assert rig.rec.receive_finished == [False]
        assert (a.CMD_GET_OBJECT, 0) not in rig.fake.commands
    finally:
        _teardown(rig)


def test_a_bad_whole_object_checksum_is_kept_with_a_warning(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeASRX(support_range=False, corrupt_object_checksum=1))
    try:
        rig.engine.scan_empty_run = 3
        list_samples(rig)
        assert receive(rig, 0, tmp_path / "k.wav")
        assert rig.rec.receive_finished == [True]
        assert rig.rec.status_has("checksum did not match")
        assert rig.log.has("checksum mismatch", logging.WARNING)
    finally:
        _teardown(rig)


def test_a_dropped_piece_is_asked_for_again(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeASRX())
    try:
        rig.engine.scan_empty_run = 3
        list_samples(rig)
        rig.fake.drop_range_replies = 0
        original = rig.fake._handle_range
        state = {"n": 0}

        def flaky(data):
            state["n"] += 1
            if state["n"] == 3:
                return  # the third read (the second audio piece) is lost
            original(data)

        rig.fake._handle_range = flaky
        assert receive(rig, 1, tmp_path / "s.wav")
        assert rig.rec.receive_finished == [True]
        assert sds_encoder.read_wav_samples(str(tmp_path / "s.wav"))[0] == rig.fake.audio_of(1)
        assert rig.log.has("asking for bytes")
    finally:
        _teardown(rig)


def test_a_piece_that_never_comes_fails_after_the_retries(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeASRX())
    try:
        rig.engine.scan_empty_run = 3
        list_samples(rig)
        original = rig.fake._handle_range
        state = {"n": 0}

        def dead_after_header(data):
            state["n"] += 1
            if state["n"] >= 3:
                return
            original(data)

        rig.fake._handle_range = dead_after_header
        rig.controller.receive_samples([(1, str(tmp_path / "s.wav"))])
        assert wait_until(lambda: rig.rec.receive_finished, 20)
        assert rig.rec.receive_finished == [False]
        assert rig.rec.status_has("stopped answering")
        assert rig.log.has("last messages before", logging.WARNING)
    finally:
        _teardown(rig)


def test_an_unrepeatable_read_is_noted(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeASRX())
    try:
        rig.engine.scan_empty_run = 3
        list_samples(rig)
        # corrupt exactly the LAST read (the re-read of the first audio piece): header + n audio pieces come first
        chunks = -(-(len(rig.fake.slots[1]) - 52) // rig.engine.range_chunk_bytes)
        original = rig.fake._handle_range
        state = {"n": 0}

        def corrupt_last(data):
            state["n"] += 1
            if state["n"] == 1 + chunks + 1:
                rig.fake.corrupt_range_replies = 1
            original(data)

        rig.fake._handle_range = corrupt_last
        assert receive(rig, 1, tmp_path / "s.wav")
        assert rig.rec.status_has("second read of the first piece differed")
    finally:
        _teardown(rig)


def test_receiving_an_empty_slot_reports_it(rig, tmp_path):
    list_samples(rig)
    rig.controller.receive_samples([(40, str(tmp_path / "x.wav"))])
    assert wait_until(lambda: rig.rec.receive_finished, 20)
    assert rig.rec.receive_finished == [False]
    assert rig.rec.status_has("refresh the list") or rig.rec.status_has("empty")


def test_a_sample_sharing_another_ones_audio_is_read_from_the_parent(qapp, monkeypatch, tmp_path):
    import struct

    parent = make_sample_object(0, "PARENT", frames=800)
    child = bytearray(a.build_sample_object(1, "CHILD", [0] * 40, 44100))
    child = bytearray(child[:0x34])
    struct.pack_into(">I", child, 0, 0x34)  # header only
    struct.pack_into(">H", child, 0x1E, 0)  # shares the audio of slot 0
    fake = FakeASRX(slots={0: parent, 1: bytes(child)})
    rig = _build(qapp, monkeypatch, fake)
    try:
        rig.engine.scan_empty_run = 3
        list_samples(rig)
        assert receive(rig, 1, tmp_path / "c.wav")
        assert rig.rec.receive_finished == [True]
        assert sds_encoder.read_wav_samples(str(tmp_path / "c.wav"))[0] == fake.audio_of(0)
        assert rig.log.has("shares the audio of slot 0")
    finally:
        _teardown(rig)


def test_receive_logs_the_header_for_remote_debugging(rig, tmp_path):
    list_samples(rig)
    assert receive(rig, 0, tmp_path / "k.wav")
    assert rig.log.has("slot 0 header: ")
    assert rig.log.has("slot 0 decoded: ")
    assert rig.log.has("pitch_word=0x")
    assert rig.log.has("receive finished in")


# --- sending ---------------------------------------------------------------------------


def test_send_uploads_into_the_first_free_slot_and_verifies(rig, tmp_path):
    rig.engine.scan_empty_run = 4
    samples = triangle(1500)
    path = wav(tmp_path, "newone", samples)
    assert send(rig, [entry(path)])
    assert rig.rec.transfer_finished == [True]
    assert rig.rec.file_transferred == [path]
    assert 3 in rig.fake.slots
    parsed = a.parse_sample_object(rig.fake.slots[3])
    assert parsed.name == "newone" and parsed.samples == samples and parsed.own_slot == 3
    assert parsed.rate_hz == pytest.approx(44100, rel=1e-3)
    assert rig.fake.uploads == [(3, len(rig.fake.slots[3]), True)]
    assert rig.rec.status_has("Sent 1 file")
    assert not rig.rec.status_has("didn't read back")
    assert rig.log.has("is in slot 3 (verified)")
    assert rig.log.has("header sent: ")
    # the list was refreshed afterwards and now shows the new sample
    assert wait_until(lambda: rig.rec.slots and (3, "newone") in rig.rec.slots[-1])


def test_send_keeps_the_native_rate_in_the_pitch_word(rig, tmp_path):
    rig.engine.scan_empty_run = 4
    path = wav(tmp_path, "slow", triangle(800), rate=22050)
    assert send(rig, [entry(path)])
    assert a.parse_sample_object(rig.fake.slots[3]).rate_hz == pytest.approx(22050, rel=1e-3)


def test_send_honours_a_target_sample_rate(rig, tmp_path):
    rig.engine.scan_empty_run = 4
    path = wav(tmp_path, "down", triangle(4410), rate=44100)
    assert send(rig, [entry(path, sample_rate=22050)])
    parsed = a.parse_sample_object(rig.fake.slots[3])
    assert parsed.rate_hz == pytest.approx(22050, rel=1e-3)
    assert 2150 <= len(parsed.samples) <= 2260


def test_send_uses_the_override_name(rig, tmp_path):
    rig.engine.scan_empty_run = 4
    path = wav(tmp_path, "file", triangle(500))
    assert send(rig, [entry(path, name="My Kick")])
    assert a.parse_sample_object(rig.fake.slots[3]).name == "My Kick"


def test_a_stereo_file_becomes_two_samples(rig, tmp_path):
    rig.engine.scan_empty_run = 4
    left, right = triangle(900), [-v for v in triangle(900)]
    path = wav(tmp_path, "stereo_loop_name", left, stereo_right=right)
    assert send(rig, [entry(path)])
    first, second = a.parse_sample_object(rig.fake.slots[3]), a.parse_sample_object(rig.fake.slots[4])
    assert (first.name, second.name) == ("stereo_loop_na-L", "stereo_loop_na-R")
    assert first.samples == left and second.samples == right
    assert rig.rec.file_transferred == [path]  # one file, one completion


def test_a_mono_setting_sends_only_the_left_channel(rig, tmp_path):
    rig.engine.scan_empty_run = 4
    left, right = triangle(600), [5] * 600
    path = wav(tmp_path, "st", left, stereo_right=right)
    assert send(rig, [entry(path, mono=True)])
    assert a.parse_sample_object(rig.fake.slots[3]).samples == left
    assert 4 not in rig.fake.slots


def test_several_files_go_to_consecutive_slots(rig, tmp_path):
    rig.engine.scan_empty_run = 4
    paths = [wav(tmp_path, f"f{i}", triangle(300 + i)) for i in range(3)]
    assert send(rig, [entry(p) for p in paths])
    assert [a.parse_sample_object(rig.fake.slots[s]).name for s in (3, 4, 5)] == ["f0", "f1", "f2"]
    assert rig.rec.file_transferred == paths


def test_a_hidden_slot_is_never_overwritten(qapp, monkeypatch, tmp_path):
    hidden_obj = make_sample_object(3, "RECBUF", frames=300)
    fake = FakeASRX(hidden={3}, slots={0: make_sample_object(0, "A", frames=100), 3: hidden_obj})
    rig = _build(qapp, monkeypatch, fake)
    try:
        rig.engine.scan_empty_run = 4
        assert send(rig, [entry(wav(tmp_path, "x", triangle(500)))])
        assert fake.slots[3] == hidden_obj  # untouched
        assert a.parse_sample_object(fake.slots[1]).name == "x"  # slot 1 was free
    finally:
        _teardown(rig)


def test_a_hidden_slot_in_the_way_is_skipped(qapp, monkeypatch, tmp_path):
    hidden_obj = make_sample_object(1, "RECBUF", frames=300)
    fake = FakeASRX(hidden={1}, slots={0: make_sample_object(0, "A", frames=100), 1: hidden_obj})
    rig = _build(qapp, monkeypatch, fake)
    try:
        rig.engine.scan_empty_run = 4
        assert send(rig, [entry(wav(tmp_path, "x", triangle(500)))])
        assert fake.slots[1] == hidden_obj
        assert a.parse_sample_object(fake.slots[2]).name == "x"
        assert rig.log.has("has no name but a range read answered", logging.WARNING)
    finally:
        _teardown(rig)


def test_a_lost_ack_for_a_sample_that_arrived_is_a_success_with_a_warning(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeASRX(drop_acks=1))
    try:
        rig.engine.scan_empty_run = 4
        assert send(rig, [entry(wav(tmp_path, "x", triangle(500)))])
        assert rig.rec.transfer_finished == [True]
        assert rig.rec.status_has("didn't read back the same")
        assert rig.log.has("although no acknowledgement came", logging.WARNING)
    finally:
        _teardown(rig)


def test_memory_full_is_reported_when_nothing_arrives(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeASRX(memory_bytes=9000))
    try:
        rig.engine.scan_empty_run = 4
        path = wav(tmp_path, "big", triangle(20000))
        rig.controller.send_file_queue([entry(path)])
        assert wait_until(lambda: rig.rec.transfer_finished, 30)
        assert rig.rec.transfer_finished == [False]
        assert rig.rec.status_has("nothing arrived in slot 3")
    finally:
        _teardown(rig)


def test_a_bad_checksum_leaves_a_stored_slot_and_says_so(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeASRX())
    try:
        rig.engine.scan_empty_run = 4
        original = rig.fake._handle_put

        def damaged(data):
            data = bytearray(data)
            data[-2] = (data[-2] + 1) & 0x7F
            original(bytes(data))

        rig.fake._handle_put = damaged
        rig.controller.send_file_queue([entry(wav(tmp_path, "x", triangle(500)))])
        assert wait_until(lambda: rig.rec.transfer_finished, 30)
        # the unit stored it but sent no ack: the engine finds it by name and flags it
        assert rig.rec.transfer_finished == [True]
        assert rig.rec.status_has("didn't read back")
    finally:
        _teardown(rig)


def test_a_file_that_cannot_be_read_is_skipped(rig, tmp_path):
    rig.engine.scan_empty_run = 4
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav")
    good = wav(tmp_path, "good", triangle(400))
    assert send(rig, [entry(str(bad)), entry(good)])
    assert rig.rec.status_has("Skipping bad.wav")
    assert rig.rec.file_transferred == [good]
    assert a.parse_sample_object(rig.fake.slots[3]).name == "good"
    assert rig.rec.status_has("skipped 1")


def test_an_unsendable_rate_is_skipped_not_sent(rig, tmp_path):
    rig.engine.scan_empty_run = 4
    path = wav(tmp_path, "weird", triangle(400), rate=200)
    assert send(rig, [entry(path)])
    assert rig.fake.uploads == []
    assert rig.rec.status_has("Skipping weird.wav")


def test_the_send_never_uses_a_dangerous_command(rig, tmp_path):
    rig.engine.scan_empty_run = 4
    assert send(rig, [entry(wav(tmp_path, "x", triangle(400)))])
    commands = {m[4] for m in rig.midi.sent if m[:3] == a.HEADER}
    assert commands <= {a.CMD_PUT_OBJECT, a.CMD_READ_RANGE}
    assert rig.fake.ignored_ops == []


def test_send_needs_a_midi_input(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeASRX(), input_name=None)
    try:
        assert not rig.controller.send_file_queue([entry(wav(tmp_path, "x", triangle(400)))])
        assert rig.rec.status_has("needs a MIDI input")
    finally:
        _teardown(rig)


def test_progress_is_reported_while_a_send_waits(rig, tmp_path, monkeypatch):
    monkeypatch.setattr(at, "MIDI_BYTES_PER_SECOND", 200_000)  # a ~1.5 s estimated upload
    rig.engine.scan_empty_run = 4
    rig.engine._progress_timer.setInterval(20)
    rig.midi.paced = True
    assert send(rig, [entry(wav(tmp_path, "x", triangle(8000)))])
    assert rig.rec.unit_progress and rig.rec.unit_progress[-1] == 1.0


# --- cancel and robustness -----------------------------------------------------------------


def test_cancel_during_a_receive(rig, tmp_path):
    rig.midi.paced = True
    list_samples(rig)
    rig.controller.receive_samples([(0, str(tmp_path / "k.wav"))])
    wait_until(lambda: rig.fake.range_reads)
    rig.controller.cancel_transfer()
    assert wait_until(lambda: rig.engine.idle)
    assert rig.rec.receive_finished[-1] is False
    assert rig.rec.status_has("cancelled")


def test_cancel_during_an_upload_warns_that_the_unit_keeps_receiving(rig, tmp_path, monkeypatch):
    rig.engine.scan_empty_run = 4
    started = rig.controller.send_file_queue([entry(wav(tmp_path, "x", triangle(500)))])
    assert started
    assert wait_until(lambda: rig.engine._tx is not None and rig.engine._tx.get("uploading"))
    rig.controller.cancel_transfer()
    assert rig.rec.transfer_finished[-1] is False
    assert rig.rec.status_has("still taking in")
    assert wait_until(lambda: rig.engine.idle)


def test_unexpected_messages_are_counted_not_fatal(rig):
    rig.engine.handle_sysex(bytes((0x7E, 0, 0x06, 0x02, 1, 2, 3)))
    rig.engine.handle_sysex(a.HEADER + bytes((0, 0x42)))
    rig.engine.handle_sysex(b"\x01\x02\x03")
    assert rig.engine._unexpected >= 2
    assert rig.engine.idle


def test_a_second_operation_while_busy_is_refused(rig, tmp_path):
    rig.midi.paced = True
    rig.controller.refresh_sample_list()
    rig.controller.refresh_sample_list()
    assert rig.rec.status_has("already in progress")
    rig.engine.cancel()


def test_full_trace_logging_can_be_switched_on(qapp, monkeypatch):
    monkeypatch.setenv("AKAISDS_ASRX_TRACE", "1")
    rig = _build(qapp, monkeypatch, FakeASRX())
    try:
        assert rig.engine.trace_full
        rig.engine.scan_empty_run = 2
        list_samples(rig)
        assert rig.log.has("TRACE out")
        assert rig.log.has("TRACE in")
    finally:
        _teardown(rig)


# --- the override file a tester can be asked to create -------------------------------------------------


@pytest.fixture
def overrides_file():
    path = debug_log.LOG_PATH.parent / "asrx.json"
    yield path
    if path.exists():
        path.unlink()


def test_an_overrides_file_changes_the_settings_and_is_logged(qapp, monkeypatch, overrides_file):
    import json

    overrides_file.write_text(
        json.dumps(
            {"receive_mode": "whole", "range_chunk_bytes": 320, "verify_reread": False, "ack_margin_s": 5, "header_patch": {"0x24": 100, "0x1C": 1}, "bogus": 1}
        )
    )
    rig = _build(qapp, monkeypatch, FakeASRX())
    try:
        e = rig.engine
        assert (e.receive_mode, e.range_chunk_bytes, e.verify_reread) == ("whole", 320, False)
        assert e.header_patch == {0x24: 100, 0x1C: 1}
        assert rig.log.has("settings taken from")
    finally:
        _teardown(rig)


def test_bad_overrides_are_ignored_with_a_warning(qapp, monkeypatch, overrides_file):
    import json

    overrides_file.write_text(
        json.dumps({"receive_mode": "sideways", "scan_empty_run": "many", "send_verify": 1, "header_patch": {"0x02": 1, "0x30": 999, "zz": 1}})
    )
    rig = _build(qapp, monkeypatch, FakeASRX())
    try:
        e = rig.engine
        assert e.receive_mode == "auto" and e.scan_empty_run == 32 and e.send_verify is True and e.header_patch == {}
        assert rig.log.has("receive_mode must be", logging.WARNING) and rig.log.has("needs a int", logging.WARNING)
        assert rig.log.has("refused", logging.WARNING)
    finally:
        _teardown(rig)


def test_an_unreadable_overrides_file_is_survived(qapp, monkeypatch, overrides_file):
    overrides_file.write_text("{ not json")
    rig = _build(qapp, monkeypatch, FakeASRX())
    try:
        assert rig.engine.receive_mode == "auto"
        assert rig.log.has("couldn't read", logging.WARNING)
    finally:
        _teardown(rig)


def test_a_header_patch_goes_out_on_the_upload_and_is_logged(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeASRX())
    try:
        rig.engine.scan_empty_run = 4
        rig.engine.header_patch = {0x24: 100, 0x1C: 1}
        assert send(rig, [entry(wav(tmp_path, "x", triangle(400)))])
        stored = rig.fake.slots[3]
        assert stored[0x24] == 100 and stored[0x1C] == 1
        assert rig.log.has("header_patch applied", logging.WARNING)
    finally:
        _teardown(rig)


def test_a_header_the_unit_adjusts_is_logged_but_does_not_fail_the_verification(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeASRX())
    try:
        rig.engine.scan_empty_run = 4
        original = rig.fake._handle_put

        def adjusting(data):
            original(data)
            if 3 in rig.fake.slots:
                changed = bytearray(rig.fake.slots[3])
                changed[0x19] = 1  # the unit flags the sample
                rig.fake.slots[3] = bytes(changed)

        rig.fake._handle_put = adjusting
        assert send(rig, [entry(wav(tmp_path, "x", triangle(400)))])
        assert rig.rec.transfer_finished == [True]
        assert not rig.rec.status_has("didn't read back")
        assert rig.log.has("header differs at 1 byte", logging.WARNING)
        assert rig.log.has("+0x19: sent 00 got 01")
    finally:
        _teardown(rig)


def test_damaged_audio_on_the_unit_is_reported_as_not_read_back_the_same(qapp, monkeypatch, tmp_path):
    rig = _build(qapp, monkeypatch, FakeASRX())
    try:
        rig.engine.scan_empty_run = 4
        original = rig.fake._handle_put

        def damaging(data):
            original(data)
            if 3 in rig.fake.slots:
                damaged = bytearray(rig.fake.slots[3])
                damaged[-10] ^= 0xFF
                rig.fake.slots[3] = bytes(damaged)

        rig.fake._handle_put = damaging
        assert send(rig, [entry(wav(tmp_path, "x", triangle(400)))])
        assert rig.rec.status_has("didn't read back the same")
        assert rig.log.has("DIFFERENT")
    finally:
        _teardown(rig)
