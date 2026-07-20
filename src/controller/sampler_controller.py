from PySide6.QtCore import QObject, Signal, QTimer
from core import akai_sysex, sds_encoder
import os


class SamplerController(QObject):
    sample_list_updated = Signal(list)
    status_changed = Signal(str)
    transfer_progress = Signal(int, int)  # (packets sent so far, total packets)
    transfer_finished = Signal(bool)
    file_transferred = Signal(str)

    def __init__(self, midi_manager):
        super().__init__()
        self.midi_manager = midi_manager
        self.midi_manager.sysex_received.connect(self.on_sysex_received)

        # state for an in-progress send - lets us dispatch one packet at a time
        # via QTtimer instead of blocking the GUI thread like a slow person walking in the middle of the aisles at Kmart
        self._send_queue = []
        self._send_index = 0
        self._active_channel = 0
        self._active_sample_number = 0

        # PRIMING STATE - for SOME GODDAMN REASON the akai ignores a brand new SDATA header UNLESS...
        # it's preceeded by this exact dance (RSLIST, sustain pedal reset on all channels, RSLIST again)
        # dont ask me how long it took for me to figure this out...
        # also dont ask why this bs isnt documented anywhere...
        self._priming_stage = None
        self._pending_transfer = None
        self._stereo_queue = []

        # FILE QUEUE STATE - for send_file_queue: a whole batch of local files to send in order
        # auto-numbered starting from however many samples already exist on the hardware
        self._file_queue = []
        self._file_queue_total = 0
        self._file_queue_channel = 0
        self._file_queue_effective_bits = 16
        self._file_queue_target_rate = None
        self._awaiting_count_for_queue = False
        self._next_file_sample_number = 0
        self._current_file_path = (
            None  # which queued file is in transit right now (if any)
        )

    def refresh_sample_list(self):
        self._send_rslist_request()
        self.status_changed.emit("Requesting sample list...")

    def delete_sample(self, sample_number, channel=0):
        # delete the selected sample (DELS command in akai documentation)
        request = akai_sysex.build_dels_request(sample_number, channel)
        self.midi_manager.send_sysex(request)
        self.status_changed.emit(f"Deleting sample {sample_number}...")
        QTimer.singleShot(300, self.refresh_sample_list)

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

                if self._awaiting_count_for_queue:
                    self._awaiting_count_for_queue = False
                    self._next_file_sample_number = len(names)
                    self._send_next_queued_file()

                elif self._priming_stage == 1:
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

    def send_sample_file(
        self,
        filepath,
        sample_number=0,
        channel=0,
        effective_bits=16,
        target_sample_rate=None,
    ):
        # read wav file, encode as SDS dump, send it per packet
        # pacing is driven by sampler handshake responses
        # effective bits reduces audio resolution but still sends as 16 bit audio samples. akai cant understand anything other than 16 bit dumps
        # target_sample_rate if given and differnet from wav's own sample rate will downsample before sending. this actually reduces file size
        # only integer ratio downsample supported for now

        samples, framerate = sds_encoder.read_wav_samples(filepath)
        sample_name = os.path.splitext(os.path.basename(filepath))[0]

        samples, framerate = self._prepare_samples(
            samples, framerate, effective_bits, target_sample_rate
        )
        self._start_send(sample_name, samples, framerate, sample_number, channel)

    def send_stereo_sample_file(
        self,
        filepath,
        sample_number_left,
        sample_number_right,
        channel=0,
        effective_bits=16,
        target_sample_rate=None,
    ):
        # send stereo file as 2 separate samples with -L/-R suffixes
        # left channel sent first, right sent once left is completed

        channels, framerate = sds_encoder.read_wav_channels(filepath)
        if len(channels) != 2:
            self.status_changed.emit(
                "That file isn't stereo - use send_sample_file instead"
            )
            return

        if (
            self._priming_stage is not None
            or self._send_queue
            or self._pending_transfer is not None
            or self._stereo_queue
        ):
            self.status_changed.emit(
                "A transfer is already in progress - please wait for it to finish"
            )
            return

        base_name = os.path.splitext(os.path.basename(filepath))[0]
        left_name = akai_sysex.build_stereo_channel_name(base_name, "-L")
        right_name = akai_sysex.build_stereo_channel_name(base_name, "-R")

        left_samples, left_rate = self._prepare_samples(
            channels[0], framerate, effective_bits, target_sample_rate
        )
        right_samples, right_rate = self._prepare_samples(
            channels[1], framerate, effective_bits, target_sample_rate
        )

        # right sample waits here until left sample finishes transfer
        self._stereo_queue = [
            (right_name, right_samples, right_rate, sample_number_right, channel),
        ]

        self.status_changed.emit("Sending stereo file: left channel first...")
        self._start_send(
            left_name, left_samples, left_rate, sample_number_left, channel
        )

    def _prepare_samples(self, samples, framerate, effective_bits, target_sample_rate):
        # shared prep step for both mono and stero files
        # optional downsampling, then bitcrush
        if target_sample_rate is not None and target_sample_rate != framerate:
            samples, framerate = sds_encoder.resample_to_target_rate(
                samples, framerate, target_sample_rate
            )

        if effective_bits < 16:
            samples = [sds_encoder.bitcrush_sample(s, effective_bits) for s in samples]

        return samples, framerate

    def _start_send(self, name, samples, framerate, sample_number, channel):
        # build SDATA header + data packets for one already prepped samples and kick off priming+send flow
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
            name=name,
            sample_length=len(samples),
            sample_rate=framerate,
            sample_number=sample_number,
            channel=channel,
        )

        data_packets = sds_encoder.build_data_packets(samples, channel, bit_depth=16)

        # DONT SEND SAMPLE YET! Stash for now and run the priming dance thing first
        # on_sysex_received's priming branch picks this up once both SLIST round-trips finish
        self._pending_transfer = [sdata_message] + data_packets
        self._active_channel = channel
        self._active_sample_number = sample_number

        self.status_changed.emit("Priming sample before transfer...")
        self._priming_stage = 1
        self._send_rslist_request()

    def send_file_queue(
        self, file_entries, channel=0, effective_bits=16, target_sample_rate=None
    ):
        # send a batch of local wav files, one after another, without overwriting anything already on the sampler
        # file_entries = list of (filepath, name_override) tuples, in the order they should be sent
        # starting sample number is deterimed automatically from the hardware
        if not file_entries:
            return

        if (
            self._priming_stage is not None
            or self._send_queue
            or self._pending_transfer is not None
            or self._stereo_queue
            or self._file_queue
            or self._awaiting_count_for_queue
        ):
            self.status_changed.emit(
                "A transfer is already in progress - please wait for it to finish"
            )
            return

        self._file_queue = list(file_entries)
        self._file_queue_total = len(file_entries)
        self._file_queue_channel = channel
        self._file_queue_effective_bits = effective_bits
        self._file_queue_target_rate = target_sample_rate
        self._awaiting_count_for_queue = True

        self.status_changed.emit(
            "Checking existing samples to choose a starting slot..."
        )
        self._send_rslist_request()

    def _send_next_queued_file(self):
        if not self._file_queue:
            return

        filepath, name_override = self._file_queue.pop(0)
        self._current_file_path = filepath
        current_index = self._file_queue_total - len(self._file_queue)

        try:
            channels, framerate = sds_encoder.read_wav_channels(filepath)
        except (OSError, ValueError) as e:
            self.status_changed.emit(f"Skipping {os.path.basename(filepath)}: {e}")
            QTimer.singleShot(0, self._send_next_queued_file)
            return

        base_name = name_override or os.path.splitext(os.path.basename(filepath))[0]
        channel = self._file_queue_channel
        effective_bits = self._file_queue_effective_bits
        target_sample_rate = self._file_queue_target_rate

        if len(channels) == 2:
            left_name = akai_sysex.build_stereo_channel_name(base_name, "-L")
            right_name = akai_sysex.build_stereo_channel_name(base_name, "-R")
            left_samples, left_rate = self._prepare_samples(
                channels[0], framerate, effective_bits, target_sample_rate
            )
            right_samples, right_rate = self._prepare_samples(
                channels[1], framerate, effective_bits, target_sample_rate
            )

            sample_number_left = self._next_file_sample_number
            sample_number_right = self._next_file_sample_number + 1
            self._next_file_sample_number += 2

            # right leg awaits here, same mechanism as send_stereo_sample_file
            self._stereo_queue = [
                (right_name, right_samples, right_rate, sample_number_right, channel),
            ]
            self.status_changed.emit(
                f"Sending file {current_index}/{self._file_queue_total}: {base_name} (stereo)..."
            )
            self._start_send(
                left_name, left_samples, left_rate, sample_number_left, channel
            )
        else:
            samples, out_rate = self._prepare_samples(
                channels[0], framerate, effective_bits, target_sample_rate
            )

            sample_number = self._next_file_sample_number
            self._next_file_sample_number += 1

            self.status_changed.emit(
                f"Sending file {current_index}/{self._file_queue_total}: {base_name}..."
            )
            self._start_send(base_name, samples, out_rate, sample_number, channel)

    def cancel_transfer(self):
        # abort whatever is currently happening and communicate the cancel to the hardware
        # should be safe to call at any time i think
        in_progrss = (
            self._priming_stage is not None
            or bool(self._send_queue)
            or self._pending_transfer is not None
            or bool(self._stereo_queue)
            or bool(self._file_queue)
            or self._awaiting_count_for_queue
        )
        if not in_progrss:
            return

        if self._send_queue:
            # tell the sampler we're bailing out
            # communicate using universal CANCEL handshake in SDS standard
            packet_num = self._send_index & 0x7F
            self.midi_manager.send_sysex(
                [0x7E, self._active_channel & 0x7F, sds_encoder.CANCEL, packet_num]
            )

            self.midi_manager.send_sysex(
                akai_sysex.build_dels_request(
                    self._active_sample_number, self._active_channel
                )
            )
        self._stereo_queue = []
        self._file_queue = []
        self._awaiting_count_for_queue = False

        self.status_changed.emit("Transfer cancelled")
        self._abort_transfer(completed=False)

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

        if completed and self._stereo_queue:
            name, samples, framerate, sample_number, channel = self._stereo_queue.pop(0)
            self.status_changed.emit("Left channel done - sending right channel...")
            QTimer.singleShot(
                300,
                lambda: self._start_send(
                    name, samples, framerate, sample_number, channel
                ),
            )
            return

        # if we get here, whatever file was in flight (mono or the full stereo pair) the transfer is finished
        # tell the UI it can remove it from the queue. only on success - cancelled/failed transfer should stay in the queue
        if self._current_file_path is not None:
            if completed:
                self.file_transferred.emit(self._current_file_path)
            self._current_file_path = None

        if completed and self._file_queue:
            QTimer.singleShot(300, self._send_next_queued_file)
            return

        self._stereo_queue = []
        self._file_queue = []
        if completed:
            self.status_changed.emit("Transfer complete")
            QTimer.singleShot(300, self.refresh_sample_list)
        self.transfer_finished.emit(completed)
