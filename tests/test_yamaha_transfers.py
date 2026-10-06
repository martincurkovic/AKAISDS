# tests for controller/yamaha_transfers.py (the Dashboard's Yamaha sample LIST and RECEIVE over the unit's native
# protocol) and the Dashboard wiring around it. Real SamplerController + YamahaSession + YamahaTransfers (+ the real
# TransferDashboard) against core/demo_a4000.FakeA4000 over the S950 tests' fake MidiManager.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QCheckBox

from controller.sampler_controller import SamplerController
from core import demo_a4000 as demo
from core import dropped_files, sds_encoder

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
