"""
Standalone dev tool - NOT part of the app. WRITES to a Yamaha A4000/A5000 (RAM only): proves "assign samples to a program" and the
Easy Edit part of "restore from backup" through the app's own session code (YamahaSession.change_link, RestoreJob).

On a throwaway program (default 128) it assigns three factory samples, checks the slots and the unit's own confirmation, gives each slot
distinct Easy Edit values, then runs a RestoreJob round trip (mutate -> restore from a snapshot -> the dump must match), removes the
middle sample (the later slots must close up and keep their values), and finally removes everything. Run only on a unit with nothing of
value in it, with the app CLOSED.

    uv run python tools/a4000_assign_check.py [--program 128] [--samples "pulse 1,pulse 2,pulse 3"]
"""

import argparse
import os
import sys

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from controller.sampler_controller import SamplerController
from controller.yamaha_restore import RestoreJob
from core import app_config, midi_manager as mm
from core import yamaha_params as yp
from core import yamaha_restore as yr
from core import yamaha_sysex as ysx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", type=int, default=128)
    ap.add_argument("--samples", default="pulse 1,pulse 2,pulse 3")
    args = ap.parse_args()
    names = args.samples.split(",")
    prog = ysx.program_object_name(args.program)

    app = QCoreApplication([])
    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    controller = SamplerController(midi)
    controller.set_device_type("yamaha_a4000")
    session = controller.yamaha_session()
    print(f"ports: {in_name} / {out_name}; program {prog}; samples {names}")

    def wait(start, timeout_ms=180_000):
        got = []
        loop = QEventLoop()
        start(lambda *a: (got.append(a), loop.quit()))
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        return got[0][0] if got else None

    def dump():
        return wait(lambda cb: session.request_bulk("PG", prog, lambda d: cb(d)))

    def link(sample, on):
        r = wait(lambda cb: session.change_link(prog, sample, on, lambda res: cb(res)))
        print(f"  {'assign' if on else 'remove':6} {sample!r:10} -> ok={r.ok} unit says linked={r.linked}: {r.message}")
        return r

    def slots(label):
        d = dump()
        count = yp.extract(yp.get("program", "assigned_samples"), d.data)
        rows = [(yp.extract(yp.get("easy_edit", "assigned_name"), d.data, s), yp.extract(yp.get("easy_edit", "level_offset"), d.data, s),
                 yp.extract(yp.get("easy_edit", "pan_offset"), d.data, s)) for s in range(count)]
        print(f"  {label}: " + ("; ".join(f"[{i}] {n!r} level {lv} pan {pn}" for i, (n, lv, pn) in enumerate(rows)) or "(none)"))
        return d, rows

    def write(key, value, slot):
        r = wait(lambda cb: session.write_parameter(yp.get("easy_edit", key), value, prog, lambda res: cb(res), slot=slot))
        if not r.ok:
            raise SystemExit(f"write failed: {r.message}")

    ok = True
    print("\n=== assign ===")
    d0, rows = slots("before")
    if rows:
        raise SystemExit(f"program {prog} already has samples - pick an empty throwaway program")
    for n in names:
        ok &= link(n, True).ok
    d1, rows = slots("after assigning")
    ok &= [r[0] for r in rows] == names
    print("  slots in the order assigned:", [r[0] for r in rows] == names)
    for slot, (lv, pn) in enumerate(((11, -5), (22, 6), (33, -7))[: len(names)]):
        write("level_offset", lv, slot)
        write("pan_offset", pn, slot)
    d2, rows = slots("with distinct Easy Edit values")

    print("\n=== restore round trip on the Easy Edit values ===")
    for slot, (lv, pn) in enumerate(((50, 20), (-40, -20), (60, 9))[: len(names)]):
        write("level_offset", lv, slot)
        write("pan_offset", pn, slot)
    job = RestoreJob(session, d2)
    plan, message = wait(lambda cb: job.prepare(lambda p, m: cb(p, m))) if False else (None, None)
    got = []
    loop = QEventLoop()
    job.prepare(lambda p, m: (got.append((p, m)), loop.quit()))
    QTimer.singleShot(60_000, loop.quit)
    loop.exec()
    plan, message = got[0]
    print(f"  plan: {len(plan.items)} value(s):", ", ".join(i.label for i in plan.items))
    result = wait(lambda cb: job.run(lambda r: cb(r)))
    final, rows = slots("after the restore")
    same = yr.residual_offsets(d2, final)
    print(f"  restore ok={result.ok} written={result.written} passes={result.passes}; bytes differing from the snapshot: {same}")
    ok &= result.ok and not same

    print("\n=== remove the middle sample ===")
    ok &= link(names[1], False).ok
    d3, rows = slots("after removing the middle one")
    ok &= [r[0] for r in rows] == [names[0], names[2]] and [r[1] for r in rows] == [11, 33]
    print("  later slot closed up and kept its value:", [r[0] for r in rows] == [names[0], names[2]] and [r[1] for r in rows] == [11, 33])

    print("\n=== remove everything ===")
    for n in (names[0], names[2]):
        ok &= link(n, False).ok
    d4, rows = slots("after removing all")
    ok &= not rows
    for n in names:
        sp = wait(lambda cb: session.request_bulk("SP", n, lambda d: cb(d)))
        print(f"  sample {n!r}: used in programs {yp.linked_programs(sp.data)}")
        ok &= args.program not in yp.linked_programs(sp.data)
    print("\nASSIGN / REMOVE / RESTORE ALL BEHAVED" if ok else "\nSOMETHING DID NOT BEHAVE - see above")
    midi.close_input()
    midi.close_output()


if __name__ == "__main__":
    main()
