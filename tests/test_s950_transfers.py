# tests for controller/s950_transfers.py - the S900/S950 catalog / send /
# receive / rename / info choreography.
#
# The real S950Transfers and the real SamplerController run against core/
# demo_s950.FakeS950 over a fake MidiManager that delivers replies the way the
# real one does - asynchronously, via the event loop, never from inside the
# send call. Needs a real (offscreen) QApplication for the timers. The wire
# waits are shrunk (MIDI_BYTES_PER_SECOND, every *_ms attribute) so each
# transfer takes a few milliseconds.

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from controller import s950_transfers
from controller.sampler_controller import SamplerController
from core import s950_sysex as s
from core import sds_encoder
from core.demo_s950 import FakeS950, make_sample

SOX, EOX = 0xF0, 0xF7


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Midi(QObject):
    # the MidiManager surface SamplerController/S950Transfers use, wired to a FakeS950
    sysex_received = Signal(bytes)

    def __init__(self, fake, input_name="Fake In"):
        super().__init__()
        self.fake = fake
        self.input_name = input_name
        self.output_name = "Fake Out"
        self.sent = []
        self.fail_sends = False

    def send_sysex(self, data):
        if self.fail_sends:
            raise RuntimeError("port closed")
        self.sent.append(bytes(data))
        self.fake.out.send_message([SOX, *data, EOX])
        QTimer.singleShot(0, self._deliver)

    def _deliver(self):
        while (got := self.fake.inp.get_message()) is not None:
            self.sysex_received.emit(bytes(got[0][1:-1]))


class _Recorder:
    def __init__(self, controller):
        self.statuses = []
        self.slots = []
        self.file_transferred = []
        self.transfer_finished = []
        self.receive_finished = []
        self.sample_received = []
        self.info = []
        self.transfer_progress = []
        self.receive_progress = []
        controller.status_changed.connect(self.statuses.append)
        controller.sample_slots_updated.connect(self.slots.append)
        controller.file_transferred.connect(self.file_transferred.append)
        controller.transfer_finished.connect(self.transfer_finished.append)
        controller.receive_finished.connect(self.receive_finished.append)
        controller.sample_received.connect(self.sample_received.append)
        controller.sample_info_received.connect(self.info.append)
        controller.transfer_progress.connect(lambda a, b: self.transfer_progress.append((a, b)))
        controller.receive_progress.connect(lambda a, b: self.receive_progress.append((a, b)))

    def status_has(self, text):
        return any(text in message for message in self.statuses)


def wait_until(predicate, timeout=5.0):
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
    monkeypatch.setattr(s950_transfers, "MIDI_BYTES_PER_SECOND", 5_000_000)
    rig = _Rig()
    rig.fake = fake
    rig.midi = _Midi(fake, input_name)
    rig.controller = SamplerController(rig.midi)
    rig.controller.set_device_type("akai_s900_s950")
    rig.engine = rig.controller._s950
    rig.engine.reply_timeout_ms = 500
    rig.engine.ack_interval_ms = 1
    rig.engine.nak_window_ms = 10
    rig.engine.sprm_settle_ms = 1
    rig.engine.progress_tick_ms = 5
    rig.engine.drain_floor_ms = 1
    rig.engine.dump_timeout_slack_ms = 500
    rig.rec = _Recorder(rig.controller)
    return rig


@pytest.fixture
def rig(qapp, monkeypatch):
    r = _build(qapp, monkeypatch, FakeS950())
    yield r
    r.engine.cancel()
    # a dashboard-less rig still posts deferred deletes for the timers
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def make_wav(tmp_path, name="loop.wav", frames=1000, rate=22050, stereo=False):
    path = str(tmp_path / name)
    samples = [((i * 37) % 2000) - 1000 for i in range(frames)]
    if stereo:
        sds_encoder.write_wav_file_stereo(path, samples, [-v for v in samples], rate, 16)
    else:
        sds_encoder.write_wav_file(path, samples, rate, 16)
    return path


def entry(path, **extra):
    return {"filepath": path, "name": None, "bit_depth": 16, "sample_rate": None, "mono": False, **extra}


# --- prepare_words / names (pure) ----------------------------------------------------------------


def test_prepare_words_converts_16_to_12_bit_offset_binary():
    words, rate = s950_transfers.prepare_words([[0] * 300 + [16000] * 300], 22050)
    assert rate == 22050
    assert words[0] == s.SILENCE_WORD
    assert words[-1] == s.pcm16_to_word(16000)


def test_prepare_words_folds_stereo_to_mono():
    left, right = [1000] * 300, [3000] * 300
    mixed, _ = s950_transfers.prepare_words([left, right], 22050)
    only_left, _ = s950_transfers.prepare_words([left, right], 22050, force_mono=True)
    assert mixed[0] == s.pcm16_to_word(2000)  # the average
    assert only_left[0] == s.pcm16_to_word(1000)  # "mono" means the left channel


def test_prepare_words_resamples_to_the_target_rate():
    words, rate = s950_transfers.prepare_words([[0] * 4000], 44100, target_rate=22050)
    assert rate == 22050 and len(words) == 2000


def test_prepare_words_clamps_the_rate_into_the_dump_headers_range():
    _, rate = s950_transfers.prepare_words([[0] * 1000], 22050, target_rate=1000)
    assert rate == 2000
    _, rate = s950_transfers.prepare_words([[0] * 1000], 22050, target_rate=96000)
    assert rate == 65535


def test_prepare_words_pads_short_samples_to_the_minimum():
    words, _ = s950_transfers.prepare_words([[500] * 50], 22050)
    assert len(words) == s.MIN_TOTAL_WORDS
    assert words[-1] == s.SILENCE_WORD


def test_prepare_words_refuses_rather_than_truncates_long_samples():
    with pytest.raises(ValueError, match="too long"):
        s950_transfers.prepare_words([[0] * (s.MAX_TOTAL_WORDS + 1)], 22050)


def test_unique_name_and_naming_rules():
    assert s950_transfers.sample_name_for("  my kick drum 01 ") == "MY KICK DR"
    assert s950_transfers.unique_name("kick", ["SNARE"]) == "KICK"
    assert s950_transfers.unique_name("kick", ["KICK"]) == "KICK-2"
    assert s950_transfers.unique_name("kick", ["kick", "KICK-2"]) == "KICK-3"
    # the suffix still fits in 10 characters
    assert s950_transfers.unique_name("ABCDEFGHIJ", ["ABCDEFGHIJ"]) == "ABCDEFGH-2"
    assert s950_transfers.unique_name("", []) == "SAMPLE"


# --- catalog ------------------------------------------------------------------------------------


def test_refresh_publishes_the_sparse_slot_list(rig):
    rig.fake.samples[7] = make_sample("HAT", frames=300)
    rig.controller.refresh_sample_list()
    assert wait_until(lambda: rig.rec.slots)
    assert rig.rec.slots[-1] == [(0, "KICK"), (1, "SNARE"), (2, "PAD"), (7, "HAT")]
    assert rig.engine.samples == rig.rec.slots[-1]
    assert rig.engine.programs == [(0, "DRUMS"), (1, "PAD PROG")]
    assert rig.rec.status_has("4 samples")
    assert rig.engine.busy is False


def test_silent_refresh_says_nothing_on_success(rig):
    rig.controller.refresh_sample_list(silent=True)
    assert wait_until(lambda: rig.rec.slots)
    assert rig.rec.statuses == []


def test_refresh_needs_a_midi_input(qapp, monkeypatch):
    r = _build(qapp, monkeypatch, FakeS950(), input_name=None)
    r.controller.refresh_sample_list()
    assert r.midi.sent == []
    assert r.rec.status_has("MIDI input")
    r.controller.refresh_sample_list(silent=True)  # silent: no new message
    assert len(r.rec.statuses) == 1


def test_refresh_with_no_reply_times_out_with_a_useful_message(qapp, monkeypatch):
    r = _build(qapp, monkeypatch, FakeS950(channel=9))  # we talk on channel 0
    r.engine.reply_timeout_ms = 30
    r.controller.refresh_sample_list()
    assert wait_until(lambda: r.rec.status_has("No reply"))
    assert r.rec.status_has("the sample catalog")
    assert r.rec.slots == []
    assert r.engine.busy is False
    # and the engine is usable again afterwards
    r.controller.refresh_sample_list()
    assert r.engine._op == "catalog"


def test_a_midi_send_failure_is_reported_not_raised(rig):
    rig.midi.fail_sends = True
    rig.controller.refresh_sample_list()
    assert rig.rec.status_has("Couldn't send to the S900/S950")
    assert rig.engine._op is None


def test_unrelated_or_corrupt_messages_are_ignored(rig):
    h = rig.engine.handle_sysex
    h(b"")
    h(bytes([0x47, 0, 0x0B, 0x40, 0, 0, 0x7F]))  # bad checksum
    h(bytes([0x7E, 0x02, 0x7F, 0x00]))  # a standard-SDS ACK
    h(bytes([0x42, 1, 2, 3]))  # another manufacturer
    h(s.build_handshake(s.CODE_ACKS))  # a stray ACK
    assert rig.rec.statuses == []
    assert rig.engine._op is None


# --- sending ------------------------------------------------------------------------------------


def test_send_uploads_to_the_first_empty_slot_names_it_and_refreshes(rig, tmp_path):
    path = make_wav(tmp_path, "My Loop.wav", frames=1000)
    assert rig.controller.send_file_queue([entry(path)]) is True
    assert rig.controller.is_transfer_busy() is True
    assert wait_until(lambda: rig.rec.transfer_finished)
    assert rig.rec.transfer_finished == [True]
    assert rig.rec.file_transferred == [path]
    assert rig.fake.uploads == [3]  # 0-2 are taken
    sample = rig.fake.samples[3]
    assert sample["params"].name == "MY LOOP"
    assert sample["params"].total_words == 1000
    assert sample["params"].sample_rate_hz == 22050
    assert len(sample["words"]) == 1000
    assert rig.fake.sprm_writes == 1
    # the first thing on the wire was the readiness/slot-pick catalog request
    assert rig.midi.sent[0][2] == s.FUNC_RCAT
    assert rig.rec.status_has("Sent 1 file")
    # and the post-send refresh shows it
    assert wait_until(lambda: rig.rec.slots and (3, "MY LOOP") in rig.rec.slots[-1])
    assert rig.controller.is_transfer_busy() is False
    assert rig.rec.transfer_progress  # progress was reported


def test_send_never_overwrites_and_treats_tone_as_empty(qapp, monkeypatch, tmp_path):
    fake = FakeS950(samples={0: make_sample("TONE", frames=300), 1: make_sample("A", frames=300), 3: make_sample("B", frames=300)})
    r = _build(qapp, monkeypatch, fake)
    path = make_wav(tmp_path, "x.wav")
    r.controller.send_file_queue([entry(path), entry(path, name="y")])
    assert wait_until(lambda: r.rec.transfer_finished)
    # slot 0 (the TONE placeholder) first, then the lowest remaining gap
    assert fake.uploads == [0, 2]
    assert fake.samples[1]["params"].name == "A"
    assert fake.samples[3]["params"].name == "B"


def test_send_makes_names_unique(rig, tmp_path):
    path = make_wav(tmp_path, "kick.wav")
    rig.controller.send_file_queue([entry(path)])
    assert wait_until(lambda: rig.rec.transfer_finished)
    assert rig.fake.samples[3]["params"].name == "KICK-2"  # KICK already exists


def test_send_honours_the_queue_entrys_rate_name_and_mono_settings(rig, tmp_path):
    path = make_wav(tmp_path, "st.wav", frames=2000, rate=44100, stereo=True)
    rig.controller.send_file_queue([entry(path, name="renamed", sample_rate=22050, mono=True)])
    assert wait_until(lambda: rig.rec.transfer_finished)
    sample = rig.fake.samples[3]
    assert sample["params"].name == "RENAMED"
    assert sample["params"].sample_rate_hz == 22050
    assert sample["params"].total_words == 1000


def test_send_ignores_the_starting_slot_the_dashboard_passes(rig, tmp_path):
    path = make_wav(tmp_path)
    rig.controller.send_file_queue([entry(path)], starting_sample_number=0)
    assert wait_until(lambda: rig.rec.transfer_finished)
    assert rig.fake.uploads == [3]  # never the (occupied) slot 0


def test_send_skips_unreadable_and_too_long_files_and_carries_on(rig, tmp_path, monkeypatch):
    good = make_wav(tmp_path, "good.wav", frames=500)
    long_one = make_wav(tmp_path, "long.wav", frames=1000)
    monkeypatch.setattr(s, "MAX_TOTAL_WORDS", 800)
    rig.controller.send_file_queue(
        [entry(str(tmp_path / "missing.wav")), entry(long_one), entry(good)]
    )
    assert wait_until(lambda: rig.rec.transfer_finished)
    assert rig.rec.transfer_finished == [True]
    assert rig.rec.file_transferred == [good]  # skipped ones stay in the queue
    assert rig.rec.status_has("Skipping missing.wav")
    assert rig.rec.status_has("too long")
    assert rig.rec.status_has("skipped 2")
    assert rig.fake.uploads == [3]


def test_a_nak_fails_the_transfer_and_names_nothing(rig, tmp_path):
    path = make_wav(tmp_path)
    real = rig.fake._receive_dump

    def _nak_after_receiving(data):
        real(data)
        rig.fake._send(s.build_handshake(s.CODE_NAKS))

    rig.fake._receive_dump = _nak_after_receiving
    rig.controller.send_file_queue([entry(path), entry(path)])
    assert wait_until(lambda: rig.rec.transfer_finished)
    assert rig.rec.transfer_finished == [False]  # stopped, not carried on
    assert rig.rec.status_has("NAK")
    assert rig.rec.file_transferred == []  # stays queued so it can be retried
    assert rig.fake.sprm_writes == 0  # never named
    assert len(rig.fake.uploads) == 1
    assert rig.engine.busy is False


def test_send_fails_cleanly_when_the_unit_never_answers_the_slot_check(qapp, monkeypatch, tmp_path):
    r = _build(qapp, monkeypatch, FakeS950(channel=9))
    r.engine.reply_timeout_ms = 30
    r.controller.send_file_queue([entry(make_wav(tmp_path))])
    assert wait_until(lambda: r.rec.transfer_finished)
    assert r.rec.transfer_finished == [False]
    assert r.rec.status_has("No reply")
    assert r.fake.received == [r.fake.received[0]]  # one RCAT, no dump sent blind


def test_send_fails_when_every_slot_is_taken(qapp, monkeypatch, tmp_path):
    fake = FakeS950(samples={i: make_sample(f"S{i}", frames=200) for i in range(100)})
    r = _build(qapp, monkeypatch, fake)
    r.controller.send_file_queue([entry(make_wav(tmp_path))])
    assert wait_until(lambda: r.rec.transfer_finished)
    assert r.rec.transfer_finished == [False]
    assert r.rec.status_has("occupied")
    assert fake.uploads == []


def test_send_without_an_input_is_refused(qapp, monkeypatch, tmp_path):
    r = _build(qapp, monkeypatch, FakeS950(), input_name=None)
    assert r.controller.send_file_queue([entry(make_wav(tmp_path))]) is False
    assert r.midi.sent == []
    assert r.rec.status_has("MIDI input")


def test_send_streams_the_whole_dump_as_one_message_with_a_one_shot_loop(rig, tmp_path):
    path = make_wav(tmp_path, frames=700)
    rig.controller.send_file_queue([entry(path)])
    assert wait_until(lambda: rig.rec.transfer_finished)
    dumps = [m for m in rig.midi.sent if m[:2] == bytes([0x7E, 0x01])]
    assert len(dumps) == 1
    header = s.SampleDumpHeader.from_bytes(dumps[0])
    assert (header.num, header.total_words, header.loop_start, header.loop_end) == (3, 700, 695, 699)
    assert header.bits_per_word == 12 and header.period_ns == s.hz_to_period_ns(22050)
    assert s.parse_sample_dump(dumps[0])[0] == header


# --- receiving ----------------------------------------------------------------------------------


def test_receive_saves_the_sample_as_a_wav(rig, tmp_path):
    out = str(tmp_path / "snare.wav")
    rig.controller.receive_samples([(1, out)])
    assert rig.controller.is_transfer_busy() is True
    assert wait_until(lambda: rig.rec.receive_finished)
    assert rig.rec.receive_finished == [True]
    assert rig.rec.sample_received == [out]
    samples, rate = sds_encoder.read_wav_samples(out)
    assert rate == 22050
    assert samples == [s.word_to_pcm16(w) for w in rig.fake.samples[1]["words"]]
    assert rig.fake.acks_received > 0  # the ACK pump kept the dump moving
    assert rig.rec.receive_progress and rig.rec.receive_progress[-1] == (4000, 4000)
    assert rig.rec.status_has("All samples received")
    assert rig.controller.is_transfer_busy() is False


def test_receive_a_queue_of_samples_one_after_another(rig, tmp_path):
    outs = [str(tmp_path / "a.wav"), str(tmp_path / "b.wav")]
    rig.controller.receive_samples([(0, outs[0]), (2, outs[1])])
    assert wait_until(lambda: rig.rec.receive_finished)
    assert rig.rec.sample_received == outs
    assert sds_encoder.read_wav_samples(outs[1])[1] == 44100  # the PAD sample's rate


def test_receive_skips_a_header_only_message_and_waits_for_the_dump(qapp, monkeypatch, tmp_path):
    r = _build(qapp, monkeypatch, FakeS950(split_header_envelope=True))
    out = str(tmp_path / "k.wav")
    r.controller.receive_samples([(0, out)])
    assert wait_until(lambda: r.rec.receive_finished)
    assert r.rec.receive_finished == [True]
    assert len(sds_encoder.read_wav_samples(out)[0]) == 3000


def test_receive_of_an_empty_slot_fails_without_requesting_a_dump(rig, tmp_path):
    rig.engine.reply_timeout_ms = 30
    rig.controller.receive_samples([(50, str(tmp_path / "x.wav"))])
    assert wait_until(lambda: rig.rec.receive_finished)
    assert rig.rec.receive_finished == [False]
    assert rig.rec.status_has("No reply")
    assert not any(m[:2] == bytes([0x7E, 0x00]) for m in rig.midi.sent)  # no RSD


def test_receive_reports_a_nak_to_the_dump_request(rig, tmp_path):
    rig.fake._rsd = lambda slot: rig.fake._send(s.build_handshake(s.CODE_NAKS))
    rig.controller.receive_samples([(1, str(tmp_path / "x.wav"))])
    assert wait_until(lambda: rig.rec.receive_finished)
    assert rig.rec.receive_finished == [False]
    assert rig.rec.status_has("refused")


def test_receive_that_never_completes_times_out_and_stops_pumping(qapp, monkeypatch, tmp_path):
    r = _build(qapp, monkeypatch, FakeS950(dump_acks_required=10**9))
    r.engine.dump_timeout_slack_ms = 40
    r.controller.receive_samples([(1, str(tmp_path / "x.wav"))])
    assert wait_until(lambda: r.rec.receive_finished)
    assert r.rec.receive_finished == [False]
    assert r.rec.status_has("Timed out")
    sent = len(r.midi.sent)
    wait_until(lambda: False, timeout=0.05)
    assert len(r.midi.sent) == sent  # the pump stopped with the failure


def test_receive_rejects_a_dump_for_the_wrong_slot(rig, tmp_path):
    real = rig.fake._rsd

    def _wrong_slot(slot):
        real(slot)
        # re-address whatever was queued to a different slot
        rig.fake._pending_dump["frame"][3] = 9

    rig.fake._rsd = _wrong_slot
    rig.controller.receive_samples([(1, str(tmp_path / "x.wav"))])
    assert wait_until(lambda: rig.rec.receive_finished)
    assert rig.rec.receive_finished == [False]
    assert rig.rec.status_has("slot 9")


def test_receive_into_an_unwritable_path_fails_cleanly(rig, tmp_path):
    rig.controller.receive_samples([(1, str(tmp_path / "no" / "such" / "dir" / "x.wav"))])
    assert wait_until(lambda: rig.rec.receive_finished)
    assert rig.rec.receive_finished == [False]
    assert rig.engine.busy is False


# --- cancel / one thing at a time -----------------------------------------------------------------


def test_cancelling_a_receive_aborts_the_dump_and_stops_the_pump(qapp, monkeypatch, tmp_path):
    r = _build(qapp, monkeypatch, FakeS950(dump_acks_required=10**9))
    r.controller.receive_samples([(1, str(tmp_path / "x.wav")), (0, str(tmp_path / "y.wav"))])
    assert wait_until(lambda: r.fake.acks_received >= 3)
    r.controller.cancel_transfer()
    assert r.rec.receive_finished == [False]
    assert r.rec.status_has("cancelled")
    assert r.midi.sent[-1] == bytes([0x7E, s.CODE_ASD])  # told the unit to stop
    assert r.controller.is_transfer_busy() is False
    sent = len(r.midi.sent)
    wait_until(lambda: False, timeout=0.05)
    assert len(r.midi.sent) == sent  # no more ACKs, and the second sample never starts


def test_cancelling_during_the_upload_wait_says_the_unit_may_still_be_receiving(rig, tmp_path, monkeypatch):
    monkeypatch.setattr(s950_transfers, "MIDI_BYTES_PER_SECOND", 1000)  # a long wire wait
    rig.controller.send_file_queue([entry(make_wav(tmp_path))])
    assert wait_until(lambda: any(m[:2] == bytes([0x7E, 0x01]) for m in rig.midi.sent))
    rig.controller.cancel_transfer()
    assert rig.rec.transfer_finished == [False]
    assert rig.rec.status_has("may still be receiving")
    assert rig.engine.busy is False
    wait_until(lambda: False, timeout=0.05)
    assert rig.fake.sprm_writes == 0  # the naming step never ran


def test_cancel_with_nothing_running_is_a_no_op(rig):
    assert rig.engine.cancel() is False
    rig.controller.cancel_transfer()
    assert rig.rec.statuses == []


def test_only_one_operation_at_a_time(qapp, monkeypatch, tmp_path):
    r = _build(qapp, monkeypatch, FakeS950(dump_acks_required=10**9))
    r.controller.receive_samples([(1, str(tmp_path / "x.wav"))])
    assert wait_until(lambda: r.engine._phase == "dump")
    before = len(r.midi.sent)
    r.controller.refresh_sample_list()  # ignored
    assert r.controller.send_file_queue([entry(make_wav(tmp_path))]) is False
    r.controller.receive_samples([(0, str(tmp_path / "y.wav"))])
    r.controller.rename_sample(1, "NEW")
    r.controller.request_sample_info(1)
    assert r.rec.statuses.count(
        "A transfer is already in progress - please wait for it to finish"
    ) >= 3
    assert not any(m[2:3] == bytes([s.FUNC_RSPRM]) or m[:2] == bytes([0x7E, 0x01]) for m in r.midi.sent[before:])
    r.controller.cancel_transfer()


# --- rename / info / delete ------------------------------------------------------------------------


def test_rename_rewrites_the_name_and_refreshes_the_list(rig):
    rig.controller.refresh_sample_list(silent=True)
    assert wait_until(lambda: rig.rec.slots)
    rig.controller.rename_sample(1, "snare 2")
    assert wait_until(lambda: len(rig.rec.slots) >= 2)
    assert rig.fake.samples[1]["params"].name == "SNARE 2"
    assert (1, "SNARE 2") in rig.rec.slots[-1]
    assert rig.rec.status_has("Renamed sample 1")
    assert rig.fake.sprm_writes == 1


def test_rename_keeps_every_other_sprm_field(rig):
    before = rig.fake.samples[2]["params"]
    keep = (before.total_words, before.sample_rate_hz, before.end, before.nominal_pitch)
    rig.controller.rename_sample(2, "pad two")
    assert wait_until(lambda: rig.fake.sprm_writes == 1)
    after = rig.fake.samples[2]["params"]
    assert (after.total_words, after.sample_rate_hz, after.end, after.nominal_pitch) == keep


def test_rename_to_a_name_another_sample_has_is_refused(rig):
    rig.controller.refresh_sample_list(silent=True)
    assert wait_until(lambda: rig.rec.slots)
    sent = len(rig.midi.sent)
    rig.controller.rename_sample(1, "kick")
    assert rig.rec.status_has("already called 'KICK'")
    assert len(rig.midi.sent) == sent and rig.fake.sprm_writes == 0
    # renaming a sample to (a case change of) its OWN name is fine
    rig.controller.rename_sample(0, "kick")
    assert wait_until(lambda: rig.fake.sprm_writes == 1)


def test_rename_to_nothing_is_refused(rig):
    rig.controller.rename_sample(1, "   ")
    assert rig.rec.status_has("can't be empty")
    assert rig.midi.sent == []


def test_sample_info_reports_what_the_unit_knows_and_no_guessed_pitch(rig):
    rig.controller.request_sample_info(1)
    assert wait_until(lambda: rig.rec.info)
    info = rig.rec.info[0]
    assert info["name"] == "SNARE"
    assert info["sample_number"] == 1
    assert info["bit_depth"] == 12
    assert info["sample_rate"] == 22050
    assert info["sample_length"] == 4000
    assert info["root_key"] is None and info["detune"] is None
    assert rig.engine.busy is False


def test_delete_is_refused_on_the_wire(rig):
    rig.controller.delete_sample(1)
    assert rig.midi.sent == []
    assert rig.rec.status_has("front panel")


# --- program read (Stage 4) ----------------------------------------------------------------------


class _ProgramRecorder:
    def __init__(self, controller):
        self.received = []
        self.listed = []
        controller.s950_program_received.connect(lambda slot, p: self.received.append((slot, p)))
        controller.program_slots_updated.connect(self.listed.append)


def test_request_program_reads_a_whole_program(rig):
    rec = _ProgramRecorder(rig.controller)
    rig.controller.request_program(0)
    assert rig.controller.is_transfer_busy() is True  # a read blocks other windows' MIDI use
    assert wait_until(lambda: rec.received)
    slot, program = rec.received[0]
    assert slot == 0 and program.name == "DRUMS"
    assert [(k.lower_key, k.upper_key, k.soft_sample) for k in program.keygroups] == [
        (24, 59, "KICK"),
        (60, 127, "SNARE"),
    ]
    assert rig.midi.sent[-1][2] == s.FUNC_RPRGM
    assert rig.controller.is_transfer_busy() is False
    assert rig.engine.idle


def test_request_program_for_an_empty_slot_times_out_with_none(rig):
    rec = _ProgramRecorder(rig.controller)
    rig.engine.reply_timeout_ms = 30
    rig.controller.request_program(9)  # the fake stays silent for an empty slot
    assert wait_until(lambda: rec.received)
    assert rec.received == [(9, None)]
    assert rig.rec.status_has("No reply")
    assert rig.rec.status_has("program 9")
    assert rig.engine.idle


def test_request_program_while_busy_answers_none_and_leaves_the_running_op_alone(rig):
    rec = _ProgramRecorder(rig.controller)
    rig.controller.refresh_sample_list()
    assert rig.engine._op == "catalog"
    rig.controller.request_program(0)
    assert rec.received == [(0, None)]
    assert rig.engine._op == "catalog"
    assert wait_until(lambda: rig.rec.slots)  # the catalog read still completes


def test_request_program_without_an_input_answers_none(qapp, monkeypatch):
    r = _build(qapp, monkeypatch, FakeS950(), input_name=None)
    rec = _ProgramRecorder(r.controller)
    r.controller.request_program(0)
    assert rec.received == [(0, None)]
    assert r.midi.sent == []


def test_an_unreadable_program_reports_none_and_logs_the_bytes(rig, monkeypatch):
    rec = _ProgramRecorder(rig.controller)
    rig.fake.programs[5] = rig.fake.programs[0]
    monkeypatch.setattr(
        type(rig.fake.programs[5]), "to_payload", lambda self: b"\x00" * 10
    )
    rig.controller.request_program(5)
    assert wait_until(lambda: rec.received)
    assert rec.received == [(5, None)]
    assert rig.rec.status_has("Couldn't read program 5")
    assert rig.engine.idle


def test_cancelling_a_program_read_answers_none(rig):
    rec = _ProgramRecorder(rig.controller)
    rig.controller.request_program(0)
    assert rig.engine.cancel() is True
    assert rec.received == [(0, None)]
    assert rig.engine.idle


def test_catalog_refresh_also_publishes_the_programs(rig):
    rec = _ProgramRecorder(rig.controller)
    rig.controller.refresh_sample_list()
    assert wait_until(lambda: rec.listed)
    assert rec.listed[-1] == [(0, "DRUMS"), (1, "PAD PROG")]


def test_is_s950_idle_is_stricter_than_is_transfer_busy(rig):
    assert rig.controller.is_s950_idle()
    rig.controller.refresh_sample_list()
    assert rig.controller.is_transfer_busy() is False  # a catalog read isn't a "transfer"...
    assert rig.controller.is_s950_idle() is False  # ...but it owns the wire
    assert wait_until(lambda: rig.rec.slots)
    assert rig.controller.is_s950_idle()


# --- program write (Stage 5) ---------------------------------------------------------------------

import dataclasses

from core import s950_params


class _WriteRecorder:
    def __init__(self, controller):
        self.results = []
        controller.s950_program_written.connect(
            lambda slot, ok, program, message: self.results.append((slot, ok, program, message))
        )


def _edit(program, **keygroup0):
    kgs = [dataclasses.replace(kg) for kg in program.keygroups]
    kgs[0] = dataclasses.replace(kgs[0], **keygroup0)
    return dataclasses.replace(program, keygroups=kgs)


@pytest.fixture
def wrig(rig, tmp_path):
    rig.engine.backup_dir = tmp_path / "backups"
    rig.writes = _WriteRecorder(rig.controller)
    return rig


def test_writing_an_unchanged_program_verifies_and_backs_up_the_original(wrig):
    original = wrig.fake.programs[0]
    before = original.to_payload()
    wrig.controller.write_program(0, original, original)
    assert wait_until(lambda: wrig.writes.results)
    slot, ok, held, message = wrig.writes.results[0]
    assert (slot, ok) == (0, True) and "verified" in message
    assert held == original
    assert wrig.fake.prgm_writes == 1
    assert wrig.fake.programs[0].to_payload() == before  # byte-identical no-op
    backups = list((wrig.engine.backup_dir).glob("prog00-DRUMS-*.syx"))
    assert len(backups) == 1
    raw = backups[0].read_bytes()
    assert raw[0] == 0xF0 and raw[-1] == 0xF7
    assert s.parse_akai(raw[1:-1]).payload == before  # the exact original PRGM payload
    # read, write, read back - in that order
    assert [m[2] for m in wrig.midi.sent] == [s.FUNC_RPRGM, s.FUNC_PRGM, s.FUNC_RPRGM]
    assert wrig.engine.idle


def test_a_write_changes_only_the_edited_fields(wrig):
    original = wrig.fake.programs[0]
    edited = _edit(original, attack=12, soft_transpose=16)
    edited = dataclasses.replace(edited, key_tilt=-5)
    wrig.controller.write_program(0, original, edited)
    assert wait_until(lambda: wrig.writes.results)
    assert wrig.writes.results[0][1] is True
    stored = wrig.fake.programs[0]
    assert stored.key_tilt == -5
    assert stored.keygroups[0].attack == 12 and stored.keygroups[0].soft_transpose == 16
    # everything else - the second keygroup included - is untouched
    assert stored.keygroups[1] == original.keygroups[1]
    assert dataclasses.replace(stored.keygroups[0], attack=0, soft_transpose=0) == dataclasses.replace(
        original.keygroups[0], attack=0, soft_transpose=0
    )


def test_unmodelled_bytes_survive_a_write(wrig):
    original = wrig.fake.programs[0]
    junk = bytearray(original.keygroups[0].raw)
    junk[112:116] = bytes([1, 2, 3, 4])  # reserved 112..127: never modelled
    original.keygroups[0] = dataclasses.replace(original.keygroups[0], raw=bytes(junk))
    hdr = bytearray(original.header_raw)
    hdr[60:64] = bytes([9, 8, 7, 6])
    original.header_raw = bytes(hdr)
    wrig.controller.write_program(0, original, _edit(original, decay=5))
    assert wait_until(lambda: wrig.writes.results)
    assert wrig.writes.results[0][1] is True
    stored = wrig.fake.programs[0]
    assert stored.keygroups[0].raw[112:116] == bytes([1, 2, 3, 4])
    assert stored.header_raw[60:64] == bytes([9, 8, 7, 6])


def test_a_front_panel_edit_made_after_loading_is_not_clobbered(wrig):
    loaded = dataclasses.replace(wrig.fake.programs[0], keygroups=[dataclasses.replace(k) for k in wrig.fake.programs[0].keygroups])
    # someone turns the attack knob on the unit after we loaded the program...
    wrig.fake.programs[0] = _edit(wrig.fake.programs[0], attack=33)
    # ...and we only change the decay
    wrig.controller.write_program(0, loaded, _edit(loaded, decay=7))
    assert wait_until(lambda: wrig.writes.results)
    stored = wrig.fake.programs[0].keygroups[0]
    assert stored.decay == 7 and stored.attack == 33


def test_out_of_range_values_are_refused_before_anything_is_sent(wrig):
    original = wrig.fake.programs[0]
    wrig.controller.write_program(0, original, _edit(original, attack=100))
    assert wrig.writes.results[0][1:3] == (False, None)
    assert "attack must be 0..99" in wrig.writes.results[0][3]
    assert wrig.midi.sent == []
    assert wrig.engine.idle


def test_an_inverted_key_range_is_refused(wrig):
    original = wrig.fake.programs[0]
    wrig.controller.write_program(0, original, _edit(original, lower_key=100, upper_key=30))
    assert wrig.writes.results[0][1] is False
    assert "lower key is above" in wrig.writes.results[0][3]
    assert wrig.midi.sent == []


def test_changing_the_keygroup_count_is_refused(wrig):
    original = wrig.fake.programs[0]
    fewer = dataclasses.replace(original, keygroups=original.keygroups[:1])
    wrig.controller.write_program(0, original, fewer)
    assert "number of keygroups" in wrig.writes.results[0][3]
    assert wrig.midi.sent == []


def test_a_value_the_unit_already_holds_out_of_range_may_stay(wrig):
    original = wrig.fake.programs[0]
    odd = _edit(original, filter_key_track=120)  # beyond our assumed 0..99
    wrig.fake.programs[0] = odd
    wrig.controller.write_program(0, odd, _edit(odd, attack=3))
    assert wait_until(lambda: wrig.writes.results)
    assert wrig.writes.results[0][1] is True
    assert wrig.fake.programs[0].keygroups[0].filter_key_track == 120


def test_writing_to_an_empty_slot_never_creates_a_program(wrig):
    wrig.engine.reply_timeout_ms = 30
    original = wrig.fake.programs[0]
    wrig.controller.write_program(9, original, _edit(original, attack=1))
    assert wait_until(lambda: wrig.writes.results)
    assert wrig.writes.results[0][1:3] == (False, None)
    assert "No reply" in wrig.writes.results[0][3]
    assert wrig.fake.prgm_writes == 0 and 9 not in wrig.fake.programs


def test_no_backup_means_no_write(wrig, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    wrig.engine.backup_dir = blocker  # mkdir will fail
    original = wrig.fake.programs[0]
    wrig.controller.write_program(0, original, _edit(original, attack=1))
    assert wait_until(lambda: wrig.writes.results)
    assert wrig.writes.results[0][1] is False
    assert "backup" in wrig.writes.results[0][3]
    assert wrig.fake.prgm_writes == 0


def test_a_nak_during_the_write_fails_it_and_names_the_backup(wrig):
    real = wrig.fake._prgm

    def nak_then_store(slot, payload):
        real(slot, payload)
        wrig.fake._send(s.build_handshake(s.CODE_NAKS))

    wrig.fake._prgm = nak_then_store
    original = wrig.fake.programs[0]
    wrig.controller.write_program(0, original, _edit(original, attack=1))
    assert wait_until(lambda: wrig.writes.results)
    _, ok, held, message = wrig.writes.results[0]
    assert ok is False and held is None
    assert "NAK" in message and ".syx" in message


def test_a_readback_that_differs_is_reported_with_what_the_unit_holds(wrig):
    real = wrig.fake._prgm

    def store_but_clamp(slot, payload):
        real(slot, payload)
        kg = wrig.fake.programs[slot].keygroups[0]
        wrig.fake.programs[slot].keygroups[0] = dataclasses.replace(kg, attack=kg.attack - 1)

    wrig.fake._prgm = store_but_clamp
    original = wrig.fake.programs[0]
    wrig.controller.write_program(0, original, _edit(original, attack=20))
    assert wait_until(lambda: wrig.writes.results)
    _, ok, held, message = wrig.writes.results[0]
    assert ok is False
    assert held is not None and held.keygroups[0].attack == 19  # what the unit says now
    assert "different values" in message and "attack" in message


def test_a_write_while_busy_is_refused_and_leaves_the_running_op_alone(wrig):
    wrig.controller.refresh_sample_list()
    original = wrig.fake.programs[0]
    wrig.controller.write_program(0, original, _edit(original, attack=1))
    assert wrig.writes.results[0][1:3] == (False, None)
    assert wrig.engine._op == "catalog"
    assert wait_until(lambda: wrig.rec.slots)
    assert wrig.fake.prgm_writes == 0


def test_a_write_counts_as_a_transfer_and_can_be_cancelled(wrig):
    original = wrig.fake.programs[0]
    wrig.controller.write_program(0, original, _edit(original, attack=1))
    assert wrig.controller.is_transfer_busy() is True
    assert wrig.engine.cancel() is True
    assert wrig.writes.results[0][1] is False and "nothing was written" in wrig.writes.results[0][3]
    assert wrig.engine.idle


def test_writing_needs_a_midi_input(qapp, monkeypatch, tmp_path):
    r = _build(qapp, monkeypatch, FakeS950(), input_name=None)
    r.engine.backup_dir = tmp_path
    rec = _WriteRecorder(r.controller)
    original = r.fake.programs[0]
    r.controller.write_program(0, original, _edit(original, attack=1))
    assert rec.results[0][1:3] == (False, None)
    assert r.midi.sent == []


# --- sample parameter read (Samples tab) ---------------------------------------------------------


class _SampleParamsRecorder:
    def __init__(self, controller):
        self.received = []
        controller.s950_sample_params_received.connect(lambda slot, p: self.received.append((slot, p)))


def test_request_sample_params_reads_a_samples_sprm(rig):
    rec = _SampleParamsRecorder(rig.controller)
    rig.controller.request_sample_params(1)
    assert rig.controller.is_transfer_busy() is True
    assert wait_until(lambda: rec.received)
    slot, params = rec.received[0]
    assert slot == 1 and params.name == "SNARE"
    assert params.sample_rate_hz == 22050 and params.total_words == 4000
    assert rig.midi.sent[-1][2] == s.FUNC_RSPRM
    assert rig.engine.idle
    assert rig.rec.statuses == []  # a background read says nothing


def test_request_sample_params_for_an_empty_slot_times_out_with_none(rig):
    rec = _SampleParamsRecorder(rig.controller)
    rig.engine.reply_timeout_ms = 30
    rig.controller.request_sample_params(60)
    assert wait_until(lambda: rec.received)
    assert rec.received == [(60, None)]
    assert rig.engine.idle


def test_request_sample_params_while_busy_answers_none_and_leaves_the_op_alone(rig):
    rec = _SampleParamsRecorder(rig.controller)
    rig.controller.refresh_sample_list()
    rig.controller.request_sample_params(0)
    assert rec.received == [(0, None)]
    assert rig.engine._op == "catalog"
    assert wait_until(lambda: rig.rec.slots)


def test_request_sample_params_without_an_input_answers_none(qapp, monkeypatch):
    r = _build(qapp, monkeypatch, FakeS950(), input_name=None)
    rec = _SampleParamsRecorder(r.controller)
    r.controller.request_sample_params(0)
    assert rec.received == [(0, None)]
    assert r.midi.sent == []


def test_cancelling_a_sample_params_read_answers_none(rig):
    rec = _SampleParamsRecorder(rig.controller)
    rig.controller.request_sample_params(0)
    assert rig.engine.cancel() is True
    assert rec.received == [(0, None)]
    assert rig.engine.idle


def test_an_unreadable_sprm_answers_none(rig, monkeypatch):
    rec = _SampleParamsRecorder(rig.controller)
    monkeypatch.setattr(type(rig.fake.samples[0]["params"]), "to_payload", lambda self: b"\x00" * 10)
    rig.controller.request_sample_params(0)
    assert wait_until(lambda: rec.received)
    assert rec.received == [(0, None)]
    assert rig.engine.idle
