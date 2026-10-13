# "Edit in External App" / "Send Edited Audio" in the two editors: the Program Editor (S1000, S2000/S3000 - the replace is the
# send/delete/rename path of Trim/Reverse, run here in demo mode) and the Yamaha Samples tab (the edit becomes a NEW sample).
# The external app itself is stubbed: the test "saves" over the exported file.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox

from core import external_edit as ee
from core import sds_encoder
from core import yamaha_params as yp
from ui import yamaha_sample_edit as edit_module

from test_program_editor_window import (  # noqa: F401
    _pump_until,
    _stub_demo_audio,
    editor,
    qapp,
)
from test_s950_transfers import wait_until
from test_yamaha_markers_ui import user_sample
from test_yamaha_program_editor import warning_acknowledged  # noqa: F401
from test_yamaha_waveform import window  # noqa: F401


@pytest.fixture
def launched(monkeypatch, tmp_path):
    """The external app is never started; the paths it was asked to open are recorded."""
    from core import dropped_files

    monkeypatch.setattr(dropped_files, "_session_dir", tmp_path)
    opened = []
    monkeypatch.setattr(ee, "launch", lambda path, editor=None: opened.append(path))
    return opened


def _save_over(path, samples, rate=44100):
    """What the editor's Save does."""
    sds_encoder.write_wav_file(path, samples, rate, bit_depth=16)
    # a same-size save inside one timestamp tick would not look changed
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


# --- Program Editor ----------------------------------------------------------------------------------------------------


def _load_demo_audio(editor, qapp, monkeypatch, samples):
    import ui.program_editor_window as pew

    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    monkeypatch.setattr(pew, "_DEMO_MS_PER_WORD", 0.001)
    monkeypatch.setattr(pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.Yes)
    _stub_demo_audio(editor, monkeypatch, samples)
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    editor._load_sample_waveform()


def _saved(editor, path, samples, rate=44100):
    _save_over(path, samples, rate)
    editor._refresh_external_edit_buttons()


def test_the_external_buttons_follow_the_loaded_audio_and_the_saved_file(editor, qapp, monkeypatch, launched):
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    assert not editor.external_edit_button.isEnabled() and not editor.external_load_button.isEnabled()

    _load_demo_audio(editor, qapp, monkeypatch, list(range(500)))
    assert editor.external_edit_button.isEnabled() and not editor.external_load_button.isEnabled()

    editor.external_edit_button.click()
    assert len(launched) == 1 and launched[0].endswith(".wav") and os.path.exists(launched[0])
    assert not editor.external_load_button.isEnabled()  # nothing saved yet
    # the exported file is the sample's audio
    assert sds_encoder.read_wav_samples(launched[0]) == (list(range(500)), 44100)

    _saved(editor, launched[0], [5] * 500)
    assert editor.external_load_button.isEnabled()

    editor.sample_list_widget.setCurrentRow(-1)
    assert not editor.external_edit_button.isEnabled() and not editor.external_load_button.isEnabled()


def test_loading_the_edited_audio_makes_it_the_working_copy_and_sends_nothing(editor, qapp, monkeypatch, launched):
    _load_demo_audio(editor, qapp, monkeypatch, list(range(500)))
    editor.waveform_view.set_marker("start", 10)
    editor.waveform_view.set_marker("end", 400)
    editor.external_edit_button.click()
    _saved(editor, launched[0], [7] * 500)

    editor.external_load_button.click()

    entry = editor._sample_waveform_cache[0]
    assert entry["samples"] == [7] * 500 and (entry["start"], entry["end"]) == (10, 400)
    assert editor.waveform_view.has_waveform()
    # the waveform shows the edited audio; the sampler's version is remembered, not touched
    name = editor._sample_name_at_row(0)
    staged = editor._staged_edits[name]
    assert staged.samples == [7] * 500 and staged.original_samples == list(range(500))
    assert bool(editor._staged_banner.property("unsent"))
    assert not editor._sample_row_dots[0].isHidden()
    # Load waits for the next save
    assert not editor.external_load_button.isEnabled()
    _saved(editor, launched[0], [8] * 500)
    assert editor.external_load_button.isEnabled()


def test_a_shorter_edit_adjusts_the_markers_and_a_stereo_file_is_mixed_to_mono(editor, qapp, monkeypatch, launched):
    _load_demo_audio(editor, qapp, monkeypatch, list(range(500)))
    editor.external_edit_button.click()
    sds_encoder.write_wav_file_stereo(launched[0], [100] * 300, [300] * 300, 44100, bit_depth=16)
    stat = os.stat(launched[0])
    os.utime(launched[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    editor._refresh_external_edit_buttons()

    editor.external_load_button.click()

    entry = editor._sample_waveform_cache[0]
    assert entry["samples"] == [200] * 300
    assert entry["frame_count"] == 300 and entry["start"] == 0 and entry["end"] == 299


def test_a_second_load_replaces_the_working_copy_but_keeps_the_samplers_original(editor, qapp, monkeypatch, launched):
    _load_demo_audio(editor, qapp, monkeypatch, list(range(500)))
    editor.external_edit_button.click()
    _saved(editor, launched[0], [7] * 500)
    editor.external_load_button.click()
    _saved(editor, launched[0], [9] * 400)
    editor.external_load_button.click()
    staged = editor._staged_edits[editor._sample_name_at_row(0)]
    assert staged.samples == [9] * 400 and staged.original_samples == list(range(500))


def test_an_unreadable_edited_file_is_reported_and_nothing_is_staged(editor, qapp, monkeypatch, launched):
    _load_demo_audio(editor, qapp, monkeypatch, list(range(500)))
    editor.external_edit_button.click()
    with open(launched[0], "wb") as f:
        f.write(b"half a file")
    stat = os.stat(launched[0])
    os.utime(launched[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    editor._refresh_external_edit_buttons()

    editor.external_load_button.click()

    assert "Couldn't read the edited file" in editor.status_bar.currentMessage()
    assert editor._staged_edits == {} and editor._sample_waveform_cache[0]["samples"] == list(range(500))


def test_a_launch_failure_is_shown_in_the_status_bar(editor, qapp, monkeypatch, launched):
    _load_demo_audio(editor, qapp, monkeypatch, list(range(500)))
    monkeypatch.setattr(ee, "launch", lambda path, editor=None: "no such app")
    editor.external_edit_button.click()
    assert "no such app" in editor.status_bar.currentMessage()
    assert not editor.external_load_button.isEnabled()


# --- Yamaha editor ---------------------------------------------------------------------------------------------------


@pytest.fixture
def yamaha_fast(window, monkeypatch):  # noqa: F811
    window._session.send_baud, window._session.send_gap_ms, window._session.send_tick_ms = 10_000_000, 0, 5
    window.controller._yamaha_transfers.verify_delay_ms = 0
    window.controller._yamaha_transfers.verify_retry_ms = 5
    monkeypatch.setattr(edit_module, "LINK_SETTLE_MS", 0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)


def test_the_yamaha_external_buttons_need_the_audio_and_a_saved_file(window, yamaha_fast, launched):  # noqa: F811
    tab = window.samples_tab
    assert not tab.external_edit_button.isEnabled() and not tab.external_load_button.isEnabled()
    tab = user_sample(window, mode=4, end=4000)
    assert tab.external_edit_button.isEnabled() and not tab.external_load_button.isEnabled()
    tab.external_edit_button.click()
    assert len(launched) == 1
    assert not tab.external_load_button.isEnabled()
    _save_over(launched[0], [3] * 2000, rate=sds_encoder.read_wav_info(launched[0])[1])
    tab._edits._external.changed.emit()
    assert tab.external_load_button.isEnabled()


def test_loading_edited_audio_makes_a_working_copy_and_sending_it_makes_a_new_sample(window, yamaha_fast, launched):  # noqa: F811
    tab = user_sample(window, mode=4, end=4000)
    original = list(window.fake.audio["UM"])
    tab.external_edit_button.click()
    rate = sds_encoder.read_wav_info(launched[0])[1]
    exported, _ = sds_encoder.read_wav_samples(launched[0])
    assert exported == original

    _save_over(launched[0], [11] * 1500, rate=rate)
    tab._edits._external.changed.emit()
    before = set(window.fake.samples)
    tab.external_load_button.click()
    # loaded, not sent: the tab shows the edited audio and the unit is untouched
    assert set(window.fake.samples) == before and tab._staged["UM"].channels[0] == [11] * 1500
    assert tab._audio_samples == [11] * 1500 and bool(tab._staged_banner.property("unsent"))
    assert not tab.external_load_button.isEnabled()  # waits for the next save

    tab.send_edits_as_new_button.click()
    assert wait_until(lambda: "UM EDIT" in window.fake.samples, timeout=10)
    assert wait_until(lambda: "UM EDIT" in window.samples_tab.sample_names(), timeout=10)
    assert window.fake._audio_for("UM EDIT") == [11] * 1500
    assert list(window.fake.audio["UM"]) == original
    data = window.fake.samples["UM EDIT"]
    assert yp.extract(yp.get("sample", "wave_length"), data) == 1500
    assert wait_until(lambda: not tab._edit_busy)
    assert tab._staged == {}  # the work lives on as the new sample


def test_a_stereo_yamaha_sample_stays_stereo_through_the_external_editor(window, yamaha_fast, launched):  # noqa: F811
    tab = user_sample(window, "ST", mode=4, end=4000, right=True)
    tab.external_edit_button.click()
    rate = sds_encoder.read_wav_info(launched[0])[1]
    channels, _ = sds_encoder.read_wav_channels(launched[0])
    assert len(channels) == 2
    sds_encoder.write_wav_file_stereo(launched[0], [1] * 1000, [2] * 1000, rate, bit_depth=16)
    stat = os.stat(launched[0])
    os.utime(launched[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    tab._edits._external.changed.emit()
    tab.external_load_button.click()
    assert tab._staged["ST"].channels == [[1] * 1000, [2] * 1000]
    tab.send_edits_as_new_button.click()
    assert wait_until(lambda: "ST EDIT" in window.fake.samples, timeout=10)
    assert yp.is_stereo(window.fake.samples["ST EDIT"])


def test_a_mono_file_for_a_stereo_sample_goes_on_both_sides_and_a_stereo_file_for_a_mono_sample_is_mixed(window, yamaha_fast, launched):  # noqa: F811
    tab = user_sample(window, "ST", mode=4, end=4000, right=True)
    tab.external_edit_button.click()
    _save_over(launched[0], [5] * 800, rate=sds_encoder.read_wav_info(launched[0])[1])
    tab._edits._external.changed.emit()
    tab.external_load_button.click()
    assert tab._staged["ST"].channels == [[5] * 800, [5] * 800]
    tab = user_sample(window, "MO", mode=4, end=4000)
    launched.clear()
    tab.external_edit_button.click()
    rate = sds_encoder.read_wav_info(launched[0])[1]
    sds_encoder.write_wav_file_stereo(launched[0], [100] * 600, [300] * 600, rate, bit_depth=16)
    stat = os.stat(launched[0])
    os.utime(launched[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    tab._edits._external.changed.emit()
    tab.external_load_button.click()
    assert tab._staged["MO"].channels == [[200] * 600]


# --- Time Stretch / Pitch Shift (core/time_stretch.py, ui/time_stretch_dialog.py) ----------------------------------------


def _accepting_dialog(monkeypatch, *, percent=100, grain_ms=20, mode="stretch", semitones=0, cents=0):
    """Stand-in for the dialog's exec(): turn the knobs and accept, as a user would."""
    from ui.time_stretch_dialog import PITCH, STRETCH, TimeStretchDialog

    def fake_exec(self):
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(PITCH if mode == "pitch" else STRETCH))
        self.length_knob.setValue(percent)
        self.semitones_knob.setValue(semitones)
        self.cents_knob.setValue(cents)
        self.grain_knob.setValue(grain_ms)
        return True

    monkeypatch.setattr(TimeStretchDialog, "exec", fake_exec)


def test_the_time_stretch_dialog_reports_the_result_and_refuses_no_change(qapp):
    from PySide6.QtWidgets import QDialogButtonBox

    from ui.time_stretch_dialog import TimeStretchDialog

    dialog = TimeStretchDialog(None, "KICK", [0] * 44100, 44100)
    ok = dialog._button_box.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled() and "44,100 frames" in dialog.result_label.text()  # 100%: nothing to do
    dialog.length_knob.setValue(200)
    assert ok.isEnabled() and "88,200 frames" in dialog.result_label.text() and dialog.factor() == 2.0
    assert dialog.edit()[0] == "stretch" and dialog.summary() == "stretched to 200% of its length"
    dialog.grain_knob.setValue(10)
    assert dialog.grain_frames() == pytest.approx(441.0)


def test_the_dialogs_knobs_are_live_and_double_click_resets_to_the_neutral_value(qapp):
    from ui.time_stretch_dialog import TimeStretchDialog

    dialog = TimeStretchDialog(None, "KICK", [0] * 1000, 44100)
    knobs = (dialog.length_knob, dialog.semitones_knob, dialog.cents_knob, dialog.grain_knob)
    assert all(k.isEnabled() for k in knobs)
    assert [k.defaultValue() for k in knobs] == [100, 0, 0, 40]
    assert (dialog.length_knob.value(), dialog.grain_knob.value()) == (100, 40)


def test_pitch_mode_swaps_the_knobs_keeps_the_length_and_describes_the_shift(qapp):
    from PySide6.QtWidgets import QDialogButtonBox

    from ui.time_stretch_dialog import PITCH, TimeStretchDialog

    dialog = TimeStretchDialog(None, "KICK", [0] * 1000, 44100)
    ok = dialog._button_box.button(QDialogButtonBox.StandardButton.Ok)
    assert not dialog._length_column.isHidden() and dialog._semitones_column.isHidden()
    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData(PITCH))
    assert dialog._length_column.isHidden() and not dialog._semitones_column.isHidden() and not dialog._cents_column.isHidden()
    assert not ok.isEnabled()  # 0 semitones, 0 cents
    dialog.semitones_knob.setValue(3)
    dialog.cents_knob.setValue(10)
    assert ok.isEnabled() and "1,000 frames" in dialog.result_label.text()
    kind, options = dialog.edit()
    assert kind == "pitch" and options["ratio"] == pytest.approx(2 ** (3.1 / 12))
    assert dialog.summary() == "shifted by +3.1 semitones"
    dialog.semitones_knob.setValue(-12)
    dialog.cents_knob.setValue(0)
    assert dialog.summary() == "shifted by -12 semitones" and dialog.edit()[1]["ratio"] == pytest.approx(0.5)


def test_the_time_stretch_dialog_refuses_a_result_too_long_for_the_sampler(qapp):
    from PySide6.QtWidgets import QDialogButtonBox

    from core import time_stretch
    from ui.time_stretch_dialog import TimeStretchDialog

    dialog = TimeStretchDialog(None, "LONG", [0] * (time_stretch.MAX_OUTPUT_FRAMES // 3), 44100)
    dialog.length_knob.setValue(400)
    assert not dialog._button_box.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    assert "Too long" in dialog.result_label.text()


def test_time_stretch_replaces_the_sample_with_a_longer_one_and_scales_the_markers(editor, qapp, monkeypatch):
    _load_demo_audio(editor, qapp, monkeypatch, [int(8000 * ((i % 100) / 50 - 1)) for i in range(1000)])
    assert editor.time_stretch_button.isEnabled()
    editor.waveform_view.set_marker("start", 100)
    editor.waveform_view.set_marker("end", 800)
    _accepting_dialog(monkeypatch, percent=200)

    editor.time_stretch_button.click()

    entry = editor._sample_waveform_cache[0]
    assert entry["frame_count"] == 2000 and len(entry["samples"]) == 2000
    assert entry["start"] == round(100 * 1999 / 999) and entry["end"] == round(800 * 1999 / 999)


def test_pitch_shift_replaces_the_sample_keeping_its_length_and_markers(editor, qapp, monkeypatch):
    original = [int(8000 * ((i % 100) / 50 - 1)) for i in range(1000)]
    _load_demo_audio(editor, qapp, monkeypatch, original)
    editor.waveform_view.set_marker("start", 100)
    editor.waveform_view.set_marker("end", 800)
    _accepting_dialog(monkeypatch, mode="pitch", semitones=12, grain_ms=10)

    editor.time_stretch_button.click()

    entry = editor._sample_waveform_cache[0]
    assert entry["frame_count"] == 1000 and entry["samples"] != original
    assert (entry["start"], entry["end"]) == (100, 800)
    assert "Pitch Shift" in editor.status_bar.currentMessage()


def test_declining_the_time_stretch_dialog_changes_nothing(editor, qapp, monkeypatch):
    from ui.time_stretch_dialog import TimeStretchDialog

    _load_demo_audio(editor, qapp, monkeypatch, list(range(500)))
    monkeypatch.setattr(TimeStretchDialog, "exec", lambda self: False)
    editor.time_stretch_button.click()
    assert editor._sample_waveform_cache[0]["frame_count"] == 500


def test_yamaha_time_stretch_applies_to_the_working_copy_and_sending_makes_a_new_longer_sample(window, yamaha_fast, monkeypatch):  # noqa: F811
    tab = user_sample(window, "ST", mode=4, end=4000, right=True)
    original = list(window.fake.audio["ST"])
    _accepting_dialog(monkeypatch, percent=150, grain_ms=10)
    before = set(window.fake.samples)
    tab.time_stretch_button.click()
    assert set(window.fake.samples) == before  # OK changes the working copy only
    assert [len(c) for c in tab._staged["ST"].channels] == [6000, 6000]
    tab.send_edits_as_new_button.click()
    assert wait_until(lambda: "ST EDIT" in window.fake.samples, timeout=20)
    data = window.fake.samples["ST EDIT"]
    assert yp.is_stereo(data)
    assert yp.extract(yp.get("sample", "wave_length"), data) == 6000  # the 4000-frame wave end x 1.5
    assert list(window.fake.audio["ST"]) == original


def test_yamaha_pitch_shift_then_send_makes_a_new_sample_of_the_same_length(window, yamaha_fast, monkeypatch):  # noqa: F811
    tab = user_sample(window, mode=4, end=4000)
    original = list(window.fake.audio["UM"])
    _accepting_dialog(monkeypatch, mode="pitch", semitones=-5, grain_ms=40)
    tab.time_stretch_button.click()
    tab.send_edits_as_new_button.click()
    assert wait_until(lambda: "UM EDIT" in window.fake.samples, timeout=20)
    assert yp.extract(yp.get("sample", "wave_length"), window.fake.samples["UM EDIT"]) == 4000
    assert list(window.fake.audio["UM"]) == original
