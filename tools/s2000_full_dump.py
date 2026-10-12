"""
Standalone dev tool - NOT part of the app. READ-ONLY (only request messages are ever sent): snapshots everything about an S2000/S3000 that SysEx can read,
so two snapshots - one before and one after a setting is changed ON THE FRONT PANEL - show what the panel's edit really touched.

    uv run python tools/s2000_full_dump.py dump <label>                       # snapshot -> ~/.akaisds/misc_probe/full_<label>.json
    uv run python tools/s2000_full_dump.py dump <label> --only misc,mdata    # just those sections (misc, mdata, multi, fx, programs, status)
    uv run python tools/s2000_full_dump.py diff <label> <label> [<label>...]  # what differs between them (first = baseline)
    uv run python tools/s2000_full_dump.py diff t0 t12 --unstable noise1 noise2   # ignore whatever changes between two dumps of the SAME state

App CLOSED (it owns the MIDI ports) and ALL OTHER MIDI SOFTWARE CLOSED (a DAW echoing the sampler's replies back turns them into writes). Misc indices 6-9 (load/delete/save triggers) are never read. Typical session (this is how the global TUNE was investigated, 2026-10-10):
  1. Panel: tune 0.   `dump noise1`, `dump noise2`          (two dumps of one state: whatever differs is volatile - cursor, counters)
  2. Panel: TUNE +12 (do it ON THE PANEL).   `dump t12`
  3. `diff noise1 t12 --unstable noise1 noise2`

What is read (all documented, bounded reads - nothing is written):
  misc      the misc BYTE / WORD / DWORD banks, indices 0-`--misc-max` (default 255 = the range the earlier probes already read safely)
  mdata     the 48-byte whole-block misc data (RMDATA, 0x10)
  multi     the multi file header (1024 bytes) and the 16 multi parts (192 bytes each)
  fx        the effects header and assignment records
  programs  every resident program's 192-byte header
  status    RSTAT
NOT read on purpose: misc indices past `--misc-max` (nobody knows how the sampler bounds-checks an out-of-range index - a bad read could hang it and lose RAM
samples; raise `--misc-max` only with nothing valuable in memory), misc banks 4-7 (sizes unknown), sample/keygroup data (huge, irrelevant to globals).
A section that cannot be read is recorded as an error and the dump goes on - a missing section is a result too.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import s2000_misc_probe as probe  # noqa: E402  (open_bridge, read_register, SNAPSHOT_DIR)
from s3k import messages as m  # noqa: E402
from s3k.bridge import DeviceError  # noqa: E402

_IO_ERRORS = (ValueError, DeviceError, TimeoutError, KeyError)
CHUNK = 64


def path_for(label):
    return os.path.join(probe.SNAPSHOT_DIR, f"full_{label}.json")


class SamplerSilent(Exception):
    """The sampler stopped answering. The dump stops at once rather than send more requests into silence (on 2026-10-10 an earlier version kept asking
    a frozen sampler for over a minute)."""


def retry(call, tries=4):
    """The sampler intermittently answers with a frame s3k cannot decode ("expected at least a 7-byte body, got 1"): retry those before recording a
    failure. A TIMEOUT is different - no answer at all - so it is retried once, not waited out repeatedly."""
    last = None
    timeouts = 0
    for _ in range(tries):
        try:
            return call()
        except TimeoutError as e:
            last = e
            timeouts += 1
            if timeouts >= 2:
                break
        except _IO_ERRORS as e:
            last = e
            time.sleep(0.3)
    raise last


def alive(bridge):
    """A cheap RSTAT: does the sampler answer at all?"""
    frame = m.RequestStatus(exclusive_channel=getattr(bridge, "exclusive_channel", m.DEFAULT_EXCLUSIVE_CHANNEL)).encode()
    for _ in range(2):
        try:
            bridge.send_and_receive(frame, timeout=2.0)
            return True
        except _IO_ERRORS:
            time.sleep(0.5)
    return False


def read_chunked(bridge, region, index, total, selector=0):
    data = bytearray()
    for offset in range(0, total, CHUNK):
        count = min(CHUNK, total - offset)
        data += retry(lambda: bridge.get_header_bytes(region, index, offset, count, selector=selector))
    return bytes(data)


def section(bridge, results, name, reader):
    """Returns False when the sampler has gone silent: the caller stops the dump."""
    started = time.monotonic()
    if not alive(bridge):
        results[name] = {"error": "sampler not answering before this section"}
        results["stopped_at"] = name
        print(f"  STOPPED before {name}: the sampler is not answering. Nothing more is sent.", flush=True)
        return False
    try:
        results[name] = reader()
        print(f"  {name}: ok ({time.monotonic() - started:.1f}s)", flush=True)
    except SamplerSilent as e:
        results[name] = {"error": str(e)}
        results["stopped_at"] = name
        print(f"  STOPPED in {name}: {e}. Nothing more is sent.", flush=True)
        return False
    except Exception as e:  # noqa: BLE001 - a section that fails is a result, not a crash
        results[name] = {"error": str(e)}
        print(f"  {name}: FAILED - {e}", flush=True)
    return True


SILENT_LIMIT = 3  # consecutive unanswered misc reads (each waits `timeout`, default 3 s) before the dump gives up


def dump_misc(bridge, misc_max, timeout):
    regs = {}
    silent = 0
    for bank in (1, 2, 3):
        for index in range(misc_max + 1):
            if index in probe.NEVER_READ_INDEXES:
                continue  # the load/delete/save triggers: never read (see s2000_misc_probe.NEVER_READ_INDEXES)
            value = probe.read_register(bridge, bank, index, timeout)
            if value is None:
                silent += 1
                if silent >= SILENT_LIMIT:
                    raise SamplerSilent(f"no answer to {silent} misc reads in a row (bank {bank}, index {index}) - the sampler stopped answering")
                continue
            silent = 0
            regs[f"{bank}:{index}"] = value
            if index % 64 == 63:
                print(f"    misc bank {bank}: {index + 1}/{misc_max + 1}", flush=True)
    return regs


def dump_mdata(bridge):
    frame = m.build_frame(m.Command.RMDATA, [], exclusive_channel=getattr(bridge, "exclusive_channel", m.DEFAULT_EXCLUSIVE_CHANNEL))

    def read():
        reply = bridge.send_and_receive(frame, timeout=3.0)
        _c, command, payload = m.parse_frame(reply)
        if command != m.Command.MDATA:
            raise DeviceError(f"expected MDATA, got {int(command):#04x}")
        return bytes(m.decode_nibbles(list(payload))).hex()

    return retry(read)


def dump_multi(bridge):
    out = {"header": read_chunked(bridge, "multi", 0, 1024).hex()}
    for part in range(16):
        out[f"part{part:02d}"] = read_chunked(bridge, "multipart", part, 192).hex()
    return out


def dump_fx(bridge):
    out = {}
    for selector in (m.FxSelector.FX_HEADER, m.FxSelector.FX_ASSIGN, m.FxSelector.RVB_ASSIGN):
        out[selector.name] = retry(lambda: bridge.fx_bytes(selector, 0, 0, 64)).hex()
    return out


def dump_programs(bridge):
    names = retry(lambda: bridge.program_list())
    out = {"names": list(names)}
    for index in range(len(names)):
        out[f"program{index:03d}"] = read_chunked(bridge, "program", index, 192).hex()
    return out


def stage_dump(label, misc_max, timeout, only=None):
    bridge = probe.open_bridge()[1]
    started = time.monotonic()
    results = {"label": label, "saved": time.strftime("%Y-%m-%d %H:%M:%S"), "misc_max": misc_max}
    print(f"dumping '{label}' (read-only)...", flush=True)
    readers = [
        ("misc", lambda: dump_misc(bridge, misc_max, timeout)),
        ("mdata", lambda: dump_mdata(bridge)),
        ("multi", lambda: dump_multi(bridge)),
        ("fx", lambda: dump_fx(bridge)),
        ("programs", lambda: dump_programs(bridge)),
        ("status", lambda: retry(lambda: bridge.send_and_receive(m.RequestStatus(
            exclusive_channel=getattr(bridge, "exclusive_channel", m.DEFAULT_EXCLUSIVE_CHANNEL)).encode(), timeout=2.0)).hex()),
    ]
    for name, reader in readers:
        if only and name not in only:
            continue
        if not section(bridge, results, name, reader):
            break
    os.makedirs(probe.SNAPSHOT_DIR, exist_ok=True)
    with open(path_for(label), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1, sort_keys=True)
    note = f" - STOPPED EARLY at {results['stopped_at']}" if "stopped_at" in results else ""
    print(f"saved {path_for(label)} in {time.monotonic() - started:.1f}s{note}", flush=True)
    os._exit(0)  # the MIDI ports are native - don't wait for their destructors


def load(label):
    with open(path_for(label), encoding="utf-8") as fh:
        return json.load(fh)


def flatten(snapshot):
    """{"section/key[/byte N]": value} for every comparable thing in a snapshot."""
    flat = {}
    for name, body in snapshot.items():
        if name in ("label", "saved", "misc_max"):
            continue
        if isinstance(body, dict) and set(body) == {"error"}:
            flat[f"{name}/<error>"] = body["error"]
        elif name == "misc":
            for key, value in body.items():
                flat[f"misc/{key}"] = value
        elif isinstance(body, dict):
            for key, value in body.items():
                if isinstance(value, str) and key != "names":
                    raw = bytes.fromhex(value)
                    for i, byte in enumerate(raw):
                        flat[f"{name}/{key}/byte {i}"] = byte
                else:
                    flat[f"{name}/{key}"] = json.dumps(value)
        elif isinstance(body, str):
            raw = bytes.fromhex(body)
            for i, byte in enumerate(raw):
                flat[f"{name}/byte {i}"] = byte
    return flat


def stage_diff(labels, unstable):
    flats = {label: flatten(load(label)) for label in labels}
    noisy = set()
    if len(unstable) >= 2:
        base = flatten(load(unstable[0]))
        for other in unstable[1:]:
            snap = flatten(load(other))
            noisy |= {k for k in set(base) | set(snap) if base.get(k) != snap.get(k)}
        print(f"{len(noisy)} entries differ between {' / '.join(unstable)} (same state) - ignored as noise: {sorted(noisy)[:20]}{' ...' if len(noisy) > 20 else ''}")
    keys = sorted(set().union(*flats.values()))
    changed = [k for k in keys if k not in noisy and len({flats[l].get(k) for l in labels}) > 1]
    print(f"\ncompared: {', '.join(labels)}  ({len(keys)} entries)")
    if not changed:
        print("nothing changed between them (outside the noise)")
        return
    for key in changed:
        print(f"  {key:45} " + "  ".join(f"{l}={flats[l].get(key)}" for l in labels))


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("dump", "diff"):
        print(__doc__)
        sys.exit(2)
    if args[0] == "dump":
        misc_max, timeout, rest, only = 255, 3.0, [], None
        it = iter(args[1:])
        for a in it:
            if a == "--only":
                only = set(next(it).split(","))
            elif a == "--misc-max":
                misc_max = int(next(it))
            elif a == "--timeout":
                timeout = float(next(it))
            else:
                rest.append(a)
        if len(rest) != 1:
            print("dump needs exactly one label")
            sys.exit(2)
        stage_dump(rest[0], misc_max, timeout, only)
    else:
        rest = args[1:]
        unstable = []
        if "--unstable" in rest:
            cut = rest.index("--unstable")
            rest, unstable = rest[:cut], rest[cut + 1:]
        if len(rest) < 2:
            print("diff needs at least two labels")
            sys.exit(2)
        stage_diff(rest, unstable)


if __name__ == "__main__":
    main()
