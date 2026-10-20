# The Yamaha Samples tab's WORKING COPY: edits (and audio loaded back from an external editor) change what the tab shows and send nothing; the
# A4000 can't overwrite a sample, so "Send as New Sample" is the only way out. Undo / Revert, local marker edits, the Sample menu and the layout.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox

from core import yamaha_markers as ym
from core import yamaha_params as yp
from test_s950_transfers import qapp, wait_until  # noqa: F401
from test_yamaha_markers_ui import drag, put, settled, unit, user_sample
from test_yamaha_program_editor import warning_acknowledged  # noqa: F401
from test_yamaha_waveform import load, window  # noqa: F401
from ui import yamaha_sample_edit as edit_module


@pytest.fixture(autouse=True)
def _fast(window, monkeypatch):  # noqa: F811
    window._session.send_baud, window._session.send_gap_ms, window._session.send_tick_ms = 10_000_000, 0, 5
    window.controller._yamaha_transfers.verify_delay_ms = 0
    window.controller._yamaha_transfers.verify_retry_ms = 5
    monkeypatch.setattr(edit_module, "LINK_SETTLE_MS", 0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)


def _staged_sample(window, **kwargs):  # noqa: F811
    tab = user_sample(window, mode=kwargs.pop("mode", 4), end=kwargs.pop("end", 3500), **kwargs)
    tab.edit_buttons["reverse"].click()
    return tab


def test_an_edit_changes_what_the_tab_shows_and_sends_nothing(window):  # noqa: F811
    tab = user_sample(window, mode=4, end=3500)
    before_audio, before_samples = list(window.fake.audio["UM"]), set(window.fake.samples)
    messages = []
    tab.status_message.connect(messages.append)
    tab.edit_buttons["reverse"].click()
    staged = tab._staged["UM"]
    assert staged.channels[0] == list(reversed(before_audio)) and staged.original_channels[0] == before_audio
    assert tab._audio_samples == staged.channels[0]
    assert set(window.fake.samples) == before_samples and list(window.fake.audio["UM"]) == before_audio  # the unit is untouched
    assert bool(tab._staged_banner.property("unsent")) and not tab._row_dots["UM"].isHidden()
    assert any("applied" in m and "not on the unit" in m for m in messages)


def test_the_header_cards_show_the_working_copys_length(window):  # noqa: F811
    tab = user_sample(window, mode=4, end=3999)
    on_unit = yp.extract(yp.get("sample", "wave_length"), tab._cache["UM"])
    tab.edit_buttons["reverse"].click()
    assert "4,000 frames" in tab.summary_label.text()  # same length
    # trim to Start..End: the summary and the Wave card follow the working copy
    tab.waveform_view.set_marker("start", 1000)
    tab.edit_buttons["trim"].click()
    assert len(tab._staged["UM"].channels[0]) == 3000
    assert "3,000 frames" in tab.summary_label.text()
    # the real payload from the unit is untouched
    assert yp.extract(yp.get("sample", "wave_length"), tab._cache["UM"]) == on_unit


def test_marker_edits_stay_local_while_a_sample_has_unsent_edits(window):  # noqa: F811
    tab = user_sample(window, mode=1, end=3500, loop=(1500, 2500))
    on_unit = unit(window)
    tab.edit_buttons["reverse"].click()
    view = tab.waveform_view
    drag(view, "loop_end", 2400)
    drag(view, "start", 100)
    staged = tab._staged["UM"]
    assert staged.markers[0] == 100 and staged.markers[2] == 2400
    assert tab._marker_job is None and tab._marker_pending is None  # no write was started
    assert unit(window) == on_unit  # nothing was written for the real sample
    assert view.markers()["start"] == 100 and view.markers()["loop_end"] == 2400


def test_without_unsent_edits_marker_edits_still_write_at_once(window):  # noqa: F811
    tab = user_sample(window, mode=4, end=3500)
    drag(tab.waveform_view, "start", 400)
    assert wait_until(lambda: unit(window).start == 400, timeout=10)


def test_the_working_copy_survives_selecting_another_sample_and_coming_back(window):  # noqa: F811
    tab = _staged_sample(window)
    edited = list(tab._staged["UM"].channels[0])
    other = next(n for n in tab.sample_names() if n != "UM")
    tab.select_sample(other)
    assert wait_until(lambda: tab._selected == other and not tab.cards_scroll.isHidden())
    assert not bool(tab._staged_banner.property("unsent"))  # the other sample has nothing unsent
    tab.select_sample("UM")
    assert wait_until(lambda: tab._selected == "UM")
    assert tab._audio_name == "UM" and tab._audio_samples == edited and bool(tab._staged_banner.property("unsent"))
    assert tab.waveform_view.has_waveform()


def test_the_working_copy_survives_a_reload_of_the_sample_list_and_a_refresh(window):  # noqa: F811
    tab = _staged_sample(window)
    edited = list(tab._staged["UM"].channels[0])
    tab.set_samples(list(window.fake.samples))
    assert wait_until(lambda: tab._selected == "UM")
    assert tab._staged["UM"].channels[0] == edited and not tab._row_dots["UM"].isHidden()
    tab.refresh()  # the host re-reads everything
    tab._cache.clear()
    tab.select_sample("UM")
    tab._on_selected(tab.sample_list_widget.currentItem(), None)
    assert wait_until(lambda: tab._audio_samples == edited and not tab.cards_scroll.isHidden())


def test_undo_steps_back_and_the_first_undo_goes_back_to_the_units_version(window):  # noqa: F811
    tab = user_sample(window, mode=4, end=3500)
    original = list(window.fake.audio["UM"])
    tab.edit_buttons["reverse"].click()
    tab.edit_buttons["normalise"].click()
    normalised = list(tab._staged["UM"].channels[0])
    assert normalised != list(reversed(original))
    tab.undo_edits_button.click()
    assert tab._staged["UM"].channels[0] == list(reversed(original)) and tab._audio_samples == list(reversed(original))
    tab.undo_edits_button.click()
    assert tab._staged == {} and tab._audio_samples == original and not bool(tab._staged_banner.property("unsent"))
    assert not tab.undo_edits_button.isEnabled()


def test_undo_history_is_limited(window):  # noqa: F811
    from ui import yamaha_samples_tab as module

    tab = user_sample(window, mode=4, end=3500)
    for _ in range(module._UNDO_LIMIT + 4):
        tab.edit_buttons["reverse"].click()
    assert len(tab._staged["UM"].history) == module._UNDO_LIMIT


def test_revert_asks_first_and_goes_back_to_the_units_version(window, monkeypatch):  # noqa: F811
    tab = _staged_sample(window)
    original = list(window.fake.audio["UM"])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    tab.revert_edits_button.click()
    assert "UM" in tab._staged  # declined
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    tab.revert_edits_button.click()
    assert tab._staged == {} and tab._audio_samples == original and tab.waveform_view.has_waveform()
    assert "●" not in tab._row_labels["UM"].text()


def test_send_as_new_makes_the_unit_copy_and_the_original_goes_back_to_its_version(window):  # noqa: F811
    tab = _staged_sample(window)
    original = list(window.fake.audio["UM"])
    tab.send_edits_as_new_button.click()
    assert wait_until(lambda: "UM EDIT" in window.fake.samples, timeout=15)
    assert window.fake._audio_for("UM EDIT") == list(reversed(original))
    assert list(window.fake.audio["UM"]) == original
    assert wait_until(lambda: tab._selected == "UM EDIT" and tab._staged == {}, timeout=15)
    tab.select_sample("UM")
    assert wait_until(lambda: tab._selected == "UM")
    assert tab._staged == {} and tab._audio_samples == original  # not left showing the edit


def test_a_failed_send_keeps_the_working_copy(window):  # noqa: F811
    tab = _staged_sample(window)
    window.fake.bulk_protect = True  # the unit ignores everything sent to it
    tab.send_edits_as_new_button.click()
    assert wait_until(lambda: not tab._edit_busy, timeout=40)
    assert "UM" in tab._staged and "UM EDIT" not in window.fake.samples


def test_a_sample_that_is_gone_from_the_unit_loses_its_unsent_edits(window):  # noqa: F811
    tab = _staged_sample(window)
    messages = []
    tab.status_message.connect(messages.append)
    tab.set_samples([n for n in window.fake.samples if n != "UM"])
    assert tab._staged == {} and any("no longer on the unit" in m for m in messages)


def test_closing_the_editor_with_unsent_edits_asks_first(window, monkeypatch):  # noqa: F811
    tab = _staged_sample(window)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted() and "UM" in asked[0]


def test_the_edit_dialogs_own_send_as_new_sample_from_a_working_copy_clears_it(window, monkeypatch):  # noqa: F811
    class Stub:
        def __init__(self, *a, **k): pass
        def exec(self): return True
        def send_as_new(self): return True
        def highpass_enabled(self): return False
        def highpass_cutoff_hz(self): return None
        def highpass_slope_db_per_octave(self): return 12
        def lowpass_enabled(self): return True
        def lowpass_cutoff_hz(self): return 800
        def lowpass_slope_db_per_octave(self): return 12

    monkeypatch.setattr(edit_module, "FilterSampleDialog", Stub)
    tab = _staged_sample(window)
    tab.edit_buttons["filter"].click()
    assert wait_until(lambda: "UM FILT" in window.fake.samples, timeout=15)
    assert wait_until(lambda: tab._staged == {}, timeout=15)


def test_the_slice_editor_slices_the_working_copy(window, monkeypatch):  # noqa: F811
    seen = {}

    class Stub:
        export_succeeded = False

        def __init__(self, parent, name, samples, *a, **k):
            seen["samples"] = list(samples)

        def exec(self):
            return 0

    monkeypatch.setattr(edit_module, "SliceEditorWindow", Stub)
    tab = _staged_sample(window)
    tab.slice_button.click()
    assert seen["samples"] == tab._staged["UM"].channels[0]


# --- the Sample menu and the right-click menu ------------------------------------------------------------------------


def _texts(actions):
    return [a.text() if not a.isSeparator() else "-" for a in actions]


def test_the_sample_menu_lists_every_action_in_groups(window):  # noqa: F811
    menu = window._sample_menu
    assert _texts(menu.actions()) == [
        "Load Audio", "-",
        "Trim to Markers", "Reverse", "Fade In/Out", "Normalise", "Filter…", "Stretch / Pitch…", "-",
        "Slice Editor…", "-",
        "Edit in External App…", "Load Edited Audio", "-",
        "Undo", "Send as New Sample…", "Revert", "-",
        "Detect Pitch",
    ]  # fmt: skip
    titles = [a.text() for a in window.menuBar().actions()]
    assert titles.index("&File") + 1 == titles.index("&Sample") < titles.index("&Hardware")
    # the right-click menu shares the very same actions
    listed = window.samples_tab.sample_list_widget.actions()
    assert _texts(listed) == _texts(menu.actions())
    for key, (_button, action) in window.samples_tab._sample_controls.items():
        assert action in listed, key


def test_the_actions_follow_their_buttons_but_only_on_the_samples_tab(window):  # noqa: F811
    tab = user_sample(window, mode=4, end=3500)
    window.main_tabs.setCurrentIndex(0)  # the Programs tab
    assert not any(a.isEnabled() for _b, a in tab._sample_controls.values())  # a destructive-looking item must not act unseen
    window.main_tabs.setCurrentIndex(window._samples_tab_index)
    for key in ("trim", "reverse", "fade", "normalise", "filter", "stretch", "slice", "external_edit", "detect"):
        button, action = tab._sample_controls[key]
        assert button.isEnabled() and action.isEnabled(), key
    for key in ("external_load", "stage_undo", "stage_send", "stage_revert"):
        button, action = tab._sample_controls[key]
        assert not button.isEnabled() and not action.isEnabled(), key
    tab.edit_buttons["reverse"].click()
    for key in ("stage_undo", "stage_send", "stage_revert"):
        button, action = tab._sample_controls[key]
        assert button.isEnabled() and action.isEnabled(), key


def test_load_audio_is_offered_until_the_audio_is_loaded(window):  # noqa: F811
    tab = window.samples_tab
    window.main_tabs.setCurrentIndex(window._samples_tab_index)
    tab.select_sample("pulse 1")
    assert wait_until(lambda: tab._selected == "pulse 1" and not tab.cards_scroll.isHidden())
    assert tab.load_audio_action.isEnabled()
    tab.load_audio_action.trigger()
    assert wait_until(lambda: tab._audio_name == "pulse 1")
    assert not tab.load_audio_action.isEnabled()


def test_the_menu_action_does_what_the_button_does(window):  # noqa: F811
    tab = user_sample(window, mode=4, end=3500)
    window.main_tabs.setCurrentIndex(window._samples_tab_index)
    tab._sample_controls["reverse"][1].trigger()
    assert "UM" in tab._staged


def test_the_sample_menu_is_disabled_while_something_long_runs(window):  # noqa: F811
    tab = _staged_sample(window)
    window._session.send_baud, window._session.send_gap_ms, window._session.send_tick_ms = 60_000, 0, 5
    tab.send_edits_as_new_button.click()
    assert wait_until(lambda: tab._edit_busy, timeout=10)
    assert not window._sample_menu.isEnabled()
    window.cancel_button.click()
    assert wait_until(lambda: not tab._edit_busy, timeout=15)
    assert window._sample_menu.isEnabled()


# --- layout ---------------------------------------------------------------------------------------------------------------


def test_the_edit_sample_card_sits_under_loop_controls_and_the_loop_preview_is_beside_the_wave_card(window):  # noqa: F811
    from PySide6.QtCore import QPoint

    tab = user_sample(window, mode=1, end=3500, loop=(1500, 2500))
    window.resize(1250, 900)
    window.show()
    window.main_tabs.setCurrentIndex(window._samples_tab_index)
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()
    try:
        origin = lambda w: w.mapTo(window, QPoint(0, 0))  # noqa: E731
        trim, preview, wave_text = (
            origin(tab.edit_buttons["trim"]),
            origin(tab.loop_preview),
            origin(tab.panel.widgets["wave_length"]),
        )
        assert trim.y() < preview.y()  # Edit Sample above the pair
        assert wave_text.x() > preview.x() + tab.loop_preview.width()  # the Wave card to the right of the Loop Preview
        assert abs(wave_text.y() - preview.y()) < 150  # ...in the same row
    finally:
        window.hide()


# --- the Edit Sample card (same look as the Program Editor's) -----------------------------------------------------------------


def _unsent(tab):
    return bool(tab._staged_banner.property("unsent"))


def test_the_strip_is_always_there_and_only_its_state_changes(window):  # noqa: F811
    tab = user_sample(window, mode=4, end=3500)
    assert not tab._staged_banner.isHidden() and not _unsent(tab)
    assert tab._working_copy_label.text() == "Original from the unit" and tab._working_copy_dot.isHidden()
    for button in (tab.undo_edits_button, tab.send_edits_as_new_button, tab.revert_edits_button):
        assert not button.isEnabled()  # there, but off
    tab.edit_buttons["reverse"].click()
    assert not tab._staged_banner.isHidden() and _unsent(tab)
    assert tab._working_copy_label.text() == "Edited – not on the unit yet" and not tab._working_copy_dot.isHidden()
    assert tab.send_edits_as_new_button.isEnabled() and tab.revert_edits_button.isEnabled()
    tab.undo_edits_button.click()
    assert not _unsent(tab) and tab._working_copy_label.text() == "Original from the unit"


def test_send_as_new_sample_is_the_primary_button_and_load_edited_audio_becomes_one_once_saved(window, monkeypatch):  # noqa: F811
    tab = user_sample(window, mode=4, end=3500)
    assert tab.send_edits_as_new_button.property("primary") is True
    assert not tab.external_load_button.property("primary")
    monkeypatch.setattr(tab._edits, "edited_audio_ready", lambda name: True)
    tab.update_edit_buttons()
    assert tab.external_load_button.isEnabled() and tab.external_load_button.property("primary") is True
    monkeypatch.setattr(tab._edits, "edited_audio_ready", lambda name: False)
    tab.update_edit_buttons()
    assert not tab.external_load_button.property("primary")


def test_the_external_hint_follows_the_round_trip(window, monkeypatch, tmp_path):  # noqa: F811
    from core import app_config, dropped_files, external_edit as ee, sds_encoder

    tab = user_sample(window, mode=4, end=3500)
    assert tab._external_hint.isHidden()
    monkeypatch.setattr(dropped_files, "_session_dir", tmp_path)
    opened = []
    monkeypatch.setattr(ee, "launch", lambda path, editor=None: opened.append(path))
    monkeypatch.setattr(app_config, "get_saved_external_editor", lambda: "/Applications/iZotope RX 10 Audio Editor.app/")
    tab.external_edit_button.click()
    assert tab._external_hint.text() == "Waiting for you to save in iZotope RX 10 Audio Editor" and not tab._external_hint.isHidden()
    rate = sds_encoder.read_wav_info(opened[0])[1]
    sds_encoder.write_wav_file(opened[0], [1000] * 500, rate, bit_depth=16)
    stat = os.stat(opened[0])
    os.utime(opened[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    tab._edits._external.changed.emit()
    assert tab._external_hint.text() == "Saved – ready to load" and tab.external_load_button.property("primary") is True
    tab.external_load_button.click()
    assert tab._external_hint.isHidden() and not tab.external_load_button.property("primary")
    assert tab._staged["UM"].channels[0] == [1000] * 500


def test_the_card_keeps_its_height_and_both_rows_line_up_without_a_scroll_bar(window):  # noqa: F811
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    tab = user_sample(window, mode=1, end=3500, loop=(1500, 2500))
    window.resize(1250, 900)
    window.show()
    window.main_tabs.setCurrentIndex(window._samples_tab_index)
    QApplication.processEvents()
    try:
        origin = lambda w: w.mapTo(window, QPoint(0, 0))  # noqa: E731
        before = origin(tab.loop_preview).y()
        tab.edit_buttons["reverse"].click()
        tab._external_waiting["UM"] = "A Really Quite Remarkably Long Audio Editor Application Name Pro Edition"
        tab.update_edit_buttons()
        QApplication.processEvents()
        assert origin(tab.loop_preview).y() == before  # nothing below moved
        assert origin(tab.edit_buttons["trim"]).x() == origin(tab.slice_button).x()  # both rows start at one x
        assert tab.cards_scroll.horizontalScrollBar().maximum() == 0  # a long hint never widens the page
        dividers = [w for w in tab.findChildren(type(tab._staged_banner)) if w.objectName() == "groupDivider"]
        assert len(dividers) == 1 and origin(tab.slice_button).x() < origin(dividers[0]).x() < origin(tab.external_edit_button).x()
    finally:
        tab._staged.clear()
        window.hide()


def test_the_external_hint_is_given_room_to_show(window):  # noqa: F811
    from PySide6.QtWidgets import QApplication

    tab = user_sample(window, mode=4, end=3500)
    window.resize(1250, 900)
    window.show()
    window.main_tabs.setCurrentIndex(window._samples_tab_index)
    tab._external_waiting["UM"] = "iZotope RX 10 Audio Editor"
    tab.update_edit_buttons()
    QApplication.processEvents()
    try:
        assert tab._external_hint.width() > 100  # it had 0 once: an Ignored-policy label beside a trailing stretch
    finally:
        window.hide()
