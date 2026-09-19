from PySide6.QtGui import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QComboBox,
    QWidget,
    QStackedWidget,
    QDoubleSpinBox,
    QSpinBox,
)
from ui.knob import Knob
from ui.envelope_graph import ADSREnvelopeGraph, Envelope2Graph
from core.program_editor_bridge import (
    KeygroupLoader,
    ParameterWriter,
    ProgramListLoader,
    KeygroupDetailLoader,
)
import s3k.params as p


class ProgramEditorWindow(QMainWindow):
    def __init__(self, main_window, bridge):
        super().__init__()
        self.setWindowTitle("AKAISDS - Program Editor")
        self.setMinimumSize(800, 500)
        self._active_writers = {}

        self._main_window = main_window
        self._bridge = bridge

        # placeholder - real program, keygroup panels come later
        self.program_list = QListWidget()
        self.program_list.setFixedWidth(160)

        self.keygroup_list = QListWidget()
        self.keygroup_list.setFixedWidth(160)

        self.detail_label = QLabel("Loading programs...")

        self.cutoff_knob = Knob()
        self.cutoff_knob.setRange(0, 99)
        self.cutoff_knob.setFixedSize(80, 80)
        self.resonance_knob = Knob()
        self.resonance_knob.setRange(0, 15)
        self.resonance_knob.setFixedSize(80, 80)

        self.env1_graph = ADSREnvelopeGraph()
        self.env1_graph.setFixedSize(160, 60)
        self.env2_graph = Envelope2Graph()
        self.env2_graph.setFixedSize(160, 60)

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

        self.zone1_tune_spinbox = QDoubleSpinBox()
        self.zone1_tune_spinbox.setRange(-50.00, 50.00)
        self.zone1_tune_spinbox.setSingleStep(0.01)
        self.zone1_tune_spinbox.setDecimals(2)
        self.zone1_tune_spinbox.setSuffix(" st")
        self.zone1_tune_spinbox.setEnabled(True)
        self.zone1_tune_spinbox.editingFinished.connect(self._on_zone1_tune_changed)
        zone1_tune_label = QLabel("Zone 1 tune")
        zone1_tune_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        zone1_tune_column = QVBoxLayout()
        zone1_tune_column.setSpacing(4)
        zone1_tune_column.addWidget(
            zone1_tune_label, alignment=Qt.AlignmentFlag.AlignHCenter
        )
        zone1_tune_column.addWidget(self.zone1_tune_spinbox)

        knobs_layout = QHBoxLayout()
        knobs_layout.addLayout(cutoff_column)
        knobs_layout.addLayout(resonance_column)
        knobs_layout.addLayout(zone1_tune_column)

        detail_container_layout = QVBoxLayout()
        detail_container_layout.addWidget(self.detail_label)
        detail_container_layout.addLayout(knobs_layout)
        detail_container_layout.addLayout(envelopes_layout)
        detail_container = QWidget()
        detail_container.setLayout(detail_container_layout)

        self.pan_knob = Knob()
        self.pan_knob.setRange(-50, 50)
        self.pan_knob.setFixedSize(80, 80)
        pan_column, self.pan_value_label = self._build_knob_column("Pan", self.pan_knob)

        self.lfo_rate_knob = Knob()
        self.lfo_rate_knob.setRange(0, 99)
        self.lfo_rate_knob.setFixedSize(80, 80)
        self.lfo_depth_knob = Knob()
        self.lfo_depth_knob.setRange(0, 99)
        self.lfo_depth_knob.setFixedSize(80, 80)
        self.lfo_delay_knob = Knob()
        self.lfo_delay_knob.setRange(0, 99)
        self.lfo_delay_knob.setFixedSize(80, 80)

        self.lfo_shape_combo = QComboBox()
        self.lfo_shape_combo.addItems(["Triangle", "Sawtooth", "Square", "Random"])
        self.lfo_shape_combo.setEnabled(True)
        self.lfo_shape_combo.currentIndexChanged.connect(self._on_lfo_shape_changed)

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

        pan_page = QWidget()
        pan_page_layout = QVBoxLayout()
        pan_page_layout.addLayout(pan_column)
        pan_page_layout.addLayout(lfo_rate_column)
        pan_page_layout.addLayout(lfo_depth_column)
        pan_page_layout.addLayout(lfo_delay_column)
        pan_page_layout.addLayout(lfo_shape_column)
        pan_page_layout.addLayout(polyph_column)
        pan_page.setLayout(pan_page_layout)

        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(pan_page)
        self.detail_stack.addWidget(detail_container)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)

        content_layout = QHBoxLayout()
        content_layout.addWidget(self.program_list)
        content_layout.addWidget(self.keygroup_list)
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
        self.program_list.setCurrentRow(
            0
        )  # this is what triggers keygroup loading for the first program

    def _on_program_load_failed(self, error_message):
        self.detail_label.setText(f"Couldn't load programs: {error_message}")

    def _on_program_selected(self, current, previous):
        self.keygroup_list.clear()
        self.detail_label.setText("Select a keygroup")
        self.detail_stack.setCurrentIndex(0)
        if current is None:
            return
        program_index = self.program_list.currentRow()
        self._loader = KeygroupLoader(self._bridge, program_index)
        self._loader.keygroups_loaded.connect(self._on_keygroups_loaded)
        self._loader.load_failed.connect(self._on_load_failed)
        self._loader.start()

    def _on_keygroups_loaded(self, program_index, keygroup_ranges, program_values):
        # ignore result for program the user has already clicked away from
        if program_index != self.program_list.currentRow():
            return
        self.keygroup_list.addItems(keygroup_ranges)
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
        self.detail_stack.setCurrentIndex(1)
        program_index = self.program_list.currentRow()
        keygroup_index = self.keygroup_list.currentRow()

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
        self.zone1_tune_spinbox.blockSignals(True)
        self.zone1_tune_spinbox.setValue(
            self._tune_offset_to_semitones(values["VTUNO1"])
        )
        self.zone1_tune_spinbox.blockSignals(False)

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
            getattr(self, "_loader", None),
            getattr(self, "_detail_loader", None),
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

    def _tune_offset_to_semitones(self, raw_value):
        return round(raw_value / 2.56) / 100

    def _semitones_to_tune_offset(self, semitones):
        return round(semitones * 100 * 2.56)

    def _on_lfo_shape_changed(self, new_index):
        self._pending_lfo_shape_writer = ParameterWriter(
            self._bridge,
            "LFO1WAVE",
            "program",
            self.program_list.currentRow(),
            new_index,
        )
        self._pending_lfo_shape_writer.write_succeeded.connect(
            self._on_lfo_shape_write_succeeded
        )
        self._pending_lfo_shape_writer.write_failed.connect(
            self._on_lfo_shape_write_failed
        )
        self._pending_lfo_shape_writer.start()

    def _on_lfo_shape_write_succeeded(self, new_value):
        self.detail_label.setText(
            f"LFO shape updated ({self.lfo_shape_combo.currentText()})"
        )

    def _on_lfo_shape_write_failed(self, error_message):
        # revert combobox to whatever the sampler actually has, since the ui has diverged from hardware
        self.detail_label.setText(f"Write failed: {error_message}")
        program_index = self.program_list.currentRow()
        self.lfo_shape_combo.blockSignals(True)
        correct_value = self._bridge.get_parameter(
            p.lookup("LFO1WAVE", "program"), program_index
        )
        self.lfo_shape_combo.setCurrentIndex(correct_value)
        self.lfo_shape_combo.blockSignals(False)

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

    def _on_zone1_tune_changed(self):
        raw_value = self._semitones_to_tune_offset(self.zone1_tune_spinbox.value())
        self._write_knob_value(
            "VTUNO1",
            "keygroup",
            raw_value,
            keygroup_index=self.keygroup_list.currentRow(),
        )

    def _on_polyph_changed(self):
        self._write_knob_value("POLYPH", "program", self.polyph_spinbox.value())
