from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel
from core import app_config
from ui.qt_helpers import widen_popup_to_fit_items


class MidiSettingsDialog(QDialog):
    def __init__(self, midi_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MIDI Settings")
        self.midi_manager = midi_manager

        layout = QFormLayout(self)

        self.combo_input = QComboBox()
        self.combo_output = QComboBox()
        self.combo_input.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.combo_output.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._populate_ports()

        layout.addRow(QLabel("MIDI Input:"), self.combo_input)
        layout.addRow(QLabel("MIDI Output:"), self.combo_output)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._apply_and_close)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _populate_ports(self):
        self.combo_input.addItem("(None)", None)
        for name in self.midi_manager.list_inputs():
            self.combo_input.addItem(name, name)

        self.combo_output.addItem("(None)", None)
        for name in self.midi_manager.list_outputs():
            self.combo_output.addItem(name, name)

        if self.midi_manager.input_name:
            idx = self.combo_input.findData(self.midi_manager.input_name)
            if idx >= 0:
                self.combo_input.setCurrentIndex(idx)

        if self.midi_manager.output_name:
            idx = self.combo_output.findData(self.midi_manager.output_name)
            if idx >= 0:
                self.combo_output.setCurrentIndex(idx)

        widen_popup_to_fit_items(self.combo_input)
        widen_popup_to_fit_items(self.combo_output)

    def _apply_and_close(self):
        input_name = self.combo_input.currentData()
        output_name = self.combo_output.currentData()

        self.midi_manager.open_input(input_name)
        self.midi_manager.open_output(output_name)

        # remember these for next launch
        app_config.save_ports(input_name, output_name)

        self.accept()
