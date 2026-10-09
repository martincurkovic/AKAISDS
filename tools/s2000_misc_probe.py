"""
Standalone dev tool - NOT part of the app. READ-ONLY: finds which miscellaneous-data register holds the S2000's GLOBAL "external control" setting
(Breath / Footpedal / Volume - what EXTRNL means as an APM source). Neither the S1000 nor the S2800/S3000/S3200 sysex document names that register
(the misc data-index table is in none of them), so it has to be found by watching the machine, the way s3ked found its own misc registers.
App CLOSED (it owns the MIDI ports). Only RMISCDATA requests are ever sent - nothing is written.

    uv run python tools/s2000_misc_probe.py dump breath          # snapshot every misc register, saved as ~/.akaisds/misc_probe/breath.json
    uv run python tools/s2000_misc_probe.py diff breath foot volume
    uv run python tools/s2000_misc_probe.py diff breath foot volume --unstable breath breath2

Procedure, at the sampler:
  1. GLOBAL mode, the "external control" page (the page after the one with the MIDI/tuning fields; the manual calls it "SELECTING THE EXTERNAL MIDI
     CONTROLLER", p.200). Stay on that page for the whole session - the sampler's own mode/page registers change when you navigate and would show up as noise.
  2. Set it to BREATH           -> `dump breath`
  3. Set it to BREATH again     -> `dump breath2`   (same value, a second look: whatever differs between these two is noise, not the setting)
  4. Set it to FOOTPEDAL        -> `dump foot`
  5. Set it to VOLUME           -> `dump volume`
  6. `diff breath foot volume --unstable breath breath2`
The register we want takes three different values and stays put between breath and breath2. Also try writing nothing: this tool cannot write.

Options for `dump`: `--max N` (highest register index to read, default 127), `--banks 1,2,3` (1 = byte, 2 = word, 3 = dword; default 1,2),
`--timeout S` (per read, default 0.6 - an index the machine doesn't have either times out or answers with an error; both are recorded as "no value").
"""

import json
import os
import sys
import time

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from s3k import messages as m

from core import app_config, midi_manager as mm
from core import program_editor_bridge as peb

SNAPSHOT_DIR = os.path.join(os.path.expanduser("~"), ".akaisds", "misc_probe")
BANK_SIZES = {1: 1, 2: 2, 3: 4}  # bank number -> bytes read (the sysex docs' "data bank number")
BANK_NAMES = {1: "byte", 2: "word", 3: "dword"}


def open_bridge():
    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    bridge = peb.connect(midi, "akai_s2000_s3000")
    print(f"ports {in_name} / {out_name}", flush=True)
    return midi, bridge


def read_register(bridge, bank, index, timeout):
    """One RMISCDATA request -> the register's value (little-endian int) or None if the machine didn't answer with data."""
    size = BANK_SIZES[bank]
    frame = m.HeaderRequest(
        command=m.Command.RMISCDATA,
        index=index,
        selector=bank,
        offset=0,
        count=size,
        exclusive_channel=getattr(bridge, "exclusive_channel", m.DEFAULT_EXCLUSIVE_CHANNEL),
    ).encode()
    try:
        reply = bridge.send_and_receive(frame, timeout=timeout)
        _chan, command, _payload = m.parse_frame(reply)
        if command == m.Command.REPLY:
            return None
        data = bytes(m.HeaderData.decode(reply).data)
    except Exception:
        return None
    if len(data) < size:
        return None
    return int.from_bytes(data[:size], "little")


def path_for(label):
    return os.path.join(SNAPSHOT_DIR, f"{label}.json")


def stage_dump(label, max_index=127, banks=(1, 2), timeout=0.6):
    midi, bridge = open_bridge()
    started = time.monotonic()
    regs = {}  # "bank:index" -> value
    for bank in banks:
        answered = 0
        for index in range(max_index + 1):
            value = read_register(bridge, bank, index, timeout)
            if value is not None:
                regs[f"{bank}:{index}"] = value
                answered += 1
        print(f"{BANK_NAMES[bank]} bank: {answered}/{max_index + 1} registers answered", flush=True)
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    with open(path_for(label), "w", encoding="utf-8") as fh:
        json.dump({"label": label, "registers": regs}, fh, indent=1, sort_keys=True)
    print(f"saved {len(regs)} registers to {path_for(label)} in {time.monotonic() - started:.1f}s", flush=True)
    os._exit(0)  # the MIDI ports are native - don't wait for their destructors


def load(label):
    with open(path_for(label), encoding="utf-8") as fh:
        return json.load(fh)["registers"]


def stage_diff(labels, unstable=()):
    snaps = {label: load(label) for label in labels}
    noisy = set()
    if len(unstable) >= 2:
        base = load(unstable[0])
        for other in unstable[1:]:
            snap = load(other)
            noisy |= {k for k in set(base) | set(snap) if base.get(k) != snap.get(k)}
        print(f"{len(noisy)} registers differ between {' / '.join(unstable)} (same setting) - ignored as noise: {sorted(noisy, key=sort_key)}")
    every = sorted(set().union(*snaps.values()), key=sort_key)
    changed = [k for k in every if k not in noisy and len({snaps[l].get(k) for l in labels}) > 1]
    print(f"\ncompared: {', '.join(labels)}")
    if not changed:
        print("no register changed between them")
        return
    for key in changed:
        bank, index = key.split(":")
        values = {l: snaps[l].get(key) for l in labels}
        distinct = len(set(values.values())) == len(labels) and None not in values.values()
        tag = "  <-- takes a different value for each setting: the candidate" if distinct else ""
        print(f"  {BANK_NAMES[int(bank)]:5} register {index:>3}: " + "  ".join(f"{l}={v}" for l, v in values.items()) + tag)


def sort_key(key):
    bank, index = key.split(":")
    return int(bank), int(index)


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("dump", "diff"):
        print(__doc__)
        sys.exit(2)
    stage, rest = args[0], args[1:]
    if stage == "dump":
        opts = {"--max": "127", "--banks": "1,2", "--timeout": "0.6"}
        labels = []
        it = iter(rest)
        for a in it:
            if a in opts:
                opts[a] = next(it)
            else:
                labels.append(a)
        if len(labels) != 1:
            print("dump needs exactly one label, e.g. `dump breath`")
            sys.exit(2)
        stage_dump(labels[0], int(opts["--max"]), tuple(int(b) for b in opts["--banks"].split(",")), float(opts["--timeout"]))
    else:
        unstable = []
        labels = rest
        if "--unstable" in rest:
            cut = rest.index("--unstable")
            labels, unstable = rest[:cut], rest[cut + 1:]
        if len(labels) < 2:
            print("diff needs at least two snapshot labels")
            sys.exit(2)
        stage_diff(labels, unstable)


if __name__ == "__main__":
    main()
