"""
Standalone dev tool - NOT part of the app. READ-ONLY discovery for the Yamaha A4000/A5000.

Phase 0 of the A4000 editor roadmap: find out whether the unit really speaks what the service
manual (MIDI DATA FORMAT, pages 32-42) says it does, before any editor code exists. Nothing here
writes to the sampler: the only things this script will ever send are

  * an Identity Request                          F0 7E <ch> 06 01 F7
  * a bulk DUMP REQUEST                          F0 43 2n 7A "LM  0474" <fmt> <name> F7
  * a parameter REQUEST                          F0 43 3n 58 01|02 P1..P6 F7
  * an object SELECT (just picks which object    F0 43 1n 58 00 <name> <type> F7
    later parameter requests read; changes no data)

and `send()` refuses anything else. Every reply is printed and saved as .syx under
~/.akaisds/a4000_discovery/ so it can be attached to a bug report / read by the next agent.

QUIT THE AKAISDS APP FIRST - two programs on one MIDI port race each other.

Run from the repo root:
    uv run python tools/a4000_discovery.py ports
    uv run python tools/a4000_discovery.py identity
    uv run python tools/a4000_discovery.py dump OL
    uv run python tools/a4000_discovery.py dump PG --name 001
    uv run python tools/a4000_discovery.py listen 60       # then do a bulk dump from the front panel
    uv run python tools/a4000_discovery.py decode ~/.akaisds/a4000_discovery/<file>.syx

Ports default to the ones saved in AKAISDS's config; override with --in/--out (a substring of the
port name). The unit's Device Number must be set (not "off"); pass it with --device N (default 0).
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mido

from core import app_config
from core import yamaha_sysex as ysx
from core.yamaha_sysex import denibble, pad_name  # noqa: F401  (denibble used by describe)

OUT_DIR = os.path.expanduser("~/.akaisds/a4000_discovery")
FORMATS = ("SY", "PG", "SB", "SP", "WD", "SQ", "OL")
OBJECT_TYPES = {"program": 0x14, "bank": 0x11, "sample": 0x10, "wave": 0x02, "sequence": 0x13}
TYPE_NAMES = {v: k for k, v in OBJECT_TYPES.items()}


# -- the read-only guard -------------------------------------------------------------------------


def _allowed(data):
    """data = the bytes BETWEEN F0 and F7. True only for the few read-only message shapes."""
    if len(data) >= 4 and data[0] == 0x7E and data[2] == 0x06 and data[3] == 0x01:
        return True  # identity request
    if data[:1] != b"\x43" or len(data) < 4:
        return False
    kind, model = data[1] >> 4, data[2]
    if kind == 0x2 and model == 0x7A:
        return True  # bulk dump request
    if kind == 0x3 and model == 0x58:
        return True  # parameter request (object / system / link)
    if kind == 0x1 and model == 0x58 and len(data) > 3 and data[3] == 0x00:
        return True  # object SELECT only (sub-status 00) - never object edit (01) or system change (02)
    return False


# -- ports ---------------------------------------------------------------------------------------


def _pick(names, wanted, saved, what):
    if wanted:
        hits = [n for n in names if wanted.lower() in n.lower()]
    elif saved:
        hits = [n for n in names if n == saved]
    else:
        hits = []
    if len(hits) != 1:
        sys.exit(
            f"Couldn't pick a single MIDI {what} port (wanted {wanted or saved!r}; matches: {hits}).\n"
            f"Available: {names}\nPass --{'in' if what == 'input' else 'out'} <part of the name>."
        )
    return hits[0]


def open_ports(args):
    saved_in, saved_out = app_config.get_saved_ports()
    in_name = _pick(mido.get_input_names(), args.inp, saved_in, "input")
    out_name = _pick(mido.get_output_names(), args.out, saved_out, "output")
    print(f"MIDI in : {in_name}\nMIDI out: {out_name}\nDevice number: {args.device}")
    return mido.open_input(in_name), mido.open_output(out_name)


def send(port, data):
    data = bytes(data)
    if not _allowed(data):
        raise SystemExit(f"REFUSING to send a non-read-only message: {data.hex(' ')}")
    print(f"  -> F0 {data.hex(' ')} F7")
    port.send(mido.Message("sysex", data=list(data)))


def collect(port, seconds, *, stop_after=None):
    """Every sysex that arrives within `seconds` (or until `stop_after` messages)."""
    out, deadline = [], time.monotonic() + seconds
    while time.monotonic() < deadline:
        for msg in port.iter_pending():
            if msg.type == "sysex":
                out.append(bytes(msg.data))
                print(f"  <- {len(msg.data) + 2} bytes: F0 {bytes(msg.data[:24]).hex(' ')}{' ...' if len(msg.data) > 24 else ''}")
        if stop_after and len(out) >= stop_after:
            break
        time.sleep(0.005)
    return out


def save(label, messages):
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = []
    for i, data in enumerate(messages):
        path = os.path.join(OUT_DIR, f"{time.strftime('%Y%m%d-%H%M%S')}_{label}_{i}.syx")
        with open(path, "wb") as fh:
            fh.write(b"\xf0" + data + b"\xf7")
        paths.append(path)
    if paths:
        print("  saved:", *paths, sep="\n    ")
    return paths


# -- message builders ----------------------------------------------------------------------------


def dump_request(device, fmt, name, fill):
    return ysx.build_dump_request(device, fmt, name, fill=fill)


def select_object(device, name, otype):
    return ysx.build_object_select(device, name, otype)


def param_request(device, params, system=False):
    return ysx.build_parameter_request(device, params, system=system)


# -- decoding ------------------------------------------------------------------------------------


def hexdump(data, limit=160):
    for i in range(0, min(len(data), limit), 16):
        chunk = data[i : i + 16]
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"    {i:04d}  {chunk.hex(' '):<47}  {text}")
    if len(data) > limit:
        print(f"    ... ({len(data)} bytes total)")


xor7 = ysx.xor7


def split_bulk(data):
    """LENIENT block splitter for diagnostics (prints bad checksums instead of raising; the strict one
    is core.yamaha_sysex.parse_bulk_dump). data = bytes between F0 and F7 -> (header, [spans], [ok?], clean).

    Measured on a real A4000 (2026-10-06): the byte count is MSB-FIRST (20 00 = 4096), a dump is
    one or more blocks inside a single F0..F7, each `count(2) span checksum`, the checksum is the
    XOR of the span, and only the FIRST block's span starts with the 26-byte header ("LM  0474",
    format, 16-byte name). Blocks hold at most 4096 span bytes."""
    pos, spans, oks = 3, [], []
    while pos + 2 <= len(data):
        count = (data[pos] << 7) | data[pos + 1]
        span = data[pos + 2 : pos + 2 + count]
        if len(span) < count or pos + 2 + count >= len(data):
            break
        spans.append(span)
        oks.append(xor7(span) == data[pos + 2 + count])
        pos += 3 + count
    header = spans[0][:26] if spans else b""
    return header, spans, oks, pos == len(data)


def describe_bulk(data):
    header, spans, oks, clean = split_bulk(data)
    print(f"  BULK DUMP: header {header[:8]!r} format {header[8:10]!r} name {header[10:26]!r}")
    print(f"  {len(spans)} block(s), span sizes {[len(x) for x in spans]}, XOR checksums "
          f"{'all OK' if all(oks) else oks}{'' if clean else '  (TRAILING BYTES - decoder out of step)'}")
    body = spans[0][26:] + b"".join(spans[1:])
    real = denibble(body)
    print(f"  data: {len(body)} MIDI bytes -> {len(real)} real bytes")
    hexdump(real)
    fmt = header[8:10].decode("ascii", "replace")
    if fmt == "OL":
        print("  object list:")
        for i in range(0, len(real) - 16, 17):
            otype, name = real[i], real[i + 1 : i + 17]
            print(f"    {TYPE_NAMES.get(otype, hex(otype)):<9} {name.decode('ascii', 'replace')!r}")
    elif fmt in ("PG", "SP", "SB", "WD"):
        print(f"  [Common]: type byte {real[0]} ({TYPE_NAMES.get(real[0], '?')}), name@2 {real[2:18]!r}, "
              f"name@64 {real[64:80]!r}")


def describe(data):
    """data = bytes between F0 and F7."""
    print(f"  message: {len(data) + 2} bytes")
    if data[:2] == b"\x7e\x7f" or (data[:1] == b"\x7e" and len(data) > 3 and data[2:4] == b"\x06\x02"):
        print("  identity reply:", data.hex(" "))
        if len(data) >= 13:
            number = data[7] | (data[8] << 7)
            model = {0x1DA: "A4000", 0x1DB: "A5000"}.get(number, "unknown")
            print(f"    manufacturer {data[4]:#04x}, family code raw {data[5]:02x} {data[6]:02x} (manual: $0041),"
                  f" family number {number:#06x} = {model}, revision raw {data[9:13].hex(' ')}")
        return
    if data[:1] != b"\x43":
        print("  not a Yamaha message:", data[:16].hex(" "))
        return
    kind, model = data[1] >> 4, data[2]
    print(f"  Yamaha, type nibble {kind}, device number {data[1] & 0xF}, model byte {model:#04x}")
    if model == 0x7A and kind == 0:
        describe_bulk(data)
    elif model == 0x58:
        print(f"  PARAMETER message, sub-status {data[3]:#04x}: {data[4:].hex(' ')}")
    else:
        print("  unrecognised:", data[:32].hex(" "))


def messages_in(blob):
    out, i = [], 0
    while True:
        start = blob.find(b"\xf0", i)
        if start < 0:
            return out
        end = blob.find(b"\xf7", start)
        if end < 0:
            out.append(blob[start + 1 :])
            return out
        out.append(blob[start + 1 : end])
        i = end + 1


# -- commands ------------------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", help="part of the MIDI input port name")
    ap.add_argument("--out", help="part of the MIDI output port name")
    ap.add_argument("--device", type=int, default=0, help="the unit's Device Number (0-15), default 0")
    ap.add_argument("--wait", type=float, default=6.0, help="seconds to wait for a reply")
    ap.add_argument("--fill", type=lambda x: int(x, 0), default=0x20,
                    help="byte used to pad the 16-char object name (default 0x20 space; try 0)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ports")
    sub.add_parser("identity")
    d = sub.add_parser("dump", help="request a bulk dump")
    d.add_argument("fmt", choices=FORMATS)
    d.add_argument("--name", default="", help='object name; programs are their number, e.g. "001"')
    ls = sub.add_parser("listen", help="record sysex for N seconds (e.g. while you bulk-dump from the front panel)")
    ls.add_argument("seconds", type=float, nargs="?", default=30)
    pm = sub.add_parser("param", help="select an object then request one parameter")
    pm.add_argument("otype", choices=sorted(OBJECT_TYPES))
    pm.add_argument("name")
    pm.add_argument("p", type=int, nargs="+", help="P1..P6 (missing ones are 0)")
    sy = sub.add_parser("sysparam", help="request one system parameter")
    sy.add_argument("p", type=int, nargs="+")
    dc = sub.add_parser("decode", help="decode a saved .syx file")
    dc.add_argument("file")
    args = ap.parse_args()

    if not 0 <= args.device <= 15:
        sys.exit("--device must be 0-15")

    if args.cmd == "ports":
        print("inputs :", mido.get_input_names())
        print("outputs:", mido.get_output_names())
        print("saved in AKAISDS config:", app_config.get_saved_ports())
        return
    if args.cmd == "decode":
        with open(os.path.expanduser(args.file), "rb") as fh:
            for message in messages_in(fh.read()):
                describe(message)
        return

    port_in, port_out = open_ports(args)
    try:
        if args.cmd == "listen":
            print(f"Listening for {args.seconds:.0f}s - start the bulk dump on the unit now...")
            got = collect(port_in, args.seconds)
            for m in got:
                describe(m)
            save("listen", got)
            return
        if args.cmd == "identity":
            send(port_out, bytes([0x7E, 0x7F, 0x06, 0x01]))
        elif args.cmd == "dump":
            send(port_out, dump_request(args.device, args.fmt, args.name, args.fill))
        elif args.cmd == "param":
            send(port_out, select_object(args.device, args.name, OBJECT_TYPES[args.otype]))
            time.sleep(0.15)
            send(port_out, param_request(args.device, args.p))
        elif args.cmd == "sysparam":
            send(port_out, param_request(args.device, args.p, system=True))
        got = collect(port_in, args.wait)
        if not got:
            print("  (no reply - check Device Number is set and matches --device, and that Bulk "
                  "Protect isn't blocking; also try --fill 0)")
        for m in got:
            describe(m)
        save(args.cmd + (getattr(args, "fmt", "") or ""), got)
    finally:
        port_in.close()
        port_out.close()


if __name__ == "__main__":
    main()
