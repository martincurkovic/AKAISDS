"""
Standalone dev tool - NOT part of the app.

Opens the Transfer Dashboard wired to core.demo_s950.FakeS950 instead of real
MIDI ports, with the Sampler Type set to Akai S900/S950, so the S900/S950
transfer UI (browse, send, receive, rename, the experimental warning) and the
read-only program viewer (the Open Editor button) can be iterated on with no hardware. The fake speaks the real wire format, so this
exercises the real SamplerController -> S950Transfers -> core.s950_sysex stack;
only the ports are fake. The Settings dialog is not usable here (it would
re-open real ports) - everything else is.

Run from the repo root:   uv run python tools/s950_demo.py
Headless self-check:      QT_QPA_PLATFORM=offscreen uv run python tools/s950_demo.py --smoke
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
from core.demo_s950 import FakeS950
from core.midi_manager import MidiManager
from ui import theme
from ui.dashboard import TransferDashboard


class _FakePort:
    # the rtmidi-style output MidiManager.send_sysex writes to. Replies are
    # delivered from the event loop (never inside the send call), like the
    # real input callback would.
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

    fake = FakeS950()
    midi_manager = MidiManager()
    midi_manager.raw_output = _FakePort(fake, midi_manager)
    midi_manager.input_name = "Demo S900/S950 (in)"
    midi_manager.output_name = "Demo S900/S950 (out)"

    controller = SamplerController(midi_manager)
    controller.set_device_type(sampler_models.AKAI_S900_S950)

    dashboard = TransferDashboard(controller, midi_manager)
    window = QMainWindow()
    window.setWindowTitle("AKAISDS - S900/S950 demo (fake sampler)")
    window.resize(1030, 600)
    window.setCentralWidget(dashboard)
    window.setStatusBar(dashboard.status_bar)
    window.show()

    controller.refresh_sample_list()
    if smoke:
        def _report():
            rows = dashboard.list_hardware.count()
            print(f"smoke: {rows} samples listed; status: {dashboard.status_bar.currentMessage()!r}")
            app.exit(0 if rows == len(fake.samples) else 1)

        QTimer.singleShot(800, _report)
    sys.exit(app.exec())


if __name__ == "__main__":
    main(smoke="--smoke" in sys.argv)
