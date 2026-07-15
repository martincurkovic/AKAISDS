import mido
from PySide6.QtCore import QObject, Signal


class MidiManager(QObject):
    # owns the real mido input/output ports

    sysex_received = Signal(bytes)
    connection_changed = Signal()

    def __init__(self):
        super().__init__()
        self.input_port = None
        self.output_port = None
        self.input_name = None
        self.output_name = None

    @staticmethod
    def list_inputs():
        return mido.get_input_names()

    @staticmethod
    def list_outputs():
        return mido.get_output_names()

    def open_input(self, name):
        self.close_input()
        if name:
            self.input_port = mido.open_input(name, callback=self._on_message)
            self.input_name = name
        self.connection_changed.emit()

    def close_input(self):
        if self.input_port is not None:
            self.input_port.close()
        self.input_port = None
        self.input_name = None

    def open_output(self, name):
        self.close_output()
        if name:
            self.output_port = mido.open_output(name)
            self.output_name = name
        self.connection_changed.emit()

    def close_output(self):
        if self.output_port is not None:
            self.output_port.close()
        self.output_port = None
        self.output_name = None

    def _on_message(self, message):
        if message.type == "sysex":
            self.sysex_received.emit(bytes(message.data))

    def send_sysex(self, data_bytes):
        # note that mido automatically adds the F0/F7 bytes to the payload
        if self.output_port is None:
            raise RuntimeError("No MIDI output port is open")
        self.output_port.send(mido.Message("sysex", data=data_bytes))
