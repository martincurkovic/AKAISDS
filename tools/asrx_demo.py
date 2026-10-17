"""
Standalone dev tool - NOT part of the app.

Opens the Transfer Dashboard wired to core.demo_asrx.FakeASRX instead of real MIDI ports, with the Sampler Type set to Ensoniq ASR-X, so the ASR-X list,
receive and send UI can be iterated on with no hardware. A sent file lands in the fake as a new sample. The fake speaks the real wire format (as the codec reads
the ASR-X ROM), so this exercises the real SamplerController -> AsrxTransfers -> core.asrx_sysex stack; only the ports are fake.
The Settings dialog is not usable here (it would re-open real ports) - everything else is. The log (~/.akaisds/akaisds.log) shows the whole conversation.

NOTE: the fake is built from the same reading of the ROM as the app, so a pass here proves the choreography hangs together, not that a real ASR-X agrees.
(AKAISDS_DEMO_INSTANT=1 does nothing here: the fake answers at once, the engine's own wire-time estimates are the only pacing.)

Run from the repo root:   uv run python tools/asrx_demo.py
Headless self-check:      QT_QPA_PLATFORM=offscreen uv run python tools/asrx_demo.py --smoke
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
from core.demo_asrx import FakeASRX
from core.midi_manager import MidiManager
from ui import theme
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

    fake = FakeASRX()
    midi_manager = MidiManager()
    midi_manager.raw_output = _FakePort(fake, midi_manager)
    midi_manager.input_name = "Demo ASR-X (in)"
    midi_manager.output_name = "Demo ASR-X (out)"

    controller = SamplerController(midi_manager)
    controller.set_device_type(sampler_models.ENSONIQ_ASRX)

    dashboard = TransferDashboard(controller, midi_manager)
    window = QMainWindow()
    window.setWindowTitle("AKAISDS - Ensoniq ASR-X demo (fake sampler)")
    window.resize(1030, 600)
    window.setCentralWidget(dashboard)
    window.setStatusBar(dashboard.status_bar)
    window.show()

    controller.refresh_sample_list()
    if smoke:

        def _report():
            rows = dashboard.list_hardware.count()
            expected = len(fake.visible_slots)
            print(f"smoke: {rows} samples listed; status: {dashboard.status_bar.currentMessage()!r}")
            app.exit(0 if rows == expected else 1)

        QTimer.singleShot(800, _report)
    sys.exit(app.exec())


if __name__ == "__main__":
    main(smoke="--smoke" in sys.argv)
