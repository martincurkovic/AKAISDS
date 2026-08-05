from PySide6.QtWidgets import QMainWindow, QFileDialog
import os
from PySide6.QtGui import QAction, QKeySequence
from ui.dashboard import TransferDashboard
from ui.about_dialog import AboutDialog
from core.midi_manager import MidiManager
from core import app_config
from controller.sampler_controller import SamplerController


class ApplicationWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AKAI SDS")
        self.setMinimumSize(1030, 600)

        # owns the real mido ports for the app's lifetime
        self.midi_manager = MidiManager()
        self.sampler_controller = SamplerController(self.midi_manager)

        # restore saved channel/device type BEFORE building dashboard so the UI will be correct
        self.sampler_controller.set_channel(app_config.get_saved_channel())
        self.sampler_controller.set_device_type(app_config.get_saved_device_type())

        # reconnect to whatever MIDI ports were used last time, if still exist
        self._restore_saved_ports()

        # TEMPORARY DEBUG WIRING - print EVERY status handshake message to console so we can debug responses from the sampler
        self.sampler_controller.status_changed.connect(print)

        # instantiate custom UI layout components
        self.dashboard_view = TransferDashboard(
            self.sampler_controller, self.midi_manager
        )

        # mount layout into core central display panel area
        self.setCentralWidget(self.dashboard_view)

        # Settings menu action
        file_menu = self.menuBar().addMenu("File")

        about_action = QAction("About AKAISDS...", self)
        about_action.setMenuRole(QAction.MenuRole.AboutRole)
        about_action.triggered.connect(self._show_about_dialog)
        file_menu.addAction(about_action)

        file_menu.addSeparator()

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
        settings_action.setShortcut(QKeySequence.StandardKey.Preferences)
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
            },
            text_sync_map={
                self.dashboard_view.btn_select_all: select_all_action,
            },
        )

    def _show_about_dialog(self):
        dialog = AboutDialog(self)
        dialog.exec()

    def _open_files_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Open Audio Files",
            os.path.expanduser("~"),
            "Audio Files (*.wav *.aif *.aiff *.flac)",
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
            self.midi_manager.open_input(input_name)

        if output_name and output_name in available_outputs:
            self.midi_manager.open_output(output_name)

    def closeEvent(self, event):
        # release midi ports cleanly so mido's backend doesnt hang around like a fart in a doctor's waiting room
        self.midi_manager.close_input()
        self.midi_manager.close_output()
        super().closeEvent(event)
