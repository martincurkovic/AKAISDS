"""
Standalone dev tool - NOT part of the app. WRITES to a real Akai S2000/S3000 on the saved MIDI ports (app CLOSED): runs the Program Editor's OWN
"Slice Editor export + create a program from a template" code (ProgramEditorWindow._export_slices / _create_program_from_slices) against it,
offscreen, the way the Slice Editor's Export button does - the scenario that once wrote every slice into the WRONG program.

    QT_QPA_PLATFORM=offscreen uv run python tools/s2000_slices_check.py [SLICES=4] [TEMPLATE="TEST PROGRAM"]
"""

import os
import sys
import time

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import soundfile as sf
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import s2000_check as sc
from controller.sampler_controller import SamplerController
from core import app_config, midi_manager as mm
from core import program_editor_bridge as peb
from s3k import params as p
from ui import theme
from ui.program_editor_window import ProgramEditorWindow


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    template = sys.argv[2] if len(sys.argv) > 2 else "TEST PROGRAM"
    app = QApplication([])
    theme.apply_to_app(app)
    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    controller = SamplerController(midi)
    controller.set_device_type("akai")
    controller.set_sampler_model("akai_s2000_s3000") if hasattr(controller, "set_sampler_model") else None
    bridge = peb.connect(midi, "akai_s2000_s3000")

    class Host:
        sampler_controller = controller
        midi_manager = midi

        def show(self):
            pass

    window = ProgramEditorWindow(Host(), bridge=bridge)

    def spin(seconds):
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()

    def wait_for(cond, timeout=120):
        end = time.monotonic() + timeout
        while time.monotonic() < end and not cond():
            spin(0.1)
        return cond()

    def snap():
        """The window's own worker must be idle: S3kBridge is not safe for two callers at once (AGENTS.md)."""
        wait_for(window._worker.is_idle, 120)
        spin(0.5)
        wait_for(window._worker.is_idle, 120)
        return sc.snapshot(bridge)

    sc.say("INFO", f"ports {in_name} / {out_name}")
    if not sc.check(wait_for(lambda: window.program_list.count() > 0, 60), "the editor loaded the program list"):
        os._exit(1)
    before = snap()
    sc.show(before)
    names_before = [s["name"] for s in before.values()]
    template_index = names_before.index(template)

    data, rate = sf.read(os.path.join(os.path.dirname(__file__), "..", "tests", "test_audio.wav"), dtype="int16")
    if data.ndim > 1:
        data = data[:, 0]
    data = [int(v) for v in data]
    step = len(data) // count
    slices = [data[i * step : (i + 1) * step] for i in range(count)]
    slice_names = [f"S2K-{i + 1:02d}" for i in range(count)]
    sc.say("INFO", f"{count} slices of {step} frames @ {rate}; template {template!r} (index {template_index})")

    status = []
    t0 = time.monotonic()
    ok, message = window._export_slices(
        slice_names, slices, rate, 16, rate, 60, 0, 0, lambda *a: None, status.append, lambda busy: None
    )
    sc.say("PASS" if ok else "FAIL", f"export: {message} ({time.monotonic() - t0:.0f}s)")
    if not ok:
        os._exit(1)

    program_name = "S2K-CHOP"
    t0 = time.monotonic()
    ok, message = window._create_program_from_slices(
        slice_names, template_index, program_name, lambda *a: None, status.append, lambda busy: None
    )
    sc.say("PASS" if ok else "FAIL", f"create program: {message} ({time.monotonic() - t0:.0f}s)")
    spin(1)

    after = snap()
    sc.show(after)
    names_after = [s["name"] for s in after.values()]
    if not sc.check(program_name in names_after, f"{program_name!r} is in the list (index {names_after.index(program_name) if program_name in names_after else None})"):
        os._exit(1)
    new = after[names_after.index(program_name)]
    sc.check(new["groups"] == count, f"{program_name!r} has {new['groups']} keygroups (expected {count})")
    lo, hi, snames = [], [], []
    ni = names_after.index(program_name)
    wait_for(window._worker.is_idle, 120)
    for k in range(new["groups"]):
        lo.append(bridge.get_parameter(p.lookup("LONOTE", "keygroup"), ni, keygroup=k))
        hi.append(bridge.get_parameter(p.lookup("HINOTE", "keygroup"), ni, keygroup=k))
        snames.append(bridge.get_parameter(p.lookup("SNAME1", "keygroup"), ni, keygroup=k))
    sc.say("INFO", f"key ranges {list(zip(lo, hi))}; zone 1 samples {snames}")
    sc.check(lo == [36 + i for i in range(count)] and hi == lo, "each keygroup maps to its own key, C1 upward")
    sc.check(snames == slice_names, "each keygroup plays its own slice, in order")
    # nothing else on the sampler may have changed
    for i, s in before.items():
        j = names_after.index(s["name"])
        sc.check(after[j]["header"] == s["header"] and after[j]["keygroups"] == s["keygroups"], f"{s['name']!r} untouched")
    window._worker.stop()
    window._worker.wait()
    print(f"\n{sc.RESULTS.count('PASS')} passed, {sc.RESULTS.count('FAIL')} failed", flush=True)
    os._exit(1 if "FAIL" in sc.RESULTS else 0)


if __name__ == "__main__":
    main()
