from PySide6.QtCore import QObject, Signal
import akai_sysex


class SamplerController(QObject):
    sample_list_updated = Signal(list)
    status_changed = Signal(str)

    def __init__(self, midi_manager):
        super().__init__()
        self.midi_manager = midi_manager
        self.midi_manager.sysex_received.connect(self.on_sysex_received)

    def refresh_sample_list(self):
        request = akai_sysex.build_slist_request()
        self.midi_manager.send_sysex(request)
        self.status_changed.emit("Requesting sample list...")

    def on_sysex_received(self, data_bytes):
        count, names = akai_sysex.parse_slist_response(data_bytes)
        self.sample_list_updated.emit(names)


if __name__ == "__main__":
    import midi_manager as mm

    manager = mm.MidiManager()
    controller = SamplerController(manager)
    print("Controller created OK")
