from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt
from ui.settings_dialog import MidiSettingsDialog


class TransferDashboard(QWidget):
    def __init__(self, sampler_controller, midi_manager, parent=None):
        super().__init__(parent)

        # single MidiManager instance is owwned by the main window
        # and handed down here - everything MIDI related goes thru it
        self.midi_manager = midi_manager
        self.sampler_controller = sampler_controller
        self.sampler_controller.sample_list_updated.connect(self.on_sample_list_updated)

        # TEMPORARY TEST WIRING!!!
        self.sampler_controller.transfer_progress.connect(self.on_transfer_progress)
        self.sampler_controller.transfer_finished.connect(self.on_transfer_finished)

        # top level layout (vertical architecture)
        # everything will stack cleanly top to bottom
        master_layout = QVBoxLayout(self)
        master_layout.setContentsMargins(15, 15, 15, 15)
        master_layout.setSpacing(12)

        # TOP BAR - to finish later but holds settings button for now
        top_bar = QHBoxLayout()
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
        self.list_local = QListWidget()
        self.list_local.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list_local.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

        left_vbox.addWidget(lbl_local)
        left_vbox.addWidget(self.list_local)

        # populate left list using custom row customisation
        local_files = [
            "Sample 01.wav",
            "Sample 02.wav",
            "Sample 03.wav",
            "Sample 04.wav",
            "Sample 05.wav",
        ]
        for file_name in local_files:
            self.create_local_row(file_name)

        # RIGHT COLUMN - files on the akai already
        right_container = QWidget()
        right_vbox = QVBoxLayout(right_container)
        right_vbox.setContentsMargins(0, 0, 0, 0)

        lbl_hardware = QLabel("<b>Currently Loaded Samples</b>")
        self.list_hardware = QListWidget()
        self.list_hardware.setSelectionMode(QListWidget.SelectionMode.NoSelection)

        # HEADER ROW - title + refresh button share one line
        hardware_header = QHBoxLayout()
        hardware_header.addWidget(lbl_hardware, stretch=1)
        self.btn_refresh = QPushButton("\u27f3 Refresh")
        self.btn_refresh.clicked.connect(self.request_sample_list)
        hardware_header.addWidget(self.btn_refresh)

        right_vbox.addLayout(hardware_header)
        right_vbox.addWidget(self.list_hardware)

        # NEST BOTH CONNTAINERS SIDE BY SIDE INTO HORIZONTAL LAYOUT
        panel_layout.addWidget(left_container)
        panel_layout.addWidget(right_container)

        # populate right list using checkbox row customisation
        hardware_files = ["SAMPLE01", "SAMPLE02", "SAMPLE03 -L", "SAMPLE03 -R"]
        for file_name in hardware_files:
            self.create_hardware_row(file_name)

        # ACtION CONTROL BAR (Horizontal layout)
        action_layout = QHBoxLayout()
        self.btn_send = QPushButton("Send Samples")
        self.btn_recieve = QPushButton("Recieve Samples")

        # TEMPORARY!!! Hijack send button to fire hardcoded test transfer
        self.btn_send.clicked.connect(self.test_send_sample)

        # Add stretch spacer to push both control columns cleanly to the bottom of the window
        action_layout.addStretch()
        action_layout.addWidget(self.btn_recieve)
        action_layout.addWidget(self.btn_send)

        # PROGRESS BAR BABYYYY
        self.progress_bar_current_smpl = QProgressBar()
        self.progress_bar_current_smpl.setRange(0, 100)
        self.progress_bar_current_smpl.setValue(0)

        self.progress_bar_overall = QProgressBar()
        self.progress_bar_overall.setRange(0, 100)
        self.progress_bar_overall.setValue(0)

        # STATUS FEEDBACK BAR
        self.status_bar = QStatusBar()
        self.status_bar.showMessage("Ready")

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

    def on_sample_list_updated(self, names):
        self.list_hardware.clear()
        for name in names:
            self.create_hardware_row(name)
        self.status_bar.showMessage(f"Loaded {len(names)} sample(s) from hardware")

    # TEMPORARY TEST METHODS!!!!!
    def test_send_sample(self):
        # point this to a real wav file to test sending samples
        test_path = "/Users/martincurkovic/Music/Sample Packs/Akai Packs/SCSI ID 5 - Default/Partition A - Stabs and Synths/Volume 002 - Zenhiser Stabs 1/Stab1_01.wav"
        self.sampler_controller.send_sample_file(test_path, sample_number=0)

    def on_transfer_progress(self, sent, total):
        percent = int((sent / total) * 100) if total else 0
        self.progress_bar_current_smpl.setValue(percent)
        self.status_bar.showMessage(f"Sending packet {sent}/{total}")

    def on_transfer_finished(self):
        self.progress_bar_current_smpl.setValue(100)
        self.status_bar.showMessage("Transfer complete")

    # ROW BUILDER METHODS
    def create_local_row(self, filename):
        # build an editable, drag-swappable row with action buttons pinned to right side

        # create blank structural placeholder item inside real list
        item = QListWidgetItem(self.list_local)

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
        edit_field = QLineEdit(filename)
        edit_field.setReadOnly(True)
        edit_field.setStyleSheet(
            "background: transparent; border: none; font-size: 13px; color: black;"
        )

        # interactive logic for editing file names
        def enable_editing(event):
            edit_field.setReadOnly(False)
            edit_field.setFocus()
            edit_field.setStyleSheet(
                "background: #2a2a35; border: 1px solid #3f3f4e; font-size: 13px; color: white;"
            )

        def disable_editing():
            edit_field.setReadOnly(True)
            edit_field.clearFocus()
            edit_field.setStyleSheet(
                "background: transparent; border: none; font-size: 13px; color: black;"
            )

        edit_field.mouseDoubleClickEvent = enable_editing
        edit_field.returnPressed.connect(disable_editing)
        edit_field.editingFinished.connect(disable_editing)

        # right components - small control buttons
        btn_edit = QPushButton("Edit")
        btn_del = QPushButton("Delete")

        # make buttons compact so they dont look fucken massive hey
        btn_edit.setFixedHeight(24)
        btn_del.setFixedHeight(24)

        # assemble row layout horizontaly
        row_layout.addWidget(lbl_handle)
        row_layout.addWidget(
            edit_field, stretch=1
        )  # stretch=1 forces file name to take maximum room
        row_layout.addWidget(btn_edit)
        row_layout.addWidget(btn_del)

        # inject custom canvas widget directly into list row framework
        item.setSizeHint(row_widget.sizeHint())
        self.list_local.setItemWidget(item, row_widget)

    def create_hardware_row(self, filename):
        # build read only, fized row with leading checkbox and trailing action buttons
        item = QListWidgetItem(self.list_hardware)

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

        # assemble row layout horizontally
        row_layout.addWidget(checkbox, stretch=1)
        row_layout.addWidget(btn_edit)
        row_layout.addWidget(btn_del)

        # inject canvas widget into list row framework
        item.setSizeHint(row_widget.sizeHint())
        self.list_hardware.setItemWidget(item, row_widget)
