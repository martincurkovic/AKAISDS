# widget-level tests for TransferDashboard (ui/dashboard.py) - needs a real
# offscreen QApplication, unlike test_dashboard_helpers.py's pure
# staticmethod tests. Uses real MidiManager/SamplerController instances
# (both construct safely with no actual MIDI hardware touched - see their
# own __init__s) rather than app_config-backed ApplicationWindow, so these
# never read/write the user's real ~/.akaisds/config.json.

import os
import struct
import time
import wave
from pathlib import Path

# must be set BEFORE the first QApplication() call below - see
# test_slice_editor_window.py's own comment on this exact guard for why a
# file lacking it only runs offscreen by accident (whichever OTHER test
# module happens to get collected first)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QLineEdit

from core import dropped_files, sample_slicing, sds_encoder
from core.midi_manager import MidiManager
from controller.sampler_controller import SamplerController
from ui.dashboard import SETTINGS_ROLE, TransferDashboard


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
    dash = TransferDashboard(sampler_controller, midi_manager)
    yield dash
    # QListWidget.clear() (any test that repopulates the hardware list)
    # schedules its row widgets for deferred deletion. Left pending, that
    # delete fires later - inside some OTHER test's nested event loop
    # (test_program_editor_window's _wait_for_any_signal) - after this
    # dashboard has been garbage-collected, and segfaults in
    # QWidget::destroy (confirmed from a macOS crash report). Flush them now,
    # while everything is still alive.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


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
    assert "Settings" in dashboard.btn_open_editor.toolTip()

    dashboard.midi_manager.input_name = "Fake In"
    dashboard.midi_manager.output_name = "Fake Out"
    dashboard.sampler_controller.device_type = "generic"
    dashboard._update_open_editor_enabled()
    assert "Akai S2000/S3000" in dashboard.btn_open_editor.toolTip()

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


# --- Settings / Open Editor buttons: stacked, same width -------------


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


def _write_stereo_wav(path, n_frames=100, left_value=1000, right_value=-1000):
    # distinct, easily-asserted-on L/R values (rather than silence) so a
    # test can actually catch a channel-order bug, not just "some stereo
    # data survived"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        frame = struct.pack("<hh", left_value, right_value)
        wf.writeframes(frame * n_frames)


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


# --- Editing a queued file's name scrolls the field back to the start -----
# Per direct user report: renaming a queued file to something long left the
# field showing its END, not the start, even though open_edit_dialog always
# called setCursorPosition(0) right after setText(). QLineEdit only
# recomputes its horizontal scroll offset lazily, inside its own
# paintEvent, based on whatever the cursor position is AT THAT MOMENT - a
# synchronous setCursorPosition() call in the same stack as setText() isn't
# guaranteed to still be in effect by the time the next paint actually
# happens. Deferred via QTimer.singleShot(0, ...) instead - see
# open_edit_dialog's own comment.


def test_renaming_to_a_long_name_scrolls_the_field_back_to_the_start(
    dashboard, tmp_path, qapp, monkeypatch
):
    import ui.dashboard as dashboard_module

    source = tmp_path / "short.wav"
    _write_wav(source)
    dashboard.on_files_dropped([str(source)])
    item = dashboard.list_local.item(0)
    row_widget = dashboard.list_local.itemWidget(item)
    edit_field = row_widget.findChild(QLineEdit)

    long_name = "A_VERY_LONG_SAMPLE_FILENAME_THAT_OVERFLOWS_THE_FIELD"

    class _FakeSettingsDialog:
        slice_requested = False

        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return True

        def get_settings(self):
            return {
                "name": long_name,
                "bit_depth": 16,
                "sample_rate": None,
                "mono": False,
            }

    monkeypatch.setattr(dashboard_module, "SampleSettingsDialog", _FakeSettingsDialog)

    dashboard.open_edit_dialog(item, edit_field)
    assert edit_field.text() == long_name

    # lets the deferred setCursorPosition(0) fire - a zero-delay QTimer
    # armed during a processEvents() call isn't always dispatched within
    # that same call (measured: one pass is enough on macOS, but a second
    # pass is needed on Linux's xcb/wayland dispatcher), so poll instead
    # of assuming a single pump is enough
    deadline = time.monotonic() + 2.0
    while edit_field.cursorPosition() != 0:
        qapp.processEvents()
        if time.monotonic() > deadline:
            break

    assert edit_field.cursorPosition() == 0
    # the real regression: not just the logical cursor index, but whether
    # the field actually scrolled to show it - a cursor rect with a
    # negative/way-off x means the display is still showing the tail end
    assert edit_field.cursorRect().x() >= -5


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


# --- Slice Editor reachable from the queue's own Edit dialog --------------
# _export_slices_to_queue is SliceEditorWindow's export_callback for the
# Transfer Dashboard (see ui/dashboard.py's own _open_slice_editor) - tested
# directly with fabricated names/slices rather than driving the real modal
# dialog, same reasoning SliceEditorWindow.exec() itself is never called in
# a test (see tests/test_slice_editor_window.py's own module comment).


def _noop(*args, **kwargs):
    pass


def test_export_slices_to_queue_replaces_the_row_with_new_ones(dashboard, tmp_path):
    source = tmp_path / "beat.wav"
    _write_wav(source, n_frames=4410)
    dashboard.on_files_dropped([str(source)])
    item = dashboard.list_local.item(0)
    original_path = item.data(Qt.ItemDataRole.UserRole)

    names = ["BEAT-01", "BEAT-02"]
    slices = [[0] * 2205, [0] * 2205]
    success, message = dashboard._export_slices_to_queue(
        item, names, slices, 44100, 16, None, _noop, _noop
    )

    assert success is True
    assert "BEAT-01" in message and "BEAT-02" in message
    assert dashboard.list_local.count() == 2
    row_names = [
        dashboard.list_local.itemWidget(dashboard.list_local.item(i))
        .findChild(QLineEdit)
        .text()
        for i in range(2)
    ]
    assert row_names == names
    # the original row's own stable copy is gone, replaced by two new ones
    assert not os.path.exists(original_path)
    for i in range(2):
        new_path = dashboard.list_local.item(i).data(Qt.ItemDataRole.UserRole)
        assert os.path.exists(new_path)


def test_export_slices_to_queue_carries_bit_depth_and_rate_choice_onto_new_rows(
    dashboard, tmp_path
):
    source = tmp_path / "beat.wav"
    _write_wav(source, n_frames=4410)
    dashboard.on_files_dropped([str(source)])
    item = dashboard.list_local.item(0)

    dashboard._export_slices_to_queue(
        item, ["SLICE-01"], [[0] * 4410], 44100, 8, 22050, _noop, _noop
    )

    new_item = dashboard.list_local.item(0)
    settings = new_item.data(SETTINGS_ROLE)
    assert settings["bit_depth"] == 8
    assert settings["sample_rate"] == 22050


def test_export_slices_to_queue_rolls_back_on_a_mid_batch_failure(
    dashboard, tmp_path, monkeypatch
):
    source = tmp_path / "beat.wav"
    _write_wav(source, n_frames=4410)
    dashboard.on_files_dropped([str(source)])
    item = dashboard.list_local.item(0)
    original_path = item.data(Qt.ItemDataRole.UserRole)

    real_create_local_row = dashboard.create_local_row
    calls = []

    def _flaky_create_local_row(filepath):
        calls.append(filepath)
        if len(calls) == 2:
            return False
        return real_create_local_row(filepath)

    monkeypatch.setattr(dashboard, "create_local_row", _flaky_create_local_row)

    success, message = dashboard._export_slices_to_queue(
        item,
        ["SLICE-01", "SLICE-02"],
        [[0] * 2205, [0] * 2205],
        44100,
        16,
        None,
        _noop,
        _noop,
    )

    assert success is False
    # the first slice that DID land got rolled back, and the original row
    # this was supposed to replace is still untouched - a failed slice
    # must not leave the queue in a half-sliced state
    assert dashboard.list_local.count() == 1
    assert dashboard.list_local.item(0).data(Qt.ItemDataRole.UserRole) == original_path
    assert os.path.exists(original_path)


# --- Stereo source files: right channel preserved through slicing ---------


def test_export_slices_to_queue_preserves_both_channels_for_a_stereo_source(
    dashboard, tmp_path
):
    source = tmp_path / "stereo_beat.wav"
    _write_stereo_wav(source, n_frames=4410, left_value=1000, right_value=-1000)
    dashboard.on_files_dropped([str(source)])
    item = dashboard.list_local.item(0)

    left_slices = [[1000] * 2205, [1000] * 2205]
    right_slices = [[-1000] * 2205, [-1000] * 2205]
    success, message = dashboard._export_slices_to_queue(
        item,
        ["BEAT-01", "BEAT-02"],
        left_slices,
        44100,
        16,
        None,
        _noop,
        _noop,
        right_slices=right_slices,
    )

    assert success is True
    assert dashboard.list_local.count() == 2
    for i in range(2):
        new_item = dashboard.list_local.item(i)
        new_path = new_item.data(Qt.ItemDataRole.UserRole)
        channels, rate = sds_encoder.read_wav_channels(new_path)
        assert rate == 44100
        assert len(channels) == 2
        assert channels[0] == left_slices[i]
        assert channels[1] == right_slices[i]
        # a stereo slice must never fall back to the dashboard's own
        # global mono default - that would silently discard the right
        # channel this test just confirmed survived the write itself
        assert new_item.data(SETTINGS_ROLE)["mono"] is False


def test_export_slices_to_queue_forces_mono_false_even_when_global_default_is_mono(
    dashboard, tmp_path
):
    dashboard._global_mono = True
    source = tmp_path / "stereo_beat.wav"
    _write_stereo_wav(source, n_frames=100)
    dashboard.on_files_dropped([str(source)])
    item = dashboard.list_local.item(0)

    dashboard._export_slices_to_queue(
        item,
        ["SLICE-01"],
        [[1000] * 100],
        44100,
        16,
        None,
        _noop,
        _noop,
        right_slices=[[-1000] * 100],
    )

    new_item = dashboard.list_local.item(0)
    assert new_item.data(SETTINGS_ROLE)["mono"] is False


def test_export_slices_to_queue_without_right_slices_stays_mono(dashboard, tmp_path):
    # the plain mono path (right_slices=None, exercised by every other
    # _export_slices_to_queue test above) must still write ordinary mono
    # WAVs, not accidentally pick up stereo handling
    source = tmp_path / "beat.wav"
    _write_wav(source, n_frames=100)
    dashboard.on_files_dropped([str(source)])
    item = dashboard.list_local.item(0)

    dashboard._export_slices_to_queue(
        item, ["SLICE-01"], [[0] * 100], 44100, 16, None, _noop, _noop
    )

    new_item = dashboard.list_local.item(0)
    new_path = new_item.data(Qt.ItemDataRole.UserRole)
    channels, _rate = sds_encoder.read_wav_channels(new_path)
    assert len(channels) == 1


def test_open_slice_editor_wires_both_channels_through_to_export(
    dashboard, tmp_path, monkeypatch
):
    # end-to-end check of _open_slice_editor's own closure (the `dialog`
    # forward-reference in particular - it's assigned AFTER _export is
    # defined, and only resolved once _export actually runs) - fakes
    # SliceEditorWindow.exec() rather than calling it for real, same
    # reasoning tests/test_slice_editor_window.py's own module comment
    # gives for never driving the real modal dialog in a test
    from ui.slice_editor_window import SliceEditorWindow

    source = tmp_path / "stereo_beat.wav"
    _write_stereo_wav(source, n_frames=4410, left_value=1000, right_value=-1000)
    dashboard.on_files_dropped([str(source)])
    item = dashboard.list_local.item(0)
    row_widget = dashboard.list_local.itemWidget(item)
    edit_field = row_widget.findChild(QLineEdit)

    captured = {}

    def _fake_exec(self):
        # one marker splitting the sample into two halves, then invoke
        # the export callback exactly like a real "Export Slices" click
        self.waveform.set_markers([2205])
        names = ["S-01", "S-02"]
        slices = sample_slicing.slice_samples(
            self._samples, self.waveform.start(), self.waveform.end(), [2205]
        )
        captured["result"] = self._export_callback(
            names,
            slices,
            self._framerate,
            16,
            None,
            self._spitch,
            self._stuno,
            self._shlto,
            _noop,
            _noop,
        )
        return 0

    monkeypatch.setattr(SliceEditorWindow, "exec", _fake_exec)

    dashboard._open_slice_editor(item, edit_field)

    success, message = captured["result"]
    assert success is True
    assert dashboard.list_local.count() == 2
    for i in range(2):
        new_path = dashboard.list_local.item(i).data(Qt.ItemDataRole.UserRole)
        channels, _rate = sds_encoder.read_wav_channels(new_path)
        assert channels[0] == [1000] * 2205
        assert channels[1] == [-1000] * 2205


# --- Program Editor + the Akai S1000 ----------------------------------------------


def _stub_editor_opening(monkeypatch, dashboard):
    # records connect() calls and swaps ProgramEditorWindow for a stub, so
    # open_program_editor() can be driven without a real bridge/window
    from ui import dashboard as dashboard_module

    connects = []
    monkeypatch.setattr(
        dashboard_module.program_editor_bridge,
        "connect",
        lambda midi_manager, sampler_model=None: connects.append(sampler_model)
        or object(),
    )

    class _StubEditor:
        def __init__(self, main_window, bridge):
            pass

        def show(self):
            pass

    monkeypatch.setattr(dashboard_module, "ProgramEditorWindow", _StubEditor)
    return connects


def _isolate_s1000_warning(monkeypatch, acknowledged):
    from ui import dashboard as dashboard_module

    state = {"acknowledged": acknowledged}
    monkeypatch.setattr(
        dashboard_module.app_config,
        "get_s1000_editor_warning_acknowledged",
        lambda: state["acknowledged"],
    )
    monkeypatch.setattr(
        dashboard_module.app_config,
        "save_s1000_editor_warning_acknowledged",
        lambda acknowledged=True: state.update(acknowledged=acknowledged),
    )
    return state


def _stub_warning(monkeypatch, answer):
    from PySide6.QtWidgets import QMessageBox
    from ui import dashboard as dashboard_module

    shown = []

    def fake_warning(parent, title, text, *args):
        shown.append(title)
        return answer

    monkeypatch.setattr(dashboard_module.QMessageBox, "warning", fake_warning)
    return shown


def test_s1000_editor_asks_before_first_open_and_remembers_ok(
    dashboard, monkeypatch
):
    from PySide6.QtWidgets import QMessageBox

    connects = _stub_editor_opening(monkeypatch, dashboard)
    state = _isolate_s1000_warning(monkeypatch, acknowledged=False)
    shown = _stub_warning(monkeypatch, QMessageBox.StandardButton.Ok)
    dashboard.sampler_controller.set_device_type("akai_s1000")

    dashboard.open_program_editor()

    assert shown == ["Akai S1000 - experimental"]
    assert state["acknowledged"] is True
    assert connects == ["akai_s1000"]


def test_s1000_editor_cancelling_the_warning_does_not_connect(
    dashboard, monkeypatch
):
    from PySide6.QtWidgets import QMessageBox

    connects = _stub_editor_opening(monkeypatch, dashboard)
    state = _isolate_s1000_warning(monkeypatch, acknowledged=False)
    _stub_warning(monkeypatch, QMessageBox.StandardButton.Cancel)
    dashboard.sampler_controller.set_device_type("akai_s1000")

    dashboard.open_program_editor()

    assert connects == []
    assert state["acknowledged"] is False


def test_s1000_editor_does_not_ask_again_once_acknowledged(dashboard, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    connects = _stub_editor_opening(monkeypatch, dashboard)
    _isolate_s1000_warning(monkeypatch, acknowledged=True)
    shown = _stub_warning(monkeypatch, QMessageBox.StandardButton.Cancel)
    dashboard.sampler_controller.set_device_type("akai_s1000")

    dashboard.open_program_editor()

    assert shown == []
    assert connects == ["akai_s1000"]


def test_s2000_s3000_editor_never_shows_the_s1000_warning(dashboard, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    connects = _stub_editor_opening(monkeypatch, dashboard)
    _isolate_s1000_warning(monkeypatch, acknowledged=False)
    shown = _stub_warning(monkeypatch, QMessageBox.StandardButton.Cancel)

    dashboard.open_program_editor()

    assert shown == []
    assert connects == ["akai_s2000_s3000"]


def test_open_editor_is_enabled_for_the_s1000_too(dashboard):
    dashboard.midi_manager.input_name = "Fake In"
    dashboard.midi_manager.output_name = "Fake Out"
    dashboard.sampler_controller.set_device_type("akai_s1000")
    dashboard._update_open_editor_enabled()
    assert dashboard.btn_open_editor.isEnabled() is True


# --- Akai S900/S950 ------------------------------------------------------------


@pytest.fixture
def s950_dashboard(dashboard):
    dashboard.midi_manager.input_name = "Fake In"
    dashboard.midi_manager.output_name = "Fake Out"
    dashboard.sampler_controller.set_device_type("akai_s900_s950")
    dashboard._update_device_type_ui()
    dashboard._update_open_editor_enabled()
    return dashboard


def test_s950_with_an_input_enables_browsing_but_has_no_memory_bar(s950_dashboard):
    d = s950_dashboard
    assert d.list_hardware.isEnabled()
    assert d.btn_refresh.isEnabled()
    assert d.btn_delete_selected.isEnabled() is False
    assert d.memory_avail_prog_bar.isHidden()  # no RSTAT equivalent


def test_s950_without_an_input_cant_refresh_receive_or_send(dashboard):
    dashboard.midi_manager.input_name = None
    dashboard.sampler_controller.set_device_type("akai_s900_s950")
    dashboard.list_local.addItem("kick.wav")
    dashboard._update_device_type_ui()
    assert dashboard.btn_refresh.isEnabled() is False
    assert dashboard.btn_receive.isEnabled() is False
    assert dashboard.btn_send.isEnabled() is False
    assert "MIDI input" in dashboard.btn_send.toolTip()
    assert "MIDI input" in dashboard.empty_hardware_label.text()


def test_s950_send_is_enabled_with_files_and_an_input(s950_dashboard):
    d = s950_dashboard
    d.list_local.addItem("kick.wav")
    d._update_queue_buttons_state()
    assert d.btn_send.isEnabled() is True
    assert d.btn_send.toolTip() == ""


def test_s950_enables_the_editor_with_an_experimental_tooltip(s950_dashboard):
    assert s950_dashboard.btn_open_editor.isEnabled() is True
    assert "experimental" in s950_dashboard.btn_open_editor.toolTip()


def test_s950_editor_needs_both_ports(s950_dashboard):
    s950_dashboard.midi_manager.input_name = None
    s950_dashboard._update_open_editor_enabled()
    assert s950_dashboard.btn_open_editor.isEnabled() is False
    assert "Settings" in s950_dashboard.btn_open_editor.toolTip()


def test_s950_opens_its_editor_on_the_shared_controller_not_a_bridge(
    s950_dashboard, monkeypatch
):
    from ui import dashboard as dashboard_module

    opened = []

    class _StubEditor2:
        def __init__(self, main_window, controller):
            opened.append((main_window, controller))

        def show(self):
            pass

    monkeypatch.setattr(dashboard_module, "S950ProgramEditorWindow", _StubEditor2)
    monkeypatch.setattr(
        dashboard_module.program_editor_bridge,
        "connect",
        lambda *a, **k: pytest.fail("the S900/S950 has no S3kBridge"),
    )
    s950_dashboard.open_program_editor()
    assert len(opened) == 1
    assert opened[0][1] is s950_dashboard.sampler_controller


def test_s950_slot_list_uses_the_real_slot_numbers_and_cant_delete(s950_dashboard):
    from PySide6.QtWidgets import QPushButton

    d = s950_dashboard
    # sparse: slots 3 and 40, not 0 and 1
    d.sampler_controller.sample_slots_updated.emit([(3, "KICK"), (40, "SNARE")])
    assert d.list_hardware.count() == 2
    numbers = [
        d.list_hardware.item(i).data(Qt.ItemDataRole.UserRole) for i in range(2)
    ]
    assert numbers == [3, 40]
    for i in range(2):
        buttons = {
            b.text(): b
            for b in d.list_hardware.itemWidget(d.list_hardware.item(i)).findChildren(
                QPushButton
            )
        }
        assert buttons["Delete"].isEnabled() is False
        assert "front panel" in buttons["Delete"].toolTip()
        assert buttons["Edit"].isEnabled() is True


def test_s950_delete_selected_stays_disabled_with_a_row_checked(s950_dashboard):
    from PySide6.QtWidgets import QCheckBox

    d = s950_dashboard
    d.sampler_controller.sample_slots_updated.emit([(3, "KICK")])
    d.list_hardware.itemWidget(d.list_hardware.item(0)).findChild(QCheckBox).setChecked(True)
    assert d.btn_delete_selected.isEnabled() is False


def test_akai_list_is_dropped_when_switching_to_s950_but_not_when_settings_just_closes(
    dashboard,
):
    dashboard.midi_manager.input_name = "Fake In"
    dashboard.sampler_controller.sample_list_updated.emit(["A", "B"])
    assert dashboard.list_hardware.count() == 2
    dashboard._update_device_type_ui()  # Settings closed, nothing changed
    assert dashboard.list_hardware.count() == 2
    dashboard.sampler_controller.set_device_type("akai_s900_s950")
    dashboard._update_device_type_ui()
    assert dashboard.list_hardware.count() == 0


def test_leaving_s950_keeps_send_available_without_a_queue_change(s950_dashboard):
    d = s950_dashboard
    d.midi_manager.input_name = None
    d.list_local.addItem("kick.wav")
    d._update_device_type_ui()
    assert d.btn_send.isEnabled() is False
    d.sampler_controller.set_device_type("akai_s2000_s3000")
    d._update_device_type_ui()
    # an Akai sampler can be sent to open loop, so Send comes back
    assert d.btn_send.isEnabled() is True
    assert d.btn_send.toolTip() == ""


def test_s950_editor_refuses_without_a_midi_input_and_never_touches_a_bridge(
    s950_dashboard, monkeypatch
):
    from core import program_editor_bridge
    from ui import dashboard as dashboard_module

    def _must_not_connect(*_a, **_k):
        raise AssertionError("connected a bridge for an S900/S950")

    monkeypatch.setattr(program_editor_bridge, "connect", _must_not_connect)
    monkeypatch.setattr(
        dashboard_module,
        "S950ProgramEditorWindow",
        lambda *a, **k: pytest.fail("opened an editor with no MIDI input"),
    )
    s950_dashboard.midi_manager.input_name = None  # the unit answers on the input
    s950_dashboard.open_program_editor()
    assert "MIDI input" in s950_dashboard.status_bar.currentMessage()


def test_s950_experimental_warning_is_shown_once_then_remembered(
    s950_dashboard, monkeypatch
):
    from PySide6.QtWidgets import QMessageBox

    from core import app_config

    state = {"acknowledged": False, "asked": 0}
    monkeypatch.setattr(
        app_config,
        "get_s950_transfer_warning_acknowledged",
        lambda: state["acknowledged"],
    )
    monkeypatch.setattr(
        app_config,
        "save_s950_transfer_warning_acknowledged",
        lambda value=True: state.update(acknowledged=value),
    )

    def _warning(*_a, **_k):
        state["asked"] += 1
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", _warning)
    assert s950_dashboard._confirm_s950_experimental() is True
    assert s950_dashboard._confirm_s950_experimental() is True
    assert state == {"acknowledged": True, "asked": 1}


def test_s950_experimental_warning_cancel_blocks_the_send(s950_dashboard, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from core import app_config

    saved = []
    monkeypatch.setattr(app_config, "get_s950_transfer_warning_acknowledged", lambda: False)
    monkeypatch.setattr(
        app_config, "save_s950_transfer_warning_acknowledged", lambda v=True: saved.append(v)
    )
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Cancel
    )
    assert s950_dashboard._confirm_s950_experimental() is False
    assert saved == []


def test_other_sampler_types_never_see_the_s950_warning(dashboard, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: pytest.fail("warned a non-S950 user")
    )
    assert dashboard._confirm_s950_experimental() is True


# --- Yamaha A4000/A5000: SDS transfers (the generic family) + its own editor -------------------------


@pytest.fixture
def yamaha_dashboard(dashboard):
    dashboard.midi_manager.input_name = "Fake In"
    dashboard.midi_manager.output_name = "Fake Out"
    dashboard.sampler_controller.set_device_type("yamaha_a4000")
    dashboard._update_device_type_ui()
    dashboard._update_open_editor_enabled()
    return dashboard


def test_yamaha_sends_are_generic_sds_but_the_editor_and_the_sample_list_are_enabled(yamaha_dashboard):
    d = yamaha_dashboard
    assert d.sampler_controller.device_type == "generic"  # SENDING samples: plain SDS
    assert d.btn_open_editor.isEnabled() is True
    assert "Yamaha" in d.btn_open_editor.toolTip() and "experimental" in d.btn_open_editor.toolTip()
    # ...but listing and receiving use the unit's own protocol, so the hardware list is live (unlike Generic SDS)
    assert d.list_hardware.isEnabled() and d.btn_refresh.isEnabled()
    assert d.btn_delete_selected.isEnabled() is False and d.memory_avail_prog_bar.isHidden()


def test_yamaha_without_a_midi_input_cant_refresh_or_receive(dashboard):
    dashboard.midi_manager.input_name = None
    dashboard.sampler_controller.set_device_type("yamaha_a4000")
    dashboard._update_device_type_ui()
    assert dashboard.btn_refresh.isEnabled() is False and dashboard.btn_receive.isEnabled() is False
    assert "MIDI input" in dashboard.empty_hardware_label.text()


def test_generic_sds_still_cant_browse(dashboard):
    dashboard.midi_manager.input_name = "Fake In"
    dashboard.sampler_controller.set_device_type("generic")
    dashboard._update_device_type_ui()
    assert dashboard.btn_refresh.isEnabled() is False and dashboard.list_hardware.isEnabled() is False
    assert "Generic SDS" in dashboard.empty_hardware_label.text()


def test_yamaha_editor_needs_both_ports(yamaha_dashboard):
    yamaha_dashboard.midi_manager.input_name = None
    yamaha_dashboard._update_open_editor_enabled()
    assert yamaha_dashboard.btn_open_editor.isEnabled() is False
    assert "Settings" in yamaha_dashboard.btn_open_editor.toolTip()


def test_generic_sds_still_has_no_editor(dashboard):
    dashboard.midi_manager.input_name = "Fake In"
    dashboard.midi_manager.output_name = "Fake Out"
    dashboard.sampler_controller.set_device_type("generic")
    dashboard._update_open_editor_enabled()
    assert dashboard.btn_open_editor.isEnabled() is False


def test_yamaha_opens_its_editor_on_the_shared_controller_not_a_bridge(yamaha_dashboard, monkeypatch):
    from ui import dashboard as dashboard_module

    opened = []

    class _StubEditor:
        def __init__(self, main_window, controller):
            opened.append((main_window, controller))

        def show(self):
            pass

    monkeypatch.setattr(dashboard_module, "YamahaProgramEditorWindow", _StubEditor)
    monkeypatch.setattr(
        dashboard_module.program_editor_bridge,
        "connect",
        lambda *a, **k: pytest.fail("the Yamaha editor has no S3kBridge"),
    )
    yamaha_dashboard.open_program_editor()
    assert len(opened) == 1 and opened[0][1] is yamaha_dashboard.sampler_controller


def test_yamaha_editor_refuses_without_a_midi_input(yamaha_dashboard, monkeypatch):
    from ui import dashboard as dashboard_module

    monkeypatch.setattr(
        dashboard_module, "YamahaProgramEditorWindow", lambda *a, **k: pytest.fail("opened with no MIDI input")
    )
    yamaha_dashboard.midi_manager.input_name = None  # every answer comes back on the input
    yamaha_dashboard.open_program_editor()
    assert "MIDI input" in yamaha_dashboard.status_bar.currentMessage()
