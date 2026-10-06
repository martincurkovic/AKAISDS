"""
Standalone dev tool - NOT part of the app. WRITES to a Yamaha A4000/A5000 (RAM only): proves "restore from backup" on real hardware.

For each target object it takes a snapshot, MUTATES several values through the session (a spread of ordinary rows and, for a sample,
the coupled wave/loop address rows), then runs the app's own `RestoreJob` with the snapshot as the backup and requires the object's
dump to come back identical (apart from the unit's "edited" flag). Reports the passes it needed, anything it could not put back, and the
residual byte differences. Run only on a unit with nothing of value in it, with the app CLOSED.

    uv run python tools/a4000_restore_check.py [--program 128] [--sample "pulse 3"] [--easy-program 1 --slots 2]
"""

import argparse
import os
import sys
import time

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from controller.sampler_controller import SamplerController
from controller.yamaha_restore import RestoreJob
from core import app_config, midi_manager as mm
from core import yamaha_params as yp
from core import yamaha_restore as yr
from core import yamaha_sysex as ysx


def norm(data):
    data = bytearray(data)
    data[1] &= 0xFE
    return bytes(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", type=int, default=128)
    ap.add_argument("--sample", default="pulse 3")
    ap.add_argument("--easy-program", type=int, default=1)
    ap.add_argument("--slots", type=int, default=2)
    args = ap.parse_args()

    app = QCoreApplication([])
    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    controller = SamplerController(midi)
    controller.set_device_type("yamaha_a4000")
    controller.status_changed.connect(lambda m: print("  status:", m) if "Wrote" not in m else None)
    session = controller.yamaha_session()
    print(f"ports: {in_name} / {out_name}; backups -> {session.backup_dir}")

    def wait(start, timeout_ms=180_000):
        got = []
        loop = QEventLoop()
        start(lambda *a: (got.append(a), loop.quit()))
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        return got[0] if got else None

    def dump(fmt, name):
        return wait(lambda cb: session.request_bulk(fmt, name, lambda d: cb(d)))[0]

    def write(scope, key, value, name, slot=None):
        row = yp.get(scope, key)
        r = wait(lambda cb: session.write_parameter(row, value, name, lambda res: cb(res), slot=slot))[0]
        if not r.ok:
            raise SystemExit(f"setup write failed: {key}={value}: {r.message}")

    def round_trip(label, fmt, name, mutations):
        print(f"\n=== {label} ===")
        original = dump(fmt, name)
        for scope, key, value, slot in mutations:
            write(scope, key, value, name, slot)
        mutated = dump(fmt, name)
        print(f"  mutated {len(mutations)} value(s); bytes different from the original: {len(yr.residual_offsets(original, mutated))}")
        job = RestoreJob(session, original)
        plan = wait(lambda cb: job.prepare(lambda p, m: cb(p, m)))
        plan, message = plan
        if plan is None:
            print("  PREPARE FAILED:", message)
            return False
        print(f"  plan: {len(plan.items)} value(s) to write:", ", ".join(i.label for i in plan.items))
        for note in plan.notes:
            print("  note:", note)
        t0 = time.monotonic()
        result = wait(lambda cb: job.run(lambda r: cb(r), on_progress=lambda d, t, label: None))[0]
        final = dump(fmt, name)
        identical = norm(final.data) == norm(original.data)
        print(f"  restore: ok={result.ok} written={result.written} passes={result.passes} in {time.monotonic() - t0:.1f}s; {result.message}")
        print(f"  snapshot of the pre-restore state: {result.snapshot_path}")
        print(f"  dump identical to the original (ignoring the edited flag): {identical}")
        if not identical:
            print("  bytes still different:", yr.residual_offsets(original, final))
        return result.ok and identical

    ok = True
    prog = ysx.program_object_name(args.program)
    ok &= round_trip(
        f"program {prog}", "PG", prog,
        [("program", "program_level", 50, None), ("program", "transpose", -12, None), ("program", "lfo_cycle", 3, None),
         ("program", "lfo_wave", 2, None), ("program", "portamento_rate", 40, None), ("program", "ad_in_l_pan", -20, None)],
    )
    easy = ysx.program_object_name(args.easy_program)
    easy_dump = dump("PG", easy)
    count = yp.extract(yp.get("program", "assigned_samples"), easy_dump.data)
    slots = min(count, args.slots)
    if slots:
        ok &= round_trip(
            f"program {easy} Easy Edit (slots 0-{slots - 1})", "PG", easy,
            [("easy_edit", "level_offset", 20, s) for s in range(slots)] + [("easy_edit", "pan_offset", -10, s) for s in range(slots)]
            + [("easy_edit", "key_range_shift", 5, 0)],
        )
    else:
        print(f"\n(program {easy} has no assigned samples: skipping the Easy Edit round trip)")
    ok &= round_trip(
        f"sample {args.sample!r}", "SP", args.sample,
        [("sample", "filter_cutoff", 77, None), ("sample", "sample_level", 64, None), ("sample", "pan", -30, None),
         ("sample", "loop_mode", 2, None), ("sample", "key_range_low", 40, None), ("sample", "eq_gain", 70, None)],
    )
    original = dump("SP", args.sample)
    ok &= round_trip(
        f"sample {args.sample!r} - COUPLED wave/loop addresses", "SP", args.sample,
        [("sample", "wave_start_address", 4, None), ("sample", "loop_start_address", 8, None), ("sample", "loop_length", 50, None),
         ("sample", "loop_end_address", 90, None)],
    )
    print("\nALL ROUND TRIPS RESTORED EXACTLY" if ok else "\nSOMETHING DID NOT COME BACK - see above")
    midi.close_input()
    midi.close_output()


if __name__ == "__main__":
    main()
