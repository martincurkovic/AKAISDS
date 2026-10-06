"""
Standalone dev tool - NOT part of the app. READ-ONLY check of core/yamaha_params.py against a real A4000.

For every parameter row it reads the value TWO ways - a parameter request (P1..P6) and the bulk dump at
the row's offset - and reports any disagreement, plus replies that are the wrong size, out of the
manual's range, or missing. Run it after touching core/yamaha_params.py; a clean run means the
table's addresses, sizes, signedness and bulk offsets all match the unit.

It only ever sends dump requests, object-select and parameter requests (the same guard as
tools/a4000_discovery.py, which it reuses). Quit the AKAISDS app first.

    uv run python tools/a4000_verify_params.py                      # program 001 + its Easy Edit slot 0 + "sine wave"
    uv run python tools/a4000_verify_params.py --program 5 --sample "saw up" --sample triangle
    uv run python tools/a4000_verify_params.py --device 1 --only-bad
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import a4000_discovery as disc  # noqa: E402
import mido  # noqa: E402

from core import yamaha_params as yp  # noqa: E402
from core import yamaha_sysex as ysx  # noqa: E402


def quiet_send(port, data):
    data = bytes(data)
    if not disc._allowed(data):
        raise SystemExit(f"REFUSING to send a non-read-only message: {data.hex(' ')}")
    port.send(mido.Message("sysex", data=list(data)))


def await_message(port, predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for msg in port.iter_pending():
            if msg.type == "sysex" and predicate(bytes(msg.data)):
                return bytes(msg.data)
        time.sleep(0.002)
    return None


def fetch_dump(pin, pout, device, fmt, name):
    quiet_send(pout, ysx.build_dump_request(device, fmt, name))
    raw = await_message(pin, lambda m: ysx.classify(m) == "bulk_dump", 6.0)
    if raw is None:
        raise SystemExit(f"no {fmt} dump for {name!r} - is the Device Number {device} and Bulk Protect off?")
    return ysx.parse_bulk_dump(raw)


def select(pin, pout, device, name, otype):
    quiet_send(pout, ysx.build_object_select(device, name, otype))
    await_message(pin, lambda m: ysx.classify(m) == "parameter", 1.0)  # the unit echoes the select


def request(pin, pout, device, p):
    quiet_send(pout, ysx.build_parameter_request(device, p))

    def mine(m):
        if ysx.classify(m) != "parameter":
            return False
        try:
            msg = ysx.parse_parameter_message(m)
        except ysx.YamahaSysexError:
            return False
        return msg.kind == "object" and msg.params == tuple(p)

    raw = await_message(pin, mine, 1.5)
    return ysx.parse_parameter_message(raw) if raw else None


def check(scope, params, dump, pin, pout, device, slot, results):
    for param in params:
        if param.bulk_only:
            continue
        p = yp.request_params(param, slot)
        bulk = yp.extract(param, dump.data, slot)
        reply = request(pin, pout, device, p)
        label = f"{scope}{'[%d]' % slot if slot is not None else ''}.{param.key}"
        if reply is None:
            results.append(("NO REPLY", label, p, bulk, None, ""))
            continue
        # a bitfield's reply is the field value; text and ints decode per the row
        if param.kind == "int" and len(reply.data) != param.bulk_size and not param.bits:
            results.append(("SIZE", label, p, bulk, reply.data.hex(), f"reply is {len(reply.data)} bytes, row says {param.size}"))
            continue
        value = yp.decode_reply(param, reply.data)
        if value != bulk:
            results.append(("DIFF", label, p, bulk, value, "request != bulk"))
        elif param.kind == "int" and not (param.lo <= value <= param.hi):
            results.append(("RANGE", label, p, bulk, value, f"outside {param.lo}..{param.hi}"))
        else:
            results.append(("ok", label, p, bulk, value, ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp")
    ap.add_argument("--out")
    ap.add_argument("--device", type=int, default=0)
    ap.add_argument("--program", type=int, default=1)
    ap.add_argument("--slot", type=int, action="append", help="Easy Edit slot(s) to check (default: every assigned one)")
    ap.add_argument("--sample", action="append", help="sample name(s) to check (default: 'sine wave')")
    ap.add_argument("--only-bad", action="store_true", help="print only rows that are not ok")
    args = ap.parse_args()

    pin, pout = disc.open_ports(args)
    results = []
    try:
        name = ysx.program_object_name(args.program)
        program = fetch_dump(pin, pout, args.device, "PG", name)
        select(pin, pout, args.device, name, ysx.OBJECT_TYPES["program"])
        check("program", yp.rows("program"), program, pin, pout, args.device, None, results)
        assigned = yp.extract(yp.get("program", "assigned_samples"), program.data)
        slots = args.slot if args.slot is not None else list(range(assigned))
        print(f"program {name}: {assigned} assigned sample(s); Easy Edit slots checked: {slots or 'none'}")
        for slot in slots:
            check("easy_edit", yp.rows("easy_edit"), program, pin, pout, args.device, slot, results)
        for sample in args.sample or ["sine wave"]:
            dump = fetch_dump(pin, pout, args.device, "SP", sample)
            select(pin, pout, args.device, sample, ysx.OBJECT_TYPES["sample"])
            check(f"sample({sample})", yp.rows("sample"), dump, pin, pout, args.device, None, results)
    finally:
        pin.close()
        pout.close()

    bad = [r for r in results if r[0] != "ok"]
    for status, label, p, bulk, got, note in results:
        if status == "ok" and args.only_bad:
            continue
        print(f"{status:9} {label:58} P={p!s:24} bulk={bulk!s:12} request={got!s:12} {note}")
    print(f"\n{len(results) - len(bad)} of {len(results)} rows agree; {len(bad)} do not.")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
