from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPushButton,
)
from core import app_config
from ui.qt_helpers import widen_popup_to_fit_items
import time
import mido

_LOOPBACK_TEST_SIZES = [8, 32, 64, 127, 256, 512, 1024, 1536, 2048, 2560, 3072]
_LOOPBACK_RECEIVE_TIMEOUT = 1.5  # seconds to wait for each message to return


class MidiSettingsDialog(QDialog):
    def __init__(self, midi_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MIDI Settings")
        self.midi_manager = midi_manager

        layout = QFormLayout(self)

        self.combo_input = QComboBox()
        self.combo_output = QComboBox()
        self.combo_input.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.combo_output.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._populate_ports()

        layout.addRow(QLabel("MIDI Input:"), self.combo_input)
        layout.addRow(QLabel("MIDI Output:"), self.combo_output)

        loopback_note = QLabel(
            "\nTo test a MIDI interface's SysEx reliability:\n"
            "Connect a cable from its MIDI OUT port back into\nits own MIDI IN port.\n\n"
            "Select the ports above and then run the test\n(may take 5-20 seconds to complete)."
        )
        layout.addRow(loopback_note)

        self.btn_loopback_test = QPushButton("Loopback Test")
        self.btn_loopback_test.clicked.connect(self._run_loopback_test)
        layout.addRow(self.btn_loopback_test)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._apply_and_close)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

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

        self.midi_manager.open_input(input_name)
        self.midi_manager.open_output(output_name)

        # remember these for next launch
        app_config.save_ports(input_name, output_name)

        self.accept()

    def _run_loopback_test(self):
        input_name = self.combo_input.currentData()
        output_name = self.combo_output.currentData()

        if not input_name or not output_name:
            QMessageBox.warning(
                self,
                "Loopback Test",
                "Select both a MIDI Input and MIDI Output above first - "
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
            QMessageBox.critical(self, "Loopback Test", f"Couldn't run the test {e}")
            results = None
        finally:
            # restore th app's real midi connection upon test completion
            self.midi_manager.open_input(previous_input)
            self.midi_manager.open_output(previous_output)
            self.btn_loopback_test.setEnabled(True)
            self.btn_loopback_test.setText("Loopback Test")

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
            f"corruped - sent {len(expected_data)} bytes, got {len(received_data)} "
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
