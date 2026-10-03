import os
import tempfile
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
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
from core import (
    app_config,
    dropped_files,
    sample_slicing,
    sds_encoder,
    program_editor_bridge,
    debug_log,
    sampler_models,
)
from ui.qt_helpers import load_colored_pixmap
from ui.settings_dialog import MidiSettingsDialog
from ui.drop_list_widget import DropListWidget
from ui.program_editor_window import ProgramEditorWindow
from ui.sample_settings_dialog import SampleSettingsDialog
from ui.sample_info_dialog import SampleInfoDialog
from ui.slice_editor_window import SliceEditorWindow
from ui.ascii_logo import LOGO
from ui import tooltips

SETTINGS_ROLE = Qt.ItemDataRole.UserRole + 1


class TransferDashboard(QWidget):
    def __init__(self, sampler_controller, midi_manager, parent=None):
        super().__init__(parent)

        # crash-safety backstop for the stable local copies dropped/opened
        # files get (see create_local_row and core/dropped_files.py's own
        # module docstring) - a session that never reaches
        # dropped_files.cleanup_session() (main_window.py's closeEvent)
        # leaves its own PID-named directory behind forever otherwise.
        # Self-healing at the next normal launch, before this session's
        # own directory is ever created, rather than a background
        # watchdog - there's no reason to notice a crash while this app
        # isn't even running.
        dropped_files.sweep_orphaned_sessions()

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
        self.sampler_controller.memory_status_updated.connect(
            self.on_memory_status_updated
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
        self._global_starting_sample_number = None  # required for generic SDS sends

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
        self.btn_settings = QPushButton("\u2699 Settings...")
        self.btn_settings.clicked.connect(self.open_settings_dialog)

        self.btn_open_editor = QPushButton("Open Editor...")
        self.btn_open_editor.clicked.connect(self.open_program_editor)
        # only enabled once there's actually somewhere for the editor to
        # connect TO - both a MIDI input and output port selected, and the
        # sampler type set to "Akai Sampler" (program_editor_bridge.connect()
        # needs Akai-specific SysEx extensions the Generic SDS protocol
        # family doesn't have) - see _update_open_editor_enabled, called
        # here and after the Settings dialog closes (open_settings_
        # dialog), the same two points _update_device_type_ui already
        # hooks for its own device-type-driven enabling.
        settings_editor_column = QVBoxLayout()
        settings_editor_column.setSpacing(6)
        settings_editor_column.addWidget(self.btn_settings)
        settings_editor_column.addWidget(self.btn_open_editor)
        # same width, stacked - both requested directly rather than the
        # original side-by-side layout; sized to whichever button's own
        # text is wider so neither one gets clipped (computed from each
        # button's own current sizeHint rather than hardcoded, so renaming
        # either button's text - as already happened once - can't silently
        # go stale)
        button_width = max(
            self.btn_settings.sizeHint().width(),
            self.btn_open_editor.sizeHint().width(),
        )
        self.btn_settings.setFixedWidth(button_width)
        self.btn_open_editor.setFixedWidth(button_width)
        top_bar.addLayout(settings_editor_column)
        # without this, top_bar (a QHBoxLayout) stretches this nested
        # column to the row's full height - set by the tall multi-line
        # logo beside it - and with nothing in the column claiming that
        # slack itself, QBoxLayout dumps ALL of it into the one gap
        # between the two buttons (see _build_section_card's own comment
        # on this exact behaviour), which read as a much bigger gap than
        # the 2px spacing actually asked for. This tells top_bar to give
        # the column its own natural (sizeHint) height instead and center
        # it vertically, so the buttons sit their own 2px apart.
        top_bar.setAlignment(settings_editor_column, Qt.AlignmentFlag.AlignVCenter)

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

        # placeholder overlay - reuses the same mechanism as file queue's "drag files here" placeholder
        self.empty_hardware_label = QLabel("", self.list_hardware.viewport())
        self.empty_hardware_label.setObjectName("emptyQueueLabel")
        self.empty_hardware_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hardware_label.setWordWrap(True)
        self.empty_hardware_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self.list_hardware.set_overlay_widget(self.empty_hardware_label)

        # MEMORY AVAILABILITY PROGRESS BAR
        self.memory_avail_prog_bar = QProgressBar()
        self.memory_avail_prog_bar.setRange(0, 100)
        self.memory_avail_prog_bar.setVisible(True)
        self.memory_avail_prog_bar.setFormat("%p% memory used")
        # self.memory_avail_prog_bar.setFixedHeight(20) # nup, this looks shit, dont worry about it

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
        self.btn_select_all.setEnabled(False)
        hardware_header.addWidget(self.btn_select_all)
        self.btn_delete_selected = QPushButton("Delete Selected")
        self.btn_delete_selected.clicked.connect(
            self.confirm_and_delete_selected_samples
        )
        self.btn_delete_selected.setEnabled(False)
        hardware_header.addWidget(self.btn_delete_selected)
        self.btn_refresh = QPushButton("\u27f3 Refresh")
        self.btn_refresh.clicked.connect(self.request_sample_list)
        hardware_header.addWidget(self.btn_refresh)

        right_vbox.addLayout(hardware_header)
        right_vbox.addWidget(self.list_hardware)
        right_vbox.addWidget(self.memory_avail_prog_bar)

        # NEST BOTH CONNTAINERS SIDE BY SIDE INTO HORIZONTAL LAYOUT
        panel_layout.addWidget(left_container)
        panel_layout.addWidget(right_container)

        # ACtION CONTROL BAR (Horizontal layout)
        action_layout = QHBoxLayout()
        self.btn_send = QPushButton("Send Samples")
        self.btn_send.setEnabled(False)
        self.btn_receive = QPushButton("Receive Samples")
        self.btn_receive.setEnabled(False)
        self.btn_cancel = QPushButton("Cancel Transfer")
        self.btn_cancel.setEnabled(False)

        self.btn_send.clicked.connect(self.send_queued_samples)
        self.btn_receive.clicked.connect(self.on_receive_clicked)
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

        # STATUS FEEDBACK BAR - not added to master_layout: TransferDashboard
        # is just this window's central widget, so this is handed up to the
        # real QMainWindow (ApplicationWindow, in main_window.py) to mount
        # via setStatusBar() instead - the same native, pinned-to-the-bottom
        # docking used for the program editor's status bar, rather than
        # being one more widget stacked at the end of a plain layout
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

        # reflect whatever device type was ALREADY restored before the dashboard was constructed
        self._update_device_type_ui()
        self._update_queue_buttons_state()
        self._update_open_editor_enabled()

    def open_program_editor(self):
        # public (no leading underscore) since main_window.py's "Program
        # Editor" menu action calls this directly, same as its other
        # menu-wired dashboard methods
        if self.sampler_controller.is_transfer_busy():
            # belt-and-braces alongside btn_open_editor/editor_action being
            # disabled while busy (see _update_open_editor_enabled) - a
            # second S3kBridge/BridgeWorker connecting to the sampler while
            # a send/receive is still in flight on the shared MIDI
            # connection is the exact dual-connection race AGENTS.md's
            # "Follow-up, first real-hardware session" section documents
            self.status_bar.showMessage(
                "Can't open the Program Editor - a MIDI transfer is already in progress"
            )
            return
        main_window = self.window()
        sampler_model = self.sampler_controller.sampler_model
        if sampler_models.is_s1000(
            sampler_model
        ) and not app_config.get_s1000_editor_warning_acknowledged():
            # S1000 editing was written from Akai's spec alone, with no
            # S1000 on hand to test against - every edit rewrites the whole
            # program/keygroup/sample block (the S1000 has no per-field
            # write), so say so before the first one, once
            answer = QMessageBox.warning(
                self,
                "Akai S1000 - experimental",
                "Program Editor support for the Akai S1000 is experimental.\n\n"
                "The S1000 can only be edited a whole program, keygroup or "
                "sample header at a time, so every change you make rewrites "
                "that entire block on the sampler. It was written from "
                "Akai's documentation without an S1000 to test against.\n\n"
                "Save anything you can't afford to lose to disk first, and "
                "please report problems along with ~/.akaisds/akaisds.log.",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Ok:
                return
            app_config.save_s1000_editor_warning_acknowledged(True)
        try:
            bridge = program_editor_bridge.connect(self.midi_manager, sampler_model)
        except Exception as e:
            # connect() failures happen before LoggingBridge ever wraps
            # anything, so without this they're invisible to
            # akaisds.log - the one file AGENTS.md says to ask a user
            # for when diagnosing a real-hardware issue
            debug_log.get_logger().error(
                "TransferDashboard: couldn't connect to the sampler for the "
                "Program Editor",
                exc_info=True,
            )
            QMessageBox.warning(
                self, "Couldn't connect", f"Couldn't reach the sampler: {e}"
            )
            return
        self.editor_window = ProgramEditorWindow(main_window, bridge=bridge)
        self.editor_window.show()
        main_window.hide()

    def open_settings_dialog(self):
        if self.sampler_controller.is_transfer_busy():
            # same reasoning as open_program_editor's own guard - the
            # Settings dialog can reopen midi_manager's ports outright,
            # which would pull them out from under an in-flight transfer
            self.status_bar.showMessage(
                "Can't open Settings - a MIDI transfer is already in progress"
            )
            return
        dialog = MidiSettingsDialog(self.midi_manager, self.sampler_controller, self)
        dialog.exec()
        self._update_device_type_ui()
        self._update_open_editor_enabled()

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
        self._update_empty_hardware_placeholder()
        has_samples = len(names) > 0
        self.btn_select_all.setEnabled(has_samples)
        self.btn_receive.setEnabled(
            not self.sampler_controller.is_open_loop() and has_samples
        )
        self.btn_delete_selected.setEnabled(False)

    def on_files_dropped(self, paths):
        paths = self._expand_dropped_paths(paths)
        added = 0
        for path in paths:
            if (
                os.path.splitext(path)[1].lower()
                in sds_encoder.SUPPORTED_AUDIO_EXTENSIONS
            ):
                # False means create_local_row already showed its own
                # "couldn't add" status message (the file was gone before
                # it could even be copied - see its own comment) - don't
                # let the "Added N file(s)" message below stomp on that
                if self.create_local_row(path):
                    added += 1
            else:
                self.status_bar.showMessage(
                    f"Skipped non-WAV file: {os.path.basename(path)}"
                )
        if added:
            self.status_bar.showMessage(f"Added {added} file(s) to the queue")
            self._update_queue_buttons_state()
        self._update_empty_queue_placeholder()

    def _update_empty_queue_placeholder(self):
        self.empty_queue_label.setVisible(self.list_local.count() == 0)

    def _update_empty_hardware_placeholder(self):
        if self.sampler_controller.device_type == "akai":
            if self.sampler_controller.is_open_loop():
                self.empty_hardware_label.setText(
                    "No MIDI Input selected - can't refresh or receive samples\n"
                    "(sending samples will still work without a MIDI Input)"
                )
            else:
                self.empty_hardware_label.setText(
                    "No samples loaded - click Refresh to check"
                )
            self.empty_hardware_label.setVisible(self.list_hardware.count() == 0)

    def _update_open_editor_enabled(self):
        # Program Editor needs a real, fully-configured connection to talk
        # to - both a MIDI input AND output port actually selected (not
        # "(None)" - see MidiSettingsDialog._populate_ports/_apply_and_close,
        # which is the only place that ever changes midi_manager.input_name/
        # output_name), and the sampler type set to Akai (not Generic SDS -
        # program_editor_bridge.connect() talks the Akai-specific SysEx
        # extensions this whole window is built on, which Generic SDS
        # doesn't have at all). Called at the two points the dashboard's
        # own knowledge of this state can change: construction (whatever
        # was restored from app_config before this widget was built - see
        # __init__'s own comment) and after the Settings dialog closes
        # (open_settings_dialog) - same two hooks _update_device_type_ui
        # already uses for its own device-type-driven enabling.
        # register_menu_actions' polling timer mirrors this onto the
        # Window menu's "Program Editor" action automatically - no direct
        # wiring to it needed here.
        has_ports = bool(self.midi_manager.input_name) and bool(
            self.midi_manager.output_name
        )
        is_akai = self.sampler_controller.device_type == "akai"
        # a MIDI transfer in progress (started from here, or from the
        # Program Editor sharing this same sampler_controller) locks out
        # BOTH windows that could open a second thing on the wire - see
        # open_program_editor/open_settings_dialog's own matching guards,
        # which this only mirrors visually (disabling a button/action
        # doesn't stop a direct call, hence the guards existing there too)
        busy = self.sampler_controller.is_transfer_busy()
        self.btn_settings.setEnabled(not busy)
        self.btn_settings.setToolTip(
            tooltips.BUSY_BLOCKS_OTHER_WINDOWS if busy else ""
        )
        self.btn_open_editor.setEnabled(has_ports and is_akai and not busy)
        if busy:
            self.btn_open_editor.setToolTip(tooltips.BUSY_BLOCKS_OTHER_WINDOWS)
        elif has_ports and is_akai:
            self.btn_open_editor.setToolTip("")
        elif not has_ports:
            self.btn_open_editor.setToolTip(tooltips.OPEN_EDITOR_NEEDS_MIDI_PORTS)
        else:
            self.btn_open_editor.setToolTip(
                tooltips.OPEN_EDITOR_NEEDS_AKAI_DEVICE_TYPE
            )

    def _update_device_type_ui(self):
        is_generic = self.sampler_controller.device_type == "generic"  # True or False
        is_open_loop = self.sampler_controller.is_open_loop()
        has_samples = self.list_hardware.count() > 0

        if is_generic:
            self.list_hardware.setEnabled(False)
            self.btn_select_all.setEnabled(False)
            self.btn_delete_selected.setEnabled(False)
            self.btn_refresh.setEnabled(False)
            self.btn_receive.setEnabled(not is_open_loop)
            self.memory_avail_prog_bar.setVisible(False)
        else:
            self.list_hardware.setEnabled(True)
            self.btn_select_all.setEnabled(has_samples)
            self.btn_refresh.setEnabled(not is_open_loop)
            self.btn_receive.setEnabled(not is_open_loop and has_samples)
            self.memory_avail_prog_bar.setVisible(True)
            self.memory_avail_prog_bar.setEnabled(not is_open_loop)
            self._update_delete_selected_button_state()

        if is_generic:
            self.list_hardware.clear()
            self.empty_hardware_label.setText(
                "Not available via Generic SDS - browsing, renaming, and\n"
                "deleting samples relies on proprietary extensions to the\n"
                "SDS standard, which a generic device cannot support."
            )
            self.empty_hardware_label.setVisible(True)
        else:
            self._update_empty_hardware_placeholder()

    def open_global_settings_dialog(self):
        dialog = SampleSettingsDialog(
            self,
            title="Transmission Settings",
            show_name=False,
            bit_depth=self._global_bit_depth,
            sample_rate=self._global_sample_rate,
            mono=self._global_mono,
            show_starting_slot=(
                self.sampler_controller.device_type == "generic"
                or self.sampler_controller.is_open_loop()
            ),
            starting_sample_number=self._global_starting_sample_number,
        )
        if not dialog.exec():
            return

        settings = dialog.get_settings()
        self._global_bit_depth = settings["bit_depth"]
        self._global_sample_rate = settings["sample_rate"]
        self._global_mono = settings["mono"]
        self._global_starting_sample_number = settings["starting_sample_number"]

        # bulk apply to every file in the queue (per file names are left untouched)
        # this dialog doesnt have a name field
        applied = 0
        new_settings = {
            "bit_depth": self._global_bit_depth,
            "sample_rate": self._global_sample_rate,
            "mono": self._global_mono,
        }

        for i in range(self.list_local.count()):
            item = self.list_local.item(i)
            item.setData(SETTINGS_ROLE, new_settings)
            row_widget = self.list_local.itemWidget(item)
            filepath = item.data(Qt.ItemDataRole.UserRole)
            self._refresh_row_indicators(row_widget, filepath, new_settings)

            applied += 1

        if applied:
            self.status_bar.showMessage(
                f"Applied global settings to {applied} file(s) in the queue"
            )
        else:
            self.status_bar.showMessage(
                "Global settings saved - will apply to files added from now on"
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
            show_slice_button=True,
        )
        accepted = dialog.exec()
        if dialog.slice_requested:
            # jumps to the Slice Editor instead of saving anything from
            # this dialog - whatever's in its fields right now was never
            # meant to be committed (see SampleSettingsDialog._request_
            # slice_editor's own comment)
            self._open_slice_editor(item, edit_field)
            return
        if accepted:
            settings = dialog.get_settings()
            edit_field.setText(settings["name"])
            # QLineEdit only recomputes its horizontal scroll offset lazily,
            # inside its own paintEvent, based on the cursor position AT
            # THAT MOMENT - calling setCursorPosition(0) synchronously right
            # after setText() sets the logical cursor correctly, but nothing
            # guarantees a repaint happens before something else (a focus
            # change, another update in this same call stack) touches the
            # field again first. Deferring to the next event-loop turn via
            # singleShot(0, ...) runs this once everything from setText()'s
            # own pending updates has already settled, which is the
            # standard workaround for this exact class of Qt timing quirk.
            QTimer.singleShot(0, lambda ef=edit_field: ef.setCursorPosition(0))
            new_settings = {
                "bit_depth": settings["bit_depth"],
                "sample_rate": settings["sample_rate"],
                "mono": settings["mono"],
            }
            item.setData(SETTINGS_ROLE, new_settings)
            row_widget = self.list_local.itemWidget(item)
            filepath = item.data(Qt.ItemDataRole.UserRole)
            self._refresh_row_indicators(row_widget, filepath, new_settings)

    def _open_slice_editor(self, item, edit_field):
        # brings the Slice Editor (ui/slice_editor_window.py) - previously
        # only reachable from the Program Editor's Samples tab, hardware-
        # bound and Akai-only - to the Transfer Dashboard's own local
        # queue instead, so a file can be chopped into one-shot slices
        # BEFORE it's ever sent anywhere. Works for both Akai and Generic
        # SDS targets identically, since nothing here talks to a sampler
        # at all - see _export_slices_to_queue below for why there's no
        # device-type branching anywhere in this path.
        #
        # Stereo: the waveform/marker editing and click-to-preview are
        # deliberately never made stereo-aware - a slice boundary is just
        # a frame index, identical for both channels since they're time-
        # aligned, so SliceEditorWindow only ever sees/plays the left
        # channel (read_wav_channels()[0], same value read_wav_samples()
        # would have given). The right channel (if any) is read here too
        # and only ever touched again in _export below, at the point
        # sliced audio actually gets written to disk - unlike the Program
        # Editor's own Slice Editor use, which is genuinely mono-only
        # (Akai hardware has no such thing as a stereo resident sample -
        # see AGENTS.md), a queued LOCAL file can be real stereo, and this
        # app's existing Send path already treats it as one (sends a
        # -L/-R pair - see sds_encoder.read_wav_channels/
        # sampler_controller.build_stereo_channel_name) - dropping the
        # right channel here without at least trying to preserve it would
        # be a real, silent loss of audio, not just a simplification.
        filepath = item.data(Qt.ItemDataRole.UserRole)
        try:
            channels, framerate = sds_encoder.read_wav_channels(filepath)
        except Exception as e:
            debug_log.get_logger().error(
                f"TransferDashboard: couldn't load {filepath!r} for slicing",
                exc_info=True,
            )
            self.status_bar.showMessage(
                f"Couldn't open Slice Editor - couldn't read the audio: {e}"
            )
            return

        samples = channels[0]
        right_channel = channels[1] if len(channels) == 2 else None

        sample_name = edit_field.text().strip() or "SLICE"

        def _existing_names():
            # collision-check against every OTHER queued row's own display
            # name - nothing's resident on a sampler yet, so there's no
            # hardware sample list to check against the way
            # ProgramEditorWindow._open_slice_editor's own
            # existing_names_provider does
            names = []
            for i in range(self.list_local.count()):
                other_item = self.list_local.item(i)
                if other_item is item:
                    continue
                other_widget = self.list_local.itemWidget(other_item)
                other_edit = other_widget.findChild(QLineEdit) if other_widget else None
                if other_edit is not None:
                    names.append(other_edit.text().strip())
            return names

        # assigned right below - _export only ever runs later, from a user
        # click inside dialog itself, so by the time it's actually called
        # `dialog` is always already set (a plain forward reference within
        # this same closure, not a race)
        dialog = None

        def _export(
            names,
            slices,
            slice_framerate,
            bit_depth,
            sample_rate,
            spitch,
            stuno,
            shlto,
            progress_callback,
            status_callback,
            busy_callback=None,
        ):
            # spitch/stuno/shlto are Akai program-header concepts
            # (SliceEditorWindow's shared export_callback contract always
            # forwards them) with no equivalent for a queued file that
            # hasn't been sent to any sampler yet - accepted positionally,
            # unused. busy_callback (ProgramEditorWindow._export_slices' own
            # indeterminate-progress marker for its post-send hardware
            # verify) is accepted for the same "shared contract" reason but
            # never called - this export never leaves the local queue, so
            # there's no hardware round-trip to mark indeterminate.
            right_slices = None
            if right_channel is not None:
                # the exact same start/end/markers that produced `slices`
                # (the left channel's own sliced buffers) just above, read
                # live off the still-open dialog - see sample_slicing.
                # slice_samples's own contract; this can't disagree with
                # `slices` since nothing else runs between SliceEditorWindow
                # computing one and calling this callback with the other
                right_slices = sample_slicing.slice_samples(
                    right_channel,
                    dialog.waveform.start(),
                    dialog.waveform.end(),
                    dialog.waveform.slice_markers(),
                )
            return self._export_slices_to_queue(
                item,
                names,
                slices,
                slice_framerate,
                bit_depth,
                sample_rate,
                progress_callback,
                status_callback,
                right_slices=right_slices,
            )

        dialog = SliceEditorWindow(
            self,
            sample_name,
            samples,
            framerate,
            60,
            0,
            0,
            _existing_names,
            _export,
            demo_mode=False,
            export_confirm_message=lambda slice_count, names: (
                f'Replace "{sample_name}" in the queue with {slice_count} '
                f'slice{"s" if slice_count != 1 else ""} '
                f'("{names[0]}".."{names[-1]}")? This cannot be undone.'
            ),
        )
        dialog.exec()

    def _remove_rows(self, items):
        for row_item in items:
            row_widget = self.list_local.itemWidget(row_item)
            edit_field = row_widget.findChild(QLineEdit) if row_widget else None
            self._remove_local_row(row_item, edit_field)

    def _export_slices_to_queue(
        self,
        item,
        names,
        slices,
        framerate,
        bit_depth,
        sample_rate,
        progress_callback,
        status_callback,
        right_slices=None,
    ):
        # the Transfer Dashboard's own export_callback for SliceEditorWindow
        # - see ProgramEditorWindow._export_slices for the hardware-sending
        # counterpart this mirrors. Nothing here talks to any sampler:
        # this operates purely on the local queue, replacing the row being
        # sliced with N new rows, one per slice - each becomes an ordinary
        # queued file from this point on, sent later (to an Akai OR a
        # Generic SDS device, identically) through the exact same
        # send_queued_samples path as any other queued file.
        #
        # Deliberately no equivalent of ProgramEditorWindow._export_slices'
        # own SPTYPE=3 "force one-shot" header write here - that's a
        # resident-Akai-sample-header concept with no meaning for a file
        # that hasn't been sent anywhere yet, so there's nothing to
        # disable for a Generic SDS target either: every file this app
        # ever sends is already unconditionally encoded as NO_LOOP at the
        # base SDS dump-header level regardless of device type (see
        # sds_encoder.build_dump_header) - one-shot is already the only
        # thing a plain queued-file send has ever produced, for both.
        #
        # right_slices (see _open_slice_editor) mirrors `slices` 1:1 when
        # the source file was stereo - each pair gets written as one real
        # interleaved stereo WAV (write_wav_file_stereo) rather than two
        # separate mono files; the existing Send path already knows how to
        # split a stereo queued file into a -L/-R pair on its own (see
        # sds_encoder.read_wav_channels/build_stereo_channel_name), so
        # there's nothing else stereo-specific to do here.
        logger = debug_log.get_logger()
        total = len(names)
        temp_paths = []
        new_items = []
        try:
            for index, name in enumerate(names):
                status_callback(f"Writing slice {index + 1}/{total}...")
                fd, temp_path = tempfile.mkstemp(
                    suffix=".wav", prefix="akaisds_slice_"
                )
                os.close(fd)
                temp_paths.append(temp_path)
                if right_slices is not None:
                    sds_encoder.write_wav_file_stereo(
                        temp_path,
                        slices[index],
                        right_slices[index],
                        framerate,
                        bit_depth=16,
                    )
                else:
                    sds_encoder.write_wav_file(
                        temp_path, slices[index], framerate, bit_depth=16
                    )

                if not self.create_local_row(temp_path):
                    self._remove_rows(new_items)
                    return False, (
                        f"Couldn't add slice {index + 1}/{total} to the queue "
                        "- see the status bar message above for why"
                    )
                new_item = self.list_local.item(self.list_local.count() - 1)
                new_row_widget = self.list_local.itemWidget(new_item)
                new_edit_field = (
                    new_row_widget.findChild(QLineEdit) if new_row_widget else None
                )
                if new_edit_field is not None:
                    new_edit_field.setText(name)
                    new_edit_field.setCursorPosition(0)
                # carries the dialog's own chosen bit depth/sample rate
                # through as this row's transmission override -
                # create_local_row otherwise defaults every new row to the
                # dashboard's global settings, which would silently
                # discard the choice made in the Slice Editor's own Bit
                # depth/Sample rate combos. mono is forced False for a
                # stereo source specifically - falling through to the
                # dashboard's own global mono default here would risk
                # silently sending just-preserved right-channel audio as
                # mono anyway the moment that default happens to be True
                new_settings = {
                    "bit_depth": bit_depth,
                    "sample_rate": sample_rate,
                    "mono": False if right_slices is not None else self._global_mono,
                }
                new_item.setData(SETTINGS_ROLE, new_settings)
                if new_row_widget is not None:
                    self._refresh_row_indicators(
                        new_row_widget,
                        new_item.data(Qt.ItemDataRole.UserRole),
                        new_settings,
                    )
                new_items.append(new_item)
                progress_callback(index + 1, total)

            # every slice landed - now safe to remove the original row it
            # replaces
            self._remove_rows([item])
            return True, (
                f"Replaced with {total} slice{'s' if total != 1 else ''}: "
                f"{names[0]}..{names[-1]}"
            )
        except Exception:
            logger.error("_export_slices_to_queue: unexpected error", exc_info=True)
            # roll back whatever slices already landed rather than leaving
            # a half-sliced queue with the original row still present too
            self._remove_rows(new_items)
            return False, (
                f"Slicing failed - unexpected error, see {debug_log.LOG_PATH} "
                "for details"
            )
        finally:
            for path in temp_paths:
                try:
                    os.remove(path)
                except OSError:
                    pass

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

        started = self.sampler_controller.send_file_queue(
            entries, starting_sample_number=self._global_starting_sample_number
        )
        if not started:
            return

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

    def on_receive_clicked(self):
        if self.sampler_controller.device_type == "generic":
            self.receive_sample_by_number_dialog()
        else:
            self.receive_selected_samples()

    def receive_sample_by_number_dialog(self):
        # generic SDS path
        # no way to browse device's memory, so user needs to enter sample number to receive
        sample_number, ok = QInputDialog.getInt(
            self,
            "Receive Sample",
            "Sample number to receive:",
            value=0,
            minValue=0,
            maxValue=16383,
        )
        if not ok:
            return

        default_path = os.path.join(
            os.path.expanduser("~"), f"sample_{sample_number}.wav"
        )
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save Sample As", default_path, "WAV Files (*.wav)"
        )
        if not save_path:
            return

        self.sampler_controller.receive_sample_generic(sample_number, save_path)

        self.btn_send.setEnabled(False)
        self.btn_receive.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        # single item "batch"
        self._queue_total = 1
        self._queue_completed = 0
        self.progress_bar_current_smpl.setValue(0)
        self.progress_bar_overall.setValue(0)
        self.progress_bar_current_smpl.setVisible(True)
        self.progress_bar_overall.setVisible(False)

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
            self, "Choose folder to save received samples", os.path.expanduser("~")
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
    def _expand_dropped_paths(paths):
        # Expand directories in a list of dropped paths into every file found recrusively inside them (ie, incl subfolders)
        # existing file extension check still decides what to accept from the drop
        expanded = []
        for path in paths:
            if os.path.isdir(path):
                for root, _dirs, files in os.walk(path):
                    for filename in sorted(files):
                        expanded.append(os.path.join(root, filename))
            else:
                expanded.append(path)
        return expanded

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

        # filepath is this row's own stable copy (see create_local_row) -
        # sent, skipped, or cancelled, there's no reason to keep it around
        # for the rest of the session once its row is gone
        dropped_files.remove_copy(filepath)

        self._update_queue_buttons_state()
        self._update_empty_queue_placeholder()

        # one more file done out of however many were thrown in the queue
        self._queue_completed += 1
        if self._queue_total:
            percent = int((self._queue_completed / self._queue_total) * 100)
            self.progress_bar_overall.setValue(percent)

    def on_transfer_finished(self, completed):
        self.btn_cancel.setEnabled(False)
        if completed:
            self.progress_bar_current_smpl.setValue(100)
        else:
            self.progress_bar_current_smpl.setValue(0)

        # nothing happening anymore: hide both progress bars
        self.progress_bar_current_smpl.setVisible(False)
        self.progress_bar_overall.setVisible(False)
        self._update_queue_buttons_state()

    # ROW BUILDER METHODS

    def _remove_local_row(self, item, edit_field):
        if self._active_edit_field is edit_field:
            self._active_edit_field = None
        # this row's own stable copy (see create_local_row) - no reason to
        # keep it around once the row itself is gone
        dropped_files.remove_copy(item.data(Qt.ItemDataRole.UserRole))
        self.list_local.takeItem(self.list_local.row(item))
        self._update_queue_buttons_state()
        self._update_empty_queue_placeholder()

    def _refresh_row_indicators(self, row_widget, filepath, settings):
        # updates a queue row's mono/stereo icon (respecting explicit override)
        # also updates the custom settings overrid easterisk
        # called upon row creation, editing or bulk update via global settings
        lbl_channels = row_widget.findChild(QLabel, "channelIcon")
        if lbl_channels is not None:
            try:
                n_channels, _ = sds_encoder.read_wav_info(filepath)
            except (OSError, ValueError):
                n_channels = 1

            is_effectively_stereo = (n_channels == 2) and not settings["mono"]
            lbl_channels.setPixmap(
                self._stereo_icon if is_effectively_stereo else self._mono_icon
            )
            lbl_channels.setToolTip("Stereo" if is_effectively_stereo else "Mono")

        lbl_override = row_widget.findChild(QLabel, "overrideIndicator")
        if lbl_override is not None:
            is_overridden = (
                settings["bit_depth"] != self._global_bit_depth
                or settings["sample_rate"] != self._global_sample_rate
                or settings["mono"] != self._global_mono
            )
            lbl_override.setText("*" if is_overridden else "")
            lbl_override.setToolTip(
                "Custom transmission settings for this file (differs from "
                "the global defaults)"
                if is_overridden
                else ""
            )

    def create_local_row(self, filepath):
        # copy into a stable, app-owned location FIRST, before building
        # anything - see core/dropped_files.py's own module docstring for
        # why: some sample-browser apps (Sononym, dragging a "cropped"
        # preview out) hand this app a path to a file THEY own and expect
        # to clean up shortly after the drop, which this app would
        # otherwise only discover much later, whenever Send actually gets
        # around to this file - too late to do anything but skip it.
        # Reading it now, right where it's still guaranteed to exist,
        # sidesteps that race entirely. Every consumer below (and every
        # other place this row's stored path gets read - the Send queue
        # included, via item.data(Qt.ItemDataRole.UserRole)) uses the
        # COPY, not the original, from this point on; `filepath` itself
        # is still used for the row's initial DISPLAY name below, so a
        # uniquifying suffix the copy's own filename might have needed
        # (see dropped_files._unique_name) never shows up in the UI.
        try:
            stable_path = dropped_files.copy_into_session(filepath)
        except OSError as e:
            self.status_bar.showMessage(
                f"Couldn't add {os.path.basename(filepath)}: {e}"
            )
            return False

        # build an editable, drag-swappable row with action buttons pinned to right side

        # create blank structural placeholder item inside real list
        item = QListWidgetItem(self.list_local)
        item.setData(Qt.ItemDataRole.UserRole, stable_path)

        try:
            native_bit_depth = sds_encoder.read_wav_native_bit_depth(stable_path)
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
        edit_field.setCursorPosition(0)

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

        # "custom settings" asterisk - updated by _refresh_row_indicators
        lbl_override = QLabel()
        lbl_override.setObjectName("overrideIndicator")
        lbl_override.setFixedSize(12, 24)
        lbl_override.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # mono/stereo indicator icon - set by _refresh_row_indicators
        lbl_channels = QLabel()
        lbl_channels.setObjectName("channelIcon")
        lbl_channels.setFixedSize(24, 16)
        lbl_channels.setAlignment(Qt.AlignmentFlag.AlignCenter)

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
        row_layout.addWidget(lbl_override)
        row_layout.addWidget(lbl_channels)
        row_layout.addWidget(btn_edit)
        row_layout.addWidget(btn_del)
        self._refresh_row_indicators(row_widget, stable_path, item.data(SETTINGS_ROLE))

        # inject custom canvas widget directly into list row framework
        item.setSizeHint(row_widget.sizeHint())
        self.list_local.setItemWidget(item, row_widget)
        return True

    def register_menu_actions(self, enabled_sync_map, text_sync_map=None):
        # keeps the menu options in sync with the button states
        # runs on a set timer to check button states (i know, i KNOWWWWWW.....)
        self._enabled_sync_map = enabled_sync_map
        self._text_sync_map = text_sync_map
        timer = QTimer(self)
        timer.timeout.connect(self._sync_menu_actions)
        timer.start(150)
        self._sync_menu_actions()

    def _sync_menu_actions(self):
        # re-derive btn_open_editor/btn_settings' own enabled state every
        # tick rather than only at the handful of explicit call sites
        # (construction, Settings closing, transfer start/finish) - a
        # transfer can also start/stop from the Program Editor side (it
        # shares this same sampler_controller), which has no reason to
        # know this dashboard even exists, let alone poke its buttons
        self._update_open_editor_enabled()
        for button, action in self._enabled_sync_map.items():
            action.setEnabled(button.isEnabled())
        for button, action in self._text_sync_map.items():  # type: ignore
            action.setText(button.text())

    def _update_queue_buttons_state(self):
        # updates the state of clear queue, transmission settings, send samples buttons
        has_files = self.list_local.count() > 0
        self.btn_clear_queue.setEnabled(has_files)
        self.btn_global_settings.setEnabled(has_files)
        self.btn_send.setEnabled(has_files)

    def _update_delete_selected_button_state(self):
        # enabled whenever at least ONE hardware checkbox is currently selected
        any_checked = False
        for i in range(self.list_hardware.count()):
            item = self.list_hardware.item(i)
            row_widget = self.list_hardware.itemWidget(item)
            checkbox = row_widget.findChild(QCheckBox)
            if checkbox and checkbox.isChecked():
                any_checked = True
                break
        self.btn_delete_selected.setEnabled(any_checked)

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
        checkbox.toggled.connect(self._update_delete_selected_button_state)

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

    def on_memory_status_updated(self, info):
        if info["max_num_samp_words"] > 0:
            percent_used = int(
                (info["max_num_samp_words"] - info["num_words_free"])
                / info["max_num_samp_words"]
                * 100
            )
        else:
            percent_used = 0

        if percent_used >= 90:
            self.memory_avail_prog_bar.setProperty("memoryLevel", "critical")
        elif percent_used >= 70:
            self.memory_avail_prog_bar.setProperty("memoryLevel", "warning")
        else:
            self.memory_avail_prog_bar.setProperty("memoryLevel", "normal")

        self.memory_avail_prog_bar.setValue(percent_used)
        self.memory_avail_prog_bar.setToolTip(
            f"{info['num_words_free']:,} sample words free\n"
            f"{info['num_blocks_free']:,} sample blocks/slots free"
        )
        self.memory_avail_prog_bar.style().unpolish(self.memory_avail_prog_bar)
        self.memory_avail_prog_bar.style().polish(self.memory_avail_prog_bar)

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
        # each row's own stable copy (see create_local_row) - list_local.
        # clear() below doesn't go through _remove_local_row/
        # on_file_transferred (the two other places this cleanup happens),
        # so it needs its own pass first
        for i in range(self.list_local.count()):
            item = self.list_local.item(i)
            dropped_files.remove_copy(item.data(Qt.ItemDataRole.UserRole))
        self.list_local.clear()
        self._active_edit_field = None
        self._update_empty_queue_placeholder()
        self.status_bar.showMessage("Cleared the file transfer queue")
        self.btn_clear_queue.setEnabled(False)
        self.btn_global_settings.setEnabled(False)
        self.btn_send.setEnabled(False)

    def toggle_select_all_hardware_samples(self):
        now_selecting = self.btn_select_all.text() == "Select All"
        for i in range(self.list_hardware.count()):
            item = self.list_hardware.item(i)
            row_widget = self.list_hardware.itemWidget(item)
            checkbox = row_widget.findChild(QCheckBox)
            if checkbox:
                checkbox.setChecked(now_selecting)
        self.btn_select_all.setText("Deselect All" if now_selecting else "Select All")
        self._update_delete_selected_button_state()

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
