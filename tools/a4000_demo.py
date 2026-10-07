"""
Standalone dev tool - NOT part of the app.

Opens the Transfer Dashboard wired to core.demo_a4000.FakeA4000 instead of real MIDI ports, with the Sampler Type set to
Yamaha A4000/A5000, so the Yamaha Program Editor (Programs and Samples tabs, the waveform/loop controls, the edit buttons and
the Slice Editor) can be used and iterated on with no hardware. The fake speaks the real wire format, so this exercises the
real SamplerController -> YamahaSession -> core.yamaha_sysex stack; only the ports are fake. The Settings dialog is not
usable here (it would re-open real ports) - everything else is.

The fake unit holds the factory waveforms plus three demo samples with real audio, so the Samples tab has something to show:
  "DEMO LOOP"   mono, loops (loop mode "Loop (continuous)"), a decaying tone with a repeating pulse
  "DEMO STEREO" stereo, one-shot: two different channels (to see the stacked stereo waveform)
  "DEMO BREAK"  mono, one-shot, four evenly spaced hits (something to slice)
Double-click a waveform to load its audio. Edits and slices create NEW samples on the fake, like they do on a real unit.

Run from the repo root:   uv run python tools/a4000_demo.py            (the Dashboard)
                          uv run python tools/a4000_demo.py --editor   (straight into the Program Editor's Samples tab)
Headless self-check:      QT_QPA_PLATFORM=offscreen uv run python tools/a4000_demo.py --smoke
"""

import math
import os
import random
import sys

# MidiManager must use the raw-port path (send_sysex -> raw_output) the fake plugs into
os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow

from controller.sampler_controller import SamplerController
from core import sampler_models
from core import yamaha_params as yp
from core.demo_a4000 import FakeA4000
from core.midi_manager import MidiManager
from ui import theme
from ui.dashboard import TransferDashboard

RATE = 22050


class _FakePort:
    # the rtmidi-style output MidiManager.send_sysex writes to. Replies are delivered from the event loop (never inside the send
    # call), like the real input callback would.
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


def _tone(seconds, hz, decay, rng):
    frames = int(seconds * RATE)
    return [
        int(26000 * math.exp(-decay * i / RATE) * (math.sin(2 * math.pi * hz * i / RATE) + 0.15 * (rng.random() - 0.5)))
        for i in range(frames)
    ]


def _hits(seconds, count, rng):
    frames = int(seconds * RATE)
    out = [0] * frames
    step = frames // count
    for n in range(count):
        for i in range(min(int(0.12 * RATE), frames - n * step)):
            env = math.exp(-28 * i / RATE)
            out[n * step + i] = int(30000 * env * (rng.random() * 2 - 1) * (1.0 if n % 2 == 0 else 0.6))
    return out


def add_demo_samples(fake):
    rng = random.Random(4000)
    fake.add_sample("DEMO LOOP", audio=_tone(1.4, 220, 1.2, rng))
    fake.add_sample("DEMO STEREO", audio=_tone(1.2, 330, 2.0, rng), audio_right=_tone(1.2, 495, 1.5, rng))
    fake.add_sample("DEMO BREAK", audio=_hits(2.0, 4, rng))
    for name in ("DEMO LOOP", "DEMO STEREO", "DEMO BREAK"):
        data = fake.samples[name]
        for key in ("sampling_frequency_l", "sampling_frequency_r"):
            yp.store(yp.get("sample", key), data, RATE)
    for name in ("DEMO STEREO", "DEMO BREAK"):  # (the fake's sample template loops: make these one-shots)
        data = fake.samples[name]
        frames = yp.extract(yp.get("sample", "wave_length"), data)
        yp.store(yp.get("sample", "loop_mode"), data, 0)
        for key in ("loop_start_address", "loop_end_address"):
            yp.store(yp.get("sample", key), data, frames)
        yp.store(yp.get("sample", "loop_length"), data, 0)
    # DEMO LOOP loops the middle of the wave (loop mode 1 = continuous); the others play once
    data = fake.samples["DEMO LOOP"]
    frames = yp.extract(yp.get("sample", "wave_length"), data)
    yp.store(yp.get("sample", "loop_mode"), data, 1)
    yp.store(yp.get("sample", "loop_start_address"), data, frames // 4)
    yp.store(yp.get("sample", "loop_end_address"), data, (frames * 3) // 4)
    yp.store(yp.get("sample", "loop_length"), data, (frames * 3) // 4 - frames // 4)
    for number, names in ((1, ["DEMO LOOP"]), (2, ["DEMO STEREO"]), (3, ["DEMO BREAK"])):
        fake.assign(number, names[0])


def main(smoke=False, editor=False):
    app = QApplication(sys.argv)
    theme.apply_to_app(app)

    fake = FakeA4000()
    add_demo_samples(fake)
    midi_manager = MidiManager()
    midi_manager.raw_output = _FakePort(fake, midi_manager)
    midi_manager.input_name = "Demo A4000 (in)"
    midi_manager.output_name = "Demo A4000 (out)"

    controller = SamplerController(midi_manager)
    controller.set_device_type(sampler_models.YAMAHA_A4000)

    dashboard = TransferDashboard(controller, midi_manager)
    window = QMainWindow()
    window.setWindowTitle("AKAISDS - Yamaha A4000 demo (fake sampler)")
    window.resize(1030, 600)
    window.setCentralWidget(dashboard)
    window.setStatusBar(dashboard.status_bar)
    window.show()

    controller.refresh_sample_list()

    def open_editor():
        dashboard.open_program_editor()
        editor_window = getattr(dashboard, "editor_window", None)
        if editor_window is not None:
            editor_window.main_tabs.setCurrentIndex(editor_window._samples_tab_index)
        return editor_window

    if editor:
        QTimer.singleShot(300, open_editor)
    if smoke:
        def _report():
            rows = dashboard.list_hardware.count()
            print(f"smoke: {rows} samples listed; status: {dashboard.status_bar.currentMessage()!r}")
            ok = rows == len(fake.samples)
            editor_window = open_editor()
            ok = ok and editor_window is not None
            print(f"smoke: editor opened: {editor_window is not None}")
            QTimer.singleShot(500, lambda: app.exit(0 if ok else 1))

        QTimer.singleShot(800, _report)
    sys.exit(app.exec())


if __name__ == "__main__":
    main(smoke="--smoke" in sys.argv, editor="--editor" in sys.argv)
