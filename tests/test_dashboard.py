# widget-level tests for TransferDashboard (ui/dashboard.py) - needs a real
# offscreen QApplication, unlike test_dashboard_helpers.py's pure
# staticmethod tests. Uses real MidiManager/SamplerController instances
# (both construct safely with no actual MIDI hardware touched - see their
# own __init__s) rather than app_config-backed ApplicationWindow, so these
# never read/write the user's real ~/.akaisds/config.json.

import os
import wave
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit

from core import dropped_files
from core.midi_manager import MidiManager
from controller.sampler_controller import SamplerController
from ui.dashboard import TransferDashboard


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolated_dropped_files_base_dir(tmp_path, monkeypatch):
    # TransferDashboard.__init__ calls dropped_files.sweep_orphaned_
    # sessions() unconditionally, and several tests below go on to drop
    # real files - this keeps every test in this module off the user's
    # actual ~/.akaisds/dropped_files, same isolation test_dropped_files.py
    # already uses for that module directly
    monkeypatch.setattr(dropped_files, "_BASE_DIR", tmp_path / "dropped_files")
    monkeypatch.setattr(dropped_files, "_session_dir", None)


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


# --- Dropped files get a stable local copy, not just a stored path --------
# Per direct user report: dragging a "cropped" sample out of a sample
# browser (e.g. Sononym on macOS) hands this app a path to a file the
# SOURCE app owns and cleans up shortly after the drop - by the time Send
# actually gets around to reading it (item.data(Qt.ItemDataRole.UserRole),
# read lazily whenever this file's turn in the queue comes up), it's gone.
# create_local_row now copies the file into a stable, app-owned location
# (core/dropped_files.py) immediately, before the row is even built.


def _write_wav(path, n_frames=100):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00\x00" * n_frames)


def test_dropping_a_file_queues_a_stable_copy_not_the_original_path(
    dashboard, tmp_path
):
    source = tmp_path / "cropped.wav"
    _write_wav(source)

    dashboard.on_files_dropped([str(source)])

    assert dashboard.list_local.count() == 1
    item = dashboard.list_local.item(0)
    queued_path = item.data(Qt.ItemDataRole.UserRole)
    assert queued_path != str(source)
    assert dropped_files._BASE_DIR.resolve() in Path(queued_path).resolve().parents


def test_dropped_files_copy_survives_the_original_being_deleted(dashboard, tmp_path):
    # the actual bug: the original disappearing between drop and Send
    # must not affect the queued copy at all
    source = tmp_path / "cropped.wav"
    _write_wav(source)

    dashboard.on_files_dropped([str(source)])
    queued_path = dashboard.list_local.item(0).data(Qt.ItemDataRole.UserRole)

    os.remove(source)  # simulate the source app's own cleanup

    assert os.path.exists(queued_path)
    with wave.open(queued_path, "rb") as wf:
        assert wf.getnframes() == 100


def test_dropped_file_display_name_uses_the_original_basename(dashboard, tmp_path):
    # the copy's own filename can get a disambiguating suffix (see
    # dropped_files._unique_name) - the row's displayed name must not
    source = tmp_path / "my sample.wav"
    _write_wav(source)

    dashboard.on_files_dropped([str(source)])

    row_widget = dashboard.list_local.itemWidget(dashboard.list_local.item(0))
    edit_field = row_widget.findChild(QLineEdit)
    assert edit_field.text() == "my sample"


def test_dropping_a_file_that_vanishes_before_it_can_be_copied_adds_no_row(
    dashboard, tmp_path
):
    never_existed = tmp_path / "gone.wav"

    dashboard.on_files_dropped([str(never_existed)])

    assert dashboard.list_local.count() == 0
    assert "Couldn't add" in dashboard.status_bar.currentMessage()


def test_removing_a_row_deletes_its_copy(dashboard, tmp_path):
    source = tmp_path / "cropped.wav"
    _write_wav(source)
    dashboard.on_files_dropped([str(source)])
    item = dashboard.list_local.item(0)
    queued_path = item.data(Qt.ItemDataRole.UserRole)
    row_widget = dashboard.list_local.itemWidget(item)
    edit_field = row_widget.findChild(QLineEdit)

    dashboard._remove_local_row(item, edit_field)

    assert not os.path.exists(queued_path)


def test_clear_queue_deletes_every_copy(dashboard, tmp_path):
    source_a = tmp_path / "a.wav"
    source_b = tmp_path / "b.wav"
    _write_wav(source_a)
    _write_wav(source_b)
    dashboard.on_files_dropped([str(source_a), str(source_b)])
    queued_paths = [
        dashboard.list_local.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(dashboard.list_local.count())
    ]
    assert len(queued_paths) == 2

    dashboard.clear_local_queue()

    assert all(not os.path.exists(p) for p in queued_paths)


def test_on_file_transferred_deletes_the_copy(dashboard, tmp_path):
    source = tmp_path / "cropped.wav"
    _write_wav(source)
    dashboard.on_files_dropped([str(source)])
    queued_path = dashboard.list_local.item(0).data(Qt.ItemDataRole.UserRole)

    dashboard.on_file_transferred(queued_path)

    assert dashboard.list_local.count() == 0
    assert not os.path.exists(queued_path)
