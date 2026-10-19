"""
Standalone dev tool - NOT part of the app. HARDWARE CHECK (S2000/S3000) that WRITES single fields of ONE multi part, to find out whether writing them from this app is safe and
takes effect. It is the step before Load Multi (dev_docs/multi-save-load-progress.md): `plan_load` would write these fields, and nobody has written them from here yet.

Fields tested, one at a time (the ones a base S2000's panel can show; `OUTPUT`, `VOSCL`, `PFXCHAN`, `PFXSLEV` and `FX1`-`FX4` need the multi-output board / FX card to see anything and are NOT touched):

    PRIORT (priority)   PLAYLO / PLAYHI (play range)   TRANSPOSE (semitones)   PTUNOCM (tune, cents)

For each field it: reads the whole part; writes ONE field through `set_parameter` (the same per-parameter path the Multi tab uses - never a whole-block write); reads the part back and
demands that the field has the new value and EVERY OTHER BYTE is as before (except the sampler-internal pointers and "last note played"); waits `--hold` seconds so you can look at the
panel; writes the original value back; reads again and demands the part is exactly as it was. It stops at the first surprise (an error reply, a timeout, a field that doesn't change, a
byte that moved that shouldn't) and tries to put the field back.

    uv run python tools/s2000_multi_write_check.py plan                        # what it would do; touches nothing (no MIDI)
    uv run python tools/s2000_multi_write_check.py run --yes                   # part 16 (an unassigned part), every field, 6 s to look at the panel each
    uv run python tools/s2000_multi_write_check.py run --yes --part 3 --fields PRIORT,TRANSPOSE --hold 10
    uv run python tools/s2000_multi_write_check.py restore BACKUP.m3 --part 16 --yes   # put the five test fields of a part back from a saved multi (after a crash)

Safety (why this is not reckless, and what is NOT known):
  - Nothing is sent before the whole multi (header and 16 parts) is saved as a `.m3` to `~/.akaisds/multi_backups/` (no backup, no write). The original value of each field is also
    kept in memory and written back right after its test, and again on an error or Ctrl-C.
  - Only these five fields are ever written, one request at a time, each waiting for the sampler's REPLY. It never writes FX1-FX4 (a byte above 204 crashed the machine, 2026-09-10),
    the pointer bytes, "last note played", a program name, or a whole block, and it never reads a part above 15.
  - It refuses to run without `--yes` (it is a WRITE) and refuses if the multi can't be read as 16 real parts.
  - UNKNOWN: whether the sampler accepts these writes at all (it may answer with an error REPLY, or ignore them: then the result is "no", fine), and whether a write has side effects on
    other state. After a run look at the multi page: it should be exactly as before. If anything is off, `restore` from the backup, or set the five values back by hand.

What you do (the AKAISDS app must be CLOSED - it owns the MIDI ports - and no DAW or MIDI monitor may be open):
  a. Sampler on, MIDI connected as usual, sitting on a normal page (NOT in the middle of an edit: it can stop answering SysEx then).
  b. Use a THROWAWAY multi if you can. The default part, 16, has no program.
  c. Put the panel on that part's multi page where priority, play range, transpose and tune are shown, and keep watching it during the run.
  d. Run `run --yes`. For each field the tool says what it wrote and pauses `--hold` seconds: say (or note) whether the panel showed the new value. Then it restores it.
The result of each field is printed and saved to `~/.akaisds/multi_backups/write_check_<timestamp>.json`. Send the whole output and what the panel showed.
"""

import datetime
import json
import os
import sys
import time

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from s3k import params as p

from core import akai_multi_file as amf

BACKUP_DIR = os.path.join(os.path.expanduser("~"), ".akaisds", "multi_backups")
#: the only fields this tool writes
SAFE_FIELDS = ("PRIORT", "PLAYLO", "PLAYHI", "TRANSPOSE", "PTUNOCM")
DEFAULT_PART = 16
DEFAULT_HOLD_S = 6.0
SETTLE_S = 0.5  # between a write and the read that follows (a block write once went unapplied when read back within milliseconds)


class CheckStop(Exception):
    """A surprise: the run stops (after putting the field back)."""


def decode(name, block):
    return amf._decode("multipart", name, block)


def test_value(name, block):
    """A value to write for `name` that differs from what the part holds and keeps the part valid (play range low <= high), or None if there is no safe one."""
    current = int(decode(name, block))
    low, high = int(decode("PLAYLO", block)), int(decode("PLAYHI", block))
    if name == "PRIORT":
        return 2 if current != 2 else 0  # high, or low
    if name == "PLAYLO":
        value = 48 if current != 48 else 36
        return value if value <= high else None
    if name == "PLAYHI":
        value = 96 if current != 96 else 108
        return value if value >= low else None
    if name == "TRANSPOSE":
        return 7 if current != 7 else -7
    if name == "PTUNOCM":
        return 25 if current != 25 else -25
    raise ValueError(f"{name} is not one of the fields this tool tests")


def read_part(bridge, index):
    if not 0 <= index < amf.PART_COUNT:
        raise ValueError(f"part {index + 1} doesn't exist")  # a read above 15 silently returns part 15's buffer
    return bytes(bridge.get_header_bytes("multipart", index, 0, amf.PART_SIZE, selector=amf.PART_SELECTOR))


def write_field(bridge, name, index, value):
    if name not in SAFE_FIELDS:
        raise ValueError(f"refusing to write {name}")
    bridge.set_parameter(p.lookup(name, "multipart"), index, value)


def save_backup(multi, backup_dir, label="before"):
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(backup_dir, f"{amf.safe_file_stem(multi.name)}-{label}-{stamp}.m3")
    with open(path, "wb") as handle:
        handle.write(amf.build_file(multi.header, multi.parts))
    return path


def print_line(kind, text):
    print(f"{kind:5} {text}", flush=True)


def differing(before, after):
    """(offset, was, now) for every byte that differs between two reads of a part, apart from the sampler-internal ones."""
    return [(o, before[o], after[o]) for o in range(amf.PART_SIZE) if before[o] != after[o] and o not in amf.IGNORED_PART_OFFSETS]


def describe(changes):
    return ", ".join(f"{amf.describe_part_offset(o)} {a}->{b}" for o, a, b in changes)


def check_field(bridge, name, index, hold, say, sleep, results):
    """One field's test; its result dict is appended to `results` as soon as the write is attempted. Raises CheckStop on a surprise, after putting the field back."""
    before = read_part(bridge, index)
    original = int(decode(name, before))
    value = test_value(name, before)
    result = {"field": name, "original": original}
    if value is None:
        say("SKIP", f"{name}: no safe test value for this part's current play range")
        result["result"] = "skipped"
        results.append(result)
        return
    result["wrote"] = value
    results.append(result)
    say("INFO", f"{name}: part {index + 1} holds {original}; writing {value}")
    problem = None
    try:
        try:
            write_field(bridge, name, index, value)
        except Exception as e:  # noqa: BLE001 - an error reply or a timeout IS the result
            result.update(result="write refused", error=f"{type(e).__name__}: {e}")
            say("FAIL", f"{name}: the sampler didn't accept the write ({type(e).__name__}: {e})")
            problem = f"{name} write failed"
        if problem is None:
            sleep(SETTLE_S)
            after = read_part(bridge, index)
            got = int(decode(name, after))
            moved = [c for c in differing(before, after) if c[0] not in range(*_span(name))]
            result.update(read_back=got, other_bytes_changed=[amf.describe_part_offset(o) for o, _a, _b in moved])
            if got != value:
                problem = f"{name} reads back {got}, not {value} (the write was acknowledged but did not stick, or went somewhere else)"
            elif moved:
                problem = f"{name} changed, but so did {len(moved)} other byte(s): {describe(moved)}"
            if problem is None:
                say("PASS", f"{name}: wrote {value}, read back {got}, no other byte moved")
                if hold:
                    say("LOOK", f"{name} is {value} on part {index + 1} now: does the panel show it? ({hold:g} s)")
                    sleep(hold)
                    result["panel_prompt_seconds"] = hold
            else:
                say("FAIL", problem)
                result["result"] = "failed"
    finally:
        # put it back whatever happened, also on Ctrl-C
        try:
            write_field(bridge, name, index, original)
            sleep(SETTLE_S)
            restored = read_part(bridge, index)
        except Exception as e:  # noqa: BLE001
            say("FAIL", f"{name}: COULD NOT PUT {original} BACK ({type(e).__name__}: {e}). Set it by hand or run `restore` with the backup.")
            result["restore"] = f"failed: {e}"
            problem = f"{problem}; " if problem else ""
            problem += f"{name} restore failed"
        else:
            left = differing(before, restored)
            if left:
                say("FAIL", f"{name}: after restoring, {len(left)} byte(s) still differ from the start: {describe(left)}")
                result["restore"] = "differs"
                problem = f"{problem}; " if problem else ""
                problem += f"{name} restore did not match"
            else:
                say("PASS", f"{name}: put back to {original}, the part is exactly as it was")
                result["restore"] = "ok"
    if problem is not None:
        raise CheckStop(problem)
    result["result"] = "pass"


def _span(name):
    prm = p.lookup(name, "multipart")
    return prm.offset, prm.offset + prm.size


def run_check(bridge, part, fields, hold=DEFAULT_HOLD_S, backup_dir=BACKUP_DIR, say=print_line, sleep=time.sleep):
    """The whole check. Returns (results, backup path). Raises CheckStop on a surprise (`.results` and `.backup` are on the exception)."""
    index = part - 1
    for name in fields:
        if name not in SAFE_FIELDS:
            raise ValueError(f"{name} is not one of the fields this tool tests: {', '.join(SAFE_FIELDS)}")
    multi = amf.read_from_bridge(bridge)  # refuses a multi that can't be read as 16 real parts
    say("INFO", f"the resident multi is {multi.name!r}; testing part {part} (program {multi.part_name(index)!r})")
    backup = save_backup(multi, backup_dir)
    say("INFO", f"backed up the whole multi to {backup} (nothing has been written yet)")
    results = []
    try:
        for name in fields:
            check_field(bridge, name, index, hold, say, sleep, results)
    except CheckStop as stop:
        stop.results, stop.backup = results, backup
        raise
    after = amf.read_from_bridge(bridge)
    differences = amf.compare(multi, after)
    if differences:
        say("FAIL", f"the whole multi differs from the backup after the run ({len(differences)} difference(s)): " + "; ".join(differences[:5]))
        stop = CheckStop("the multi differs from the backup after the run")
        stop.results, stop.backup = results, backup
        raise stop
    else:
        say("PASS", "the whole multi (header and all 16 parts) is exactly as it was before the run")
    return results, backup


def save_results(results, backup, part, backup_dir=BACKUP_DIR):
    os.makedirs(backup_dir, exist_ok=True)
    path = os.path.join(backup_dir, f"write_check_{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    with open(path, "w") as handle:
        json.dump({"part": part, "backup": backup, "results": results}, handle, indent=1)
    return path


def restore_fields(bridge, backup_path, part, say=None, sleep=time.sleep):
    """Write the five test fields of `part` back from a saved multi (only where they differ). Returns how many were written."""
    say = say or print_line
    with open(backup_path, "rb") as handle:
        saved = amf.parse_file(handle.read())
    index = part - 1
    current = read_part(bridge, index)
    written = 0
    for name in SAFE_FIELDS:
        want = int(decode(name, saved.parts[index]))
        have = int(decode(name, current))
        if want != have:
            say("INFO", f"{name}: {have} -> {want}")
            write_field(bridge, name, index, want)
            sleep(SETTLE_S)
            written += 1
    say("PASS", f"{written} field(s) restored on part {part}")
    return written


def print_plan(part, fields, hold):
    print(__doc__.split("Safety")[0])
    print(f"This run would test part {part} (index {part - 1}), fields {', '.join(fields)}, holding {hold:g} s on each.")
    print("For each field: read the part -> write ONE field -> read back (field changed, nothing else) -> hold -> write the original back -> read (exactly as before).")
    print(f"First the whole multi would be saved to {BACKUP_DIR}. Nothing is sent by `plan`.")


def open_bridge():
    from core import app_config, midi_manager as mm
    from core import program_editor_bridge as peb

    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    bridge = peb.connect(midi, "akai_s2000_s3000")
    print(f"ports {in_name} / {out_name}", flush=True)
    return bridge


def parse_args(argv):
    options = {"part": DEFAULT_PART, "fields": SAFE_FIELDS, "hold": DEFAULT_HOLD_S, "yes": False}
    rest = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--yes":
            options["yes"] = True
        elif arg in ("--part", "--hold", "--fields"):
            i += 1
            value = argv[i]
            options[arg[2:]] = int(value) if arg == "--part" else float(value) if arg == "--hold" else tuple(v.strip().upper() for v in value.split(",") if v.strip())
        else:
            rest.append(arg)
        i += 1
    return options, rest


def main(argv):
    if not argv or argv[0] not in ("plan", "run", "restore"):
        print(__doc__)
        return 2
    options, rest = parse_args(argv[1:])
    part = options["part"]
    if not 1 <= part <= amf.PART_COUNT:
        print(f"--part must be 1-{amf.PART_COUNT}")
        return 2
    if argv[0] == "plan":
        print_plan(part, options["fields"], options["hold"])
        return 0
    if not options["yes"]:
        print("This WRITES to the sampler. Read the safety notes (run with no arguments), then add --yes.")
        return 2
    bridge = open_bridge()
    if argv[0] == "restore":
        if not rest:
            print("restore needs the backup .m3 file")
            return 2
        try:
            restore_fields(bridge, rest[0], part)
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  restore: {type(e).__name__}: {e}")
            return 1
        return 0
    try:
        results, backup = run_check(bridge, part, options["fields"], options["hold"])
    except CheckStop as stop:
        path = save_results(getattr(stop, "results", []), getattr(stop, "backup", None), part)
        print(f"\nSTOPPED: {stop}. Results so far: {path}. Backup: {getattr(stop, 'backup', None)}")
        return 1
    path = save_results(results, backup, part)
    passed = sum(1 for r in results if r.get("result") == "pass")
    print(f"\n{passed} of {len(results)} fields passed. Results: {path}. Backup: {backup}")
    return 0 if passed == len([r for r in results if r.get("result") != "skipped"]) else 1


if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    os._exit(code)  # (the MIDI ports' native threads can keep a plain exit waiting)
