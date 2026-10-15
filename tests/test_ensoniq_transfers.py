# tests for controller/ensoniq_transfers.py - the Ensoniq list and receive choreography.
#
# The real EnsoniqTransfers and the real SamplerController run against core/demo_ensoniq.FakeEnsoniq over a fake MidiManager that delivers
# replies the way the real one does - asynchronously, via the event loop, never from inside the send call. Needs a real (offscreen)
# QApplication for the timers. The fake is built from the same reading of the spec as the engine, so this checks the choreography, not
# that a real Ensoniq agrees.

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from controller import ensoniq_transfers as et
from controller.sampler_controller import SamplerController
from core import ensoniq_sysex as e
from core import sds_encoder
from core.demo_ensoniq import FakeEnsoniq, make_instrument, make_layer, make_wavesample

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
        #: replies arrive ONE message per event-loop turn, so a transfer is really in flight for a while (cancel tests)
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
        self.receive_finished = []
        self.sample_received = []
        self.receive_progress = []
        controller.status_changed.connect(self.statuses.append)
        controller.sample_slots_updated.connect(self.slots.append)
        controller.receive_finished.connect(self.receive_finished.append)
        controller.sample_received.connect(self.sample_received.append)
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
    monkeypatch.setattr(et, "MIDI_BYTES_PER_SECOND", 5_000_000)
    rig = _Rig()
    rig.fake = fake
    rig.midi = _Midi(fake, input_name)
    rig.controller = SamplerController(rig.midi)
    rig.controller.set_device_type("ensoniq_eps_asr")
    rig.engine = rig.controller._ensoniq
    rig.engine.reply_timeout_ms = 500
    rig.rec = _Recorder(rig.controller)
    return rig


@pytest.fixture
def rig(qapp, monkeypatch):
    r = _build(qapp, monkeypatch, FakeEnsoniq())
    yield r
    r.engine.cancel()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def list_samples(rig):
    rig.controller.refresh_sample_list()
    assert wait_until(lambda: rig.engine.idle), rig.rec.statuses
    return rig.rec.slots[-1] if rig.rec.slots else None


def receive(rig, number, path):
    rig.controller.receive_samples([(number, str(path))])
    return wait_until(lambda: rig.rec.receive_finished, 10)


def wav_samples(path):
    samples, _rate = sds_encoder.read_wav_samples(str(path))
    return samples


def wav_rate(path):
    return sds_encoder.read_wav_samples(str(path))[1]


KICK = et.pack_number(0, 0, 1)
SNARE = et.pack_number(0, 0, 2)
PAD = et.pack_number(2, 0, 1)


def kick_samples(rig):
    return rig.fake.instruments[0]["layers"][0]["wavesamples"][1]["samples"]


# --- numbering -----------------------------------------------------------------


def test_numbers_pack_and_unpack():
    for key in [(0, 0, 1), (7, 7, 127), (2, 3, 55), (0, 7, 1)]:
        assert et.unpack_number(et.pack_number(*key)) == key
    assert len({et.pack_number(i, l, w) for i in range(8) for l in range(8) for w in (1, 2, 127)}) == 8 * 8 * 3


def test_wavesample_numbers_come_from_the_layer_key_map():
    block = [0] * e.LAYER_BLOCK_WORDS
    block[19] = 5
    block[20] = 5
    block[60] = 2
    block[106] = 127
    block[18] = 99  # not in the map
    assert et.wavesample_numbers_in_layer(block) == [2, 5, 127]


# --- the list ------------------------------------------------------------------


def test_the_list_probes_instruments_layers_and_wavesamples(rig):
    slots = list_samples(rig)
    assert slots == [(KICK, "KICK"), (SNARE, "SNARE"), (PAD, "PAD C3")]
    assert rig.rec.status_has("3 samples on the Ensoniq")
    assert rig.engine.instruments == {0: "DRUMS", 2: "PAD"}
    kick = rig.engine.entries[0]
    assert (kick["rate_code"], kick["length"]) == (20, 3000)


def test_the_list_sends_only_requests_and_acks(rig):
    list_samples(rig)
    commands = set()
    for data in rig.midi.sent:
        commands.add(e.parse_message(data).command)
    assert commands == {e.CMD_GET_INSTRUMENT, e.CMD_GET_LAYER, e.CMD_GET_WAVESAMPLE_PARAMETERS, e.CMD_RESPONSE}
    assert not commands & e.NEVER_SEND


def test_every_instrument_is_probed_and_absent_ones_are_not_errors(rig):
    list_samples(rig)
    probed = [
        e.decode_word12(m.body[0], m.body[1])
        for m in (e.parse_message(d) for d in rig.midi.sent)
        if m.command == e.CMD_GET_INSTRUMENT
    ]
    assert probed == list(range(8))
    assert not rig.rec.status_has("answered")


def test_an_empty_unit_lists_nothing(qapp, monkeypatch):
    r = _build(qapp, monkeypatch, FakeEnsoniq(instruments={}))
    assert list_samples(r) == []
    assert r.rec.status_has("0 samples")


def test_same_named_wavesamples_are_told_apart_by_where_they_live(qapp, monkeypatch):
    fake = FakeEnsoniq(
        instruments={
            0: make_instrument("A", {0: make_layer("L", {1: make_wavesample("HAT", frames=300)})}),
            1: make_instrument("B", {2: make_layer("L", {7: make_wavesample("hat", frames=300)})}),
        }
    )
    r = _build(qapp, monkeypatch, fake)
    labels = [label for _n, label in list_samples(r)]
    assert labels == ["HAT (1.1.1)", "hat (2.3.7)"]


def test_the_list_follows_the_instruments_layers_depth_first(qapp, monkeypatch):
    fake = FakeEnsoniq(
        instruments={
            3: make_instrument(
                "X",
                {
                    1: make_layer("L2", {4: make_wavesample("B", frames=200, key_range=(60, 127))}),
                    0: make_layer("L1", {2: make_wavesample("A", frames=200, key_range=(0, 59))}),
                },
            )
        }
    )
    r = _build(qapp, monkeypatch, fake)
    assert [label for _n, label in list_samples(r)] == ["A", "B"]


def test_a_silent_unit_fails_with_a_helpful_message(qapp, monkeypatch):
    r = _build(qapp, monkeypatch, FakeEnsoniq(channel=5))
    r.engine.reply_timeout_ms = 60
    r.controller.refresh_sample_list()
    assert wait_until(lambda: r.rec.status_has("No reply from the Ensoniq"))
    assert "SysEx is enabled" in r.rec.statuses[-1]
    assert r.engine.idle and r.rec.slots == []


def test_a_silent_refresh_says_nothing_when_it_fails(qapp, monkeypatch):
    r = _build(qapp, monkeypatch, FakeEnsoniq(channel=5))
    r.engine.reply_timeout_ms = 60
    r.controller.refresh_sample_list(silent=True)
    assert wait_until(lambda: r.engine.idle)
    assert r.rec.statuses == []


def test_the_wait_status_is_acked_and_the_conversation_carries_on(qapp, monkeypatch):
    r = _build(qapp, monkeypatch, FakeEnsoniq(wait_on_first_get=True))
    assert list_samples(r) == [(KICK, "KICK"), (SNARE, "SNARE"), (PAD, "PAD C3")]


def test_a_unit_that_skips_the_initial_ack_still_works(qapp, monkeypatch):
    r = _build(qapp, monkeypatch, FakeEnsoniq(skip_initial_ack=True))
    assert len(list_samples(r)) == 3


def test_a_busy_engine_refuses_a_second_operation(rig):
    rig.controller.refresh_sample_list()
    rig.controller.refresh_sample_list()
    assert rig.rec.status_has("already in progress")
    assert wait_until(lambda: rig.engine.idle)
    assert len(rig.rec.slots) == 1


# --- guards ---------------------------------------------------------------------


def test_no_midi_input_refuses_with_a_reason(qapp, monkeypatch):
    r = _build(qapp, monkeypatch, FakeEnsoniq(), input_name=None)
    r.controller.refresh_sample_list()
    r.controller.receive_samples([(KICK, "x.wav")])
    assert r.midi.sent == []
    assert sum("needs a MIDI input" in s for s in r.rec.statuses) == 2


def test_a_device_id_above_15_refuses_because_the_byte_is_a_channel(rig):
    rig.controller.set_channel(20)
    rig.controller.refresh_sample_list()
    assert rig.midi.sent == []
    assert rig.rec.status_has("0-15")


def test_the_device_id_is_the_wire_channel(qapp, monkeypatch):
    r = _build(qapp, monkeypatch, FakeEnsoniq(channel=4))
    r.controller.set_channel(4)
    assert len(list_samples(r)) == 3
    assert {d[2] for d in r.midi.sent} == {4}


def test_a_failing_send_fails_the_operation(rig):
    rig.midi.fail_sends = True
    rig.controller.refresh_sample_list()
    assert rig.rec.status_has("Couldn't send to the Ensoniq")
    assert rig.engine.idle


# --- receiving --------------------------------------------------------------------


def test_receive_saves_the_audio_between_start_and_end(rig, tmp_path):
    list_samples(rig)
    path = tmp_path / "KICK.wav"
    assert receive(rig, KICK, path)
    assert rig.rec.receive_finished == [True]
    assert rig.rec.sample_received == [str(path)]
    assert wav_samples(path) == kick_samples(rig)
    assert wav_rate(path) == 31250  # rate code 20
    assert rig.rec.status_has("All samples received")


def test_receive_uses_the_rate_code_for_the_sample_rate(rig, tmp_path):
    list_samples(rig)
    path = tmp_path / "PAD.wav"
    assert receive(rig, PAD, path)
    assert wav_rate(path) == round(e.rate_code_to_hz(14))


def test_receive_in_several_chunks_joins_them_exactly(rig, tmp_path):
    rig.engine.chunk_words = 700
    list_samples(rig)
    path = tmp_path / "KICK.wav"
    assert receive(rig, KICK, path)
    assert wav_samples(path) == kick_samples(rig)
    data_requests = [
        m for m in (e.parse_message(d) for d in rig.midi.sent) if m.command == e.CMD_GET_WAVESAMPLE_DATA
    ]
    assert len(data_requests) == 5  # 3000 words in chunks of 700
    assert rig.rec.receive_progress[-1] == (3000, 3000)


def test_receive_progress_counts_words(rig, tmp_path):
    rig.engine.chunk_words = 1000
    list_samples(rig)
    assert receive(rig, KICK, tmp_path / "k.wav")
    assert rig.rec.receive_progress == [(1000, 3000), (2000, 3000), (3000, 3000), (3000, 3000)]


def test_receive_several_samples_in_order(rig, tmp_path):
    list_samples(rig)
    rig.controller.receive_samples([(KICK, str(tmp_path / "a.wav")), (SNARE, str(tmp_path / "b.wav")), (PAD, str(tmp_path / "c.wav"))])
    assert wait_until(lambda: rig.rec.receive_finished, 10)
    assert rig.rec.receive_finished == [True]
    assert [os.path.basename(p) for p in rig.rec.sample_received] == ["a.wav", "b.wav", "c.wav"]
    assert len(wav_samples(tmp_path / "c.wav")) == 9000


@pytest.mark.parametrize("chunk", [4096, 700, 1])
def test_an_inclusive_unit_gives_exactly_the_same_audio(qapp, monkeypatch, tmp_path, chunk):
    # the spec doesn't say whether a data range's end is inclusive; the engine copes with both
    r = _build(qapp, monkeypatch, FakeEnsoniq(inclusive_end=True))
    r.engine.chunk_words = chunk
    ws = r.fake.instruments[0]["layers"][0]["wavesamples"][2]
    ws["samples"] = ws["samples"][:40]
    ws["params"].end = 40
    list_samples(r)
    assert receive(r, SNARE, tmp_path / "s.wav")
    assert wav_samples(tmp_path / "s.wav") == ws["samples"]


def test_a_final_chunk_the_unit_refuses_is_asked_for_again_one_word_shorter(qapp, monkeypatch, tmp_path):
    # the parameter block says end = number of words, but the unit's data ranges are inclusive: the last chunk reaches one word past the
    # data and is refused (0A), then asked for once more one word shorter, and every word still arrives
    fake = FakeEnsoniq()
    r = _build(qapp, monkeypatch, fake)
    list_samples(r)
    original = fake._get_wavesample_data

    def inclusive_data(message):
        fake.inclusive_end = True
        try:
            original(message)
        finally:
            fake.inclusive_end = False

    fake._get_wavesample_data = inclusive_data  # the block is still served exclusive: end = 3000
    assert receive(r, KICK, tmp_path / "k.wav")
    assert wav_samples(tmp_path / "k.wav") == kick_samples(r)
    refused = [
        m for m in (e.parse_message(d) for d in r.midi.sent) if m.command == e.CMD_GET_WAVESAMPLE_DATA
    ]
    assert len(refused) == 2  # (0, 3000) refused, (0, 2999) answered


def test_a_damaged_data_message_is_nakked_and_asked_for_again(qapp, monkeypatch, tmp_path):
    fake = FakeEnsoniq()
    r = _build(qapp, monkeypatch, fake)
    list_samples(r)
    fake.corrupt_data_messages = 2
    assert receive(r, KICK, tmp_path / "k.wav")
    assert wav_samples(tmp_path / "k.wav") == kick_samples(r)
    naks = [m for m in (e.parse_message(d) for d in r.midi.sent) if m.command == e.CMD_RESPONSE and e.parse_response(m) == e.STATUS_NAK]
    assert len(naks) == 2


def test_a_damaged_parameter_block_is_asked_for_again_too(qapp, monkeypatch, tmp_path):
    fake = FakeEnsoniq()
    r = _build(qapp, monkeypatch, fake)
    list_samples(r)
    fake.corrupt_block_messages = 1
    assert receive(r, KICK, tmp_path / "k.wav")
    assert wav_samples(tmp_path / "k.wav") == kick_samples(r)


def test_a_data_message_that_keeps_being_damaged_fails_the_receive(qapp, monkeypatch, tmp_path):
    fake = FakeEnsoniq()
    r = _build(qapp, monkeypatch, fake)
    list_samples(r)
    fake.corrupt_data_messages = 99
    assert receive(r, KICK, tmp_path / "k.wav")
    assert r.rec.receive_finished == [False]
    assert r.rec.status_has("Couldn't read the audio of KICK")
    assert not os.path.exists(tmp_path / "k.wav")


def test_a_copy_wavesample_says_so(qapp, monkeypatch, tmp_path):
    r = _build(qapp, monkeypatch, FakeEnsoniq(copies={(0, 0, 1)}))
    list_samples(r)
    assert receive(r, KICK, tmp_path / "k.wav")
    assert r.rec.receive_finished == [False]
    assert r.rec.status_has("is a copy of another wavesample")


def test_a_wavesample_that_vanished_says_to_refresh(rig, tmp_path):
    list_samples(rig)
    del rig.fake.instruments[0]["layers"][0]["wavesamples"][1]
    assert receive(rig, KICK, tmp_path / "k.wav")
    assert rig.rec.receive_finished == [False]
    assert rig.rec.status_has("refresh the list")


def test_a_length_it_cannot_understand_is_reported_not_guessed(rig, tmp_path):
    ws = rig.fake.instruments[0]["layers"][0]["wavesamples"][1]
    ws["params"].end = 0
    list_samples(rig)
    assert receive(rig, KICK, tmp_path / "k.wav")
    assert rig.rec.receive_finished == [False]
    assert rig.rec.status_has("Couldn't work out how long KICK is")
    assert [m for m in rig.midi.sent if e.parse_message(m).command == e.CMD_GET_WAVESAMPLE_DATA] == []


def test_a_rate_code_of_zero_is_reported(rig, tmp_path):
    rig.fake.instruments[0]["layers"][0]["wavesamples"][1]["params"].sample_rate_code = 0
    list_samples(rig)
    assert receive(rig, KICK, tmp_path / "k.wav")
    assert rig.rec.status_has("sample rate code")


def test_an_unwritable_path_fails_cleanly(rig, tmp_path):
    list_samples(rig)
    assert receive(rig, KICK, tmp_path / "no" / "such" / "dir" / "k.wav")
    assert rig.rec.receive_finished == [False]
    assert rig.rec.status_has("Couldn't save KICK")


def test_a_receive_never_sends_a_destructive_command(rig, tmp_path):
    list_samples(rig)
    assert receive(rig, KICK, tmp_path / "k.wav")
    commands = {e.parse_message(d).command for d in rig.midi.sent}
    assert not commands & e.NEVER_SEND
    assert commands <= {
        e.CMD_GET_INSTRUMENT,
        e.CMD_GET_LAYER,
        e.CMD_GET_WAVESAMPLE_PARAMETERS,
        e.CMD_GET_WAVESAMPLE_DATA,
        e.CMD_RESPONSE,
    }


def test_the_first_part_is_acked_before_the_data_arrives(rig, tmp_path):
    list_samples(rig)
    rig.midi.sent.clear()
    assert receive(rig, KICK, tmp_path / "k.wav")
    kinds = []
    for d in rig.midi.sent:
        m = e.parse_message(d)
        kinds.append("ack" if m.command == e.CMD_RESPONSE else "get")
    # GET params, ACK, ACK (first part, data), GET data, ACK, ACK
    assert kinds == ["get", "ack", "ack", "get", "ack", "ack"]


# --- cancel, strays ---------------------------------------------------------------


def test_cancel_mid_receive_stops_and_ignores_what_is_still_on_the_wire(rig, tmp_path):
    list_samples(rig)
    rig.midi.paced = True
    rig.engine.chunk_words = 500
    rig.controller.receive_samples([(KICK, str(tmp_path / "k.wav"))])
    assert wait_until(lambda: rig.engine.busy and len(rig.midi.sent) >= 2)
    rig.controller.cancel_transfer()
    assert rig.rec.receive_finished == [False]
    assert rig.rec.status_has("Transfer cancelled")
    assert rig.engine.idle
    sent_at_cancel = len(rig.midi.sent)
    wait_until(lambda: False, 0.1)
    assert len(rig.midi.sent) == sent_at_cancel  # nothing more went out
    assert not os.path.exists(tmp_path / "k.wav")


def test_cancel_with_nothing_running_does_nothing(rig):
    assert rig.engine.cancel() is False


def test_messages_nobody_asked_for_are_ignored(rig):
    rig.controller.on_sysex_received(bytes(e.build_response(e.STATUS_ACK)))
    rig.controller.on_sysex_received(bytes(e.build_message(e.CMD_PUT_INSTRUMENT, e.encode_context(0, 0, 1))))
    rig.controller.on_sysex_received(bytes([0x7E, 0x00, 0x7F, 0x00]))  # an SDS ACK
    assert rig.midi.sent == [] and rig.rec.statuses == []


def test_a_put_for_another_object_is_not_acked(rig):
    # an echo or a late reply must never be ACKed as if it were the answer to the request that is waiting
    rig.controller.refresh_sample_list()
    other = e.build_message(e.CMD_PUT_INSTRUMENT, e.encode_context(5, 0, 1))
    rig.controller.on_sysex_received(bytes(other))
    acks = [d for d in rig.midi.sent if e.parse_message(d).command == e.CMD_RESPONSE]
    assert acks == []
    assert wait_until(lambda: rig.engine.idle)


def test_controller_busy_and_cancel_include_the_ensoniq_engine(rig, tmp_path):
    assert rig.controller.is_transfer_busy() is False
    list_samples(rig)
    rig.midi.paced = True
    rig.controller.receive_samples([(KICK, str(tmp_path / "k.wav"))])
    assert wait_until(lambda: rig.engine.busy)
    assert rig.controller.is_transfer_busy() is True
    assert rig.controller.is_ensoniq_idle() is False
    rig.controller.cancel_transfer()
    assert rig.controller.is_transfer_busy() is False and rig.controller.is_ensoniq_idle()
