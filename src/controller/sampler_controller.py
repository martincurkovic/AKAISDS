from PySide6.QtCore import QObject, Signal, QTimer
from core import akai_sysex, sds_encoder
import os


class SamplerController(QObject):
    sample_list_updated = Signal(list)
    status_changed = Signal(str)
    transfer_progress = Signal(int, int)  # (packets sent so far, total packets)
    unit_progress = Signal(float)
    transfer_finished = Signal(bool)
    file_transferred = Signal(str)
    receive_progress = Signal(int, int)
    sample_received = Signal(str)
    receive_finished = Signal(bool)
    sample_info_received = Signal(dict)

    def __init__(self, midi_manager):
        super().__init__()
        self.midi_manager = midi_manager
        self.midi_manager.sysex_received.connect(self.on_sysex_received)
        self._refresh_is_silent = False

        # global SysEx device ID/channel (0-127) - NOT THE SAME AS MIDI CHANNELS (1-16)!!!
        # defaults to 0, otherwise it can be set manually for a daisy-chained setup
        self.channel = 0
        self.device_type = "akai"

        self._open_loop_delay_ms = 40  # pacing for open loop sending
        self._handshake_timeout_ms = 500  # how long to wait for a reponse before switching to open-loop transmission
        self._no_response_detected = False
        self._packet_send_generation = 0

    def set_channel(self, channel):
        self.channel = channel & 0x7F

    def set_device_type(self, device_type):
        self.device_type = device_type

        # state for an in-progress send - lets us dispatch one packet at a time
        # via QTtimer instead of blocking the GUI thread like a slow person walking in the middle of the aisles at Kmart
        self._send_queue = []
        self._send_index = 0
        self._active_channel = 0
        self._active_sample_number = 0

        self._stereo_queue = []

        # FILE QUEUE STATE - for send_file_queue: a whole batch of local files to send in order
        # auto-numbered starting from however many samples already exist on the hardware
        self._file_queue = []
        self._file_queue_total = 0
        self._file_queue_skipped = (
            0  # how many files in the current batch failed to read
        )
        self._file_queue_channel = 0
        self._awaiting_count_for_queue = False
        self._next_file_sample_number = 0
        self._current_file_path = (
            None  # which queued file is in transit right now (if any)
        )

        # GENERIC SDS + AUTO-RENAME STATE
        # Akai ignores our requested sample number for generic SDS and reallocates to
        # whatever the lowest sample slot number is.
        # Snapshot the sample list before and after, diff them, rename the slot that has changed
        self._awaiting_pre_send_slist = False
        self._pre_send_names = []
        self._pending_generic_send = None
        self._rename_after_send = None
        self._awaiting_post_send_slist_for_rename = False

        # RECEIVING STATE - basically the mirror image of sending state
        # We need to ACK/NAK each incoming data packet ourselves (at least i think so...)
        self._receiving = False
        self._receive_channel = 0
        self._receive_sample_number = None
        self._receive_save_path = None
        self._receive_header_info = None
        self._receive_packets = []
        self._receive_expected_packets = 0
        self._receive_queue = []
        self._receive_queue_total = 0
        self._awaiting_sample_info = False

        # tracks which leg of hte current file's transfer is in progress
        # 1 leg for mono, 2 for stereo
        # this lets the overall progress bar report progress smoothly across the whole file
        self._current_file_leg_index = 0
        self._current_file_total_legs = 1

    def refresh_sample_list(self, silent=False):
        if self.device_type == "generic":
            return
        self._refresh_is_silent = silent
        self._send_rslist_request()
        if not silent:
            self.status_changed.emit("Requesting sample list...")

    def delete_sample(self, sample_number, channel=None):
        if channel is None:
            channel = self.channel
        # delete the selected sample (DELS command in akai documentation)
        request = akai_sysex.build_dels_request(sample_number, channel)
        self.midi_manager.send_sysex(request)
        self.status_changed.emit(f"Deleting sample {sample_number}...")
        QTimer.singleShot(300, self.refresh_sample_list)

    def rename_sample(self, sample_number, new_name, channel=None):
        if channel is None:
            channel = self.channel
        # rename existing sample WITHOUT touching its audio data
        request = akai_sysex.build_rename_sample_request(
            sample_number, new_name, channel
        )
        self.midi_manager.send_sysex(request)
        self.status_changed.emit(
            f"Renaming sample {sample_number} to '{new_name.strip()}'..."
        )
        QTimer.singleShot(300, self.refresh_sample_list)

    def request_sample_info(self, sample_number, channel=None):
        if channel is None:
            channel = self.channel
        # fetch sample's header info (name, sample_rate, length, root_key, detune)
        # for display purposes
        if (
            self._send_queue
            or self._stereo_queue
            or self._file_queue
            or self._receiving
            or self._receive_queue
            or self._awaiting_sample_info
        ):
            self.status_changed.emit(
                "A transfer is already in progress - please wait for it to finish"
            )
            return

        self._awaiting_sample_info = True
        request = akai_sysex.build_rsdata_request(sample_number, channel)
        self.midi_manager.send_sysex(request)
        self.status_changed.emit(f"Requesting info for sample {sample_number}...")

    def _send_rslist_request(self):
        request = akai_sysex.build_slist_request(self.channel)
        self.midi_manager.send_sysex(request)

    def on_sysex_received(self, data_bytes):
        # entry point for catching any unexpected eception from handling logic
        try:
            self._on_sysex_received_impl(data_bytes)
        except Exception as e:
            print(f"[ERROR] Unhandled exception in on_sysex_received: {e!r}")
            import traceback

            traceback.print_exc()
            self._recover_from_error(f"Unexpected error: {e}")

    def _recover_from_error(self, message):
        # reset every in-progress state of app back to idle and tell the UI
        was_receiving = self._receiving or bool(self._receive_queue)

        self._send_queue = []
        self._send_index = 0
        self._stereo_queue = []
        self._file_queue = []
        self._file_queue_skipped = 0
        self._current_file_path = None
        self._awaiting_count_for_queue = False
        self._awaiting_pre_send_slist = False
        self._awaiting_post_send_slist_for_rename = False
        self._pending_generic_send = None
        self._rename_after_send = None
        self._pre_send_names = []
        self._awaiting_sample_info = False
        self._receiving = False
        self._receive_queue = []
        self._receive_header_info = None
        self._receive_packets = []

        self.status_changed.emit(message)

        if was_receiving:
            self.receive_finished.emit(False)
        else:
            self.transfer_finished.emit(False)

    def _on_sysex_received_impl(self, data_bytes):
        if not data_bytes:
            return

        if data_bytes[0] == 0x47:
            function_code = data_bytes[2] if len(data_bytes) > 2 else None
            if function_code == 0x05:
                # SLIST response
                count, names = akai_sysex.parse_slist_response(data_bytes)
                self.sample_list_updated.emit(names)

                if self._awaiting_pre_send_slist:
                    # get the "before" sample list snapshot, then send the generic SDS dump
                    # diff against this once transfer is done
                    self._awaiting_pre_send_slist = False
                    self._pre_send_names = list(names)
                    samples, framerate, channel, bit_depth = self._pending_generic_send
                    self._pending_generic_send = None
                    sample_number_hint = len(names)
                    self._send_generic_packets(
                        samples, framerate, sample_number_hint, channel, bit_depth
                    )

                elif self._awaiting_post_send_slist_for_rename:
                    # get the "after" sample list snapshot, find the one thats changed and rename THAT
                    self._awaiting_post_send_slist_for_rename = False
                    new_name, rename_channel = self._rename_after_send
                    self._rename_after_send = None
                    actual_slot = self._find_new_sample_slot(
                        self._pre_send_names, names
                    )
                    self._pre_send_names = []
                    if actual_slot is not None:
                        self._next_file_sample_number = max(
                            self._next_file_sample_number, actual_slot + 1
                        )
                        self.status_changed.emit(
                            f"Sample landed in slot {actual_slot} - renaming..."
                        )
                        self.rename_sample(actual_slot, new_name, rename_channel)
                    else:
                        self.status_changed.emit(
                            "Couldn't tell which slot the new sample landed in - skipping rename"
                        )
                    self._finish_unit(True)

                elif self._awaiting_count_for_queue:
                    self._awaiting_count_for_queue = False
                    self._next_file_sample_number = len(names)
                    self._send_next_queued_file()
                else:
                    # plain refresh - not part of other flow
                    # only announce refresh in status bar if it wasnt a SILENT refresh
                    if not self._refresh_is_silent:
                        self.status_changed.emit(
                            f"Loaded {len(names)} sample(s) from hardware"
                        )
                    self._refresh_is_silent = False

            elif function_code == 0x0B:
                # SDATA response - either the reply to our own RSDATA request while receiving sample
                # or something we're not expecting rn
                if self._awaiting_sample_info:
                    self._awaiting_sample_info = False
                    info = akai_sysex.parse_sdata_response(data_bytes)
                    self.sample_info_received.emit(info)
                elif self._receiving and self._receive_header_info is None:
                    self._on_receive_sdata_header(data_bytes)
                else:
                    self.status_changed.emit(
                        f"Received unexpected SDATA message: {bytes(data_bytes).hex(' ')}"
                    )

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

        if data_bytes[0] == 0x7E:
            sub_id = data_bytes[2] if len(data_bytes) > 2 else None

            if self._receiving and sub_id == 0x01:
                self._on_receive_header(data_bytes)
                return
            if self._receiving and sub_id == 0x02:
                self._on_receive_data_packet(data_bytes)
                return

        handshake = sds_encoder.classify_response(data_bytes)
        if handshake is not None:
            self._on_handshake_message(handshake)
            return

        # anything else - log it for now rather than crash so i can figure out wtf is going on
        self.status_changed.emit(
            f"Received unrecognised SysEx: {bytes(data_bytes).hex(' ')}"
        )

    def _emit_progress(self, sent, total):
        # emith both progress signals for one packet level update
        self.transfer_progress.emit(sent, total)
        leg_fraction = (sent / total) if total else 0
        unit_fraction = (
            self._current_file_leg_index + leg_fraction
        ) / self._current_file_total_legs
        self.unit_progress.emit(min(unit_fraction, 1.0))

    def _on_handshake_message(self, kind):
        if not self._send_queue:
            return
        if kind == "ack":
            # previous packet accepted - move on to next packet
            self._send_index += 1
            self._emit_progress(self._send_index, len(self._send_queue))
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

    # -----------------------------------------------------------------
    # LEGACY / DIRECT signle shot entry points
    # still useful for manual tests
    # effective_bits here means bitcrush and keep 16 bit format
    # not the new bitcrush which actually reduces bit depth for reals
    # -----------------------------------------------------------------

    def send_sample_file(
        self,
        filepath,
        sample_number=0,
        channel=None,
        effective_bits=16,
        target_sample_rate=None,
    ):
        if channel is None:
            channel = self.channel
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
        channel=None,
        effective_bits=16,
        target_sample_rate=None,
    ):
        if channel is None:
            channel = self.channel
        channels, framerate = sds_encoder.read_wav_channels(filepath)
        if len(channels) != 2:
            self.status_changed.emit(
                "That file isn't stereo - use send_sample_file instead"
            )
            return

        if self._send_queue or self._stereo_queue:
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

        self._stereo_queue = [
            (right_name, right_samples, right_rate, channel, 16, sample_number_right),
        ]
        self.status_changed.emit("Sending stereo file: left channel first...")
        self._start_send(
            left_name, left_samples, left_rate, sample_number_left, channel
        )

    def send_sample_file_generic(
        self, filepath, sample_number=0, channel=None, bit_depth=16
    ):
        if channel is None:
            channel = self.channel
        # send sample using generic usiversal midi sds protocol (NOT THE AKAI ONE)
        # this actually sends samples at a lower bit depth across less bytes (ie, 12 bit samples sent across 2 midi bytes, not 3 like a 16 bit sample)

        if self._send_queue or self._stereo_queue or self._file_queue:
            self.status_changed.emit(
                "A transfer is already in progress - please wait for it to finish"
            )
            return

        samples, framerate = sds_encoder.read_wav_samples(filepath)
        if bit_depth < 16:
            samples = [sds_encoder.reduce_bit_depth(s, bit_depth) for s in samples]
        self._send_generic_packets(
            samples, framerate, sample_number, channel, bit_depth
        )

    def send_sample_file_generic_and_rename(
        self, filepath, new_name, channel=None, bit_depth=16
    ):
        if channel is None:
            channel = self.channel
        # send via generic sds then rename it correctly using the diff sample slist logic

        if (
            self._send_queue
            or self._stereo_queue
            or self._file_queue
            or self._awaiting_pre_send_slist
        ):
            self.status_changed.emit(
                "A transfer is already in progress - please wait for it to finish"
            )
            return

        samples, framerate = sds_encoder.read_wav_samples(filepath)
        if bit_depth < 16:
            samples = [sds_encoder.reduce_bit_depth(s, bit_depth) for s in samples]

        self._begin_generic_with_rename(
            samples, framerate, channel, bit_depth, new_name
        )

    def _prepare_samples(self, samples, framerate, effective_bits, target_sample_rate):
        # shared prep step for LEGACY entry points above
        # optional downsampling, then bitcrush (keeping 16 bit wire format)
        if target_sample_rate is not None and target_sample_rate != framerate:
            samples, framerate = sds_encoder.resample_to_target_rate(
                samples, framerate, target_sample_rate
            )
        if effective_bits < 16:
            samples = [sds_encoder.bitcrush_sample(s, effective_bits) for s in samples]
        return samples, framerate

    def _find_new_sample_slot(self, names_before, names_after):
        # compare sample name lists before and after to find which slot index changed
        for i in range(min(len(names_before), len(names_after))):
            if names_before[i] != names_after[i]:
                return i
        if len(names_after) > len(names_before):
            return len(names_before)
        return None

    # -------------------------------------------------------------------
    # new per-file setting prep (actualy reduces bit depth for reals)
    # -------------------------------------------------------------------

    def _prepare_samples_for_settings(
        self, samples, framerate, bit_depth, target_sample_rate
    ):
        if target_sample_rate is not None and target_sample_rate != framerate:
            try:
                samples, framerate = sds_encoder.resample_to_target_rate(
                    samples, framerate, target_sample_rate
                )
            except ValueError as e:
                self.status_changed.emit(
                    f"Couldn't resample to {target_sample_rate}Hz ({e}) - using original rate"
                )
        if bit_depth < 16:
            samples = [sds_encoder.reduce_bit_depth(s, bit_depth) for s in samples]
        return samples, framerate

    def _start_unit(
        self, name, samples, framerate, channel, bit_depth, sample_number=None
    ):
        if self.device_type == "generic":
            self._send_generic_packets(
                samples, framerate, sample_number or 0, channel, bit_depth
            )
        # send one sample, choosing Akai SDATA (bit depth = 16, needs a real sample number) or generic SDS + auto-rename (bit depth != 16, real slot determined automaticaly)
        elif bit_depth == 16:
            self._start_send(name, samples, framerate, sample_number, channel)
        elif self._is_open_loop():
            self._send_generic_packets(
                samples, framerate, sample_number or 0, channel, bit_depth
            )
        else:
            self._begin_generic_with_rename(
                samples, framerate, channel, bit_depth, name
            )

    def _begin_generic_with_rename(
        self, samples, framerate, channel, bit_depth, new_name
    ):
        self._pending_generic_send = (samples, framerate, channel, bit_depth)
        self._rename_after_send = (new_name, channel)
        self._awaiting_pre_send_slist = True
        self.status_changed.emit("Checking current samples before sending...")
        self._send_rslist_request()

    def _send_generic_packets(
        self, samples, framerate, sample_number_hint, channel, bit_depth
    ):
        header_packet = sds_encoder.build_dump_header(
            samples, framerate, sample_number_hint, channel, bit_depth
        )
        data_packets = sds_encoder.build_data_packets(samples, channel, bit_depth)

        self._send_queue = [header_packet] + data_packets
        self._send_index = 0
        self._no_response_detected = False
        self._active_channel = channel
        self._active_sample_number = sample_number_hint

        mode_note = (
            " (open loop - no MIDI input selected)" if self._is_open_loop() else ""
        )
        self.status_changed.emit(
            f"Sending generic SDS dump ({bit_depth}-bit)... {mode_note}"
        )
        self._emit_progress(0, len(self._send_queue))
        self._send_current_packet()

    # ---------------------------------------------------------
    # FILE QUEUE - drag and drop batch sending, per file settings
    # ---------------------------------------------------------

    def send_file_queue(self, file_entries, channel=None, starting_sample_number=None):
        print()
        if channel is None:
            channel = self.channel
        # send batch of local wav files, one after another without overwriting anything already on the hardware
        # file_entries: list of dicts, each shaped like:
        #    {
        #         "filepath": str,
        #         "name": str or None,        # None = use the file's own name
        #         "bit_depth": int,           # 16 = Akai SDATA; anything
        #                                      # else = generic SDS (real
        #                                      # wire size reduction)
        #         "sample_rate": int or None, # None = keep the file's own rate
        #         "mono": bool,                # True = send only the left
        #                                      # channel even if the file
        #                                      # is stereo
        #     }
        #
        # Starting sample number (for bit_depth == 16 entries) is
        # determined automatically from however many samples already
        # exist on the hardware. Entries using bit_depth != 16 ignore
        # that number entirely (the Akai always reallocates for generic
        # SDS) and get renamed correctly afterward instead.
        if not file_entries:
            return False
        if (
            self._send_queue
            or self._stereo_queue
            or self._file_queue
            or self._awaiting_count_for_queue
        ):
            self.status_changed.emit(
                "A transfer is already in progress - please wait for it to finish"
            )
            return False
        if (
            self.device_type == "generic" or self._is_open_loop()
        ) and starting_sample_number is None:
            self.status_changed.emit(
                "Set a starting sample number in Transmission Settings before "
                "sending - required for generic device, or when no MIDI input "
                "is selected (open loop), since neither can auto-detect one."
            )
            return False

        self._file_queue = list(file_entries)
        self._file_queue_total = len(file_entries)
        self._file_queue_skipped = 0
        self._file_queue_channel = channel

        if self.device_type == "generic" or self._is_open_loop():
            # no SLIST to check - go straight to user defined slot number
            self._next_file_sample_number = starting_sample_number
            self._send_next_queued_file()
        else:
            self._awaiting_count_for_queue = True
            self.status_changed.emit(
                "Checking existing samples to choose a starting slot..."
            )
            self._send_rslist_request()

        return True

    def _send_next_queued_file(self):
        try:
            self._send_next_queued_file_impl()
        except Exception as e:
            print(f"[ERROR] Unhandled exception in _send_next_queued_file: {e!r}")
            import traceback

            traceback.print_exc()
            self._recover_from_error(f"Error sending file: {e}")

    def _send_next_queued_file_impl(self):
        if not self._file_queue:
            return

        entry = self._file_queue.pop(0)
        filepath = entry["filepath"]
        self._current_file_path = filepath
        current_index = self._file_queue_total - len(self._file_queue)

        try:
            channels, framerate = sds_encoder.read_wav_channels(filepath)
        except (OSError, ValueError) as e:
            self.status_changed.emit(f"Skipping {os.path.basename(filepath)}: {e}")
            self._file_queue_skipped += 1
            # tell the UI to remove this fiel from the queue too, same as succesfuly sent file
            self.file_transferred.emit(filepath)
            self._current_file_path = None
            if self._file_queue:
                QTimer.singleShot(0, self._send_next_queued_file)
            else:
                self._finish_unit(True)
            return

        base_name = entry.get("name") or os.path.splitext(os.path.basename(filepath))[0]
        channel = self._file_queue_channel
        bit_depth = entry.get("bit_depth", 16)
        target_sample_rate = entry.get("sample_rate")
        force_mono = entry.get("mono", False)

        send_stereo = (len(channels) == 2) and not force_mono

        # reset leg tracking for this NEW file - once per file, not per leg
        self._current_file_total_legs = 2 if send_stereo else 1
        self._current_file_leg_index = 0

        if send_stereo:
            left_name = akai_sysex.build_stereo_channel_name(base_name, "-L")
            right_name = akai_sysex.build_stereo_channel_name(base_name, "-R")
            left_samples, left_rate = self._prepare_samples_for_settings(
                channels[0], framerate, bit_depth, target_sample_rate
            )
            right_samples, right_rate = self._prepare_samples_for_settings(
                channels[1], framerate, bit_depth, target_sample_rate
            )

            if self.device_type == "generic" or bit_depth == 16 or self._is_open_loop():
                sample_number_left = self._next_file_sample_number
                sample_number_right = self._next_file_sample_number + 1
                self._next_file_sample_number += 2
            else:
                # generic path picks its own slot regardless - these are unused
                sample_number_left = None
                sample_number_right = None

            # right leg waits here until the left leg (audio + any
            # rename it needs) is FULLY done - see _finish_unit
            self._stereo_queue = [
                (
                    right_name,
                    right_samples,
                    right_rate,
                    channel,
                    bit_depth,
                    sample_number_right,
                ),
            ]
            self.status_changed.emit(
                f"Sending file {current_index}/{self._file_queue_total}: {base_name} (stereo)..."
            )
            self._start_unit(
                left_name,
                left_samples,
                left_rate,
                channel,
                bit_depth,
                sample_number_left,
            )
        else:
            samples, out_rate = self._prepare_samples_for_settings(
                channels[0], framerate, bit_depth, target_sample_rate
            )

            if self.device_type == "generic" or bit_depth == 16 or self._is_open_loop():
                sample_number = self._next_file_sample_number
                self._next_file_sample_number += 1
            else:
                sample_number = None

            self.status_changed.emit(
                f"Sending file {current_index}/{self._file_queue_total}: {base_name}..."
            )
            self._start_unit(
                base_name, samples, out_rate, channel, bit_depth, sample_number
            )

    # ----------------------------------------------------------------------------
    # RECEIVING - download samples from hardware and save as WAV files
    # -----------------------------------------------------------------------------

    def receive_sample_generic(self, sample_number, save_path, channel=None):
        # receive ONE sample from a generic SDS device
        # the user must specify a sample number manually for generic SDS, no way to obtain a file list in generic SDS
        # requests a sample dump from the device as per SDS protocol
        if channel is None:
            channel = self.channel

        if (
            self._send_queue
            or self._stereo_queue
            or self._file_queue
            or self._receiving
            or self._receive_queue
            or self._awaiting_sample_info
        ):
            self.status_changed.emit(
                "A transfer is already in progress - please wait for it to finish"
            )
            return

        self._receiving = True
        self._receive_channel = channel
        self._receive_sample_number = sample_number
        self._receive_save_path = save_path
        self._receive_header_info = None
        self._receive_packets = []

        request = sds_encoder.build_dump_request(sample_number, channel)
        self.midi_manager.send_sysex(request)
        self.status_changed.emit(f"Requesting sample {sample_number} (generic SDS)...")

    def receive_samples(self, sample_requests, channel=None):
        if channel is None:
            channel = self.channel
        # download batch of samples from hardware, one after another, saving each as wav file
        # sample_requests = list of (sample_number, save_path) tuples
        if not sample_requests:
            return

        if (
            self._send_queue
            or self._stereo_queue
            or self._file_queue
            or self._receiving
            or self._receive_queue
        ):
            self.status_changed.emit(
                "A transfer is already in progress - please wait for it to finish"
            )
            return

        self._receive_queue = list(sample_requests)
        self._receive_queue_total = len(self._receive_queue)
        self._receive_channel = channel
        self._receive_next_sample()

    def _receive_next_sample(self):
        if not self._receive_queue:
            self.status_changed.emit("All samples received")
            self.receive_finished.emit(True)
            return

        sample_number, save_path = self._receive_queue.pop(0)
        current_index = self._receive_queue_total - len(self._receive_queue)

        self._receiving = True
        self._receive_sample_number = sample_number
        self._receive_save_path = save_path
        self._receive_header_info = None
        self._receive_packets = []

        request = akai_sysex.build_rsdata_request(sample_number, self._receive_channel)
        self.midi_manager.send_sysex(request)
        self.status_changed.emit(
            f"Requesting sample {sample_number} ({current_index}/{self._receive_queue_total})..."
        )

    def _on_receive_sdata_header(self, data_bytes):
        # handles SDATA (0x0B) response to RSDATA requests
        # real akai mechanism for getting sample's header
        info = akai_sysex.parse_sdata_response(data_bytes)
        self._receive_header_info = info
        self._receive_packets = []

        bytes_per_word = (info["bit_depth"] + 6) // 7
        words_per_packet = sds_encoder.DATA_BYTES_PER_PACKET // bytes_per_word
        self._receive_expected_packets = -(-info["sample_length"] // words_per_packet)

        self.status_changed.emit(
            f"Requesting audio for '{info['name']}': {info['sample_length']} samples "
            f"({self._receive_expected_packets} packets expected)..."
        )
        self.receive_progress.emit(0, info["sample_length"])

        # RSPACK - trigger for bulk audio
        request = akai_sysex.build_rspack_request(
            self._receive_sample_number,
            offset=0,
            num_samples=info["sample_length"],
            interval=1,
            function=0,
            channel=self._receive_channel,
        )
        self.midi_manager.send_sysex(request)

    def _on_receive_header(self, data_bytes):
        info = sds_encoder.parse_dump_header(data_bytes)
        self._receive_header_info = info
        self._receive_packets = []

        bytes_per_word = (info["bit_depth"] + 6) // 7
        words_per_packet = sds_encoder.DATA_BYTES_PER_PACKET // bytes_per_word
        self._receive_expected_packets = -(-info["sample_length"] // words_per_packet)

        self.status_changed.emit(
            f"Receiving sample {info['sample_number']}: {info['bit_depth']}-bit, "
            f"{info['sample_rate']}Hz, {info['sample_length']} samples... "
            f"({self._receive_expected_packets} packets expected)..."
        )
        self.receive_progress.emit(0, info["sample_length"])

        # accept header so the hardware can start sending data packets
        self.midi_manager.send_sysex(
            [0x7E, self._receive_channel & 0x7F, sds_encoder.ACK, 0]
        )

    def _on_receive_data_packet(self, data_bytes):
        if self._receive_header_info is None:
            # got a data packet before ever seeing a header - ignore it instead of crashing, something's fucked it...
            return

        packet_num = data_bytes[3] if len(data_bytes) > 3 else 0
        payload = data_bytes[4:-1]
        received_checksum = data_bytes[-1] if len(data_bytes) > 4 else None

        computed_checksum = sds_encoder.xor_checksum(list(data_bytes[:-1]))
        if computed_checksum != received_checksum:
            self.status_changed.emit(
                f"Checksum error on packet {packet_num} - requesting resend"
            )
            self.midi_manager.send_sysex(
                [0x7E, self._receive_channel & 0x7F, sds_encoder.NAK, packet_num]
            )
            return

        self._receive_packets.append(bytes(payload))
        self.midi_manager.send_sysex(
            [0x7E, self._receive_channel & 0x7F, sds_encoder.ACK, packet_num]
        )

        info = self._receive_header_info
        bytes_per_word = (info["bit_depth"] + 6) // 7
        words_per_packet = sds_encoder.DATA_BYTES_PER_PACKET // bytes_per_word
        words_received = len(self._receive_packets) * words_per_packet

        self.receive_progress.emit(
            min(words_received, info["sample_length"]), info["sample_length"]
        )

        if len(self._receive_packets) >= self._receive_expected_packets:
            self._finish_receiving()

    def _finish_receiving(self):
        info = self._receive_header_info
        bytes_per_word = (info["bit_depth"] + 6) // 7

        all_bytes = b"".join(self._receive_packets)

        samples = []
        for i in range(0, len(all_bytes), bytes_per_word):
            chunk = all_bytes[i : i + bytes_per_word]
            if len(chunk) < bytes_per_word:
                break  # trailing zero padding on final packet
            samples.append(sds_encoder.sds_bytes_to_sample(chunk, info["bit_depth"]))

        samples = samples[
            : info["sample_length"]
        ]  # trim any padding words past the real length (ie, trailing zeroes)

        sds_encoder.write_wav_file(
            self._receive_save_path,
            samples,
            info["sample_rate"],
            info["bit_depth"],
        )

        self.status_changed.emit(
            f"Saved sample {info['sample_number']} to {self._receive_save_path}"
        )
        self.sample_received.emit(self._receive_save_path)

        self._receiving = False
        self._receive_header_info = None
        self._receive_packets = []

        QTimer.singleShot(300, self._receive_next_sample)

    def cancel_transfer(self):
        # abort whatever is currently happening and communicate the cancel to the hardware
        # should be safe to call at any time i think
        in_progrss = (
            bool(self._send_queue)
            or bool(self._stereo_queue)
            or bool(self._file_queue)
            or self._awaiting_count_for_queue
            or self._awaiting_pre_send_slist
            or self._awaiting_post_send_slist_for_rename
            or self._receiving
            or bool(self._receive_queue)
            or self._awaiting_sample_info
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
        if self._receiving:
            self.midi_manager.send_sysex(
                [0x7E, self._receive_channel & 0x7F, sds_encoder.CANCEL, 0]
            )
        self._stereo_queue = []
        self._file_queue = []
        self._awaiting_count_for_queue = False
        self._awaiting_pre_send_slist = False
        self._awaiting_post_send_slist_for_rename = False
        self._pending_generic_send = None
        self._rename_after_send = None
        self._pre_send_names = []
        self._awaiting_sample_info = False

        was_receiving = self._receiving or bool(self._receive_queue)
        self._receiving = False
        self._receive_queue = []
        self._receive_header_info = None
        self._receive_packets = []

        self.status_changed.emit("Transfer cancelled")
        if was_receiving:
            self.receive_finished.emit(False)
        else:
            self._abort_transfer(completed=False)

    def _is_open_loop(self):
        # True if no MIDI input port is selected
        # ie- no ACK/NAK/WAIT/CANCEL handshaking
        # Send packets with fixed pacing delay
        return self.midi_manager.input_name is None

    def is_open_loop(self):
        # public version of _is_open_loop() - safe for UI layer to call directly
        return self._is_open_loop()

    def _send_current_packet(self):
        # send whatever packet self._send_index currently points to.
        # called once to kick off transfer, then again every time the response tells us to advance or retry
        if self._send_index >= len(self._send_queue):
            self._abort_transfer(completed=True)
            return

        packet = self._send_queue[self._send_index]
        self.midi_manager.send_sysex(
            list(packet[1:-1])
        )  # strip F0/F7 wrapper since mido adds these

        if self._is_open_loop() or self._no_response_detected:
            # no input port - can't wait for ACK because it will never arrive
            # pace packets ourselves using fixed delay (20ms per official MIDI spec)
            # OR given up waiting for hardware to send a handshake after a fixed timeout
            self._send_index += 1
            self._emit_progress(self._send_index, len(self._send_queue))
            QTimer.singleShot(self._open_loop_delay_ms, self._send_current_packet)
        else:
            # normal ACK driven path but with safety net:
            # if nothing reponds within documented threshold, fall back to open loop comms
            self._packet_send_generation += 1
            expected_generation = self._packet_send_generation
            QTimer.singleShot(
                self._handshake_timeout_ms,
                lambda: self._check_packet_timeout(expected_generation),
            )

    def _check_packet_timeout(self, expected_generation):
        if not self._send_queue:
            return  # transfer already finished or cancelled
        if self._packet_send_generation != expected_generation:
            return  # a real reponse has already arrived and move things on - stale timeout, ignore

        # no reponse arrived in time - per spec, assume packet got thru and move past it
        # ie, switch to open loop comms
        self.status_changed.emit("No reponse from the sampler - assuming open loop...")
        self._no_response_detected = True
        self._send_index += 1
        self._emit_progress(self._send_index, len(self._send_queue))
        self._send_current_packet()

    def _abort_transfer(self, completed):
        self._send_queue = []
        self._send_index = 0

        if completed and self._rename_after_send is not None:
            self.status_changed.emit("Checking where the sample actually landed...")
            self._awaiting_post_send_slist_for_rename = True
            QTimer.singleShot(300, self._send_rslist_request)
            return
        self._finish_unit(completed)

    def _finish_unit(self, completed):
        # called once single send unit is finished - generic sds or akai sdata
        # decided whether to continue to a stereo right channel, next queued file or declare everything done

        if completed and self._stereo_queue:
            name, samples, framerate, channel, bit_depth, sample_number = (
                self._stereo_queue.pop(0)
            )
            self.status_changed.emit("Left channel done - sending right channel...")
            self._current_file_leg_index = (
                1  # second leg's progress continues from here, not zero
            )
            QTimer.singleShot(
                300,
                lambda: self._start_unit(
                    name, samples, framerate, channel, bit_depth, sample_number
                ),
            )
            return

        if self._current_file_path is not None:
            if completed:
                self.file_transferred.emit(self._current_file_path)
            self._current_file_path = None

        if completed and self._file_queue:
            QTimer.singleShot(300, self._send_next_queued_file)
            return

        self._stereo_queue = []
        self._file_queue = []
        self._rename_after_send = None
        if completed:
            if self._file_queue_skipped:
                self.status_changed.emit(
                    f"Transfer complete ({self._file_queue_skipped} file"
                    f"{'s' if self._file_queue_skipped != 1 else ''} skipped, incompatible WAV file)"
                )
            else:
                self.status_changed.emit("Transfer complete")
            # give the message above a real chance to be seen before the refresh's satus overwrites it
            QTimer.singleShot(2500, lambda: self.refresh_sample_list(silent=True))
        self.transfer_finished.emit(completed)

    def _start_send(self, name, samples, framerate, sample_number, channel):
        # build SDATA header + data packets for one already-prepared
        # sample and send it directly (no priming needed - confirmed on
        # hardware; a flaky MIDI interface was the real cause, not this)
        if self._send_queue:
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

        self._send_queue = [sdata_message] + data_packets
        self._send_index = 0
        self._no_response_detected = False
        self._active_channel = channel
        self._active_sample_number = sample_number

        mode_note = (
            " (open loop - no MIDI input selected)" if self._is_open_loop() else ""
        )
        self.status_changed.emit(
            f"Sending SDATA header + {len(data_packets)} data packets...{mode_note}"
        )
        self._emit_progress(0, len(self._send_queue))
        self._send_current_packet()
