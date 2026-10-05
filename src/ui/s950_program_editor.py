"""Akai S900/S950 program editor (Stages 4-5).

A separate, much smaller window than `ProgramEditorWindow` on purpose: that one is built
around `s3k.params` and the S3000's model (mod matrix, 4-stage ENV2, Multi, per-field
reads), none of which the S900/S950 has. Here a program is read whole (RPRGM ->
`core.s950_program.Program`) and written whole (PRGM) through the Dashboard's own
`SamplerController`/`S950Transfers` - the SAME MIDI connection, one operation at a time,
so there is no second bridge, no worker thread and nothing to freeze.

Editing is STAGED, not live: widgets change a working copy, and "Write to Sampler" sends
only the fields that differ from the program as it was loaded (`core.s950_params`),
applied by the engine onto a fresh read of the program, with the original backed up first
and the result read back and compared. See `S950Transfers.write_program`. Nothing here can
create a program, delete one, or change a keygroup count.

Every field's meaning and limits come from s950tools' notes (see `core/s950_program.py`
for how far each offset is trusted and `core/s950_params.py` for the limits); the labels
say "unverified" where even the meaning is inferred, and `control_bits` is read-only.
"""

import copy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import app_config, s950_params
from core.midi_notes import midi_note_to_name
from core.s950_sysex import NAME_LENGTH
from ui import tooltips as tt
from ui.envelope_graph import ADSREnvelopeGraph
from ui.keygroup_range_bar import KeygroupRangeBar
from ui.note_spinbox import NoteSpinBox
from ui.qt_helpers import build_scroll_area, build_section_card

# velocity_switch value meaning "no switch - the soft sample plays at every velocity"
NO_VELOCITY_SWITCH = 128
_MAX_VISIBLE_KEYGROUP_ROWS = 8
# s950tools: "0.375 dB per unit"
_LOUDNESS_DB_PER_UNIT = 0.375

# keygroup control_bits, as dxzl's struct (via s950tools) names them - the bit
# meanings are NOT verified against a unit
_CONTROL_BIT_LABELS = (
    "transpose off",
    "velocity crossfade",
    "vibrato desync",
    "one-shot trigger",
    "velocity release mode",
    "crossfade curve",
)

_RANGE_TIP = "{lo} to {hi} (s950tools' documented range - not verified on a unit)"

_ENVELOPE_ATTRS = {
    "amp": ("attack", "decay", "sustain", "release"),
    "filter": ("filter_attack", "filter_decay", "filter_sustain", "filter_release"),
}


# -- pure formatting (tested without a window) ------------------------------------------------


def format_key_range(lower, upper):
    return f"{midi_note_to_name(lower)} - {midi_note_to_name(upper)}"


def format_velocity_switch(value):
    if value >= NO_VELOCITY_SWITCH:
        return "off (soft sample only)"
    return f"loud sample above velocity {value}"


def format_transpose(raw):
    # 1/16 semitone units (s950tools' note)
    return f"{raw / 16:+.2f} st"


def format_loudness(raw):
    return f"{raw:+d} ({raw * _LOUDNESS_DB_PER_UNIT:+.1f} dB)"


def format_voice_out(value):
    if value == 255:
        return "all outputs"
    if value == 8:
        return "left group"
    if value == 9:
        return "right group"
    return f"output {value}"


def format_control_bits(value):
    active = [label for bit, label in enumerate(_CONTROL_BIT_LABELS) if value & (1 << bit)]
    return ", ".join(active) if active else "none"


def sample_label(name):
    return name.strip() or "-"


def program_row_text(slot, name):
    return f"{slot:>3}   {name.strip() or '(unnamed)'}"


def normalise_name(name):
    return name.strip().upper()


# -- the window ----------------------------------------------------------------------------------


class S950ProgramEditorWindow(QMainWindow):
    _RETRY_MS = 150

    def __init__(self, main_window, sampler_controller):
        super().__init__()
        self._main_window = main_window
        self._controller = sampler_controller
        self.setWindowTitle("AKAISDS - Akai S900/S950 Program Editor")
        self.setMinimumSize(1000, 640)

        self._programs = []  # [(slot, name)]
        self._sample_names = []  # names of the samples on the unit, in catalog order
        self._have_catalog = False
        self._slot = None  # slot of the program being edited
        self._baseline = None  # the program as the sampler holds it (as loaded / last verified)
        self._working = None  # the edited copy
        self._restore = None  # the baseline before the last verified write (for Restore)
        self._kg_row = 0
        self._loading = False  # True while widgets are being filled (their signals are ignored)
        self._writing = False
        self._reverting = False
        self._restoring = False  # the write in flight is a "Restore Previous"
        self._pending_restore = None  # baseline to offer for Restore once the write verifies
        # reading is one request at a time; the user can click faster than the
        # unit answers, so only the LATEST wanted slot is ever read next
        self._wanted_slot = None
        self._inflight_slot = None
        self._shown_slot = None  # last slot answered (success OR failure) - stops re-requesting
        self._editors = {}  # (scope, attr) -> widget
        self._readonly = {}  # attr -> QLabel

        self._build_ui()
        self._build_menus()

        c = self._controller
        self._connected = True
        c.program_slots_updated.connect(self._on_programs_listed)
        c.sample_slots_updated.connect(self._on_samples_listed)
        c.s950_program_received.connect(self._on_program_received)
        c.s950_program_written.connect(self._on_program_written)
        c.status_changed.connect(self._on_controller_status)

        self._busy_timer = QTimer(self)
        self._busy_timer.timeout.connect(self._update_buttons)
        self._busy_timer.start(150)

        self._show_placeholder("Select a program on the left.")
        self._update_buttons()
        QTimer.singleShot(0, self._refresh)

    # -- construction -------------------------------------------------------------------

    def _build_ui(self):
        self.program_list = QListWidget()
        self.program_list.setObjectName("programList")
        self.program_list.currentItemChanged.connect(self._on_program_selected)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Re-read the program and sample lists from the sampler")
        self.refresh_button.clicked.connect(self._refresh)

        left = QVBoxLayout()
        left.addWidget(QLabel("Programs"))
        left.addWidget(self.program_list, 1)
        left.addWidget(self.refresh_button)
        left_widget = QWidget()
        left_widget.setLayout(left)

        # right-hand side: a placeholder OR the detail pages + the write bar
        self.placeholder = QLabel()
        self.placeholder.setObjectName("emptyQueueLabel")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)

        self.detail_page = self._build_detail_page()
        self.detail_scroll = build_scroll_area(self.detail_page)

        self.dirty_label = QLabel()
        self.dirty_label.setObjectName("mutedLabel")
        self.discard_button = QPushButton("Discard Changes")
        self.discard_button.clicked.connect(self._discard)
        self.restore_button = QPushButton("Restore Previous")
        self.restore_button.setToolTip(
            "Write back what this program held before the last write (this session only - "
            "the original is also saved as a .syx file in ~/.akaisds/s950_backups)"
        )
        self.restore_button.clicked.connect(self._restore_previous)
        self.write_button = QPushButton("Write to Sampler")
        self.write_button.setDefault(True)
        self.write_button.clicked.connect(self._write)
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 0, 12, 8)
        bar.addWidget(self.dirty_label)
        bar.addStretch()
        bar.addWidget(self.discard_button)
        bar.addWidget(self.restore_button)
        bar.addWidget(self.write_button)
        self.write_bar = QWidget()
        self.write_bar.setLayout(bar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.placeholder, 1)
        right.addWidget(self.detail_scroll, 1)
        right.addWidget(self.write_bar)
        right_widget = QWidget()
        right_widget.setLayout(right)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 740])
        self.setCentralWidget(splitter)

        self.status_bar = QStatusBar()
        self.status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self.status_bar)
        experimental = QLabel("Experimental - writes are untested on real hardware")
        experimental.setObjectName("mutedLabel")
        self.status_bar.addPermanentWidget(experimental)

    def _editor(self, scope, attr):
        """The input widget for one field, built from what the field is."""
        if attr == "name":
            w = QLineEdit()
            w.setMaxLength(NAME_LENGTH)
            w.editingFinished.connect(lambda w=w: self._on_name_finished(scope, attr, w))
        elif attr in ("soft_sample", "loud_sample"):
            w = QComboBox()
            w.activated.connect(lambda _i, w=w: self._on_edit(scope, attr, w.currentData()))
        elif attr == "voice_out":
            w = QComboBox()
            for value in s950_params.VOICE_OUT_VALUES:
                w.addItem(format_voice_out(value).capitalize(), value)
            w.activated.connect(lambda _i, w=w: self._on_edit(scope, attr, w.currentData()))
        elif attr in ("positional_xfade", "enable_midi_program"):
            w = QCheckBox()
            w.toggled.connect(lambda v: self._on_edit(scope, attr, bool(v)))
        elif attr in ("lower_key", "upper_key"):
            lo, hi = s950_params.KEYGROUP_RANGES[attr]
            w = NoteSpinBox()
            w.setRange(lo, hi)
            w.setToolTip(_RANGE_TIP.format(lo=midi_note_to_name(lo), hi=midi_note_to_name(hi)))
            w.valueChanged.connect(lambda v: self._on_edit(scope, attr, int(v)))
        elif attr.endswith("_transpose"):
            limit = s950_params.TRANSPOSE_LIMIT_ST
            w = QDoubleSpinBox()
            w.setDecimals(4)
            w.setSingleStep(1 / 16)
            w.setRange(-limit, limit)
            w.setSuffix(" st")
            w.setToolTip(
                f"1/16-semitone steps; limited to +-{limit} st here (our limit, not the hardware's)"
            )
            # raw units are 1/16 semitone
            w.valueChanged.connect(lambda v: self._on_edit(scope, attr, round(v * 16)))
        else:
            lo, hi = (
                s950_params.PROGRAM_RANGES if scope == "program" else s950_params.KEYGROUP_RANGES
            )[attr]
            w = QSpinBox()
            w.setRange(lo, hi)
            w.setToolTip(_RANGE_TIP.format(lo=lo, hi=hi))
            if lo < 0:
                w.setPrefix("")
            w.valueChanged.connect(lambda v: self._on_edit(scope, attr, int(v)))
        self._editors[(scope, attr)] = w
        return w

    def _field_grid(self, scope, rows):
        # rows: [(attr, label text)] -> a label/input grid
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        for r, (attr, text) in enumerate(rows):
            label = QLabel(text)
            label.setObjectName("mutedLabel")
            if attr == "control_bits":
                widget = QLabel("-")  # read-only on purpose: the bit meanings are inferred
                widget.setToolTip("Shown, never written: what the bits mean is not verified")
                self._readonly[attr] = widget
            else:
                widget = self._editor(scope, attr)
            grid.addWidget(label, r, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(widget, r, 1)
        grid.setColumnStretch(1, 1)
        return grid

    def _build_detail_page(self):
        def card(title, scope, rows, extra=None):
            grid = self._field_grid(scope, rows)
            layouts = [grid] if extra is None else [extra, grid]
            return build_section_card(title, *layouts)

        self.program_card = card(
            "Program",
            "program",
            [
                ("name", "Name"),
                ("midi_program_number", "MIDI program"),
                ("enable_midi_program", "Respond to program change"),
                ("key_tilt", "Key tilt (loudness by key)"),
                ("positional_xfade", "Positional crossfade"),
            ],
        )
        self.keygroup_count_label = QLabel()
        self.keygroup_count_label.setObjectName("mutedLabel")

        self.range_bar = KeygroupRangeBar()
        self.keygroup_table = QTableWidget(0, 5)
        self.keygroup_table.setHorizontalHeaderLabels(
            ["#", "Range", "Soft sample", "Loud sample", "Velocity switch"]
        )
        self.keygroup_table.verticalHeader().setVisible(False)
        self.keygroup_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.keygroup_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.keygroup_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.keygroup_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.keygroup_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.keygroup_table.currentCellChanged.connect(
            lambda row, _col, _prow, _pcol: self._show_keygroup(row)
        )
        kg_layout = QVBoxLayout()
        kg_layout.setSpacing(6)
        kg_layout.addWidget(self.keygroup_count_label)
        kg_layout.addWidget(self.range_bar)
        kg_layout.addWidget(self.keygroup_table)
        self.keygroups_card = build_section_card("Keygroups", kg_layout)

        self.zone_card = card(
            "Zone",
            "kg",
            [
                ("lower_key", "Lowest key"),
                ("upper_key", "Highest key"),
                ("velocity_switch", f"Velocity switch ({NO_VELOCITY_SWITCH} = off)"),
                ("voice_out", "Output"),
                ("midi_offset", "MIDI channel offset"),
                ("control_bits", "Options (read-only)"),
            ],
        )
        self.soft_card = card(
            "Soft sample",
            "kg",
            [
                ("soft_sample", "Sample"),
                ("soft_transpose", "Transpose"),
                ("soft_filter", "Filter"),
                ("soft_loudness", "Loudness"),
            ],
        )
        self.loud_card = card(
            "Loud sample",
            "kg",
            [
                ("loud_sample", "Sample"),
                ("loud_transpose", "Transpose"),
                ("loud_filter", "Filter"),
                ("loud_loudness", "Loudness"),
            ],
        )

        self.amp_graph = ADSREnvelopeGraph()
        self.amp_graph.setFixedHeight(90)
        self.amp_card = card(
            "Amplitude envelope",
            "kg",
            [
                ("attack", "Attack"),
                ("decay", "Decay"),
                ("sustain", "Sustain"),
                ("release", "Release"),
                ("attack_vel", "Attack by velocity"),
                ("release_vel", "Release by velocity"),
                ("loudness_vel", "Loudness by velocity"),
            ],
            extra=self._wrap(self.amp_graph),
        )
        self.filter_graph = ADSREnvelopeGraph()
        self.filter_graph.setFixedHeight(90)
        self.filter_card = card(
            "Filter envelope",
            "kg",
            [
                ("filter_attack", "Attack"),
                ("filter_decay", "Decay"),
                ("filter_sustain", "Sustain"),
                ("filter_release", "Release"),
                ("adsr_to_vcf", "Envelope to filter"),
                ("filter_vel", "Filter by velocity"),
                ("filter_key_track", "Filter key tracking"),
            ],
            extra=self._wrap(self.filter_graph),
        )
        self.lfo_card = card(
            "LFO and controllers",
            "kg",
            [
                ("lfo_rate", "LFO rate"),
                ("lfo_depth", "LFO depth"),
                ("lfo_build", "LFO build-up"),
                ("aftertouch_depth", "Aftertouch to LFO depth"),
                ("modwheel_depth", "Mod wheel to LFO depth"),
            ],
        )
        self.pitch_card = card(
            "Pitch warp",
            "kg",
            [
                ("pitch_warp_vel", "Warp by velocity"),
                ("pitch_warp_offset", "Warp offset"),
                ("pitch_warp_recovery", "Recovery"),
            ],
        )

        self.keygroup_title = QLabel()
        self.keygroup_title.setObjectName("sectionHeader")

        def row(*cards):
            layout = QHBoxLayout()
            layout.setSpacing(12)
            for c in cards:
                layout.addWidget(c, 1, Qt.AlignmentFlag.AlignTop)
            return layout

        page_layout = QVBoxLayout()
        page_layout.setContentsMargins(12, 12, 12, 12)
        page_layout.setSpacing(12)
        page_layout.addWidget(self.program_card)
        page_layout.addWidget(self.keygroups_card)
        page_layout.addWidget(self.keygroup_title)
        page_layout.addLayout(row(self.zone_card, self.soft_card, self.loud_card))
        page_layout.addLayout(row(self.amp_card, self.filter_card))
        page_layout.addLayout(row(self.lfo_card, self.pitch_card))
        page_layout.addStretch()
        page = QWidget()
        page.setLayout(page_layout)
        return page

    @staticmethod
    def _wrap(widget):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(widget)
        return layout

    def _build_menus(self):
        hardware_menu = self.menuBar().addMenu("&Hardware")
        self._refresh_action = QAction("Refresh from Hardware", self)
        self._refresh_action.setShortcut("Ctrl+R")
        self._refresh_action.triggered.connect(self._refresh)
        hardware_menu.addAction(self._refresh_action)
        hardware_menu.addSeparator()
        self._write_action = QAction("Write to Sampler", self)
        self._write_action.setShortcut("Ctrl+S")
        self._write_action.triggered.connect(self._write)
        hardware_menu.addAction(self._write_action)
        # the safest possible first write: send the program back exactly as it is
        self._write_unchanged_action = QAction("Write Program Back Unchanged (test)", self)
        self._write_unchanged_action.setToolTip(
            "Checks the write path without changing anything: the program is sent back as "
            "read, then read again and compared"
        )
        self._write_unchanged_action.triggered.connect(self._write_unchanged)
        hardware_menu.addAction(self._write_unchanged_action)

        # mirrors the Dashboard's &Window menu (main_window.py) so Ctrl+T/Ctrl+E
        # work from either window
        window_menu = self.menuBar().addMenu("&Window")
        self._dashboard_action = QAction("Transfer Dashboard", self)
        self._dashboard_action.setShortcut("Ctrl+T")
        # closing reuses closeEvent's "show the Dashboard again" + busy guard
        self._dashboard_action.triggered.connect(self.close)
        window_menu.addAction(self._dashboard_action)
        editor_action = QAction("Program Editor", self)
        editor_action.setShortcut("Ctrl+E")
        editor_action.setEnabled(False)  # this window IS it
        window_menu.addAction(editor_action)

    # -- state ----------------------------------------------------------------------------------

    def _changes(self):
        if self._baseline is None or self._working is None:
            return []
        try:
            return s950_params.diff_programs(self._baseline, self._working)
        except ValueError:
            return []

    def is_dirty(self):
        return bool(self._changes())

    def _update_buttons(self):
        idle = self._controller.is_s950_idle() and not self._writing
        busy = self._controller.is_transfer_busy()
        loaded = self._working is not None and self._slot is not None
        changes = self._changes() if loaded else []
        self.refresh_button.setEnabled(idle)
        self._refresh_action.setEnabled(idle)
        self.write_button.setEnabled(loaded and idle and bool(changes))
        self._write_action.setEnabled(loaded and idle and bool(changes))
        self._write_unchanged_action.setEnabled(loaded and idle and not changes)
        self.discard_button.setEnabled(loaded and not self._writing and bool(changes))
        self.restore_button.setEnabled(idle and self._restore is not None)
        self.write_bar.setVisible(loaded)
        n = len(changes)
        self.dirty_label.setText(f"{n} unsaved change{'' if n == 1 else 's'}" if n else "")
        self.detail_page.setEnabled(not self._writing)
        self._dashboard_action.setEnabled(not busy and not self._writing)
        self._dashboard_action.setToolTip(tt.BUSY_BLOCKS_OTHER_WINDOWS if busy else "")

    def _confirm_discard(self):
        if not self.is_dirty():
            return True
        n = len(self._changes())
        answer = QMessageBox.question(
            self,
            "Discard changes?",
            f"This program has {n} change{'' if n == 1 else 's'} that haven't been written "
            "to the sampler. Discard them?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    # -- loading ------------------------------------------------------------------------------

    def _show_placeholder(self, text):
        self.placeholder.setText(text)
        self.placeholder.setVisible(True)
        self.detail_scroll.setVisible(False)
        self.write_bar.setVisible(False)

    def _show_details(self):
        self.placeholder.setVisible(False)
        self.detail_scroll.setVisible(True)

    def _refresh(self):
        # re-read the catalog, then the program on screen (a stale one is the
        # likeliest thing the user is refreshing for)
        if not self._controller.is_s950_idle() or self._writing:
            self.status_bar.showMessage(
                "A MIDI operation is already in progress - try again in a moment", 5000
            )
            return
        if not self._confirm_discard():
            return
        self._shown_slot = None
        if self._slot is not None:
            self._wanted_slot = self._slot
        self._controller.refresh_sample_list(silent=True)
        self.status_bar.showMessage("Reading the program list...", 0)

    def _on_programs_listed(self, entries):
        self._have_catalog = True
        self._programs = list(entries)
        keep = self._wanted_slot if self._wanted_slot is not None else self._slot
        self.program_list.blockSignals(True)
        self.program_list.clear()
        select_row = -1
        for row, (slot, name) in enumerate(self._programs):
            # no setItemWidget here, so plain item text is right (AGENTS.md: the
            # double-paint gotcha is only when BOTH exist)
            item = QListWidgetItem(program_row_text(slot, name))
            item.setData(Qt.ItemDataRole.UserRole, slot)
            self.program_list.addItem(item)
            if slot == keep:
                select_row = row
        if select_row >= 0:
            self.program_list.setCurrentRow(select_row)
        self.program_list.blockSignals(False)
        n = len(self._programs)
        if not self._programs:
            self._show_placeholder("The sampler reports no programs.")
            self.status_bar.showMessage("No programs on the sampler", 0)
        else:
            self.status_bar.showMessage(f"{n} program{'' if n == 1 else 's'} on the sampler", 0)
            if select_row < 0:
                self._show_placeholder("Select a program on the left.")
            else:
                self._pump()
                self._refresh_sample_widgets()

    def _on_samples_listed(self, entries):
        self._sample_names = [name for _slot, name in entries]
        self._refresh_sample_widgets()

    def _on_program_selected(self, current, previous):
        if current is None or self._reverting:
            return
        slot = current.data(Qt.ItemDataRole.UserRole)
        if slot != self._slot and not self._confirm_discard():
            # the user chose to keep editing: put the selection back
            self._reverting = True
            self.program_list.setCurrentItem(previous)
            self._reverting = False
            return
        self._wanted_slot = slot
        self._pump()

    def _pump(self):
        slot = self._wanted_slot
        if slot is None or slot == self._shown_slot or self._inflight_slot is not None:
            return
        if not self._controller.is_s950_idle():
            # the catalog read (or a Dashboard operation) is still on the wire
            QTimer.singleShot(self._RETRY_MS, self._pump)
            return
        self._inflight_slot = slot
        # whatever was on screen has been confirmed discarded (or was clean) - drop it so
        # nothing can be written to the OLD program while the new one loads
        self._slot, self._baseline, self._working, self._restore = None, None, None, None
        self._show_placeholder(f"Reading program {slot}...")
        self._update_buttons()
        self._controller.request_program(slot)

    def _on_program_received(self, slot, program):
        if slot != self._inflight_slot:
            return  # not a read this window asked for
        self._inflight_slot = None
        self._shown_slot = slot
        if self._wanted_slot is not None and self._wanted_slot != slot:
            # the user moved on while this was in flight
            self._shown_slot = None
            self._pump()
            return
        if program is None:
            self._slot, self._baseline, self._working, self._restore = slot, None, None, None
            self._show_placeholder(
                f"Couldn't read program {slot}.\n\nCheck the status bar, then press "
                "Refresh to try again. ~/.akaisds/akaisds.log has the details."
            )
            self._update_buttons()
            return
        self._slot, self._restore = slot, None
        self._load(program)

    def _on_controller_status(self, message):
        # the engine reports progress and why a read/write failed through status_changed
        self.status_bar.showMessage(message, 8000)

    def _load(self, program):
        # `program` is what the sampler holds: it becomes both the baseline and a fresh
        # working copy to edit
        self._baseline = program
        self._working = copy.deepcopy(program)
        self._kg_row = 0
        self._show_details()
        self._fill_program_widgets()
        self._fill_keygroup_table()
        self._show_keygroup(0)
        self._update_buttons()

    # -- display ------------------------------------------------------------------------------

    def _set_widget(self, widget, value):
        # set an input without it counting as an edit; widen a range so a value the unit
        # already holds is shown truthfully rather than clamped
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            if value < widget.minimum():
                widget.setMinimum(value)
            if value > widget.maximum():
                widget.setMaximum(value)
            widget.setValue(value)
        elif isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QLineEdit):
            widget.setText(value)
        elif isinstance(widget, QComboBox):
            index = widget.findData(value)
            if index < 0:
                widget.addItem(f"{value} (unexpected)", value)
                index = widget.findData(value)
            widget.setCurrentIndex(index)

    def _fill_program_widgets(self):
        self._loading = True
        try:
            for attr in s950_params.PROGRAM_FIELDS:
                self._set_widget(self._editors[("program", attr)], getattr(self._working, attr))
        finally:
            self._loading = False

    def _fill_keygroup_table(self):
        program = self._working
        self.keygroup_count_label.setText(
            f"{program.num_keygroups} keygroup{'' if program.num_keygroups == 1 else 's'} "
            "(adding or removing keygroups isn't supported)"
        )
        table = self.keygroup_table
        table.blockSignals(True)
        table.setRowCount(program.num_keygroups)
        for row in range(program.num_keygroups):
            self._fill_table_row(row)
        table.blockSignals(False)
        # sized to its rows (up to a cap, then it scrolls) so a 2-keygroup program
        # doesn't leave a tall empty box
        rows = min(program.num_keygroups, _MAX_VISIBLE_KEYGROUP_ROWS)
        table.setFixedHeight(
            table.horizontalHeader().height()
            + rows * table.verticalHeader().defaultSectionSize()
            + 4
        )
        self.range_bar.set_ranges([(kg.lower_key, kg.upper_key) for kg in program.keygroups])
        table.selectRow(0)

    def _fill_table_row(self, row):
        kg = self._working.keygroups[row]
        cells = (
            str(row + 1),
            format_key_range(kg.lower_key, kg.upper_key),
            sample_label(kg.soft_sample) + self._missing_suffix(kg.soft_sample, "  (not on sampler)"),
            sample_label(kg.loud_sample) + self._missing_suffix(kg.loud_sample, "  (not on sampler)"),
            format_velocity_switch(kg.velocity_switch),
        )
        for col, text in enumerate(cells):
            self.keygroup_table.setItem(row, col, QTableWidgetItem(text))

    def _show_keygroup(self, row):
        program = self._working
        if program is None or not 0 <= row < program.num_keygroups:
            return
        self._kg_row = row
        kg = program.keygroups[row]
        self.keygroup_title.setText(f"Keygroup {row + 1} of {program.num_keygroups}")
        self._loading = True
        try:
            for attr in s950_params.KEYGROUP_FIELDS:
                if attr in ("soft_sample", "loud_sample"):
                    self._fill_sample_combo(attr, getattr(kg, attr))
                elif attr.endswith("_transpose"):
                    self._set_widget(self._editors[("kg", attr)], getattr(kg, attr) / 16)
                else:
                    self._set_widget(self._editors[("kg", attr)], getattr(kg, attr))
        finally:
            self._loading = False
        self._readonly["control_bits"].setText(format_control_bits(kg.control_bits))
        self._refresh_graphs(kg)

    def _fill_sample_combo(self, attr, current):
        combo = self._editors[("kg", attr)]
        combo.clear()
        combo.addItem("(none)", "")
        seen = {""}
        for name in self._sample_names:
            key = normalise_name(name)
            if key and key not in seen:
                seen.add(key)
                combo.addItem(name, name)
        if normalise_name(current) not in seen:
            # a name the unit's catalog doesn't have - show it (and flag it) rather than lose it
            combo.addItem(f"{current}  (not on sampler)", current)
        index = next(
            (i for i in range(combo.count()) if normalise_name(combo.itemData(i)) == normalise_name(current)),
            0,
        )
        combo.setCurrentIndex(index)

    def _refresh_graphs(self, kg):
        self.amp_graph.set_values(kg.attack, kg.decay, kg.sustain, kg.release)
        self.filter_graph.set_values(
            kg.filter_attack, kg.filter_decay, kg.filter_sustain, kg.filter_release
        )

    def _refresh_sample_widgets(self):
        # the catalog changed: the sample pickers and the "not on sampler" flags follow it
        if self._working is None:
            return
        for row in range(self._working.num_keygroups):
            self._fill_table_row(row)
        self._loading = True
        try:
            kg = self._working.keygroups[min(self._kg_row, self._working.num_keygroups - 1)]
            self._fill_sample_combo("soft_sample", kg.soft_sample)
            self._fill_sample_combo("loud_sample", kg.loud_sample)
        finally:
            self._loading = False

    def _missing_suffix(self, name, text):
        # programs find samples BY NAME; a name the unit's catalog doesn't have is the most
        # useful thing the editor can point out. An empty name is "no sample" (normal for an
        # unused loud sample), and without a catalog there's nothing to compare against.
        known = {normalise_name(n) for n in self._sample_names}
        missing = bool(name.strip()) and self._have_catalog and normalise_name(name) not in known
        return text if missing else ""

    # -- editing --------------------------------------------------------------------------------

    def _on_name_finished(self, scope, attr, widget):
        if self._loading:
            return
        name = widget.text().strip().upper()
        if name != widget.text():
            widget.setText(name)
        self._on_edit(scope, attr, name)

    def _on_edit(self, scope, attr, value):
        if self._loading or self._working is None:
            return
        if scope == "program":
            setattr(self._working, attr, value)
        else:
            kg = self._working.keygroups[self._kg_row]
            setattr(kg, attr, value)
            self._fill_table_row(self._kg_row)
            if attr in ("lower_key", "upper_key"):
                self.range_bar.set_ranges(
                    [(k.lower_key, k.upper_key) for k in self._working.keygroups]
                )
            if attr in _ENVELOPE_ATTRS["amp"] + _ENVELOPE_ATTRS["filter"]:
                self._refresh_graphs(kg)
        self._update_buttons()

    def _discard(self):
        if self._baseline is None or not self.is_dirty():
            return
        self._load(self._baseline)
        self.status_bar.showMessage("Changes discarded", 5000)

    # -- writing --------------------------------------------------------------------------------

    def _write_unchanged(self):
        self._send_write(self._baseline, copy.deepcopy(self._baseline), confirm=True)

    def _write(self):
        self._send_write(self._baseline, self._working)

    def _restore_previous(self):
        if self._restore is None or self._baseline is None:
            return
        answer = QMessageBox.question(
            self,
            "Restore previous values?",
            "Write this program back to what it held before the last write?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return
        self._send_write(self._baseline, copy.deepcopy(self._restore), restoring=True)

    def _send_write(self, baseline, edited, *, confirm=False, restoring=False):
        if baseline is None or edited is None or self._slot is None or self._writing:
            return
        if not self._controller.is_s950_idle():
            self.status_bar.showMessage(
                "A MIDI operation is already in progress - try again in a moment", 5000
            )
            return
        try:
            changes = s950_params.diff_programs(baseline, edited)
        except ValueError as e:
            QMessageBox.warning(self, "Can't write", str(e))
            return
        problems = s950_params.program_problems(edited, changes)
        problems += self._name_clash_problems(edited)
        if problems:
            QMessageBox.warning(
                self, "Can't write this program", "Nothing was written:\n\n" + "\n".join(problems)
            )
            return
        if not self._confirm_first_write():
            return
        self._pending_restore = None if restoring else baseline
        self._restoring = restoring
        self._writing = True
        self._update_buttons()
        n = len(changes)
        self.status_bar.showMessage(
            f"Writing program {self._slot} ({n} change{'' if n == 1 else 's'})...", 0
        )
        self._controller.write_program(self._slot, baseline, edited)

    def _name_clash_problems(self, program):
        # whether two programs with one name is harmful on an S900/S950 is unknown, so don't
        # make one (the S1000 deletes the other, which is why the same rule exists there)
        clash = [
            slot
            for slot, name in self._programs
            if slot != self._slot and normalise_name(name) == normalise_name(program.name)
        ]
        if program.name.strip() and clash:
            return [f"Another program (slot {clash[0]}) is already called '{program.name.strip()}'."]
        return []

    def _confirm_first_write(self):
        if app_config.get_s950_program_write_warning_acknowledged():
            return True
        answer = QMessageBox.warning(
            self,
            "Akai S900/S950 - experimental",
            "Writing programs to the Akai S900/S950 is experimental.\n\n"
            "It was written from s950tools' documentation without an S900/S950 to test "
            "against. Before every write the program's original is saved to "
            "~/.akaisds/s950_backups as a .syx file, and afterwards it is read back and "
            "compared.\n\n"
            "Try a scratch program first, and please report problems along with "
            "~/.akaisds/akaisds.log.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return False
        app_config.save_s950_program_write_warning_acknowledged(True)
        return True

    def _on_program_written(self, slot, verified, held, message):
        if not self._writing:
            return
        self._writing = False
        restoring, previous = self._restoring, self._pending_restore
        self._pending_restore = None
        self._restoring = False
        if slot != self._slot:
            self._update_buttons()
            return
        if held is not None:
            # what the sampler holds now is the truth - show it (and make it the baseline),
            # whether or not it is what we sent
            self._load(held)
            if restoring:
                self._restore = None
            elif previous is not None:
                self._restore = previous
            self._update_buttons()
        else:
            self._update_buttons()
        self.status_bar.showMessage(message, 15000)
        if verified:
            # a renamed program changes the list
            QTimer.singleShot(50, lambda: self._controller.refresh_sample_list(silent=True))
        else:
            QMessageBox.warning(
                self,
                "Program not written cleanly",
                message
                + "\n\nYour edits are still on screen"
                + (
                    "; the program shown is what the sampler reports now."
                    if held is not None
                    else "."
                ),
            )

    # -- closing --------------------------------------------------------------------------------

    def closeEvent(self, event):
        # same rule as ProgramEditorWindow: don't hand control back to the Dashboard
        # while a MIDI operation is still in flight on the shared connection
        if self._controller.is_transfer_busy() or self._writing:
            self.status_bar.showMessage(
                "Can't switch to the Transfer Dashboard - a MIDI operation is already "
                "in progress",
                5000,
            )
            event.ignore()
            return
        if not self._confirm_discard():
            event.ignore()
            return
        self._busy_timer.stop()
        if not self._connected:
            super().closeEvent(event)
            self._main_window.show()
            return
        self._connected = False
        c = self._controller
        for signal, slot in (
            (c.program_slots_updated, self._on_programs_listed),
            (c.sample_slots_updated, self._on_samples_listed),
            (c.s950_program_received, self._on_program_received),
            (c.s950_program_written, self._on_program_written),
            (c.status_changed, self._on_controller_status),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._main_window.show()
        super().closeEvent(event)
