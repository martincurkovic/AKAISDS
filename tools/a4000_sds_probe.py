"""
Standalone dev tool - NOT part of the app. READ-ONLY probe of a Yamaha A4000/A5000's Sample Dump Standard numbering.

The Yamaha editor's Samples tab wants each sample's WAVEFORM, and the A4000 serves audio through plain SDS (a dump
request by sample NUMBER) - but the manual doesn't say which number is which sample NAME (it only says numbers are 0-1024,
"displayed as 1-1025", assigned to sample objects). This asks for a range of numbers through the app's own generic SDS receive
path, prints each answer's header (rate, length, loop) and a coarse picture of the waveform, so the mapping can be worked out
by recognising the factory waveforms (sine / saw / triangle / square / pulses).

It only ever sends SDS Dump Requests and their ACK/NAK handshakes - nothing is written to the unit. Quit the AKAISDS app first.

    uv run python tools/a4000_sds_probe.py [--first 0] [--last 12] [--channel 0]
"""

import argparse
import os
import sys
import tempfile

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np
import soundfile as sf
from PySide6.QtCore import QCoreApplication, QTimer

from controller.sampler_controller import SamplerController
from core import app_config, midi_manager as mm


def picture(samples, width=32):
    """A coarse look at a waveform: `width` columns, each a digit 0-9 for its height (0 = lowest, 9 = highest)."""
    x = np.asarray(samples, dtype=float)
    if x.size == 0 or x.max() == x.min():
        return "(flat)" if x.size else "(empty)"
    cols = np.array_split(x, min(width, x.size))
    levels = [(c.mean() - x.min()) / (x.max() - x.min()) for c in cols]
    return "".join(str(min(9, int(v * 10))) for v in levels)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=0)
    ap.add_argument("--last", type=int, default=12)
    ap.add_argument("--channel", type=int, default=0, help="the SDS channel (0 matches Device Number 0)")
    ap.add_argument("--timeout", type=int, default=2500, help="ms to wait for a header before calling a number empty")
    args = ap.parse_args()

    app = QCoreApplication([])
    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    controller = SamplerController(midi)
    controller.set_device_type("yamaha_a4000")
    controller._receive_timeout_ms = args.timeout
    statuses = []
    controller.status_changed.connect(statuses.append)
    print(f"ports: {in_name} / {out_name}; SDS channel {args.channel}; probing numbers {args.first}..{args.last}")

    tmp = tempfile.mkdtemp(prefix="a4000_sds_")
    state = {"n": args.first, "path": None}

    def next_number():
        if state["n"] > args.last:
            QTimer.singleShot(0, app.quit)
            return
        state["path"] = os.path.join(tmp, f"sample_{state['n']}.wav")
        statuses.clear()
        controller.receive_sample_generic(state["n"], state["path"], args.channel)

    def finished(ok):
        n, path = state["n"], state["path"]
        if ok and os.path.exists(path):
            data, rate = sf.read(path, dtype="int16", always_2d=False)
            data = data if data.ndim == 1 else data[:, 0]
            print(f"  number {n:4d}: {len(data):6d} frames @ {rate} Hz  {picture(data)}")
        else:
            print(f"  number {n:4d}: no sample ({statuses[-1] if statuses else 'no status'})")
        state["n"] += 1
        QTimer.singleShot(150, next_number)

    controller.receive_finished.connect(finished)
    QTimer.singleShot(0, next_number)
    QTimer.singleShot(10 * 60 * 1000, app.quit)
    app.exec()
    midi.close_input()
    midi.close_output()
    print("wavs kept in", tmp)


if __name__ == "__main__":
    main()
