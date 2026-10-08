"""Yamaha A4000/A5000 program editor.

Laid out like the S3000 editor (Programs | assigned samples | cards, plus a Samples tab) but for the
A4000's data model, which differs from an Akai's: a PROGRAM holds program-level settings and a list of
assigned samples; each assigned sample carries Easy Edit overrides that apply to THIS program only, while
the sample's own sound (filter, envelopes, key range ...) lives on the sample and is shared by every
program that plays it - that part is the Samples tab. So the middle column lists a program's assigned
samples the way the S3000 editor lists keygroups, with each one's EFFECTIVE key range on the bar: the
sample's own range moved by the Easy Edit key shift and cut by its key limits (owner's manual p.99).

All talking to the unit goes through the Dashboard's `SamplerController` -> `YamahaSession`
(controller/yamaha_session.py): the SAME MIDI connection, one operation at a time, no worker thread.
A program is read with ONE bulk dump (`PG`), a sample with one (`SP`); the program list is filled by a
background scan of every program's name and assigned-sample count (~7 s), and programs with nothing
assigned are hidden unless "Show empty programs" is ticked (there are always 128).

EDITING: a changed control is written to the sampler's memory (RAM - nothing is saved to its disk) one parameter
at a time through `ui/yamaha_writer.WriteCoordinator` -> `YamahaSession.write_parameter`, which saves a `.syx`
backup of the object before the first write to it, proves the right object is selected, and reads every value
back. The local cache is patched at once and the object is re-read once its writes settle, because the unit has
side effects an edit's own read-back can't show (mirrored bytes, derived EQ coefficients). A row the table marks
unwritable stays disabled (`FieldPanel`). The window refuses to close or refresh while a write is on its way.
"""

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from controller.yamaha_restore import RestoreJob
from core import debug_log
from core import yamaha_backups, yamaha_restore
from core import yamaha_params as yp
from core import yamaha_sysex as ysx
from core.midi_notes import midi_note_to_name
from ui import theme, tooltips as tt
from ui.diagnostics_ui import add_open_log_folder_action
from ui.yamaha_assign_dialog import AssignDialog
from ui.yamaha_restore_dialog import RestoreDialog
from ui.yamaha_writer import WriteCoordinator
from ui.editor_layout import (
    add_keygroup_row,
    build_content_row,
    build_list_column,
    build_paired_row,
    equalize_card_heights,
    keygroup_row_label,
    style_card_page_layout,
)
from ui.keygroup_range_bar import KeygroupRangeBar, keygroup_color
from ui.qt_helpers import FullWidthTabBar, build_scroll_area, build_section_card
from ui.yamaha_fields import Field, FieldPanel
from ui.yamaha_samples_tab import YamahaSamplesTab, _check, _combo, _knob, _spin

_SPECIAL_ALL_NOTES = {-1: "All notes"}
NOTE_MAX = 127


def _log(message):
    debug_log.get_logger().info(f"YamahaEditor: {message}")


# -- pure helpers (tested without a window) -----------------------------------------------------------


def program_row_text(number, name):
    return f"{number:03d}  {name}" if name else f"{number:03d}"


def effective_key_range(sample_data, easy_data, slot):
    """(low, high) a sample really plays in a program: its own range moved by the Easy Edit key range shift and
    cut by the key limits (owner's manual p.99: the shift offsets the sample's original key, low and high
    together; notes below the low limit / above the high limit do not sound). A sample's "Original" ends
    (low -1, high 128) are taken as the keyboard's ends. `easy_data` is the PROGRAM payload (slot picks the block)."""
    low = yp.extract(yp.get("sample", "key_range_low"), sample_data)
    high = yp.extract(yp.get("sample", "key_range_high"), sample_data)
    low = 0 if low < 0 else low
    high = NOTE_MAX if high > NOTE_MAX else high
    shift = yp.extract(yp.get("easy_edit", "key_range_shift"), easy_data, slot)
    limit_low = yp.extract(yp.get("easy_edit", "key_limit_low"), easy_data, slot)
    limit_high = yp.extract(yp.get("easy_edit", "key_limit_high"), easy_data, slot)
    low, high = max(low + shift, limit_low), min(high + shift, limit_high)
    low = min(max(low, 0), NOTE_MAX)
    high = min(max(high, low), NOTE_MAX)
    return low, high


def describe_restore_plan(plan, limit=8):
    """The confirmation text for a restore: what will change (current -> what the backup holds), and anything that won't."""
    kind = {"PG": "program", "SP": "sample"}.get(plan.fmt, plan.fmt)
    lines = [f"Put {len(plan.items)} value(s) of {kind} {plan.name!r} back to what the backup holds:", ""]
    for item in plan.items[:limit]:
        lines.append(f"   {item.label}: {item.current} -> {item.value}")
    if len(plan.items) > limit:
        lines.append(f"   ... and {len(plan.items) - limit} more")
    for note in plan.notes:
        lines += ["", note]
    lines += ["", "The object as it is now is saved as a new backup first, so this can be undone."]
    return "\n".join(lines)


def describe_restore_result(result):
    """The summary shown when a restore ends."""
    lines = [result.message]
    if result.remaining:
        shown = ", ".join(result.remaining[:6]) + (f" and {len(result.remaining) - 6} more" if len(result.remaining) > 6 else "")
        lines.append(f"Not restored: {shown}.")
    if result.residual_offsets:
        lines.append(
            f"{len(result.residual_offsets)} byte(s) still differ from the backup that the editor has no control over: the unit's own "
            "bookkeeping (for a sample, the right channel's mirror bytes) and, for a program, samples assigned or removed since the "
            "backup - use Assign Sample / Remove for those."
        )
    for note in result.notes:
        lines.append(note)
    if result.snapshot_path is not None:
        lines.append(f"The state before the restore is saved at {result.snapshot_path}")
    return "\n\n".join(lines)


def format_range(low, high):
    return f"{midi_note_to_name(low)} - {midi_note_to_name(high)}"


# -- the window ---------------------------------------------------------------------------------------------


class YamahaProgramEditorWindow(QMainWindow):
    def __init__(self, main_window, sampler_controller):
        super().__init__()
        self._main_window = main_window
        self._controller = sampler_controller
        self._session = sampler_controller.yamaha_session()
        _log("opened")
        self.setWindowTitle("AKAISDS - Yamaha A4000/A5000 Program Editor")
        self.setMinimumSize(1150, 720)

        self._program_numbers = []  # 1..128 from the object list
        self._sample_names = []
        self._counts = {}  # program number -> assigned-sample count (None until scanned)
        self._names = {}  # program number -> its 8-character name
        self._program_data = {}  # program number -> bulk payload
        self._sample_cache = {}  # sample name -> bulk payload (shared with the Samples tab)
        self._items = {}  # program number -> QListWidgetItem
        self._selected = None  # program number being shown
        self._scan_generation = 0  # bumped by every refresh so a late scan result is dropped
        self._scanning = False
        self._loading_program = None
        self._range_rows = []  # [(low, high)] of the assigned list (None until known)
        self._assigned = []  # [(sample name, object type)] of the shown program
        self._connected = True
        self._restoring = False  # a restore from backup is running: nothing else may write, refresh or close the window
        self._linking = False  # a sample is being assigned/removed: same lock
        self._edit_sample_ok = False  # the shown assigned entry is a sample this window knows (what "Edit Sample..." needs)
        self.model = None  # "A4000" / "A5000" / "unknown" from the unit's identity reply (None until it has answered)
        self._select_sample_after_load = None  # after an assignment, select that sample's row once the program is re-read

        self.program_panel = FieldPanel("program")
        self.easy_panel = FieldPanel("easy_edit")
        self._writer = WriteCoordinator(self._session, self)
        self._writer.message.connect(lambda m: self.status_bar.showMessage(m, 8000))
        self._build_ui()
        self._build_busy_indicator()
        self._build_menus()
        self._controller.status_changed.connect(self._on_controller_status)
        theme.notifier.changed.connect(self._refresh_themed_swatches)

        self._show_placeholder("Reading the object list...")
        QTimer.singleShot(0, self._refresh)

    # -- construction -------------------------------------------------------------------------------------

    def _build_busy_indicator(self):
        """The same debounce as the S3000 editor's loading bar: shown only once the session has been busy for 200 ms (a quick read
        never flashes it), hidden 150 ms after it goes idle (the program scan is a burst of back-to-back reads, which would
        otherwise flicker between them)."""
        self._busy_show_timer = QTimer(self)
        self._busy_show_timer.setSingleShot(True)
        self._busy_show_timer.setInterval(200)
        self._busy_show_timer.timeout.connect(lambda: self._loading_progress.setVisible(True))
        self._busy_hide_timer = QTimer(self)
        self._busy_hide_timer.setSingleShot(True)
        self._busy_hide_timer.setInterval(150)
        self._busy_hide_timer.timeout.connect(self._confirm_session_idle)
        self._session.busy_changed.connect(self._on_session_busy_changed)
        self._busy_hooked = True
        if self._session.working:
            self._on_session_busy_changed(True)

    def _on_session_busy_changed(self, busy):
        if busy:
            self._busy_hide_timer.stop()  # a burst of jobs is one continuous span
            # isHidden(), not isVisible(): the latter is false for a window that has not been shown yet
            if not self._busy_show_timer.isActive() and self._loading_progress.isHidden():
                self._busy_show_timer.start()
        else:
            self._busy_hide_timer.start()

    def _confirm_session_idle(self):
        if self._session.working:
            return  # (a stale False: more work has been queued since)
        self._busy_show_timer.stop()
        self._loading_progress.setVisible(False)

    def _build_ui(self):
        self.program_list = QListWidget()
        self.program_list.setObjectName("programList")
        self.program_list.setFixedWidth(190)
        self.program_list.currentItemChanged.connect(self._on_program_selected)
        self.program_list.itemClicked.connect(lambda _item: self.detail_stack.setCurrentIndex(0))
        self.show_empty_check = QCheckBox("Show empty programs")
        self.show_empty_check.setToolTip("There are always 128 programs; most are empty")
        self.show_empty_check.toggled.connect(self._apply_visibility)
        self.scan_label = QLabel("")
        self.scan_label.setObjectName("mutedLabel")
        programs_container = build_list_column("Programs", self.program_list, self.show_empty_check, self.scan_label)

        self.assigned_list = QListWidget()
        self.assigned_list.setObjectName("keygroupList")
        self.assigned_list.setFixedWidth(230)
        self.assigned_list.currentRowChanged.connect(self._show_assigned)
        self.assigned_list.itemClicked.connect(lambda _item: self.detail_stack.setCurrentIndex(1))
        self.range_bar = KeygroupRangeBar()
        self.assign_button = QPushButton("Assign Sample...")
        self.assign_button.setToolTip("Add one of the sampler's samples to this program")
        self.assign_button.clicked.connect(self._assign_sample)
        self.remove_assigned_button = QPushButton("Remove")
        self.remove_assigned_button.setToolTip("Take the selected sample out of this program (the sample itself stays on the sampler)")
        self.remove_assigned_button.clicked.connect(self._remove_assigned)
        assign_buttons = QWidget()
        assign_row = QHBoxLayout(assign_buttons)
        assign_row.setContentsMargins(0, 0, 0, 0)
        assign_row.addWidget(self.assign_button, 1)
        assign_row.addWidget(self.remove_assigned_button, 1)
        assigned_container = build_list_column("Assigned samples", self.range_bar, self.assigned_list, assign_buttons)

        self.placeholder = QLabel()
        self.placeholder.setObjectName("emptyQueueLabel")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(build_scroll_area(self._build_program_page()))
        self.detail_stack.addWidget(build_scroll_area(self._build_assigned_page()))
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.placeholder, 1)
        right.addWidget(self.detail_stack, 1)
        right_container = QWidget()
        right_container.setLayout(right)

        programs_tab = QWidget()
        programs_tab.setLayout(build_content_row(programs_container, assigned_container, right_container))
        self.samples_tab = YamahaSamplesTab(self._controller, self._session, self._sample_cache, self._writer)
        self.samples_tab.status_message.connect(lambda m: self.status_bar.showMessage(m, 8000))
        self.samples_tab.samples_changed.connect(self._on_samples_changed)
        self.samples_tab.programs_changed.connect(self._on_programs_changed)
        self.samples_tab.busy_changed.connect(lambda _busy: self._apply_lock())
        self.samples_tab.free_programs_provider = self._free_programs
        self.main_tabs = QTabWidget()
        self.main_tabs.setTabBar(FullWidthTabBar(self.main_tabs))
        self.main_tabs.addTab(programs_tab, "Programs")
        self._samples_tab_index = self.main_tabs.addTab(self.samples_tab, "Samples")
        self.main_tabs.currentChanged.connect(self._on_tab_changed)

        # indeterminate, like the S3000 editor's: a read over MIDI has no predictable length (see _on_session_busy_changed)
        self._loading_progress = QProgressBar()
        self._loading_progress.setRange(0, 0)
        self._loading_progress.setFixedWidth(120)
        self._loading_progress.setTextVisible(False)
        self._loading_progress.setVisible(False)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Re-read the program and sample lists from the sampler")
        self.refresh_button.clicked.connect(self._refresh)
        # while an audio load or an edit's send runs: stops it (like the S3000 editor's bottom-row Cancel). The Samples tab keeps its own
        # "Cancel load" beside the waveform.
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Stop the transfer that is running")
        self.cancel_button.clicked.connect(self._cancel_long_operation)
        self.cancel_button.setVisible(False)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(self.refresh_button)
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(self._loading_progress)  # beside Refresh, like the S3000 editor's
        bottom.addStretch()
        bottom.addWidget(close_button)
        layout = QVBoxLayout()
        layout.addWidget(self.main_tabs, stretch=1)
        layout.addLayout(bottom)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.status_bar = QStatusBar()
        self.status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self.status_bar)
        note = QLabel("Experimental - edits go to the sampler's memory (backed up first)")
        note.setObjectName("mutedLabel")
        self.status_bar.addPermanentWidget(note)
        for panel, handler in ((self.program_panel, self._on_program_edited), (self.easy_panel, self._on_easy_edited)):
            panel.set_editable(True)
            panel.edited.connect(handler)

    def _build_program_page(self):
        p = self.program_panel
        row, knobs = p.labeled_row, p.knob_row
        k, s, c, ck = _knob, _spin, _combo, _check
        note = Field("lfo_reset_note", "Reset note", "note", specials=_SPECIAL_ALL_NOTES)

        program = build_section_card(
            "Program",
            row(Field("program_name", "Name", "text")),
            row(Field("assigned_samples", "Assigned samples", "text")),
            knobs([k("program_level", "Level", 56)]),
            row(s("transpose", "Transpose")),
        )
        lfo = build_section_card(
            "LFO",
            row(c("lfo_wave", "Wave")),
            row(c("lfo_sync", "Sync")),
            row(s("lfo_tempo", "Tempo")),
            row(c("lfo_cycle", "Cycle")),
            row(c("lfo_initial_phase", "Initial phase")),
            row(note),
            row(c("lfo_reset_midi_channel", "Reset channel")),
        )
        porta = build_section_card(
            "Portamento & S/H",
            row(c("portamento_type", "Type")),
            knobs([k("portamento_rate", "Rate"), k("portamento_time", "Time"), k("sh_speed", "S/H speed")]),
        )
        audio = build_section_card(
            "Audio Input",
            row(ck("ad_in_on", "Input on")),
            row(c("ad_in_source", "Source")),
            knobs([k("ad_in_l_pan", "L pan"), k("ad_in_r_pan", "R pan")]),
            row(c("ad_in_l_output1", "L output 1")),
            row(c("ad_in_l_output2", "L output 2")),
            row(c("ad_in_r_output1", "R output 1")),
            row(c("ad_in_r_output2", "R output 2")),
            knobs([k("ad_in_l_output1_level", "L out 1"), k("ad_in_l_output2_level", "L out 2"),
                   k("ad_in_r_output1_level", "R out 1"), k("ad_in_r_output2_level", "R out 2")]),
        )
        steps = [k(f"lfo_step_value_{n}", str(n), 28) for n in range(1, 17)]
        step_card = build_section_card(
            "LFO Step Wave",
            row(c("lfo_step_total", "Total steps")),
            row(c("lfo_step_slope", "Slope")),
            p.knob_row(steps[:8], spacing=6),
            p.knob_row(steps[8:], spacing=6),
        )
        effects_note = QLabel("Effects and controllers are not shown yet.")
        effects_note.setObjectName("mutedLabel")

        # aligned pairs, each pinned to equal heights: the two short cards together (Program, Portamento) and the two tall
        # ones together (LFO, Audio Input), the wide Step Wave across the bottom
        equalize_card_heights(program, porta)
        equalize_card_heights(lfo, audio)

        layout = QVBoxLayout()
        style_card_page_layout(layout)
        layout.addLayout(build_paired_row(program, porta))
        layout.addLayout(build_paired_row(lfo, audio))
        layout.addWidget(step_card)
        layout.addWidget(effects_note)
        layout.addStretch()
        page = QWidget()
        page.setLayout(layout)
        return page

    def _build_assigned_page(self):
        e = self.easy_panel
        row, knobs = e.labeled_row, e.knob_row
        k, s, c, ck = _knob, _spin, _combo, _check
        low_note = Field("key_limit_low", "Key low limit", "note")
        high_note = Field("key_limit_high", "Key high limit", "note")

        self.assigned_name = QLabel("")
        self.assigned_name.setObjectName("sectionHeader")
        self.assigned_info = QLabel("")
        self.assigned_info.setObjectName("mutedLabel")
        self.assigned_info.setWordWrap(True)
        self.edit_sample_button = QPushButton("Edit Sample...")
        self.edit_sample_button.setToolTip("Open this sample's own parameters (filter, envelopes, loop...) in the Samples tab")
        self.edit_sample_button.clicked.connect(self._edit_sample)
        head = QHBoxLayout()
        title = QVBoxLayout()
        title.setSpacing(2)
        title.addWidget(self.assigned_name)
        title.addWidget(self.assigned_info)
        head.addLayout(title, stretch=1)
        head.addWidget(self.edit_sample_button)

        key = build_section_card(
            "Key",
            row(low_note),
            row(high_note),
            row(s("key_range_shift", "Key range shift")),
            row(s("velocity_limit_low", "Velocity low limit")),
            row(s("velocity_limit_high", "Velocity high limit")),
        )
        level = build_section_card(
            "Level & Pan",
            knobs([k("level_offset", "Level"), k("pan_offset", "Pan"), k("velocity_sensitivity_offset", "Velocity")]),
        )
        equalize_card_heights(key, level)

        pitch = build_section_card(
            "Pitch",
            knobs([k("coarse_tune_offset", "Coarse"), k("fine_tune_offset", "Fine")]),
            row(c("portamento", "Portamento")),
        )
        filt = build_section_card(
            "Filter",
            knobs([k("filter_cutoff_offset", "Cutoff"), k("filter_q_offset", "Q / Width"),
                   k("filter_gain_offset", "Gain"), k("cutoff_distance_offset", "Distance")]),
        )
        equalize_card_heights(pitch, filt)

        aeg = build_section_card(
            "Amplitude Envelope",
            knobs([k("aeg_attack_rate_offset", "Attack"), k("aeg_decay_rate_offset", "Decay"),
                   k("aeg_release_rate_offset", "Release")]),
        )
        xfade = build_section_card(
            "Velocity Crossfade",
            knobs([k("velocity_xfade_low_offset", "Low"), k("velocity_xfade_high_offset", "High")]),
        )
        equalize_card_heights(aeg, xfade)

        # two columns: where the sound goes (outputs and their levels) beside how it plays
        routing, playback = QVBoxLayout(), QVBoxLayout()
        routing.setSpacing(10)
        playback.setSpacing(10)
        for rows, column in (
            ((row(c("output1", "Output 1")), row(c("output2", "Output 2")),
              knobs([k("output1_level_offset", "Out 1 level"), k("output2_level_offset", "Out 2 level")])), routing),
            ((row(c("receive_channel", "MIDI channel")), row(c("mono_mode", "Mono mode")),
              row(c("key_xfade_on", "Key crossfade")),
              row(Field("alternate_group", "Alternate group", "spin", specials={-1: "=Sample"})),
              row(ck("midi_control_on", "MIDI control"))), playback),
        ):
            for r in rows:
                column.addLayout(r)
            column.addStretch()
        out_columns = QHBoxLayout()
        out_columns.setSpacing(20)
        out_columns.addLayout(routing, 1)
        out_columns.addLayout(playback, 1)
        out = build_section_card("Output & Playback", out_columns)

        layout = QVBoxLayout()
        style_card_page_layout(layout)
        layout.addLayout(head)
        layout.addLayout(build_paired_row(key, level))
        layout.addLayout(build_paired_row(pitch, filt))
        layout.addLayout(build_paired_row(aeg, xfade))
        layout.addWidget(out)
        layout.addStretch()
        page = QWidget()
        page.setLayout(layout)
        return page

    def _build_menus(self):
        hardware = self.menuBar().addMenu("&Hardware")
        self._refresh_action = QAction("Refresh from Hardware", self)
        self._refresh_action.setShortcut("Ctrl+R")
        self._refresh_action.triggered.connect(self._refresh)
        hardware.addAction(self._refresh_action)
        self._unchanged_action = QAction("Write Program Back Unchanged (test)", self)
        self._unchanged_action.setToolTip(
            "Writes the selected program's Level back to the value it already has, then checks that the program "
            "dump is byte for byte what it was - the safest first test of writing"
        )
        self._unchanged_action.triggered.connect(self._write_back_unchanged)
        hardware.addAction(self._unchanged_action)
        self._restore_action = QAction("Restore from Backup...", self)
        self._restore_action.setToolTip(
            "Put an object back to what one of its saved backups holds (the current state is saved first, so it can be undone)"
        )
        self._restore_action.triggered.connect(self._restore_from_backup)
        hardware.addAction(self._restore_action)
        backups = QAction("Open Backup Folder", self)
        backups.setToolTip(f"Opens {self._session.backup_dir} - the .syx saved before the first change to each object")
        backups.triggered.connect(self._open_backup_folder)
        hardware.addAction(backups)
        hardware.addSeparator()
        add_open_log_folder_action(hardware, self)

        window = self.menuBar().addMenu("&Window")
        dashboard = QAction("Transfer Dashboard", self)
        dashboard.setShortcut("Ctrl+T")
        dashboard.triggered.connect(self.close)
        window.addAction(dashboard)
        editor = QAction("Program Editor", self)
        editor.setShortcut("Ctrl+E")
        editor.setEnabled(False)
        window.addAction(editor)
        window.addSeparator()
        programs = QAction("Programs Tab", self)
        programs.setShortcut("Ctrl+2")
        programs.triggered.connect(lambda: self.main_tabs.setCurrentIndex(0))
        window.addAction(programs)
        self._tab_actions = [programs]
        samples = QAction("Samples Tab", self)
        samples.setShortcut("Ctrl+3")
        samples.triggered.connect(lambda: self.main_tabs.setCurrentIndex(self._samples_tab_index))
        window.addAction(samples)
        self._tab_actions.append(samples)

    def _on_tab_changed(self, index):
        on_samples = index == self._samples_tab_index
        _log(f"tab: {'Samples' if on_samples else 'Programs'}")
        self.samples_tab.set_active(on_samples)
        if not on_samples:
            self._recompute_ranges()  # a sample's key range may have been edited there

    # -- loading ------------------------------------------------------------------------------------------

    def _show_placeholder(self, text):
        self.placeholder.setText(text)
        self.placeholder.setVisible(True)
        self.detail_stack.setVisible(False)
        self.assigned_list.clear()
        self.range_bar.set_ranges([])

    def _show_details(self):
        self.placeholder.setVisible(False)
        self.detail_stack.setVisible(True)

    def _refresh(self):
        self._writer.flush()
        if self._restoring or self._linking:
            self.status_bar.showMessage("A change is being made on the sampler - try again when it has finished", 5000)
            return
        if self._writer.busy:
            self.status_bar.showMessage("Still writing to the sampler - try again in a moment", 5000)
            return
        _log("refresh")
        self._scan_generation += 1
        self._scanning = False
        self._session.cancel()
        self._counts.clear()
        self._names.clear()
        self._program_data.clear()
        self._sample_cache.clear()
        self.samples_tab.refresh()
        self._selected = None
        self.program_list.blockSignals(True)
        self.program_list.clear()
        self.program_list.blockSignals(False)
        self._items.clear()
        self._show_placeholder("Reading the object list...")
        self.scan_label.setText("")
        if self.model is None:  # (asked once it has been answered; a unit that didn't answer is asked again on the next refresh)
            self._session.request_identity(self._on_identity)
        self._session.request_object_list(self._on_object_list)

    def _on_identity(self, reply):
        """The unit's identity reply says which model is connected: the A5000's extra dropdown entries (effects 4-6 as an output) are offered
        only for an A5000. No reply leaves the editor as it was - A4000 features only."""
        if not self._connected:
            return
        self.model = reply.model if reply is not None else None
        _log(f"identity: {reply.model if reply is not None else 'no reply'}")
        a5000 = self.model == "A5000"
        for panel in (self.program_panel, self.easy_panel, self.samples_tab.panel):
            panel.set_a5000(a5000)

    def _on_object_list(self, entries):
        if entries is None:
            self._show_placeholder(
                "Couldn't read the sampler's object list.\n\nCheck the MIDI ports, that its Device Number is set "
                "(0 unless config.json's \"yamaha_device_number\" says otherwise) and that Bulk Protect is off."
            )
            return
        self._program_numbers = [int(e.name) for e in entries if e.kind == "program" and e.name.isdigit()]
        self._sample_names = [e.name for e in entries if e.kind == "sample"]
        _log(f"object list: {len(self._program_numbers)} programs, {len(self._sample_names)} samples")
        self.program_list.blockSignals(True)
        for number in self._program_numbers:
            item = QListWidgetItem(program_row_text(number, ""))
            item.setData(Qt.ItemDataRole.UserRole, number)
            self.program_list.addItem(item)
            self._items[number] = item
        self.program_list.blockSignals(False)
        self.samples_tab.set_samples(self._sample_names)
        self._apply_visibility()
        self._show_placeholder("Reading the programs...")
        self._start_scan()

    def _free_programs(self):
        """[(number, row text)] of the programs known to hold no sample (the scan has read them) - what the Slice Editor can fill."""
        return [(n, program_row_text(n, self._names.get(n, ""))) for n in self._program_numbers if self._counts.get(n) == 0]

    def _on_programs_changed(self, number, sample_names):
        """The Slice Editor assigned samples to a program: show what the unit holds now."""
        for name in sample_names:
            self._sample_cache.pop(name, None)  # their "used in programs" changed
        self._counts[number] = len(sample_names)
        self._program_data.pop(number, None)
        self._apply_visibility()
        self._reload_object("PG", ysx.program_object_name(number))
        self.status_bar.showMessage(f"Program {number:03d} now holds {len(sample_names)} slices", 8000)

    def _on_samples_changed(self, select_name):
        """The Samples tab made new samples (an edit's copy, slices): re-read the sample list and show them."""
        self._session.request_object_list(lambda entries: self._on_new_sample_list(entries, select_name))

    def _on_new_sample_list(self, entries, select_name):
        if entries is None or not self._connected:
            return
        self._sample_names = [e.name for e in entries if e.kind == "sample"]
        self.samples_tab.set_samples(self._sample_names)
        if select_name and select_name in self._sample_names:
            self.samples_tab.select_sample(select_name)

    # -- the background scan (names + assigned counts) --------------------------------------------------------

    def _start_scan(self):
        self._scanning = True
        self._scan_next(self._scan_generation)

    def _scan_next(self, generation):
        if generation != self._scan_generation or not self._connected:
            return
        todo = [n for n in self._program_numbers if n not in self._counts]
        if todo:
            # pass 1: every program's assigned-sample count (one small request each - ~7 s for all 128)
            number = todo[0]
            done = len(self._program_numbers) - len(todo)
            self.scan_label.setText(f"Scanning {done}/{len(self._program_numbers)}")
            self._session.request_parameters(
                "program", ysx.program_object_name(number), [yp.get("program", "assigned_samples").p],
                lambda results, n=number, g=generation: self._on_scanned(g, n, results),
            )
            return
        # pass 2: the names - only of programs that have samples (an empty one is just its number)
        unnamed = [n for n in self._program_numbers if self._counts.get(n) and n not in self._names]
        if unnamed:
            number = unnamed[0]
            self._session.request_parameters(
                "program", ysx.program_object_name(number), [yp.get("program", "program_name").p],
                lambda results, n=number, g=generation: self._on_named(g, n, results),
            )
            return
        self._scanning = False
        empty = sum(1 for n in self._program_numbers if not self._counts.get(n))
        self.scan_label.setText(f"{empty} program{'s' if empty != 1 else ''} empty")  # short: a long line widens the whole column
        if self._selected is None and not self._first_visible_number():
            self._show_placeholder("No programs have samples assigned.\n\nTick \"Show empty programs\" to see all 128.")

    def _on_named(self, generation, number, results):
        if generation != self._scan_generation or not self._connected:
            return
        msg = results[0]
        # a failed name read just leaves the bare number (never retried this refresh)
        self._names[number] = "" if msg is None else yp.decode_reply(yp.get("program", "program_name"), msg.data)
        self._items[number].setText(program_row_text(number, self._names[number]))
        QTimer.singleShot(0, lambda g=generation: self._scan_next(g))

    def _on_scanned(self, generation, number, results):
        if generation != self._scan_generation or not self._connected:
            return
        count_msg = results[0]
        if count_msg is None:
            self._scanning = False
            self.scan_label.setText("The scan stopped - the sampler stopped answering")
            if self._selected is None:
                self._show_placeholder("The sampler stopped answering while reading the program list.\n\nPress Refresh to try again.")
            return
        self._counts[number] = yp.decode_reply(yp.get("program", "assigned_samples"), count_msg.data)
        item = self._items[number]
        self._apply_visibility(only=number)
        if self._selected is None and self._counts[number] and self._first_visible_number() == number:
            self.program_list.setCurrentItem(item)  # the first program with samples, as the S3000 editor does
        QTimer.singleShot(0, lambda g=generation: self._scan_next(g))

    def _first_visible_number(self):
        for number in self._program_numbers:
            if not self._items[number].isHidden():
                return number
        return None

    def _apply_visibility(self, _checked=None, only=None):
        show_all = self.show_empty_check.isChecked()
        for number in [only] if only is not None else self._program_numbers:
            item = self._items.get(number)
            if item is None:
                continue
            has_samples = bool(self._counts.get(number))
            item.setHidden(not (show_all or has_samples))
        current = self.program_list.currentItem()
        if current is not None and current.isHidden():
            self.program_list.setCurrentItem(None)

    # -- one program ------------------------------------------------------------------------------------------

    def _on_program_selected(self, current, _previous):
        if current is None:
            return
        number = current.data(Qt.ItemDataRole.UserRole)
        self._selected = number
        _log(f"program selected: {number:03d}")
        self.detail_stack.setCurrentIndex(0)
        if number in self._program_data:
            self._show_program(number)
            return
        self._show_placeholder(f"Reading program {number:03d}...")
        self._loading_program = number
        self._session.request_bulk(
            "PG", ysx.program_object_name(number), lambda dump, n=number: self._on_program_dump(n, dump)
        )

    def _on_program_dump(self, number, dump):
        if not self._connected:
            return
        if dump is None:
            if number == self._selected:
                self._show_placeholder(f"Couldn't read program {number:03d} from the sampler.")
            return
        self._program_data[number] = bytearray(dump.data)
        count = yp.extract(yp.get("program", "assigned_samples"), dump.data)
        if self._counts.get(number) != count:  # an assignment changed it: the list's "has samples" filter must follow
            self._counts[number] = count
            self._apply_visibility(only=number)
        if number == self._selected:
            self._show_program(number)

    def _show_program(self, number):
        data = self._program_data[number]
        self.program_panel.fill(data)
        count = yp.extract(yp.get("program", "assigned_samples"), data)
        self._assigned = []
        for slot in range(count):
            name = yp.extract(yp.get("easy_edit", "assigned_name"), data, slot)
            otype = yp.extract(yp.get("easy_edit", "assigned_type"), data, slot)
            self._assigned.append((name, otype))
        self._range_rows = [None] * count
        self.assigned_list.blockSignals(True)
        self.assigned_list.clear()
        for slot, (name, otype) in enumerate(self._assigned):
            add_keygroup_row(self.assigned_list, slot, 0, NOTE_MAX, self._refresh_swatch)
            self._set_row_text(slot)
        self.assigned_list.blockSignals(False)
        self._update_range_bar()
        self._show_details()
        self.detail_stack.setCurrentIndex(0)
        if count:
            wanted = self._select_sample_after_load
            self._select_sample_after_load = None
            names = [name for name, _otype in self._assigned]
            self.assigned_list.setCurrentRow(names.index(wanted) if wanted in names else 0)
            self.detail_stack.setCurrentIndex(1 if wanted in names else 0)  # a sample just assigned: show ITS page
            self._fetch_sample_ranges(number)
        self._update_assign_buttons()

    def _fetch_sample_ranges(self, number):
        # each assigned sample's own key range comes from the sample's dump - one at a time, only for
        # samples (type 16) we don't already hold
        for slot, (name, otype) in enumerate(self._assigned):
            if otype != ysx.OBJECT_TYPES["sample"] or not name:
                continue
            if name in self._sample_cache:
                self._on_sample_for_range(number, name, self._sample_cache[name])
                continue
            self._session.request_bulk(
                "SP", name, lambda dump, n=number, nm=name: self._on_range_dump(n, nm, dump)
            )

    def _on_range_dump(self, number, name, dump):
        if not self._connected or dump is None:
            return
        self._sample_cache[name] = bytearray(dump.data)
        self._on_sample_for_range(number, name, self._sample_cache[name])

    def _on_sample_for_range(self, number, name, sample_data):
        if number != self._selected:
            return
        program_data = self._program_data[number]
        for slot, (assigned_name, _otype) in enumerate(self._assigned):
            if assigned_name == name:
                self._range_rows[slot] = effective_key_range(sample_data, program_data, slot)
                self._set_row_text(slot)
        self._update_range_bar()
        if self.assigned_list.currentRow() >= 0:
            self._show_assigned(self.assigned_list.currentRow())

    def _set_row_text(self, slot):
        name, otype = self._assigned[slot]
        label = keygroup_row_label(self.assigned_list, slot)
        if label is None:
            return
        rng = self._range_rows[slot]
        if otype != ysx.OBJECT_TYPES["sample"]:
            suffix = "(sample bank)"
        else:
            suffix = format_range(*rng) if rng else "..."
        label.setText(f"{name}: {suffix}" if name else f"(empty slot): {suffix}")

    def _update_range_bar(self):
        self.range_bar.set_ranges([r for r in self._range_rows if r])

    def _refresh_swatch(self, swatch):
        color = keygroup_color(swatch.property("keygroupIndex")).name()
        swatch.setStyleSheet(f"background-color: {color}; border-radius: 2px;")

    def _refresh_themed_swatches(self):
        for label in self.assigned_list.findChildren(QLabel):
            if label.property("swatchKind"):
                self._refresh_swatch(label)

    # -- one assigned sample ---------------------------------------------------------------------------------

    def _show_assigned(self, row):
        number = self._selected
        if number is None or number not in self._program_data or not 0 <= row < len(self._assigned):
            return
        self.easy_panel.fill(self._program_data[number], slot=row)
        self._update_assigned_info(row)

    def _update_assigned_info(self, row):
        name, otype = self._assigned[row]
        self.assigned_name.setText(name or "(empty slot)")
        rng = self._range_rows[row]
        if otype != ysx.OBJECT_TYPES["sample"]:
            info = "A sample bank - its members' ranges aren't shown here."
        elif rng is None:
            info = "Reading the sample's key range..."
        else:
            own_low = yp.extract(yp.get("sample", "key_range_low"), self._sample_cache[name])
            own_high = yp.extract(yp.get("sample", "key_range_high"), self._sample_cache[name])
            own = "original" if own_low < 0 or own_high > NOTE_MAX else format_range(own_low, own_high)
            info = f"Plays {format_range(*rng)} in this program (the sample's own range: {own})"
        self.assigned_info.setText(info)
        self._edit_sample_ok = otype == ysx.OBJECT_TYPES["sample"] and name in self._sample_names
        self._update_edit_sample_button()

    def _update_edit_sample_button(self):
        self.edit_sample_button.setEnabled(self._edit_sample_ok and not self._is_busy())  # (it jumps to another tab)

    def _edit_sample(self):
        row = self.assigned_list.currentRow()
        if 0 <= row < len(self._assigned):
            self.main_tabs.setCurrentIndex(self._samples_tab_index)
            self.samples_tab.select_sample(self._assigned[row][0])

    # -- editing -------------------------------------------------------------------------------------------------

    def _on_program_edited(self, key, value):
        self._edit(yp.get("program", key), value, None)

    def _on_easy_edited(self, key, value):
        row = self.assigned_list.currentRow()
        if row >= 0:
            self._edit(yp.get("easy_edit", key), value, row)

    def _edit(self, row, value, slot):
        number = self._selected
        if number is None or number not in self._program_data:
            return
        name = ysx.program_object_name(number)
        done = lambda result, n=number, r=row, s=slot: self._on_write_done(n, r, s, result)  # noqa: E731
        if not self._writer.edit(row, value, name, slot, done):
            self._show_program_values(number)  # declined the warning: put the widget back
            return
        yp.store(row, self._program_data[number], value, slot)  # shown at once; the read-back confirms it
        self._recompute_ranges()

    def _on_write_done(self, number, row, slot, result):
        if not self._connected:
            return
        data = self._program_data.get(number)
        if data is not None and result.readback is not None:
            yp.store(row, data, result.readback, slot)  # whatever the unit really holds
        if number == self._selected:
            if not result.ok:
                self._show_program_values(number)
            self._recompute_ranges()
        if result.edit_sent:
            self._writer.schedule_reread("PG", ysx.program_object_name(number), lambda d, n=number: self._on_program_reread(n, d))

    def _on_program_reread(self, number, dump):
        if not self._connected or dump is None:
            return
        self._program_data[number] = bytearray(dump.data)
        if number == self._selected:
            self._show_program_values(number)
            self._recompute_ranges()

    def _show_program_values(self, number):
        """Refill both panels from the cached payload (no list rebuild, selection unchanged)."""
        data = self._program_data[number]
        self.program_panel.fill(data)
        row = self.assigned_list.currentRow()
        if 0 <= row < len(self._assigned):
            self.easy_panel.fill(data, slot=row)

    def _recompute_ranges(self):
        """Each assigned sample's effective key range from the cached payloads (they change with edits)."""
        number = self._selected
        if number is None or number not in self._program_data:
            return
        data = self._program_data[number]
        for slot, (name, otype) in enumerate(self._assigned):
            sample = self._sample_cache.get(name)
            if otype == ysx.OBJECT_TYPES["sample"] and sample is not None:
                self._range_rows[slot] = effective_key_range(sample, data, slot)
                self._set_row_text(slot)
        self._update_range_bar()
        row = self.assigned_list.currentRow()
        if 0 <= row < len(self._assigned):
            self._update_assigned_info(row)

    def _write_back_unchanged(self):
        """The first thing to try on a real unit: write Level back to its own value, then require the whole program
        dump to be unchanged (apart from the unit's "edited" flag, which any edit sets)."""
        number = self._selected
        if number is None or number not in self._program_data:
            self.status_bar.showMessage("Select a program first", 5000)
            return
        row = yp.get("program", "program_level")
        name = ysx.program_object_name(number)
        before = bytes(self._program_data[number])
        _log(f"write-back-unchanged test on program {number:03d}")

        def written(result):
            if not result.ok:
                self.status_bar.showMessage(f"Write-back test FAILED: {result.message}", 12000)
                return
            self._session.request_bulk("PG", name, lambda dump: compared(result, dump))

        def compared(result, dump):
            if dump is None:
                self.status_bar.showMessage("Write-back test: the sampler didn't send the program back", 12000)
                return
            differing = [i for i, (a, b) in enumerate(zip(before, bytes(dump.data))) if (a ^ b) & (0xFE if i == 1 else 0xFF)]
            same = not differing and len(before) == len(dump.data)
            _log(f"write-back-unchanged test: {'identical' if same else f'DIFFERENT at {differing[:20]}'}")
            where = f"Backup: {result.backup_path}"
            self.status_bar.showMessage(
                (f"Write-back test passed - program {number:03d} is byte for byte unchanged. " if same
                 else f"Write-back test: program {number:03d} DIFFERS at bytes {differing[:10]}. ") + where,
                15000,
            )
            if self._connected:
                self._program_data[number] = bytearray(dump.data)
                if number == self._selected:
                    self._show_program_values(number)

        self._writer.flush()
        self._session.write_parameter(row, yp.extract(row, before), name, written)

    # -- restoring from a backup ---------------------------------------------------------------------------------

    def _current_object(self):
        """(fmt, name) of what the user is looking at - the selected program or sample - or None."""
        if self.main_tabs.currentIndex() == self._samples_tab_index:
            return ("SP", self.samples_tab._selected) if self.samples_tab._selected else None
        return ("PG", ysx.program_object_name(self._selected)) if self._selected is not None else None

    def _restore_from_backup(self):
        self._writer.flush()
        if self._restoring or self._linking or self._writer.busy:
            self.status_bar.showMessage("Still writing to the sampler - try again in a moment", 5000)
            return
        folder = self._session.backup_dir
        dialog = RestoreDialog(yamaha_backups.list_backups(folder), self._current_object(), str(folder), self)
        if dialog.exec() != RestoreDialog.DialogCode.Accepted or not dialog.chosen_path:
            return
        try:
            backup = yamaha_restore.load_backup(dialog.chosen_path)
        except yamaha_restore.RestoreError as e:
            QMessageBox.warning(self, "Restore from Backup", str(e))
            return
        _log(f"restore: chose {dialog.chosen_path} ({backup.fmt} {backup.name!r})")
        self._set_restoring(True)
        self.status_bar.showMessage("Reading the object from the sampler...")
        job = RestoreJob(self._session, backup)
        job.prepare(lambda plan, message, job=job: self._on_restore_planned(job, plan, message))

    def _on_restore_planned(self, job, plan, message):
        if not self._connected:
            return
        if plan is None:
            self._set_restoring(False)
            QMessageBox.warning(self, "Restore from Backup", message)
            return
        if plan.identical:
            self._set_restoring(False)
            QMessageBox.information(self, "Restore from Backup", f"{plan.name!r} already matches the backup - nothing to restore." + (
                "\n\n" + "\n".join(plan.notes) if plan.notes else ""))
            return
        answer = QMessageBox.question(
            self, "Restore from Backup", describe_restore_plan(plan),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            _log("restore: declined")
            self._set_restoring(False)
            self.status_bar.showMessage("Restore cancelled", 5000)
            return
        _log(f"restore: writing {len(plan.items)} value(s) to {plan.fmt} {plan.name!r}")
        job.run(
            lambda result, job=job: self._on_restore_done(job, result),
            on_progress=lambda done, total, label: self.status_bar.showMessage(f"Restoring {label} ({done + 1}/{total})..."),
        )

    def _on_restore_done(self, job, result):
        if not self._connected:
            return
        _log(f"restore: ok={result.ok} written={result.written} passes={result.passes} - {result.message}")
        self._set_restoring(False)
        self._reload_object(job.backup.fmt, job.backup.name)  # whatever happened, show what the unit holds now
        self.status_bar.showMessage(result.message, 10000)
        text = describe_restore_result(result)
        (QMessageBox.information if result.ok else QMessageBox.warning)(self, "Restore from Backup", text)

    def _set_restoring(self, restoring):
        self._restoring = restoring
        self._apply_lock()

    def _set_linking(self, linking):
        self._linking = linking
        self._apply_lock()

    def _is_busy(self):
        """A restore, an assignment, an audio load or an edit's send is running: the window can't take anything else."""
        return self._restoring or self._linking or self.samples_tab.long_operation

    def _apply_lock(self):
        """While something long runs nothing else may start: the lists, the tab bar, Refresh and the menu actions that start things are off
        (a click would only queue behind the transfer - and swap the cards for a "Reading..." placeholder - so the window would look hung),
        and the controls that write are off. A load or an edit's send can be cancelled from the bottom row."""
        busy = self._is_busy()
        if busy:
            focused = QApplication.focusWidget()  # (see YamahaSamplesTab._drop_focus: a disabled widget's focus scrolls a page)
            if focused is not None and self.isAncestorOf(focused):
                focused.clearFocus()
        for action in (self._restore_action, self._unchanged_action, self._refresh_action, *self._tab_actions):
            action.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        for widget in (
            self.program_list, self.assigned_list, self.show_empty_check, self.samples_tab.sample_list_widget, self.main_tabs.tabBar(),
        ):
            widget.setEnabled(not busy)
        for panel in (self.program_panel, self.easy_panel, self.samples_tab.panel):
            panel.set_editable(not busy)
        self.cancel_button.setVisible(self.samples_tab.long_operation)
        self._update_assign_buttons()
        self._update_edit_sample_button()

    def _cancel_long_operation(self):
        if self.samples_tab.loading:
            self.samples_tab._cancel_load()
        else:
            self._controller.cancel_transfer()  # an edit's / slice export's send

    def _update_assign_buttons(self):
        busy = self._is_busy()
        self.assign_button.setEnabled(not busy and self._selected is not None and self._selected in self._program_data)
        row = self.assigned_list.currentRow()
        self.remove_assigned_button.setEnabled(not busy and 0 <= row < len(self._assigned))

    # -- assigning samples to a program ---------------------------------------------------------------------------

    def _assign_sample(self):
        number = self._selected
        if number is None or self._restoring or self._linking:
            return
        self._writer.flush()
        if self._writer.busy:
            self.status_bar.showMessage("Still writing to the sampler - try again in a moment", 5000)
            return
        taken = {name for name, _otype in self._assigned}
        candidates = [n for n in self._sample_names if n not in taken]
        midi_loaded = self._controller.yamaha_midi_loaded_names()
        dialog = AssignDialog(candidates, f"program {number:03d}", parent=self, midi_loaded=midi_loaded)
        if dialog.exec() != AssignDialog.DialogCode.Accepted or not dialog.chosen:
            return
        if dialog.chosen in midi_loaded and not self._confirm_assign_midi_loaded(dialog.chosen, number):
            return
        _log(f"assign {dialog.chosen!r} to program {number:03d}")
        self._start_link(number, dialog.chosen, True, "sample")

    def _confirm_assign_midi_loaded(self, sample, number):
        """Linking a sample that was loaded over MIDI has left a real A4000 not answering until OK was pressed on its front panel
        (measured in several runs, cause unknown - dev_docs/a4000-editor-roadmap.md, session 6). Nothing is known to make it safe, so
        say so, briefly, before doing it."""
        _log(f"assign of {sample!r} (loaded over MIDI by this app) to program {number:03d}: asking first")
        answer = QMessageBox.question(
            self,
            "Assign Sample",
            f"{sample!r} was sent to the sampler over MIDI by this app.\n\n"
            "You will likely need to press OK (Knob 5) on the sampler if it displays \"MIDI Bulk Received\".\n\n"
            "Assign it anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _remove_assigned(self):
        number, row = self._selected, self.assigned_list.currentRow()
        if number is None or self._restoring or self._linking or not 0 <= row < len(self._assigned):
            return
        self._writer.flush()
        if self._writer.busy:
            self.status_bar.showMessage("Still writing to the sampler - try again in a moment", 5000)
            return
        name, otype = self._assigned[row]
        answer = QMessageBox.question(
            self,
            "Remove Sample",
            f"Remove {name!r} from program {number:03d}?\n\nIts settings for this program (level, pan, key limits, ...) are "
            "discarded, and the samples after it move up one place. The sample itself stays on the sampler.\n\nThe program is "
            "backed up first.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        _log(f"remove {name!r} from program {number:03d}")
        self._start_link(number, name, False, ysx.OBJECT_TYPE_NAMES.get(otype, "sample"))

    def _start_link(self, number, sample, linked, sample_type):
        self._set_linking(True)
        self.status_bar.showMessage(f"{'Assigning' if linked else 'Removing'} {sample!r}...")
        self._select_sample_after_load = sample if linked else None
        self._session.change_link(
            ysx.program_object_name(number), sample, linked,
            lambda result, n=number: self._on_link_done(n, result), sample_type,
        )

    def _on_link_done(self, number, result):
        if not self._connected:
            return
        _log(f"link: ok={result.ok} linked={result.linked} - {result.message}")
        self._set_linking(False)
        self.status_bar.showMessage(result.message, 8000)
        if not result.ok:
            self._select_sample_after_load = None
            QMessageBox.warning(self, "Assign Sample" if result.requested else "Remove Sample", result.message)
        # whatever happened, show what the unit holds now: the program (its slots changed) and the sample ("used in programs")
        self._reload_object("PG", ysx.program_object_name(number))
        self._sample_cache.pop(result.sample, None)
        self.samples_tab.reload_sample(result.sample)

    def _reload_object(self, fmt, name):
        """Forget what is cached for a restored object and read it again, so every control shows what the unit holds."""
        if fmt == "PG" and name.isdigit():
            number = int(name)
            self._program_data.pop(number, None)
            if number == self._selected and self.program_list.currentItem() is not None:
                self._on_program_selected(self.program_list.currentItem(), None)
        elif fmt == "SP":
            self._sample_cache.pop(name, None)
            self.samples_tab.reload_sample(name)

    def _open_backup_folder(self):
        folder = self._session.backup_dir
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # -- status / closing --------------------------------------------------------------------------------------

    def _on_controller_status(self, message):
        self.status_bar.showMessage(message, 8000)

    def closeEvent(self, event):
        # same rule as the other editors: don't hand control back to the Dashboard while a transfer is mid-flight
        if self._controller.is_transfer_busy():
            self.status_bar.showMessage(
                "Can't switch to the Transfer Dashboard - a MIDI operation is already in progress", 5000
            )
            event.ignore()
            return
        self._writer.flush()
        if self._writer.busy or self._restoring or self._linking:
            self.status_bar.showMessage("Still writing to the sampler - try closing again in a moment", 5000)
            event.ignore()
            return
        _log("closed")
        self._writer.close()
        self._scan_generation += 1
        self._session.cancel()
        self._stop_busy_indicator()
        if not self._connected:
            super().closeEvent(event)
            self._main_window.show()
            return
        self._connected = False
        self.samples_tab.disconnect_controller()
        for signal, slot in (
            (self._controller.status_changed, self._on_controller_status),
            (theme.notifier.changed, self._refresh_themed_swatches),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._main_window.show()
        super().closeEvent(event)

    def _stop_busy_indicator(self):
        for timer in (self._busy_show_timer, self._busy_hide_timer):
            timer.stop()
        self._loading_progress.setVisible(False)
        if self._busy_hooked:
            self._busy_hooked = False
            self._session.busy_changed.disconnect(self._on_session_busy_changed)
