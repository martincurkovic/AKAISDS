from PySide6.QtWidgets import QMainWindow
from ui.dashboard import TransferDashboard
from core.midi_manager import MidiManager
from core import app_config
from controller.sampler_controller import SamplerController


class ApplicationWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AKAI SDS")
        self.setMinimumSize(950, 500)

        # owns the real mido ports for the app's lifetime
        self.midi_manager = MidiManager()
        self.sampler_controller = SamplerController(self.midi_manager)

        # restore saved channel/device type BEFORE building dashboard so the UI will be correct
        self.sampler_controller.set_channel(app_config.get_saved_channel())
        self.sampler_controller.set_device_type(app_config.get_saved_device_type())

        # reconnect to whatever MIDI ports were used last time, if still exist
        self._restore_saved_ports()

        # TEMPORARY DEBUG WIRING - print EVERY status handshake message to console so we can debug responses from the sampler
        self.sampler_controller.status_changed.connect(print)

        # instantiate custom UI layout components
        self.dashboard_view = TransferDashboard(
            self.sampler_controller, self.midi_manager
        )

        # mount layout into core central display panel area
        self.setCentralWidget(self.dashboard_view)

    def _restore_saved_ports(self):
        input_name, output_name = app_config.get_saved_ports()

        available_inputs = self.midi_manager.list_inputs()
        available_outputs = self.midi_manager.list_outputs()

        if input_name and input_name in available_inputs:
            self.midi_manager.open_input(input_name)

        if output_name and output_name in available_outputs:
            self.midi_manager.open_output(output_name)

    def closeEvent(self, event):
        # release midi ports cleanly so mido's backend doesnt hang around like a fart in a doctor's waiting room
        self.midi_manager.close_input()
        self.midi_manager.close_output()
        super().closeEvent(event)
