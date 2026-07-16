from PySide6.QtCore import QObject, Signal, QTimer
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

        # PRIMING STATE - for SOME GODDAMN REASON the akai ignores a brand new SDATA header UNLESS...
        # it's preceeded by this exact dance (RSLIST, sustain pedal reset on all channels, RSLIST again)
        # dont ask me how long it took for me to figure this out...
        # also dont ask why this bs isnt documented anywhere...
        self._priming_stage = None
        self._pending_transfer = None

    def refresh_sample_list(self):
        self._send_rslist_request()
        self.status_changed.emit("Requesting sample list...")

    def _send_rslist_request(self):
        request = akai_sysex.build_slist_request()
        self.midi_manager.send_sysex(request)

    def on_sysex_received(self, data_bytes):
        if not data_bytes:
            return

        if data_bytes[0] == 0x47:
            function_code = data_bytes[2] if len(data_bytes) > 2 else None
            if function_code == 0x05:
                # SLIST response
                count, names = akai_sysex.parse_slist_response(data_bytes)
                self.sample_list_updated.emit(names)

                if self._priming_stage == 1:
                    # got first SLIST reply - reset sustain pedal on all 16 channels, then ask for SLIST again
                    # why Akai, WHYYYYY???
                    self._priming_stage = 2
                    self.status_changed.emit(
                        "Priming: resetting sustain pedal on all channels..."
                    )
                    for channel in range(16):
                        self.midi_manager.send_control_change(channel, 64, 0)
                    QTimer.singleShot(100, self._send_rslist_request)

                elif self._priming_stage == 2:
                    # got the second SLIST reply, priming is done and start the transfer for reals this time...
                    self._priming_stage = None
                    self.status_changed.emit(
                        "Priming complete, settling before transfer..."
                    )
                    QTimer.singleShot(400, self._begin_pending_transfer)
            elif function_code == 0x16:
                # S1000 command reply: F0, 47, cc, 16, 48, mm, F7 - mm: 0=ok, 1=error
                result = data_bytes[4] if len(data_bytes) > 4 else None
                if result == 0:
                    self.status_changed.emit("Sampler confirmed: OK")
                elif result == 1:
                    self.status_changed.emit(
                        "Sampler confirmed: ERROR creating/replacing sample"
                    )
                else:
                    self.status_changed.emit(
                        f"Received REPLY with unexpected byte: {bytes(data_bytes).hex(' ')}"
                    )
            else:
                self.status_changed.emit(
                    f"Received unrecognised Akai message (function {function_code}): "
                    f"{bytes(data_bytes).hex(' ')}"
                )
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

        if (
            self._priming_stage is not None
            or self._send_queue
            or self._pending_transfer is not None
        ):
            self.status_changed.emit(
                "A transfer is already in progress - please wait for it to finish"
            )
            return

        sdata_message = akai_sysex.build_sdata_message(
            name=sample_name,
            sample_length=len(samples),
            sample_rate=framerate,
            sample_number=sample_number,
            channel=channel,
        )

        data_packets = sds_encoder.build_data_packets(samples, channel)

        # DONT SEND SAMPLE YET! Stash for now and run the priming dance thing first
        # on_sysex_received's priming branch picks this up once both SLIST round-trips finish
        self._pending_transfer = [sdata_message] + data_packets

        self.status_changed.emit("Priming sample before transfer...")
        self._priming_stage = 1
        self._send_rslist_request()

    def _begin_pending_transfer(self):
        queue = self._pending_transfer
        self._pending_transfer = None

        if queue is None:
            self.status_changed.emit("No pending transfer to start - ignoring")
            return

        self._send_queue = queue
        self._send_index = 0
        self.status_changed.emit(
            f"Sending SDATA header + {len(queue) - 1} data packets..."
        )
        self.transfer_progress.emit(0, len(queue))
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
        self._priming_stage = None
        self._pending_transfer = None
        if completed:
            self.status_changed.emit("Transfer complete")
        self.transfer_finished.emit(completed)
