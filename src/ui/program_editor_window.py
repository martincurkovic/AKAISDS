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
    QWidget,
    QStackedWidget,
)
from ui.knob import Knob
from core.program_editor_bridge import (
    KeygroupLoader,
    ProgramListLoader,
    KeygroupDetailLoader,
)


class ProgramEditorWindow(QMainWindow):
    def __init__(self, main_window, bridge):
        super().__init__()
        self.setWindowTitle("AKAISDS - Program Editor")
        self.setMinimumSize(800, 500)

        self._main_window = main_window
        self._bridge = bridge

        # placeholder - real program, keygroup panels come later
        self.program_list = QListWidget()
        self.program_list.setFixedWidth(160)

        self.keygroup_list = QListWidget()
        self.keygroup_list.setFixedWidth(160)
        self.keygroup_list.currentItemChanged.connect(self._on_keygroup_selected)

        self.detail_label = QLabel("Loading programs...")

        self.cutoff_knob = Knob()
        self.cutoff_knob.setRange(0, 99)
        self.cutoff_knob.setFixedSize(80, 80)
        self.cutoff_value_label = QLabel("-")
        self.resonance_knob = Knob()
        self.resonance_knob.setRange(0, 15)
        self.resonance_knob.setFixedSize(80, 80)
        self.resonance_value_label = QLabel("-")

        self.cutoff_knob = Knob()
        self.cutoff_knob.setRange(0, 99)
        self.cutoff_knob.setFixedSize(80, 80)
        self.resonance_knob = Knob()
        self.resonance_knob.setRange(0, 15)
        self.resonance_knob.setFixedSize(80, 80)

        cutoff_column, self.cutoff_value_label = self._build_knob_column(
            "Cutoff", self.cutoff_knob
        )
        resonance_column, self.resonance_value_label = self._build_knob_column(
            "Resonance", self.resonance_knob
        )

        knobs_layout = QHBoxLayout()
        knobs_layout.addLayout(cutoff_column)
        knobs_layout.addLayout(resonance_column)

        detail_container_layout = QVBoxLayout()
        detail_container_layout.addWidget(self.detail_label)
        detail_container_layout.addLayout(knobs_layout)
        detail_container = QWidget()
        detail_container.setLayout(detail_container_layout)

        self.pan_knob = Knob()
        self.pan_knob.setRange(-50, 50)
        self.pan_knob.setFixedSize(80, 80)
        pan_column, self.pan_value_label = self._build_knob_column("Pan", self.pan_knob)
        pan_page = QWidget()
        pan_page_layout = QVBoxLayout()
        pan_page_layout.addLayout(pan_column)
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

        self._program_loader = ProgramListLoader(self._bridge)
        self._program_loader.programs_loaded.connect(self._on_programs_loaded)
        self._program_loader.load_failed.connect(self._on_program_load_failed)
        self._program_loader.start()

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

    def _on_keygroups_loaded(self, program_index, keygroup_ranges, pan_value):
        # ignore result for program the user has already clicked away from
        if program_index != self.program_list.currentRow():
            return
        self.keygroup_list.addItems(keygroup_ranges)
        self.pan_knob.setValue(pan_value)
        self.pan_value_label.setText(str(pan_value))

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

    def _on_detail_load_failed(self, program_index, keygroup_index, error_message):
        if (
            program_index == self.program_list.currentRow()
            and keygroup_index == self.keygroup_list.currentRow()
        ):
            self.detail_label.setText(f"Couldn't load detail: {error_message}")

    def closeEvent(self, event):
        # runs regardless of how the window closes (close button, command + w, etc)
        for loader in (self._program_loader, getattr(self, "_loader", None)):
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
