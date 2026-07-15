from PySide6.QtCore import QObject, Signal, QTimer
from core import akai_sysex, sds_encoder


class SamplerController(QObject):
    sample_list_updated = Signal(list)
    status_changed = Signal(str)
    transfer_progress = Signal(int, int)  # (packets sent so far, total packets)
    transfer_finished = Signal()

    def __init__(self, midi_manager):
        super().__init__()
        self.midi_manager = midi_manager
        self.midi_manager.sysex_received.connect(self.on_sysex_received)

        # state for an in-progress send - lets us dispatch one packet at a time
        # via QTtimer instead of blocking the GUI thread like a slow person walking in the middle of the aisles at Kmart
        self._send_queue = []
        self._send_index = 0
        self._send_timer = QTimer(self)
        self._send_timer.timeout.connect(self._send_next_packet)

    def refresh_sample_list(self):
        request = akai_sysex.build_slist_request()
        self.midi_manager.send_sysex(request)
        self.status_changed.emit("Requesting sample list...")

    def on_sysex_received(self, data_bytes):
        if not data_bytes:
            return

        if data_bytes[0] == 0x47:
            # akai manufacturer ID - ie and SLIST response or something akai specific (NOT SDS ACK/NAK/WAIT)
            count, names = akai_sysex.parse_slist_response(data_bytes)
            self.sample_list_updated.emit(names)
            return

        handshake = sds_encoder.classify_response(data_bytes)
        if handshake is not None:
            self._on_handshake_message(handshake, data_bytes)
            return

        # anything else - log it for now rather than crash so i can figure out wtf is going on
        self.status_changed.emit(
            f"Received unrecognised SysEx: {bytes(data_bytes).hex(' ')}"
        )

    def _on_handshake_message(self, kind, data_bytes):
        # PLACEHOLDER - just log for now, but this is where the ACK/NAK/WAIT handling needs to happen
        packet_num = data_bytes[3] if len(data_bytes) > 3 else "?"
        self.status_changed.emit(
            f"Received handshake message: {kind} (packet #{packet_num})"
        )

    def send_sample_file(
        self, filepath, sample_number=0, channel=0, packet_interval_ms=20
    ):
        # read wav file, encode as SDS dump, send it per packet based on timer interval
        # IMPORTANT - this is deliberately naiive - no ACK/NAK handshake yet. Too complex for now
        # This just yeets packets and hopes the receiver can keep up. To fix later
        samples, framerate = sds_encoder.read_wav_samples(filepath)
        packets = sds_encoder.build_sds_dump(samples, framerate, sample_number, channel)

        self._send_queue = packets
        self._send_index = 0
        self.status_changed.emit(f"Sending {len(packets)} packets...")
        self.transfer_progress.emit(0, len(packets))

        self._send_timer.start(packet_interval_ms)

    def _send_next_packet(self):
        if self._send_index >= len(self._send_queue):
            self._send_timer.stop()
            self.status_changed.emit("Transfer complete")
            self.transfer_finished.emit()
            return

        packet = self._send_queue[self._send_index]

        # packet is a full sysex message (F0 ... F7) BUUUTTTTT mido expects payload without the F0/F7 werapper
        # strip first and last byte before sending
        self.midi_manager.send_sysex(list(packet[1:-1]))

        self._send_index += 1
        self.transfer_progress.emit(self._send_index, len(self._send_queue))
