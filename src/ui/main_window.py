from PySide6.QtWidgets import QMainWindow
from ui.dashboard import TransferDashboard
from core.midi_manager import MidiManager
from controller.sampler_controller import SamplerController


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
        # release midi ports cleanly so mido's backend doesnt hang around like a fart in a doctor's waiting room
        self.midi_manager.close_input()
        self.midi_manager.close_output()
        super().closeEvent(event)
