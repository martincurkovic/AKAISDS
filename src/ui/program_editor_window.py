import os
import sys

if __name__ == "__main__":
    # running this file directly (not through main.py) puts src/ui on
    # sys.path, not src/ - so the ui./core. imports below would otherwise
    # fail with "No module named 'ui'"
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtGui import Qt
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
)
from ui.knob import Knob
from ui.note_spinbox import NoteSpinBox
from ui.envelope_graph import ADSREnvelopeGraph, Envelope2Graph
from ui.keygroup_range_bar import KeygroupRangeBar, keygroup_color
from core.midi_notes import midi_note_to_name
from core.program_editor_bridge import (
    KeygroupLoader,
    ParameterWriter,
    ProgramListLoader,
    KeygroupDetailLoader,
    SampleListLoader,
)


class ProgramEditorWindow(QMainWindow):
    def __init__(self, main_window, bridge):
        super().__init__()
        self.setWindowTitle("AKAISDS - Program Editor")
        self.setMinimumSize(800, 500)
        self._active_writers = {}
        self._sample_list = []
        self._keygroup_ranges = []  # [lo, hi] per keygroup - mirrors keygroup_range_bar

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

        self.detail_label = QLabel("Select a keygroup")

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

        env1_column = self._build_labeled_column("ENV1", self.env1_graph)
        env2_column = self._build_labeled_column("ENV2", self.env2_graph)

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
            vel_lo.editingFinished.connect(
                lambda z=zone_idx, f=lovel: self._write_knob_value(
                    f,
                    "keygroup",
                    self._zone_vel_lo[z].value(),
                    keygroup_index=self.keygroup_list.currentRow(),
                )
            )
            vel_hi.editingFinished.connect(
                lambda z=zone_idx, f=hivel: self._write_knob_value(
                    f,
                    "keygroup",
                    self._zone_vel_hi[z].value(),
                    keygroup_index=self.keygroup_list.currentRow(),
                )
            )
            tune.editingFinished.connect(
                lambda z=zone_idx, f=vtuno: self._write_knob_value(
                    f,
                    "keygroup",
                    self._semitones_to_tune_offset(self._zone_tune[z].value()),
                    keygroup_index=self.keygroup_list.currentRow(),
                )
            )
            loud_knob.sliderReleased.connect(
                lambda z=zone_idx, f=vloud: self._write_knob_value(
                    f,
                    "keygroup",
                    self._zone_loudness[z].value(),
                    keygroup_index=self.keygroup_list.currentRow(),
                )
            )
            pan_knob.sliderReleased.connect(
                lambda z=zone_idx, f=vpano: self._write_knob_value(
                    f,
                    "keygroup",
                    self._zone_pan[z].value(),
                    keygroup_index=self.keygroup_list.currentRow(),
                )
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
        detail_container_layout.addWidget(self.detail_label)
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
            lambda i: self._write_knob_value("LFO1WAVE", "program", i)
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

        self.polyph_spinbox = QSpinBox()
        self.polyph_spinbox.setRange(1, 32)
        self.polyph_spinbox.setEnabled(True)
        self.polyph_spinbox.editingFinished.connect(self._on_polyph_changed)
        polyph_label = QLabel("Polyphony")
        polyph_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        polyph_column = QVBoxLayout()
        polyph_column.setSpacing(4)
        polyph_column.addWidget(polyph_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        polyph_column.addWidget(self.polyph_spinbox)

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
        self.polyph_spinbox.setMaximumWidth(80)
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

        content_layout = QHBoxLayout()
        content_layout.addWidget(programs_container)
        content_layout.addWidget(keygroups_container)
        content_layout.addWidget(self.detail_stack, stretch=1)

        main_layout = QVBoxLayout()
        main_layout.addLayout(content_layout)
        main_layout.addWidget(close_button)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

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

        # enable knobs and wire sliderReleased
        self.cutoff_knob.setEnabled(True)
        self.cutoff_knob.sliderReleased.connect(
            lambda: self._write_knob_value(
                "FILFRQ",
                "keygroup",
                self.cutoff_knob.value(),
                keygroup_index=self.keygroup_list.currentRow(),
            )
        )
        self.resonance_knob.setEnabled(True)
        self.resonance_knob.sliderReleased.connect(
            lambda: self._write_knob_value(
                "FILQ",
                "keygroup",
                self.resonance_knob.value(),
                keygroup_index=self.keygroup_list.currentRow(),
            )
        )
        self.note_lo_spinbox.setEnabled(True)
        self.note_lo_spinbox.valueChanged.connect(self._on_note_range_changed)
        self.note_lo_spinbox.editingFinished.connect(self._commit_note_range)
        self.note_hi_spinbox.setEnabled(True)
        self.note_hi_spinbox.valueChanged.connect(self._on_note_range_changed)
        self.note_hi_spinbox.editingFinished.connect(self._commit_note_range)
        self.pan_knob.setEnabled(True)
        self.pan_knob.sliderReleased.connect(
            lambda: self._write_knob_value("PANPOS", "program", self.pan_knob.value())
        )
        self.lfo_rate_knob.setEnabled(True)
        self.lfo_rate_knob.sliderReleased.connect(
            lambda: self._write_knob_value(
                "LFORAT", "program", self.lfo_rate_knob.value()
            )
        )
        self.lfo_depth_knob.setEnabled(True)
        self.lfo_depth_knob.sliderReleased.connect(
            lambda: self._write_knob_value(
                "LFODEP", "program", self.lfo_depth_knob.value()
            )
        )
        self.lfo_delay_knob.setEnabled(True)
        self.lfo_delay_knob.sliderReleased.connect(
            lambda: self._write_knob_value(
                "LFODEL", "program", self.lfo_delay_knob.value()
            )
        )

    def _on_programs_loaded(self, programs):
        self.program_list.addItems(programs)
        self._sample_loader = SampleListLoader(self._bridge)
        self._sample_loader.samples_loaded.connect(self._on_samples_loaded)
        self._sample_loader.load_failed.connect(
            lambda e: self.detail_label.setText(f"Couldn't load samples: {e}")
        )
        self._sample_loader.start()

    def _on_program_load_failed(self, error_message):
        self.detail_label.setText(f"Couldn't load programs: {error_message}")

    def _on_program_selected(self, current, previous):
        self.keygroup_list.clear()
        self._keygroup_ranges = []
        self.keygroup_range_bar.set_ranges([])
        self.detail_label.setText("Select a keygroup")
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
        for i, (lo, hi) in enumerate(keygroup_ranges):
            self._add_keygroup_row(i, lo, hi)
        self._keygroup_ranges = [list(r) for r in keygroup_ranges]
        self.keygroup_range_bar.set_ranges(self._keygroup_ranges)
        self.pan_knob.setValue(program_values["PANPOS"])
        self.pan_value_label.setText(str(program_values["PANPOS"]))
        self.lfo_rate_knob.setValue(program_values["LFORAT"])
        self.lfo_rate_value_label.setText(str(program_values["LFORAT"]))
        self.lfo_depth_knob.setValue(program_values["LFODEP"])
        self.lfo_depth_value_label.setText(str(program_values["LFODEP"]))
        self.lfo_delay_knob.setValue(program_values["LFODEL"])
        self.lfo_delay_value_label.setText(str(program_values["LFODEL"]))
        self.lfo_shape_combo.blockSignals(True)
        self.lfo_shape_combo.setCurrentIndex(program_values["LFO1WAVE"])
        self.lfo_shape_combo.blockSignals(False)
        self.polyph_spinbox.blockSignals(True)
        self.polyph_spinbox.setValue(program_values["POLYPH"])
        self.polyph_spinbox.blockSignals(False)

    def _on_load_failed(self, program_index, error_message):
        if program_index != self.program_list.currentRow():
            return
        self.detail_label.setText(f"Couldn't load keygroups: {error_message}")

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
        self.cutoff_knob.setValue(values["FILFRQ"])
        self.cutoff_value_label.setText(str(values["FILFRQ"]))
        self.resonance_knob.setValue(values["FILQ"])
        self.resonance_value_label.setText(str(values["FILQ"]))
        self.env1_graph.set_values(
            values["ATTAK1"], values["DECAY1"], values["SUSTN1"], values["RELSE1"]
        )
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
            self.detail_label.setText(f"Couldn't load detail: {error_message}")

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

    def _build_labeled_column(self, label_text, widget):
        name_label = QLabel(label_text)
        name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        name_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        column = QVBoxLayout()
        column.setSpacing(4)
        column.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(widget, alignment=Qt.AlignmentFlag.AlignHCenter)
        column.addStretch()

        return column

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

    def _write_knob_value(self, param_name, region, value, *, keygroup_index=0):
        program_index = self.program_list.currentRow()
        writer = ParameterWriter(
            self._bridge,
            param_name,
            region,
            program_index,
            value,
            keygroup_index=keygroup_index,
        )
        writer.write_succeeded.connect(
            lambda v: self.detail_label.setText(f"{param_name} → {v}")
        )
        writer.write_failed.connect(
            lambda e: self.detail_label.setText(f"Write failed ({param_name}): {e}")
        )
        # store writer per-param so it can't be garbage collected before the thread finishes
        self._active_writers[param_name] = writer
        writer.start()

    def _on_polyph_changed(self):
        self._write_knob_value("POLYPH", "program", self.polyph_spinbox.value())

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

    def _commit_note_range(self):
        # both bounds are written together (not just the one the user
        # touched), so a pushed value that this round's push-clamp changed
        # programmatically never gets left stale on the sampler
        keygroup_index = self.keygroup_list.currentRow()
        self._write_knob_value(
            "LONOTE", "keygroup", self.note_lo_spinbox.value(),
            keygroup_index=keygroup_index,
        )
        self._write_knob_value(
            "HINOTE", "keygroup", self.note_hi_spinbox.value(),
            keygroup_index=keygroup_index,
        )

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
        self._write_knob_value(
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
