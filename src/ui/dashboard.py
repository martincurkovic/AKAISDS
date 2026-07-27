import os
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from core import sds_encoder
from ui.qt_helpers import load_colored_pixmap
from ui.settings_dialog import MidiSettingsDialog
from ui.drop_list_widget import DropListWidget
from ui.sample_settings_dialog import SampleSettingsDialog
from ui.sample_info_dialog import SampleInfoDialog
from ui.ascii_logo import LOGO

SETTINGS_ROLE = Qt.ItemDataRole.UserRole + 1


class TransferDashboard(QWidget):
    def __init__(self, sampler_controller, midi_manager, parent=None):
        super().__init__(parent)

        # single MidiManager instance is owwned by the main window
        # and handed down here - everything MIDI related goes thru it
        self.midi_manager = midi_manager
        self.sampler_controller = sampler_controller
        self.sampler_controller.sample_list_updated.connect(self.on_sample_list_updated)
        self.sampler_controller.transfer_progress.connect(self.on_transfer_progress)
        self.sampler_controller.unit_progress.connect(self.on_unit_progress)
        self.sampler_controller.transfer_finished.connect(self.on_transfer_finished)
        self.sampler_controller.file_transferred.connect(self.on_file_transferred)
        self.sampler_controller.sample_received.connect(self.on_sample_received)
        self.sampler_controller.receive_finished.connect(self.on_receive_finished)
        self.sampler_controller.receive_progress.connect(self.on_receive_progress)
        self.sampler_controller.sample_info_received.connect(
            self.on_sample_info_received
        )

        # tracks queue row's name edit field status
        self._active_edit_field = None

        self._pending_deletions = []  # sample numbers queued for sequential deletion

        # mono/stereo channel count icons, recoloured once from source svg's
        icons_dir = os.path.join(os.path.dirname(__file__), "icons")
        self._mono_icon = load_colored_pixmap(
            os.path.join(icons_dir, "mono-svgrepo-com.svg"), "#888888", size=18
        )
        self._stereo_icon = load_colored_pixmap(
            os.path.join(icons_dir, "stereo-svgrepo-com.svg"), "#888888", size=18
        )

        # tracks the current batch progress for the overall progress bar
        # how many files finished vs how many were queued when send was clicked
        self._queue_total = 0
        self._queue_completed = 0

        # DEFAULT transmission values for newly dropped files
        self._global_bit_depth = 16
        self._global_sample_rate = None
        self._global_mono = False

        # top level layout (vertical architecture)
        # everything will stack cleanly top to bottom
        master_layout = QVBoxLayout(self)
        master_layout.setContentsMargins(15, 15, 15, 15)
        master_layout.setSpacing(12)

        # TOP BAR - to finish later but holds settings button for now
        top_bar = QHBoxLayout()

        self.lbl_logo = QLabel(LOGO)
        logo_font = QFont("Menlo")
        logo_font.setStyleHint(QFont.StyleHint.Monospace)
        logo_font.setPointSize(8)
        self.lbl_logo.setFont(logo_font)
        self.lbl_logo.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_logo.setWordWrap(False)
        self.lbl_logo.setStyleSheet("background: transparent;")
        top_bar.addWidget(self.lbl_logo)

        top_bar.addStretch()
        self.btn_settings = QPushButton("\u2699 MIDI Settings")
        self.btn_settings.clicked.connect(self.open_settings_dialog)
        top_bar.addWidget(self.btn_settings)

        # TWIN PANELS BABYYYY (horizontal layout nesting)
        panel_layout = QHBoxLayout()
        panel_layout.setSpacing(15)

        # LEFT COLUMN - Files queue list thingy
        left_container = QWidget()
        left_vbox = QVBoxLayout(left_container)
        left_vbox.setContentsMargins(0, 0, 0, 0)

        lbl_local = QLabel("<b>File Transfer Queue</b>")

        local_header = QHBoxLayout()
        local_header.addWidget(lbl_local, stretch=1)
        self.btn_clear_queue = QPushButton("Clear Queue")
        self.btn_clear_queue.clicked.connect(self.clear_local_queue)
        local_header.addWidget(self.btn_clear_queue)
        self.btn_global_settings = QPushButton("\u2699 Transmission Settings")
        self.btn_global_settings.clicked.connect(self.open_global_settings_dialog)
        local_header.addWidget(self.btn_global_settings)

        self.list_local = DropListWidget()
        self.list_local.setDragDropMode(DropListWidget.DragDropMode.InternalMove)
        self.list_local.setSelectionMode(DropListWidget.SelectionMode.SingleSelection)
        self.list_local.filesDropped.connect(self.on_files_dropped)

        # placeholder text shown when queue is empty
        self.empty_queue_label = QLabel(
            "Drag audio files here to add them to the queue", self.list_local.viewport()
        )
        self.empty_queue_label.setObjectName("emptyQueueLabel")
        self.empty_queue_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_queue_label.setWordWrap(True)
        self.empty_queue_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self.list_local.set_overlay_widget(self.empty_queue_label)
        self._update_empty_queue_placeholder()

        left_vbox.addLayout(local_header)
        left_vbox.addWidget(self.list_local)

        # RIGHT COLUMN - files on the akai already
        right_container = QWidget()
        right_vbox = QVBoxLayout(right_container)
        right_vbox.setContentsMargins(0, 0, 0, 0)

        lbl_hardware = QLabel("<b>Currently Loaded Samples</b>")
        self.list_hardware = (
            DropListWidget()
        )  # not used for drop here, just re-using the same widget type
        self.list_hardware.setSelectionMode(DropListWidget.SelectionMode.NoSelection)
        self.list_hardware.setAcceptDrops(False)

        # HEADER ROW - title + refresh button share one line
        hardware_header = QHBoxLayout()
        hardware_header.addWidget(lbl_hardware, stretch=1)
        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.clicked.connect(self.toggle_select_all_hardware_samples)
        # fixed width for select all button
        # calculated width rather than guessing px values lmao
        metrics = QFontMetrics(self.btn_select_all.font())
        button_width = (
            max(
                metrics.horizontalAdvance("Select All"),
                metrics.horizontalAdvance("Deselect All"),
            )
            + 24
        )  # giving extra padding juuuuust in case
        self.btn_select_all.setFixedWidth(button_width)
        hardware_header.addWidget(self.btn_select_all)
        self.btn_delete_selected = QPushButton("Delete Selected")
        self.btn_delete_selected.clicked.connect(
            self.confirm_and_delete_selected_samples
        )
        hardware_header.addWidget(self.btn_delete_selected)
        self.btn_refresh = QPushButton("\u27f3 Refresh")
        self.btn_refresh.clicked.connect(self.request_sample_list)
        hardware_header.addWidget(self.btn_refresh)

        right_vbox.addLayout(hardware_header)
        right_vbox.addWidget(self.list_hardware)

        # NEST BOTH CONNTAINERS SIDE BY SIDE INTO HORIZONTAL LAYOUT
        panel_layout.addWidget(left_container)
        panel_layout.addWidget(right_container)

        # ACtION CONTROL BAR (Horizontal layout)
        action_layout = QHBoxLayout()
        self.btn_send = QPushButton("Send Samples")
        self.btn_receive = QPushButton("Receive Samples")
        self.btn_cancel = QPushButton("Cancel Transfer")
        self.btn_cancel.setEnabled(False)

        self.btn_send.clicked.connect(self.send_queued_samples)
        self.btn_receive.clicked.connect(self.receive_selected_samples)
        self.btn_cancel.clicked.connect(self.cancel_transfer)

        # Add stretch spacer to push both control columns cleanly to the bottom of the window
        action_layout.addStretch()
        action_layout.addWidget(self.btn_cancel)
        action_layout.addWidget(self.btn_receive)
        action_layout.addWidget(self.btn_send)

        # PROGRESS BAR BABYYYY
        self.progress_bar_current_smpl = QProgressBar()
        self.progress_bar_current_smpl.setRange(0, 100)
        self.progress_bar_current_smpl.setValue(0)
        self.progress_bar_current_smpl.setVisible(False)

        self.progress_bar_overall = QProgressBar()
        self.progress_bar_overall.setRange(0, 100)
        self.progress_bar_overall.setValue(0)
        self.progress_bar_overall.setVisible(False)

        # STATUS FEEDBACK BAR
        self.status_bar = QStatusBar()
        self.status_bar.setSizeGripEnabled(False)
        self.status_bar.showMessage("Ready")

        # show controller's detailed status messages
        self.sampler_controller.status_changed.connect(self.on_status_message)

        # FINAL ASSEMBLY
        master_layout.addLayout(top_bar)
        master_layout.addLayout(
            panel_layout, stretch=1
        )  # stretch=1 forces panels to grab all expanding screen space
        master_layout.addLayout(action_layout)
        master_layout.addWidget(self.progress_bar_current_smpl)
        master_layout.addWidget(self.progress_bar_overall)
        master_layout.addWidget(self.status_bar)

    def open_settings_dialog(self):
        dialog = MidiSettingsDialog(self.midi_manager, self)
        dialog.exec()

    def request_sample_list(self):
        self.sampler_controller.refresh_sample_list()

    def on_status_message(self, message):
        # 5 second timeout  for status messages
        self.status_bar.showMessage(message, 5000)

    def on_sample_list_updated(self, names):
        self.list_hardware.clear()
        for sample_number, name in enumerate(names):
            self.create_hardware_row(name, sample_number)
        self.btn_select_all.setText("Select All")

    def on_files_dropped(self, paths):
        added = 0
        for path in paths:
            if (
                os.path.splitext(path)[1].lower()
                in sds_encoder.SUPPORTED_AUDIO_EXTENSIONS
            ):
                self.create_local_row(path)
                added += 1
            else:
                self.status_bar.showMessage(
                    f"Skipped non-WAV file: {os.path.basename(path)}"
                )
        if added:
            self.status_bar.showMessage(f"Added {added} file(s) to the queue")
        self._update_empty_queue_placeholder()

    def _update_empty_queue_placeholder(self):
        self.empty_queue_label.setVisible(self.list_local.count() == 0)

    def open_global_settings_dialog(self):
        dialog = SampleSettingsDialog(
            self,
            title="Global Transmission Settings",
            show_name=False,
            bit_depth=self._global_bit_depth,
            sample_rate=self._global_sample_rate,
            mono=self._global_mono,
        )
        if dialog.exec():
            settings = dialog.get_settings()
            self._global_bit_depth = settings["bit_depth"]
            self._global_sample_rate = settings["sample_rate"]
            self._global_mono = settings["mono"]

        # bulk apply to every file in the queue (per file names are left untouched)
        # this dialog doesnt have a name field
        applied = 0
        for i in range(self.list_local.count()):
            item = self.list_local.item(i)
            item.setData(
                SETTINGS_ROLE,
                {
                    "bit_depth": self._global_bit_depth,
                    "sample_rate": self._global_sample_rate,
                    "mono": self._global_mono,
                },
            )
            applied += 1

        if applied:
            self.status_bar.showMessage(
                f"Applied global settings to {applied} file(s) in the queue"
            )
        else:
            self.status_bar.showMessage(
                "Gloabl settings saved - will apply to files added from now on"
            )

    def open_edit_dialog(self, item, edit_field):
        current_settings = item.data(SETTINGS_ROLE) or {
            "bit_depth": self._global_bit_depth,
            "sample_rate": self._global_sample_rate,
            "mono": self._global_mono,
        }

        dialog = SampleSettingsDialog(
            self,
            title="Sample Settings",
            show_name=True,
            name=edit_field.text(),
            bit_depth=current_settings["bit_depth"],
            sample_rate=current_settings["sample_rate"],
            mono=current_settings["mono"],
        )
        if dialog.exec():
            settings = dialog.get_settings()
            edit_field.setText(settings["name"])
            item.setData(
                SETTINGS_ROLE,
                {
                    "bit_depth": settings["bit_depth"],
                    "sample_rate": settings["sample_rate"],
                    "mono": settings["mono"],
                },
            )

    def send_queued_samples(self):
        entries = []
        for i in range(self.list_local.count()):
            item = self.list_local.item(i)
            filepath = item.data(Qt.ItemDataRole.UserRole)
            row_widget = self.list_local.itemWidget(item)
            edit_field = row_widget.findChild(QLineEdit)
            override_name = edit_field.text().strip() if edit_field else None

            settings = item.data(SETTINGS_ROLE) or {
                "bit_depth": self._global_bit_depth,
                "sample_rate": self._global_sample_rate,
                "mono": self._global_mono,
            }

            entries.append(
                {
                    "filepath": filepath,
                    "name": override_name or None,
                    "bit_depth": settings["bit_depth"],
                    "sample_rate": settings["sample_rate"],
                    "mono": settings["mono"],
                }
            )

        if not entries:
            self.status_bar.showMessage(
                "No files in the queue to send - drag some WAV files in first"
            )
            return

        self.sampler_controller.send_file_queue(entries)

        self.btn_send.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        self._queue_total = len(entries)
        self._queue_completed = 0
        self.progress_bar_current_smpl.setValue(0)
        self.progress_bar_overall.setValue(0)
        self.progress_bar_current_smpl.setVisible(True)

        # single stereo file is still 2 transfers (L and R)
        # determine real units to send so the second progress_bar_overall can be shown appropriately
        total_units = 0
        for entry in entries:
            if entry["mono"]:
                units = 1
            else:
                try:
                    n_channels, _ = sds_encoder.read_wav_info(entry["filepath"])
                except (OSError, ValueError):
                    n_channels = (
                        1  # cant tell - the real sample send will show an actual error
                    )
                units = 2 if n_channels == 2 else 1
            total_units += units

        self.progress_bar_overall.setVisible(total_units > 1)

    def cancel_transfer(self):
        self.sampler_controller.cancel_transfer()

    def receive_selected_samples(self):
        selected = []
        for i in range(self.list_hardware.count()):
            item = self.list_hardware.item(i)
            row_widget = self.list_hardware.itemWidget(item)
            checkbox = row_widget.findChild(QCheckBox)
            if checkbox and checkbox.isChecked():
                sample_number = item.data(Qt.ItemDataRole.UserRole)
                selected.append((sample_number, checkbox.text().strip()))

        if not selected:
            self.status_bar.showMessage(
                "No samples selected - check the boxes next to the samples you want to receive"
            )
            return

        save_dir = QFileDialog.getExistingDirectory(
            self, "Choose folder to save received samples"
        )
        if not save_dir:
            return

        requests = []
        claimed_paths = set()
        for sample_number, name in selected:
            safe_name = self._sanitize_filename(name) or f"sample_{sample_number}"
            save_path = self._unique_save_path(save_dir, safe_name, claimed_paths)
            claimed_paths.add(save_path)
            requests.append((sample_number, save_path))

        self.sampler_controller.receive_samples(requests)

        # a receive is now running - disable Send/Receive so the user cant double click them
        # also enable cancel
        self.btn_send.setEnabled(False)
        self.btn_receive.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        self._queue_total = len(requests)
        self._queue_completed = 0
        self.progress_bar_current_smpl.setValue(0)
        self.progress_bar_overall.setValue(0)
        self.progress_bar_current_smpl.setVisible(True)
        self.progress_bar_overall.setVisible(self._queue_total > 1)

    @staticmethod
    def _sanitize_filename(name):
        name = name.strip()
        return name.replace("/", "-").replace("\\", "-")

    @staticmethod
    def _unique_save_path(save_dir, base_name, claimed_paths=()):
        # build a save path that doesnt collide with any other existing files
        path = os.path.join(save_dir, f"{base_name}.wav")
        counter = 2
        while os.path.exists(path) or path in claimed_paths:
            path = os.path.join(save_dir, f"{base_name} ({counter}).wav")
            counter += 1
        return path

    def on_receive_progress(self, received, total):
        percent = int((received / total) * 100) if total else 0
        self.progress_bar_current_smpl.setValue(percent)
        self.status_bar.showMessage(f"Receiving: {received}/{total} samples")

        if self._queue_total > 1:
            current_file_fraction = (received / total) if total else 0
            overall_percent = int(
                ((self._queue_completed + current_file_fraction) / self._queue_total)
                * 100
            )
            self.progress_bar_overall.setValue(overall_percent)

    def on_sample_received(self, path):
        self._queue_completed += 1
        if self._queue_total:
            percent = int((self._queue_completed / self._queue_total) * 100)
            self.progress_bar_overall.setValue(percent)

    def on_receive_finished(self, completed):
        self.btn_send.setEnabled(True)
        self.btn_receive.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        if completed:
            self.progress_bar_current_smpl.setValue(100)
            # samples that were just downloaded are done with - uncheck them so the list isn't left looking like theyre still queued
            # left checked on cancel
            for i in range(self.list_hardware.count()):
                item = self.list_hardware.item(i)
                row_widget = self.list_hardware.itemWidget(item)
                checkbox = row_widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.setChecked(False)
            self.btn_select_all.setText("Select All")
        else:
            self.progress_bar_current_smpl.setValue(0)
        self.progress_bar_current_smpl.setVisible(False)
        self.progress_bar_overall.setVisible(False)

    def on_transfer_progress(self, sent, total):
        percent = int((sent / total) * 100) if total else 0
        self.progress_bar_current_smpl.setValue(percent)
        self.status_bar.showMessage(f"Sending packet {sent}/{total}")

    def on_unit_progress(self, fraction):
        # drives overall progress bar
        if self._queue_total > 0:
            overall_percent = int(
                ((self._queue_completed + fraction) / self._queue_total) * 100
            )
            self.progress_bar_overall.setValue(min(overall_percent, 100))

    def on_file_transferred(self, filepath):
        # find and remove whichever row holds this exact file path
        # ie, the row's stored data (set in create_local_row) NOT its display text since the user may have renamed it
        for i in range(self.list_local.count()):
            item = self.list_local.item(i)
            if item.data((Qt.ItemDataRole.UserRole)) == filepath:
                self.list_local.takeItem(i)
                break

        self._update_empty_queue_placeholder()

        # one more file done out of however many were thrown in the queue
        self._queue_completed += 1
        if self._queue_total:
            percent = int((self._queue_completed / self._queue_total) * 100)
            self.progress_bar_overall.setValue(percent)

    def on_transfer_finished(self, completed):
        self.btn_send.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        if completed:
            self.progress_bar_current_smpl.setValue(100)
        else:
            self.progress_bar_current_smpl.setValue(0)

        # nothing happening anymore: hide both progress bars
        self.progress_bar_current_smpl.setVisible(False)
        self.progress_bar_overall.setVisible(False)

    # ROW BUILDER METHODS

    def _remove_local_row(self, item, edit_field):
        if self._active_edit_field is edit_field:
            self._active_edit_field = None
        self.list_local.takeItem(self.list_local.row(item))
        self._update_empty_queue_placeholder()

    def create_local_row(self, filepath):
        # build an editable, drag-swappable row with action buttons pinned to right side

        # create blank structural placeholder item inside real list
        item = QListWidgetItem(self.list_local)
        item.setData(Qt.ItemDataRole.UserRole, filepath)

        try:
            native_bit_depth = sds_encoder.read_wav_native_bit_depth(filepath)
        except (OSError, ValueError):
            native_bit_depth = 16  # cant tell, fall back to safe default

        default_bit_depth = min(self._global_bit_depth, native_bit_depth)

        item.setData(
            SETTINGS_ROLE,
            {
                "bit_depth": default_bit_depth,
                "sample_rate": self._global_sample_rate,
                "mono": self._global_mono,
            },
        )

        # create custom layout container canvas for the row
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(5, 2, 5, 2)

        # hamburger icon draggy thingy
        lbl_handle = QLabel("☰")
        lbl_handle.setFixedSize(24, 24)
        lbl_handle.setStyleSheet("color: #777777; font-size: 16px;")
        lbl_handle.setCursor(Qt.CursorShape.OpenHandCursor)

        # left component - use qline edit instead of qlabel to allow renaming
        display_name = os.path.splitext(os.path.basename(filepath))[0]
        edit_field = QLineEdit(display_name)
        edit_field.setReadOnly(True)

        # interactive logic for editing file names
        def enable_editing(event):
            # if a DIFFERENT row is being edited, close it explicitly first
            if (
                self._active_edit_field is not None
                and self._active_edit_field is not edit_field
            ):
                try:
                    self._active_edit_field.setReadOnly(True)
                    self._active_edit_field.clearFocus()
                except RuntimeError:
                    pass  # ceebs handling this properly, whatever

            edit_field.setReadOnly(False)
            edit_field.setFocus()
            self._active_edit_field = edit_field

        def disable_editing():
            edit_field.setReadOnly(True)
            edit_field.clearFocus()
            if self._active_edit_field is edit_field:
                self._active_edit_field = None

        edit_field.mouseDoubleClickEvent = enable_editing
        edit_field.returnPressed.connect(disable_editing)
        edit_field.editingFinished.connect(disable_editing)

        # right components - small control buttons
        btn_edit = QPushButton("Edit")
        btn_del = QPushButton("Delete")

        # make buttons compact so they dont look fucken massive hey
        btn_edit.setFixedHeight(24)
        btn_del.setFixedHeight(24)

        # mono/stereo indicator icon
        try:
            n_channels, _ = sds_encoder.read_wav_info(filepath)
        except (OSError, ValueError):
            n_channels = 1  # cant tell, fall back to the mono icon i guess??

        lbl_channels = QLabel()
        lbl_channels.setPixmap(
            self._stereo_icon if n_channels == 2 else self._mono_icon
        )
        lbl_channels.setFixedSize(24, 24)
        lbl_channels.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_channels.setToolTip("Stereo" if n_channels == 2 else "Mono")

        # opens the per-file settings dialog
        btn_edit.clicked.connect(
            lambda checked=False, it=item, ef=edit_field: self.open_edit_dialog(it, ef)
        )

        # remove this file from the queue - ie remove this row
        # nothing has been sent to the hardware yet
        btn_del.clicked.connect(
            lambda checked=False, it=item, ef=edit_field: self._remove_local_row(it, ef)
        )

        # assemble row layout horizontaly
        row_layout.addWidget(lbl_handle)
        row_layout.addWidget(
            edit_field, stretch=1
        )  # stretch=1 forces file name to take maximum room
        row_layout.addWidget(lbl_channels)
        row_layout.addWidget(btn_edit)
        row_layout.addWidget(btn_del)

        # inject custom canvas widget directly into list row framework
        item.setSizeHint(row_widget.sizeHint())
        self.list_local.setItemWidget(item, row_widget)

    def create_hardware_row(self, filename, sample_number):
        # build read only, fized row with leading checkbox and trailing action buttons
        item = QListWidgetItem(self.list_hardware)
        item.setData(Qt.ItemDataRole.UserRole, sample_number)

        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(5, 2, 5, 2)

        # left component - checkbox with name acting as its linked text
        checkbox = QCheckBox(filename)
        checkbox.setStyleSheet("font-size: 13px;")

        # right components - action buttons
        btn_edit = QPushButton("Edit")
        btn_del = QPushButton("Delete")
        btn_edit.setFixedHeight(24)
        btn_del.setFixedHeight(24)

        btn_del.clicked.connect(
            lambda checked=False, name=filename, num=sample_number: (
                self.confirm_and_delete_sample(name, num)
            )
        )

        btn_edit.clicked.connect(
            lambda checked=False, num=sample_number: (
                self.sampler_controller.request_sample_info(num)
            )
        )

        # assemble row layout horizontally
        row_layout.addWidget(checkbox, stretch=1)
        row_layout.addWidget(btn_edit)
        row_layout.addWidget(btn_del)

        # inject canvas widget into list row framework
        item.setSizeHint(row_widget.sizeHint())
        self.list_hardware.setItemWidget(item, row_widget)

    def on_sample_info_received(self, info):
        dialog = SampleInfoDialog(self, info)
        if dialog.exec():
            new_name = dialog.get_new_name()
            if new_name and new_name != info["name"]:
                self.sampler_controller.rename_sample(info["sample_number"], new_name)

    def confirm_and_delete_sample(self, name, sample_number):
        reply = QMessageBox.question(
            self,
            "Delete Sample",
            f"Delete '{name.strip()}' (sample {sample_number}) from the sampler? "
            f"This cannot be undone. ",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.sampler_controller.delete_sample(sample_number)

    def clear_local_queue(self):
        self.list_local.clear()
        self._active_edit_field = None
        self._update_empty_queue_placeholder()
        self.status_bar.showMessage("Cleared the file transfer queue")

    def toggle_select_all_hardware_samples(self):
        now_selecting = self.btn_select_all.text() == "Select All"
        for i in range(self.list_hardware.count()):
            item = self.list_hardware.item(i)
            row_widget = self.list_hardware.itemWidget(item)
            checkbox = row_widget.findChild(QCheckBox)
            if checkbox:
                checkbox.setChecked(now_selecting)
        self.btn_select_all.setText("Deselect All" if now_selecting else "Select All")

    def confirm_and_delete_selected_samples(self):
        to_delete = []
        for i in range(self.list_hardware.count()):
            item = self.list_hardware.item(i)
            row_widget = self.list_hardware.itemWidget(item)
            checkbox = row_widget.findChild(QCheckBox)
            if checkbox and checkbox.isChecked():
                sample_number = item.data(Qt.ItemDataRole.UserRole)
                to_delete.append((checkbox.text().strip(), sample_number))

        if not to_delete:
            self.status_bar.showMessage(
                "No samples selected - please select samples to delete"
            )
            return

        preview_names = ", ".join(name for name, _ in to_delete[:5])
        if len(to_delete) > 5:
            preview_names += f", and {len(to_delete) - 5} more"

        reply = QMessageBox.question(
            self,
            "Delete Selected Samples",
            f"Delete {len(to_delete)} sample(s) from the sampler?\n({preview_names})\n"
            f"This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # delete HIGHEST sample number FIRST
        # because removing a sample shifts every later sample's number down by one,
        # so deleting in ASCENDING order will end up targetting the wrong samples partway thru
        # also paced with a small gap instead of firing DELS messages b2b
        # allows the MIDI interface and sampler to keep up
        self._pending_deletions = sorted(
            (sample_number for _, sample_number in to_delete), reverse=True
        )
        self._delete_next_pending_sample()

    def _delete_next_pending_sample(self):
        if not self._pending_deletions:
            return
        sample_number = self._pending_deletions.pop(0)
        self.sampler_controller.delete_sample(sample_number)
        if self._pending_deletions:
            QTimer.singleShot(100, self._delete_next_pending_sample)
