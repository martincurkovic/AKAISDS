"""
Standalone dev tool - NOT part of the app. WRITES to a Yamaha A4000/A5000 (RAM only): measures how the six WAVE / LOOP ADDRESS rows
of a sample (wave_start_address, wave_length, wave_end_address, loop_start_address, loop_length, loop_end_address) influence each other
when ONE is written - the coupling an editable-marker writer has to know. Use a USER sample (the factory ones ignore some of these
writes); run only on a unit with nothing of value in it, with the app CLOSED.

Raw object edits (like tools/a4000_write_verify.py - the app's own write path refuses the rows the unit ignores, and this measures
whether it really does on a user sample). From a looping baseline it writes one value, dumps, prints the six rows before/after and every
other byte that moved, then puts the baseline back.

    uv run python tools/a4000_marker_probe.py --sample "MIDI 00101" [--experiments default|NAME=VALUE,...]
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import a4000_discovery as disc  # noqa: E402
import a4000_write_verify as wv  # noqa: E402

from core import yamaha_markers as ym  # noqa: E402
from core import yamaha_params as yp  # noqa: E402
from core import yamaha_sysex as ysx  # noqa: E402

ROWS = ("wave_start_address", "wave_length", "wave_end_address", "loop_start_address", "loop_length", "loop_end_address")
BASELINES = {  # loop_mode, markers
    "loop": (1, ym.Markers(0, 1000, 3000, 3996)),
    "noloop": (0, ym.Markers(0, 3996, 3996, 3996)),
}
EXPERIMENTS = [
    ("wave_start_address", 100), ("wave_start_address", 500), ("wave_start_address", 1200),
    ("wave_length", 3000), ("wave_end_address", 3500),
    ("loop_start_address", 1500), ("loop_start_address", 100), ("loop_start_address", 3900),
    ("loop_length", 1000), ("loop_length", 3500),
    ("loop_end_address", 2500), ("loop_end_address", 3990), ("loop_end_address", 900),
    # edges: equality, tiny loops, an end beyond the wave itself, the end below the loop
    ("wave_start_address", 1000), ("loop_start_address", 3000), ("loop_end_address", 1000), ("loop_length", 1), ("loop_length", 0),
    ("wave_end_address", 4200), ("wave_end_address", 3000), ("wave_end_address", 2000), ("wave_end_address", 10),
]


def values(data):
    return {k: yp.extract(yp.get("sample", k), data) for k in ROWS}


def fmt(v):
    return " ".join(f"{k.split('_')[0] + ('W' if k.startswith('wave') else 'L') + k.split('_')[1][:3]}={v[k]}" for k in ROWS)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", default="MIDI 00101")
    ap.add_argument("--device", type=int, default=0)
    ap.add_argument("--in", dest="inp")
    ap.add_argument("--out")
    ap.add_argument("--baseline", choices=sorted(BASELINES), default="loop")
    ap.add_argument("--experiments", default="default", help="'default' or NAME=VALUE,NAME=VALUE")
    args = ap.parse_args()
    pin, pout = disc.open_ports(args)
    unit = wv.Unit(pin, pout, args.device)
    name = args.sample
    otype = ysx.OBJECT_TYPES["sample"]

    def dump():
        return unit.dump("SP", name).data

    def edit(key, value):
        row = yp.get("sample", key)
        unit.select(name, otype)
        unit.edit(yp.request_params(row, None), wv.value_bytes(row, value))

    def settle_to(target, label):
        """Write the four ADDRESSES in the planner's order until they equal `target` (the lengths follow)."""
        for _attempt in range(3):
            steps = ym.plan_marker_writes(ym.read_markers(dump()), target)
            if not steps:
                return True
            for marker, value in steps:
                edit(ym.ROW_FOR[marker], value)
        got = ym.read_markers(dump())
        if got != target:
            print(f"  !! could not settle {label}: wanted {target}; got {got}")
        return got == target

    try:
        original = dump()
        original_mode = yp.extract(yp.get("sample", "loop_mode"), original)
        original_markers = ym.read_markers(original)
        print(f"sample {name!r}: loop_mode {original_mode}; original markers {original_markers}; rows: {fmt(values(original))}")
        wv.save_backup(unit, "SP", name, f"markerprobe_{name.strip().replace(' ', '_')}")
        mode, base = BASELINES[args.baseline]
        edit("loop_mode", mode)
        print(f"going to the {args.baseline!r} baseline {base} (planner order) ...")
        settle_to(base, "baseline")
        baseline = dump()
        print(f"baseline: {fmt(values(baseline))}")
        todo = EXPERIMENTS if args.experiments == "default" else [(kv.split("=")[0], int(kv.split("=")[1])) for kv in args.experiments.split(",")]
        for key, value in todo:
            before = baseline
            edit(key, value)
            after = dump()
            b, a = values(before), values(after)
            changed = {k: (b[k], a[k]) for k in ROWS if b[k] != a[k]}
            print(f"\n  write {key} = {value}: " + (f"changed {changed}" if changed else "IGNORED (nothing changed)"))
            if changed:
                print(f"    after: {fmt(a)}   markers {ym.read_markers(after)}")
            settle_to(ym.read_markers(baseline), f"after {key}={value}")
            if wv.norm(dump()) != wv.norm(baseline):
                print(f"    (baseline not exactly back: bytes differing {sorted(wv.changed_bytes(baseline, dump()))})")
        print("\nrestoring the sample's original loop mode and markers ...")
        edit("loop_mode", original_mode)
        settle_to(original_markers, "original")
        print("final bytes different from the original:", sorted(wv.changed_bytes(original, dump())) or "none")
    finally:
        pin.close()
        pout.close()


if __name__ == "__main__":
    main()
