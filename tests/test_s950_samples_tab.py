# tests for ui/s950_samples_tab.py - the read-only Samples tab of the S900/S950 editor.
#
# The real tab and the real SamplerController/S950Transfers run against core/demo_s950.FakeS950
# over the same fake MidiManager test_s950_transfers.py uses (offscreen Qt, shrunk waits).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QLabel

from core import s950_sysex as s
from core.demo_s950 import FakeS950, make_sample
from ui import s950_samples_tab as st
from ui.s950_samples_tab import S950SamplesTab

from test_s950_transfers import _build, qapp, wait_until  # noqa: F401


# --- pure helpers -------------------------------------------------------------------------------


def test_replay_mode_names():
    assert st.format_replay_mode(s.REPLAY_ONE_SHOT) == "One-shot"
    assert st.format_replay_mode(s.REPLAY_LOOP) == "Loop"
    assert st.format_replay_mode(s.REPLAY_ALTERNATING) == "Alternating loop"
    assert "unknown" in st.format_replay_mode(0)


def test_duration_and_length_text():
    assert st.format_duration(22050, 22050) == "1.00s"
    assert st.format_duration(100, 0) == ""
    assert st.format_length(44100, 22050) == "44,100 words (2.00s)"
    assert st.format_length(10, 0) == "10 words"


def params(**kw):
    base = dict(total_words=1000, sample_rate_hz=22050, start=0, end=999, loop_length=0,
                replay_mode=s.REPLAY_ONE_SHOT)
    base.update(kw)
    return s.SampleParams(**base)


def test_a_one_shot_has_no_loop_and_its_loop_markers_sit_at_the_end():
    start, loop_start, loop_end, end, loops = st.sample_markers(params())
    assert (start, end, loops) == (0, 999, False)
    assert loop_start == loop_end == 999


def test_a_loop_runs_backwards_from_the_end_by_its_length():
    start, loop_start, loop_end, end, loops = st.sample_markers(
        params(replay_mode=s.REPLAY_LOOP, start=100, end=900, loop_length=300)
    )
    assert (start, loop_start, loop_end, end, loops) == (100, 600, 900, 900, True)


def test_markers_are_clamped_into_the_sample():
    start, loop_start, loop_end, end, _ = st.sample_markers(
        params(replay_mode=s.REPLAY_ALTERNATING, start=5000, end=100, loop_length=99999, total_words=1000)
    )
    assert 0 <= start <= loop_start <= loop_end <= end <= 999
    start, loop_start, *_ = st.sample_markers(
        params(replay_mode=s.REPLAY_LOOP, start=50, end=900, loop_length=99999)
    )
    assert loop_start == 50  # a loop longer than the sample starts at the sample start


def test_an_empty_sample_does_not_crash_the_markers():
    assert st.sample_markers(params(total_words=0, end=0))[:4] == (0, 0, 0, 0)


# --- the tab --------------------------------------------------------------------------------------


@pytest.fixture
def tab(qapp, monkeypatch):
    rig = _build(qapp, monkeypatch, FakeS950())
    widget = S950SamplesTab(rig.controller)
    widget.rig = rig
    yield widget
    rig.engine.cancel()
    widget.disconnect_controller()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def _catalog(tab):
    tab.rig.controller.refresh_sample_list(silent=True)
    assert wait_until(lambda: tab.sample_list_widget.count() == 3)


def _texts(tab, slot):
    name, duration = tab._rows[slot]
    return name.text(), duration.text()


def test_the_list_shows_the_catalogs_samples(tab):
    _catalog(tab)
    assert [_texts(tab, slot)[0] for slot in (0, 1, 2)] == ["KICK", "SNARE", "PAD"]
    # plain QListWidgetItems with a row widget carry no text of their own
    assert tab.sample_list_widget.item(0).text() == ""
    assert tab.sample_list_widget.item(0).data(Qt.ItemDataRole.UserRole) == 0


def test_nothing_is_read_while_the_tab_is_not_on_screen(tab):
    _catalog(tab)
    QCoreApplication.processEvents()
    assert not [m for m in tab.rig.midi.sent if m[2] == s.FUNC_RSPRM]
    assert _texts(tab, 1)[1] == ""


def test_durations_fill_in_once_the_tab_is_active(tab):
    _catalog(tab)
    tab.set_active(True)
    assert wait_until(lambda: all(_texts(tab, slot)[1] for slot in (0, 1, 2)))
    assert _texts(tab, 1)[1] == st.format_duration(4000, 22050)
    assert _texts(tab, 2)[1] == st.format_duration(9000, 44100)
    # one SPRM per sample, sequentially, and the wire is free afterwards
    assert len([m for m in tab.rig.midi.sent if m[2] == s.FUNC_RSPRM]) == 3
    assert tab.rig.engine.idle


def test_leaving_the_tab_stops_the_background_reads(tab):
    _catalog(tab)
    tab.set_active(True)
    tab.set_active(False)
    wait_until(lambda: tab._inflight is None)
    reads = len([m for m in tab.rig.midi.sent if m[2] == s.FUNC_RSPRM])
    QCoreApplication.processEvents()
    assert len([m for m in tab.rig.midi.sent if m[2] == s.FUNC_RSPRM]) == reads
    assert reads < 3 or all(_texts(tab, slot)[1] for slot in (0, 1, 2))


def test_the_selected_sample_is_read_first(tab):
    _catalog(tab)
    tab.sample_list_widget.setCurrentRow(2)
    tab.set_active(True)
    assert wait_until(lambda: tab._params.get(2) is not None)
    first_read = next(m for m in tab.rig.midi.sent if m[2] == s.FUNC_RSPRM)
    assert first_read[4] == 2  # the slot number


def test_selecting_a_sample_shows_its_parameters(tab):
    _catalog(tab)
    tab.set_active(True)
    tab.sample_list_widget.setCurrentRow(1)
    assert wait_until(lambda: tab._values["name"].text() == "SNARE")
    v = tab._values
    assert v["rate"].text() == "22,050 Hz"
    assert v["length"].text() == st.format_length(4000, 22050)
    assert v["replay"].text() == "One-shot"
    assert v["direction"].text() == "Forward"
    assert "unverified" in v["pitch"].text()
    assert tab.waveform_view.has_header() and not tab.waveform_view.has_waveform()
    assert tab.waveform_view.frame_count() == 4000


def test_the_waveform_markers_are_locked_so_nothing_can_be_edited(tab):
    assert tab.waveform_view._markers_locked is True
    assert tab.waveform_view._markers_within_hit_radius(10) == []


def test_before_a_selection_the_values_are_blank(tab):
    assert all(label.text() == "-" for label in tab._values.values())


def test_a_sample_that_cannot_be_read_says_so_and_is_not_retried(tab):
    tab.rig.engine.reply_timeout_ms = 30
    _catalog(tab)
    tab.rig.fake.samples.pop(1)  # listed, then gone: the fake stays silent
    tab.sample_list_widget.setCurrentRow(1)
    tab.set_active(True)
    assert wait_until(lambda: 1 in tab._failed and tab._inflight is None)
    assert "Couldn't read" in tab.waveform_view._placeholder_text
    reads = len([m for m in tab.rig.midi.sent if m[2] == s.FUNC_RSPRM and m[4] == 1])
    QCoreApplication.processEvents()
    assert len([m for m in tab.rig.midi.sent if m[2] == s.FUNC_RSPRM and m[4] == 1]) == reads


def test_refresh_forgets_what_was_read(tab):
    _catalog(tab)
    tab.set_active(True)
    assert wait_until(lambda: len(tab._params) == 3)
    tab.refresh()
    assert tab._params == {} and tab._failed == set()


def test_a_renamed_sample_is_read_again(tab):
    _catalog(tab)
    tab.set_active(True)
    assert wait_until(lambda: len(tab._params) == 3)
    tab.rig.fake.samples[1]["params"].name = "SNARE2"
    tab.rig.controller.refresh_sample_list(silent=True)
    assert wait_until(lambda: _texts(tab, 1)[0] == "SNARE2")
    assert wait_until(lambda: 1 in tab._params)
    assert tab._params[1].name == "SNARE2"


def test_a_sample_looping_enables_the_loop_markers(tab):
    tab.rig.fake.samples[1]["params"].replay_mode = s.REPLAY_LOOP
    tab.rig.fake.samples[1]["params"].loop_length = 500
    tab.rig.fake.samples[1]["params"].end = 3999
    _catalog(tab)
    tab.set_active(True)
    tab.sample_list_widget.setCurrentRow(1)
    assert wait_until(lambda: tab._params.get(1) is not None and tab.waveform_view.has_header())
    assert tab.waveform_view.markers()["loop_start"] == 3499
    assert tab.waveform_view._loop_enabled is True


def _wait_audio(tab, slot):
    assert wait_until(lambda: tab._audio_slot == slot and tab.waveform_view.has_waveform(), timeout=10)


def test_double_clicking_loads_the_audio_without_blocking_and_shows_the_waveform(tab):
    _catalog(tab)
    tab.set_active(True)
    tab.sample_list_widget.setCurrentRow(1)
    assert wait_until(lambda: tab._params.get(1) is not None and tab.waveform_view.has_header())
    tab.waveform_view.load_requested.emit()
    assert tab.waveform_view._loading is True  # asynchronous: we are back at once
    _wait_audio(tab, 1)
    assert tab.waveform_view.frame_count() == 4000
    assert tab.waveform_view._loading is False
    assert tab._wave_path is None  # the temp file is gone
    # the engine pauses briefly after a receive (its "between" phase) before going idle
    assert wait_until(lambda: tab.rig.engine.idle)


def test_the_temp_file_is_removed_after_loading(tab):
    _catalog(tab)
    tab.set_active(True)
    tab.sample_list_widget.setCurrentRow(1)
    assert wait_until(lambda: tab._params.get(1) is not None)
    tab.waveform_view.load_requested.emit()
    path = tab._wave_path
    assert path and os.path.exists(path)
    _wait_audio(tab, 1)
    assert not os.path.exists(path)


def test_audio_for_a_sample_that_is_no_longer_selected_is_not_shown(tab):
    _catalog(tab)
    tab.set_active(True)
    tab.sample_list_widget.setCurrentRow(1)
    assert wait_until(lambda: tab._params.get(1) is not None and tab._params.get(2) is not None)
    tab.waveform_view.load_requested.emit()
    tab.sample_list_widget.setCurrentRow(2)  # moves on while the dump is in flight
    assert wait_until(lambda: tab._wave_path is None, timeout=10)
    assert tab.waveform_view.frame_count() == 9000  # sample 2's header, not sample 1's audio
    assert not tab.waveform_view.has_waveform()


def test_a_failed_audio_load_clears_the_loading_state(tab):
    _catalog(tab)
    tab.set_active(True)
    tab.sample_list_widget.setCurrentRow(1)
    assert wait_until(lambda: tab._params.get(1) is not None)
    tab.rig.fake.samples.pop(1)  # the RSPRM inside the receive gets no answer
    tab.rig.engine.reply_timeout_ms = 30
    path_before = None
    tab.waveform_view.load_requested.emit()
    path_before = tab._wave_path
    assert wait_until(lambda: tab._wave_path is None)
    assert tab.waveform_view._loading is False
    assert not os.path.exists(path_before)
    assert not tab.waveform_view.has_waveform()


def test_a_load_waits_for_a_busy_wire_instead_of_failing(tab):
    _catalog(tab)
    tab.set_active(True)
    tab.sample_list_widget.setCurrentRow(1)
    assert wait_until(lambda: tab._params.get(1) is not None)
    tab.rig.controller.refresh_sample_list(silent=True)  # puts a catalog read on the wire
    assert not tab.rig.controller.is_s950_idle()
    tab.waveform_view.load_requested.emit()
    _wait_audio(tab, 1)


def test_other_receives_are_not_mistaken_for_ours(tab):
    tab._on_audio_file("/some/other/file.wav")  # the Dashboard's own receive
    assert tab._audio_slot is None


def test_disconnecting_stops_the_tab_listening(tab):
    tab.disconnect_controller()
    tab.rig.controller.sample_slots_updated.emit([(0, "X")])
    assert tab.sample_list_widget.count() == 0


def test_the_hint_changes_once_the_audio_is_loaded(tab):
    _catalog(tab)
    tab.set_active(True)
    tab.sample_list_widget.setCurrentRow(1)
    assert wait_until(lambda: tab._params.get(1) is not None and tab.waveform_view.has_header())
    assert "Double-click" in tab.waveform_hint.text()
    tab.waveform_view.load_requested.emit()
    _wait_audio(tab, 1)
    assert "can't be edited" in tab.waveform_hint.text()
