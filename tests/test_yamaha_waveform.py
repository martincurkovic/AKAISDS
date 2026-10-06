# tests for the Yamaha editor's waveform: SDS over the shared connection, the number == list position rule,
# and the refusal of a dump that doesn't match the selected sample. Real controller + session + window against
# core/demo_a4000.FakeA4000 (which answers SDS dump requests like the real unit did on 2026-10-06).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core import demo_a4000 as demo
from core import sds_encoder
from core import yamaha_params as yp
from ui import yamaha_samples_tab as tab_module

from test_s950_transfers import qapp, wait_until  # noqa: F401
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
