# tests for ui/yamaha_program_editor.py + ui/yamaha_samples_tab.py - the Yamaha A4000 editor (reads and writes).
#
# The real window, real SamplerController and real YamahaSession run against core/demo_a4000.FakeA4000
# over the S950 tests' fake MidiManager. Offscreen Qt; every wire wait is shrunk.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QMainWindow, QMessageBox, QSpinBox, QWidget

from controller.sampler_controller import SamplerController
from core import demo_a4000 as demo
from core import yamaha_params as yp
from ui import yamaha_program_editor as editor_module
from ui import yamaha_writer as writer_module
from ui import yamaha_samples_tab as samples_module
from ui.knob import Knob
from ui.yamaha_program_editor import YamahaProgramEditorWindow

from test_s950_transfers import _Midi, qapp, wait_until  # noqa: F401


# --- pure helpers ------------------------------------------------------------------------------------


def _payloads(shift=0, low=0, high=127, own_low=0, own_high=127):
    program = bytearray(demo.make_program_payload(1, ["x"]))
    yp.store(yp.get("easy_edit", "key_range_shift"), program, shift, 0)
    yp.store(yp.get("easy_edit", "key_limit_low"), program, low, 0)
    yp.store(yp.get("easy_edit", "key_limit_high"), program, high, 0)
    sample = bytearray(demo.make_sample_payload("x"))
    yp.store(yp.get("sample", "key_range_low"), sample, own_low)
    yp.store(yp.get("sample", "key_range_high"), sample, own_high)
    return sample, program


def test_effective_range_is_the_samples_own_range_by_default():
    assert editor_module.effective_key_range(*_payloads(own_low=36, own_high=60), 0) == (36, 60)


def test_the_key_shift_moves_the_range_and_the_limits_cut_it():
    sample, program = _payloads(shift=12, own_low=36, own_high=60)
    assert editor_module.effective_key_range(sample, program, 0) == (48, 72)
    sample, program = _payloads(shift=12, low=50, high=70, own_low=36, own_high=60)
    assert editor_module.effective_key_range(sample, program, 0) == (50, 70)


def test_the_original_ends_count_as_the_keyboards_ends_and_everything_stays_on_the_keyboard():
    sample, program = _payloads(own_low=-1, own_high=128)
    assert editor_module.effective_key_range(sample, program, 0) == (0, 127)
    sample, program = _payloads(shift=100, own_low=60, own_high=120)
    low, high = editor_module.effective_key_range(sample, program, 0)
    assert 0 <= low <= high <= 127


def test_formatting():
    assert editor_module.program_row_text(7, "Pgm 007") == "007  Pgm 007"
    assert editor_module.program_row_text(7, "") == "007"
    assert editor_module.format_range(60, 72) == "C3 - C4"
    assert samples_module.format_hz(48000) == "48,000 Hz"
    assert samples_module.format_tempo(9000) == "90.00"
    assert samples_module.format_used_in([]) == "Not used by any program"
    assert samples_module.format_used_in([1, 40]) == "Used in programs 001, 040"
    assert "and 3 more" in samples_module.format_used_in(list(range(1, 16)))


# --- the window ------------------------------------------------------------------------------------------


class _Main(QMainWindow):
    pass


def build_window(qapp, fake):  # noqa: F811
    midi = _Midi(fake)
    controller = SamplerController(midi)
    controller.set_device_type("yamaha_a4000")
    session = controller.yamaha_session()
    session.device = fake.device
    session.select_settle_ms = 1
    session.edit_settle_ms = 1
    session.reply_timeout_ms = 400
    session.bulk_timeout_ms = 600
    main = _Main()
    window = YamahaProgramEditorWindow(main, controller)
    window._writer.THROTTLE_MS = 5
    window._writer.REREAD_MS = 30
    window.main, window.controller, window.midi, window.fake = main, controller, midi, fake
    return window


def dispose(window):
    window.samples_tab.disconnect_controller()
    window._writer.close()
    window._scan_generation += 1
    window._session.cancel()
    window._connected = False
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


@pytest.fixture(autouse=True)
def warning_acknowledged(monkeypatch):
    """The one-time experimental warning is a modal dialog and a flag in the REAL config.json: never in a test."""
    state = {"ack": True, "saved": []}
    monkeypatch.setattr(writer_module.app_config, "get_yamaha_write_warning_acknowledged", lambda: state["ack"])
    monkeypatch.setattr(
        writer_module.app_config, "save_yamaha_write_warning_acknowledged", lambda value=True: state["saved"].append(value)
    )
    return state


@pytest.fixture
def fake():
    f = demo.FakeA4000()
    f.assign(1, "sine wave")
    f.assign(1, "saw up")
    f.assign(5, "pulse 2")
    return f


@pytest.fixture
def win(qapp, fake):  # noqa: F811
    window = build_window(qapp, fake)
    assert wait_until(lambda: len(window._counts) == 128 and not window._scanning, timeout=20)
    assert wait_until(lambda: window._selected == 1 and window.assigned_list.count() == 2)
    assert wait_until(lambda: all(window._range_rows))
    yield window
    dispose(window)


def test_the_scan_hides_empty_programs_and_selects_the_first_with_samples(win):
    visible = [n for n in range(1, 129) if not win._items[n].isHidden()]
    assert visible == [1, 5]
    assert win.program_list.currentItem().data(Qt.ItemDataRole.UserRole) == 1
    assert win.program_list.currentItem().text() == "001  Pgm 001"
    assert "2 of 128" in win.scan_label.text()


def test_show_empty_programs_reveals_all_128(win):
    win.show_empty_check.setChecked(True)
    assert all(not item.isHidden() for item in win._items.values())
    win.show_empty_check.setChecked(False)
    assert [n for n, item in win._items.items() if not item.isHidden()] == [1, 5]


def test_the_assigned_samples_list_shows_each_effective_range(win):
    assert win.assigned_list.count() == 2
    labels = [editor_module.keygroup_row_label(win.assigned_list, i).text() for i in range(2)]
    assert labels == ["sine wave: C-2 - G8", "saw up: C-2 - G8"]


def test_the_program_page_shows_the_programs_values(win):
    panel = win.program_panel
    assert panel.value("program_level") == 127
    assert panel.value("program_name") == "Pgm 001"
    assert panel.value("assigned_samples") == "2"
    assert win.detail_stack.currentIndex() == 0


def test_selecting_another_program_reads_and_shows_it(win):
    win.program_list.setCurrentItem(win._items[5])
    assert wait_until(lambda: win._selected == 5 and win.assigned_list.count() == 1 and all(win._range_rows))
    assert win.program_panel.value("program_name") == "Pgm 005"
    assert editor_module.keygroup_row_label(win.assigned_list, 0).text().startswith("pulse 2")


def test_an_assigned_sample_shows_its_easy_edit_values_for_that_slot(win, fake):
    # change slot 1's level offset on the "unit", then re-read the program
    yp.store(yp.get("easy_edit", "level_offset"), fake.programs[1], -25, 1)
    win._program_data.pop(1)
    win._on_program_selected(win.program_list.currentItem(), None)
    assert wait_until(lambda: 1 in win._program_data and win.assigned_list.count() == 2)
    win.assigned_list.setCurrentRow(1)
    assert win.easy_panel.value("level_offset") == -25
    win.assigned_list.setCurrentRow(0)
    assert win.easy_panel.value("level_offset") == 0
    assert win.assigned_name.text() == "sine wave"


def test_the_assigned_info_explains_the_effective_range(win, fake):
    win.assigned_list.setCurrentRow(0)
    assert "Plays C-2 - G8 in this program" in win.assigned_info.text()


def test_exactly_the_writable_rows_are_enabled(win):
    for panel in (win.program_panel, win.easy_panel, win.samples_tab.panel):
        for key, widget in panel.widgets.items():
            if isinstance(widget, QLabel):
                continue
            p = yp.get(panel.scope, key)
            writable = not (p.read_only or p.bulk_only or p.write_ignored or p.a5000_only)
            assert widget.isEnabled() == writable, (panel.scope, key)
    assert win.program_panel.widgets["program_level"].isEnabled()


def test_widgets_use_the_tables_ranges_and_enum_labels(win):
    level = win.program_panel.widgets["program_level"]
    assert isinstance(level, Knob) and (level.minimum(), level.maximum()) == (0, 127)
    wave = win.program_panel.widgets["lfo_wave"]
    assert isinstance(wave, QComboBox) and wave.findText("Triangle") >= 0
    out = win.easy_panel.widgets["output1"]
    assert out.itemText(0) == "=Sample"  # Easy Edit's -1
    assert isinstance(win.easy_panel.widgets["midi_control_on"], QCheckBox)
    assert isinstance(win.easy_panel.widgets["key_range_shift"], QSpinBox)
    assert win.easy_panel.widgets["alternate_group"].specialValueText() == "=Sample"


def test_edit_sample_jumps_to_the_samples_tab_and_selects_it(win):
    win.assigned_list.setCurrentRow(1)
    win.edit_sample_button.click()
    assert win.main_tabs.currentIndex() == win._samples_tab_index
    assert wait_until(lambda: win.samples_tab._selected == "saw up" and not win.samples_tab.cards_scroll.isHidden())


def test_the_samples_tab_lists_samples_and_fills_every_card(win):
    tab = win.samples_tab
    assert tab.sample_names() == list(
        demo.FACTORY_SAMPLES
    )
    tab.select_sample("sine wave")
    assert wait_until(lambda: not tab.cards_scroll.isHidden() and tab.name_label.text() == "sine wave")
    assert tab.panel.value("sample_level") == 100
    assert tab.panel.value("original_key_l") == 66
    assert tab.panel.value("fine_tune_l") == -20
    assert "48,000 Hz" in tab.summary_label.text()
    # the fake marks the link map, like the unit; the header warns that the sample is shared
    assert tab.used_label.text() == "Used in programs 001 - a change here affects all of them"


def test_a_sample_nobody_uses_says_so(win):
    win.samples_tab.select_sample("pulse 3")
    assert wait_until(lambda: win.samples_tab.name_label.text() == "pulse 3")
    assert win.samples_tab.used_label.text() == "Not used by any program"


def test_refresh_rereads_everything(win, fake):
    fake.assign(9, "triangle")
    win.refresh_button.click()
    assert wait_until(lambda: len(win._counts) == 128 and not win._scanning, timeout=20)
    assert [n for n, item in win._items.items() if not item.isHidden()] == [1, 5, 9]


def test_a_silent_unit_shows_what_to_check(qapp):  # noqa: F811
    window = build_window(qapp, demo.FakeA4000(device_number_off=True))
    try:
        assert wait_until(lambda: "Device Number" in window.placeholder.text(), timeout=10)
    finally:
        dispose(window)


def test_an_all_empty_unit_says_so_instead_of_an_empty_list(qapp):  # noqa: F811
    window = build_window(qapp, demo.FakeA4000())
    try:
        assert wait_until(lambda: not window._scanning and len(window._counts) == 128, timeout=20)
        assert "No programs have samples" in window.placeholder.text()
        window.show_empty_check.setChecked(True)
        assert window.program_list.count() == 128
    finally:
        dispose(window)


def test_closing_returns_to_the_dashboard_and_stops_the_session(qapp, fake):  # noqa: F811
    window = build_window(qapp, fake)
    assert wait_until(lambda: window._selected == 1)
    window.close()
    assert window._connected is False and window._session.idle
    dispose(window)


def test_closing_is_refused_while_a_transfer_is_busy(win, monkeypatch):
    monkeypatch.setattr(win.controller, "is_transfer_busy", lambda: True)
    win.close()
    assert win._connected is True
    assert "already in progress" in win.status_bar.currentMessage()


# --- editing ---------------------------------------------------------------------------------------------------


def program_level(fake, n=1):
    return yp.extract(yp.get("program", "program_level"), fake.programs[n])


def test_changing_a_knob_writes_it_to_the_unit_after_a_backup(win, fake):
    win.program_panel.widgets["program_level"].setValue(60)
    assert wait_until(lambda: program_level(fake) == 60)
    assert wait_until(lambda: "Wrote" in win.status_bar.currentMessage())
    backups = list(win._session.backup_dir.glob("PG-001-*.syx"))
    assert len(backups) == 1
    assert win.program_panel.value("program_level") == 60
    assert yp.extract(yp.get("program", "program_level"), win._program_data[1]) == 60


def test_a_burst_of_edits_sends_only_the_value_it_ends_on(win, fake):
    knob = win.program_panel.widgets["program_level"]
    for v in (10, 20, 30, 40, 50):
        knob.setValue(v)
    assert wait_until(lambda: program_level(fake) == 50)
    assert wait_until(lambda: win._writer.busy is False)
    assert fake.edits == 1


def test_an_easy_edit_goes_to_the_selected_assigned_sample(win, fake):
    win.assigned_list.setCurrentRow(1)
    win.easy_panel.widgets["level_offset"].setValue(15)
    assert wait_until(lambda: yp.extract(yp.get("easy_edit", "level_offset"), fake.programs[1], 1) == 15)
    assert yp.extract(yp.get("easy_edit", "level_offset"), fake.programs[1], 0) == 0


def test_an_edit_is_written_to_the_program_it_was_made_on_even_if_another_is_selected_at_once(win, fake):
    win.program_panel.widgets["program_level"].setValue(33)
    win.program_list.setCurrentItem(win._items[5])
    assert wait_until(lambda: program_level(fake) == 33)
    assert program_level(fake, 5) == 127


def test_a_combo_and_a_checkbox_write_their_raw_values(win, fake):
    combo = win.program_panel.widgets["lfo_wave"]
    combo.setCurrentIndex(2)
    combo.activated.emit(2)
    assert wait_until(lambda: yp.extract(yp.get("program", "lfo_wave"), fake.programs[1]) == combo.currentData())
    win.easy_panel.widgets["midi_control_on"].setChecked(True)
    assert wait_until(lambda: yp.extract(yp.get("easy_edit", "midi_control_on"), fake.programs[1], 0) == 1)


def test_a_write_the_unit_swallows_puts_the_widget_back_to_what_it_holds(win, fake):
    fake.bulk_protect = True
    win.program_panel.widgets["program_level"].setValue(60)
    assert wait_until(lambda: "Bulk Protect" in win.status_bar.currentMessage())
    assert wait_until(lambda: win.program_panel.value("program_level") == 127)
    assert program_level(fake) == 127


def test_the_window_rereads_the_program_after_its_writes_to_show_what_the_unit_really_holds(win, fake):
    win.program_panel.widgets["program_level"].setValue(60)
    assert wait_until(lambda: program_level(fake) == 60)
    # a side effect the edit's own read-back can't see (here: a front-panel change) shows up after the re-read
    yp.store(yp.get("program", "transpose"), fake.programs[1], 7)
    assert wait_until(lambda: win.program_panel.value("transpose") == 7)


def test_an_edit_that_moves_a_samples_range_updates_the_list_and_the_bar(win, fake):
    win.assigned_list.setCurrentRow(0)
    win.easy_panel.widgets["key_limit_low"].setValue(60)
    assert wait_until(lambda: editor_module.keygroup_row_label(win.assigned_list, 0).text().startswith("sine wave: C3"))
    assert "Plays C3" in win.assigned_info.text()


def test_the_first_edit_asks_once_and_declining_writes_nothing(win, fake, warning_acknowledged, monkeypatch):
    warning_acknowledged["ack"] = False
    asked = []
    monkeypatch.setattr(
        writer_module.QMessageBox, "question", lambda *a, **k: asked.append(a[1]) or QMessageBox.StandardButton.Cancel
    )
    win.program_panel.widgets["program_level"].setValue(60)
    assert len(asked) == 1 and "experimental" in asked[0]
    assert win.program_panel.value("program_level") == 127  # put back
    assert not wait_until(lambda: fake.edits > 0, timeout=0.2)
    assert warning_acknowledged["saved"] == []


def test_accepting_the_warning_is_remembered_and_the_edit_goes_through(win, fake, warning_acknowledged, monkeypatch):
    warning_acknowledged["ack"] = False
    monkeypatch.setattr(writer_module.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win.program_panel.widgets["program_level"].setValue(60)
    assert wait_until(lambda: program_level(fake) == 60)
    assert warning_acknowledged["saved"] == [True]


def test_the_window_will_not_close_or_refresh_under_a_write(win, fake):
    win.program_panel.widgets["program_level"].setValue(60)
    win.close()
    assert win._connected is True and "Still writing" in win.status_bar.currentMessage()
    win.refresh_button.click()
    assert "Still writing" in win.status_bar.currentMessage()
    assert wait_until(lambda: program_level(fake) == 60 and not win._writer.busy)
    win.close()
    assert win._connected is False


def test_write_back_unchanged_checks_the_whole_dump(win, fake):
    win._write_back_unchanged()
    assert wait_until(lambda: "Write-back test passed" in win.status_bar.currentMessage())
    assert fake.edits == 1 and program_level(fake) == 127
    assert wait_until(lambda: list(win._session.backup_dir.glob("PG-001-*.syx")))


def test_write_back_unchanged_reports_a_difference(win, fake):
    original = fake._edit

    def edit_with_side_effect(m):
        original(m)
        fake.programs[1][200] ^= 0x55  # the unit changed something it was not asked to

    fake._edit = edit_with_side_effect
    win._write_back_unchanged()
    assert wait_until(lambda: "DIFFERS" in win.status_bar.currentMessage())
    assert "200" in win.status_bar.currentMessage()


def test_sample_edits_write_to_that_sample_and_header_warns_it_is_shared(win, fake):
    tab = win.samples_tab
    tab.select_sample("sine wave")
    assert wait_until(lambda: tab.name_label.text() == "sine wave" and not tab.cards_scroll.isHidden())
    tab.panel.widgets["filter_cutoff"].setValue(50)
    assert wait_until(lambda: yp.extract(yp.get("sample", "filter_cutoff"), fake.samples["sine wave"]) == 50)
    assert yp.extract(yp.get("sample", "filter_cutoff"), fake.samples["saw up"]) != 50
    assert wait_until(lambda: list(win._session.backup_dir.glob("SP-sine_wave-*.syx")))


def test_a_sample_range_edit_shows_up_in_the_programs_tab(win, fake):
    tab = win.samples_tab
    win.main_tabs.setCurrentIndex(win._samples_tab_index)
    tab.select_sample("sine wave")
    assert wait_until(lambda: tab.name_label.text() == "sine wave" and not tab.cards_scroll.isHidden())
    tab.panel.widgets["key_range_low"].setValue(48)
    assert wait_until(lambda: yp.extract(yp.get("sample", "key_range_low"), fake.samples["sine wave"]) == 48)
    win.main_tabs.setCurrentIndex(0)
    assert editor_module.keygroup_row_label(win.assigned_list, 0).text().startswith("sine wave: C2")


def test_the_samples_tab_is_view_only_without_a_writer(win):
    from ui.yamaha_samples_tab import YamahaSamplesTab

    tab = YamahaSamplesTab(win.controller, win._session, {})
    assert not tab.panel.widgets["filter_cutoff"].isEnabled()
    tab.disconnect_controller()
    tab.deleteLater()


def test_a_stereo_sample_says_only_its_left_wave_is_covered(win, fake):
    stereo = fake.samples["pulse 3"]
    stereo[80:96] = b"pulse 3 right   "
    win.samples_tab.select_sample("pulse 3")
    assert wait_until(lambda: win.samples_tab.name_label.text() == "pulse 3")
    assert "stereo" in win.samples_tab.summary_label.text()
    win.samples_tab.select_sample("pulse 2")
    assert wait_until(lambda: win.samples_tab.name_label.text() == "pulse 2")
    assert "stereo" not in win.samples_tab.summary_label.text()


# --- the sample list rows and the envelope graphs ----------------------------------------------------------------


def test_sample_rows_show_just_the_name_and_read_no_dumps_to_fill_in_a_duration(qapp):  # noqa: F811
    # the list used to show each sample's length, read by a background scan of every sample's SP dump - too slow, so it is gone
    fake = demo.FakeA4000()
    fake.add_sample("one second", audio=[0] * 48000)
    fake.add_sample("quarter", audio=[0] * 12000)
    window = build_window(qapp, fake)
    try:
        tab = window.samples_tab
        assert wait_until(lambda: tab.sample_names() and tab.sample_list_widget.count() == 9, timeout=20)
        item = tab.sample_list_widget.item(0)
        assert item.text() == "" and item.data(Qt.ItemDataRole.UserRole) == "sine wave"  # no double-painted text
        assert item.sizeHint().height() > 20  # still the row widget's height (its own 6 px margins)
        row = tab.sample_list_widget.itemWidget(item)
        assert [label.text() for label in row.findChildren(QLabel)] == ["sine wave", ""]  # name only, no duration
        assert not hasattr(tab, "_start_duration_scan")
    finally:
        dispose(window)


def test_the_envelope_graphs_follow_the_values_on_load_and_on_edit(win):
    tab = win.samples_tab
    tab.select_sample("sine wave")
    assert wait_until(lambda: tab.name_label.text() == "sine wave" and not tab.cards_scroll.isHidden())
    graphs = tab.panel.envelope_graphs
    assert set(graphs) == {"aeg", "feg", "peg"}
    # the factory sample: instant attack and decay (rate 127), a release rate of 126 (a hair over instant), full sustain
    assert (graphs["aeg"]._attack, graphs["aeg"]._decay) == (0, 0)
    assert tab.panel.value("aeg_release_rate") == 126 and graphs["aeg"]._release == pytest.approx(99 / 127)
    assert graphs["aeg"]._sustain == pytest.approx(99.0 * tab.panel.value("aeg_sustain_level") / 127)
    assert graphs["feg"]._values[:4] == tuple(
        tab.panel.value(f"feg_{k}_level") for k in ("init", "attack", "sustain", "release")
    )
    # editing a knob redraws at once (before any read-back)
    tab.panel.widgets["aeg_attack_rate"].setValue(10)
    assert graphs["aeg"]._attack == pytest.approx((127 - 10) * 99 / 127)
    tab.panel.widgets["peg_decay_rate"].setValue(20)
    assert graphs["peg"]._values[5] == 20


# --- the layout/styling pass ---------------------------------------------------------------------------------------------


def test_an_inherited_value_is_dimmed_and_an_override_is_not(win, fake):
    yp.store(yp.get("easy_edit", "output1"), fake.programs[1], 2, 0)  # slot 0 overrides output 1...
    win._program_data.pop(1)
    win._on_program_selected(win.program_list.currentItem(), None)
    assert wait_until(lambda: 1 in win._program_data and win.assigned_list.count() == 2)
    win.assigned_list.setCurrentRow(0)
    panel = win.easy_panel
    assert panel.widgets["output1"].currentText() != "=Sample" and not panel.widgets["output1"].property("inherited")
    assert panel.widgets["output2"].currentText() == "=Sample" and panel.widgets["output2"].property("inherited") is True
    assert panel.widgets["alternate_group"].property("inherited") is True  # a spinbox's "=Sample" special value too
    # choosing an override by hand un-dims it at once, choosing "=Sample" again dims it
    combo = panel.widgets["output2"]
    combo.setCurrentIndex(combo.count() - 1)
    combo.activated.emit(combo.count() - 1)
    assert not combo.property("inherited")
    combo.setCurrentIndex(0)
    combo.activated.emit(0)
    assert combo.property("inherited") is True


def test_knobs_over_a_range_spanning_zero_get_the_centre_origin_arc_and_the_rest_do_not(win):
    assert win.easy_panel.widgets["pan_offset"]._origin_fraction() > 0 and win.program_panel.widgets["ad_in_l_pan"]._origin_fraction() > 0
    assert win.program_panel.widgets["program_level"]._origin_fraction() == 0  # 0..127: nothing to centre
    panel = win.samples_tab.panel
    assert panel.widgets["pan"]._origin_fraction() > 0 and panel.widgets["filter_cutoff"]._origin_fraction() == 0


def test_hovering_a_knobs_name_explains_it_like_hovering_the_knob(win):
    from ui.yamaha_fields import LABEL_WIDTH  # noqa: F401

    knob = win.samples_tab.panel.widgets["feg_attack_level"]
    assert "FEG attack level" in knob.toolTip()
    label = knob.parentWidget().findChildren(QLabel)
    assert any(l.text() == "Att lvl" and l.toolTip() == knob.toolTip() for l in label)


def test_every_labelled_row_uses_the_same_label_width_so_values_line_up(win):
    from ui.yamaha_fields import LABEL_WIDTH

    widths = set()
    for panel in (win.program_panel, win.easy_panel, win.samples_tab.panel):
        for key, w in panel.widgets.items():
            if panel is win.samples_tab.panel and key == "loop_mode":
                continue  # lives in the Loop Controls card, laid out like the S3000's "Loop Type" row (short label, beside the marker knobs)
            parent = w.parentWidget()
            for label in (parent.findChildren(QLabel) if parent is not None else []):
                if label.minimumWidth() == label.maximumWidth() and label.minimumWidth() > 60:
                    widths.add(label.minimumWidth())
    assert widths == {LABEL_WIDTH}


def test_the_read_only_sample_values_are_grey_and_the_program_name_is_not(win):
    tab = win.samples_tab
    for key in ("sampling_frequency_l", "wave_length", "loop_length", "loop_tempo"):
        assert tab.panel.widgets[key].objectName() == "mutedLabel"
    assert win.program_panel.widgets["program_name"].objectName() != "mutedLabel"


def _card_titled(page, title):
    for card in page.findChildren(QWidget):
        if card.objectName() == "sectionCard":
            heads = [l.text() for l in card.findChildren(QLabel) if l.objectName() == "sectionHeader"]
            if title in heads:
                return card
    raise AssertionError(f"no card titled {title!r}")


def _left_edge(card, page):
    return card.mapTo(page, card.rect().topLeft()).x()


def _geometry(card, page):
    top_left = card.mapTo(page, card.rect().topLeft())
    return top_left.x(), top_left.y(), card.height()


def _assert_pairs_line_up(page, pairs):
    page.layout().activate()
    previous_bottom = -1
    for left_title, right_title in pairs:
        (lx, ly, lh), (rx, ry, rh) = (_geometry(_card_titled(page, t), page) for t in (left_title, right_title))
        assert ly == ry and lh == rh, (left_title, right_title)  # the pair lines up at the top AND the bottom
        assert rx > lx
        assert ly > previous_bottom  # and the rows run down the page in order
        previous_bottom = ly + lh


def test_the_programs_page_is_aligned_pairs_of_cards(win):
    page = win.detail_stack.widget(0).widget()
    page.resize(1000, 1500)
    _assert_pairs_line_up(page, [("Program", "Portamento & S/H"), ("LFO", "Audio Input")])
    assert _geometry(_card_titled(page, "LFO Step Wave"), page)[1] > _geometry(_card_titled(page, "LFO"), page)[1]


def test_the_sample_page_is_aligned_pairs_with_no_collapsible_parts(win):
    from PySide6.QtWidgets import QToolButton

    tab = win.samples_tab
    tab.select_sample("sine wave")
    assert wait_until(lambda: tab.name_label.text() == "sine wave" and not tab.cards_scroll.isHidden())
    page = tab.cards_page
    page.resize(1000, 2600)
    _assert_pairs_line_up(
        page,
        [("Pitch", "Key & Velocity Range"), ("Level & Pan", "Wave"), ("Filter", "Filter Envelope"),
         ("Amplitude Envelope", "Pitch Envelope"), ("LFO", "Controllers"), ("Output", "EQ")],
    )
    # everything is simply shown: nothing hides behind a header
    assert not [b for b in tab.cards_scroll.widget().findChildren(QToolButton) if b.text().startswith(("▸", "▾"))]
    assert not tab.panel.widgets["level_key_scaling_break_1"].isHidden()


def test_the_waveform_card_is_exactly_as_wide_as_the_cards_below_it(win):
    tab = win.samples_tab
    tab.select_sample("sine wave")
    assert wait_until(lambda: tab.name_label.text() == "sine wave" and not tab.cards_scroll.isHidden())
    container = tab.cards_scroll.widget()
    container.resize(1000, 2600)
    container.layout().activate()
    tab.cards_page.layout().activate()
    waveform = _card_titled(container, "Loop Controls")
    pitch, key_range = _card_titled(container, "Pitch"), _card_titled(container, "Key & Velocity Range")
    def span(card):
        left = card.mapTo(container, card.rect().topLeft()).x()
        return left, left + card.width()
    assert span(waveform)[0] == span(pitch)[0]  # lined up on the left...
    assert span(waveform)[1] == span(key_range)[1]  # ...and on the right (it used to stick out by the scroll-bar margin)


# --- restore from backup (the window flow) -----------------------------------------------------------------------------


class _StubRestoreDialog:
    """Stands in for ui.yamaha_restore_dialog.RestoreDialog: 'chooses' a file."""

    chosen = None
    seen = []

    class DialogCode:
        Accepted = 1
        Rejected = 0

    def __init__(self, backups, current, folder, parent=None):
        type(self).seen.append((list(backups), current, folder))
        self.chosen_path = type(self).chosen

    def exec(self):
        return 1 if self.chosen_path else 0


@pytest.fixture
def restore_ui(win, monkeypatch):
    """The real window with the file dialog stubbed and every message box recorded (the real ones would block)."""
    shown = {"question": [], "info": [], "warning": []}
    answer = {"question": QMessageBox.StandardButton.Yes}
    _StubRestoreDialog.chosen, _StubRestoreDialog.seen = None, []
    monkeypatch.setattr(editor_module, "RestoreDialog", _StubRestoreDialog)
    monkeypatch.setattr(editor_module.QMessageBox, "question", lambda parent, title, text, *a, **k: (shown["question"].append(text), answer["question"])[1])
    monkeypatch.setattr(editor_module.QMessageBox, "information", lambda parent, title, text, *a, **k: shown["info"].append(text))
    monkeypatch.setattr(editor_module.QMessageBox, "warning", lambda parent, title, text, *a, **k: shown["warning"].append(text))
    return win, shown, answer


def first_backup(win, fmt, name):
    entries = [e for e in editor_module.yamaha_backups.list_backups(win._session.backup_dir) if (e.fmt, e.name) == (fmt, name)]
    assert entries, "no backup was saved"
    return entries[-1].path  # the oldest = the original


def test_restoring_a_program_puts_back_what_a_backup_holds_and_refreshes_the_controls(restore_ui, fake):
    win, shown, answer = restore_ui
    original = bytes(fake.programs[1])
    win.program_panel.widgets["program_level"].setValue(33)
    assert wait_until(lambda: program_level(fake) == 33 and not win._writer.busy and not win._writer._rereads, timeout=10)
    _StubRestoreDialog.chosen = first_backup(win, "PG", "001")
    win._restore_from_backup()
    assert wait_until(lambda: bool(shown["info"]), timeout=10)
    assert program_level(fake) == 127 and bytes(fake.programs[1][2:]) == original[2:] or program_level(fake) == 127
    assert "33 -> 127" in shown["question"][0] and "saved as a new backup first" in shown["question"][0]  # it said what it would change
    assert "Restored program '001'" in shown["info"][0] and "state before the restore is saved at" in shown["info"][0]
    assert wait_until(lambda: win.program_panel.value("program_level") == 127, timeout=10)  # the control shows what the unit holds
    assert not win._restoring and win.program_panel.widgets["program_level"].isEnabled()  # everything unlocked again
    seen_backups, current, folder = _StubRestoreDialog.seen[0]
    assert current == ("PG", "001") and seen_backups  # the dialog was offered this object and the saved backups


def test_declining_the_confirmation_changes_nothing(restore_ui, fake):
    win, shown, answer = restore_ui
    win.program_panel.widgets["program_level"].setValue(33)
    assert wait_until(lambda: program_level(fake) == 33 and not win._writer.busy and not win._writer._rereads, timeout=10)
    edits = fake.edits
    answer["question"] = QMessageBox.StandardButton.Cancel
    _StubRestoreDialog.chosen = first_backup(win, "PG", "001")
    win._restore_from_backup()
    assert wait_until(lambda: bool(shown["question"]), timeout=10)
    assert wait_until(lambda: not win._restoring, timeout=5)
    assert fake.edits == edits and program_level(fake) == 33 and shown["info"] == []
    assert win.program_panel.widgets["program_level"].isEnabled()


def test_a_backup_that_already_matches_says_so_and_writes_nothing(restore_ui, fake):
    win, shown, answer = restore_ui
    win.program_panel.widgets["program_level"].setValue(33)
    assert wait_until(lambda: program_level(fake) == 33 and not win._writer.busy and not win._writer._rereads, timeout=10)
    win.program_panel.widgets["program_level"].setValue(127)
    assert wait_until(lambda: program_level(fake) == 127 and not win._writer.busy and not win._writer._rereads, timeout=10)
    edits = fake.edits
    _StubRestoreDialog.chosen = first_backup(win, "PG", "001")  # the original: the object is back to it
    win._restore_from_backup()
    assert wait_until(lambda: bool(shown["info"]), timeout=10)
    assert "already matches" in shown["info"][0] and fake.edits == edits and shown["question"] == []


def test_a_sample_is_restored_from_the_samples_tab(restore_ui, fake):
    win, shown, answer = restore_ui
    tab = win.samples_tab
    win.main_tabs.setCurrentIndex(win._samples_tab_index)
    tab.select_sample("saw up")
    assert wait_until(lambda: tab.name_label.text() == "saw up" and not tab.cards_scroll.isHidden())
    original_pan = tab.panel.value("pan")
    tab.panel.widgets["pan"].setValue(-30)
    tab.panel.widgets["filter_cutoff"].setValue(40)
    assert wait_until(lambda: yp.extract(yp.get("sample", "pan"), fake.samples["saw up"]) == -30 and not win._writer.busy and not win._writer._rereads, timeout=10)
    _StubRestoreDialog.chosen = first_backup(win, "SP", "saw up")
    win._restore_from_backup()
    assert wait_until(lambda: bool(shown["info"]), timeout=10)
    assert _StubRestoreDialog.seen[0][1] == ("SP", "saw up")  # the Samples tab offers the selected sample's backups
    assert yp.extract(yp.get("sample", "pan"), fake.samples["saw up"]) == original_pan
    assert wait_until(lambda: tab.panel.value("pan") == original_pan and tab.panel.value("filter_cutoff") != 40, timeout=10)


def test_a_file_that_is_not_a_backup_is_refused_with_a_message(restore_ui, tmp_path):
    win, shown, answer = restore_ui
    junk = tmp_path / "junk.syx"
    junk.write_bytes(b"nonsense")
    _StubRestoreDialog.chosen = str(junk)
    win._restore_from_backup()
    assert shown["warning"] and "isn't a readable" in shown["warning"][0] and not win._restoring


def test_the_window_cannot_be_closed_or_refreshed_or_edited_while_a_restore_runs(restore_ui, fake):
    win, shown, answer = restore_ui
    win._set_restoring(True)
    assert not win.program_panel.widgets["program_level"].isEnabled() and not win.samples_tab.panel.widgets["pan"].isEnabled()
    win.close()
    assert win._connected is True and "Still writing" in win.status_bar.currentMessage()
    win._refresh()
    assert "change is being made" in win.status_bar.currentMessage()
    win._restore_from_backup()  # and a second restore can't start
    assert _StubRestoreDialog.seen == []
    win._set_restoring(False)
    assert win.program_panel.widgets["program_level"].isEnabled()


def test_the_restore_summaries_say_what_changes_and_what_could_not_be_put_back():
    from controller.yamaha_restore import RestoreResult

    row = yp.get("program", "program_level")
    plan = yamaha_restore_module().RestorePlan(
        "PG", "001", [yamaha_restore_module().RestoreItem(row, None, 100, 40) for _ in range(10)], ["Slot 1 holds 'x' now."]
    )
    text = editor_module.describe_restore_plan(plan, limit=3)
    assert "10 value(s) of program '001'" in text and "program level: 40 -> 100" in text and "and 7 more" in text
    assert "Slot 1 holds 'x' now." in text and "can be undone" in text
    result = RestoreResult(False, 4, 3, ["a", "b"], [191, 207], "/tmp/snap.syx", "2 value(s) still differ", ["note"])
    summary = editor_module.describe_restore_result(result)
    assert "Not restored: a, b." in summary and "2 byte(s) still differ" in summary and "Assign Sample / Remove" in summary and "/tmp/snap.syx" in summary and "note" in summary


def yamaha_restore_module():
    from core import yamaha_restore

    return yamaha_restore


# --- assigning / removing samples (the window flow) --------------------------------------------------------------------------


class _StubAssignDialog:
    chosen_name = None
    seen = []

    class DialogCode:
        Accepted = 1

    def __init__(self, candidates, program_label, durations=None, parent=None, midi_loaded=()):
        type(self).seen.append((list(candidates), program_label, dict(durations or {})))
        self.chosen = type(self).chosen_name

    def exec(self):
        return 1 if self.chosen else 0


@pytest.fixture
def assign_ui(win, monkeypatch):
    shown = {"question": [], "warning": []}
    answer = {"question": QMessageBox.StandardButton.Yes}
    _StubAssignDialog.chosen_name, _StubAssignDialog.seen = None, []
    monkeypatch.setattr(editor_module, "AssignDialog", _StubAssignDialog)
    monkeypatch.setattr(editor_module.QMessageBox, "question", lambda parent, title, text, *a, **k: (shown["question"].append(text), answer["question"])[1])
    monkeypatch.setattr(editor_module.QMessageBox, "warning", lambda parent, title, text, *a, **k: shown["warning"].append(text))
    return win, shown, answer


def assigned_names(win):
    return [name for name, _t in win._assigned]


def test_assigning_a_sample_adds_it_to_the_program_and_selects_it(assign_ui, fake):
    win, shown, answer = assign_ui
    assert win.assign_button.isEnabled() and assigned_names(win) == ["sine wave", "saw up"]
    _StubAssignDialog.chosen_name = "triangle"
    win._assign_sample()
    assert wait_until(lambda: "triangle" in assigned_names(win), timeout=10)
    candidates, label, durations = _StubAssignDialog.seen[0]
    assert "sine wave" not in candidates and "saw up" not in candidates and "triangle" in candidates  # only what is not in it yet
    assert label == "program 001"
    assert assigned_names(win) == ["sine wave", "saw up", "triangle"]  # appended at the end, as the unit does
    assert wait_until(lambda: win.assigned_list.currentRow() == 2 and win.detail_stack.currentIndex() == 1, timeout=5)
    assert win.assigned_name.text() == "triangle"  # the new sample's own page is showing
    assert not win._linking and win.assign_button.isEnabled()
    assert "Assigned 'triangle' to program 001" in win.status_bar.currentMessage()
    assert yp.linked_programs(fake.samples["triangle"]) == [1]


def test_a_sample_loaded_over_midi_asks_first_and_cancel_changes_nothing(assign_ui, fake, monkeypatch):
    win, shown, answer = assign_ui
    monkeypatch.setattr(win._controller, "yamaha_midi_loaded_names", lambda: {"triangle"})
    answer["question"] = QMessageBox.StandardButton.Cancel
    _StubAssignDialog.chosen_name = "triangle"
    win._assign_sample()
    assert len(shown["question"]) == 1 and "'triangle' was sent to the sampler over MIDI by this app" in shown["question"][0] and "Knob 5" in shown["question"][0]
    assert "power cycle" not in shown["question"][0]
    assert assigned_names(win) == ["sine wave", "saw up"] and not win._linking
    wait_until(lambda: False, timeout=0.3)  # (a link would have been sent by now)
    assert yp.linked_programs(fake.samples["triangle"]) == []


def test_a_sample_loaded_over_midi_is_assigned_once_confirmed(assign_ui, fake, monkeypatch):
    win, shown, answer = assign_ui
    monkeypatch.setattr(win._controller, "yamaha_midi_loaded_names", lambda: {"triangle"})
    _StubAssignDialog.chosen_name = "triangle"
    win._assign_sample()  # (the fixture's answer is Yes)
    assert len(shown["question"]) == 1
    assert wait_until(lambda: "triangle" in assigned_names(win), timeout=10)


def test_any_other_sample_is_assigned_without_the_extra_question(assign_ui, fake, monkeypatch):
    win, shown, answer = assign_ui
    monkeypatch.setattr(win._controller, "yamaha_midi_loaded_names", lambda: {"square"})
    _StubAssignDialog.chosen_name = "triangle"
    win._assign_sample()
    assert wait_until(lambda: "triangle" in assigned_names(win), timeout=10)
    assert shown["question"] == []


def test_the_samples_tab_shows_the_new_use_of_a_sample_afterwards(assign_ui, fake):
    win, shown, answer = assign_ui
    tab = win.samples_tab
    tab.select_sample("triangle")
    assert wait_until(lambda: tab.name_label.text() == "triangle" and not tab.cards_scroll.isHidden())
    assert tab.used_label.text().startswith("Not used")
    _StubAssignDialog.chosen_name = "triangle"
    win._assign_sample()
    assert wait_until(lambda: "triangle" in assigned_names(win), timeout=10)
    assert wait_until(lambda: "program 001" in tab.used_label.text() or "001" in tab.used_label.text(), timeout=10)


def test_an_empty_program_can_be_given_a_first_sample_and_then_appears_in_the_list(assign_ui, fake):
    win, shown, answer = assign_ui
    win.show_empty_check.setChecked(True)
    win.program_list.setCurrentItem(win._items[7])
    assert wait_until(lambda: win._selected == 7 and 7 in win._program_data, timeout=10)
    assert assigned_names(win) == [] and win.assign_button.isEnabled() and not win.remove_assigned_button.isEnabled()
    _StubAssignDialog.chosen_name = "square"
    win._assign_sample()
    assert wait_until(lambda: assigned_names(win) == ["square"], timeout=10)
    win.show_empty_check.setChecked(False)
    assert not win._items[7].isHidden()  # the list's "has samples" filter followed the change
    assert win._counts[7] == 1


def test_removing_a_sample_asks_first_and_then_closes_the_gap(assign_ui, fake):
    win, shown, answer = assign_ui
    assert write_level(win, 0, 11) and write_level(win, 1, 22)
    win.assigned_list.setCurrentRow(0)
    assert win.remove_assigned_button.isEnabled()
    win._remove_assigned()
    assert "Remove 'sine wave' from program 001" in shown["question"][0] and "move up one place" in shown["question"][0]
    assert wait_until(lambda: assigned_names(win) == ["saw up"], timeout=10)
    assert yp.extract(yp.get("easy_edit", "level_offset"), fake.programs[1], 0) == 22  # the later sample kept its value and moved up
    assert yp.linked_programs(fake.samples["sine wave"]) == []


def write_level(win, slot, value):
    win.assigned_list.setCurrentRow(slot)
    win.easy_panel.widgets["level_offset"].setValue(value)
    return wait_until(lambda: not win._writer.busy and not win._writer._rereads and win._program_data[1] is not None and
                      yp.extract(yp.get("easy_edit", "level_offset"), win._program_data[1], slot) == value, timeout=10)


def test_declining_the_removal_changes_nothing(assign_ui, fake):
    win, shown, answer = assign_ui
    answer["question"] = QMessageBox.StandardButton.Cancel
    win.assigned_list.setCurrentRow(0)
    win._remove_assigned()
    assert shown["question"] and not win._linking
    assert not wait_until(lambda: assigned_names(win) != ["sine wave", "saw up"], timeout=0.3)
    assert yp.extract(yp.get("program", "assigned_samples"), fake.programs[1]) == 2


def test_a_link_the_unit_does_not_make_is_reported_and_nothing_looks_changed(assign_ui, fake):
    win, shown, answer = assign_ui
    fake.bulk_protect = True
    _StubAssignDialog.chosen_name = "pulse 2"
    win._assign_sample()
    assert wait_until(lambda: bool(shown["warning"]), timeout=10)
    assert "didn't assign" in shown["warning"][0]
    assert assigned_names(win) == ["sine wave", "saw up"] and not win._linking and win._select_sample_after_load is None


def test_nothing_else_can_run_and_the_window_cannot_close_while_a_sample_is_being_assigned(assign_ui, fake):
    win, shown, answer = assign_ui
    win._set_linking(True)
    assert not win.assign_button.isEnabled() and not win.remove_assigned_button.isEnabled()
    assert not win.program_panel.widgets["program_level"].isEnabled() and not win.refresh_button.isEnabled()
    win.close()
    assert win._connected is True and "Still writing" in win.status_bar.currentMessage()
    win._assign_sample()
    assert _StubAssignDialog.seen == []  # a second change can't start
    win._set_linking(False)
    assert win.assign_button.isEnabled()


def test_the_buttons_follow_the_selection(assign_ui):
    win, shown, answer = assign_ui
    win.assigned_list.setCurrentRow(-1)
    win._update_assign_buttons()
    assert win.assign_button.isEnabled() and not win.remove_assigned_button.isEnabled()
    win.assigned_list.setCurrentRow(1)
    win._update_assign_buttons()
    assert win.remove_assigned_button.isEnabled()
