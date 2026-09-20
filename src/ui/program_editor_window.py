import os
import sys

if __name__ == "__main__":
    # running this file directly (not through main.py) puts src/ui on
    # sys.path, not src/ - so the ui./core. imports below would otherwise
    # fail with "No module named 'ui'"
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtGui import Qt, QAction
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QComboBox,
    QWidget,
    QStackedWidget,
    QDoubleSpinBox,
    QButtonGroup,
    QSpinBox,
    QStatusBar,
    QTabWidget,
)
from ui.knob import Knob
from ui.note_spinbox import NoteSpinBox
from ui.qt_helpers import FullWidthTabBar
from ui.envelope_graph import ADSREnvelopeGraph, Envelope2Graph
from ui.keygroup_range_bar import KeygroupRangeBar, keygroup_color
from core.midi_notes import midi_note_to_name
from core.program_editor_bridge import (
    KeygroupLoader,
    ParameterWriter,
    ProgramListLoader,
    KeygroupDetailLoader,
    SampleListLoader,
    MultiPartsLoader,
    ProgramChangeSender,
)


class ProgramEditorWindow(QMainWindow):
    def __init__(self, main_window, bridge):
        super().__init__()
        self.setWindowTitle("AKAISDS - Program Editor")
        # the old 800x500 predates the envelope controls - height in
        # particular is now well past what fits there. 850x800 gives some
        # breathing room over the layout's own measured minimum (810x781)
        self.setMinimumSize(850, 800)
        self._active_writers = {}
        self._sample_list = []
        self._keygroup_ranges = []  # [lo, hi] per keygroup - mirrors keygroup_range_bar
        self._pending_restore_state = None  # set only by _refresh_from_hardware()
        self._refresh_in_progress = False
        self._multi_refresh_in_progress = False

        self._main_window = main_window
        self._bridge = bridge

        # placeholder - real program, keygroup panels come later
        self.program_list = QListWidget()
        self.program_list.setObjectName("programList")
        self.program_list.setFixedWidth(160)

        self.keygroup_list = QListWidget()
        self.keygroup_list.setObjectName("keygroupList")
        self.keygroup_list.setFixedWidth(190)  # fits "Keygroup 12: C#1 - D#7"

        self.keygroup_range_bar = KeygroupRangeBar()

        # bold header + list, same layout shape as the dashboard's queue/hardware panels
        programs_column = QVBoxLayout()
        programs_column.setContentsMargins(0, 0, 0, 0)
        programs_column.setSpacing(6)
        programs_column.addWidget(QLabel("<b>Programs</b>"))
        programs_column.addWidget(self.program_list)
        programs_container = QWidget()
        programs_container.setLayout(programs_column)

        keygroups_column = QVBoxLayout()
        keygroups_column.setContentsMargins(0, 0, 0, 0)
        keygroups_column.setSpacing(6)
        keygroups_column.addWidget(QLabel("<b>Keygroups</b>"))
        keygroups_column.addWidget(self.keygroup_range_bar)
        keygroups_column.addWidget(self.keygroup_list)
        keygroups_container = QWidget()
        keygroups_container.setLayout(keygroups_column)

        self.note_lo_spinbox = NoteSpinBox()
        self.note_hi_spinbox = NoteSpinBox()
        note_range_row = QHBoxLayout()
        note_range_label = QLabel("Note Range")
        note_range_label.setFixedWidth(80)
        note_range_row.addWidget(note_range_label)
        note_range_row.addWidget(self.note_lo_spinbox)
        note_range_row.addWidget(QLabel("-"))
        note_range_row.addWidget(self.note_hi_spinbox)
        note_range_row.addStretch()

        self.cutoff_knob = Knob()
        self.cutoff_knob.setRange(0, 99)
        self.cutoff_knob.setDefaultValue(99)  # fully open - no filtering
        self.cutoff_knob.setFixedSize(80, 80)
        self.resonance_knob = Knob()
        self.resonance_knob.setRange(0, 15)
        self.resonance_knob.setDefaultValue(0)  # no resonance
        self.resonance_knob.setFixedSize(80, 80)

        self.env1_graph = ADSREnvelopeGraph()
        self.env1_graph.setFixedSize(200, 90)
        self.env2_graph = Envelope2Graph()
        self.env2_graph.setFixedSize(200, 90)

        # ENV1 - a standard ADSR: one knob per stage, in a single row
        self.attack1_knob = Knob()
        self.attack1_knob.setRange(0, 99)
        self.attack1_knob.setFixedSize(40, 40)
        self.decay1_knob = Knob()
        self.decay1_knob.setRange(0, 99)
        self.decay1_knob.setFixedSize(40, 40)
        self.sustain1_knob = Knob()
        self.sustain1_knob.setRange(0, 99)
        self.sustain1_knob.setFixedSize(40, 40)
        self.release1_knob = Knob()
        self.release1_knob.setRange(0, 99)
        self.release1_knob.setFixedSize(40, 40)

        attack1_col, self.attack1_value_label = self._build_knob_column(
            "Attack", self.attack1_knob
        )
        decay1_col, self.decay1_value_label = self._build_knob_column(
            "Decay", self.decay1_knob
        )
        sustain1_col, self.sustain1_value_label = self._build_knob_column(
            "Sustain", self.sustain1_knob
        )
        release1_col, self.release1_value_label = self._build_knob_column(
            "Release", self.release1_knob
        )

        env1_controls_row = QHBoxLayout()
        env1_controls_row.setSpacing(10)
        env1_controls_row.addLayout(attack1_col)
        env1_controls_row.addLayout(decay1_col)
        env1_controls_row.addLayout(sustain1_col)
        env1_controls_row.addLayout(release1_col)

        for knob, field in (
            (self.attack1_knob, "ATTAK1"),
            (self.decay1_knob, "DECAY1"),
            (self.sustain1_knob, "SUSTN1"),
            (self.release1_knob, "RELSE1"),
        ):
            knob.valueChanged.connect(self._on_env1_knob_changed)
            self._wire_knob_write(
                knob, field, "keygroup", keygroup_index_getter=self.keygroup_list.currentRow
            )

        # ENV2 - a 4-stage rate/level generator: rate knobs on top, that
        # stage's level knob below it, stages left-to-right in order
        self._env2_rate_knobs = [Knob() for _ in range(4)]
        self._env2_level_knobs = [Knob() for _ in range(4)]
        self._env2_rate_value_labels = []
        self._env2_level_value_labels = []
        env2_rate_row = QHBoxLayout()
        env2_rate_row.setSpacing(6)
        env2_level_row = QHBoxLayout()
        env2_level_row.setSpacing(6)
        for i, (rate_knob, level_knob) in enumerate(
            zip(self._env2_rate_knobs, self._env2_level_knobs), start=1
        ):
            rate_knob.setRange(0, 99)
            rate_knob.setFixedSize(32, 32)
            level_knob.setRange(0, 99)
            level_knob.setFixedSize(32, 32)
            rate_col, rate_value_label = self._build_knob_column(f"R{i}", rate_knob)
            level_col, level_value_label = self._build_knob_column(f"L{i}", level_knob)
            env2_rate_row.addLayout(rate_col)
            env2_level_row.addLayout(level_col)
            self._env2_rate_value_labels.append(rate_value_label)
            self._env2_level_value_labels.append(level_value_label)

            rate_knob.valueChanged.connect(self._on_env2_knob_changed)
            level_knob.valueChanged.connect(self._on_env2_knob_changed)

        # ENV2's eight fields aren't a plain rate1..4/level1..4 sequence -
        # ATTAK2/DECAY2/RELSE2/SUSTN2 keep their ADSR-flavoured names from
        # ENV1, and ENV2L1/ENV2R2/ENV2L2/ENV2L4 fill in the rest of the same
        # 4-stage rate/level structure (see s3k/params.py's notes on these).
        _ENV2_FIELDS = ["ATTAK2", "ENV2R2", "DECAY2", "RELSE2"]
        for rate_knob, level_knob, rate_field, level_field in zip(
            self._env2_rate_knobs,
            self._env2_level_knobs,
            _ENV2_FIELDS,
            ["ENV2L1", "ENV2L2", "SUSTN2", "ENV2L4"],
        ):
            self._wire_knob_write(
                rate_knob,
                rate_field,
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )
            self._wire_knob_write(
                level_knob,
                level_field,
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )

        env2_controls_column = QVBoxLayout()
        env2_controls_column.setSpacing(4)
        env2_controls_column.addLayout(env2_rate_row)
        env2_controls_column.addLayout(env2_level_row)

        env1_column = self._build_labeled_column(
            "ENV1", self.env1_graph, extra_layout=env1_controls_row
        )
        env2_column = self._build_labeled_column(
            "ENV2", self.env2_graph, extra_layout=env2_controls_column
        )

        envelopes_layout = QHBoxLayout()
        envelopes_layout.addLayout(env1_column)
        envelopes_layout.addLayout(env2_column)

        cutoff_column, self.cutoff_value_label = self._build_knob_column(
            "Cutoff", self.cutoff_knob
        )
        resonance_column, self.resonance_value_label = self._build_knob_column(
            "Resonance", self.resonance_knob
        )

        knobs_layout = QHBoxLayout()
        knobs_layout.addLayout(cutoff_column)
        knobs_layout.addLayout(resonance_column)

        # per-zone control lists — indexed 0-3
        self._zone_combos = []
        self._zone_vel_lo = []
        self._zone_vel_hi = []
        self._zone_tune = []
        self._zone_loudness = []
        self._zone_pan = []

        _ZONE_FIELDS = [
            ("SNAME1", "LOVEL1", "HIVEL1", "VTUNO1", "VLOUD1", "VPANO1"),
            ("SNAME2", "LOVEL2", "HIVEL2", "VTUNO2", "VLOUD2", "VPANO2"),
            ("SNAME3", "LOVEL3", "HIVEL3", "VTUNO3", "VLOUD3", "VPANO3"),
            ("SNAME4", "LOVEL4", "HIVEL4", "VTUNO4", "VLOUD4", "VPANO4"),
        ]

        zone_selector_row = QHBoxLayout()
        self._zone_button_group = QButtonGroup(self)
        self._zone_button_group.setExclusive(True)

        self._zone_stack = QStackedWidget()
        self._zone_stack.setStyleSheet("background: transparent;")

        self._zone_loudness_labels = []
        self._zone_pan_labels = []
        for zone_idx, (sname, lovel, hivel, vtuno, vloud, vpano) in enumerate(
            _ZONE_FIELDS
        ):
            btn = QPushButton(f"Zone {zone_idx + 1}")
            btn.setCheckable(True)
            btn.setChecked(zone_idx == 0)
            self._zone_button_group.addButton(btn, zone_idx)
            zone_selector_row.addWidget(btn)

            page = QWidget()
            page.setStyleSheet("background: transparent;")
            page_layout = QVBoxLayout()
            page_layout.setSpacing(6)

            # sample row
            sample_row = QHBoxLayout()
            sample_label = QLabel("Sample")
            sample_label.setFixedWidth(55)
            combo = QComboBox()
            combo.setEnabled(False)
            sample_row.addWidget(sample_label)
            sample_row.addWidget(combo, stretch=1)
            page_layout.addLayout(sample_row)
            self._zone_combos.append(combo)

            # velocity row
            vel_row = QHBoxLayout()
            vel_lo_label = QLabel("Velocity Low")
            vel_lo_label.setFixedWidth(80)
            vel_lo = QSpinBox()
            vel_lo.setRange(0, 127)
            vel_lo.setFixedWidth(60)
            vel_hi_label = QLabel("Velocity High")
            vel_hi_label.setFixedWidth(80)
            vel_hi = QSpinBox()
            vel_hi.setRange(0, 127)
            vel_hi.setFixedWidth(60)
            vel_row.addWidget(vel_lo_label)
            vel_row.addWidget(vel_lo)
            vel_row.addSpacing(12)
            vel_row.addWidget(vel_hi_label)
            vel_row.addWidget(vel_hi)
            vel_row.addStretch()
            page_layout.addLayout(vel_row)
            self._zone_vel_lo.append(vel_lo)
            self._zone_vel_hi.append(vel_hi)

            # tune stays as a spinbox — precision decimal values need it
            tune_row = QHBoxLayout()
            tune_row_label = QLabel("Tune")
            tune_row_label.setFixedWidth(35)
            tune = QDoubleSpinBox()
            tune.setRange(-50.0, 50.0)
            tune.setSingleStep(0.01)
            tune.setDecimals(2)
            tune.setSuffix(" st")
            tune.setFixedWidth(90)
            tune_row.addWidget(tune_row_label)
            tune_row.addWidget(tune)
            tune_row.addStretch()
            page_layout.addLayout(tune_row)
            self._zone_tune.append(tune)

            # loudness and pan as small knobs — ±50 range maps naturally
            loud_knob = Knob()
            loud_knob.setRange(-50, 50)
            loud_knob.setFixedSize(40, 40)
            loud_knob.setEnabled(True)
            pan_knob = Knob()
            pan_knob.setRange(-50, 50)
            pan_knob.setFixedSize(40, 40)
            pan_knob.setEnabled(True)

            loud_col, loud_val_label = self._build_knob_column("Loud", loud_knob)
            pan_col, pan_val_label = self._build_knob_column("Pan", pan_knob)

            zone_knob_row = QHBoxLayout()
            zone_knob_row.addLayout(loud_col)
            zone_knob_row.addLayout(pan_col)
            zone_knob_row.addStretch()
            page_layout.addLayout(zone_knob_row)

            self._zone_loudness.append(loud_knob)
            self._zone_pan.append(pan_knob)
            self._zone_loudness_labels.append(loud_val_label)
            self._zone_pan_labels.append(pan_val_label)

            page.setLayout(page_layout)
            self._zone_stack.addWidget(page)

            # wire write signals for this zone's controls
            self._wire_spinbox_write(
                vel_lo, lovel, "keygroup", keygroup_index_getter=self.keygroup_list.currentRow
            )
            self._wire_spinbox_write(
                vel_hi, hivel, "keygroup", keygroup_index_getter=self.keygroup_list.currentRow
            )
            self._wire_spinbox_write(
                tune,
                vtuno,
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
                value_converter=self._semitones_to_tune_offset,
            )
            self._wire_knob_write(
                loud_knob,
                vloud,
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )
            self._wire_knob_write(
                pan_knob, vpano, "keygroup", keygroup_index_getter=self.keygroup_list.currentRow
            )

        self._zone_button_group.idClicked.connect(self._zone_stack.setCurrentIndex)

        zone_section = QVBoxLayout()
        zone_section.setContentsMargins(10, 10, 10, 10)
        zone_section.setSpacing(4)
        zone_section.addLayout(zone_selector_row)
        zone_section.addWidget(self._zone_stack)
        zone_card = QWidget()
        zone_card.setObjectName("zoneCard")
        zone_card.setLayout(zone_section)

        detail_container_layout = QVBoxLayout()
        detail_container_layout.setSpacing(10)
        detail_container_layout.addLayout(note_range_row)
        detail_container_layout.addLayout(knobs_layout)
        detail_container_layout.addLayout(envelopes_layout)
        detail_container_layout.addWidget(zone_card)
        detail_container_layout.addStretch()
        detail_container = QWidget()
        detail_container.setLayout(detail_container_layout)

        self.pan_knob = Knob()
        self.pan_knob.setRange(-50, 50)
        self.pan_knob.setFixedSize(80, 80)
        pan_column, self.pan_value_label = self._build_knob_column("Pan", self.pan_knob)

        self.lfo_rate_knob = Knob()
        self.lfo_rate_knob.setRange(0, 99)
        self.lfo_rate_knob.setDefaultValue(0)  # no modulation without depth anyway
        self.lfo_rate_knob.setFixedSize(80, 80)
        self.lfo_depth_knob = Knob()
        self.lfo_depth_knob.setRange(0, 99)
        self.lfo_depth_knob.setDefaultValue(0)  # no modulation
        self.lfo_depth_knob.setFixedSize(80, 80)
        self.lfo_delay_knob = Knob()
        self.lfo_delay_knob.setRange(0, 99)
        self.lfo_delay_knob.setDefaultValue(0)  # no delay before the LFO starts
        self.lfo_delay_knob.setFixedSize(80, 80)

        self.lfo_shape_combo = QComboBox()
        self.lfo_shape_combo.addItems(["Triangle", "Sawtooth", "Square", "Random"])
        self.lfo_shape_combo.setEnabled(True)
        self.lfo_shape_combo.currentIndexChanged.connect(
            lambda i: self._schedule_write("LFO1WAVE", "program", i)
        )

        lfo_shape_label = QLabel("LFO shape")
        lfo_shape_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lfo_shape_column = QVBoxLayout()
        lfo_shape_column.setSpacing(4)
        lfo_shape_column.addWidget(
            lfo_shape_label, alignment=Qt.AlignmentFlag.AlignHCenter
        )
        lfo_shape_column.addWidget(self.lfo_shape_combo)

        lfo_rate_column, self.lfo_rate_value_label = self._build_knob_column(
            "LFO rate", self.lfo_rate_knob
        )
        lfo_depth_column, self.lfo_depth_value_label = self._build_knob_column(
            "LFO depth", self.lfo_depth_knob
        )
        lfo_delay_column, self.lfo_delay_value_label = self._build_knob_column(
            "LFO delay", self.lfo_delay_knob
        )

        self.polyph_combo = QComboBox()
        for voices in range(1, 33):  # 1-32 voices - the sampler's full range
            self.polyph_combo.addItem(str(voices), voices)
        self.polyph_combo.setEnabled(True)
        self.polyph_combo.currentIndexChanged.connect(
            lambda i: self._schedule_write(
                "POLYPH", "program", self.polyph_combo.itemData(i)
            )
        )
        polyph_label = QLabel("Polyphony")
        polyph_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        polyph_column = QVBoxLayout()
        polyph_column.setSpacing(4)
        polyph_column.addWidget(polyph_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        polyph_column.addWidget(self.polyph_combo)

        # row 1: Pan, LFO rate, LFO depth
        program_knobs_row1 = QHBoxLayout()
        program_knobs_row1.addLayout(pan_column)
        program_knobs_row1.addLayout(lfo_rate_column)
        program_knobs_row1.addLayout(lfo_depth_column)
        program_knobs_row1.addStretch()

        # row 2: LFO delay (only one knob in this row currently)
        program_knobs_row2 = QHBoxLayout()
        program_knobs_row2.addLayout(lfo_delay_column)
        program_knobs_row2.addStretch()

        # row 3: LFO shape and Polyphony side by side, constrained width
        self.lfo_shape_combo.setMaximumWidth(180)
        self.polyph_combo.setMaximumWidth(80)
        program_controls_row = QHBoxLayout()
        program_controls_row.addLayout(lfo_shape_column)
        program_controls_row.addLayout(polyph_column)
        program_controls_row.addStretch()

        program_page = QWidget()
        program_page_layout = QVBoxLayout()
        program_page_layout.setSpacing(12)
        program_page_layout.addLayout(program_knobs_row1)
        program_page_layout.addLayout(program_knobs_row2)
        program_page_layout.addLayout(program_controls_row)
        program_page_layout.addStretch()
        program_page.setLayout(program_page_layout)

        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(program_page)
        self.detail_stack.addWidget(detail_container)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)

        refresh_button = QPushButton("⟳ Refresh")
        refresh_button.setToolTip(
            "Reload the current program/keygroup from the hardware (⌘R) - "
            "use this if you've changed something on the sampler's own front panel"
        )
        refresh_button.clicked.connect(self._refresh_from_hardware)

        content_layout = QHBoxLayout()
        content_layout.addWidget(programs_container)
        content_layout.addWidget(keygroups_container)
        content_layout.addWidget(self.detail_stack, stretch=1)
        programs_tab_page = QWidget()
        programs_tab_page.setLayout(content_layout)

        multis_tab_page = self._build_multis_tab()

        self.main_tabs = QTabWidget()
        # same full-width tab bar as the MIDI Settings dialog - must be
        # installed before any tabs are added, or they'd be dropped
        self.main_tabs.setTabBar(FullWidthTabBar(self.main_tabs))
        self.main_tabs.addTab(multis_tab_page, "Multis")
        self.main_tabs.addTab(programs_tab_page, "Programs")

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(refresh_button)
        bottom_row.addStretch()
        bottom_row.addWidget(close_button)

        main_layout = QVBoxLayout()
        main_layout.addWidget(self.main_tabs)
        main_layout.addLayout(bottom_row)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        refresh_action = QAction("Refresh from Hardware", self)
        refresh_action.setShortcut("Ctrl+R")  # shows as ⌘R on macOS
        refresh_action.triggered.connect(self._refresh_from_hardware)
        hardware_menu = self.menuBar().addMenu("&Hardware")
        hardware_menu.addAction(refresh_action)

        # only one of {dashboard, program editor} is ever open at a time -
        # two simultaneous MIDI connections to the hardware is untested and
        # may not be safe - mirrors the dashboard's own "&Window" menu
        # (main_window.py) so the same Ctrl+1/Ctrl+2 shortcuts work no
        # matter which window currently has focus
        window_menu = self.menuBar().addMenu("&Window")

        dashboard_action = QAction("Transfer Dashboard", self)
        dashboard_action.setShortcut("Ctrl+1")
        # closing (rather than hiding outright) reuses closeEvent()'s
        # existing "show the main window again" cleanup below
        dashboard_action.triggered.connect(self.close)
        window_menu.addAction(dashboard_action)

        editor_action = QAction("Program Editor", self)
        editor_action.setShortcut("Ctrl+2")
        editor_action.setEnabled(False)  # this window IS the program editor
        window_menu.addAction(editor_action)

        # same idea as the dashboard's status bar (dashboard.py), but that's
        # a plain QWidget so it builds its own QStatusBar into its layout -
        # this window is a QMainWindow, which docks one below the central
        # widget (bottom row included) natively, full width, for free
        self.status_bar = QStatusBar()
        self.status_bar.setSizeGripEnabled(False)
        self.status_bar.showMessage("Select a keygroup")
        self.setStatusBar(self.status_bar)

        self.program_list.currentItemChanged.connect(self._on_program_selected)
        self.program_list.itemClicked.connect(
            lambda item: self.detail_stack.setCurrentIndex(0)
        )
        self.keygroup_list.currentItemChanged.connect(self._on_keygroup_selected)
        self.keygroup_list.itemClicked.connect(
            lambda item: self.detail_stack.setCurrentIndex(1)
        )
        self._program_loader = ProgramListLoader(self._bridge)
        self._program_loader.programs_loaded.connect(self._on_programs_loaded)
        self._program_loader.load_failed.connect(self._on_program_load_failed)
        self._program_loader.start()

        # enable knobs and wire their (debounced) writes
        self.cutoff_knob.setEnabled(True)
        self._wire_knob_write(
            self.cutoff_knob,
            "FILFRQ",
            "keygroup",
            keygroup_index_getter=self.keygroup_list.currentRow,
        )
        self.resonance_knob.setEnabled(True)
        self._wire_knob_write(
            self.resonance_knob,
            "FILQ",
            "keygroup",
            keygroup_index_getter=self.keygroup_list.currentRow,
        )
        self.note_lo_spinbox.setEnabled(True)
        self.note_lo_spinbox.valueChanged.connect(self._on_note_range_changed)
        self.note_lo_spinbox.editingFinished.connect(self._commit_note_range)
        self.note_hi_spinbox.setEnabled(True)
        self.note_hi_spinbox.valueChanged.connect(self._on_note_range_changed)
        self.note_hi_spinbox.editingFinished.connect(self._commit_note_range)
        self.pan_knob.setEnabled(True)
        self._wire_knob_write(self.pan_knob, "PANPOS", "program")
        self.lfo_rate_knob.setEnabled(True)
        self._wire_knob_write(self.lfo_rate_knob, "LFORAT", "program")
        self.lfo_depth_knob.setEnabled(True)
        self._wire_knob_write(self.lfo_depth_knob, "LFODEP", "program")
        self.lfo_delay_knob.setEnabled(True)
        self._wire_knob_write(self.lfo_delay_knob, "LFODEL", "program")
        for knob in (
            self.attack1_knob,
            self.decay1_knob,
            self.sustain1_knob,
            self.release1_knob,
            *self._env2_rate_knobs,
            *self._env2_level_knobs,
        ):
            knob.setEnabled(True)

    def _on_programs_loaded(self, programs):
        self.program_list.addItems(programs)
        self._sample_loader = SampleListLoader(self._bridge)
        self._sample_loader.samples_loaded.connect(self._on_samples_loaded)
        self._sample_loader.load_failed.connect(
            lambda e: self.status_bar.showMessage(f"Couldn't load samples: {e}")
        )
        self._sample_loader.start()

        # a part can only be assigned a program that actually exists -
        # populate all 16 combos with the same name list now that it's in
        for combo in self._multi_program_combos:
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(programs)
            combo.setEnabled(True)
            combo.blockSignals(False)
        self._refresh_multi_parts()

    def _on_program_load_failed(self, error_message):
        self.status_bar.showMessage(f"Couldn't load programs: {error_message}")

    def _refresh_multi_parts(self, *, show_confirmation=False):
        self._multi_refresh_in_progress = show_confirmation
        if hasattr(self, "_multi_parts_loader"):
            try:
                self._multi_parts_loader.parts_loaded.disconnect()
            except RuntimeError:
                pass
        self._multi_parts_loader = MultiPartsLoader(self._bridge)
        self._multi_parts_loader.parts_loaded.connect(self._on_multi_parts_loaded)
        self._multi_parts_loader.load_failed.connect(self._on_multi_load_failed)
        self._multi_parts_loader.start()

    def _on_multi_parts_loaded(self, parts):
        for part_index, (program_name, channel) in enumerate(parts):
            program_combo = self._multi_program_combos[part_index]
            channel_combo = self._multi_channel_combos[part_index]

            program_combo.blockSignals(True)
            program_combo.setCurrentIndex(program_combo.findText(program_name))
            program_combo.blockSignals(False)

            channel_combo.blockSignals(True)
            channel_index = channel_combo.findData(channel)
            channel_combo.setCurrentIndex(channel_index if channel_index >= 0 else 0)
            channel_combo.blockSignals(False)

        if self._multi_refresh_in_progress:
            self._multi_refresh_in_progress = False
            self.status_bar.showMessage("Multi parts refreshed from hardware")

    def _on_multi_load_failed(self, error_message):
        self._multi_refresh_in_progress = False
        self.status_bar.showMessage(f"Couldn't load multi: {error_message}")

    def _refresh_from_hardware(self):
        # re-fetches the current program's keygroup list/ranges and
        # program-level knobs (Pan/LFO/Polyphony) from the sampler, so
        # changes made on the hardware's own front panel don't leave this
        # window showing stale values. Restores whatever the user was
        # looking at (which keygroup, which zone tab, program vs keygroup
        # page) rather than resetting the view. Also always refreshes the
        # Multis tab's 16 parts, independent of program selection.
        self._refresh_multi_parts(show_confirmation=True)

        program_index = self.program_list.currentRow()
        if program_index < 0:
            return  # no program selected yet - nothing else to refresh

        self.status_bar.showMessage("Refreshing from hardware…")
        self._refresh_in_progress = True
        self._pending_restore_state = {
            "keygroup_index": self.keygroup_list.currentRow(),
            "stack_index": self.detail_stack.currentIndex(),
            "zone_index": self._zone_button_group.checkedId(),
        }

        if hasattr(self, "_keygroup_loader"):
            try:
                self._keygroup_loader.keygroups_loaded.disconnect()
            except RuntimeError:
                pass

        self._keygroup_loader = KeygroupLoader(self._bridge, program_index)
        self._keygroup_loader.keygroups_loaded.connect(self._on_keygroups_loaded)
        self._keygroup_loader.load_failed.connect(self._on_load_failed)
        self._keygroup_loader.start()

    def _on_program_selected(self, current, previous):
        self.keygroup_list.clear()
        self._keygroup_ranges = []
        self.keygroup_range_bar.set_ranges([])
        self.status_bar.showMessage("Select a keygroup")
        self.detail_stack.setCurrentIndex(0)
        if current is None:
            return
        program_index = self.program_list.currentRow()

        # disconnect previous loader to avoid stale signals firing
        if hasattr(self, "_keygroup_loader"):
            try:
                self._keygroup_loader.keygroups_loaded.disconnect()
            except RuntimeError:
                pass

        self._keygroup_loader = KeygroupLoader(self._bridge, program_index)
        self._keygroup_loader.keygroups_loaded.connect(self._on_keygroups_loaded)
        self._keygroup_loader.load_failed.connect(self._on_load_failed)
        self._keygroup_loader.start()

    def _on_keygroups_loaded(self, program_index, keygroup_ranges, program_values):
        # ignore result for program the user has already clicked away from
        if program_index != self.program_list.currentRow():
            return
        # idempotent even though _on_program_selected already clears this -
        # _refresh_from_hardware() reaches this same handler without going
        # through _on_program_selected first
        self.keygroup_list.clear()
        for i, (lo, hi) in enumerate(keygroup_ranges):
            self._add_keygroup_row(i, lo, hi)
        self._keygroup_ranges = [list(r) for r in keygroup_ranges]
        self.keygroup_range_bar.set_ranges(self._keygroup_ranges)
        # blockSignals during every one of these loaded-from-hardware
        # setValue() calls - valueChanged now also schedules a debounced
        # write (see _wire_knob_write), so an unblocked setValue() here
        # would schedule a write that just echoes the value straight back
        # to the hardware a moment later. The value label and graph/other
        # side effects are set explicitly right here instead of relying on
        # the (now blocked) valueChanged connections.
        self.pan_knob.blockSignals(True)
        self.pan_knob.setValue(program_values["PANPOS"])
        self.pan_knob.blockSignals(False)
        self.pan_value_label.setText(str(program_values["PANPOS"]))
        self.lfo_rate_knob.blockSignals(True)
        self.lfo_rate_knob.setValue(program_values["LFORAT"])
        self.lfo_rate_knob.blockSignals(False)
        self.lfo_rate_value_label.setText(str(program_values["LFORAT"]))
        self.lfo_depth_knob.blockSignals(True)
        self.lfo_depth_knob.setValue(program_values["LFODEP"])
        self.lfo_depth_knob.blockSignals(False)
        self.lfo_depth_value_label.setText(str(program_values["LFODEP"]))
        self.lfo_delay_knob.blockSignals(True)
        self.lfo_delay_knob.setValue(program_values["LFODEL"])
        self.lfo_delay_knob.blockSignals(False)
        self.lfo_delay_value_label.setText(str(program_values["LFODEL"]))
        self.lfo_shape_combo.blockSignals(True)
        self.lfo_shape_combo.setCurrentIndex(program_values["LFO1WAVE"])
        self.lfo_shape_combo.blockSignals(False)
        self.polyph_combo.blockSignals(True)
        self.polyph_combo.setCurrentIndex(
            self.polyph_combo.findData(program_values["POLYPH"])
        )
        self.polyph_combo.blockSignals(False)

        # only ever set by _refresh_from_hardware() - restores whatever the
        # user was looking at before the refresh (a plain program selection
        # never sets this, so this is a no-op on the normal load path)
        restore = self._pending_restore_state
        self._pending_restore_state = None
        if restore is not None:
            keygroup_index = restore["keygroup_index"]
            if 0 <= keygroup_index < self.keygroup_list.count():
                self.keygroup_list.setCurrentRow(keygroup_index)
                self.detail_stack.setCurrentIndex(restore["stack_index"])
                zone_index = restore["zone_index"]
                if zone_index >= 0:
                    self._zone_button_group.button(zone_index).setChecked(True)
                    self._zone_stack.setCurrentIndex(zone_index)

        if self._refresh_in_progress:
            self._refresh_in_progress = False
            self.status_bar.showMessage("Refreshed from hardware")

    def _on_load_failed(self, program_index, error_message):
        self._refresh_in_progress = False
        self._pending_restore_state = None
        if program_index != self.program_list.currentRow():
            return
        self.status_bar.showMessage(f"Couldn't load keygroups: {error_message}")

    def _on_keygroup_selected(self, current, previous):
        if current is None:
            return
        self._zone_stack.setCurrentIndex(0)
        self._zone_button_group.button(0).setChecked(True)
        self.detail_stack.setCurrentIndex(1)
        program_index = self.program_list.currentRow()
        keygroup_index = self.keygroup_list.currentRow()

        if hasattr(self, "_detail_loader"):
            try:
                self._detail_loader.detail_loaded.disconnect()
            except RuntimeError:
                pass  # already disconencted

        self._detail_loader = KeygroupDetailLoader(
            self._bridge, program_index, keygroup_index
        )
        self._detail_loader.detail_loaded.connect(self._on_detail_loaded)
        self._detail_loader.load_failed.connect(self._on_detail_load_failed)
        self._detail_loader.start()

    def _on_detail_loaded(self, program_index, keygroup_index, values):
        if (
            program_index != self.program_list.currentRow()
            or keygroup_index != self.keygroup_list.currentRow()
        ):
            return  # stale result from selection the user has already moved past
        self.note_lo_spinbox.blockSignals(True)
        self.note_lo_spinbox.setValue(values["LONOTE"])
        self.note_lo_spinbox.blockSignals(False)
        self.note_hi_spinbox.blockSignals(True)
        self.note_hi_spinbox.setValue(values["HINOTE"])
        self.note_hi_spinbox.blockSignals(False)
        # blockSignals during every loaded-from-hardware setValue() call -
        # valueChanged now also schedules a debounced write (see
        # _wire_knob_write), so leaving these unblocked would schedule a
        # write that echoes the just-loaded value straight back to the
        # hardware. Value labels and the envelope graphs are updated
        # explicitly right here instead of relying on the (blocked)
        # valueChanged connections that normally do it.
        self.cutoff_knob.blockSignals(True)
        self.cutoff_knob.setValue(values["FILFRQ"])
        self.cutoff_knob.blockSignals(False)
        self.cutoff_value_label.setText(str(values["FILFRQ"]))
        self.resonance_knob.blockSignals(True)
        self.resonance_knob.setValue(values["FILQ"])
        self.resonance_knob.blockSignals(False)
        self.resonance_value_label.setText(str(values["FILQ"]))
        self.attack1_knob.blockSignals(True)
        self.attack1_knob.setValue(values["ATTAK1"])
        self.attack1_knob.blockSignals(False)
        self.attack1_value_label.setText(str(values["ATTAK1"]))
        self.decay1_knob.blockSignals(True)
        self.decay1_knob.setValue(values["DECAY1"])
        self.decay1_knob.blockSignals(False)
        self.decay1_value_label.setText(str(values["DECAY1"]))
        self.sustain1_knob.blockSignals(True)
        self.sustain1_knob.setValue(values["SUSTN1"])
        self.sustain1_knob.blockSignals(False)
        self.sustain1_value_label.setText(str(values["SUSTN1"]))
        self.release1_knob.blockSignals(True)
        self.release1_knob.setValue(values["RELSE1"])
        self.release1_knob.blockSignals(False)
        self.release1_value_label.setText(str(values["RELSE1"]))
        self.env1_graph.set_values(
            values["ATTAK1"], values["DECAY1"], values["SUSTN1"], values["RELSE1"]
        )

        env2_field_pairs = [
            ("ATTAK2", "ENV2L1"),
            ("ENV2R2", "ENV2L2"),
            ("DECAY2", "SUSTN2"),
            ("RELSE2", "ENV2L4"),
        ]
        for i, ((rate_field, level_field), rate_knob, level_knob) in enumerate(
            zip(env2_field_pairs, self._env2_rate_knobs, self._env2_level_knobs)
        ):
            rate_knob.blockSignals(True)
            rate_knob.setValue(values[rate_field])
            rate_knob.blockSignals(False)
            self._env2_rate_value_labels[i].setText(str(values[rate_field]))
            level_knob.blockSignals(True)
            level_knob.setValue(values[level_field])
            level_knob.blockSignals(False)
            self._env2_level_value_labels[i].setText(str(values[level_field]))
        self.env2_graph.set_values(
            values["ATTAK2"],
            values["ENV2L1"],
            values["ENV2R2"],
            values["ENV2L2"],
            values["DECAY2"],
            values["SUSTN2"],
            values["RELSE2"],
            values["ENV2L4"],
        )
        self._update_zone_panels(values)

    def _on_detail_load_failed(self, program_index, keygroup_index, error_message):
        if (
            program_index == self.program_list.currentRow()
            and keygroup_index == self.keygroup_list.currentRow()
        ):
            self.status_bar.showMessage(f"Couldn't load detail: {error_message}")

    def closeEvent(self, event):
        # runs regardless of how the window closes (close button, command + w, etc)
        loaders = [
            self._program_loader,
            getattr(self, "_keygroup_loader", None),
            getattr(self, "_detail_loader", None),
            getattr(self, "_sample_loader", None),
        ] + list(self._active_writers.values())

        for loader in loaders:
            if loader is not None and loader.isRunning():
                loader.wait()

        self._main_window.show()
        event.accept()

    def _build_knob_column(self, label_text, knob):
        name_label = QLabel(label_text)
        name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        name_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        value_label = QLabel("-")
        value_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        # value_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        column = QVBoxLayout()
        column.setSpacing(4)  # fixed gap, in pixels - never stretches
        column.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(knob, alignment=Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(value_label)

        knob.valueChanged.connect(lambda v: value_label.setText(str(v)))

        return column, value_label

    def _build_labeled_column(self, label_text, widget, extra_layout=None):
        name_label = QLabel(label_text)
        name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        name_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        column = QVBoxLayout()
        column.setSpacing(4)
        column.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(widget, alignment=Qt.AlignmentFlag.AlignHCenter)
        if extra_layout is not None:
            column.addLayout(extra_layout)
        column.addStretch()

        return column

    def _build_multis_tab(self):
        # the sampler holds exactly one resident multi - no list to choose
        # between, just its fixed 16 parts, each with an independent
        # program assignment and MIDI channel
        self._multi_program_combos = []
        self._multi_channel_combos = []

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(6)

        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        part_header = QLabel("<b>Part</b>")
        part_header.setFixedWidth(60)
        program_header = QLabel("<b>Program</b>")
        channel_header = QLabel("<b>Channel</b>")
        channel_header.setFixedWidth(90)
        header_row.addWidget(part_header)
        header_row.addWidget(program_header, stretch=1)
        header_row.addWidget(channel_header)
        layout.addLayout(header_row)

        for part_index in range(MultiPartsLoader.PART_COUNT):
            row = QHBoxLayout()
            row.setSpacing(10)

            part_label = QLabel(f"Part {part_index + 1}")
            part_label.setFixedWidth(60)

            program_combo = QComboBox()
            # populated once the program list loads - a part can only be
            # assigned a program that actually exists on the sampler
            program_combo.setEnabled(False)

            channel_combo = QComboBox()
            channel_combo.addItem("OMNI", 255)
            for channel in range(16):
                channel_combo.addItem(str(channel + 1), channel)
            channel_combo.setFixedWidth(90)

            row.addWidget(part_label)
            row.addWidget(program_combo, stretch=1)
            row.addWidget(channel_combo)
            layout.addLayout(row)

            self._multi_program_combos.append(program_combo)
            self._multi_channel_combos.append(channel_combo)

            program_combo.currentIndexChanged.connect(
                lambda program_index, z=part_index: self._on_multi_part_program_changed(
                    z, program_index
                )
            )
            channel_combo.currentIndexChanged.connect(
                lambda _, z=part_index: self._on_multi_part_channel_changed(z)
            )

        layout.addStretch()
        return page

    def _on_multi_part_channel_changed(self, part_index):
        channel = self._multi_channel_combos[part_index].currentData()
        self._schedule_write(
            "PMCHAN",
            "multipart",
            channel,
            index=part_index,
            debounce_key=f"multipart_channel_{part_index}",
        )

    def _on_multi_part_program_changed(self, part_index, program_index):
        if program_index < 0:
            return  # combo cleared/reset, not a real user selection
        program_name = self._multi_program_combos[part_index].currentText()
        channel = self._multi_channel_combos[part_index].currentData()
        # OMNI isn't a real wire value a Program Change can target - the
        # part still listens on every channel while OMNI, so any channel
        # reaches it; 0 is as good a choice as any
        send_channel = channel if channel != 255 else 0

        sender = ProgramChangeSender(
            self._bridge, part_index, program_index, program_name, send_channel
        )
        sender.change_sent.connect(
            lambda z, name: self.status_bar.showMessage(
                f"Part {z + 1}: sent Program Change for '{name}'"
            )
        )
        sender.send_failed.connect(
            lambda z, e: self.status_bar.showMessage(
                f"Part {z + 1}: couldn't send Program Change: {e}"
            )
        )
        self._active_writers[f"multipart_program_{part_index}"] = sender
        sender.start()

    def _add_keygroup_row(self, index, lo, hi):
        # colored swatch + range text, same row-widget approach as the
        # dashboard's queue/hardware panels - keeps each row's identity tied
        # to its keygroup_range_bar segment (same color, same order) rather
        # than color alone
        item = QListWidgetItem(self.keygroup_list)

        row_widget = QWidget()
        row_widget.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(8, 6, 8, 6)
        row_layout.setSpacing(8)

        swatch = QLabel()
        swatch.setFixedSize(10, 10)
        swatch.setStyleSheet(
            f"background-color: {keygroup_color(index).name()}; border-radius: 2px;"
        )
        row_layout.addWidget(swatch)

        label = QLabel(
            f"Keygroup {index + 1}: {midi_note_to_name(lo)} - {midi_note_to_name(hi)}"
        )
        label.setObjectName("keygroupRangeLabel")
        row_layout.addWidget(label, stretch=1)

        item.setSizeHint(row_widget.sizeHint())
        self.keygroup_list.setItemWidget(item, row_widget)

    def _tune_offset_to_semitones(self, raw_value):
        return round(raw_value / 2.56) / 100

    def _semitones_to_tune_offset(self, semitones):
        return round(semitones * 100 * 2.56)

    # old sampler hardware can't keep up with a write per wheel-notch or
    # per drag-frame - a value is only actually sent once it's held still
    # for this long, unless a "the user is clearly done" signal
    # (sliderReleased/editingFinished) flushes it early
    _WRITE_DEBOUNCE_MS = 500

    def _schedule_write(
        self, param_name, region, value, *, keygroup_index=0, index=None, debounce_key=None
    ):
        # call on every CONTINUOUS change signal (valueChanged,
        # currentIndexChanged) - covers inputs like mouse-wheel scrolling
        # that change the value but never fire editingFinished/
        # sliderReleased, which used to leave the write pending forever.
        #
        # debounce_key defaults to param_name, which is only safe when at
        # most one "thing" using that field name can be mid-edit at once
        # (true for every keygroup-scoped field, since only one keygroup is
        # ever selected at a time). The Multis tab breaks that assumption -
        # all 16 parts share the field name PMCHAN, but are all editable
        # simultaneously - so its callers pass a per-part debounce_key to
        # keep each part's pending write from clobbering another's.
        key = debounce_key if debounce_key is not None else param_name
        if not hasattr(self, "_pending_writes"):
            self._pending_writes = {}
            self._write_timers = {}

        self._pending_writes[key] = (param_name, region, value, keygroup_index, index)

        timer = self._write_timers.get(key)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda k=key: self._flush_write(k))
            self._write_timers[key] = timer
        timer.start(self._WRITE_DEBOUNCE_MS)

    def _flush_write(self, debounce_key):
        # sends a pending debounced value immediately and cancels its timer
        # - wired to sliderReleased/editingFinished so a deliberate
        # drag-then-release or type-then-Enter stays instant rather than
        # also waiting out the debounce window. A no-op if nothing is
        # pending (e.g. a click that didn't actually change the value).
        #
        # NOTE: callers outside this file's own multi-part wiring can keep
        # passing a bare param_name here exactly as before - debounce_key
        # defaults to param_name in _schedule_write, so the two agree.
        timer = getattr(self, "_write_timers", {}).get(debounce_key)
        if timer is not None:
            timer.stop()
        pending = getattr(self, "_pending_writes", {}).pop(debounce_key, None)
        if pending is None:
            return
        param_name, region, value, keygroup_index, index = pending
        self._write_knob_value(
            param_name,
            region,
            value,
            keygroup_index=keygroup_index,
            index=index,
            writer_key=debounce_key,
        )

    def _wire_knob_write(self, knob, param_name, region, *, keygroup_index_getter=None):
        getter = keygroup_index_getter or (lambda: 0)
        knob.valueChanged.connect(
            lambda v: self._schedule_write(param_name, region, v, keygroup_index=getter())
        )
        knob.sliderReleased.connect(lambda: self._flush_write(param_name))

    def _wire_spinbox_write(
        self, spinbox, param_name, region, *, keygroup_index_getter=None, value_converter=None
    ):
        getter = keygroup_index_getter or (lambda: 0)
        converter = value_converter or (lambda v: v)
        spinbox.valueChanged.connect(
            lambda v: self._schedule_write(
                param_name, region, converter(v), keygroup_index=getter()
            )
        )
        spinbox.editingFinished.connect(lambda: self._flush_write(param_name))

    def _write_knob_value(
        self, param_name, region, value, *, keygroup_index=0, index=None, writer_key=None
    ):
        # index overrides the usual "whichever program is selected in the
        # Programs tab" target - needed for multipart writes, where the
        # thing being addressed is a part number (0-15), unrelated to
        # program_list's own selection
        program_index = self.program_list.currentRow() if index is None else index
        # writer_key overrides _active_writers' storage key (also
        # param_name by default) for the same reason _schedule_write takes
        # debounce_key - see its comment
        key = writer_key if writer_key is not None else param_name
        writer = ParameterWriter(
            self._bridge,
            param_name,
            region,
            program_index,
            value,
            keygroup_index=keygroup_index,
        )
        writer.write_succeeded.connect(
            lambda v: self.status_bar.showMessage(f"{param_name} → {v}")
        )
        writer.write_failed.connect(
            lambda e: self.status_bar.showMessage(f"Write failed ({param_name}): {e}")
        )
        # store writer per-key so it can't be garbage collected before the thread finishes
        self._active_writers[key] = writer
        writer.start()

    def _on_env1_knob_changed(self):
        # redraws the graph immediately as any ADSR knob is dragged - the
        # actual hardware write is throttled separately, on sliderReleased
        self.env1_graph.set_values(
            self.attack1_knob.value(),
            self.decay1_knob.value(),
            self.sustain1_knob.value(),
            self.release1_knob.value(),
        )

    def _on_env2_knob_changed(self):
        # same live-redraw idea as ENV1, for all 4 rate/level stage pairs
        stage_values = []
        for rate_knob, level_knob in zip(
            self._env2_rate_knobs, self._env2_level_knobs
        ):
            stage_values.append(rate_knob.value())
            stage_values.append(level_knob.value())
        self.env2_graph.set_values(*stage_values)

    def _on_note_range_changed(self):
        # low can't be raised past high (or high dropped past low) - the
        # bound that got pushed follows, same as the sampler is believed to
        # do on the front panel, so the UI never shows/sends an inverted range
        if self.sender() is self.note_lo_spinbox:
            if self.note_lo_spinbox.value() > self.note_hi_spinbox.value():
                self.note_hi_spinbox.blockSignals(True)
                self.note_hi_spinbox.setValue(self.note_lo_spinbox.value())
                self.note_hi_spinbox.blockSignals(False)
        elif self.sender() is self.note_hi_spinbox:
            if self.note_hi_spinbox.value() < self.note_lo_spinbox.value():
                self.note_lo_spinbox.blockSignals(True)
                self.note_lo_spinbox.setValue(self.note_hi_spinbox.value())
                self.note_lo_spinbox.blockSignals(False)

        # keeps the keygroup list's "Keygroup N: lo - hi" label in step while
        # the user edits, rather than only after the write round-trips back
        item = self.keygroup_list.currentItem()
        if item is None:
            return
        index = self.keygroup_list.currentRow()
        lo = self.note_lo_spinbox.value()
        hi = self.note_hi_spinbox.value()
        row_widget = self.keygroup_list.itemWidget(item)
        row_widget.findChild(QLabel, "keygroupRangeLabel").setText(
            f"Keygroup {index + 1}: {midi_note_to_name(lo)} - {midi_note_to_name(hi)}"
        )
        self._keygroup_ranges[index] = [lo, hi]
        self.keygroup_range_bar.set_ranges(self._keygroup_ranges)

        # both bounds scheduled together (not just the one the user
        # touched) - the pushed spinbox's own valueChanged is blocked
        # during the clamp above, so it never schedules a write of its own;
        # scheduling both here every time either one changes is what keeps
        # a pushed value from being left stale on the sampler
        keygroup_index = index
        self._schedule_write(
            "LONOTE", "keygroup", lo, keygroup_index=keygroup_index
        )
        self._schedule_write(
            "HINOTE", "keygroup", hi, keygroup_index=keygroup_index
        )

    def _commit_note_range(self):
        # flushes both bounds immediately instead of waiting out the
        # debounce window - pressing Enter or clicking away is a clear
        # "done editing" signal, same as sliderReleased for knobs
        self._flush_write("LONOTE")
        self._flush_write("HINOTE")

    def _on_samples_loaded(self, samples):
        self._sample_list = samples
        for combo in self._zone_combos:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("-")
            combo.addItems(samples)
            combo.setEnabled(True)
            combo.blockSignals(False)
        # wire write signal now that items exist - doing it here rather than
        # in __init__ avoids currentIndexChanged firing before the list is populated
        sname_fields = ["SNAME1", "SNAME2", "SNAME3", "SNAME4"]
        for zone_idx, (combo, field) in enumerate(zip(self._zone_combos, sname_fields)):
            combo.currentIndexChanged.connect(
                lambda _, f=field, z=zone_idx: self._on_zone_sample_changed(f, z)
            )

        if self.program_list.count() > 0:
            self.program_list.setCurrentRow(
                0
            )  # this is what triggers keygroup loading for the first program

    def _on_zone_sample_changed(self, field, zone_idx):
        text = self._zone_combos[zone_idx].currentText()
        sample_name = "" if text == "-" else text
        self._schedule_write(
            field,
            "keygroup",
            sample_name,
            keygroup_index=self.keygroup_list.currentRow(),
        )

    def _update_zone_panels(self, values):
        zone_field_map = [
            ("SNAME1", "LOVEL1", "HIVEL1", "VTUNO1", "VLOUD1", "VPANO1"),
            ("SNAME2", "LOVEL2", "HIVEL2", "VTUNO2", "VLOUD2", "VPANO2"),
            ("SNAME3", "LOVEL3", "HIVEL3", "VTUNO3", "VLOUD3", "VPANO3"),
            ("SNAME4", "LOVEL4", "HIVEL4", "VTUNO4", "VLOUD4", "VPANO4"),
        ]
        for z, (sname, lovel, hivel, vtuno, vloud, vpano) in enumerate(zone_field_map):
            combo = self._zone_combos[z]
            name = values.get(sname, "").strip()
            idx = combo.findText(name)
            combo.blockSignals(True)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

            for widget, field in [
                (self._zone_vel_lo[z], lovel),
                (self._zone_vel_hi[z], hivel),
            ]:
                widget.blockSignals(True)
                widget.setValue(values.get(field, 0))
                widget.blockSignals(False)

            loud_val = values.get(vloud, 0)
            self._zone_loudness[z].blockSignals(True)
            self._zone_loudness[z].setValue(loud_val)
            self._zone_loudness_labels[z].setText(str(loud_val))
            self._zone_loudness[z].blockSignals(False)

            pan_val = values.get(vpano, 0)
            self._zone_pan[z].blockSignals(True)
            self._zone_pan[z].setValue(pan_val)
            self._zone_pan_labels[z].setText(str(pan_val))
            self._zone_pan[z].blockSignals(False)


if __name__ == "__main__":
    # standalone launch for UI work away from the hardware sampler - always
    # against the demo bridge, never a real port. For the real thing, go
    # through main.py, which wires the dashboard's "Program Editor" button to
    # program_editor_bridge.connect() instead.
    from PySide6.QtWidgets import QApplication
    from s3ked.demo import DemoBridge
    from ui import theme

    class _StandaloneHost:
        # ProgramEditorWindow.closeEvent() calls main_window.show() to bring
        # the dashboard back - there is none here, so close the app instead
        def show(self):
            QApplication.instance().quit()

    app = QApplication(sys.argv)
    theme.apply_to_app(app)
    window = ProgramEditorWindow(_StandaloneHost(), bridge=DemoBridge())
    window.show()
    sys.exit(app.exec())
