# tests for the Yamaha editor's waveform: the unit's native wave dump ("WD", core/yamaha_wave.py) - both channels of a
# stereo sample, progressive drawing, Cancel - and the refusal of audio that doesn't match the selected sample. Real
# controller + session + window against core/demo_a4000.FakeA4000. (The first tests pin the fake's SDS side, which the
# Dashboard's sample SENDS still use.)

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
    # factory waveform: 128 frames, continuous loop
    frames, start, loop_start, loop_end, end, loops = tab_module.sample_markers(data)
    assert (frames, start, end) == (128, 0, 127)
    assert loops is True and loop_start <= loop_end <= end
    assert tab_module.audio_matches(data, 128)
    assert not tab_module.audio_matches(data, 127)


@pytest.fixture
def window(qapp):  # noqa: F811
    fake = demo.FakeA4000()
    fake.assign(1, "sine wave")
    window = build_window(qapp, fake)
    window._session.wave_chunk_timeout_ms = 300
    window._session.drain_idle_ms = 60
    window.samples_tab._REVEAL_INITIAL_INTERVAL_S = 0.05  # real chunks are ~1.5 s apart; the fake's are instant
    window.samples_tab._REVEAL_FINISH_S = 0.05
    assert wait_until(lambda: len(window._counts) == 128 and not window._scanning, timeout=20)
    yield window
    dispose(window)


def load(window, name):
    tab = window.samples_tab
    tab.select_sample(name)
    assert wait_until(lambda: tab._selected == name and name in tab._cache and not tab.cards_scroll.isHidden())
    tab._load_audio()
    return tab


def wd_requests(window):
    return [m for k, m in window.fake.received if k == "dump_request" and m[11:13] == b"WD"]


def test_double_clicking_loads_the_audio_into_the_waveform(window):
    window.fake.audio["saw up"] = list(range(-6400, 6400, 100))  # 128 distinct words
    tab = load(window, "saw up")
    assert wait_until(lambda: tab._audio_name == "saw up")
    assert tab._audio_samples == window.fake.audio["saw up"] and tab._audio_samples_right is None
    assert tab.waveform_view.has_waveform() and tab.waveform_view_right.isHidden()
    assert "Drag a marker" in tab.waveform_hint.text()
    assert tab.cancel_load_button.isHidden() and not tab.loading
    # it asked for the sample's WAVE object by name - not for a Sample Dump Standard number
    assert len(wd_requests(window)) == 1 and bytes(wd_requests(window)[0][13:29]).rstrip() == b"saw up"
    # (the editor's own identity request is a 0x7E message too, but a Universal one - not Sample Dump Standard)
    assert not [m for k, m in window.fake.received if m[:1] == b"\x7e" and m[2:4] != b"\x06\x01"]


def test_a_user_sample_is_fetched_by_its_wave_name_whatever_its_position_or_name(window):
    window.fake.add_sample("MIDI 00101", audio=[123] * 300)
    window.samples_tab.set_samples(list(window.fake.samples))
    tab = load(window, "MIDI 00101")
    assert wait_until(lambda: tab._audio_name == "MIDI 00101")
    assert tab._audio_samples == [123] * 300


def test_a_stereo_sample_loads_both_channels_into_two_half_height_views(window):
    left, right = [i % 500 - 250 for i in range(6000)], [250 - i % 500 for i in range(6000)]
    window.fake.add_sample("ST", audio=left, audio_right=right)
    window.samples_tab.set_samples(list(window.fake.samples))
    tab = window.samples_tab
    tab.select_sample("ST")
    assert wait_until(lambda: tab._selected == "ST" and "ST" in tab._cache and not tab.cards_scroll.isHidden())
    # stereo is known from the header: two views at half height each, even before any audio is loaded
    assert not tab.waveform_view_right.isHidden()
    assert tab.waveform_view.height() == tab.waveform_view_right.height() == 90
    assert "stereo" in tab.summary_label.text()
    tab._load_audio()
    assert wait_until(lambda: tab._audio_name == "ST")
    assert tab._audio_samples == left and tab._audio_samples_right == right
    assert tab.waveform_view.has_waveform() and tab.waveform_view_right.has_waveform()
    assert [bytes(m[13:29]).rstrip() for m in wd_requests(window)] == [b"ST-L", b"ST-R"]
    assert "stereo" in window.status_bar.currentMessage()
    # a mono sample goes back to ONE full-height view
    tab.select_sample("pulse 1")
    assert wait_until(lambda: tab._selected == "pulse 1" and tab.name_label.text() == "pulse 1")
    assert tab.waveform_view_right.isHidden() and tab.waveform_view.height() == 180


def test_the_two_channel_views_zoom_and_pan_together(window):
    window.fake.add_sample("ST", audio=[i % 200 for i in range(8000)], audio_right=[i % 300 for i in range(8000)])
    window.samples_tab.set_samples(list(window.fake.samples))
    tab = load(window, "ST")
    assert wait_until(lambda: tab._audio_name == "ST")
    left, right = tab.waveform_view, tab.waveform_view_right
    left.set_zoom(8.0)
    assert right.view_state() == left.view_state() and left.view_state()[0] == 8.0
    tab.waveform_scrollbar.setValue(3000)
    assert right.view_state() == left.view_state() and left.view_state()[1] > 0
    right.set_zoom(2.0)  # zooming the OTHER view drives both too
    assert left.view_state() == right.view_state() and left.view_state()[0] == 2.0


def test_the_waveform_fills_in_progressively_while_the_wave_arrives(window):
    window.fake.add_sample("long", audio=[(i * 7) % 20000 - 10000 for i in range(20000)])
    window.samples_tab.set_samples(list(window.fake.samples))
    window.midi.paced = True
    tab = window.samples_tab
    tab.select_sample("long")
    assert wait_until(lambda: tab._selected == "long" and "long" in tab._cache and not tab.cards_scroll.isHidden())
    tab._load_audio()
    view = tab.waveform_view
    assert wait_until(lambda: view._samples is not None and 0 < len(view._samples) < 20000)
    assert tab.loading and not tab.cancel_load_button.isHidden() and tab._audio_name is None
    assert "fills in as it arrives" in tab.waveform_hint.text()
    assert wait_until(lambda: tab._audio_name == "long")
    assert len(view._samples) == 20000 and not tab.loading


def test_audio_that_does_not_match_the_sample_is_refused(window):
    window.fake.audio["square"] = [1] * 64  # not 128 frames: this can't be 'square' - never show it
    tab = load(window, "square")
    assert wait_until(lambda: "doesn't match" in window.status_bar.currentMessage(), timeout=5)
    assert not tab.loading and tab._audio_name is None and not tab.waveform_view.has_waveform()


def test_audio_is_forgotten_by_refresh_and_when_another_sample_is_selected(window):
    tab = load(window, "triangle")
    assert wait_until(lambda: tab._audio_name == "triangle")
    tab.select_sample("pulse 1")
    assert wait_until(lambda: tab._selected == "pulse 1" and tab.name_label.text() == "pulse 1")
    assert not tab.waveform_view.has_waveform()  # header only until it is loaded
    tab.refresh()
    assert tab._audio_name is None and tab._audio_samples_right is None


def test_a_wave_the_unit_no_longer_has_leaves_the_tab_usable(window):
    tab = window.samples_tab
    tab.select_sample("pulse 3")
    assert wait_until(lambda: "pulse 3" in tab._cache and not tab.cards_scroll.isHidden())
    window.fake.samples.pop("pulse 3")  # the list on screen is stale: the unit has no such wave any more -> no answer
    tab._load_audio()
    assert wait_until(lambda: not tab.loading, timeout=15)
    assert tab._audio_name is None and not tab.waveform_view.has_waveform()
    assert "Couldn't load" in window.status_bar.currentMessage()
    tab.select_sample("sine wave")  # and it still works afterwards
    assert wait_until(lambda: tab._selected == "sine wave")


# --- cancelling a long load ----------------------------------------------------------------------------------------


def test_cancel_stops_a_long_load_and_the_session_drains_before_working_again(window):
    window.fake.audio["square"] = [0] * 40000
    window.fake.add_sample("long", audio=[(i * 3) % 2000 for i in range(40000)])
    window.samples_tab.set_samples(list(window.fake.samples))
    window.midi.paced = True
    tab = window.samples_tab
    assert tab.cancel_load_button.isHidden()  # nothing to cancel yet
    tab.select_sample("long")
    assert wait_until(lambda: tab._selected == "long" and "long" in tab._cache and not tab.cards_scroll.isHidden())
    tab._load_audio()
    assert not tab.cancel_load_button.isHidden()
    view = tab.waveform_view
    assert wait_until(lambda: view._samples is not None and len(view._samples) > 0)
    arrived = len(view._samples)
    tab.cancel_load_button.click()
    assert not tab.loading and tab.cancel_load_button.isHidden()
    assert "Cancelled" in window.status_bar.currentMessage()
    assert tab._audio_name is None and not view.has_waveform()  # back to the header-only view
    assert not window._session.idle  # the unit is still streaming the rest: the wire stays reserved
    # a request made now waits for the stream to go quiet, then gets ITS answer (not the wave's tail)
    entries = collect_list(window)
    assert len(entries) >= 142 and window._session.idle
    assert len(view._samples or []) <= arrived + 1 or not view.has_waveform()
    tab.select_sample("saw up")
    assert wait_until(lambda: tab._selected == "saw up" and "saw up" in tab._cache)
    tab._load_audio()
    assert wait_until(lambda: tab._audio_name == "saw up")


def test_cancel_with_nothing_loading_does_nothing(window):
    window.samples_tab._cancel_load()
    assert "Cancelled" not in window.status_bar.currentMessage()


def test_closing_the_window_abandons_a_running_load(window):
    window.fake.add_sample("long", audio=[(i * 3) % 2000 for i in range(30000)])
    window.samples_tab.set_samples(list(window.fake.samples))
    window.midi.paced = True
    tab = window.samples_tab
    tab.select_sample("long")
    assert wait_until(lambda: tab._selected == "long" and "long" in tab._cache and not tab.cards_scroll.isHidden())
    tab._load_audio()
    assert wait_until(lambda: tab.waveform_view._samples is not None and len(tab.waveform_view._samples) > 0)
    window.close()
    assert window._connected is False and not tab.loading


def collect_list(window):
    got = []
    window._session.request_object_list(got.append)
    assert wait_until(lambda: bool(got), timeout=15)
    return got[0]


# --- the session waits for an SDS transfer ---------------------------------------------------------------------


def test_yamaha_operations_wait_while_a_sds_transfer_owns_the_wire(qapp):  # noqa: F811
    rig = build(demo.FakeA4000())
    rig.session.busy_retry_ms = 5
    busy = {"on": True}
    rig.controller.is_sds_transfer_busy = lambda: busy["on"]
    got = []
    rig.session.request_object_list(got.append)
    assert wait_until(lambda: False, timeout=0.15) is False  # let it sit
    assert got == [] and rig.fake.received == []  # nothing was put on the wire
    busy["on"] = False
    assert wait_until(lambda: bool(got))
    assert len(got[0]) == 142


def test_the_two_channels_touch_with_no_gap_between_them(window):
    window.fake.add_sample("ST", audio=[i % 300 for i in range(3000)], audio_right=[-(i % 300) for i in range(3000)])
    window.samples_tab.set_samples(list(window.fake.samples))
    tab = window.samples_tab
    tab.select_sample("ST")
    assert wait_until(lambda: tab._selected == "ST" and "ST" in tab._cache and not tab.cards_scroll.isHidden())
    left, right = tab.waveform_view, tab.waveform_view_right
    assert (left._stack_role, right._stack_role) == ("top", "bottom")
    # one layout holds both with no spacing, so they touch
    assert tab.channel_stack.spacing() == 0 and tab.channel_stack.indexOf(left) == 0 and tab.channel_stack.indexOf(right) == 1
    assert tab.channel_stack.contentsMargins().top() == tab.channel_stack.contentsMargins().bottom() == 0
    # a mono sample is a normal stand-alone view again
    tab.select_sample("pulse 1")
    assert wait_until(lambda: tab._selected == "pulse 1" and tab.name_label.text() == "pulse 1")
    assert left._stack_role is None and right._stack_role is None


# --- the smooth reveal ---------------------------------------------------------------------------------------------


def _reveal_rig(window, interval=0.4):
    tab = window.samples_tab
    tab._REVEAL_INITIAL_INTERVAL_S = interval
    tab._reset_reveal()
    view = tab.waveform_view
    view.set_header(10000, 0, 100, 9000, 9999)
    view.begin_live_capture()
    return tab, view


def test_a_chunk_is_revealed_gradually_not_all_at_once(window):
    tab, view = _reveal_rig(window)
    tab._queue_for_reveal(view, list(range(3000)))
    sizes = []
    assert wait_until(lambda: (sizes.append(len(view._samples)), len(view._samples) == 3000)[1], timeout=5)
    steps = sorted(set(sizes))
    assert len(steps) >= 5 and steps[0] < 3000  # it grew in several steps...
    assert view._samples == list(range(3000))  # ...and never showed anything but the received frames, in order
    assert not tab._reveal_timer.isActive() and tab._reveal_pending == 0


def test_the_pace_follows_the_measured_gap_between_chunks(window):
    tab, view = _reveal_rig(window, interval=0.5)
    tab._queue_for_reveal(view, [1] * 1000)
    assert abs(tab._reveal_speed - 2000) < 1  # 1000 frames over the estimated 0.5 s
    tab._reveal_last_chunk -= 0.1  # pretend the last chunk came 0.1 s ago: the next gap measured is ~0.1 s
    tab._queue_for_reveal(view, [1] * 1000)
    assert tab._reveal_interval_s < 0.5  # the estimate moved towards the shorter real gap
    tab._reset_reveal()


def test_finishing_waits_for_the_display_to_catch_up(window):
    tab, view = _reveal_rig(window)
    done = []
    tab._when_revealed(lambda: done.append("immediately"))  # nothing queued: runs at once
    assert done == ["immediately"]
    tab._queue_for_reveal(view, list(range(4000)))
    tab._when_revealed(lambda: done.append(len(view._samples)))
    assert done == ["immediately"]  # not yet
    assert wait_until(lambda: len(done) == 2, timeout=5)
    assert done[1] == 4000  # it ran only once everything had been shown


def test_cancelling_drops_what_was_still_waiting_to_be_shown(window):
    tab, view = _reveal_rig(window, interval=2.0)
    tab._queue_for_reveal(view, list(range(5000)))
    assert wait_until(lambda: len(view._samples) > 0, timeout=3)
    shown = len(view._samples)
    tab._reset_reveal()
    assert not wait_until(lambda: len(view._samples) > shown + 5, timeout=0.3)  # nothing more trickles in
    assert tab._reveal_pending == 0 and not tab._reveal_queue
