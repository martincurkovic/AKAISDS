# tests for controller/yamaha_transfers.py (the Dashboard's Yamaha sample LIST and RECEIVE over the unit's native
# protocol) and the Dashboard wiring around it. Real SamplerController + YamahaSession + YamahaTransfers (+ the real
# TransferDashboard) against core/demo_a4000.FakeA4000 over the S950 tests' fake MidiManager.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QCheckBox

from controller.sampler_controller import SamplerController
from core import demo_a4000 as demo
from core import dropped_files, sds_encoder
from core import yamaha_params as yp

from test_s950_transfers import qapp, wait_until  # noqa: F401
from test_yamaha_session import build


class Recorder:
    def __init__(self, controller):
        self.lists, self.received, self.finished, self.progress, self.statuses = [], [], [], [], []
        controller.sample_list_updated.connect(self.lists.append)
        controller.sample_received.connect(self.received.append)
        controller.receive_finished.connect(self.finished.append)
        controller.receive_progress.connect(lambda r, t: self.progress.append((r, t)))
        controller.status_changed.connect(self.statuses.append)


@pytest.fixture
def rig(qapp):  # noqa: F811
    fake = demo.FakeA4000()
    rig = build(fake)
    rig.rec = Recorder(rig.controller)
    return rig


def read(path):
    channels, rate = sds_encoder.read_wav_channels(path)
    return [list(c) for c in channels], rate


# --- the list --------------------------------------------------------------------------------------------------


def test_refresh_lists_the_samples_in_order_and_remembers_them(rig):
    rig.fake.add_sample("MIDI 00101", audio=[1] * 200)
    rig.controller.refresh_sample_list()
    assert wait_until(lambda: bool(rig.rec.lists))
    assert rig.rec.lists[-1] == list(demo.FACTORY_SAMPLES) + ["MIDI 00101"]
    assert any("Loaded 8 sample" in s for s in rig.rec.statuses)


def test_a_silent_refresh_says_nothing_and_a_failed_one_says_what_to_check(qapp):  # noqa: F811
    rig = build(demo.FakeA4000())
    rig.rec = Recorder(rig.controller)
    rig.controller.refresh_sample_list(silent=True)
    assert wait_until(lambda: bool(rig.rec.lists))
    assert not any("Loaded" in s for s in rig.rec.statuses)
    dead = build(demo.FakeA4000(device_number_off=True))
    dead.rec = Recorder(dead.controller)
    dead.controller.refresh_sample_list()
    assert wait_until(lambda: any("Couldn't read the Yamaha" in s for s in dead.rec.statuses), timeout=10)
    assert dead.rec.lists == []


def test_generic_sds_still_has_no_list(qapp):  # noqa: F811
    rig = build(demo.FakeA4000(), model="generic")
    rig.controller.refresh_sample_list()
    assert rig.fake.received == [] and rig.controller._yamaha_transfers_engine is None


# --- receiving ---------------------------------------------------------------------------------------------------


def start(rig, requests):
    rig.controller.refresh_sample_list(silent=True)
    assert wait_until(lambda: bool(rig.rec.lists))
    rig.controller.receive_samples(requests)


def test_a_mono_sample_is_saved_as_a_wav_through_its_wave_object(rig, tmp_path):
    audio = [(i * 31) % 20000 - 10000 for i in range(5000)]
    rig.fake.add_sample("MIDI 00101", audio=audio)
    path = str(tmp_path / "a.wav")
    start(rig, [(7, path)])
    assert wait_until(lambda: bool(rig.rec.finished))
    assert rig.rec.finished == [True] and rig.rec.received == [path]
    channels, rate = read(path)
    assert len(channels) == 1 and channels[0] == audio and rate == 48000
    assert rig.rec.progress[-1] == (5000, 5000) and any(r > 0 for r, _t in rig.rec.progress)
    assert not rig.controller.is_transfer_busy()
    # native protocol only: no Sample Dump Standard traffic at all
    assert not [m for k, m in rig.fake.received if m[:1] == b"\x7e"]


def test_a_stereo_sample_is_saved_as_a_stereo_wav_with_both_channels(rig, tmp_path):
    left, right = [i % 400 - 200 for i in range(4000)], [300 - i % 600 for i in range(4000)]
    rig.fake.add_sample("ST", audio=left, audio_right=right)
    path = str(tmp_path / "st.wav")
    start(rig, [(7, path)])
    assert wait_until(lambda: bool(rig.rec.finished))
    channels, _rate = read(path)
    assert len(channels) == 2 and channels[0] == left and channels[1] == right
    assert rig.rec.progress[-1] == (8000, 8000)  # progress counts both channels


def test_several_samples_arrive_one_after_another(rig, tmp_path):
    paths = [str(tmp_path / f"{n}.wav") for n in ("a", "b", "c")]
    start(rig, [(0, paths[0]), (3, paths[1]), (6, paths[2])])
    assert wait_until(lambda: bool(rig.rec.finished))
    assert rig.rec.received == paths and rig.rec.finished == [True]
    assert [len(read(p)[0][0]) for p in paths] == [128, 128, 128]
    assert any("All samples received" in s for s in rig.rec.statuses)


def test_a_number_that_is_not_in_the_list_fails_cleanly(rig, tmp_path):
    start(rig, [(0, str(tmp_path / "ok.wav")), (99, str(tmp_path / "bad.wav"))])
    assert wait_until(lambda: bool(rig.rec.finished))
    assert rig.rec.finished == [False] and len(rig.rec.received) == 1
    assert any("isn't in the list" in s for s in rig.rec.statuses)
    assert not rig.controller.is_transfer_busy()


def test_a_sample_the_unit_cannot_deliver_fails_cleanly(rig, tmp_path):
    start(rig, [(0, str(tmp_path / "x.wav"))])
    rig.fake.samples.pop("sine wave")  # gone between listing and reading
    assert wait_until(lambda: bool(rig.rec.finished), timeout=15)
    assert rig.rec.finished == [False] and rig.rec.received == []
    assert not rig.controller.is_transfer_busy() and not os.path.exists(tmp_path / "x.wav")


def test_a_second_receive_is_refused_while_one_runs_and_so_is_a_send(rig, tmp_path):
    rig.fake.add_sample("big", audio=[i % 100 for i in range(20000)])
    rig.midi.paced = True
    start(rig, [(7, str(tmp_path / "big.wav"))])
    assert rig.controller.is_transfer_busy()
    rig.controller.receive_samples([(0, str(tmp_path / "again.wav"))])
    assert any("already in progress" in s for s in rig.rec.statuses)
    assert rig.controller.send_file_queue([{"filepath": "x.wav"}], starting_sample_number=0) is False
    assert wait_until(lambda: bool(rig.rec.finished), timeout=20)
    assert rig.rec.finished == [True] and not os.path.exists(tmp_path / "again.wav")


def test_cancel_stops_the_receive_and_nothing_more_is_saved(rig, tmp_path):
    rig.fake.add_sample("big", audio=[i % 100 for i in range(30000)])
    rig.midi.paced = True
    first = str(tmp_path / "big.wav")
    start(rig, [(7, first), (0, str(tmp_path / "never.wav"))])
    assert wait_until(lambda: any(r > 0 for r, _t in rig.rec.progress))
    rig.controller.cancel_transfer()
    assert rig.rec.finished == [False] and "Transfer cancelled" in rig.rec.statuses
    assert not rig.controller.is_transfer_busy()
    assert not rig.session.idle  # the unit is still streaming: drained before the next request
    got = []
    rig.session.request_object_list(got.append)
    assert wait_until(lambda: bool(got), timeout=15) and got[0] is not None
    QCoreApplication.processEvents()
    assert rig.rec.received == [] and rig.rec.finished == [False]
    assert not os.path.exists(first) and not os.path.exists(tmp_path / "never.wav")


def test_delete_rename_and_info_are_refused_without_putting_akai_bytes_on_the_wire(rig):
    rig.controller.delete_sample(3)
    rig.controller.rename_sample(3, "NEW")
    rig.controller.request_sample_info(3)
    assert rig.fake.received == [] and rig.fake.ignored_ops == []
    assert any("isn't available for the Yamaha" in s for s in rig.rec.statuses)


# --- the Dashboard ---------------------------------------------------------------------------------------------


@pytest.fixture
def dash(qapp, tmp_path, monkeypatch):  # noqa: F811
    from ui.dashboard import TransferDashboard

    monkeypatch.setattr(dropped_files, "_BASE_DIR", tmp_path / "dropped_files")
    monkeypatch.setattr(dropped_files, "_session_dir", None)
    fake = demo.FakeA4000()
    fake.add_sample("MIDI 00101", audio=[(i * 11) % 5000 for i in range(3000)])
    fake.add_sample("ST", audio=[i % 300 for i in range(2000)], audio_right=[-(i % 300) for i in range(2000)])
    rig = build(fake)
    rig.midi.output_name = "Fake Out"
    d = TransferDashboard(rig.controller, rig.midi)
    d.rig = rig
    yield d
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def test_the_dashboard_lists_the_unit_samples_and_receives_the_checked_ones(dash, tmp_path, monkeypatch):
    from ui import dashboard as dashboard_module

    dash._update_device_type_ui()
    assert dash.btn_refresh.isEnabled()
    dash.btn_refresh.click()
    assert wait_until(lambda: dash.list_hardware.count() == 9)  # 7 factory + 2
    names = []
    for i in range(dash.list_hardware.count()):
        row = dash.list_hardware.itemWidget(dash.list_hardware.item(i))
        names.append(row.findChild(QCheckBox).text())
        # no delete / info for the Yamaha - the row buttons are off with a hint
        buttons = [b for b in row.findChildren(type(dash.btn_refresh)) if b.text() in ("Edit", "Delete")]
        assert buttons and all(not b.isEnabled() for b in buttons)
    assert names[:2] == ["sine wave", "saw up"] and names[-2:] == ["MIDI 00101", "ST"]
    assert dash.btn_receive.isEnabled() and dash.btn_select_all.isEnabled()
    for name in ("MIDI 00101", "ST"):
        row = dash.list_hardware.itemWidget(dash.list_hardware.item(names.index(name)))
        row.findChild(QCheckBox).setChecked(True)
    monkeypatch.setattr(dashboard_module.QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    dash.btn_receive.click()
    assert wait_until(lambda: (tmp_path / "ST.wav").exists() and dash.btn_cancel.isEnabled() is False, timeout=15)
    mono, _ = read(str(tmp_path / "MIDI 00101.wav"))
    stereo, _ = read(str(tmp_path / "ST.wav"))
    assert len(mono) == 1 and len(mono[0]) == 3000 and len(stereo) == 2
    assert dash.progress_bar_current_smpl.isHidden() and dash.btn_send.isEnabled()


# --- sending (the unit's native bulk load) -----------------------------------------------------------------------


class SendRecorder:
    def __init__(self, controller):
        self.finished, self.transferred, self.progress, self.units, self.lists = [], [], [], [], []
        controller.transfer_finished.connect(self.finished.append)
        controller.file_transferred.connect(self.transferred.append)
        controller.transfer_progress.connect(lambda s, t: self.progress.append((s, t)))
        controller.unit_progress.connect(self.units.append)
        controller.sample_list_updated.connect(self.lists.append)


@pytest.fixture
def sender(qapp):  # noqa: F811
    rig = build(demo.FakeA4000())
    rig.rec = Recorder(rig.controller)
    rig.send = SendRecorder(rig.controller)
    rig.session.send_baud = 10_000_000  # a message "takes" microseconds, not 1.3 s
    rig.session.send_gap_ms = 0
    rig.session.send_tick_ms = 5
    rig.controller._yamaha_transfers.verify_delay_ms = 0
    rig.controller._yamaha_transfers.verify_retry_ms = 20
    return rig


def wav(tmp_path, name, left, right=None, rate=22050):
    path = str(tmp_path / f"{name}.wav")
    if right is None:
        sds_encoder.write_wav_file(path, left, rate, 16)
    else:
        sds_encoder.write_wav_file_stereo(path, left, right, rate, 16)
    return path


def entry(path, **kw):
    return {"filepath": path, "name": None, "bit_depth": 16, "sample_rate": None, "mono": False, **kw}


def finish(rig):
    assert wait_until(lambda: bool(rig.send.finished), timeout=20)
    return rig.send.finished[-1]


def test_a_mono_file_becomes_a_new_sample_with_its_audio(sender, tmp_path):
    audio = [(i * 31) % 20000 - 10000 for i in range(6000)]
    path = wav(tmp_path, "kick", audio)
    assert sender.controller.send_file_queue([entry(path)])
    assert finish(sender) is True
    fake = sender.fake
    assert "kick" in fake.samples and fake.audio["kick"] == audio and "kick" not in fake.audio_right
    data = fake.samples["kick"]
    assert yp.extract(yp.get("sample", "wave_length"), data) == 6000 and yp.extract(yp.get("sample", "sampling_frequency_l"), data) == 22050
    assert sender.send.transferred == [path]
    assert wait_until(lambda: bool(sender.send.lists) and "kick" in sender.send.lists[-1])  # the Dashboard's list is refreshed
    assert not sender.controller.is_transfer_busy()
    assert not [m for k, m in fake.received if m[:1] == b"\x7e"]  # no Sample Dump Standard anywhere


def test_an_edited_copy_that_keeps_a_shorter_start_end_window_still_verifies(sender, tmp_path):
    # real A4000 (2026-10-07): Reverse/Fade/Normalise/Filter of a sample whose Start/End sat inside the audio carry that window
    # (wave_length 5400 of 6000 frames); the check compared wave_length with the audio's frame count and reported a good copy as "not loaded"
    audio = [(i * 31) % 20000 - 10000 for i in range(6000)]
    path = wav(tmp_path, "win", audio)
    params = {"wave_start_address": 100, "wave_end_address": 5500, "wave_length": 5400, "loop_start_address": 5500, "loop_end_address": 5500, "loop_length": 0}
    assert sender.controller.send_file_queue([entry(path, params=params)])
    assert finish(sender) is True
    data = sender.fake.samples["win"]
    assert yp.extract(yp.get("sample", "wave_length"), data) == 5400 and sender.fake.audio["win"] == audio  # all the audio, the carried window
    assert sender.controller.yamaha_loaded_names() == ["win"]


def test_a_stereo_file_becomes_one_real_stereo_sample(sender, tmp_path):
    left, right = [i % 500 - 250 for i in range(5000)], [250 - i % 500 for i in range(5000)]
    path = wav(tmp_path, "pad", left, right)
    sender.controller.send_file_queue([entry(path)])
    assert finish(sender) is True
    fake = sender.fake
    assert fake.audio["pad"] == left and fake.audio_right["pad"] == right
    assert yp.is_stereo(fake.samples["pad"])
    assert len([o for o in fake.bulk_loads if o[0] == "WD"]) >= 2  # two wave objects
    assert sender.send.transferred == [path]


def test_mono_option_sends_only_the_left_channel(sender, tmp_path):
    left, right = [100] * 2000, [-100] * 2000
    sender.controller.send_file_queue([entry(wav(tmp_path, "x", left, right), mono=True)])
    assert finish(sender) is True
    assert sender.fake.audio["x"] == left and "x" not in sender.fake.audio_right


def test_a_name_already_on_the_unit_is_never_overwritten(sender, tmp_path):
    sender.fake.add_sample("kick", audio=[7] * 300)
    path = wav(tmp_path, "kick", [5] * 2000)
    sender.controller.send_file_queue([entry(path)])
    assert finish(sender) is True
    fake = sender.fake
    assert fake.audio["kick"] == [7] * 300 and fake.audio["kick 2"] == [5] * 2000
    assert any("'kick 2'" in s and "already on the sampler" in s for s in sender.rec.statuses)


def test_the_name_typed_in_the_queue_is_used(sender, tmp_path):
    sender.controller.send_file_queue([entry(wav(tmp_path, "file", [1] * 800), name="My Snare")])
    assert finish(sender) is True
    assert "My Snare" in sender.fake.samples


def test_a_rate_override_resamples_and_is_what_the_sample_says(sender, tmp_path):
    sender.controller.send_file_queue([entry(wav(tmp_path, "r", [i % 100 for i in range(4000)], rate=22050), sample_rate=11025)])
    assert finish(sender) is True
    data = sender.fake.samples["r"]
    assert yp.extract(yp.get("sample", "sampling_frequency_l"), data) == 11025
    assert abs(yp.extract(yp.get("sample", "wave_length"), data) - 2000) <= 1


def test_several_files_load_in_order_and_progress_reaches_the_end(sender, tmp_path):
    paths = [wav(tmp_path, n, [i % 90 for i in range(3000 + 500 * k)]) for k, n in enumerate(("a", "b", "c"))]
    sender.controller.send_file_queue([entry(p) for p in paths])
    assert finish(sender) is True
    assert sender.send.transferred == paths
    assert [n for n in sender.fake.samples if n in "abc"] == ["a", "b", "c"]
    sent, total = sender.send.progress[-1]
    assert sent == total and all(s <= t for s, t in sender.send.progress)
    assert sender.send.units[-1] == 1.0 and any(0 < u < 1 for u in sender.send.units)
    assert any("Sent 3 samples" in s for s in sender.rec.statuses)


def test_an_unreadable_file_is_skipped_and_the_rest_still_load(sender, tmp_path):
    bad = str(tmp_path / "bad.wav")
    open(bad, "wb").write(b"not a wav")
    good = wav(tmp_path, "good", [3] * 1500)
    sender.controller.send_file_queue([entry(bad), entry(good)])
    assert finish(sender) is True
    assert sender.send.transferred == [good] and "good" in sender.fake.samples
    assert any("Skipping bad.wav" in s for s in sender.rec.statuses) and any("skipped 1" in s for s in sender.rec.statuses)


def test_a_unit_that_refuses_the_load_is_reported_not_trusted(sender, tmp_path):
    sender.fake.bulk_protect = True  # the unit swallows the load without a word
    sender.controller.send_file_queue([entry(wav(tmp_path, "a", [1] * 900)), entry(wav(tmp_path, "b", [1] * 900))])
    assert finish(sender) is False
    assert sender.send.transferred == [] and "a" not in sender.fake.samples
    assert any("was not loaded" in s and "Bulk Protect" in s for s in sender.rec.statuses)
    assert not sender.controller.is_transfer_busy()


def test_cancelling_mid_load_stops_sending_and_frees_the_wire(sender, tmp_path):
    sender.session.send_baud = 150_000  # ~30 ms per message: long enough to cancel in the middle
    path = wav(tmp_path, "long", [i % 700 for i in range(40000)])
    sender.controller.send_file_queue([entry(path)])
    assert wait_until(lambda: bool(sender.fake.bulk_loads))
    assert sender.controller.is_transfer_busy()
    sender.controller.cancel_transfer()
    assert sender.send.finished == [False] and not sender.controller.is_transfer_busy()
    sent_then = len(sender.fake.bulk_loads)
    QCoreApplication.processEvents()
    assert wait_until(lambda: sender.session.idle)
    assert len(sender.fake.bulk_loads) == sent_then and "long" not in sender.fake.samples
    assert sender.send.transferred == [] and any("cancelled" in s.lower() for s in sender.rec.statuses)


def test_sending_needs_a_midi_input_and_one_transfer_at_a_time(sender, tmp_path):
    path = wav(tmp_path, "a", [1] * 500)
    sender.midi.input_name = None
    assert sender.controller.send_file_queue([entry(path)]) is False
    assert any("needs a MIDI input" in s for s in sender.rec.statuses)
    sender.midi.input_name = "Fake In"
    assert sender.controller.send_file_queue([entry(path)]) is True
    assert sender.controller.send_file_queue([entry(path)]) is False  # busy
    finish(sender)


def test_the_dashboard_sends_a_queued_file_natively_with_one_stereo_unit_and_no_packet_text(dash, tmp_path):
    rig = dash.rig
    rig.session.send_baud, rig.session.send_gap_ms, rig.session.send_tick_ms = 10_000_000, 0, 5
    rig.controller._yamaha_transfers.verify_delay_ms = 0
    path = wav(tmp_path, "pad", [i % 400 for i in range(4000)], [-(i % 400) for i in range(4000)])
    dash.create_local_row(path)
    dash._update_queue_buttons_state()
    assert dash.btn_send.isEnabled()
    dash.btn_send.click()
    assert wait_until(lambda: "pad" in rig.fake.samples and dash.list_local.count() == 0, timeout=20)
    assert yp.is_stereo(rig.fake.samples["pad"])  # a stereo file is ONE stereo sample, not two mono ones
    assert dash.progress_bar_overall.isHidden()  # one unit: no second bar
    assert wait_until(lambda: dash.btn_cancel.isEnabled() is False and dash.progress_bar_current_smpl.isHidden())
    assert wait_until(lambda: dash.list_hardware.count() == 10)  # the list refreshed itself: 7 factory + MIDI 00101 + ST + pad
    assert "packet" not in dash.status_bar.currentMessage()


def test_the_send_button_needs_a_midi_input_for_a_yamaha(dash, tmp_path):
    from ui import tooltips

    dash.create_local_row(wav(tmp_path, "a", [1] * 500))
    dash._update_queue_buttons_state()
    assert dash.btn_send.isEnabled()
    dash.rig.midi.input_name = None
    dash._update_queue_buttons_state()
    assert not dash.btn_send.isEnabled() and dash.btn_send.toolTip() == tooltips.YAMAHA_SEND_NEEDS_MIDI_INPUT


# --- no memory bar ----------------------------------------------------------------------------------------------------


def test_a_list_refresh_reads_no_sample_dumps_and_reports_no_memory(rig, dash):
    # the unit can't report its free memory, and an estimate meant reading every sample's dump on each refresh - gone
    infos = []
    rig.controller.memory_status_updated.connect(infos.append)
    before = len([k for k, m in rig.fake.received if k == "dump_request"])
    rig.controller.refresh_sample_list(silent=True)
    assert wait_until(lambda: bool(rig.rec.lists))
    assert infos == []
    assert len([k for k, m in rig.fake.received if k == "dump_request"]) == before + 1  # the object list itself, nothing per sample
    dash._update_device_type_ui()
    assert dash.memory_avail_prog_bar.isHidden()


def test_a_dropped_first_read_after_a_load_is_retried_not_reported_as_a_failure(sender, tmp_path):
    # measured on the real unit: the first request after a load is sometimes simply not answered
    session = sender.session
    real = session.request_object_list
    state = {"calls": 0}

    def flaky(callback):
        state["calls"] += 1
        if state["calls"] == 2:  # the first call is the pre-send name check; the second is the post-load check
            QTimer.singleShot(0, lambda: callback(None))
            return
        real(callback)

    session.request_object_list = flaky
    sender.controller.send_file_queue([entry(wav(tmp_path, "r", [1] * 900))])
    assert finish(sender) is True and state["calls"] >= 4  # (the retry, then the refresh)
    assert sender.send.transferred and "r" in sender.fake.samples


def test_a_load_whose_checks_never_get_an_answer_fails_after_the_retries(sender, tmp_path):
    sender.controller._yamaha_transfers.verify_retries = 1
    session = sender.session
    real = session.request_object_list
    state = {"calls": 0}

    def dead_after_the_first(callback):
        state["calls"] += 1
        if state["calls"] == 1:
            real(callback)
        else:
            QTimer.singleShot(0, lambda: callback(None))

    session.request_object_list = dead_after_the_first
    sender.controller.send_file_queue([entry(wav(tmp_path, "r", [1] * 900))])
    assert finish(sender) is False and any("was not loaded" in s for s in sender.rec.statuses)
