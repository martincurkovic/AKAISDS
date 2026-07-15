import sys
from PySide6.QtWidgets import QApplication, QMainWindow
from components import TransferDashboard
from midi_manager import MidiManager
from controller import SamplerController


class ApplicationWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AKAI SDS")
        self.setMinimumSize(750, 480)

        # owns the real mido ports for the app's lifetime
        self.midi_manager = MidiManager()

        self.sampler_controller = SamplerController(self.midi_manager)

        # instantiate custom UI layout components
        self.dashboard_view = TransferDashboard(
            self.sampler_controller, self.midi_manager
        )

        # mount layout into core central display panel area
        self.setCentralWidget(self.dashboard_view)

    def closeEvent(self, event):
        # release MIDI ports cleanly so mido's backend doesn't hang around
        self.midi_manager.close_input()
        self.midi_manager.close_output()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ApplicationWindow()
    window.show()
    sys.exit(app.exec())
