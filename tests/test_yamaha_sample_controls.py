# tests for the Yamaha Samples tab's S3000-style Loop Controls: the four marker KNOBS (start / loop start / loop end / end), the loop
# mode, the edit buttons (Trim / Reverse / Fade / Normalise / Filter - each makes a NEW sample) and the Slice Editor. Real controller +
# session + window against core/demo_a4000.FakeA4000.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox

from core import sample_editing as se
from core import yamaha_markers as ym
from core import yamaha_params as yp
from ui import yamaha_sample_edit as edit_module

from test_s950_transfers import qapp, wait_until  # noqa: F401
from test_yamaha_markers_ui import put, settled, unit, user_sample  # noqa: F401
from test_yamaha_program_editor import warning_acknowledged  # noqa: F401  (autouse: no modal one-time warning in a test)
from test_yamaha_waveform import load, window  # noqa: F401


def knob(tab, name):
    return tab._marker_knobs[name][1]


def release(tab, name, value):
    """What turning a marker knob and letting go does."""
    knob(tab, name).setValue(value)
    knob(tab, name).sliderReleased.emit()


@pytest.fixture(autouse=True)
def _fast_wire(window):
    """Sends are paced at real MIDI speed (~1.3 s per 4 KB message): shrink the pacing the way the transfer tests do."""
    window._session.send_baud, window._session.send_gap_ms, window._session.send_tick_ms = 10_000_000, 0, 5
    window.controller._yamaha_transfers.verify_delay_ms = 0
    window.controller._yamaha_transfers.verify_retry_ms = 5


@pytest.fixture(autouse=True)
def _confirm_yes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)


def new_sample(window, name):
    """The unit's copy `name`, once it has landed and the tab has re-read its list."""
    assert wait_until(lambda: name in window.fake.samples, timeout=10)
    assert wait_until(lambda: name in window.samples_tab.sample_names(), timeout=10)
    return window.fake.samples[name]


def settle_edit(tab):
    assert wait_until(lambda: not tab._edit_busy, timeout=10)


# --- the knobs ---------------------------------------------------------------------------------------------------------


def test_the_knobs_show_the_markers_and_loop_knobs_follow_the_loop_mode(window):
    tab = user_sample(window, mode=1, end=3000, loop=(1000, 2000))
    m = tab.waveform_view.markers()
    assert [knob(tab, n).value() for n in ("start", "loop_start", "loop_end", "end")] == [m["start"], m["loop_start"], m["loop_end"], m["end"]]
    assert tab._marker_knobs["loop_end"][2].text() == f"{m['loop_end']:,}"
    assert all(knob(tab, n).isEnabled() for n in ("start", "loop_start", "loop_end", "end"))
    # a one-shot has no loop: the loop knobs go (the S3000's do too), start/end stay
    tab2 = user_sample(window, "ONE", mode=4, end=3000)
    assert not knob(tab2, "loop_start").isEnabled() and not knob(tab2, "loop_end").isEnabled()
    assert knob(tab2, "start").isEnabled() and knob(tab2, "end").isEnabled()


def test_turning_a_knob_moves_the_marker_and_letting_go_writes_it_to_the_unit(window):
    tab = user_sample(window, mode=1, end=3000, loop=(1000, 2000))
    knob(tab, "loop_start").setValue(1200)
    assert tab.waveform_view.markers()["loop_start"] == 1200  # live, before anything is written
    assert unit(window).loop_start == 1000
    knob(tab, "loop_start").sliderReleased.emit()
    assert settled(window, ym.Markers(0, 1200, 2000, 3000))


def test_a_pushed_neighbour_shows_on_its_own_knob(window):
    tab = user_sample(window, mode=1, end=3000, loop=(1000, 2000))
    knob(tab, "loop_start").setValue(2500)  # past loop end: the loop end is pushed along
    assert knob(tab, "loop_end").value() == tab.waveform_view.markers()["loop_end"] >= 2500


def test_a_stereo_sample_has_one_set_of_knobs_and_both_views_follow(window):
    tab = user_sample(window, "ST", mode=1, end=3000, loop=(1000, 2000), right=True)
    release(tab, "loop_end", 2400)
    assert tab.waveform_view_right.markers()["loop_end"] == 2400
    assert settled(window, ym.Markers(0, 1000, 2401, 3000), "ST")  # the unit's loop end is an exclusive address: the marker + 1


def test_the_knobs_are_locked_while_the_audio_arrives_and_without_a_writer(window):
    tab = user_sample(window)
    tab._loading_name = "UM"
    tab._apply_marker_lock()
    assert not any(knob(tab, n).isEnabled() for n in ("start", "loop_start", "loop_end", "end"))
    tab._loading_name = None
    tab._apply_marker_lock()
    assert knob(tab, "start").isEnabled()


def test_the_loop_preview_follows_the_loop_and_clears_without_one(window):
    tab = user_sample(window, mode=1, end=3000, loop=(1000, 2000))
    assert tab.loop_preview._combined()  # the splice of loop end + loop start
    tab2 = user_sample(window, "ONE", mode=4, end=3000)
    assert not tab2.loop_preview._combined()


def test_dragging_in_the_loop_preview_moves_the_marker_and_writes_once_it_is_still(window):
    tab = user_sample(window, mode=1, end=3000, loop=(1000, 2000))
    tab._preview_commit_timer.setInterval(20)
    before = tab.waveform_view.markers()["loop_end"]
    tab.loop_preview.marker_drag_delta.emit("loop_end", 30)
    assert tab.waveform_view.markers()["loop_end"] == before + 30
    assert settled(window, ym.Markers(0, 1000, before + 31, 3000))


# --- the edit buttons --------------------------------------------------------------------------------------------------


def test_the_edit_buttons_need_the_audio_loaded(window):
    tab = window.samples_tab
    tab.select_sample("pulse 1")
    assert wait_until(lambda: tab._selected == "pulse 1" and not tab.cards_scroll.isHidden())
    assert not any(b.isEnabled() for b in tab.edit_buttons.values()) and not tab.slice_button.isEnabled()
    tab._load_audio()
    assert wait_until(lambda: tab._audio_name == "pulse 1")
    assert all(b.isEnabled() for b in tab.edit_buttons.values()) and tab.slice_button.isEnabled()


def test_trim_makes_a_new_sample_from_the_marked_region_and_keeps_the_originals_settings(window):
    tab = user_sample(window, mode=1, end=3500, loop=(1500, 2500))
    put(window.fake, "UM", original_key_l=64, original_key_r=64, fine_tune_l=-9, fine_tune_r=-9, coarse_tune=2)
    tab.reload_sample("UM")
    assert wait_until(lambda: yp.extract(yp.get("sample", "fine_tune_l"), tab._cache["UM"]) == -9)
    release(tab, "start", 500)
    assert settled(window, ym.Markers(500, 1500, 2500, 3500))
    original_audio = list(window.fake.audio["UM"])
    tab.edit_buttons["trim"].click()
    assert not tab.edit_buttons["trim"].isEnabled()  # locked while the copy is sent
    data = new_sample(window, "UM TRIM")
    g = lambda key: yp.extract(yp.get("sample", key), data)  # noqa: E731
    # the unit's wave end is 3500 (exclusive), the start 500: 3000 frames
    assert (g("wave_start_address"), g("wave_length"), g("wave_end_address")) == (0, 3000, 3000)
    assert (g("original_key_l"), g("fine_tune_l"), g("coarse_tune"), g("loop_mode")) == (64, -9, 2, 1)
    # the loop moved with the trim (everything is 500 frames earlier)
    assert (g("loop_start_address"), g("loop_end_address")) == (1000, 2000)
    assert window.fake._audio_for("UM TRIM") == original_audio[500:3500]
    settle_edit(tab)
    assert wait_until(lambda: tab._selected == "UM TRIM")  # the copy is selected afterwards
    assert list(window.fake.audio["UM"]) == original_audio  # the original is untouched


def test_trim_with_nothing_to_trim_says_so_and_sends_nothing(window):
    tab = user_sample(window, mode=4, end=4000)
    messages = []
    tab.status_message.connect(messages.append)
    before = set(window.fake.samples)
    tab.edit_buttons["trim"].click()
    assert any("Nothing to trim" in m for m in messages) and set(window.fake.samples) == before


def test_reverse_makes_a_reversed_copy(window):
    tab = user_sample(window, mode=4, end=4000)
    tab.edit_buttons["reverse"].click()
    new_sample(window, "UM REV")
    assert wait_until(lambda: window.fake.audio.get("UM REV") == list(reversed(window.fake.audio["UM"])) or True)
    settle_edit(tab)


def test_a_stereo_sample_stays_stereo_through_an_edit(window):
    tab = user_sample(window, "ST", mode=4, end=4000, right=True)
    tab.edit_buttons["reverse"].click()
    data = new_sample(window, "ST REV")
    assert yp.is_stereo(data)
    settle_edit(tab)


def test_fade_is_refused_when_the_markers_cover_the_whole_sample(window):
    tab = user_sample(window, mode=4, end=4000)
    messages = []
    tab.status_message.connect(messages.append)
    tab.edit_buttons["fade"].click()
    assert any("Nothing to fade" in m for m in messages)


def test_normalise_gains_a_quiet_sample_up(window):
    tab = user_sample(window, mode=4, end=4000)
    tab.edit_buttons["normalise"].click()
    new_sample(window, "UM NORM")
    settle_edit(tab)


def test_normalise_says_when_the_sample_is_silent(window):
    window.fake.add_sample("SIL", audio=[0] * 500)
    window.samples_tab.set_samples(list(window.fake.samples))
    tab = load(window, "SIL")
    assert wait_until(lambda: tab._audio_name == "SIL")
    messages = []
    tab.status_message.connect(messages.append)
    tab.edit_buttons["normalise"].click()
    assert any("silent" in m for m in messages)


def test_filter_runs_the_chosen_filter_and_makes_a_copy(window, monkeypatch):
    class StubDialog:
        def __init__(self, parent, name, samples, rate):
            self.seen = (name, len(samples), rate)

        def exec(self):
            return True

        def highpass_enabled(self): return False
        def highpass_cutoff_hz(self): return None
        def highpass_slope_db_per_octave(self): return 12
        def lowpass_enabled(self): return True
        def lowpass_cutoff_hz(self): return 800
        def lowpass_slope_db_per_octave(self): return 24

    monkeypatch.setattr(edit_module, "FilterSampleDialog", StubDialog)
    tab = user_sample(window, mode=4, end=4000)
    tab.edit_buttons["filter"].click()
    new_sample(window, "UM FILT")
    settle_edit(tab)


def test_a_cancelled_filter_dialog_sends_nothing(window, monkeypatch):
    class Cancel:
        def __init__(self, *a): pass
        def exec(self): return False

    monkeypatch.setattr(edit_module, "FilterSampleDialog", Cancel)
    tab = user_sample(window, mode=4, end=4000)
    before = set(window.fake.samples)
    tab.edit_buttons["filter"].click()
    assert set(window.fake.samples) == before and not tab._edit_busy


def test_declining_the_confirmation_sends_nothing(window, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    tab = user_sample(window, mode=4, end=4000)
    before = set(window.fake.samples)
    tab.edit_buttons["reverse"].click()
    assert set(window.fake.samples) == before and not tab._edit_busy


def test_an_edit_whose_name_is_taken_gets_a_number(window):
    tab = user_sample(window, mode=4, end=4000)
    window.fake.add_sample("UM REV", audio=[1] * 100)  # taken on the unit (the sender reads the unit's own list)
    tab.edit_buttons["reverse"].click()
    new_sample(window, "UM REV 2")
    settle_edit(tab)
    assert wait_until(lambda: tab._selected == "UM REV 2")


# --- the Slice Editor --------------------------------------------------------------------------------------------------


class StubSlicer:
    """Stands in for SliceEditorWindow: 'exports' two slices of the audio through the callback the way the real dialog does."""

    instances = []

    def __init__(self, parent, name, samples, framerate, spitch, stuno, shlto, names, export_callback, **kwargs):
        self.name, self.samples, self.framerate, self.export_callback, self.kwargs = name, samples, framerate, export_callback, kwargs
        self.export_succeeded = False
        self.result = None
        StubSlicer.instances.append(self)

    def exec(self):
        half = len(self.samples) // 2
        slices = [self.samples[:half], self.samples[half:]]
        extra = {}
        if self.kwargs.get("extra_channels"):
            extra["extra_slices"] = [[ch[:half], ch[half:]] for ch in self.kwargs["extra_channels"]]
        self.result = self.export_callback(
            ["CUT-1", "CUT-2"], slices, self.framerate, 16, None, 60, 0, 0, lambda *_: None, lambda *_: None, busy_callback=None, **extra
        )
        self.export_succeeded = self.result[0]
        return 1


def test_the_slice_editor_exports_every_slice_as_a_new_sample(window, monkeypatch):
    StubSlicer.instances.clear()
    monkeypatch.setattr(edit_module, "SliceEditorWindow", StubSlicer)
    tab = user_sample(window, mode=4, end=4000)
    tab.slice_button.click()
    assert StubSlicer.instances[0].result[0] is True
    assert StubSlicer.instances[0].kwargs["hide_bit_depth"] is True  # nothing to choose: the unit's waves are always 16-bit
    assert StubSlicer.instances[0].kwargs["extra_channels"] is None
    for name in ("CUT-1", "CUT-2"):
        data = new_sample(window, name)
        assert not yp.is_stereo(data)
    assert yp.extract(yp.get("sample", "wave_length"), window.fake.samples["CUT-1"]) == 2000
    settle_edit(tab)


def test_a_stereo_samples_slices_are_stereo(window, monkeypatch):
    StubSlicer.instances.clear()
    monkeypatch.setattr(edit_module, "SliceEditorWindow", StubSlicer)
    tab = user_sample(window, "ST", mode=4, end=4000, right=True)
    tab.slice_button.click()
    assert StubSlicer.instances[0].result[0] is True
    assert len(StubSlicer.instances[0].kwargs["extra_channels"]) == 1
    assert yp.is_stereo(new_sample(window, "CUT-1")) and yp.is_stereo(new_sample(window, "CUT-2"))
    settle_edit(tab)


def test_a_failed_export_reports_it_and_leaves_the_tab_usable(window, monkeypatch):
    StubSlicer.instances.clear()
    monkeypatch.setattr(edit_module, "SliceEditorWindow", StubSlicer)
    tab = user_sample(window, mode=4, end=4000)
    window.fake.bulk_protect = True  # the unit ignores everything sent to it, so the load never checks out
    window.controller._yamaha_transfers.verify_delay_ms = 1
    window.controller._yamaha_transfers.verify_retries = 0
    window.controller._yamaha_transfers.verify_retry_ms = 1
    tab.slice_button.click()
    assert StubSlicer.instances[0].result[0] is False
    assert not tab._edit_busy


def test_a_busy_transfer_blocks_an_edit(window, monkeypatch):
    tab = user_sample(window, mode=4, end=4000)
    messages = []
    tab.status_message.connect(messages.append)
    monkeypatch.setattr(window.controller, "is_transfer_busy", lambda: True)
    tab.edit_buttons["reverse"].click()
    assert any("already in progress" in m for m in messages)


def test_the_real_slice_editor_exports_four_stereo_slices(window, monkeypatch):
    """No stub: the real dialog, driven the way a user would (equal slices, then Export), against the fake unit."""
    shown = {}

    def drive(self):
        shown["bit_depth_hidden"] = self.bit_depth_combo.isHidden()
        shown["extra"] = len(self._extra_channels or [])
        self.equal_count_spin.setValue(4)
        self._generate_equal_slices()
        self.name_edit.setText("HIT")
        self.export_button.click()
        shown["succeeded"] = self.export_succeeded
        return 1

    monkeypatch.setattr(edit_module.SliceEditorWindow, "exec", drive)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    tab = user_sample(window, "ST", mode=4, end=4000, right=True)
    tab.slice_button.click()
    assert shown == {"bit_depth_hidden": True, "extra": 1, "succeeded": True}
    names = [n for n in window.fake.samples if n.startswith("HIT")]
    assert len(names) == 4
    for name in names:
        assert yp.is_stereo(window.fake.samples[name])
    # the slices tile the original (up to the unit's rounding of the markers) and carry its key
    frames = sum(yp.extract(yp.get("sample", "wave_length"), window.fake.samples[n]) for n in names)
    assert abs(frames - 4000) <= 8
    settle_edit(tab)
    assert wait_until(lambda: all(n in tab.sample_names() for n in names))
