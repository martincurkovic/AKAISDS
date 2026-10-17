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


def test_sampler_type_combo_lists_the_seven_models_alphabetically(qapp):
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    labels = [
        dialog.combo_device_type.itemText(i)
        for i in range(dialog.combo_device_type.count())
    ]
    assert labels == [
        "Akai S1000",
        "Akai S2000/S3000",
        "Akai S900/S950 (experimental)",
        "Ensoniq ASR-X (untested, experimental)",
        "Ensoniq EPS/ASR (untested, experimental)",
        "Generic SDS",
        "Yamaha A4000/A5000 (experimental)",
    ]
    assert labels == sorted(labels)
    assert [dialog.combo_device_type.itemData(i) for i in range(7)] == [
        "akai_s1000",
        "akai_s2000_s3000",
        "akai_s900_s950",
        "ensoniq_asrx",
        "ensoniq_eps_asr",
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
        ("ensoniq_eps_asr", "ensoniq"),
        ("ensoniq_asrx", "ensoniq"),
        ("generic", "generic"),
        ("yamaha_a4000", "generic"),
    ]:
        dialog.combo_device_type.setCurrentIndex(
            dialog.combo_device_type.findData(selection)
        )
        assert dialog._selected_protocol_family() == family


class _FakePort:
    # the mido-port surface _send_identity_request uses: sent messages are recorded. `script` is one list of replies PER SEND: the n-th
    # message sent through the paired output port makes the n-th list available on this (input) port, and anything left over from an
    # earlier send is dropped, as a real drain-before-send would
    def __init__(self, script=()):
        self.sent = []
        self._script = [list(r) for r in script]
        self._queue = []
        self.peer = None

    def send(self, message):
        self.sent.append(message)
        if self.peer is not None:
            self.peer._queue = self.peer._script.pop(0) if self.peer._script else []

    def poll(self):
        return self._queue.pop(0) if self._queue else None


class _Reply:
    type = "sysex"

    def __init__(self, data):
        self.data = tuple(data)


def _ensoniq_dialog(qapp, device_id=0):
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    dialog.combo_device_type.setCurrentIndex(dialog.combo_device_type.findData("ensoniq_eps_asr"))
    dialog.spin_channel.setValue(device_id)
    return dialog


def _paired(script):
    inp, out = _FakePort(script), _FakePort()
    out.peer = inp
    return inp, out


def _identity_reply(version=(1, 0, 0, 0)):
    # F0 7E <dev> 06 02 <manufacturer 0F> <family lo hi> <model lo hi> <version x4> F7 (the body between F0 and F7)
    return _Reply([0x7E, 0x00, 0x06, 0x02, 0x0F, 0x03, 0x00, 0x01, 0x00, *version])


def test_the_ensoniq_hardware_test_asks_for_the_identity_then_one_read_only_get_instrument(qapp):
    from core import ensoniq_sysex as e

    dialog = _ensoniq_dialog(qapp, device_id=3)
    inp, out = _paired([[_identity_reply()], [_Reply(e.build_response(e.STATUS_ACK, 3))]])
    ok, result = dialog._send_identity_request(inp, out)
    assert ok and result.command == e.CMD_RESPONSE
    # exactly two read-only requests, one outstanding at a time: the universal identity request, then GET INSTRUMENT 1
    assert [m.data for m in out.sent] == [(0x7E, 0x7F, 0x06, 0x01), tuple(e.build_get_instrument(0, channel=3))]
    text = dialog._format_ensoniq_result(result)
    assert "Instrument 1 is loaded" in text and "MIDI identity: " in text and "family 3" in text


def test_the_identity_answer_is_optional(qapp, monkeypatch):
    from core import ensoniq_sysex as e
    from ui import settings_dialog

    monkeypatch.setattr(settings_dialog, "_ENSONIQ_IDENTITY_TIMEOUT", 0.02)
    dialog = _ensoniq_dialog(qapp)
    inp, out = _paired([[], [_Reply(e.build_response(e.STATUS_INVALID_INSTRUMENT))]])
    ok, result = dialog._send_identity_request(inp, out)
    assert ok
    text = dialog._format_ensoniq_result(result)
    assert "isn't loaded (a normal answer)" in text and "No MIDI identity reply." in text


def test_an_unreadable_identity_reply_does_not_break_the_test(qapp, monkeypatch):
    from core import ensoniq_sysex as e
    from ui import settings_dialog

    monkeypatch.setattr(settings_dialog, "_ENSONIQ_IDENTITY_TIMEOUT", 0.02)
    dialog = _ensoniq_dialog(qapp)
    short = _Reply([0x7E, 0x00, 0x06, 0x02, 0x0F, 0x03, 0x00, 0x01, 0x00, 0x01, 0x00])  # cut short
    inp, out = _paired([[short], [_Reply(e.build_response(e.STATUS_ACK))]])
    ok, _result = dialog._send_identity_request(inp, out)
    assert ok  # the real question was still asked and answered


def test_the_ensoniq_hardware_test_ignores_its_own_request_echoed_back(qapp, monkeypatch):
    # a DAW on the same interface can send our request straight back: that is not the unit answering
    from core import ensoniq_sysex as e
    from ui import settings_dialog

    monkeypatch.setattr(settings_dialog, "_LOOPBACK_RECEIVE_TIMEOUT", 0.05)
    monkeypatch.setattr(settings_dialog, "_ENSONIQ_IDENTITY_TIMEOUT", 0.02)
    dialog = _ensoniq_dialog(qapp)
    inp, out = _paired([[], [_Reply(e.build_get_instrument(0))]])
    ok, text = dialog._send_identity_request(inp, out)
    assert not ok and "No reply from an Ensoniq" in text
    assert "SysEx is enabled" in text


def test_the_ensoniq_hardware_test_needs_a_device_id_of_0_to_15(qapp):
    dialog = _ensoniq_dialog(qapp, device_id=20)
    inp, out = _paired([])
    ok, text = dialog._send_identity_request(inp, out)
    assert not ok and "0-15" in text and out.sent == []


def test_the_ensoniq_hardware_test_describes_the_answers(qapp):
    from core import ensoniq_sysex as e

    dialog = _ensoniq_dialog(qapp)
    dialog._ensoniq_identity_text = None
    fmt = lambda status: dialog._format_ensoniq_result(e.parse_message(e.build_response(status)))
    assert "Instrument 1 is loaded" in fmt(e.STATUS_ACK)
    assert "isn't loaded (a normal answer)" in fmt(e.STATUS_INVALID_INSTRUMENT)
    assert "memory full" in fmt(e.STATUS_MEMORY_FULL)
    put = e.parse_message(e.build_message(e.CMD_PUT_INSTRUMENT, e.encode_context(0, 0, 1)))
    assert "instrument data" in dialog._format_ensoniq_result(put)
    assert all("Ensoniq EPS/ASR SysEx answered" in fmt(s) for s in (0, 5, 12))


def test_only_a_response_or_a_put_counts_as_an_ensoniq_answer(qapp):
    from core import ensoniq_sysex as e

    ok = MidiSettingsDialog._is_ensoniq_answer
    assert ok(e.build_response(e.STATUS_ACK))
    assert ok(e.build_message(e.CMD_PUT_INSTRUMENT, e.encode_context(0, 0, 1)))
    assert not ok(e.build_get_instrument(0))  # our own request
    assert not ok([0x47, 0x00, 0x05, 0x00])
    assert not ok([0x0F, 0x03, 0x00])


# --- the ASR-X hardware test ---------------------------------------------------------------------


def _asrx_dialog(qapp, device_id=0):
    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    dialog.combo_device_type.setCurrentIndex(dialog.combo_device_type.findData("ensoniq_asrx"))
    dialog.spin_channel.setValue(device_id)
    return dialog


def test_the_asrx_hardware_test_asks_for_the_name_of_sample_0(qapp):
    from core import asrx_sysex as a

    dialog = _asrx_dialog(qapp, device_id=5)
    name_reply = _Reply(bytes((0x7E, 5, 0x05, 0x03, 0, 0, 0x00, 0x10)) + a.encode_name("KICK"))
    inp, out = _paired([[], [name_reply]])  # no identity answer, then the name
    ok, message = dialog._send_identity_request(inp, out)
    assert ok and message.kind == "name" and message.name == "KICK"
    assert out.sent[-1].data == tuple(a.build_name_request(5, 0))
    assert "Sample 0 is 'KICK'" in dialog._format_asrx_result(message)


def test_the_asrx_hardware_test_takes_any_device_id_and_an_empty_slot_is_an_answer(qapp):
    from core import asrx_sysex as a

    dialog = _asrx_dialog(qapp, device_id=100)  # the EPS/ASR limit of 0-15 does not apply
    empty = _Reply(a.HEADER + bytes((100, 0x7F, 0x41)))
    inp, out = _paired([[], [empty]])
    ok, message = dialog._send_identity_request(inp, out)
    assert ok and message.kind == "error"
    assert "empty (a normal answer)" in dialog._format_asrx_result(message)


def test_the_asrx_hardware_test_ignores_its_own_request_coming_back(qapp):
    from core import asrx_sysex as a

    ok = MidiSettingsDialog._is_asrx_answer
    assert ok(bytes((0x7E, 0, 0x05, 0x03, 0, 0, 0, 0x10)))
    assert ok(a.HEADER + bytes((0, 0x7F, 0x41)))
    assert not ok(a.build_name_request(0, 0))  # our own request
    assert not ok(bytes((0x47, 0, 5, 0)))


def test_the_asrx_hardware_test_says_what_to_check_when_there_is_no_reply(qapp, monkeypatch):
    from ui import settings_dialog

    monkeypatch.setattr(settings_dialog, "_LOOPBACK_RECEIVE_TIMEOUT", 0.05)
    dialog = _asrx_dialog(qapp)
    inp, out = _paired([[], []])
    ok, text = dialog._send_identity_request(inp, out)
    assert not ok and "No reply from an ASR-X" in text and "SysEx Recv" in text


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


def test_troubleshooting_tests_each_show_their_cabling_diagram(qapp):
    from PySide6.QtWidgets import QLabel

    dialog = MidiSettingsDialog(_FakeMidiManager(["In A"], ["Out A"]), _FakeSamplerController())
    pictures = [l for l in dialog.findChildren(QLabel) if l.pixmap() is not None and not l.pixmap().isNull()]
    assert len(pictures) == 2
    # drawn at 2x (~900 px wide), so only fits if the pixel ratio is applied - well inside the dialog's own minimum width
    assert all(p.sizeHint().width() <= _MINIMUM_DIALOG_WIDTH - 80 for p in pictures)


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
