"""Akai S900/S950 program viewer - read-only (Stage 4).

A separate, much smaller window than `ProgramEditorWindow` on purpose: that one is
built around `s3k.params` and the S3000's model (mod matrix, 4-stage ENV2, Multi,
per-field reads), none of which the S900/S950 has. Here a program is read whole
(RPRGM -> `core.s950_program.Program`) through the Dashboard's own
`SamplerController`/`S950Transfers` - the SAME MIDI connection, one operation at a
time, so there is no second bridge, no worker thread and nothing to freeze.

Nothing is written to the unit. Every field's meaning comes from s950tools' notes
(see `core/s950_program.py` for how far each offset is trusted); the labels say
"unverified" where even the meaning is inferred.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.midi_notes import midi_note_to_name
from ui import tooltips as tt
from ui.envelope_graph import ADSREnvelopeGraph
from ui.keygroup_range_bar import KeygroupRangeBar
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


class S950ProgramViewerWindow(QMainWindow):
    _RETRY_MS = 150

    def __init__(self, main_window, sampler_controller):
        super().__init__()
        self._main_window = main_window
        self._controller = sampler_controller
        self.setWindowTitle("AKAISDS - Akai S900/S950 Program Viewer")
        self.setMinimumSize(1000, 640)

        self._programs = []  # [(slot, name)]
        self._sample_names = set()  # normalised names of the samples on the unit
        self._have_catalog = False
        self._program = None  # the Program being shown
        self._program_slot = None
        # reading is one request at a time; the user can click faster than the
        # unit answers, so only the LATEST wanted slot is ever read next
        self._wanted_slot = None
        self._inflight_slot = None
        self._shown_slot = None  # last slot answered (success OR failure) - stops re-requesting

        self._build_ui()
        self._build_menus()

        c = self._controller
        self._connected = True
        c.program_slots_updated.connect(self._on_programs_listed)
        c.sample_slots_updated.connect(self._on_samples_listed)
        c.s950_program_received.connect(self._on_program_received)
        c.status_changed.connect(self._on_controller_status)

        self._busy_timer = QTimer(self)
        self._busy_timer.timeout.connect(self._sync_busy_state)
        self._busy_timer.start(150)

        self._show_placeholder("Select a program on the left.")
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

        # right-hand side: a placeholder OR the detail pages
        self.placeholder = QLabel()
        self.placeholder.setObjectName("emptyQueueLabel")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)

        self.detail_page = self._build_detail_page()
        self.detail_scroll = build_scroll_area(self.detail_page)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.placeholder, 1)
        right.addWidget(self.detail_scroll, 1)
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
        read_only = QLabel("Read-only - nothing is written to the sampler")
        read_only.setObjectName("mutedLabel")
        self.status_bar.addPermanentWidget(read_only)

    def _value_grid(self, rows):
        # rows: [(key, label text)] -> (layout, {key: value QLabel})
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        values = {}
        for r, (key, text) in enumerate(rows):
            label = QLabel(text)
            label.setObjectName("mutedLabel")
            value = QLabel("-")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(label, r, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, r, 1)
            values[key] = value
        grid.setColumnStretch(1, 1)
        return grid, values

    def _build_detail_page(self):
        self._values = {}

        def card(title, rows, extra=None):
            grid, values = self._value_grid(rows)
            self._values.update(values)
            layouts = [grid] if extra is None else [extra, grid]
            return build_section_card(title, *layouts)

        # program
        self.program_card = card(
            "Program",
            [
                ("name", "Name"),
                ("keygroups", "Keygroups"),
                ("midi_program", "MIDI program"),
                ("key_tilt", "Key tilt (loudness by key)"),
                ("positional_xfade", "Positional crossfade"),
            ],
        )

        # keygroup list + range bar
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
        kg_layout.addWidget(self.range_bar)
        kg_layout.addWidget(self.keygroup_table)
        self.keygroups_card = build_section_card("Keygroups", kg_layout)

        # selected keygroup
        self.zone_card = card(
            "Zone",
            [
                ("range", "Key range"),
                ("velocity_switch", "Velocity switch"),
                ("voice_out", "Output"),
                ("midi_offset", "MIDI channel offset"),
                ("control_bits", "Options (unverified)"),
            ],
        )
        self.soft_card = card(
            "Soft sample",
            [
                ("soft_sample", "Sample"),
                ("soft_transpose", "Transpose"),
                ("soft_filter", "Filter"),
                ("soft_loudness", "Loudness"),
            ],
        )
        self.loud_card = card(
            "Loud sample",
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

        # mirrors the Dashboard's &Window menu (main_window.py) so Ctrl+T/Ctrl+E
        # work from either window
        window_menu = self.menuBar().addMenu("&Window")
        self._dashboard_action = QAction("Transfer Dashboard", self)
        self._dashboard_action.setShortcut("Ctrl+T")
        # closing reuses closeEvent's "show the Dashboard again" + busy guard
        self._dashboard_action.triggered.connect(self.close)
        window_menu.addAction(self._dashboard_action)
        viewer_action = QAction("Program Editor", self)
        viewer_action.setShortcut("Ctrl+E")
        viewer_action.setEnabled(False)  # this window IS it
        window_menu.addAction(viewer_action)

    # -- loading ------------------------------------------------------------------------------

    def _show_placeholder(self, text):
        self.placeholder.setText(text)
        self.placeholder.setVisible(True)
        self.detail_scroll.setVisible(False)

    def _show_details(self):
        self.placeholder.setVisible(False)
        self.detail_scroll.setVisible(True)

    def _refresh(self):
        # re-read the catalog, then the program on screen (a stale one is the
        # likeliest thing the user is refreshing for)
        if not self._controller.is_s950_idle():
            self.status_bar.showMessage(
                "A MIDI operation is already in progress - try again in a moment", 5000
            )
            return
        self._shown_slot = None
        if self._program_slot is not None:
            self._wanted_slot = self._program_slot
        self._controller.refresh_sample_list(silent=True)
        self.status_bar.showMessage("Reading the program list...", 0)

    def _on_programs_listed(self, entries):
        self._have_catalog = True
        self._programs = list(entries)
        keep = self._wanted_slot if self._wanted_slot is not None else self._program_slot
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
                # a reload may have replaced the sample set the warning uses
                self._refresh_missing_sample_flags()

    def _on_samples_listed(self, entries):
        self._sample_names = {normalise_name(name) for _slot, name in entries}
        self._refresh_missing_sample_flags()

    def _on_program_selected(self, current, _previous):
        if current is None:
            return
        self._wanted_slot = current.data(Qt.ItemDataRole.UserRole)
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
        self._show_placeholder(f"Reading program {slot}...")
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
            self._program = None
            self._program_slot = slot
            self._show_placeholder(
                f"Couldn't read program {slot}.\n\nCheck the status bar, then press "
                "Refresh to try again. ~/.akaisds/akaisds.log has the details."
            )
            return
        self._program, self._program_slot = program, slot
        self._show_program(program)

    def _on_controller_status(self, message):
        # the engine reports progress and why a read failed through status_changed
        self.status_bar.showMessage(message, 8000)

    def _sync_busy_state(self):
        busy = self._controller.is_transfer_busy()
        self.refresh_button.setEnabled(not busy)
        self._refresh_action.setEnabled(not busy)
        self._dashboard_action.setEnabled(not busy)
        self._dashboard_action.setToolTip(tt.BUSY_BLOCKS_OTHER_WINDOWS if busy else "")

    # -- display ------------------------------------------------------------------------------

    def _show_program(self, program):
        self._show_details()
        v = self._values
        v["name"].setText(program.name.strip() or "(unnamed)")
        v["keygroups"].setText(str(program.num_keygroups))
        enabled = "responds to program change" if program.enable_midi_program else "program change off"
        v["midi_program"].setText(f"{program.midi_program_number} ({enabled})")
        v["key_tilt"].setText(f"{program.key_tilt:+d}")
        v["positional_xfade"].setText("on" if program.positional_xfade else "off")

        self.range_bar.set_ranges([(kg.lower_key, kg.upper_key) for kg in program.keygroups])
        table = self.keygroup_table
        table.blockSignals(True)
        table.setRowCount(len(program.keygroups))
        for row, kg in enumerate(program.keygroups):
            cells = (
                str(row + 1),
                format_key_range(kg.lower_key, kg.upper_key),
                sample_label(kg.soft_sample),
                sample_label(kg.loud_sample),
                format_velocity_switch(kg.velocity_switch),
            )
            for col, text in enumerate(cells):
                table.setItem(row, col, QTableWidgetItem(text))
        table.blockSignals(False)
        # sized to its rows (up to a cap, then it scrolls) so a 2-keygroup program
        # doesn't leave a tall empty box
        rows = min(program.num_keygroups, _MAX_VISIBLE_KEYGROUP_ROWS)
        table.setFixedHeight(
            table.horizontalHeader().height() + rows * table.verticalHeader().defaultSectionSize() + 4
        )
        table.selectRow(0)
        self._show_keygroup(0)
        self._refresh_missing_sample_flags()

    def _show_keygroup(self, row):
        program = self._program
        if program is None or not 0 <= row < program.num_keygroups:
            return
        kg = program.keygroups[row]
        v = self._values
        self.keygroup_title.setText(f"Keygroup {row + 1} of {program.num_keygroups}")
        v["range"].setText(format_key_range(kg.lower_key, kg.upper_key))
        v["velocity_switch"].setText(format_velocity_switch(kg.velocity_switch))
        v["voice_out"].setText(format_voice_out(kg.voice_out))
        v["midi_offset"].setText(str(kg.midi_offset))
        v["control_bits"].setText(format_control_bits(kg.control_bits))
        for prefix in ("soft", "loud"):
            v[f"{prefix}_transpose"].setText(format_transpose(getattr(kg, f"{prefix}_transpose")))
            v[f"{prefix}_filter"].setText(str(getattr(kg, f"{prefix}_filter")))
            v[f"{prefix}_loudness"].setText(format_loudness(getattr(kg, f"{prefix}_loudness")))
        for key in (
            "attack", "decay", "sustain", "release",
            "filter_attack", "filter_decay", "filter_sustain", "filter_release",
            "attack_vel", "release_vel", "loudness_vel", "adsr_to_vcf", "filter_vel",
            "filter_key_track", "lfo_rate", "lfo_depth", "lfo_build", "aftertouch_depth",
            "modwheel_depth", "pitch_warp_vel", "pitch_warp_offset", "pitch_warp_recovery",
        ):  # fmt: skip
            v[key].setText(f"{getattr(kg, key):+d}" if key in _SIGNED else str(getattr(kg, key)))
        self.amp_graph.set_values(kg.attack, kg.decay, kg.sustain, kg.release)
        self.filter_graph.set_values(
            kg.filter_attack, kg.filter_decay, kg.filter_sustain, kg.filter_release
        )
        self._refresh_missing_sample_flags()

    def _refresh_missing_sample_flags(self):
        # programs find samples BY NAME; a name the unit's catalog doesn't have is
        # the most useful thing a read-only viewer can point out
        program = self._program
        if program is None:
            return
        row = max(0, self.keygroup_table.currentRow())
        for kind, col in (("soft", 2), ("loud", 3)):
            for r, kg in enumerate(program.keygroups):
                name = getattr(kg, f"{kind}_sample")
                item = self.keygroup_table.item(r, col)
                if item is None:
                    continue
                missing = self._is_missing(name)
                item.setText(sample_label(name) + ("  (not on sampler)" if missing else ""))
        if 0 <= row < program.num_keygroups:
            kg = program.keygroups[row]
            for kind in ("soft", "loud"):
                name = getattr(kg, f"{kind}_sample")
                text = sample_label(name)
                if self._is_missing(name):
                    text += "  (not on the sampler)"
                self._values[f"{kind}_sample"].setText(text)

    def _is_missing(self, name):
        # an empty name is "no sample" (normal for an unused loud sample), and
        # without a catalog there's nothing to compare against
        return bool(name.strip()) and self._have_catalog and normalise_name(name) not in self._sample_names

    # -- closing --------------------------------------------------------------------------------

    def closeEvent(self, event):
        # same rule as ProgramEditorWindow: don't hand control back to the Dashboard
        # while a MIDI operation is still in flight on the shared connection
        if self._controller.is_transfer_busy():
            self.status_bar.showMessage(
                "Can't switch to the Transfer Dashboard - a MIDI operation is already "
                "in progress",
                5000,
            )
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
            (c.status_changed, self._on_controller_status),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._main_window.show()
        super().closeEvent(event)


# fields shown with an explicit sign (the signed ones in core/s950_program.py)
_SIGNED = frozenset(
    {"release_vel", "pitch_warp_offset", "adsr_to_vcf"}
)
