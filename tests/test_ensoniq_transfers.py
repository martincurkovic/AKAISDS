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
    assert commands == {
        e.CMD_GET_INSTRUMENT,
        e.CMD_GET_LAYER,
        e.CMD_GET_WAVESAMPLE_PARAMETERS,
        e.CMD_GET_PARAMETER,  # the memory figures
        e.CMD_RESPONSE,
    }
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


# --- the memory figures -----------------------------------------------------------


def listen_for_memory(rig):
    got = []
    rig.controller.memory_status_updated.connect(got.append)
    return got


def test_the_list_reads_free_blocks_and_every_instruments_size_for_the_memory_bar(rig):
    got = listen_for_memory(rig)
    list_samples(rig)
    used = sum(rig.fake.instrument_size(i) for i in rig.fake.instruments.values())
    free = rig.fake.free_blocks
    assert got == [
        {
            "max_num_samp_words": (free + used) * 256,
            "num_words_free": free * 256,
            "num_blocks_free": free,
        }
    ]
    assert rig.engine.idle


def test_the_samples_are_listed_before_the_memory_is_read(rig):
    order = []
    rig.controller.sample_slots_updated.connect(lambda _x: order.append("list"))
    rig.controller.memory_status_updated.connect(lambda _x: order.append("memory"))
    list_samples(rig)
    assert order == ["list", "memory"]


def test_a_unit_that_wont_say_its_memory_still_lists_and_the_bar_is_told_unknown(qapp, monkeypatch):
    fake = FakeEnsoniq()
    r = _build(qapp, monkeypatch, fake)
    fake._get_parameter = lambda message: fake._respond(e.STATUS_INVALID_PARAMETER_NUMBER)
    got = listen_for_memory(r)
    assert len(list_samples(r)) == 3
    assert got == [{"max_num_samp_words": 0, "num_words_free": 0, "num_blocks_free": 0}]
    assert not r.rec.status_has("answered")


def test_a_unit_that_goes_quiet_on_the_memory_questions_does_not_fail_the_list(qapp, monkeypatch):
    fake = FakeEnsoniq()
    r = _build(qapp, monkeypatch, fake)
    r.engine.reply_timeout_ms = 60
    fake._get_parameter = lambda message: None  # never answers
    got = listen_for_memory(r)
    assert len(list_samples(r)) == 3
    assert got == [{"max_num_samp_words": 0, "num_words_free": 0, "num_blocks_free": 0}]
    assert not r.rec.status_has("No reply")
    assert r.engine.idle


def test_an_empty_unit_reports_all_its_blocks_free(qapp, monkeypatch):
    r = _build(qapp, monkeypatch, FakeEnsoniq(instruments={}, free_blocks=100))
    got = listen_for_memory(r)
    list_samples(r)
    assert got == [{"max_num_samp_words": 100 * 256, "num_words_free": 100 * 256, "num_blocks_free": 100}]


def test_a_send_updates_the_memory_figures_through_its_refresh(rig, tmp_path):
    got = listen_for_memory(rig)
    list_samples(rig)
    before = got[-1]["num_blocks_free"]
    path, _ = make_wav(tmp_path, frames=3000)
    assert send(rig, entry(path))
    assert wait_until(lambda: len(got) >= 2 and rig.engine.idle)
    assert got[-1]["num_blocks_free"] < before


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
        e.CMD_GET_PARAMETER,
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


# === sending ======================================================================


def make_wav(tmp_path, name="kick.wav", frames=3000, rate=31250, stereo=False):
    path = str(tmp_path / name)
    samples = [((i * 37) % 2000) - 1000 for i in range(frames)]
    if stereo:
        sds_encoder.write_wav_file_stereo(path, samples, [-v for v in samples], rate, 16)
    else:
        sds_encoder.write_wav_file(path, samples, rate, 16)
    return path, samples


def entry(path, **kw):
    return {"filepath": path, "name": None, "bit_depth": 16, "sample_rate": None, "mono": False, **kw}


class _Sends:
    def __init__(self, rig):
        self.rig = rig
        rig.engine.pace_ms = 0
        rig.engine.silent_wait_ms = 20
        rig.rec.transfer_finished = []
        rig.rec.file_transferred = []
        rig.rec.transfer_progress = []
        rig.controller.transfer_finished.connect(rig.rec.transfer_finished.append)
        rig.controller.file_transferred.connect(rig.rec.file_transferred.append)
        rig.controller.transfer_progress.connect(lambda a, b: rig.rec.transfer_progress.append((a, b)))


def send(rig, *entries, timeout=10):
    _Sends(rig)
    started = rig.controller.send_file_queue(list(entries))
    assert started is True
    return wait_until(lambda: rig.rec.transfer_finished, timeout)


def new_ws(rig, inst=1, layer=0, ws=1):
    return rig.fake.instruments[inst]["layers"][layer]["wavesamples"][ws]


def test_a_mono_file_becomes_a_new_instrument_in_the_first_free_slot(rig, tmp_path):
    path, samples = make_wav(tmp_path)
    assert send(rig, entry(path, name="MY KICK"))
    assert rig.rec.transfer_finished == [True]
    assert rig.rec.file_transferred == [path]
    # the default unit has instruments 0 and 2 (see FakeEnsoniq): the first free slot is 1
    assert sorted(rig.fake.instruments) == [0, 1, 2]
    ws = new_ws(rig)
    assert ws["samples"] == samples
    p = ws["params"]
    assert (p.name, p.sample_rate_code, p.root_key, p.loop_mode) == ("MY KICK", 20, 60, e.LOOP_FORWARD_NO_LOOP)
    assert (p.start, p.end) == (0, len(samples) - 1)  # the end is written one short: see EnsoniqTransfers.end_adjust
    assert p.loop_start == 0 and p.loop_end == (len(samples) - 1, 0)
    assert rig.rec.status_has("Sent MY KICK to instrument 2 on the Ensoniq")
    assert rig.rec.status_has("Sent 1 file")


def test_the_name_defaults_to_the_file_name_and_is_cut_to_12_characters(rig, tmp_path):
    path, _ = make_wav(tmp_path, name="a very long sample name.wav")
    assert send(rig, entry(path))
    assert new_ws(rig)["params"].name == "a very long"  # 12 characters, "a very long ", the trailing space read back as padding


def test_the_send_follows_the_planned_sequence(rig, tmp_path):
    rig.engine.put_chunk_words = 1500
    path, _ = make_wav(tmp_path, frames=3000)
    assert send(rig, entry(path))
    names = {
        e.CMD_GET_PARAMETER: "free",
        e.CMD_GET_INSTRUMENT: "probe",
        e.CMD_CREATE_INSTRUMENT: "create inst",
        e.CMD_CREATE_LAYER: "create layer",
        e.CMD_GET_WAVESAMPLE_PARAMETERS: "get params",
        e.CMD_PUT_WAVESAMPLE_PARAMETERS: "put params",
        e.CMD_TRUNCATE_WAVESAMPLE: "truncate",
        e.CMD_PUT_WAVESAMPLE_DATA: "put data",
        e.CMD_GET_WAVESAMPLE_DATA: "get data",
    }
    sequence = [names[c] for c in rig.fake.commands if c in names]
    # the silent catalog refresh after the send starts with GET INSTRUMENT again: stop at the end of the send proper
    probe = sequence.index("create inst")
    assert sequence[: probe + 1] == ["free", "probe", "probe", "create inst"]  # instrument 0 exists, 1 doesn't
    after = sequence[probe + 1 :]
    assert after[:7] == ["create layer", "get params", "put params", "truncate", "put data", "put data", "get params"]
    assert after[7:11] == ["put params", "get params", "get data", "get data"]


def test_a_send_never_sends_a_destructive_command_and_never_touches_the_other_instruments(rig, tmp_path):
    import copy

    before = copy.deepcopy({k: v for k, v in rig.fake.instruments.items()})
    path, _ = make_wav(tmp_path)
    assert send(rig, entry(path))
    assert rig.fake.ignored_ops == []
    assert not set(rig.fake.commands) & e.NEVER_SEND
    # no PUT INSTRUMENT / PUT LAYER either: they carry RAM pointers, and the instrument keeps the unit's default name
    assert e.CMD_PUT_INSTRUMENT not in rig.fake.commands and e.CMD_PUT_LAYER not in rig.fake.commands
    for k, v in before.items():
        assert rig.fake.instruments[k] == v


def test_the_audio_goes_in_chunks_at_rising_offsets(rig, tmp_path):
    rig.engine.put_chunk_words = 1000
    path, samples = make_wav(tmp_path, frames=3500)
    assert send(rig, entry(path))
    assert [(a, b, n) for _i, _l, _w, a, b, n in rig.fake.data_puts] == [
        (0, 1000, 1000),
        (1000, 2000, 1000),
        (2000, 3000, 1000),
        (3000, 3500, 500),
    ]
    assert new_ws(rig)["samples"] == samples
    assert rig.rec.transfer_progress[-1] == (3500, 3500)


def test_a_rate_the_unit_cant_represent_is_resampled_to_the_nearest_codes_exact_rate(rig, tmp_path):
    path, samples = make_wav(tmp_path, frames=4410, rate=44100)
    assert send(rig, entry(path))
    p = new_ws(rig)["params"]
    assert p.sample_rate_code == 14
    # 44,643 Hz: 1.2% faster, so about 1.2% more words for the same duration
    assert len(new_ws(rig)["samples"]) == round(4410 * round(e.rate_code_to_hz(14)) / 44100)


def test_the_transmission_settings_rate_picks_the_code(rig, tmp_path):
    path, samples = make_wav(tmp_path, frames=3000, rate=31250)
    assert send(rig, entry(path, sample_rate=52083))
    assert new_ws(rig)["params"].sample_rate_code == 12


def test_a_stereo_file_is_two_panned_layers_of_one_instrument(rig, tmp_path):
    path, samples = make_wav(tmp_path, name="drum.wav", frames=2000, stereo=True)
    assert send(rig, entry(path))
    inst = rig.fake.instruments[1]
    assert sorted(inst["layers"]) == [0, 1]
    left, right = inst["layers"][0]["wavesamples"][1], inst["layers"][1]["wavesamples"][1]
    assert left["samples"] == samples and right["samples"] == [-v for v in samples]
    assert (left["params"].name, right["params"].name) == ("drum L", "drum R")
    pans = [(layer, page, item, value) for _i, layer, _w, page, item, value in rig.fake.parameter_puts]
    assert pans == [(0, 6, 2, 1), (1, 6, 2, 8)]  # volume page, Pan: 1 = far left ... 8 = far right
    assert rig.rec.transfer_progress[-1] == (4000, 4000)


def test_the_mono_setting_sends_only_the_left_channel_of_a_stereo_file(rig, tmp_path):
    path, samples = make_wav(tmp_path, frames=2000, stereo=True)
    assert send(rig, entry(path, mono=True))
    assert sorted(rig.fake.instruments[1]["layers"]) == [0]
    assert new_ws(rig)["samples"] == samples
    assert rig.fake.parameter_puts == []


def test_several_files_each_get_their_own_instrument_in_order(rig, tmp_path):
    a, sa = make_wav(tmp_path, name="a.wav", frames=800)
    b, sb = make_wav(tmp_path, name="b.wav", frames=900)
    assert send(rig, entry(a), entry(b))
    assert sorted(rig.fake.instruments) == [0, 1, 2, 3]  # 1 and 3 were free
    assert new_ws(rig, 1)["samples"] == sa and new_ws(rig, 3)["samples"] == sb
    assert rig.rec.file_transferred == [a, b]
    assert rig.rec.status_has("Sent 2 files")
    # the list is refreshed afterwards, so the new wavesamples show up
    assert wait_until(lambda: len(rig.rec.slots) and len(rig.rec.slots[-1]) == 5)


def test_an_unreadable_file_is_skipped_and_the_rest_still_go(rig, tmp_path):
    good, samples = make_wav(tmp_path, name="good.wav", frames=800)
    assert send(rig, entry(str(tmp_path / "missing.wav")), entry(good))
    assert rig.rec.file_transferred == [good]
    assert rig.rec.status_has("Skipping missing.wav")
    assert rig.rec.status_has("skipped 1")
    assert new_ws(rig)["samples"] == samples


def test_not_enough_free_memory_skips_the_file_before_anything_is_created(qapp, monkeypatch, tmp_path):
    r = _build(qapp, monkeypatch, FakeEnsoniq(free_blocks=5))
    path, _ = make_wav(tmp_path, frames=3000)
    assert send(r, entry(path))
    assert r.rec.status_has("Not enough sample memory on the Ensoniq")
    assert sorted(r.fake.instruments) == [0, 2]
    assert e.CMD_CREATE_INSTRUMENT not in r.fake.commands
    assert r.rec.transfer_finished == [False]  # nothing was sent


def test_a_unit_that_cant_report_its_free_memory_is_sent_to_anyway(qapp, monkeypatch, tmp_path):
    fake = FakeEnsoniq()
    r = _build(qapp, monkeypatch, fake)
    fake._get_parameter = lambda message: fake._respond(e.STATUS_INVALID_PARAMETER_NUMBER)
    path, samples = make_wav(tmp_path, frames=800)
    assert send(r, entry(path))
    assert new_ws(r)["samples"] == samples


def test_all_eight_instruments_in_use_refuses_without_creating_anything(qapp, monkeypatch, tmp_path):
    fake = FakeEnsoniq(
        instruments={i: make_instrument(f"I{i}", {0: make_layer("L", {1: make_wavesample("W", frames=100)})}) for i in range(8)}
    )
    r = _build(qapp, monkeypatch, fake)
    path, _ = make_wav(tmp_path, frames=800)
    assert send(r, entry(path))
    assert r.rec.status_has("All 8 instrument slots")
    assert r.rec.transfer_finished == [False]
    assert e.CMD_CREATE_INSTRUMENT not in fake.commands
    assert "incomplete" not in r.rec.statuses[-1]


def test_a_failure_after_the_instrument_exists_says_which_one_may_be_incomplete(qapp, monkeypatch, tmp_path):
    fake = FakeEnsoniq(free_blocks=2)
    r = _build(qapp, monkeypatch, fake)
    r.engine.memory_margin_blocks = -1000  # get past the up-front check so the unit itself runs out (status 0C)
    path, _ = make_wav(tmp_path, frames=3000)
    assert send(r, entry(path))
    assert r.rec.transfer_finished == [False]
    last = r.rec.statuses[-1]
    assert "memory full" in last and "Instrument 2 on the Ensoniq may be incomplete" in last
    assert r.engine.idle


def test_a_unit_that_wants_an_inclusive_range_end_is_asked_again_once(qapp, monkeypatch, tmp_path):
    r = _build(qapp, monkeypatch, FakeEnsoniq(inclusive_end=True))
    r.engine.put_chunk_words = 1000
    path, samples = make_wav(tmp_path, frames=2500)
    assert send(r, entry(path))
    assert r.engine.put_end_inclusive is True
    assert new_ws(r)["samples"] == samples
    assert r.rec.transfer_finished == [True]
    assert "doesn't read back" not in " ".join(r.rec.statuses)


def test_a_damaged_read_back_is_reported_but_the_file_counts_as_sent(qapp, monkeypatch, tmp_path):
    r = _build(qapp, monkeypatch, FakeEnsoniq(corrupt_stored_audio=True))
    path, _ = make_wav(tmp_path, frames=800)
    assert send(r, entry(path))
    assert r.rec.status_has("doesn't read back the same")
    assert r.rec.status_has("1 didn't read back the same")
    assert r.rec.file_transferred == [path]


def test_the_wait_status_during_a_send_is_acked(qapp, monkeypatch, tmp_path):
    r = _build(qapp, monkeypatch, FakeEnsoniq(wait_on_first_get=True))
    path, samples = make_wav(tmp_path, frames=800)
    assert send(r, entry(path))
    assert new_ws(r)["samples"] == samples


def test_a_silent_unit_fails_the_send_cleanly(qapp, monkeypatch, tmp_path):
    r = _build(qapp, monkeypatch, FakeEnsoniq(channel=5))
    r.engine.reply_timeout_ms = 60
    path, _ = make_wav(tmp_path, frames=800)
    assert send(r, entry(path))
    assert r.rec.transfer_finished == [False]
    assert r.rec.status_has("No reply from the Ensoniq")
    assert r.engine.idle and "incomplete" not in r.rec.statuses[-1]


def test_the_wavesample_start_and_end_follow_the_audio_not_the_placeholder(rig, tmp_path):
    path, samples = make_wav(tmp_path, frames=777)
    assert send(rig, entry(path))
    p = new_ws(rig)["params"]
    assert (p.start, p.end, p.length) == (0, 776, 776)


def test_cancel_mid_send_says_what_may_be_left_on_the_unit(rig, tmp_path):
    rig.engine.put_chunk_words = 200
    path, _ = make_wav(tmp_path, frames=3000)
    _Sends(rig)
    rig.midi.paced = True
    rig.controller.send_file_queue([entry(path)])
    assert wait_until(lambda: rig.fake.data_puts)
    rig.controller.cancel_transfer()
    assert rig.rec.transfer_finished == [False]
    assert rig.rec.status_has("Transfer cancelled. Instrument 2 on the Ensoniq may be incomplete")
    assert rig.engine.idle
    sent = len(rig.midi.sent)
    wait_until(lambda: False, 0.1)
    assert len(rig.midi.sent) == sent


def test_a_second_send_is_refused_while_one_runs(rig, tmp_path):
    path, _ = make_wav(tmp_path, frames=800)
    _Sends(rig)
    assert rig.controller.send_file_queue([entry(path)]) is True
    assert rig.controller.send_file_queue([entry(path)]) is False
    assert rig.rec.status_has("already in progress")
    assert wait_until(lambda: rig.rec.transfer_finished)


def test_sending_needs_a_midi_input(qapp, monkeypatch, tmp_path):
    r = _build(qapp, monkeypatch, FakeEnsoniq(), input_name=None)
    path, _ = make_wav(tmp_path, frames=800)
    assert r.controller.send_file_queue([entry(path)]) is False
    assert r.midi.sent == [] and r.rec.status_has("needs a MIDI input")


def test_an_empty_queue_does_not_start(rig):
    assert rig.controller.send_file_queue([]) is False
    assert rig.midi.sent == []


# --- prepare_channels --------------------------------------------------------------


def test_prepare_channels_picks_the_code_and_pads_short_audio():
    words, code, rate = et.prepare_channels([[1, 2, 3]], 31250)
    assert (code, rate) == (20, 31250)
    assert len(words[0]) == et.MIN_SEND_WORDS and words[0][:4] == [1, 2, 3, 0]


def test_prepare_channels_refuses_audio_longer_than_one_range_can_address():
    with pytest.raises(ValueError, match="too long for the Ensoniq"):
        et.prepare_channels([[0] * (e.MAX_OFFSET + 1)], 31250)


def test_prepare_channels_folds_stereo_to_the_left_when_mono_is_asked():
    words, _code, _rate = et.prepare_channels([[5] * 100, [9] * 100], 31250, force_mono=True)
    assert len(words) == 1 and words[0][0] == 5
    both, _c, _r = et.prepare_channels([[5] * 100, [9] * 100], 31250)
    assert len(both) == 2


def test_prepare_channels_clips_to_16_bits():
    words, _c, _r = et.prepare_channels([[40000, -40000] + [0] * 100], 31250)
    assert words[0][:2] == [32767, -32768]
