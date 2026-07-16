from PySide6.QtCore import QObject, Signal
from core import akai_sysex, sds_encoder
import os


class SamplerController(QObject):
    sample_list_updated = Signal(list)
    status_changed = Signal(str)
    transfer_progress = Signal(int, int)  # (packets sent so far, total packets)
    transfer_finished = Signal(bool)

    def __init__(self, midi_manager):
        super().__init__()
        self.midi_manager = midi_manager
        self.midi_manager.sysex_received.connect(self.on_sysex_received)

        # state for an in-progress send - lets us dispatch one packet at a time
        # via QTtimer instead of blocking the GUI thread like a slow person walking in the middle of the aisles at Kmart
        self._send_queue = []
        self._send_index = 0

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
            self._on_handshake_message(handshake)
            return

        # anything else - log it for now rather than crash so i can figure out wtf is going on
        self.status_changed.emit(
            f"Received unrecognised SysEx: {bytes(data_bytes).hex(' ')}"
        )

    def _on_handshake_message(self, kind):
        # PLACEHOLDER - just log for now, but this is where the ACK/NAK/WAIT handling needs to happen
        if not self._send_queue:
            return
        if kind == "ack":
            # previous packet accepted - move on to next packet
            self._send_index += 1
            self.transfer_progress.emit(self._send_index, len(self._send_queue))
            self._send_current_packet()
        elif kind == "wait":
            # Sampler still processing - do nothing and stfu
            self.status_changed.emit("Sampler asked us to wait...")
        elif kind == "nak":
            # checksum failed on sampler's end - resend the same packet, DONT advance the index
            self.status_changed.emit(
                f"NAK received - resending packet {self._send_index}"
            )
            self._send_current_packet()
        elif kind == "cancel":
            self.status_changed.emit("Transfer cancelled by sampler")
            self._abort_transfer(completed=False)

    def send_sample_file(self, filepath, sample_number=0, channel=0):
        # read wav file, encode as SDS dump, send it per packet
        # pacing is driven by sampler handshake responses

        samples, framerate = sds_encoder.read_wav_samples(filepath)
        sample_name = os.path.splitext(os.path.basename(filepath))[0]

        sdata_message = akai_sysex.build_sdata_message(
            name=sample_name,
            sample_length=len(samples),
            sample_rate=framerate,
            sample_number=sample_number,
            channel=channel,
        )

        data_packets = sds_encoder.build_data_packets(samples, channel)

        self._send_queue = [sdata_message] + data_packets
        self._send_index = 0
        self.status_changed.emit(
            f"Sending SDATA header + {len(data_packets)} packets..."
        )
        self.transfer_progress.emit(0, len(self._send_queue))

        self._send_current_packet()

    def _send_current_packet(self):
        # send whatever packet self._send_index currently points to.
        # called once to kick off transfer, then again every time the response tells us to advance or retry
        if self._send_index >= len(self._send_queue):
            self._abort_transfer(completed=True)
            return

        packet = self._send_queue[self._send_index]
        self.midi_manager.send_sysex(
            list(packet[1:-1])
        )  # strip F0/F7 wrapper since mido adds those

    def _abort_transfer(self, completed):
        self._send_queue = []
        self._send_index = 0
        if completed:
            self.status_changed.emit("Transfer complete")
        self.transfer_finished.emit(completed)
