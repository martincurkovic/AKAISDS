"""
Standalone dev tool - NOT part of the app. WRITES to a Yamaha A4000/A5000 (RAM only): measures what an OBJECT LINK CHANGE does - the
message that assigns a sample to a program (manual 5.3.7: `43 1n 58 04 <program name> <20> <sample name> <10> <1 link / 0 unlink>`).

On a throwaway program and sample (default: program 128, the factory sample "pulse 3") it asks the unit whether they are linked (object
link REQUEST, manual 5.3.8), links them, dumps both objects and diffs them against the "before" dumps, asks again, unlinks, dumps again
and checks everything came back. Run only on a unit with nothing of value in it, with the app CLOSED.

What it will send (a guard refuses everything else): bulk dump requests, object select, parameter requests, and OBJECT LINK change/request.

    uv run python tools/a4000_link_probe.py cycle [--program 128] [--sample "pulse 3"]      # link, measure, unlink, measure
    uv run python tools/a4000_link_probe.py status [--program 128] [--sample "pulse 3"]    # read-only: just ask
    uv run python tools/a4000_link_probe.py multi [--program 128]    # several samples: slot order, unlinking a middle one, duplicates,
                                                                    # a stereo sample, a name that doesn't exist; then undoes it all
"""

import argparse
import os
import sys
import time

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from controller.sampler_controller import SamplerController
from core import app_config, midi_manager as mm
from core import yamaha_params as yp
from core import yamaha_sysex as ysx


def allowed(data):
    data = bytes(data)
    if len(data) < 4 or data[0] != 0x43:
        return False
    kind, model, sub = data[1] >> 4, data[2], data[3]
    return (kind == 2 and model == 0x7A) or (kind == 3 and model == 0x58 and sub in (0x01, 0x04)) or (
        kind == 1 and model == 0x58 and sub in (0x00, 0x04)
    )


def diff(a, b):
    a, b = bytearray(a), bytearray(b)
    a[1] &= 0xFE
    b[1] &= 0xFE
    return [i for i in range(max(len(a), len(b))) if (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("status", "cycle", "multi"))
    ap.add_argument("--stereo", default="_NewSample", help="multi: a stereo sample to try")
    ap.add_argument("--program", type=int, default=128)
    ap.add_argument("--sample", default="pulse 3")
    args = ap.parse_args()

    app = QCoreApplication([])
    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    controller = SamplerController(midi)
    controller.set_device_type("yamaha_a4000")
    session = controller.yamaha_session()
    device = session.device
    prog = ysx.program_object_name(args.program)
    heard = []
    midi.sysex_received.connect(lambda d: heard.append((time.monotonic(), bytes(d))))

    def send(data):
        if not allowed(data):
            raise SystemExit(f"REFUSING to send {bytes(data).hex(' ')}")
        midi.send_sysex(data)

    def listen(seconds):
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()

    def dump(fmt, name):
        got = []
        loop = QEventLoop()
        session.request_bulk(fmt, name, lambda d: (got.append(d), loop.quit()))
        QTimer.singleShot(30_000, loop.quit)
        loop.exec()
        if not got or got[0] is None:
            raise SystemExit(f"no {fmt} dump of {name!r}")
        return bytes(got[0].data)

    def ask_for(sample):
        """The unit's own answer to 'are these linked?' - the object link REQUEST."""
        heard.clear()
        send(ysx.build_object_link_request(device, prog, "program", sample, "sample"))
        listen(1.0)
        for _t, msg in heard:
            try:
                parsed = ysx.parse_parameter_message(msg)
            except ysx.YamahaSysexError:
                continue
            if parsed.kind == "link":
                return parsed.linked
        return f"(no answer; heard {[m.hex(' ')[:40] for _t, m in heard]})"

    def ask():
        return ask_for(args.sample)

    def link(sample, on):
        send(ysx.build_object_link_change(device, prog, "program", sample, "sample", on))
        listen(1.2)

    def slots(label):
        pg = dump("PG", prog)
        count = yp.extract(yp.get("program", "assigned_samples"), pg)
        rows = []
        for slot in range(count):
            rows.append((slot, yp.extract(yp.get("easy_edit", "assigned_name"), pg, slot), yp.extract(yp.get("easy_edit", "level_offset"), pg, slot),
                         yp.extract(yp.get("easy_edit", "receive_channel"), pg, slot)))
        print(f"  {label}: count {count}, length {len(pg)}: " + ("; ".join(f"[{s}] {n!r} level {lv} ch {ch}" for s, n, lv, ch in rows) or "(none)"))
        return pg

    def multi(pg0, dump, send, listen, heard, ask_for):
        names = ["pulse 1", "pulse 2", "pulse 3"]
        print("\n=== several samples ===")
        for n in names:
            link(n, True)
            slots(f"linked {n!r}")
        # give each slot a distinct Easy Edit level so we can see what moves when one is unlinked
        for slot, level in enumerate((11, 22, 33)):
            got = []
            loop = QEventLoop()
            session.write_parameter(yp.get("easy_edit", "level_offset"), level, prog, lambda r: (got.append(r), loop.quit()), slot=slot)
            QTimer.singleShot(20_000, loop.quit)
            loop.exec()
            print(f"  wrote level {level} to slot {slot}: {'ok' if got and got[0].ok else got}")
        slots("with distinct levels")
        link("pulse 2", False)
        slots("after unlinking the MIDDLE one (pulse 2)")
        print("  unit says pulse 2 linked:", ask_for("pulse 2"), "| pulse 1:", ask_for("pulse 1"), "| pulse 3:", ask_for("pulse 3"))
        link("pulse 1", True)
        slots("linking pulse 1 AGAIN (a duplicate)")
        link("pulse 2", True)
        slots("linking pulse 2 again (it was unlinked)")
        print(f"\n=== a STEREO sample {args.stereo!r} ===")
        link(args.stereo, True)
        slots("after linking it")
        print("  unit says linked:", ask_for(args.stereo))
        link(args.stereo, False)
        print("\n=== a name that does not exist ===")
        link("no such sample", True)
        slots("after linking 'no such sample'")
        print("\n=== undo everything ===")
        for n in names + [args.stereo, "no such sample"]:
            link(n, False)
        pg = slots("after unlinking all")
        print("  back to the original program dump (ignoring the edited flag)?", diff(pg0, pg) == [], diff(pg0, pg)[:20])
        for n in names + [args.stereo]:
            print(f"  sample {n!r}: used in programs {yp.linked_programs(dump('SP', n))}")
    def show(label, pg0, sp0):
        pg, sp = dump("PG", prog), dump("SP", args.sample)
        count = yp.extract(yp.get("program", "assigned_samples"), pg)
        print(f"\n--- {label} ---")
        print(f"  unit says linked: {ask()}")
        print(f"  program {prog}: assigned_samples={count}, length {len(pg)} (was {len(pg0)}), byte 1 = {pg[1]:#04x} (was {pg0[1]:#04x})")
        print(f"  program bytes changed vs the 'before' dump: {diff(pg0, pg)[:40]}{' ...' if len(diff(pg0, pg)) > 40 else ''}")
        for slot in range(count):
            block = pg[yp.PROGRAM_EASY_EDIT_BASE + yp.EASY_EDIT_BLOCK_SIZE * slot : yp.PROGRAM_EASY_EDIT_BASE + yp.EASY_EDIT_BLOCK_SIZE * (slot + 1)]
            print(f"  Easy Edit slot {slot} (name {yp.extract(yp.get('easy_edit', 'assigned_name'), pg, slot)!r}, "
                  f"type {yp.extract(yp.get('easy_edit', 'assigned_type'), pg, slot)}): {block.hex(' ')}")
        print(f"  sample {args.sample!r}: used in programs {yp.linked_programs(sp)} (was {yp.linked_programs(sp0)}); bytes changed: {diff(sp0, sp)}")
        return pg, sp

    try:
        print(f"ports: {in_name} / {out_name}; device {device}; program {prog}, sample {args.sample!r}")
        pg0, sp0 = dump("PG", prog), dump("SP", args.sample)
        print(f"before: program {prog} assigned_samples={yp.extract(yp.get('program', 'assigned_samples'), pg0)}; sample linked to {yp.linked_programs(sp0)}")
        print(f"unit says linked (request): {ask()}")
        if args.cmd == "status":
            return
        if args.cmd == "multi":
            multi(pg0, dump, send, listen, heard, ask_for)
            return
        heard.clear()
        send(ysx.build_object_link_change(device, prog, "program", args.sample, "sample", True))
        listen(1.5)
        print(f"after LINK ON, the unit said: {[m.hex(' ') for _t, m in heard] or 'nothing'}")
        pg1, sp1 = show("linked", pg0, sp0)
        heard.clear()
        send(ysx.build_object_link_change(device, prog, "program", args.sample, "sample", False))
        listen(1.5)
        print(f"\nafter LINK OFF, the unit said: {[m.hex(' ') for _t, m in heard] or 'nothing'}")
        pg2, sp2 = show("unlinked again", pg0, sp0)
        print("\nback to the original (ignoring the edited flag)? program:", diff(pg0, pg2) == [], " sample:", diff(sp0, sp2) == [])
    finally:
        midi.close_input()
        midi.close_output()


if __name__ == "__main__":
    main()
