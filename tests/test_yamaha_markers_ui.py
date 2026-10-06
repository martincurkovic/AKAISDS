# tests for the Yamaha Samples tab's EDITABLE MARKERS and CLICK-TO-PREVIEW, and for the fake unit's wave/loop address rules they
# rely on (copied from what the real A4000 did - core/yamaha_markers.py). Real controller + session + window against FakeA4000.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import struct

import pytest

from core import audio_preview
from core import demo_a4000 as demo
from core import yamaha_markers as ym
from core import yamaha_params as yp
from core import yamaha_sysex as y

from test_s950_transfers import qapp, wait_until  # noqa: F401
from test_yamaha_waveform import load, window  # noqa: F401


def put(fake, name, **values):
    for key, value in values.items():
        yp.store(yp.get("sample", key), fake.samples[name], value)


def user_sample(window, name="UM", frames=4000, *, mode=1, end=3000, loop=(1000, 2000), right=False):
    """A user sample whose wave is `frames` long, played up to `end`, with a loop inside it."""
    fake = window.fake
    audio = [(i * 13) % 2000 - 1000 for i in range(frames)]
    fake.add_sample(name, audio=audio, audio_right=[-w for w in audio] if right else None)
    put(fake, name, loop_mode=mode, wave_end_address=end, wave_length=end, loop_start_address=loop[0], loop_end_address=loop[1],
        loop_length=loop[1] - loop[0])
    window.samples_tab.set_samples(list(fake.samples))
    tab = load(window, name)
    assert wait_until(lambda: tab._audio_name == name)
    return tab


def unit(window, name="UM"):
    return ym.read_markers(window.fake.samples[name])


def settled(window, expected, name="UM"):
    tab = window.samples_tab
    return wait_until(lambda: tab._marker_job is None and tab._marker_pending is None and unit(window, name) == expected, timeout=10)


def drag(view, name, frame):
    """What a drag-and-release does to a WaveformView."""
    view.set_marker(name, frame)
    view.marker_committed.emit(name, *[view.markers()[k] for k in ("start", "loop_start", "loop_end", "end")])


# --- the fake unit's rules ------------------------------------------------------------------------------------


def write(fake, name, key, value):
    fake.current = (y.OBJECT_TYPES["sample"], name)
    row = yp.get("sample", key)
    fake._edit(y.build_object_edit(fake.device, row.p, y.encode_value(value, row.size)))


def make(mode=1):
    fake = demo.FakeA4000()
    fake.add_sample("UM", audio=[1] * 4000)
    put(fake, "UM", loop_mode=mode, wave_end_address=3000, wave_length=3000, loop_start_address=1000, loop_end_address=2000, loop_length=1000)
    return fake


def test_the_fake_ignores_a_write_that_would_break_the_order():
    fake = make()
    for key, value in (("wave_start_address", 1500), ("loop_start_address", 2500), ("loop_end_address", 500), ("loop_end_address", 3500),
                       ("wave_end_address", 1500), ("wave_end_address", 4500)):
        before = ym.read_markers(fake.samples["UM"])
        write(fake, "UM", key, value)
        assert ym.read_markers(fake.samples["UM"]) == before, (key, value)


def test_the_fake_keeps_the_lengths_in_step_with_the_addresses():
    fake = make()
    g = lambda k: yp.extract(yp.get("sample", k), fake.samples["UM"])  # noqa: E731
    write(fake, "UM", "wave_start_address", 100)
    assert (g("wave_start_address"), g("wave_end_address"), g("wave_length")) == (100, 3000, 2900)
    write(fake, "UM", "loop_start_address", 1200)
    assert (g("loop_end_address"), g("loop_length")) == (2000, 800)
    write(fake, "UM", "loop_end_address", 2600)
    assert (g("loop_start_address"), g("loop_length")) == (1200, 1400)
    write(fake, "UM", "wave_end_address", 3500)
    assert (g("wave_start_address"), g("wave_length")) == (100, 3400)
    write(fake, "UM", "loop_length", 600)  # a loop length write moves the loop END
    assert (g("loop_start_address"), g("loop_end_address")) == (1200, 1800)


def test_the_loop_end_follows_the_wave_end_while_nothing_loops():
    fake = make(mode=0)
    write(fake, "UM", "wave_end_address", 3500)
    assert ym.read_markers(fake.samples["UM"]).loop_end == 3500
    write(fake, "UM", "loop_start_address", 3500)  # (so, as measured, a loop start can sit at the very end)
    assert ym.read_markers(fake.samples["UM"]).loop_start == 3500


def test_a_built_in_waveform_ignores_a_new_end_but_takes_the_rest():
    fake = demo.FakeA4000()
    for key, value in (("wave_end_address", 64), ("wave_length", 64)):
        write(fake, "sine wave", key, value)
    before = ym.read_markers(fake.samples["sine wave"])
    assert before.end == 128
    write(fake, "sine wave", "wave_start_address", 0)
    write(fake, "sine wave", "loop_start_address", 10)
    assert ym.read_markers(fake.samples["sine wave"]).loop_start == 10


# --- dragging markers -------------------------------------------------------------------------------------------


def test_the_markers_are_editable_and_start_where_the_unit_has_them(window):
    tab = user_sample(window)
    assert not tab.waveform_view._markers_locked and not tab.waveform_view_right._markers_locked
    assert tab.waveform_view.markers() == {"start": 0, "loop_start": 1000, "loop_end": 1999, "end": 2999}  # END markers: last frame


def test_dragging_a_loop_marker_writes_that_address_and_nothing_else(window):
    tab = user_sample(window)
    edits = window.fake.edits
    drag(tab.waveform_view, "loop_start", 400)
    assert settled(window, ym.Markers(0, 400, 2000, 3000))
    assert window.fake.edits - edits == 1
    assert tab._cache["UM"] is not None and ym.read_markers(tab._cache["UM"]) == ym.Markers(0, 400, 2000, 3000)


def test_dragging_the_end_marker_past_the_loop_pushes_the_loop_and_writes_in_a_safe_order(window):
    tab = user_sample(window)
    drag(tab.waveform_view, "end", 1500)  # pulls the loop end (and with it the loop) in with it
    assert settled(window, ym.Markers(0, 1000, 1501, 1501))
    assert tab.waveform_view.markers() == {"start": 0, "loop_start": 1000, "loop_end": 1500, "end": 1500}


def test_a_drag_that_needs_several_writes_goes_out_in_the_order_the_unit_accepts(window):
    tab = user_sample(window, loop=(1000, 3000))  # the loop reaches the end
    drag(tab.waveform_view, "start", 1200)  # start above the loop start: the loop start has to move first
    assert wait_until(lambda: unit(window).start == 1200 and tab._marker_job is None, timeout=10)
    assert unit(window).valid and unit(window).loop_start >= 1200
    assert not window.fake.ignored_ops


def test_the_wave_end_can_be_moved_back_out_to_the_whole_wave(window):
    tab = user_sample(window, end=2000, loop=(500, 1500))
    drag(tab.waveform_view, "end", 3999)
    assert settled(window, ym.Markers(0, 500, 1500, 4000))


def test_a_non_looping_sample_keeps_its_own_loop_values(window):
    tab = user_sample(window, mode=0, end=3000, loop=(1000, 2000))
    assert not tab.waveform_view._loop_enabled
    drag(tab.waveform_view, "start", 300)
    assert wait_until(lambda: unit(window).start == 300 and tab._marker_job is None, timeout=10)
    assert unit(window).loop_start == 1000 and unit(window).loop_end == 2000  # nothing was written to the loop rows


def test_a_non_looping_end_keeps_its_loop_valid(window):
    tab = user_sample(window, mode=0, end=3000, loop=(2500, 2800))
    drag(tab.waveform_view, "end", 1999)  # the (unused) loop would end up outside the wave: it is pulled in first
    assert wait_until(lambda: unit(window).end == 2000 and tab._marker_job is None, timeout=10)
    assert unit(window).valid


def test_the_built_in_waveforms_end_cannot_move_and_the_marker_goes_back(window):
    tab = load(window, "sine wave")
    assert wait_until(lambda: tab._audio_name == "sine wave")
    drag(tab.waveform_view, "end", 63)
    assert wait_until(lambda: "built-in" in window.status_bar.currentMessage(), timeout=10)
    assert wait_until(lambda: tab.waveform_view.markers()["end"] == 127, timeout=10)
    assert unit(window, "sine wave").end == 128


def test_a_drag_made_while_another_is_being_written_is_applied_afterwards(window):
    tab = user_sample(window)
    window.midi.paced = True
    drag(tab.waveform_view, "loop_start", 300)
    drag(tab.waveform_view, "loop_end", 2600)
    assert settled(window, ym.Markers(0, 300, 2601, 3000))


def test_markers_are_locked_while_the_audio_arrives_and_unlocked_after(window):
    window.fake.add_sample("long", audio=[(i * 7) % 20000 - 10000 for i in range(20000)])
    window.samples_tab.set_samples(list(window.fake.samples))
    window.midi.paced = True
    tab = window.samples_tab
    tab.select_sample("long")
    assert wait_until(lambda: tab._selected == "long" and "long" in tab._cache and not tab.cards_scroll.isHidden())
    tab._load_audio()
    assert tab.waveform_view._markers_locked
    assert wait_until(lambda: tab._audio_name == "long")
    assert not tab.waveform_view._markers_locked


def test_a_view_only_tab_keeps_its_markers_locked(qapp):  # noqa: F811
    from test_yamaha_program_editor import build_window, dispose

    window = build_window(qapp, demo.FakeA4000())
    try:
        tab = window.samples_tab
        assert tab._writer is not None
        tab._writer = None
        tab._apply_marker_lock()
        assert tab.waveform_view._markers_locked and tab.waveform_view_right._markers_locked
    finally:
        dispose(window)


# --- a stereo sample: one set of markers, shown on both channels ------------------------------------------------


def test_the_two_channels_show_the_same_markers_and_a_drag_in_either_moves_both(window):
    tab = user_sample(window, right=True)
    left, right = tab.waveform_view, tab.waveform_view_right
    assert left.markers() == right.markers()
    left.set_marker("loop_end", 2400)
    assert right.markers() == left.markers()  # mirrored live, while dragging
    drag(right, "loop_start", 700)  # ... and from the right one
    assert left.markers() == right.markers() and left.markers()["loop_start"] == 700
    assert settled(window, ym.Markers(0, 700, 2401, 3000))
    assert left.markers() == right.markers()


# --- click to preview -----------------------------------------------------------------------------------------------


class Recorder:
    def __init__(self, tab):
        self.calls = []
        self.playing = False
        tab._preview.play = lambda *a, **k: self.start(("play", a, k))
        tab._preview.play_loop = lambda *a, **k: self.start(("play_loop", a, k))
        tab._preview.is_playing = lambda: self.playing
        tab._preview.stop = self.stop

    def start(self, call):
        self.calls.append(call)
        self.playing = True

    def stop(self):
        self.calls.append(("stop",))
        self.playing = False


def test_clicking_plays_the_sample_per_its_loop_mode_and_clicking_again_stops_it(window):
    tab = user_sample(window, mode=1)
    rec = Recorder(tab)
    tab.waveform_view.preview_requested.emit()
    kind, args, kwargs = rec.calls[-1]
    assert kind == "play_loop" and args[1:5] == (0, 1000, 1999, 2999) and kwargs["dwell_ms"] is None  # held until the next click
    assert kwargs["right_samples"] is None
    rec.calls.clear()
    tab.waveform_view.preview_requested.emit()
    assert rec.calls == [("stop",)]


def test_a_loop_to_release_sample_holds_its_loop_for_a_while(window):
    tab = user_sample(window, mode=2)
    rec = Recorder(tab)
    tab.waveform_view.preview_requested.emit()
    assert rec.calls[-1][0] == "play_loop" and rec.calls[-1][2]["dwell_ms"] == tab_ms()


def tab_ms():
    from ui import yamaha_samples_tab

    return yamaha_samples_tab._RELEASE_PREVIEW_MS


@pytest.mark.parametrize("mode", [0, 4])
def test_a_one_shot_or_unlooped_sample_plays_start_to_end_once(window, mode):
    tab = user_sample(window, mode=mode)
    rec = Recorder(tab)
    tab.waveform_view.preview_requested.emit()
    kind, args, kwargs = rec.calls[-1]
    assert kind == "play" and args[1:3] == (0, 2999) and args[3] == 48000


@pytest.mark.parametrize("mode", [3, 5])
def test_a_reversed_sample_plays_a_reversed_copy_and_the_playhead_is_mapped_back(window, mode):
    tab = user_sample(window, mode=mode)
    rec = Recorder(tab)
    tab.waveform_view.preview_requested.emit()
    kind, args, kwargs = rec.calls[-1]
    audio = tab._audio_samples
    assert kind == "play" and list(args[0]) == audio[::-1] and args[1:3] == (4000 - 1 - 2999, 4000 - 1)
    tab._on_preview_position(4000 - 1 - 100)  # 100 frames from the start of the reversed copy's end = frame 100
    assert tab.waveform_view._playhead_frame == 100


def test_a_stereo_sample_is_previewed_in_stereo(window):
    tab = user_sample(window, mode=0, right=True)
    rec = Recorder(tab)
    tab.waveform_view_right.preview_requested.emit()  # a click on either channel
    kind, args, kwargs = rec.calls[-1]
    assert kwargs["right_samples"] == tab._audio_samples_right and list(args[0]) == tab._audio_samples


def test_nothing_plays_until_the_audio_is_loaded_and_it_stops_when_the_sample_changes(window):
    tab = window.samples_tab
    tab.select_sample("saw up")
    assert wait_until(lambda: tab._selected == "saw up" and "saw up" in tab._cache and not tab.cards_scroll.isHidden())
    rec = Recorder(tab)
    tab.waveform_view.preview_requested.emit()
    assert rec.calls == []
    tab = load(window, "saw up")
    assert wait_until(lambda: tab._audio_name == "saw up")
    rec = Recorder(tab)
    tab.waveform_view.preview_requested.emit()
    assert rec.calls[-1][0] == "play_loop"
    tab.select_sample("pulse 1")
    assert ("stop",) in rec.calls


def test_dragging_a_loop_marker_while_a_looped_preview_plays_moves_the_loop_live(window):
    tab = user_sample(window, mode=1)
    live = []
    tab._preview.is_playing = lambda: True
    tab._preview.update_loop_points = lambda a, b: live.append((a, b))
    tab.waveform_view.set_marker("loop_start", 500)
    assert live[-1][0] == 500


# --- the player itself: real two-channel audio ------------------------------------------------------------------


class FakeDevice:
    def __init__(self):
        self.generator = None

    def start(self, generator):
        self.generator = generator

    def stop(self):
        pass

    def close(self):
        pass


def run_generator(player, monkeypatch, call):
    opened = []
    device = FakeDevice()

    def open_device(framerate, channels=1):
        opened.append((framerate, channels))
        return device

    monkeypatch.setattr(player, "_open_device", open_device)
    call()
    return opened, device.generator  # already primed by the player: the next send() is the device's first request


def test_pack_values_interleaves_two_channels():
    assert audio_preview._pack_values([1, 2, 3]) == struct.pack("<3h", 1, 2, 3)
    assert audio_preview._pack_values([1, 2, 3], [-1, -2, -3]) == struct.pack("<6h", 1, -1, 2, -2, 3, -3)
    assert audio_preview._pack_values([1, 2, 3], [9]) == struct.pack("<2h", 1, 9)  # the shorter channel decides


def test_the_player_opens_a_stereo_device_and_plays_interleaved_frames(qapp, monkeypatch):  # noqa: F811
    player = audio_preview.SlicePreviewPlayer()
    left, right = [10, 20, 30, 40], [-10, -20, -30, -40]
    opened, gen = run_generator(player, monkeypatch, lambda: player.play(left, 1, 3, 22050, right_samples=right))
    assert opened == [(22050, 2)]
    out = gen.send(3)  # the device asks for 3 frames
    assert out == struct.pack("<6h", 20, -20, 30, -30, 40, -40)
    player.stop()


def test_a_mono_play_still_asks_for_the_one_argument_open_and_one_channel(qapp, monkeypatch):  # noqa: F811
    player = audio_preview.SlicePreviewPlayer()
    seen = []
    device = FakeDevice()
    monkeypatch.setattr(player, "_open_device", lambda framerate: seen.append(framerate) or device)
    player.play([1, 2, 3], 0, 2, 11025)
    assert seen == [11025]
    player.stop()


def test_a_stereo_loop_preview_plays_attack_then_the_loop_in_both_channels(qapp, monkeypatch):  # noqa: F811
    player = audio_preview.SlicePreviewPlayer()
    left, right = list(range(10)), [-v for v in range(10)]
    opened, gen = run_generator(
        player, monkeypatch, lambda: player.play_loop(left, 0, 2, 4, 9, 22050, dwell_ms=None, right_samples=right)
    )
    assert opened == [(22050, 2)]
    out = gen.send(8)  # attack = frames 0-4, then the loop region 2-4 begins
    frames = struct.unpack("<16h", out)
    assert frames[0:10:2] == (0, 1, 2, 3, 4) and frames[1:10:2] == (0, -1, -2, -3, -4)
    assert frames[10::2] == (2, 3, 4) and frames[11::2] == (-2, -3, -4)
    player.stop()
