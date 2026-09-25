import mido

from core import debug_log
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
            try:
                self.input_port = mido.open_input(name, callback=self._on_message)
            except Exception:
                debug_log.get_logger().error(
                    f"MidiManager: couldn't open input {name!r}", exc_info=True
                )
                raise
            self.input_name = name
            debug_log.get_logger().info(f"MidiManager: opened input {name!r}")
        self.connection_changed.emit()

    def close_input(self):
        if self.input_port is not None:
            self.input_port.close()
        self.input_port = None
        self.input_name = None

    def open_output(self, name):
        self.close_output()
        if name:
            try:
                self.output_port = mido.open_output(name)
            except Exception:
                debug_log.get_logger().error(
                    f"MidiManager: couldn't open output {name!r}", exc_info=True
                )
                raise
            self.output_name = name
            debug_log.get_logger().info(f"MidiManager: opened output {name!r}")
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
        try:
            self.output_port.send(mido.Message("sysex", data=data_bytes))
        except Exception:
            debug_log.get_logger().error(
                f"MidiManager: sysex send failed ({len(data_bytes)} bytes) on {self.output_name!r}",
                exc_info=True,
            )
            raise

    def send_control_change(self, channel, control, value):
        if self.output_port is None:
            raise RuntimeError("No MIDI output port is open")
        self.output_port.send(
            mido.Message(
                "control_change", channel=channel, control=control, value=value
            )
        )
