# tests for ui/settings_dialog.py - currently just _minimum_width_for_pages
# (the dialog-width-from-content calculation) plus a couple of real,
# end-to-end MidiSettingsDialog construction checks. Everything else in this
# dialog (port selection, the release/restore hardware-test dance, etc.) has
# no dedicated coverage yet - this file only covers what was actually added/
# fixed, per this repo's own stated testing philosophy (see TESTING.md).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from ui.settings_dialog import (
    MidiSettingsDialog,
    _MINIMUM_DIALOG_WIDTH,
    _SCROLLBAR_WIDTH_ALLOWANCE,
    _minimum_width_for_pages,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakePage:
    # duck-types the one thing _minimum_width_for_pages actually reads -
    # no real QWidget/QApplication needed for these
    def __init__(self, width):
        self._width = width

    def sizeHint(self):
        return QSize(self._width, 400)


# --- _minimum_width_for_pages -------------------------------------------------


def test_uses_the_floor_when_every_page_is_narrower_than_it(qapp):
    pages = [_FakePage(100), _FakePage(200)]
    assert _minimum_width_for_pages(pages) == _MINIMUM_DIALOG_WIDTH


def test_grows_past_the_floor_for_a_wide_page(qapp):
    pages = [_FakePage(900)]
    assert _minimum_width_for_pages(pages) == 900 + _SCROLLBAR_WIDTH_ALLOWANCE


def test_uses_the_widest_page_not_just_the_first_or_last(qapp):
    pages = [_FakePage(700), _FakePage(1200), _FakePage(300)]
    assert _minimum_width_for_pages(pages) == 1200 + _SCROLLBAR_WIDTH_ALLOWANCE


def test_empty_page_list_returns_the_floor(qapp):
    assert _minimum_width_for_pages([]) == _MINIMUM_DIALOG_WIDTH


def test_custom_minimum_is_respected_as_the_floor(qapp):
    assert _minimum_width_for_pages([_FakePage(50)], minimum=800) == 800


# --- MidiSettingsDialog: end-to-end width calculation -------------------------


class _FakeMidiManager:
    def __init__(self, input_names, output_names):
        self._input_names = input_names
        self._output_names = output_names
        self.input_name = None
        self.output_name = None

    def list_inputs(self):
        return list(self._input_names)

    def list_outputs(self):
        return list(self._output_names)

    def open_input(self, name):
        self.input_name = name

    def open_output(self, name):
        self.output_name = name


class _FakeSamplerController:
    channel = 0
    device_type = "akai"
    sampler_model = "akai_s2000_s3000"

    def is_open_loop(self):
        return False

    def set_channel(self, channel):
        self.channel = channel

    def set_device_type(self, selection):
        self.sampler_model = selection
        self.device_type = {"generic": "generic", "akai_s900_s950": "s950"}.get(
            selection, "akai"
        )


def test_dialog_width_never_drops_below_the_floor_with_short_port_names(qapp):
    # NOT necessarily exactly the floor - the page's own explanatory note
    # labels (setWordWrap(True) QLabels) have a real unwrapped-width
    # sizeHint of their own, independent of the comboboxes, which can
    # already exceed the floor on their own. The floor is a lower BOUND,
    # not a value this test can assume short combo content collapses to
    # exactly - see test_dialog_widens_for_a_long_port_name below for the
    # actual combo-driven-growth behaviour, isolated as a relative
    # comparison instead of an absolute one for exactly this reason.
    midi_manager = _FakeMidiManager(["In A"], ["Out A"])
    dialog = MidiSettingsDialog(midi_manager, _FakeSamplerController())
    assert dialog.minimumWidth() >= _MINIMUM_DIALOG_WIDTH


def test_dialog_widens_for_a_long_port_name(qapp):
    long_name = "Some USB MIDI Interface With An Unusually Long Self-Reported Device Name " * 2
    short_manager = _FakeMidiManager(["In A"], ["Out A"])
    long_manager = _FakeMidiManager(["In A", long_name], ["Out A"])
    short_dialog = MidiSettingsDialog(short_manager, _FakeSamplerController())
    long_dialog = MidiSettingsDialog(long_manager, _FakeSamplerController())
    assert long_dialog.minimumWidth() > short_dialog.minimumWidth()


def test_dialog_width_ignores_which_tab_is_currently_shown(qapp):
    # the wide combo lives on the Settings tab, which is index 0 (already
    # showing by default) - construct twice and confirm the SAME width
    # comes out regardless, since _minimum_width_for_pages measures BOTH
    # pages up front rather than just whichever tab happens to be visible
    long_name = "Some USB MIDI Interface With An Unusually Long Self-Reported Device Name " * 2
    midi_manager = _FakeMidiManager(["In A", long_name], ["Out A"])
    dialog_a = MidiSettingsDialog(midi_manager, _FakeSamplerController())
    dialog_b = MidiSettingsDialog(midi_manager, _FakeSamplerController())
    assert dialog_a.minimumWidth() == dialog_b.minimumWidth()


# --- Sampler Type combo (S1000 / S2000/S3000 / Generic) ----------------------


def test_sampler_type_combo_lists_the_five_models_alphabetically(qapp):
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    labels = [
        dialog.combo_device_type.itemText(i)
        for i in range(dialog.combo_device_type.count())
    ]
    assert labels == [
        "Akai S1000",
        "Akai S2000/S3000",
        "Akai S900/S950 (experimental)",
        "Generic SDS",
        "Yamaha A4000/A5000 (experimental)",
    ]
    assert labels == sorted(labels)
    assert [dialog.combo_device_type.itemData(i) for i in range(5)] == [
        "akai_s1000",
        "akai_s2000_s3000",
        "akai_s900_s950",
        "generic",
        "yamaha_a4000",
    ]


def test_sampler_type_combo_starts_on_the_controllers_current_model(qapp):
    controller = _FakeSamplerController()
    controller.sampler_model = "akai_s1000"
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), controller)
    assert dialog.combo_device_type.currentData() == "akai_s1000"


def test_hardware_test_speaks_the_akai_protocol_for_the_s1000(qapp):
    # the Run Hardware Test button only cares about the protocol family
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    for selection, family in [
        ("akai_s1000", "akai"),
        ("akai_s2000_s3000", "akai"),
        ("akai_s900_s950", "s950"),
        ("generic", "generic"),
        ("yamaha_a4000", "generic"),
    ]:
        dialog.combo_device_type.setCurrentIndex(
            dialog.combo_device_type.findData(selection)
        )
        assert dialog._selected_protocol_family() == family


# --- Settings tab + Appearance card ---------------------------------------------------------------


def _tab_titles(dialog):
    from PySide6.QtWidgets import QTabWidget

    tabs = dialog.findChild(QTabWidget)
    return [tabs.tabText(i) for i in range(tabs.count())]


def test_first_tab_is_called_settings(qapp):
    # was "Audio/MIDI" until the Appearance card joined it
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    assert _tab_titles(dialog) == ["Settings", "Troubleshooting"]


def test_troubleshooting_notes_point_at_the_renamed_tab(qapp):
    from PySide6.QtWidgets import QLabel

    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    notes = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "Audio/MIDI" not in notes
    assert "Settings tab" in notes


def test_appearance_card_offers_system_light_dark_defaulting_to_system(qapp, monkeypatch, tmp_path):
    from core import app_config
    from PySide6.QtWidgets import QLabel

    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "config.json")
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    combo = dialog.combo_theme
    assert [combo.itemText(i) for i in range(combo.count())] == ["System", "Light", "Dark"]
    assert [combo.itemData(i) for i in range(combo.count())] == ["system", "light", "dark"]
    assert combo.currentData() == "system"
    headers = [l.text() for l in dialog.findChildren(QLabel) if l.objectName() == "sectionHeader"]
    assert "Appearance" in headers


def test_appearance_combo_starts_on_the_saved_theme(qapp, monkeypatch, tmp_path):
    from core import app_config

    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "config.json")
    app_config.save_theme("dark")
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    assert dialog.combo_theme.currentData() == "dark"


class _ApplyableMidiManager(_FakeMidiManager):
    input_name = None
    output_name = None

    def open_input(self, name):
        self.input_name = name

    def open_output(self, name):
        self.output_name = name


def _record_theme_applications(monkeypatch):
    from ui import settings_dialog

    applied = []
    monkeypatch.setattr(settings_dialog.theme, "set_theme_preference", applied.append)
    return applied


def test_picking_a_theme_saves_and_applies_it_immediately(qapp, monkeypatch, tmp_path):
    # not on OK - the change is live while the dialog is still open
    from core import app_config

    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "config.json")
    applied = _record_theme_applications(monkeypatch)
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())

    index = dialog.combo_theme.findData("light")
    dialog.combo_theme.setCurrentIndex(index)
    dialog.combo_theme.activated.emit(index)  # what a user's click emits

    assert applied == ["light"]
    assert app_config.get_saved_theme() == "light"


def test_each_pick_is_applied_in_turn(qapp, monkeypatch, tmp_path):
    from core import app_config

    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "config.json")
    applied = _record_theme_applications(monkeypatch)
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    for value in ("dark", "light", "system"):
        index = dialog.combo_theme.findData(value)
        dialog.combo_theme.setCurrentIndex(index)
        dialog.combo_theme.activated.emit(index)
    assert applied == ["dark", "light", "system"]
    assert app_config.get_saved_theme() == "system"


def test_opening_the_dialog_does_not_reapply_the_saved_theme(qapp, monkeypatch, tmp_path):
    # restoring the saved value into the combo is a programmatic change,
    # not a user pick - it must not write or restyle anything
    from core import app_config

    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "config.json")
    app_config.save_theme("dark")
    applied = _record_theme_applications(monkeypatch)
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    dialog.combo_theme.setCurrentIndex(dialog.combo_theme.findData("light"))  # programmatic
    assert applied == []
    assert app_config.get_saved_theme() == "dark"


def test_ok_and_cancel_leave_the_theme_to_the_combo(qapp, monkeypatch, tmp_path):
    # the theme was already applied when picked, so neither button touches it
    from core import app_config

    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "config.json")
    applied = _record_theme_applications(monkeypatch)
    dialog = MidiSettingsDialog(_ApplyableMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    dialog.combo_theme.setCurrentIndex(dialog.combo_theme.findData("dark"))  # not a pick

    dialog._apply_and_close()
    dialog.reject()

    assert applied == []
    assert app_config.get_saved_theme() == "system"


# --- entry fields line up across the section cards ----------------------------------------------


def _field_x(dialog, widget):
    from PySide6.QtCore import QPoint

    return widget.mapTo(dialog, QPoint(0, 0)).x()


def test_entry_fields_start_at_the_same_x_in_every_card(qapp):
    # each card is its own QFormLayout, which sizes its label column to its
    # own widest label - so MIDI Input/Output, Audio Output and Appearance
    # used to start their fields at three different x positions
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    dialog.show()
    qapp.processEvents()
    fields = [
        dialog.combo_input,
        dialog.combo_output,
        dialog.spin_channel,
        dialog.combo_device_type,
        dialog.combo_audio_output,
        dialog.combo_audio_buffer,
        dialog.combo_theme,
    ]
    xs = {_field_x(dialog, field) for field in fields}
    assert len(xs) == 1, f"fields start at different x positions: {sorted(xs)}"
    dialog.hide()


def test_fields_stay_aligned_when_the_dialog_is_resized(qapp):
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    dialog.show()
    dialog.resize(dialog.width() + 200, dialog.height())
    qapp.processEvents()
    assert len({_field_x(dialog, f) for f in (dialog.combo_input, dialog.combo_audio_output, dialog.combo_theme)}) == 1
    dialog.hide()


def test_align_form_label_columns_equalises_every_label(qapp):
    from PySide6.QtWidgets import QFormLayout, QLabel, QLineEdit
    from ui.qt_helpers import align_form_label_columns

    forms = []
    labels = []
    for text in ("A:", "A much longer label:", "Mid:"):
        form = QFormLayout()
        label = QLabel(text)
        form.addRow(label, QLineEdit())
        forms.append(form)
        labels.append(label)
    # a note spanning the full width has no label and must be skipped
    forms[0].addRow(QLabel("spanning note"))

    align_form_label_columns(*forms)

    widest = max(label.sizeHint().width() for label in labels)
    assert [label.minimumWidth() for label in labels] == [widest] * 3


def test_align_form_label_columns_tolerates_no_labels(qapp):
    from PySide6.QtWidgets import QFormLayout
    from ui.qt_helpers import align_form_label_columns

    align_form_label_columns(QFormLayout())  # nothing to do, no crash
    align_form_label_columns()


# --- Run Hardware Test against an S900/S950 -------------------------------------------------------


class _PortsOnFakeS950:
    # a mido-style (send/poll) output+input pair wired to a FakeS950 - the
    # dialog's hardware test talks to real mido ports, this stands in for them
    def __init__(self, fake):
        self.fake = fake

    def send(self, message):
        self.fake.out.send_message([0xF0, *message.data, 0xF7])

    def poll(self):
        import mido

        got = self.fake.inp.get_message()
        if got is None:
            return None
        return mido.Message("sysex", data=got[0][1:-1])


def _s950_dialog(qapp):
    dialog = MidiSettingsDialog(
        _FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController()
    )
    dialog.combo_device_type.setCurrentIndex(
        dialog.combo_device_type.findData("akai_s900_s950")
    )
    return dialog


def test_s950_hardware_test_asks_for_a_catalog_and_reports_the_counts(qapp):
    from core.demo_s950 import FakeS950

    fake = FakeS950()
    ports = _PortsOnFakeS950(fake)
    dialog = _s950_dialog(qapp)
    ok, entries = dialog._send_identity_request(ports, ports)
    assert ok
    # exactly one request went out, and it was a catalog request
    assert len(fake.received) == 1 and fake.received[0][1:4] == [0x47, 0x00, 0x03]
    text = dialog._format_s950_result(entries)
    assert "S900/S950" in text
    assert "Programs in memory: 2" in text and "Samples in memory: 3" in text


def test_s950_hardware_test_uses_the_dialogs_channel(qapp):
    from core.demo_s950 import FakeS950

    fake = FakeS950(channel=4)
    ports = _PortsOnFakeS950(fake)
    dialog = _s950_dialog(qapp)
    dialog.spin_channel.setValue(4)
    ok, entries = dialog._send_identity_request(ports, ports)
    assert ok and len(entries) == 5


def test_s950_hardware_test_reports_an_unreadable_reply(qapp):
    import mido

    class _BadReply:
        def __init__(self):
            self.sent = False

        def send(self, message):
            self.sent = True

        def poll(self):
            if not self.sent:
                return None
            self.sent = False
            # an S1000-family RSTAT reply: right manufacturer, wrong device byte
            return mido.Message("sysex", data=[0x47, 0x00, 0x02, 0x48, 0x00, 0x00, 0x00])

    ports = _BadReply()
    ok, message = _s950_dialog(qapp)._send_identity_request(ports, ports)
    assert not ok
    assert "doesn't look like an S900/S950 catalog" in message


def test_s950_hardware_test_reports_no_answer(qapp, monkeypatch):
    import ui.settings_dialog as settings_dialog

    monkeypatch.setattr(settings_dialog, "_LOOPBACK_RECEIVE_TIMEOUT", 0.05)

    class _Silent:
        def send(self, message):
            pass

        def poll(self):
            return None

    ports = _Silent()
    ok, message = _s950_dialog(qapp)._send_identity_request(ports, ports)
    assert not ok and "not found" in message
