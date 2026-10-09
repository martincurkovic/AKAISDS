"""
Standalone dev tool - NOT part of the app. READ-ONLY: finds which miscellaneous-data register holds the S2000's GLOBAL "external control" setting
(Breath / Footpedal / Volume - what EXTRNL means as an APM source). Neither the S1000 nor the S2800/S3000/S3200 sysex document names that register
(the misc data-index table is in none of them), so it has to be found by watching the machine, the way s3ked found its own misc registers.
App CLOSED (it owns the MIDI ports) - and CLOSE ANY OTHER MIDI SOFTWARE (a DAW such as Ableton) first: if anything echoes the sampler's replies back to it they act as WRITES.
Only RMISCDATA requests are ever sent - nothing is written - and indices 6-9 (the load/delete/save triggers) are never read (`NEVER_READ_INDEXES`).

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

WHOLE-BLOCK MISC DATA (read-only too): the older RMDATA/MDATA pair (0x10/0x11, the S1000-style "miscellaneous data" block - no register index) may hold the
REAL global settings that the byte registers above only mirror (see tools/s2000_tune_trigger_check.py: a tune written to byte 64 reads back but the panel never
shows it, so byte 64 is not what the sampler uses). Same method, one block per setting value:

    uv run python tools/s2000_misc_probe.py mdump tune0        # sampler's TUNE at 0 (set on the panel), saved as ~/.akaisds/misc_probe/tune0.mdata.json
    uv run python tools/s2000_misc_probe.py mdump tune50       # TUNE at +50 set on the panel
    uv run python tools/s2000_misc_probe.py mdiff tune0 tune50 # byte offsets that moved - the candidate for the real tune
Only an RMDATA REQUEST is sent. If the sampler doesn't answer it (or answers with an error), that is itself the result - it is saved/printed. Do NOT write an MDATA
block until the offsets are understood (s3k marks MDATA writes destructive; it replaces the whole block).

Options for `dump`: `--max N` (highest register index to read, default 127), `--banks 1,2,3` (1 = byte, 2 = word, 3 = dword; default 1,2),
`--timeout S` (how long to wait for EACH reply, default 3.0 - one request at a time, never abandoned early; an index the machine doesn't have times out or answers with an error and is recorded as "no value").
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
# NEVER read these byte registers (and so never the same index in another bank): 6-9 are the LOAD / DELETE / SAVE-NEW / SAVE-SELECTED triggers - s3k: "writing value n
# PERFORMS" the action, and "none of them cares which page the panel shows". A READ is only safe while nothing sends the sampler's reply frames back to it. On 2026-10-10
# something on the user's MIDI interface (Ableton was open) did exactly that: every reply came back as a write, the sampler acknowledged each with an OK, and a dump that read
# 6-9 may have made it load, delete and save. See AGENTS.md's incident notes. The value 0 these read as is itself a command (load type 0 = entire volume).
NEVER_READ_INDEXES = frozenset({6, 7, 8, 9})
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
    """One RMISCDATA request -> the register's value (little-endian int) or None if the machine didn't answer with data FOR THIS REGISTER.

    ONE request is outstanding at a time and only a reply naming the register we asked about (index AND bank) is accepted. On 2026-10-10 a sampler that
    stalls for ~0.6-2 s every few requests was driven with a 0.6 s timeout: the tool abandoned each stalled request and sent the next, the sampler
    answered the old ones late (replies ~12 registers behind, every value misattributed) and its input backlog grew until it stopped answering. So: wait
    out the stall (`timeout` is how long to wait for THIS reply - use seconds, not tenths) and ignore stale frames and stray REPLYs until the right one comes."""
    size = BANK_SIZES[bank]
    frame = m.HeaderRequest(
        command=m.Command.RMISCDATA,
        index=index,
        selector=bank,
        offset=0,
        count=size,
        exclusive_channel=getattr(bridge, "exclusive_channel", m.DEFAULT_EXCLUSIVE_CHANNEL),
    ).encode()
    deadline = time.monotonic() + timeout
    try:
        bridge._drain()
        bridge._send(frame)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            reply = bridge._receive(remaining, accept=frozenset({int(m.Command.MISCDATA), int(m.Command.REPLY)}))
            _chan, command, _payload = m.parse_frame(reply)
            if command == m.Command.REPLY:
                continue  # an error/ok REPLY is never the data we asked for (a stray or late one) - keep waiting for the data
            try:
                header = m.HeaderData.decode(reply)
            except ValueError:
                continue
            if header.index != index or header.selector != bank:
                continue  # a late answer to an earlier request
            data = bytes(header.data)
            if len(data) < size:
                return None
            return int.from_bytes(data[:size], "little")
    except (TimeoutError, ValueError):
        return None
    except Exception:  # noqa: BLE001 - "no value" is a result for a probe
        return None


def path_for(label):
    return os.path.join(SNAPSHOT_DIR, f"{label}.json")


def stage_dump(label, max_index=127, banks=(1, 2), timeout=3.0):
    midi, bridge = open_bridge()
    started = time.monotonic()
    regs = {}  # "bank:index" -> value
    for bank in banks:
        answered = 0
        for index in range(max_index + 1):
            if index in NEVER_READ_INDEXES:
                continue
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


def mdata_path(label):
    return os.path.join(SNAPSHOT_DIR, f"{label}.mdata.json")


def stage_mdump(label, timeout=3.0):
    midi, bridge = open_bridge()
    frame = m.build_frame(
        m.Command.RMDATA, [], exclusive_channel=getattr(bridge, "exclusive_channel", m.DEFAULT_EXCLUSIVE_CHANNEL)
    )
    result = {"label": label}
    try:
        reply = bridge.send_and_receive(frame, timeout=timeout)
        _chan, command, payload = m.parse_frame(reply)
        result["command"] = int(command)
        result["payload_hex"] = bytes(payload).hex()
        if command == m.Command.MDATA:
            body = bytes(payload)
            try:
                result["decoded_hex"] = m.decode_nibbles(list(body)).hex()
            except ValueError as e:
                result["decode_error"] = str(e)
        print(f"reply: command {int(command):#04x}, {len(payload)} payload bytes", flush=True)
    except Exception as e:  # noqa: BLE001 - "no answer" is a result too
        result["error"] = str(e)
        print(f"no usable reply: {e}", flush=True)
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    with open(mdata_path(label), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    print(f"saved {mdata_path(label)}", flush=True)
    os._exit(0)


def stage_mdiff(labels):
    blocks = {}
    for label in labels:
        with open(mdata_path(label), encoding="utf-8") as fh:
            data = json.load(fh)
        hexed = data.get("decoded_hex") or data.get("payload_hex")
        if not hexed:
            print(f"{label}: no data ({data.get('error') or data.get('decode_error') or 'empty'})")
            return
        blocks[label] = bytes.fromhex(hexed)
    sizes = {label: len(b) for label, b in blocks.items()}
    print("block sizes:", sizes)
    longest = max(sizes.values())
    changed = [i for i in range(longest) if len({b[i] if i < len(b) else None for b in blocks.values()}) > 1]
    if not changed:
        print("no byte changed between them")
        return
    for i in changed:
        print(f"  offset {i:>3}: " + "  ".join(f"{label}={b[i] if i < len(b) else None}" for label, b in blocks.items()))


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
    if not args or args[0] not in ("dump", "diff", "mdump", "mdiff"):
        print(__doc__)
        sys.exit(2)
    stage, rest = args[0], args[1:]
    if stage == "mdump":
        if len(rest) != 1:
            print("mdump needs exactly one label, e.g. `mdump tune0`")
            sys.exit(2)
        stage_mdump(rest[0])
    if stage == "mdiff":
        if len(rest) < 2:
            print("mdiff needs at least two labels")
            sys.exit(2)
        stage_mdiff(rest)
        return
    if stage == "dump":
        opts = {"--max": "127", "--banks": "1,2", "--timeout": "3.0"}
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
