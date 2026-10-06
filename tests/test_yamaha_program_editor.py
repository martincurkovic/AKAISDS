# tests for ui/yamaha_program_editor.py + ui/yamaha_samples_tab.py - the Yamaha A4000 editor (view-only).
#
# The real window, real SamplerController and real YamahaSession run against core/demo_a4000.FakeA4000
# over the S950 tests' fake MidiManager. Offscreen Qt; every wire wait is shrunk.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QMainWindow, QSpinBox

from controller.sampler_controller import SamplerController
from core import demo_a4000 as demo
from core import yamaha_params as yp
from ui import yamaha_program_editor as editor_module
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
    session.reply_timeout_ms = 400
    session.bulk_timeout_ms = 600
    main = _Main()
    window = YamahaProgramEditorWindow(main, controller)
    window.main, window.controller, window.midi, window.fake = main, controller, midi, fake
    return window


def dispose(window):
    window._scan_generation += 1
    window._session.cancel()
    window._connected = False
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


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


def test_every_control_is_view_only(win):
    for panel in (win.program_panel, win.easy_panel, win.samples_tab.panel):
        for key, widget in panel.widgets.items():
            if isinstance(widget, QLabel):
                continue
            assert not widget.isEnabled(), (panel.scope, key)


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
    assert [tab.sample_list_widget.item(i).text() for i in range(tab.sample_list_widget.count())] == list(
        demo.FACTORY_SAMPLES
    )
    tab.select_sample("sine wave")
    assert wait_until(lambda: not tab.cards_scroll.isHidden() and tab.name_label.text() == "sine wave")
    assert tab.panel.value("sample_level") == 100
    assert tab.panel.value("original_key_l") == 66
    assert tab.panel.value("fine_tune_l") == -20
    assert "48,000 Hz" in tab.summary_label.text()
    assert tab.used_label.text() == "Used in programs 001"  # the fake marks the link map, like the unit


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
