"""
Standalone dev tool - NOT part of the app. WRITES to a Yamaha A4000/A5000 (RAM only) to prove the parameter table.

tools/a4000_verify_params.py reads every row of core/yamaha_params.py two ways, but on a unit at factory
settings many rows hold the same default, so a wrong bulk offset can still agree. This tool proves each
row: it writes a DISTINCTIVE value to one parameter of a throwaway object with a parameter change,
dumps the object again, and checks that EXACTLY the expected byte (or bits) changed - then writes the
original value back. Run it only on a unit with nothing of value in memory, with the app closed.

What it will send (and nothing else - `send()` refuses the rest): dump requests, object select, parameter
requests, and OBJECT EDITs (`F0 43 1n 58 01 ...`). Never a bulk load, a system-parameter change or an
object-link change. An object edit applies to whatever object was last selected, so every sweep selects its
target first and proves it took by requesting a parameter and checking the object the unit announces.

Stages (do them in order; each is a subcommand):
    backup     save a .syx dump of each target object to ~/.akaisds/a4000_discovery/write_backups/
    noop       write one parameter back to its OWN current value; the dump must come back byte-identical
    one        change one parameter, confirm exactly that byte changed, restore it
    sweep      the full per-row test for --scope program | easy_edit | sample

Targets (defaults are throwaway): program 128 (program scope), program 001 slot 0 (Easy Edit - it needs an
assigned sample), sample "pulse 3" (sample scope).
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mido  # noqa: E402

import a4000_discovery as disc  # noqa: E402
import a4000_verify_params as ver  # noqa: E402

from core import yamaha_params as yp  # noqa: E402
from core import yamaha_sysex as ysx  # noqa: E402

BACKUP_DIR = os.path.expanduser("~/.akaisds/a4000_discovery/write_backups")
TYPES = ysx.OBJECT_TYPES

#: rows the sweep skips: wave/loop geometry is coupled (writing one moves another) and a wrong value could
#: leave the sample unplayable - test those by hand with sensible values instead
GEOMETRY = {
    "wave_start_address", "wave_length", "wave_end_address", "loop_start_address", "loop_length",
    "loop_end_address",
}


def _allowed(data):
    """Everything this tool may send: reads, a select, and object EDIT. Not bulk loads/system/link."""
    data = bytes(data)
    if len(data) < 4 or data[0] != 0x43:
        return False
    kind, model, sub = data[1] >> 4, data[2], data[3]
    if kind == 2 and model == 0x7A:
        return True  # dump request
    if kind == 3 and model == 0x58:
        return True  # parameter request
    if kind == 1 and model == 0x58 and sub in (0x00, 0x01):
        return True  # object select / object edit (NOT 0x02 system, NOT 0x04 link)
    return False


def send(port, data):
    data = bytes(data)
    if not _allowed(data):
        raise SystemExit(f"REFUSING to send: {data.hex(' ')}")
    port.send(mido.Message("sysex", data=list(data)))


def drain(pin, seconds=0.0):
    out, deadline = [], time.monotonic() + seconds
    while True:
        for m in pin.iter_pending():
            if m.type == "sysex":
                out.append(bytes(m.data))
        if time.monotonic() >= deadline:
            return out
        time.sleep(0.005)


class Unit:
    def __init__(self, pin, pout, device):
        self.pin, self.pout, self.device = pin, pout, device

    def dump(self, fmt, name):
        drain(self.pin)
        send(self.pout, ysx.build_dump_request(self.device, fmt, name))
        raw = ver.await_message(self.pin, lambda m: ysx.classify(m) == "bulk_dump", 6.0)
        if raw is None:
            raise SystemExit(f"no {fmt} dump for {name!r}")
        return ysx.parse_bulk_dump(raw)

    def select(self, name, otype):
        """Select an object, then PROVE the unit has it: a parameter request is answered by the unit first
        announcing its current object (a select-style message), then the value. (The unit does NOT answer
        a select by itself.) Refuses to continue if the announced object is not the one asked for."""
        probe = (1, 10, 0, 0, 0, 0) if otype == TYPES["program"] else (2, 33, 0, 0, 0, 0)
        drain(self.pin)
        send(self.pout, ysx.build_object_select(self.device, name, otype))
        time.sleep(0.1)
        send(self.pout, ysx.build_parameter_request(self.device, probe))
        announced = None
        deadline = time.monotonic() + 1.5
        got_value = False
        while time.monotonic() < deadline and not got_value:
            for m in drain(self.pin, 0.05):
                if ysx.classify(m) != "parameter":
                    continue
                msg = ysx.parse_parameter_message(m)
                if msg.kind == "select":
                    announced = msg
                elif msg.kind == "object" and msg.params == probe:
                    got_value = True
        if announced is not None and (announced.object_name, announced.object_type) != (name.rstrip(), otype):
            raise SystemExit(f"the unit announced {announced.object_name!r}/{announced.object_type}, not {name!r} - not writing")
        if not got_value:
            raise SystemExit(f"no reply after selecting {name!r} - not writing")

    def read(self, p):
        send(self.pout, ysx.build_parameter_request(self.device, p))
        raw = ver.await_message(
            self.pin,
            lambda m: ysx.classify(m) == "parameter" and _is_reply(m, p),
            1.5,
        )
        return ysx.parse_parameter_message(raw) if raw else None

    def edit(self, p, value_bytes):
        send(self.pout, ysx.build_object_edit(self.device, p, value_bytes))
        time.sleep(0.12)
        return drain(self.pin)  # anything the unit says back (nothing, so far)


def _is_reply(m, p):
    try:
        msg = ysx.parse_parameter_message(m)
    except ysx.YamahaSysexError:
        return False
    return msg.kind == "object" and msg.params == tuple(p)


def value_bytes(param, value):
    return ysx.encode_value(value, param.size, signed=param.signed) if not param.bits else ysx.encode_value(
        value, 1, signed=param.signed
    )


def expected_after(param, slot, before, value):
    """`before` (bulk payload) with `param` set to `value`, the way the table says it is laid out."""
    data = bytearray(before)
    off = yp.bulk_offset(param, slot)
    if param.bits:
        shift, width = param.bits
        mask = ((1 << width) - 1) << shift
        data[off] = (data[off] & ~mask & 0xFF) | ((value << shift) & mask)
    else:
        data[off : off + param.size] = ysx.encode_value(value, param.size, signed=param.signed)
    return bytes(data)


#: sensible small values for the wave/loop rows of a 128-frame throwaway sample (`--geometry`)
GEOMETRY_VALUES = {
    "wave_start_address": 4, "wave_length": 100, "wave_end_address": 110,
    "loop_start_address": 8, "loop_length": 50, "loop_end_address": 90,
}


def pick_value(param, current):
    """A value in range that differs from `current`, away from the extremes."""
    if param.key in GEOMETRY_VALUES and GEOMETRY_VALUES[param.key] != current:
        return GEOMETRY_VALUES[param.key]
    lo, hi = param.lo, param.hi
    if param.bits:
        _shift, width = param.bits
        if param.signed:
            lo, hi = max(lo, -(1 << (width - 1))), min(hi, (1 << (width - 1)) - 1)
        else:
            hi = min(hi, (1 << width) - 1)
    for frac in (0.37, 0.61, 0.83, 0.19):
        v = round(lo + (hi - lo) * frac)
        if v != current:
            return v
    return hi if current != hi else lo


def norm(data):
    """Clear the unit's "edited" flag (bit 0 of the common block's byte 1, MEASURED: any edit sets it) so it
    doesn't count as a side effect of the write under test."""
    data = bytearray(data)
    data[1] &= ~1 & 0xFF
    return bytes(data)


def changed_bytes(a, b):
    a, b = norm(a), norm(b)
    return {i for i, (x, y) in enumerate(zip(a, b)) if x != y}


def test_row(unit, param, slot, fmt, name, otype, *, noop=False):
    """Write one row, dump, compare, restore. Returns (status, detail)."""
    p = yp.request_params(param, slot)
    before = unit.dump(fmt, name).data
    current = yp.extract(param, before, slot)
    value = current if noop else pick_value(param, current)
    unit.select(name, otype)
    notes = unit.edit(p, value_bytes(param, value))
    after = unit.dump(fmt, name).data
    want = expected_after(param, slot, before, value)
    got_changed, want_changed = changed_bytes(before, after), changed_bytes(before, want)
    reply = unit.read(p)
    readback = yp.decode_reply(param, reply.data) if reply else None
    # restore (even if the check failed) so the next row starts clean
    if not noop:
        unit.select(name, otype)
        unit.edit(p, value_bytes(param, current))
    if norm(after) == norm(want):
        status = "EXACT"
        detail = f"{current} -> {value}, bytes {sorted(want_changed)}"
    elif want_changed <= got_changed and all(norm(after)[i] == norm(want)[i] for i in want_changed):
        status = "EXTRA"
        detail = f"{current} -> {value}: expected bytes right, but ALSO changed {sorted(got_changed - want_changed)}"
    elif not got_changed:
        status = "NOCHANGE"
        detail = f"wrote {value} (was {current}); nothing changed in the dump; readback {readback}"
    else:
        status = "WRONG"
        detail = (f"wrote {value} (was {current}); expected bytes {sorted(want_changed)}, "
                  f"changed {sorted(got_changed)}; readback {readback}")
    if notes:
        detail += f"; unit said {[n.hex(' ') for n in notes]}"
    if readback is not None and readback != value:
        detail += f"; READBACK {readback} != written {value}"
    return status, detail


def save_backup(unit, fmt, name, label):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    raw = unit.dump(fmt, name)
    path = os.path.join(BACKUP_DIR, f"{time.strftime('%Y%m%d-%H%M%S')}_{label}.syx")
    with open(path, "wb") as fh:
        fh.write(b"\xf0" + ysx.build_bulk_dump(raw.device, raw.fmt, raw.name_raw, raw.data, header=raw.header) + b"\xf7")
    print(f"  backup {label}: {path}")
    return path


def targets(args):
    prog = ysx.program_object_name(args.program)
    easy = ysx.program_object_name(args.easy_program)
    return {
        "program": ("PG", prog, TYPES["program"], None),
        "easy_edit": ("PG", easy, TYPES["program"], args.slot),
        "sample": ("SP", args.sample, TYPES["sample"], None),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp")
    ap.add_argument("--out")
    ap.add_argument("--device", type=int, default=0)
    ap.add_argument("--program", type=int, default=128, help="throwaway program for program-scope rows")
    ap.add_argument("--easy-program", type=int, default=1, help="program holding an assigned sample for Easy Edit rows")
    ap.add_argument("--slot", type=int, default=0)
    ap.add_argument("--sample", default="pulse 3", help="throwaway sample for sample-scope rows")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backup")
    sub.add_parser("noop")
    sub.add_parser("one")
    rs = sub.add_parser("restore", help="write back every row that differs from a saved backup (from `backup`)")
    rs.add_argument("file", help="the .syx backup")
    rs.add_argument("--scope", choices=yp.SCOPES, required=True)
    sw = sub.add_parser("sweep")
    sw.add_argument("--scope", choices=yp.SCOPES, required=True)
    sw.add_argument("--only", help="comma-separated keys to test")
    sw.add_argument("--geometry", action="store_true", help="also test the wave/loop address rows (small safe values)")
    args = ap.parse_args()

    pin, pout = disc.open_ports(args)
    unit = Unit(pin, pout, args.device)
    tg = targets(args)
    try:
        if args.cmd == "backup":
            for scope, (fmt, name, _t, _s) in tg.items():
                save_backup(unit, fmt, name, f"{scope}_{name.strip().replace(' ', '_')}")
            return
        if args.cmd in ("noop", "one"):
            fmt, name, otype, slot = tg["program"]
            row = yp.get("program", "program_level")
            status, detail = test_row(unit, row, slot, fmt, name, otype, noop=args.cmd == "noop")
            print(f"program {name} program_level ({args.cmd}): {status} - {detail}")
            return
        if args.cmd == "restore":
            fmt, name, otype, slot = tg[args.scope]
            with open(os.path.expanduser(args.file), "rb") as fh:
                wanted = ysx.parse_bulk_dump(ysx.split_messages(fh.read())[0]).data
            for attempt in range(3):
                now = unit.dump(fmt, name).data
                todo = [p for p in yp.rows(args.scope)
                        if not (p.read_only or p.bulk_only or p.a5000_only or p.kind != "int")
                        and yp.extract(p, now, slot) != yp.extract(p, wanted, slot)]
                if not todo:
                    break
                print(f"pass {attempt + 1}: restoring {[p.key for p in todo]}")
                unit.select(name, otype)
                for p in todo:
                    unit.edit(yp.request_params(p, slot), value_bytes(p, yp.extract(p, wanted, slot)))
            final = unit.dump(fmt, name).data
            diff = sorted(changed_bytes(final, wanted))
            print("bytes still different from the backup:", diff or "none")
            return
        fmt, name, otype, slot = tg[args.scope]
        keys = set(args.only.split(",")) if args.only else None
        before_all = unit.dump(fmt, name).data
        results = []
        for param in yp.rows(args.scope):
            if param.read_only or param.bulk_only or param.a5000_only or param.kind != "int":
                continue
            if keys and param.key not in keys:
                continue
            if args.scope == "sample" and param.key in GEOMETRY and not args.geometry:
                results.append(("SKIPPED", param.key, "wave/loop geometry is coupled - test by hand"))
                continue
            status, detail = test_row(unit, param, slot, fmt, name, otype)
            results.append((status, param.key, detail))
            print(f"{status:9} {param.key:42} {detail}", flush=True)
        residual = changed_bytes(before_all, unit.dump(fmt, name).data)
        counts = {}
        for s, _k, _d in results:
            counts[s] = counts.get(s, 0) + 1
        print(f"\n{args.scope} on {name!r}: {counts}")
        print("bytes still different from the start after restoring:", sorted(residual) or "none")
    finally:
        pin.close()
        pout.close()


if __name__ == "__main__":
    main()
