"""
Standalone dev tool - NOT part of the app. READ-ONLY smoke test of controller/yamaha_session.py against a real
Yamaha A4000 through the app's own MidiManager + SamplerController (the exact path the editor uses).

Reads the object list, dumps one program and one sample, and times a scan of every program's "assigned samples"
count (what the editor's "hide empty programs" filter needs). Writes nothing. Quit the AKAISDS app first.

    uv run python tools/a4000_session_smoke.py [--programs 128]
"""

import argparse
import os
import sys
import time

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QCoreApplication, QTimer

from controller.sampler_controller import SamplerController
from core import app_config, midi_manager as mm
from core import yamaha_params as yp
from core import yamaha_sysex as ysx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--programs", type=int, default=128, help="how many programs to scan")
    args = ap.parse_args()

    app = QCoreApplication([])
    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    controller = SamplerController(midi)
    controller.set_device_type("yamaha_a4000")
    # (SamplerController connects itself to midi.sysex_received - connecting it again delivers every message twice)
    controller.status_changed.connect(lambda m: print("  status:", m))
    session = controller.yamaha_session()
    print(f"ports: {in_name} / {out_name}; device number {session.device}")

    t0 = time.monotonic()
    state = {}

    def lap(label):
        now = time.monotonic()
        print(f"{label}: {now - state.get('t', t0):.2f}s")
        state["t"] = now

    def step_list(entries):
        lap("object list")
        print("  objects:", None if entries is None else len(entries))
        session.request_bulk("PG", "001", step_program)

    def step_program(dump):
        lap("program 001 dump")
        print("  ", None if dump is None else (dump.fmt, dump.name, len(dump.data)))
        session.request_bulk("SP", "sine wave", step_sample)

    def step_sample(dump):
        lap("sample dump")
        print("  ", None if dump is None else (dump.fmt, dump.name, len(dump.data)))
        step_scan()

    def step_scan():
        assigned = yp.get("program", "assigned_samples")
        counts = {}
        remaining = [args.programs]

        def got(number):
            def cb(results):
                msg = results[0]
                counts[number] = None if msg is None else yp.decode_reply(assigned, msg.data)
                if args.programs <= 8:
                    print(f"  program {number}: raw {None if msg is None else msg.data.hex()} -> {counts[number]}")
                remaining[0] -= 1
                if remaining[0] == 0:
                    lap(f"scan of {args.programs} programs' assigned-sample counts")
                    nonempty = {n: c for n, c in counts.items() if c}
                    print("  counts:", dict(sorted(counts.items())) if len(counts) <= 20 else "(%d programs)" % len(counts))
                    print("  non-empty programs:", nonempty or "none", "| unanswered:", [n for n, c in counts.items() if c is None])
                    QTimer.singleShot(0, app.quit)
            return cb

        for n in range(1, args.programs + 1):
            session.request_parameters("program", ysx.program_object_name(n), [assigned.p], got(n))

    session.request_object_list(step_list)
    QTimer.singleShot(120_000, app.quit)
    app.exec()
    midi.close_input()
    midi.close_output()


if __name__ == "__main__":
    main()
