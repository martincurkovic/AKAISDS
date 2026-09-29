from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QProgressBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt
from core import app_config, audio_preview, debug_log, midi_identity
from ui.qt_helpers import (
    FullWidthTabBar,
    build_scroll_area,
    build_section_card,
    widen_popup_to_fit_items,
)
from ui import tooltips
import time
import mido

_LOOPBACK_TEST_SIZES = [8, 32, 64, 127, 256, 512, 1024, 1536, 2048, 2560, 3072]

# frames, same options/unit a DAW's own audio buffer-size combobox uses
# (e.g. Ableton Live) rather than milliseconds - converted to milliseconds
# at playback time (core/audio_preview.py, miniaudio.PlaybackDevice's
# buffersize_msec), since that conversion depends on the sample rate
# actually in use
_BUFFER_SIZE_OPTIONS = [32, 64, 128, 256, 512, 1024, 2048]

# seconds to wait for each message to return. also used for hardware test
_LOOPBACK_RECEIVE_TIMEOUT = 1.5


class MidiSettingsDialog(QDialog):
    # Two tabs, each a couple of section cards (same look/sizing rules as
    # ui.qt_helpers.build_section_card - originally Program Editor-only,
    # moved out to be shared once this dialog needed it too): "Audio/MIDI"
    # (MIDI Input/Output + Audio Output, the two things you set up once and
    # rarely touch again) and "Troubleshooting" (Hardware Test + Interface
    # Test, the two things you only reach for when something's not working).
    # Used to be 4 flat tabs (MIDI Settings/MIDI Hardware Test/MIDI
    # Interface Test/Audio Preview, the last added alongside the Slice
    # Editor's click-to-preview feature) - collapsed at the user's own
    # request once 4 tabs made the dialog uncomfortably narrow. Each page
    # is wrapped in build_scroll_area rather than added directly, same
    # reasoning as the Program Editor's own tabs: past the minimum size,
    # a tab scrolls instead of the window being forced to grow to fit
    # everything unscrolled.
    def __init__(self, midi_manager, sampler_controller, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.midi_manager = midi_manager
        self.sampler_controller = sampler_controller

        outer_layout = QVBoxLayout(self)
        self.setMinimumHeight(560)
        self.setMinimumWidth(560)

        tabs = QTabWidget()
        tabs.setTabBar(FullWidthTabBar(tabs))
        outer_layout.addWidget(tabs)

        tabs.addTab(
            build_scroll_area(self._build_audio_midi_page()), "Audio/MIDI"
        )
        tabs.addTab(
            build_scroll_area(self._build_troubleshooting_page()), "Troubleshooting"
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._apply_and_close)
        buttons.rejected.connect(self.reject)
        outer_layout.addWidget(buttons)

    # --- Audio/MIDI tab ------------------------------------------------------

    def _build_audio_midi_page(self):
        midi_form = QFormLayout()

        self.combo_input = QComboBox()
        self.combo_output = QComboBox()
        self.combo_input.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.combo_output.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._populate_ports()

        midi_form.addRow(QLabel("MIDI Input:"), self.combo_input)
        midi_form.addRow(QLabel("MIDI Output:"), self.combo_output)

        # SysEx device ID (0-127) - NOT THE SAME AS MIDI CHANNELS 1-16!!!
        # Set for daisy chained devices to avoid message conflicts
        self.spin_channel = QSpinBox()
        self.spin_channel.setRange(0, 127)
        self.spin_channel.setValue(self.sampler_controller.channel)
        self.spin_channel.setToolTip(tooltips.DEVICE_ID_CHANNEL)
        midi_form.addRow(
            QLabel("Device ID (SysEx channel 0-127):"), self.spin_channel
        )

        # sampler device type - determines which protocol family gets used for each step
        # (send, receive, list, delete, rename etc)
        self.combo_device_type = QComboBox()
        self.combo_device_type.addItem("Akai Sampler", "akai")
        self.combo_device_type.addItem("Generic SDS", "generic")
        idx = self.combo_device_type.findData(self.sampler_controller.device_type)
        if idx >= 0:
            self.combo_device_type.setCurrentIndex(idx)
        self.combo_device_type.setToolTip(tooltips.SAMPLER_TYPE)
        midi_form.addRow(QLabel("Sampler Type:"), self.combo_device_type)

        midi_card = build_section_card("MIDI Input/Output", midi_form)

        # Unrelated to MIDI/SysEx transfers - this is the output device/
        # buffer size the Slice Editor's click-to-preview playback uses
        # (Program Editor > Samples tab > Slice Editor). Still lives on
        # this same page since it's still "I/O hardware settings," just
        # for the computer's own speakers instead of the sampler.
        audio_form = QFormLayout()

        audio_note = QLabel(
            "Used by the Slice Editor's click-to-preview playback. "
            "Doesn't affect MIDI/SysEx transfers to the sampler."
        )
        audio_note.setWordWrap(True)
        audio_form.addRow(audio_note)

        self.combo_audio_output = QComboBox()
        self.combo_audio_output.addItem("(System Default)", None)
        for device in audio_preview.list_output_devices():
            self.combo_audio_output.addItem(
                device["name"], audio_preview.device_id_string(device)
            )
        self.combo_audio_output.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        saved_device_id = app_config.get_saved_audio_output_device()
        idx = self.combo_audio_output.findData(saved_device_id)
        self.combo_audio_output.setCurrentIndex(idx if idx >= 0 else 0)
        widen_popup_to_fit_items(self.combo_audio_output)
        audio_form.addRow(QLabel("Output Device:"), self.combo_audio_output)

        self.combo_audio_buffer = QComboBox()
        for frames in _BUFFER_SIZE_OPTIONS:
            self.combo_audio_buffer.addItem(f"{frames} samples", frames)
        self.combo_audio_buffer.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        saved_buffer_samples = app_config.get_saved_audio_buffer_samples()
        idx = self.combo_audio_buffer.findData(saved_buffer_samples)
        self.combo_audio_buffer.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_audio_buffer.setToolTip(tooltips.AUDIO_PREVIEW_BUFFER_SIZE)
        audio_form.addRow(QLabel("Buffer Size:"), self.combo_audio_buffer)

        audio_card = build_section_card(
            "Audio Output (Slice Editor Preview)", audio_form
        )

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.addWidget(midi_card)
        page_layout.addWidget(audio_card)
        page_layout.addStretch()
        return page

    # --- Troubleshooting tab --------------------------------------------------

    def _build_troubleshooting_page(self):
        hardware_test_layout = QVBoxLayout()

        hardware_test_note = QLabel(
            "To test your MIDI hardware setup, "
            "connect your MIDI device to both your interface's MIDI IN and OUT ports. "
            "Select the ports on the Audio/MIDI tab, then run the test below."
        )
        hardware_test_note.setWordWrap(True)
        hardware_test_note.setAlignment(
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignTop
        )
        hardware_test_layout.addWidget(hardware_test_note)

        self.id_results_label = QLabel()
        self.id_results_label.setWordWrap(True)
        hardware_test_layout.addWidget(self.id_results_label)

        self.btn_run_id_request = QPushButton("Run Hardware Test")
        self.btn_run_id_request.setToolTip(tooltips.HARDWARE_TEST_BUTTON)
        self.btn_run_id_request.clicked.connect(self._run_identity_request)
        hardware_test_layout.addWidget(self.btn_run_id_request)

        self.id_progress = QProgressBar()
        self.id_progress.setRange(0, 0)
        self.id_progress.setVisible(False)
        hardware_test_layout.addWidget(self.id_progress)

        hardware_test_card = build_section_card("Hardware Test", hardware_test_layout)

        interface_test_layout = QVBoxLayout()

        loopback_note = QLabel(
            "To test a MIDI interface's SysEx reliability, "
            "connect a cable from its MIDI OUT port back into its own MIDI IN port. "
            "Select the ports on the Audio/MIDI tab, then run the test below (may take 5-20 seconds to complete)."
        )
        loopback_note.setWordWrap(True)
        loopback_note.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        interface_test_layout.addWidget(loopback_note)

        self.btn_loopback_test = QPushButton("Run Loopback Test")
        self.btn_loopback_test.setToolTip(tooltips.LOOPBACK_TEST_BUTTON)
        self.btn_loopback_test.clicked.connect(self._run_loopback_test)
        interface_test_layout.addWidget(self.btn_loopback_test)

        self.loopback_progress = QProgressBar()
        self.loopback_progress.setRange(0, 0)
        self.loopback_progress.setVisible(False)
        interface_test_layout.addWidget(self.loopback_progress)

        interface_test_card = build_section_card(
            "Interface Test", interface_test_layout
        )

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.addWidget(hardware_test_card)
        page_layout.addWidget(interface_test_card)
        page_layout.addStretch()
        return page

    def _populate_ports(self):
        self.combo_input.addItem("(None)", None)
        for name in self.midi_manager.list_inputs():
            self.combo_input.addItem(name, name)

        self.combo_output.addItem("(None)", None)
        for name in self.midi_manager.list_outputs():
            self.combo_output.addItem(name, name)

        if self.midi_manager.input_name:
            idx = self.combo_input.findData(self.midi_manager.input_name)
            if idx >= 0:
                self.combo_input.setCurrentIndex(idx)

        if self.midi_manager.output_name:
            idx = self.combo_output.findData(self.midi_manager.output_name)
            if idx >= 0:
                self.combo_output.setCurrentIndex(idx)

        widen_popup_to_fit_items(self.combo_input)
        widen_popup_to_fit_items(self.combo_output)

    def _apply_and_close(self):
        input_name = self.combo_input.currentData()
        output_name = self.combo_output.currentData()
        channel = self.spin_channel.value()
        device_type = self.combo_device_type.currentData()

        try:
            self.midi_manager.open_input(input_name)
            self.midi_manager.open_output(output_name)
        except Exception as e:
            # unlike the Identity Request/Loopback Test paths above, this
            # was previously completely unguarded - a real mido open
            # failure here (device unplugged between refresh and click, a
            # driver error) would propagate out of this Qt slot uncaught,
            # with no log and no dialog
            debug_log.get_logger().error(
                "MidiSettingsDialog: couldn't open MIDI port(s) "
                f"(input={input_name!r}, output={output_name!r})",
                exc_info=True,
            )
            QMessageBox.critical(
                self,
                "Settings",
                f"Couldn't open the selected MIDI port(s): {e}",
            )
            return

        # the live MIDI setup actually applied, in one place - lets a user's
        # bug report be checked against this instead of trusting "I set it
        # up correctly", and against app_config's own "saved" log line to
        # catch a live-vs-persisted mismatch like the one this pair of logs
        # was added to catch
        debug_log.get_logger().info(
            f"MidiSettingsDialog: applied input={input_name!r} "
            f"output={output_name!r} channel={channel} device_type={device_type!r}"
        )

        self.sampler_controller.set_channel(channel)
        self.sampler_controller.set_device_type(device_type)

        # remember these for next launch
        app_config.save_ports(input_name, output_name)
        app_config.save_channel(channel)
        app_config.save_device_type(device_type)
        app_config.save_audio_output_device(self.combo_audio_output.currentData())
        app_config.save_audio_buffer_samples(self.combo_audio_buffer.currentData())

        self.accept()

    def _run_identity_request(self):
        input_name = self.combo_input.currentData()
        output_name = self.combo_output.currentData()

        if not input_name or not output_name:
            QMessageBox.warning(
                self,
                "Identity Request",
                "Select both a MIDI Input and MIDI Output first - "
                "the ports connected to and from the sampler.",
            )
            return

        # release the current open midi connection for the duration of the identity request.
        # prevents conflict with 2 different ports open
        previous_input = self.midi_manager.input_name
        previous_output = self.midi_manager.output_name
        self.midi_manager.open_input(None)
        self.midi_manager.open_output(None)

        self.btn_run_id_request.setEnabled(False)
        self.btn_run_id_request.setText("Testing...")
        self.id_progress.setVisible(True)
        QApplication.processEvents()

        try:
            midi_id_input = mido.open_input(input_name)
            midi_id_output = mido.open_output(output_name)
            try:
                # send identity request message and parse response
                success, id_response = self._send_identity_request(
                    midi_id_input, midi_id_output
                )
                if success:
                    device_type = self.combo_device_type.currentData()
                    if device_type == "akai":
                        self.id_results_label.setText(
                            self._format_stat_result(id_response)
                        )
                    else:
                        self.id_results_label.setText(
                            self._format_identity_result(id_response)
                        )
                else:
                    self.id_results_label.setText(str(id_response))
                QApplication.processEvents()
            finally:
                midi_id_input.close()
                midi_id_output.close()
        except Exception as e:
            # same gap _apply_and_close's own comment above describes -
            # this is literally the built-in "diagnose my MIDI connection"
            # tool, so a failure IN the tool itself needs to be as visible
            # in the log as everything else, not just a dialog the user has
            # to transcribe by hand
            debug_log.get_logger().error(
                "MidiSettingsDialog: identity request failed "
                f"(input={input_name!r}, output={output_name!r})",
                exc_info=True,
            )
            self.id_progress.setVisible(False)
            QMessageBox.critical(
                self, "Identity Request", f"Couldn't run the test: {e}"
            )
        finally:
            # restore the app's REAL connection
            self.midi_manager.open_input(previous_input)
            self.midi_manager.open_output(previous_output)
            self.btn_run_id_request.setEnabled(True)
            self.btn_run_id_request.setText("Run Hardware Test")
            self.id_progress.setVisible(False)

    def _send_identity_request(self, input_port, output_port):
        while input_port.poll() is not None:
            pass  # drain any stale bytes in the buffer

        device_type = self.combo_device_type.currentData()

        if device_type == "akai":
            output_port.send(
                mido.Message("sysex", data=midi_identity.build_rstat_request_message())
            )
        else:
            output_port.send(
                mido.Message(
                    "sysex", data=midi_identity.build_identity_request_message()
                )
            )
        deadline = time.monotonic() + _LOOPBACK_RECEIVE_TIMEOUT
        response = None
        while time.monotonic() < deadline:
            incoming = input_port.poll()
            if incoming is not None and incoming.type == "sysex":
                response = incoming
                break
            QApplication.processEvents()

        if response is None:
            return False, "Sampler hardware not found - please check your MIDI setup"

        if device_type == "akai":
            received_data = midi_identity.parse_stat_response(response.data)
        else:
            received_data = midi_identity.parse_identity_response(response.data)
        return True, received_data

    def _format_stat_result(self, data):
        return (
            f"Akai S1000/S2000/S3000-family sampler\n"
            f"OS Version: {data['version_string']}\n"
            f"Program/sample slots free: {data['num_blocks_free']} / {data['max_num_blocks']}\n"
            f"Sample memory free: {data['num_words_free']:,} / {data['max_num_samp_words']:,} words"
        )

    def _format_identity_result(self, data):
        manuf_name = midi_identity.lookup_manufacturer(data["manuf_id"])
        return (
            f"Manufacturer: {manuf_name}\n"
            f"Family Code: {data['family_code']}\n"
            f"Model Number: {data['model_number']}\n"
            f"Version: {data['version_number']}"
        )

    def _run_loopback_test(self):
        input_name = self.combo_input.currentData()
        output_name = self.combo_output.currentData()

        if not input_name or not output_name:
            QMessageBox.warning(
                self,
                "Loopback Test",
                "Select both a MIDI Input and MIDI Output first - "
                "the same interface's own ports, connected to each other "
                "with a physical MIDI cable.",
            )
            return

        # release the app's REAL connection to the sampler for the duration of the test,
        # so opening with these port names again during testing won't conflict with an already open i/o
        # restored after the test is complete
        previous_input = self.midi_manager.input_name
        previous_output = self.midi_manager.output_name
        self.midi_manager.open_input(None)
        self.midi_manager.open_output(None)

        self.btn_loopback_test.setEnabled(False)
        self.btn_loopback_test.setText("Testing...")
        self.loopback_progress.setVisible(True)
        QApplication.processEvents()

        results = []  # (size, passed, detail)
        try:
            test_input = mido.open_input(input_name)
            test_output = mido.open_output(output_name)
            try:
                for size in _LOOPBACK_TEST_SIZES:
                    passed, detail = self._test_one_size(test_input, test_output, size)
                    results.append((size, passed, detail))
                    QApplication.processEvents()  # keep ui responsive during wait
            finally:
                test_input.close()
                test_output.close()
        except Exception as e:
            # same reasoning as _run_identity_request's own matching log
            # call - this is one of the two built-in hardware diagnostic
            # tools, so its own failures need to reach the log too
            debug_log.get_logger().error(
                "MidiSettingsDialog: loopback test failed "
                f"(input={input_name!r}, output={output_name!r})",
                exc_info=True,
            )
            self.loopback_progress.setVisible(False)
            QMessageBox.critical(self, "Loopback Test", f"Couldn't run the test: {e}")
            results = None
        finally:
            # restore th app's real midi connection upon test completion
            self.midi_manager.open_input(previous_input)
            self.midi_manager.open_output(previous_output)
            self.btn_loopback_test.setEnabled(True)
            self.btn_loopback_test.setText("Loopback Test")
            self.loopback_progress.setVisible(False)

        if results is not None:
            self._show_loopback_results(results)

    def _test_one_size(self, input_port, output_port, byte_count):
        # send ONE test SysEx message of byte_count size and check it comes back correctly
        # filled with predictable 0-127 pattern, so any dropped/corrupted data is immediately obvious
        # returns (passed: bool, detail: str)
        while input_port.poll() is not None:
            pass  # drain anything stale left over from an earlier run

        expected_data = [i % 128 for i in range(byte_count)]
        output_port.send(mido.Message("sysex", data=expected_data))

        deadline = time.monotonic() + _LOOPBACK_RECEIVE_TIMEOUT
        received = None
        while time.monotonic() < deadline:
            incoming = input_port.poll()
            if incoming is not None and incoming.type == "sysex":
                received = incoming
                break
            QApplication.processEvents()
            time.sleep(0.001)

        if received is None:
            return False, "no response (timed out) - likely dropped entirely"

        received_data = list(received.data)
        if received_data == expected_data:
            return True, "OK"

        mismatch_at = next(
            (i for i, (a, b) in enumerate(zip(expected_data, received_data)) if a != b),
            min(len(expected_data), len(received_data)),
        )
        return False, (
            f"corrupted - sent {len(expected_data)} bytes, got {len(received_data)} "
            f"back, first mismatch at byte {mismatch_at}"
        )

    def _show_loopback_results(self, results):
        lines = []
        any_failed = False
        for size, passed, detail in results:
            status = "PASS" if passed else "FAIL"
            if not passed:
                any_failed = True
            lines.append(
                f"{size:>5} bytes: {status}" + ("" if passed else f" - {detail}")
            )

        summary = "\n".join(lines)
        if any_failed:
            first_failure_size = next(size for size, passed, _ in results if not passed)
            summary += (
                f"\n\nThis interface starts failing around {first_failure_size} bytes. "
                f"Real MIDI SDS data packets are 127 bytes, and larger responses "
                f"(like a sample list with many samples can be well over 1000 bytes - "
                f"if failures start below that, this interface may struggle with "
                f"real transfers too."
            )
        else:
            summary += "\n\nAll sizes tested passed cleanly."

        QMessageBox.information(self, "Loopback Test Results", summary)
