from PySide6.QtWidgets import QMainWindow, QFileDialog
from PySide6.QtCore import QTimer
import os
import sys
import time
from PySide6.QtGui import QAction, QKeySequence
from ui.dashboard import TransferDashboard
from ui.about_dialog import AboutDialog
from ui.quickstart_dialog import show_quickstart_dialog
from ui.update_helper import UpdateCheckRunner
from core.midi_manager import MidiManager
from core import app_config, debug_log, dropped_files, sds_encoder
from controller.sampler_controller import SamplerController

# don't hit the GitHub API on every single launch
_UPDATE_CHECK_INTERVAL_SECONDS = 24 * 60 * 60


class ApplicationWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AKAISDS")
        self.setMinimumSize(1030, 600)

        # owns the real mido ports for the app's lifetime
        self.midi_manager = MidiManager()
        self.sampler_controller = SamplerController(self.midi_manager)

        # restore saved channel/device type BEFORE building dashboard so the UI will be correct
        self.sampler_controller.set_channel(app_config.get_saved_channel())
        self.sampler_controller.set_device_type(app_config.get_saved_device_type())

        # reconnect to whatever MIDI ports were used last time, if still exist
        self._restore_saved_ports()

        # instantiate custom UI layout components
        self.dashboard_view = TransferDashboard(
            self.sampler_controller, self.midi_manager
        )

        # mount layout into core central display panel area
        self.setCentralWidget(self.dashboard_view)
        # dashboard builds the QStatusBar (it owns the status messages
        # throughout its own methods) but mounts it here, natively, same as
        # the program editor's own status bar
        self.setStatusBar(self.dashboard_view.status_bar)

        # Settings menu action
        file_menu = self.menuBar().addMenu("File")

        open_files_action = QAction("Open Files...", self)
        open_files_action.setShortcut(QKeySequence.StandardKey.Open)
        open_files_action.triggered.connect(self._open_files_dialog)
        file_menu.addAction(open_files_action)

        open_folder_action = QAction("Open Folder...", self)
        open_folder_action.setShortcut("Ctrl+Shift+O")
        open_folder_action.triggered.connect(self._open_folder_dialog)
        file_menu.addAction(open_folder_action)

        file_menu.addSeparator()

        settings_action = QAction("Settings...", self)
        settings_action.setMenuRole(QAction.MenuRole.PreferencesRole)
        # QKeySequence.StandardKey.Preferences only has a default binding in
        # Qt's macOS keybinding table (Cmd+,) - Windows/Linux get no default
        # shortcut at all, so it silently does nothing there. Ctrl+, is the
        # closest equivalent on those platforms.
        settings_action.setShortcut(
            QKeySequence.StandardKey.Preferences
            if sys.platform == "darwin"
            else "Ctrl+,"
        )
        settings_action.triggered.connect(self.dashboard_view.open_settings_dialog)
        file_menu.addAction(settings_action)

        edit_menu = self.menuBar().addMenu("Edit")

        select_all_action = QAction("Select All", self)
        select_all_action.setShortcut("Ctrl+A")
        select_all_action.triggered.connect(
            self.dashboard_view.toggle_select_all_hardware_samples
        )
        edit_menu.addAction(select_all_action)

        delete_selected_action = QAction("Delete Selected Samples...", self)
        delete_selected_action.setShortcuts(["Ctrl+Backspace", "delete"])
        delete_selected_action.triggered.connect(
            self.dashboard_view.confirm_and_delete_selected_samples
        )
        edit_menu.addAction(delete_selected_action)

        edit_menu.addSeparator()

        clear_queue_action = QAction("Clear Transfer Queue", self)
        clear_queue_action.setShortcut("Ctrl+Shift+Backspace")
        clear_queue_action.triggered.connect(self.dashboard_view.clear_local_queue)
        edit_menu.addAction(clear_queue_action)

        transfer_menu = self.menuBar().addMenu("Transfer")

        refresh_action = QAction("Refresh Sample List", self)
        refresh_action.setShortcut("Ctrl+R")
        refresh_action.triggered.connect(self.dashboard_view.request_sample_list)
        transfer_menu.addAction(refresh_action)

        transfer_menu.addSeparator()

        send_action = QAction("Send Samples", self)
        send_action.setShortcut("Ctrl+Shift+S")
        send_action.triggered.connect(self.dashboard_view.send_queued_samples)
        transfer_menu.addAction(send_action)

        receive_action = QAction("Receive Samples", self)
        receive_action.setShortcut("Ctrl+Shift+R")
        receive_action.triggered.connect(self.dashboard_view.on_receive_clicked)
        transfer_menu.addAction(receive_action)

        cancel_action = QAction("Cancel Transfer", self)
        cancel_action.setShortcut("Ctrl+.")
        cancel_action.triggered.connect(self.dashboard_view.cancel_transfer)
        transfer_menu.addAction(cancel_action)

        transfer_menu.addSeparator()

        transfer_settings_action = QAction("Transfer Settings...", self)
        transfer_settings_action.setShortcut("Ctrl+Shift+,")
        transfer_settings_action.triggered.connect(
            self.dashboard_view.open_global_settings_dialog
        )
        transfer_menu.addAction(transfer_settings_action)

        # only one of {dashboard, program editor} is ever open at a time -
        # two simultaneous MIDI connections to the hardware is untested and
        # may not be safe - so each window's own entry for itself is always
        # disabled, and opening the other one hides this one (see
        # ProgramEditorWindow's own "&Window" menu, and its closeEvent,
        # for the return trip)
        window_menu = self.menuBar().addMenu("&Window")

        dashboard_action = QAction("Transfer Dashboard", self)
        dashboard_action.setShortcut("Ctrl+T")
        dashboard_action.setEnabled(False)  # this window IS the dashboard
        window_menu.addAction(dashboard_action)

        editor_action = QAction("Program Editor", self)
        editor_action.setShortcut("Ctrl+E")
        editor_action.triggered.connect(self.dashboard_view.open_program_editor)
        window_menu.addAction(editor_action)

        # Qt has no MenuRole for "check for updates" (only About/Preferences/
        # Quit get auto-relocated into the native "AKAISDS" app menu on
        # macOS - see QAction.MenuRole), so this stays a plain Help menu on
        # every platform rather than trying to fake native placement
        help_menu = self.menuBar().addMenu("&Help")

        quickstart_action = QAction("Quick Start Guide...", self)
        quickstart_action.triggered.connect(lambda: show_quickstart_dialog(self))
        help_menu.addAction(quickstart_action)
        help_menu.addSeparator()

        about_action = QAction("About AKAISDS...", self)
        about_action.setMenuRole(QAction.MenuRole.AboutRole)
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)

        check_for_updates_action = QAction("Check for Updates...", self)
        check_for_updates_action.triggered.connect(self._check_for_updates_manual)
        help_menu.addAction(check_for_updates_action)

        self.dashboard_view.register_menu_actions(
            {
                self.dashboard_view.btn_settings: settings_action,
                self.dashboard_view.btn_refresh: refresh_action,
                self.dashboard_view.btn_send: send_action,
                self.dashboard_view.btn_receive: receive_action,
                self.dashboard_view.btn_global_settings: transfer_settings_action,
                self.dashboard_view.btn_select_all: select_all_action,
                self.dashboard_view.btn_delete_selected: delete_selected_action,
                self.dashboard_view.btn_clear_queue: clear_queue_action,
                self.dashboard_view.btn_cancel: cancel_action,
                # mirrors btn_open_editor's own enabled state (both MIDI
                # ports selected AND sampler type set to Akai - see
                # TransferDashboard._update_open_editor_enabled), same
                # "button is the source of truth, menu action just follows
                # it" pattern as every other entry here
                self.dashboard_view.btn_open_editor: editor_action,
            },
            text_sync_map={
                self.dashboard_view.btn_select_all: select_all_action,
            },
        )

        self._update_runner = UpdateCheckRunner(self)

        # delay slightly so this doesn't compete with startup/port restore,
        # and only fire automatically if it's been a while since the last check
        if (
            time.time() - app_config.get_last_update_check()
            > _UPDATE_CHECK_INTERVAL_SECONDS
        ):
            QTimer.singleShot(2000, lambda: self._update_runner.start(manual=False))

    def _show_about_dialog(self):
        dialog = AboutDialog(self)
        dialog.exec()

    def _check_for_updates_manual(self):
        self._update_runner.start(manual=True)

    def _open_files_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Open Audio Files",
            os.path.expanduser("~"),
            _build_audio_filter_string(),
        )
        if paths:
            self.dashboard_view.on_files_dropped(paths)

    def _open_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Open Folder", os.path.expanduser("~")
        )
        if folder:
            self.dashboard_view.on_files_dropped([folder])

    def _restore_saved_ports(self):
        input_name, output_name = app_config.get_saved_ports()

        available_inputs = self.midi_manager.list_inputs()
        available_outputs = self.midi_manager.list_outputs()

        if input_name and input_name in available_inputs:
            try:
                self.midi_manager.open_input(input_name)
            except Exception:
                # a silent startup convenience - no dialog on failure, but
                # still worth a trace so "ports reset every launch" is
                # diagnosable instead of looking like they were never saved
                debug_log.get_logger().error(
                    f"ApplicationWindow: couldn't restore saved MIDI input {input_name!r}",
                    exc_info=True,
                )

        if output_name and output_name in available_outputs:
            try:
                self.midi_manager.open_output(output_name)
            except Exception:
                debug_log.get_logger().error(
                    f"ApplicationWindow: couldn't restore saved MIDI output {output_name!r}",
                    exc_info=True,
                )

    def closeEvent(self, event):
        # release midi ports cleanly so mido's backend doesnt hang around like a fart in a doctor's waiting room
        self.midi_manager.close_input()
        self.midi_manager.close_output()

        # if an update check is mid-flight when the app quits, make sure its
        # background thread has actually stopped before this window (and
        # the worker with it) can be garbage collected - see
        # UpdateCheckRunner.wait()'s docstring
        self._update_runner.wait()

        # this session's own stable copies of dropped/opened queue files
        # (see dashboard.py's create_local_row / core/dropped_files.py) -
        # a normal quit means every one of them is done being useful,
        # whether or not their rows individually got cleaned up already.
        # The crash-safety backstop for when this DOESN'T run
        # (sweep_orphaned_sessions) lives at the next launch instead, not
        # here.
        dropped_files.cleanup_session()

        super().closeEvent(event)


def _build_audio_filter_string():
    extensions = " ".join(
        f"*{ext}" for ext in sorted(sds_encoder.SUPPORTED_AUDIO_EXTENSIONS)
    )
    return f"Audio Files ({extensions})"
