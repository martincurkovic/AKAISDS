# tests for the Yamaha editor's waveform: SDS over the shared connection, the number == list position rule,
# and the refusal of a dump that doesn't match the selected sample. Real controller + session + window against
# core/demo_a4000.FakeA4000 (which answers SDS dump requests like the real unit did on 2026-10-06).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QTimer

from core import demo_a4000 as demo
from core import sds_encoder
from core import yamaha_params as yp
from ui import yamaha_samples_tab as tab_module

from test_s950_transfers import _Midi, qapp, wait_until  # noqa: F401
from test_yamaha_program_editor import build_window, dispose
from test_yamaha_session import build, collect


# --- the fake's SDS ----------------------------------------------------------------------------------------


def test_the_fake_answers_a_dump_request_with_a_header_and_packets_after_each_ack():
    fake = demo.FakeA4000()
    fake.out.send_message([0xF0, *sds_encoder.build_dump_request(2, 0), 0xF7])
    header = bytes(fake.inp.get_message()[0][1:-1])
    info = sds_encoder.parse_dump_header(header)
    assert (info["sample_number"], info["sample_length"], info["bit_depth"]) == (2, 128, 16)
    assert info["sample_rate"] == 48001  # the unit's header rounds the period to whole nanoseconds
    assert fake.inp.get_message() is None  # nothing more until the receiver ACKs
    packets = 0
    while True:
        fake.out.send_message([0xF0, 0x7E, 0x00, sds_encoder.ACK, packets & 0x7F, 0xF7])
        got = fake.inp.get_message()
        if got is None:
            break
        assert bytes(got[0][1:-1])[2] == 0x02  # a data packet
        packets += 1
    assert packets == 4  # 128 words x 3 bytes = 384 bytes = four 120-byte packets (the last zero padded)


def test_a_number_with_no_sample_is_cancelled():
    fake = demo.FakeA4000()
    fake.out.send_message([0xF0, *sds_encoder.build_dump_request(7, 0), 0xF7])
    assert bytes(fake.inp.get_message()[0][1:-1]) == bytes([0x7E, 0x00, sds_encoder.CANCEL, 0])


def test_the_number_is_the_position_in_the_list_not_the_name():
    fake = demo.FakeA4000()
    fake.add_sample("MIDI 00101")  # sent as number 100, named ...101 - and it is the 8th sample
    fake.audio["MIDI 00101"] = [5] * 128
    fake.out.send_message([0xF0, *sds_encoder.build_dump_request(7, 0), 0xF7])
    assert sds_encoder.parse_dump_header(bytes(fake.inp.get_message()[0][1:-1]))["sample_number"] == 7
    fake.out.send_message([0xF0, *sds_encoder.build_dump_request(100, 0), 0xF7])
    assert bytes(fake.inp.get_message()[0][1:-1])[2] == sds_encoder.CANCEL


# --- the tab ---------------------------------------------------------------------------------------------


def test_helpers_match_what_the_real_unit_reported():
    data = demo.make_sample_payload("x")
    # factory waveform: 128 frames, no loop (mode 1 = continuous loop in the fixture's loop_mode is 1 -> loops)
    frames, start, loop_start, loop_end, end, loops = tab_module.sample_markers(data)
    assert (frames, start, end) == (128, 0, 127)
    assert loops is True and loop_start <= loop_end <= end
    assert tab_module.audio_matches(data, 128, 48001)  # the unit's rounded rate is accepted
    assert tab_module.audio_matches(data, 128, 48000)
    assert not tab_module.audio_matches(data, 127, 48000)
    assert not tab_module.audio_matches(data, 128, 44100)


@pytest.fixture
def window(qapp):  # noqa: F811
    fake = demo.FakeA4000()
    fake.assign(1, "sine wave")
    window = build_window(qapp, fake)
    assert wait_until(lambda: len(window._counts) == 128 and not window._scanning, timeout=20)
    yield window
    dispose(window)


def load(window, name):
    tab = window.samples_tab
    tab.select_sample(name)
    assert wait_until(lambda: tab._selected == name and name in tab._cache and not tab.cards_scroll.isHidden())
    tab._load_audio()
    return tab


def test_double_clicking_loads_the_audio_into_the_waveform(window):
    window.fake.audio["saw up"] = list(range(-6400, 6400, 100))  # 128 distinct words
    tab = load(window, "saw up")
    assert wait_until(lambda: tab._audio_name == "saw up")
    assert tab._audio_samples == window.fake.audio["saw up"]
    assert tab.waveform_view.has_waveform()
    assert "reference" in tab.waveform_hint.text()
    # the same fetch asked the unit for number 1 - saw up's position
    requests = [m for k, m in window.fake.received if m[:1] == b"\x7e" and m[2] == 0x03]
    assert len(requests) == 1 and requests[0][3] == 1


def test_a_sample_sent_later_is_fetched_by_its_position(window):
    window.fake.add_sample("MIDI 00101")
    window.fake.audio["MIDI 00101"] = [123] * 128
    window.samples_tab.set_samples(list(window.fake.samples))
    tab = load(window, "MIDI 00101")
    assert wait_until(lambda: tab._audio_name == "MIDI 00101")
    assert tab._audio_samples == [123] * 128
    asked = [m for k, m in window.fake.received if m[:1] == b"\x7e" and m[2] == 0x03]
    assert asked[-1][3] == 7


def test_a_dump_that_does_not_match_the_sample_is_refused(window):
    # the sampler "shifts" numbers: what comes back isn't 128 frames, so it can't be 'square' - never show it
    window.fake.audio["square"] = [1] * 64
    tab = load(window, "square")
    assert wait_until(lambda: "different sample" in window.status_bar.currentMessage(), timeout=5)
    assert tab._wave_path is None and tab._audio_name is None and not tab.waveform_view.has_waveform()


def test_audio_is_forgotten_by_refresh_and_when_another_sample_is_selected(window):
    tab = load(window, "triangle")
    assert wait_until(lambda: tab._audio_name == "triangle")
    tab.select_sample("pulse 1")
    assert wait_until(lambda: tab._selected == "pulse 1" and tab.name_label.text() == "pulse 1")
    assert not tab.waveform_view.has_waveform()  # header only until it is loaded
    tab.refresh()
    assert tab._audio_name is None


def test_a_cancelled_dump_leaves_the_tab_usable(window):
    tab = window.samples_tab
    tab.select_sample("pulse 3")
    assert wait_until(lambda: "pulse 3" in tab._cache and not tab.cards_scroll.isHidden())
    window.fake.samples.pop("pulse 3")  # the list on screen is stale: the unit no longer has that position -> CANCEL
    tab._load_audio()
    assert wait_until(lambda: tab._wave_path is None and not tab.waveform_view.has_waveform(), timeout=15)
    assert tab._audio_name is None
    tab.select_sample("sine wave")  # and it still works afterwards
    assert wait_until(lambda: tab._selected == "sine wave")


# --- cancelling a long load ----------------------------------------------------------------------------------------


class _Paced(_Midi):
    """Delivers ONE message per event-loop turn (the plain fake drains a whole dump inside a single call), so a
    transfer is really in flight for a while, the way it is at MIDI speed."""

    def _deliver(self):
        got = self.fake.inp.get_message()
        if got is not None:
            self.sysex_received.emit(bytes(got[0][1:-1]))
            QTimer.singleShot(0, self._deliver)


def test_cancel_stops_a_long_load_tells_the_unit_and_leaves_everything_usable(window):
    window.fake.audio["square"] = [0] * 30000  # ~750 packets: far longer than this test waits
    window.midi.__class__ = _Paced
    tab = window.samples_tab
    assert tab.cancel_load_button.isHidden()  # nothing to cancel yet
    # the fake answers faster than a test can react: click Cancel from inside the progress signal, a few packets in
    clicked = []

    def on_progress(received, total):
        if received >= 3 and not clicked:
            clicked.append(received)
            QTimer.singleShot(0, tab.cancel_load_button.click)

    window.controller.receive_progress.connect(on_progress)
    load(window, "square")
    path = tab._wave_path
    assert path is not None and not tab.cancel_load_button.isHidden()
    assert wait_until(lambda: bool(clicked) and tab._wave_path is None)
    assert clicked[0] < 30000  # it really stopped part-way (progress counts samples)
    assert len(window.controller._receive_packets) < 750
    assert tab._wave_path is None and tab.cancel_load_button.isHidden()
    assert not window.controller.is_transfer_busy()
    assert "Cancelled" in window.status_bar.currentMessage()
    assert not os.path.exists(path)  # the temp file is gone
    assert not tab.waveform_view.has_waveform() and tab._audio_name is None
    assert any(m[:1] == b"\x7e" and m[2] == sds_encoder.CANCEL for _k, m in window.fake.received)  # the unit was told
    # the Yamaha session and a new load both work again
    entries = collect_list(window)
    assert len(entries) == 142
    tab.select_sample("saw up")
    assert wait_until(lambda: tab._selected == "saw up" and "saw up" in tab._cache)
    tab._load_audio()
    assert wait_until(lambda: tab._audio_name == "saw up")


def test_the_cancel_button_hides_when_a_load_finishes_or_fails(window):
    tab = load(window, "triangle")
    assert not tab.cancel_load_button.isHidden()
    assert wait_until(lambda: tab._audio_name == "triangle")
    assert tab.cancel_load_button.isHidden()


def test_cancel_with_nothing_loading_does_nothing(window):
    window.samples_tab._cancel_load()
    assert "Cancelled" not in window.status_bar.currentMessage()


def collect_list(window):
    got = []
    window._session.request_object_list(got.append)
    assert wait_until(lambda: bool(got))
    return got[0]


# --- the session waits for an SDS transfer ---------------------------------------------------------------------


def test_yamaha_operations_wait_while_a_sds_transfer_owns_the_wire(qapp):  # noqa: F811
    rig = build(demo.FakeA4000())
    rig.session.busy_retry_ms = 5
    busy = {"on": True}
    rig.controller.is_transfer_busy = lambda: busy["on"]
    got = []
    rig.session.request_object_list(got.append)
    assert wait_until(lambda: False, timeout=0.15) is False  # let it sit
    assert got == [] and rig.fake.received == []  # nothing was put on the wire
    busy["on"] = False
    assert wait_until(lambda: bool(got))
    assert len(got[0]) == 142
