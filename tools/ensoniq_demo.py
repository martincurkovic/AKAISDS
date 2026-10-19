"""
Standalone dev tool - NOT part of the app.

Opens the Transfer Dashboard wired to core.demo_ensoniq.FakeEnsoniq instead of real MIDI ports, with the Sampler Type set to Ensoniq
EPS/ASR, so the Ensoniq list, receive and send UI can be iterated on with no hardware. A sent file lands in the fake as a new instrument. The fake speaks the real wire format (as the codec reads
Ensoniq's spec), so this exercises the real SamplerController -> EnsoniqTransfers -> core.ensoniq_sysex stack; only the ports are fake.
The Settings dialog is not usable here (it would re-open real ports) - everything else is. Sending is not built yet.

NOTE: the fake is built from the same reading of the spec as the app, so a pass here proves the choreography hangs together, not that a
real Ensoniq agrees.

Run from the repo root:   uv run python tools/ensoniq_demo.py
Headless self-check:      QT_QPA_PLATFORM=offscreen uv run python tools/ensoniq_demo.py --smoke
"""

import os
import sys

# MidiManager must use the raw-port path (send_sysex -> raw_output) the fake plugs into
os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow

from controller.sampler_controller import SamplerController
from core import sampler_models
from core.demo_ensoniq import FakeEnsoniq
from core.midi_manager import MidiManager
from ui import theme, wheel_guard
from ui.dashboard import TransferDashboard


class _FakePort:
    # the rtmidi-style output MidiManager.send_sysex writes to. Replies are delivered from the event loop (never inside the send call),
    # like the real input callback would.
    def __init__(self, fake, midi_manager):
        self._fake, self._midi = fake, midi_manager

    def send_message(self, message):
        self._fake.handle(list(message))
        QTimer.singleShot(0, self._deliver)

    def _deliver(self):
        while (got := self._fake.inp.get_message()) is not None:
            self._midi._on_raw_message(got[0])

    def close_port(self):
        pass


def main(smoke=False):
    app = QApplication(sys.argv)
    theme.apply_to_app(app)
    wheel_guard.install(app)

    fake = FakeEnsoniq()
    midi_manager = MidiManager()
    midi_manager.raw_output = _FakePort(fake, midi_manager)
    midi_manager.input_name = "Demo Ensoniq (in)"
    midi_manager.output_name = "Demo Ensoniq (out)"

    controller = SamplerController(midi_manager)
    controller.set_device_type(sampler_models.ENSONIQ_EPS_ASR)

    dashboard = TransferDashboard(controller, midi_manager)
    window = QMainWindow()
    window.setWindowTitle("AKAISDS - Ensoniq EPS/ASR demo (fake sampler)")
    window.resize(1030, 600)
    window.setCentralWidget(dashboard)
    window.setStatusBar(dashboard.status_bar)
    window.show()

    controller.refresh_sample_list()
    if smoke:

        def _report():
            rows = dashboard.list_hardware.count()
            expected = sum(len(layer["wavesamples"]) for inst in fake.instruments.values() for layer in inst["layers"].values())
            print(f"smoke: {rows} samples listed; status: {dashboard.status_bar.currentMessage()!r}")
            app.exit(0 if rows == expected else 1)

        QTimer.singleShot(800, _report)
    sys.exit(app.exec())


if __name__ == "__main__":
    main(smoke="--smoke" in sys.argv)
