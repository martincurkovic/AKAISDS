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
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDial,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core import app_config, debug_log, s950_params
from core.midi_notes import midi_note_to_name
from core.s950_sysex import NAME_LENGTH
from core.s950_program import Keygroup, Program
from ui import theme, tooltips as tt
from ui.diagnostics_ui import add_open_log_folder_action
from ui.editor_layout import (
    add_keygroup_row,
    build_centered_row,
    build_content_row,
    build_knob_column,
    build_list_column,
    build_paired_row,
    build_zone_card,
    equalize_card_heights,
    keygroup_row_label,
    keygroup_row_text,
    style_card_page_layout,
)
from ui.envelope_graph import ADSREnvelopeGraph
from ui.keygroup_range_bar import KeygroupRangeBar, keygroup_color
from ui.knob import Knob
from ui.note_spinbox import NoteSpinBox
from ui.qt_helpers import FullWidthTabBar, build_scroll_area, build_section_card
from ui.s950_samples_tab import S950SamplesTab


def _log(message):
    # what the USER did in this window; the transfer engine (S950Transfers) logs what went on the wire
    debug_log.get_logger().info(f"S950Editor: {message}")


# velocity_switch value meaning "no switch - the soft sample plays at every velocity"
NO_VELOCITY_SWITCH = 128
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

# fields shown as knobs (the rest are spinboxes/combos/checkboxes) - the same split the
# S1000/S2000/S3000 editor makes: 0..99 amounts and +-50 offsets are knobs
_KNOB_ATTRS = frozenset(
    {
        "key_tilt",
        "attack", "decay", "sustain", "release",
        "filter_attack", "filter_decay", "filter_sustain", "filter_release",
        "filter_vel", "filter_key_track", "adsr_to_vcf",
        "attack_vel", "release_vel", "loudness_vel",
        "pitch_warp_vel", "pitch_warp_offset", "pitch_warp_recovery",
        "aftertouch_depth", "modwheel_depth", "lfo_build", "lfo_rate", "lfo_depth",
        "soft_filter", "soft_loudness", "loud_filter", "loud_loudness",
    }
)  # fmt: skip

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
    active = [
        label for bit, label in enumerate(_CONTROL_BIT_LABELS) if value & (1 << bit)
    ]
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
        _log("opened")
        self.setWindowTitle("AKAISDS - Akai S900/S950 Program Editor")
        self.setMinimumSize(1150, 720)

        self._programs = []  # [(slot, name)]
        self._sample_names = []  # names of the samples on the unit, in catalog order
        self._have_catalog = False
        self._slot = None  # slot of the program being edited
        self._baseline = (
            None  # the program as the sampler holds it (as loaded / last verified)
        )
        self._working = None  # the edited copy
        self._restore = (
            None  # the baseline before the last verified write (for Restore)
        )
        self._kg_row = 0
        self._loading = (
            False  # True while widgets are being filled (their signals are ignored)
        )
        self._writing = False
        self._reverting = False
        self._restoring = False  # the write in flight is a "Restore Previous"
        self._pending_restore = (
            None  # baseline to offer for Restore once the write verifies
        )
        # reading is one request at a time; the user can click faster than the
        # unit answers, so only the LATEST wanted slot is ever read next
        self._wanted_slot = None
        self._inflight_slot = None
        self._shown_slot = (
            None  # last slot answered (success OR failure) - stops re-requesting
        )
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
        theme.notifier.changed.connect(self._refresh_themed_swatches)

        self._busy_timer = QTimer(self)
        self._busy_timer.timeout.connect(self._update_buttons)
        self._busy_timer.start(150)

        self._show_placeholder("Reading the program list...")
        self._update_buttons()
        QTimer.singleShot(0, self._refresh)

    # -- construction -------------------------------------------------------------------
    #
    # The layout follows the S1000/S2000/S3000 editor (ProgramEditorWindow's Programs
    # tab) so the two feel like one app: a Programs column, a Keygroups column (range bar
    # + colored rows), and section cards on the right - a program page and a keygroup
    # page, knobs for the 0..99/+-50 amounts, an ADSR graph over its four knobs, a Zone
    # card with one button per sample (Soft/Loud here, Zone 1-4 there), and a bottom bar
    # with Refresh on the left and Close on the right.

    def _build_ui(self):
        self.program_list = QListWidget()
        self.program_list.setObjectName("programList")
        self.program_list.setFixedWidth(160)
        self.program_list.currentItemChanged.connect(self._on_program_selected)
        self.program_list.itemClicked.connect(
            lambda _item: self.detail_stack.setCurrentIndex(0)
        )

        self.keygroup_list = QListWidget()
        self.keygroup_list.setObjectName("keygroupList")
        self.keygroup_list.setFixedWidth(190)  # fits "Keygroup 12: C#1 - D#7"
        self.keygroup_list.currentRowChanged.connect(self._show_keygroup)
        self.keygroup_list.itemClicked.connect(
            lambda _item: self.detail_stack.setCurrentIndex(1)
        )
        self.keygroup_range_bar = KeygroupRangeBar()

        programs_container = build_list_column("Programs", self.program_list)
        keygroups_container = build_list_column(
            "Keygroups", self.keygroup_range_bar, self.keygroup_list
        )

        # right-hand side: a placeholder OR the detail pages
        self.placeholder = QLabel()
        self.placeholder.setObjectName("emptyQueueLabel")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)

        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(build_scroll_area(self._build_program_page()))
        self.detail_stack.addWidget(build_scroll_area(self._build_keygroup_page()))

        right_column = QVBoxLayout()
        right_column.setContentsMargins(0, 0, 0, 0)
        right_column.addWidget(self.placeholder, 1)
        right_column.addWidget(self.detail_stack, 1)
        right_container = QWidget()
        right_container.setLayout(right_column)

        content_layout = build_content_row(
            programs_container, keygroups_container, right_container
        )

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip(
            "Re-read the program and sample lists from the sampler"
        )
        self.refresh_button.clicked.connect(self._refresh)
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
        self.write_button.clicked.connect(self._write)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.refresh_button)
        bottom_row.addWidget(self.dirty_label)
        bottom_row.addStretch()
        bottom_row.addWidget(self.discard_button)
        bottom_row.addWidget(self.restore_button)
        bottom_row.addWidget(self.write_button)
        bottom_row.addSpacing(12)
        bottom_row.addWidget(close_button)

        # the same Programs / Samples tabs as the S1000/S2000/S3000 editor (there is no Multi)
        programs_tab_page = QWidget()
        programs_tab_page.setLayout(content_layout)
        self.samples_tab = S950SamplesTab(self._controller)
        self.samples_tab.status_message.connect(
            lambda message: self.status_bar.showMessage(message, 8000)
        )
        self.main_tabs = QTabWidget()
        # same full-width tab bar as the S3000 editor - installed before any tab is added
        self.main_tabs.setTabBar(FullWidthTabBar(self.main_tabs))
        self.main_tabs.addTab(programs_tab_page, "Programs")
        self._samples_tab_index = self.main_tabs.addTab(self.samples_tab, "Samples")
        self.main_tabs.currentChanged.connect(self._on_tab_changed)

        main_layout = QVBoxLayout()
        main_layout.addWidget(self.main_tabs, stretch=1)
        main_layout.addLayout(bottom_row)
        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        self.status_bar = QStatusBar()
        self.status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self.status_bar)
        experimental = QLabel("Experimental - writes are untested on real hardware")
        experimental.setObjectName("mutedLabel")
        self.status_bar.addPermanentWidget(experimental)

    # -- field widgets ------------------------------------------------------------------------

    def _editor(self, scope, attr):
        """The input widget for one field, built from what the field is."""
        defaults = Program() if scope == "program" else Keygroup()
        if attr == "name":
            w = QLineEdit()
            w.setMaxLength(NAME_LENGTH)
            w.editingFinished.connect(
                lambda w=w: self._on_name_finished(scope, attr, w)
            )
        elif attr in ("soft_sample", "loud_sample"):
            w = QComboBox()
            w.activated.connect(
                lambda _i, w=w: self._on_edit(scope, attr, w.currentData())
            )
        elif attr == "voice_out":
            w = QComboBox()
            for value in s950_params.VOICE_OUT_VALUES:
                w.addItem(format_voice_out(value).capitalize(), value)
            w.activated.connect(
                lambda _i, w=w: self._on_edit(scope, attr, w.currentData())
            )
        elif attr in ("positional_xfade", "enable_midi_program"):
            w = QCheckBox()
            w.toggled.connect(lambda v: self._on_edit(scope, attr, bool(v)))
        elif attr in ("lower_key", "upper_key"):
            lo, hi = s950_params.KEYGROUP_RANGES[attr]
            w = NoteSpinBox()
            w.setRange(lo, hi)
            w.setToolTip(
                _RANGE_TIP.format(lo=midi_note_to_name(lo), hi=midi_note_to_name(hi))
            )
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
                s950_params.PROGRAM_RANGES
                if scope == "program"
                else s950_params.KEYGROUP_RANGES
            )[attr]
            if attr in _KNOB_ATTRS:
                w = Knob()
                w.setRange(lo, hi)
                w.setDefaultValue(getattr(defaults, attr))
                w.setEnabled(True)  # Knob starts read-only
                w.setToolTip(_RANGE_TIP.format(lo=lo, hi=hi))
            else:
                w = QSpinBox()
                w.setRange(lo, hi)
                w.setToolTip(_RANGE_TIP.format(lo=lo, hi=hi))
            w.valueChanged.connect(lambda v: self._on_edit(scope, attr, int(v)))
        self._editors[(scope, attr)] = w
        return w

    def _knob_column(self, scope, attr, text, size=40, *, bold=False):
        knob = self._editor(scope, attr)
        knob.setFixedSize(size, size)
        column, value_label = build_knob_column(
            f"<b>{text}</b>" if bold else text, knob
        )
        # setValue(0) on a fresh knob emits nothing, so _set_widget fills the readout itself
        knob.setProperty("valueReadout", value_label)
        return column

    def _knob_row(self, scope, specs, size=40, spacing=10):
        row = QHBoxLayout()
        row.setSpacing(spacing)
        for attr, text in specs:
            row.addLayout(self._knob_column(scope, attr, text, size))
        return row

    def _labeled_row(self, scope, attr, text, label_width=110):
        # "Label  [widget]" on one line, like the S3000 editor's Note Range / Tune rows
        row = QHBoxLayout()
        label = QLabel(text)
        label.setFixedWidth(label_width)
        row.addWidget(label)
        row.addWidget(self._editor(scope, attr))
        row.addStretch()
        return row

    # -- pages ----------------------------------------------------------------------------------

    def _build_program_page(self):
        name_row = self._labeled_row("program", "name", "Name")
        midi_row = self._labeled_row("program", "midi_program_number", "MIDI Program")
        respond_row = self._labeled_row(
            "program", "enable_midi_program", "Program Change"
        )
        respond_row.itemAt(1).widget().setText("Respond to MIDI program change")
        program_card = build_section_card("Program", name_row, midi_row, respond_row)

        tilt_row = self._knob_row("program", [("key_tilt", "Key Tilt")], size=56)
        tilt_row.addStretch()
        xfade_row = self._labeled_row("program", "positional_xfade", "Crossfade")
        xfade_row.itemAt(1).widget().setText("Positional crossfade")
        keyboard_card = build_section_card("Keyboard", tilt_row, xfade_row)
        equalize_card_heights(program_card, keyboard_card)

        layout = QVBoxLayout()
        style_card_page_layout(layout)
        layout.addLayout(build_paired_row(program_card, keyboard_card))
        layout.addStretch()
        page = QWidget()
        page.setLayout(layout)
        return page

    def _build_keygroup_page(self):
        # Range + Filter
        note_row = QHBoxLayout()
        note_label = QLabel("Note Range")
        note_label.setFixedWidth(80)
        note_row.addWidget(note_label)
        note_row.addWidget(self._editor("kg", "lower_key"))
        note_row.addWidget(QLabel("-"))
        note_row.addWidget(self._editor("kg", "upper_key"))
        note_row.addStretch()
        switch_row = self._labeled_row("kg", "velocity_switch", "Velocity Switch", 100)
        switch_hint = QLabel(f"{NO_VELOCITY_SWITCH} = off")
        switch_hint.setObjectName("mutedLabel")
        switch_row.insertWidget(switch_row.count() - 1, switch_hint)
        range_card = build_section_card("Range", note_row, switch_row)
        filter_card = build_section_card(
            "Filter",
            self._knob_row(
                "kg",
                [
                    ("filter_key_track", "Key Track"),
                    ("filter_vel", "Velocity"),
                    ("adsr_to_vcf", "Env Amount"),
                ],
                size=56,
                spacing=24,
            ),
        )
        equalize_card_heights(range_card, filter_card)

        # Envelopes
        self.amp_graph = ADSREnvelopeGraph()
        self.amp_graph.setFixedSize(200, 90)
        self.filter_graph = ADSREnvelopeGraph()
        self.filter_graph.setFixedSize(200, 90)
        amp_card = build_section_card(
            "Amplitude Envelope",
            build_centered_row(self.amp_graph),
            self._knob_row(
                "kg",
                [
                    ("attack", "Attack"),
                    ("decay", "Decay"),
                    ("sustain", "Sustain"),
                    ("release", "Release"),
                ],
            ),
        )
        env_filter_card = build_section_card(
            "Filter Envelope",
            build_centered_row(self.filter_graph),
            self._knob_row(
                "kg",
                [
                    ("filter_attack", "Attack"),
                    ("filter_decay", "Decay"),
                    ("filter_sustain", "Sustain"),
                    ("filter_release", "Release"),
                ],
            ),
        )
        equalize_card_heights(amp_card, env_filter_card)

        # Zone: Soft / Loud, the way the S3000 editor has Zone 1-4
        self._zone_buttons = QButtonGroup(self)
        self._zone_buttons.setExclusive(True)
        selector_row = QHBoxLayout()
        self._zone_stack = QStackedWidget()
        self._zone_stack.setObjectName("transparentContainer")
        self._sample_warnings = {}
        for index, (prefix, title) in enumerate((("soft", "Soft"), ("loud", "Loud"))):
            button = QPushButton(f"{title} sample")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setToolTip(
                "The sample played below the velocity switch"
                if prefix == "soft"
                else "The sample played above the velocity switch"
            )
            self._zone_buttons.addButton(button, index)
            selector_row.addWidget(button)

            page_layout = QVBoxLayout()
            page_layout.setSpacing(6)
            combo = self._editor("kg", f"{prefix}_sample")
            sample_column = QVBoxLayout()
            sample_column.setSpacing(4)
            sample_column.addWidget(QLabel("<b>Sample</b>"))
            sample_column.addWidget(combo)
            top_row = QHBoxLayout()
            top_row.addLayout(sample_column, stretch=1)
            top_row.addLayout(
                self._knob_column("kg", f"{prefix}_filter", "Filter", 28, bold=True)
            )
            top_row.addLayout(
                self._knob_column("kg", f"{prefix}_loudness", "Loud", 28, bold=True)
            )
            page_layout.addLayout(top_row)
            warning = QLabel()
            warning.setObjectName("mutedLabel")
            self._sample_warnings[prefix] = warning
            page_layout.addWidget(warning)
            page_layout.addLayout(
                self._labeled_row("kg", f"{prefix}_transpose", "Tune", 80)
            )
            page = QWidget()
            page.setObjectName("transparentContainer")
            page.setLayout(page_layout)
            self._zone_stack.addWidget(page)
        self._zone_buttons.idClicked.connect(self._zone_stack.setCurrentIndex)
        zone_card = build_zone_card(selector_row, self._zone_stack)

        # Velocity + LFO
        velocity_card = build_section_card(
            "Velocity",
            self._knob_row(
                "kg",
                [
                    ("attack_vel", "Attack"),
                    ("release_vel", "Release"),
                    ("loudness_vel", "Loudness"),
                ],
            ),
        )
        lfo_card = build_section_card(
            "LFO",
            self._knob_row(
                "kg",
                [
                    ("lfo_rate", "Rate"),
                    ("lfo_depth", "Depth"),
                    ("lfo_build", "Build-up"),
                ],
            ),
            self._knob_row(
                "kg",
                [("aftertouch_depth", "Aftertouch"), ("modwheel_depth", "Mod Wheel")],
            ),
        )
        equalize_card_heights(velocity_card, lfo_card)

        # Pitch warp + Output
        pitch_card = build_section_card(
            "Pitch Warp",
            self._knob_row(
                "kg",
                [
                    ("pitch_warp_vel", "Velocity"),
                    ("pitch_warp_offset", "Offset"),
                    ("pitch_warp_recovery", "Recovery"),
                ],
            ),
        )
        options = QLabel("-")
        options.setToolTip("Shown, never written: what the bits mean is not verified")
        self._readonly["control_bits"] = options
        options_row = QHBoxLayout()
        options_label = QLabel("Options")
        options_label.setFixedWidth(110)
        options_row.addWidget(options_label)
        options_row.addWidget(options, stretch=1)
        output_card = build_section_card(
            "Output",
            self._labeled_row("kg", "voice_out", "Output"),
            self._labeled_row("kg", "midi_offset", "MIDI Offset"),
            options_row,
        )
        equalize_card_heights(pitch_card, output_card)

        layout = QVBoxLayout()
        style_card_page_layout(layout)
        layout.addLayout(build_paired_row(range_card, filter_card))
        layout.addLayout(build_paired_row(amp_card, env_filter_card))
        layout.addWidget(zone_card)
        layout.addLayout(build_paired_row(velocity_card, lfo_card))
        layout.addLayout(build_paired_row(pitch_card, output_card))
        layout.addStretch()
        page = QWidget()
        page.setLayout(layout)
        return page

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
        self._write_unchanged_action = QAction(
            "Write Program Back Unchanged (test)", self
        )
        self._write_unchanged_action.setToolTip(
            "Checks the write path without changing anything: the program is sent back as "
            "read, then read again and compared"
        )
        self._write_unchanged_action.triggered.connect(self._write_unchanged)
        hardware_menu.addAction(self._write_unchanged_action)
        hardware_menu.addSeparator()
        add_open_log_folder_action(hardware_menu, self)

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
        window_menu.addSeparator()
        # Ctrl+2/Ctrl+3, as in the S1000/S2000/S3000 editor (its Ctrl+1 is the Multi tab,
        # which the S900/S950 doesn't have)
        programs_tab_action = QAction("Programs Tab", self)
        programs_tab_action.setShortcut("Ctrl+2")
        programs_tab_action.triggered.connect(lambda: self.main_tabs.setCurrentIndex(0))
        window_menu.addAction(programs_tab_action)
        samples_tab_action = QAction("Samples Tab", self)
        samples_tab_action.setShortcut("Ctrl+3")
        samples_tab_action.triggered.connect(
            lambda: self.main_tabs.setCurrentIndex(self._samples_tab_index)
        )
        window_menu.addAction(samples_tab_action)

    def _on_tab_changed(self, index):
        on_samples = index == self._samples_tab_index
        _log(f"tab: {'Samples' if on_samples else 'Programs'}")
        # the program-writing buttons only mean something on the Programs tab
        for widget in (
            self.dirty_label,
            self.discard_button,
            self.restore_button,
            self.write_button,
        ):
            widget.setVisible(not on_samples)
        self.samples_tab.set_active(on_samples)

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
        n = len(changes)
        self.dirty_label.setText(
            f"{n} unsaved change{'' if n == 1 else 's'}" if n else ""
        )
        self.detail_stack.setEnabled(not self._writing)
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
        self.detail_stack.setVisible(False)
        self.keygroup_list.clear()
        self.keygroup_range_bar.set_ranges([])

    def _show_details(self):
        self.placeholder.setVisible(False)
        self.detail_stack.setVisible(True)

    def _refresh(self):
        # re-read the catalog, then the program on screen (a stale one is the
        # likeliest thing the user is refreshing for)
        if not self._controller.is_s950_idle() or self._writing:
            _log("refresh refused: a MIDI operation is already in progress")
            self.status_bar.showMessage(
                "A MIDI operation is already in progress - try again in a moment", 5000
            )
            return
        if not self._confirm_discard():
            _log("refresh cancelled at the discard prompt")
            return
        _log(f"refresh (program on screen: {self._slot})")
        self._shown_slot = None
        if self._slot is not None:
            self._wanted_slot = self._slot
        self.samples_tab.refresh()  # what it read about the samples is about to be re-read
        self._controller.refresh_sample_list(silent=True)
        self.status_bar.showMessage("Reading the program list...", 0)

    def _on_programs_listed(self, entries):
        self._have_catalog = True
        self._programs = list(entries)
        keep = self._wanted_slot if self._wanted_slot is not None else self._slot
        if self._programs and keep not in {slot for slot, _ in self._programs}:
            # nothing chosen yet (the window just opened), or the chosen program is gone:
            # open the first one rather than asking the user to pick
            keep = self._programs[0][0]
            self._wanted_slot = keep
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
            self.status_bar.showMessage(
                f"{n} program{'' if n == 1 else 's'} on the sampler", 0
            )
            self._pump()
            self._refresh_sample_widgets()

    def _on_samples_listed(self, entries):
        self._sample_names = [name for _slot, name in entries]
        self._refresh_sample_widgets()

    def _on_program_selected(self, current, previous):
        if current is None or self._reverting:
            return
        slot = current.data(Qt.ItemDataRole.UserRole)
        _log(f"program {slot} selected (was {self._slot})")
        if slot != self._slot and not self._confirm_discard():
            _log(f"kept editing program {self._slot} (discard prompt declined)")
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
        self._slot, self._baseline, self._working, self._restore = (
            None,
            None,
            None,
            None,
        )
        self._show_placeholder(f"Reading program {slot}...")
        self._update_buttons()
        self._controller.request_program(slot)

    def _on_program_received(self, slot, program):
        if slot != self._inflight_slot:
            return  # not a read this window asked for
        self._inflight_slot = None
        self._shown_slot = slot
        _log(f"program {slot} read: {'FAILED' if program is None else 'ok'}")
        if self._wanted_slot is not None and self._wanted_slot != slot:
            # the user moved on while this was in flight
            self._shown_slot = None
            self._pump()
            return
        if program is None:
            self._slot, self._baseline, self._working, self._restore = (
                slot,
                None,
                None,
                None,
            )
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
        self.detail_stack.setCurrentIndex(0)  # the program page, as in the S3000 editor
        self._fill_program_widgets()
        self._fill_keygroup_list()
        self._show_keygroup(0)
        self._update_buttons()

    # -- display ------------------------------------------------------------------------------

    def _set_widget(self, widget, value):
        # set an input without it counting as an edit; widen a range so a value the unit
        # already holds is shown truthfully rather than clamped
        if isinstance(widget, (QSpinBox, QDoubleSpinBox, QDial)):
            if value < widget.minimum():
                widget.setMinimum(value)
            if value > widget.maximum():
                widget.setMaximum(value)
            widget.setValue(value)
            readout = widget.property("valueReadout")
            if readout is not None:
                readout.setText(str(widget.value()))
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
                self._set_widget(
                    self._editors[("program", attr)], getattr(self._working, attr)
                )
        finally:
            self._loading = False

    def _fill_keygroup_list(self):
        program = self._working
        self.keygroup_list.blockSignals(True)
        self.keygroup_list.clear()
        for index, kg in enumerate(program.keygroups):
            add_keygroup_row(
                self.keygroup_list,
                index,
                kg.lower_key,
                kg.upper_key,
                self._refresh_swatch,
            )
        self.keygroup_list.setCurrentRow(0)
        self.keygroup_list.blockSignals(False)
        self.keygroup_range_bar.set_ranges(
            [(kg.lower_key, kg.upper_key) for kg in program.keygroups]
        )

    # The row itself is ui.editor_layout.add_keygroup_row, shared with the S3000 editor. The
    # swatch color comes from the palette, so it can't be a QSS rule: it carries a swatchKind
    # property and is recolored when the theme changes.
    def keygroup_row_text(self, row):
        # what the list shows for a row (tests and the status line read it)
        label = keygroup_row_label(self.keygroup_list, row)
        return label.text() if label is not None else ""

    def _update_keygroup_row(self, row):
        kg = self._working.keygroups[row]
        label = keygroup_row_label(self.keygroup_list, row)
        if label is not None:
            label.setText(keygroup_row_text(row, kg.lower_key, kg.upper_key))

    def _refresh_swatch(self, swatch):
        color = keygroup_color(swatch.property("keygroupIndex")).name()
        swatch.setStyleSheet(f"background-color: {color}; border-radius: 2px;")

    def _refresh_themed_swatches(self):
        for label in self.keygroup_list.findChildren(QLabel):
            if label.property("swatchKind"):
                self._refresh_swatch(label)

    def _show_keygroup(self, row):
        program = self._working
        if program is None or not 0 <= row < program.num_keygroups:
            return
        self._kg_row = row
        kg = program.keygroups[row]
        self._loading = True
        try:
            for attr in s950_params.KEYGROUP_FIELDS:
                if attr in ("soft_sample", "loud_sample"):
                    self._fill_sample_combo(attr, getattr(kg, attr))
                    self._update_sample_warning(attr, getattr(kg, attr))
                elif attr.endswith("_transpose"):
                    self._set_widget(
                        self._editors[("kg", attr)], getattr(kg, attr) / 16
                    )
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
            (
                i
                for i in range(combo.count())
                if normalise_name(combo.itemData(i)) == normalise_name(current)
            ),
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
        self._loading = True
        try:
            kg = self._working.keygroups[
                min(self._kg_row, self._working.num_keygroups - 1)
            ]
            for attr in ("soft_sample", "loud_sample"):
                self._fill_sample_combo(attr, getattr(kg, attr))
                self._update_sample_warning(attr, getattr(kg, attr))
        finally:
            self._loading = False

    def _is_missing(self, name):
        # programs find samples BY NAME; a name the unit's catalog doesn't have is the most
        # useful thing the editor can point out. An empty name is "no sample" (normal for an
        # unused loud sample), and without a catalog there's nothing to compare against.
        known = {normalise_name(n) for n in self._sample_names}
        return (
            bool(name.strip())
            and self._have_catalog
            and normalise_name(name) not in known
        )

    def _update_sample_warning(self, attr, name):
        label = self._sample_warnings[attr.removesuffix("_sample")]
        label.setText(
            "Not on the sampler - programs find samples by name"
            if self._is_missing(name)
            else ""
        )

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
            if attr in ("lower_key", "upper_key"):
                self._update_keygroup_row(self._kg_row)
                self.keygroup_range_bar.set_ranges(
                    [(k.lower_key, k.upper_key) for k in self._working.keygroups]
                )
            if attr in ("soft_sample", "loud_sample"):
                self._update_sample_warning(attr, value)
            if attr in _ENVELOPE_ATTRS["amp"] + _ENVELOPE_ATTRS["filter"]:
                self._refresh_graphs(kg)
        self._update_buttons()

    def _discard(self):
        if self._baseline is None or not self.is_dirty():
            return
        _log(f"discarded {len(self._changes())} change(s) on program {self._slot}")
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
            _log("restore previous cancelled")
            return
        _log(f"restore previous requested for program {self._slot}")
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
            _log(f"write refused (diff failed): {e}")
            QMessageBox.warning(self, "Can't write", str(e))
            return
        problems = s950_params.program_problems(edited, changes)
        problems += self._name_clash_problems(edited)
        if problems:
            _log(f"write refused, {len(problems)} problem(s): {problems}")
            QMessageBox.warning(
                self,
                "Can't write this program",
                "Nothing was written:\n\n" + "\n".join(problems),
            )
            return
        if not self._confirm_first_write():
            _log("write cancelled at the experimental warning")
            return
        _log(
            f"write requested: program {self._slot}, {len(changes)} change(s)"
            f"{', restoring' if restoring else ''}"
        )
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
            if slot != self._slot
            and normalise_name(name) == normalise_name(program.name)
        ]
        if program.name.strip() and clash:
            return [
                f"Another program (slot {clash[0]}) is already called '{program.name.strip()}'."
            ]
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
        _log(
            f"program {slot} write result: verified={verified}, sampler state returned="
            f"{held is not None}: {message}"
        )
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
            QTimer.singleShot(
                50, lambda: self._controller.refresh_sample_list(silent=True)
            )
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
        _log("closed")
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
        try:
            theme.notifier.changed.disconnect(self._refresh_themed_swatches)
        except (RuntimeError, TypeError):
            pass
        self.samples_tab.disconnect_controller()
        self._main_window.show()
        super().closeEvent(event)
