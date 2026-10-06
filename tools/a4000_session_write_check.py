"""
Standalone dev tool - NOT part of the app. WRITES to a Yamaha A4000/A5000 (RAM only) through the app's own
MidiManager + SamplerController + YamahaSession.write_parameter - i.e. the exact path the editor's controls use
(tools/a4000_write_verify.py proved the table with raw messages; this proves the session's write sequence:
backup first, object proven selected, edit, read-back).

Run only on a unit with nothing of value in memory, with the AKAISDS app CLOSED. For each case it
  1. dumps the target object,  2. writes a value through the session (must come back ok, with a backup .syx),
  3. dumps again and compares with what the table says should have changed (EXACT / EXTRA side effects),
  4. at the end writes every original value back and compares the final dump with the first one.
Also runs the "write back unchanged" test (a write of a row's own value must leave the dump identical).

    uv run python tools/a4000_session_write_check.py [--program 128] [--sample "pulse 3"] [--easy-program 1 --slot 0]

The Easy Edit cases run only if --easy-program holds an assigned sample in that slot.
"""

import argparse
import os
import sys

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from controller.sampler_controller import SamplerController
from core import app_config, midi_manager as mm
from core import yamaha_params as yp
from core import yamaha_sysex as ysx


def norm(data):
    data = bytearray(data)
    data[1] &= 0xFE  # the unit's "edited" flag
    return bytes(data)


def changed(a, b):
    a, b = norm(a), norm(b)
    return {i for i, (x, y) in enumerate(zip(a, b)) if x != y}


def expected(row, slot, before, value):
    data = bytearray(before)
    yp.store(row, data, value, slot)
    return bytes(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", type=int, default=128)
    ap.add_argument("--sample", default="pulse 3")
    ap.add_argument("--easy-program", type=int, default=1)
    ap.add_argument("--slot", type=int, default=0)
    args = ap.parse_args()

    app = QCoreApplication([])
    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    controller = SamplerController(midi)
    controller.set_device_type("yamaha_a4000")
    controller.status_changed.connect(lambda m: print("  status:", m))
    session = controller.yamaha_session()
    print(f"ports: {in_name} / {out_name}; device {session.device}; backups -> {session.backup_dir}")

    def wait(start, timeout_ms=60_000):
        got = []
        loop = QEventLoop()
        start(lambda x: (got.append(x), loop.quit()))
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        return got[0] if got else None

    def dump(fmt, name):
        d = wait(lambda cb: session.request_bulk(fmt, name, cb))
        if d is None:
            raise SystemExit(f"no {fmt} dump of {name!r}")
        return bytes(d.data)

    prog = ysx.program_object_name(args.program)
    easy = ysx.program_object_name(args.easy_program)
    # (scope, key, value, object name, fmt, slot)
    cases = [
        ("program", "program_level", 50, prog, "PG", None),
        ("program", "transpose", -12, prog, "PG", None),
        ("program", "lfo_cycle", 5, prog, "PG", None),
        ("program", "lfo_wave", 2, prog, "PG", None),
        ("program", "portamento_rate", 40, prog, "PG", None),
        ("program", "ad_in_l_pan", -20, prog, "PG", None),
        ("program", "lfo_reset_note", 60, prog, "PG", None),
        ("program", "ad_in_l_output1_level", 99, prog, "PG", None),
        ("sample", "filter_cutoff", 77, args.sample, "SP", None),
        ("sample", "sample_level", 64, args.sample, "SP", None),
        ("sample", "pan", -30, args.sample, "SP", None),
        ("sample", "loop_mode", 2, args.sample, "SP", None),
        ("sample", "key_range_low", 40, args.sample, "SP", None),
    ]
    easy_data = dump("PG", easy)
    if yp.extract(yp.get("program", "assigned_samples"), easy_data) > args.slot:
        for key, value in (("level_offset", 20), ("pan_offset", -10), ("key_range_shift", 5), ("key_limit_low", 36),
                           ("coarse_tune_offset", 3), ("midi_control_on", 1)):
            cases.append(("easy_edit", key, value, easy, "PG", args.slot))
    else:
        print(f"(program {easy} has no sample in slot {args.slot}: skipping the Easy Edit cases)")

    originals = {}
    for _scope, _key, _v, name, fmt, _slot in cases:
        originals.setdefault((fmt, name), dump(fmt, name))
    first_backups = {}

    print("\n-- 'write back unchanged' ------------------------------------------------------------------")
    row = yp.get("program", "program_level")
    before = originals[("PG", prog)]
    result = wait(lambda cb: session.write_parameter(row, yp.extract(row, before), prog, cb))
    after = dump("PG", prog)
    print(f"  ok={result.ok} edit_sent={result.edit_sent} backup={result.backup_path}")
    print("  dump identical (apart from the edited flag):", norm(after) == norm(before))
    first_backups[("PG", prog)] = result.backup_path

    print("\n-- writes ------------------------------------------------------------------------------------")
    summary = {"EXACT": 0, "EXTRA": 0, "WRONG": 0, "FAILED": 0}
    for scope, key, value, name, fmt, slot in cases:
        row = yp.get(scope, key)
        before = dump(fmt, name)
        result = wait(lambda cb: session.write_parameter(row, value, name, cb, slot=slot))
        after = dump(fmt, name)
        want = expected(row, slot, before, value)
        if not result.ok:
            status = "FAILED"
        elif norm(after) == norm(want):
            status = "EXACT"
        elif (changed(before, want) <= changed(before, after)
              and all(norm(after)[i] == norm(want)[i] for i in changed(before, want))):
            status = "EXTRA"
        else:
            status = "WRONG"
        summary[status] += 1
        extra = sorted(changed(before, after) - changed(before, want))
        print(f"  {status:6} {scope}.{key:28} {name!r:12} {result.previous} -> {value} (read back {result.readback})"
              + (f"  also changed {extra}" if extra and status != "EXACT" else "")
              + ("" if result.ok else f"  [{result.message}]"))
        first_backups.setdefault((fmt, name), result.backup_path)
    print("  ", summary)

    print("\n-- restore -----------------------------------------------------------------------------------")
    for scope, key, value, name, fmt, slot in reversed(cases):
        row = yp.get(scope, key)
        original = yp.extract(row, originals[(fmt, name)], slot)
        result = wait(lambda cb: session.write_parameter(row, original, name, cb, slot=slot))
        if not result.ok:
            print(f"  could not restore {scope}.{key}: {result.message}")
    for (fmt, name), data in originals.items():
        final = dump(fmt, name)
        print(f"  {fmt} {name!r}: bytes still different from the start: {sorted(changed(final, data)) or 'none'}")

    print("\n-- backups -----------------------------------------------------------------------------------")
    for (fmt, name), path in first_backups.items():
        saved = ysx.parse_bulk_dump(ysx.split_messages(open(path, "rb").read())[0]).data
        print(f"  {fmt} {name!r}: {path}  == the object before the first write: {bytes(saved) == originals[(fmt, name)]}")
    midi.close_input()
    midi.close_output()


if __name__ == "__main__":
    main()
