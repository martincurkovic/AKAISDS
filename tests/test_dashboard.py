# widget-level tests for TransferDashboard (ui/dashboard.py) - needs a real
# offscreen QApplication, unlike test_dashboard_helpers.py's pure
# staticmethod tests. Uses real MidiManager/SamplerController instances
# (both construct safely with no actual MIDI hardware touched - see their
# own __init__s) rather than app_config-backed ApplicationWindow, so these
# never read/write the user's real ~/.akaisds/config.json.

import pytest
from PySide6.QtWidgets import QApplication

from core.midi_manager import MidiManager
from controller.sampler_controller import SamplerController
from ui.dashboard import TransferDashboard


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def dashboard(qapp):
    midi_manager = MidiManager()
    sampler_controller = SamplerController(midi_manager)
    return TransferDashboard(sampler_controller, midi_manager)


# --- Open Editor button: only enabled with both ports set AND Akai --------
# Per direct user request: program_editor_bridge.connect() needs a real,
# fully-configured connection - both a MIDI input and output port actually
# selected, and the sampler type set to Akai (Generic SDS has no support
# for the Akai-specific SysEx extensions the editor is built on).


def test_open_editor_disabled_with_no_ports_selected(dashboard):
    # MidiManager defaults both to None (see its own __init__) -
    # SamplerController defaults to "akai", so this isolates the "no
    # ports" half of the guard
    assert dashboard.midi_manager.input_name is None
    assert dashboard.midi_manager.output_name is None
    assert dashboard.sampler_controller.device_type == "akai"
    assert dashboard.btn_open_editor.isEnabled() is False


def test_open_editor_disabled_with_only_one_port_selected(dashboard):
    dashboard.midi_manager.input_name = "Fake In"
    dashboard.midi_manager.output_name = None
    dashboard._update_open_editor_enabled()
    assert dashboard.btn_open_editor.isEnabled() is False

    dashboard.midi_manager.input_name = None
    dashboard.midi_manager.output_name = "Fake Out"
    dashboard._update_open_editor_enabled()
    assert dashboard.btn_open_editor.isEnabled() is False


def test_open_editor_disabled_when_device_type_is_generic(dashboard):
    dashboard.midi_manager.input_name = "Fake In"
    dashboard.midi_manager.output_name = "Fake Out"
    dashboard.sampler_controller.device_type = "generic"
    dashboard._update_open_editor_enabled()
    assert dashboard.btn_open_editor.isEnabled() is False


def test_open_editor_enabled_with_both_ports_and_akai_device_type(dashboard):
    dashboard.midi_manager.input_name = "Fake In"
    dashboard.midi_manager.output_name = "Fake Out"
    dashboard.sampler_controller.device_type = "akai"
    dashboard._update_open_editor_enabled()
    assert dashboard.btn_open_editor.isEnabled() is True


def test_open_editor_becomes_disabled_again_if_a_port_is_cleared(dashboard):
    dashboard.midi_manager.input_name = "Fake In"
    dashboard.midi_manager.output_name = "Fake Out"
    dashboard._update_open_editor_enabled()
    assert dashboard.btn_open_editor.isEnabled() is True

    dashboard.midi_manager.output_name = None
    dashboard._update_open_editor_enabled()
    assert dashboard.btn_open_editor.isEnabled() is False


def test_open_editor_tooltip_explains_whats_missing(dashboard):
    dashboard._update_open_editor_enabled()
    assert "MIDI Settings" in dashboard.btn_open_editor.toolTip()

    dashboard.midi_manager.input_name = "Fake In"
    dashboard.midi_manager.output_name = "Fake Out"
    dashboard.sampler_controller.device_type = "generic"
    dashboard._update_open_editor_enabled()
    assert "Akai Sampler" in dashboard.btn_open_editor.toolTip()

    dashboard.sampler_controller.device_type = "akai"
    dashboard._update_open_editor_enabled()
    assert dashboard.btn_open_editor.toolTip() == ""


def test_open_editor_enabled_state_reflects_whatever_was_already_restored(qapp):
    # __init__ itself must call _update_open_editor_enabled - matches
    # _update_device_type_ui's own "reflect whatever was already restored
    # before the dashboard was constructed" comment (main_window.py
    # restores saved ports/device type before building the dashboard)
    midi_manager = MidiManager()
    midi_manager.input_name = "Fake In"
    midi_manager.output_name = "Fake Out"
    sampler_controller = SamplerController(midi_manager)
    sampler_controller.device_type = "akai"

    dashboard = TransferDashboard(sampler_controller, midi_manager)

    assert dashboard.btn_open_editor.isEnabled() is True


# --- MIDI Settings / Open Editor buttons: stacked, same width -------------


def test_settings_and_open_editor_buttons_are_the_same_width(dashboard):
    assert dashboard.btn_settings.width() == dashboard.btn_open_editor.width()
