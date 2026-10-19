"""
Standalone dev tool - NOT part of the app. READ-ONLY on the sampler: only RMULTIDATA requests are sent (the 32-byte multi header, then parts 0-15, one request
at a time, never above part 15), nothing is written. App CLOSED (it owns the MIDI ports) and CLOSE ANY OTHER MIDI SOFTWARE (a DAW can echo the sampler's replies
back at it, and a sampler executes a returned data frame as a WRITE; see AGENTS.md, "Real-hardware safety").

It settles the open questions in dev_docs/s3000-multi-file-format.md: does the file Save Multi writes match a multi file the sampler itself wrote?

    uv run python tools/s2000_multi_check.py show FILE.m3             # offline: what a multi file holds (name, FX, each part's program and channel)
    uv run python tools/s2000_multi_check.py save OUT.m3              # read the resident multi over SysEx and write it as Save Multi does
    uv run python tools/s2000_multi_check.py compare REAL.m3 [--save OUT.m3]
                                                                      # read the resident multi, build the file Save Multi WOULD write, and diff it
                                                                      # against REAL.m3 (a multi the sampler saved to disk and akaiutil extracted)
    uv run python tools/s2000_multi_check.py diff A.m3 B.m3           # offline: the same diff between two files
    uv run python tools/s2000_multi_check.py plan FILE.m3             # DRY RUN of a Load (there is none yet): read the resident multi and the program names, and
                                                                      # say what loading FILE.m3 would change, field by field, and how sure we are each write is safe
    uv run python tools/s2000_multi_check.py plan FILE.m3 --against CURRENT.m3 [--programs "KICK,SNARE"]
                                                                      # the same, offline, against another file (and a list of resident program names)

Procedure for `compare`, at the sampler:
  1. Set up a multi with DISTINCTIVE values: a different program, channel, level and pan on every part you can, and, if you use them, priority, play range,
     output, transpose, effects bus/send and tune on some parts, and an effect in each of FX1-FX4.
  2. Save that multi to disk (the sampler's own SAVE page), then export it with akaiutil (or your disk tool) as a .m3 file: that is REAL.m3.
  3. With the same multi still resident (do not change it), run `compare REAL.m3`.
An empty diff means Save Multi would have written the same bytes the sampler did, apart from the pointers and "last note played" (ignored on purpose). Any line
printed is a byte our file gets wrong, with its field name when the app knows it. Send the whole output.
"""

import os
import sys

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import akai_multi_file as amf


def load(path):
    with open(path, "rb") as handle:
        return amf.parse_file(handle.read())


def summary(label, multi):
    lines = [f"{label}: multi {multi.name!r}, FX1-FX4 = {multi.fx}"]
    for index in range(amf.PART_COUNT):
        channel = multi.part_channel(index)
        lines.append(f"  part {index + 1:2}: program {multi.part_name(index)!r:16} channel {'OMNI' if channel == 255 else channel + 1}")
    if any(multi.extra_header):
        used = sum(1 for b in multi.extra_header if b)
        lines.append(f"  header bytes 0x20-0x3FF: {used} non-zero")
    return "\n".join(lines)


def would_save(multi):
    """What Save Multi writes for `multi` (read over SysEx), parsed back."""
    return amf.parse_file(amf.build_file(multi.header, multi.parts))


def read_resident():
    from core import app_config, midi_manager as mm
    from core import program_editor_bridge as peb

    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    bridge = peb.connect(midi, "akai_s2000_s3000")
    print(f"ports {in_name} / {out_name}", flush=True)
    multi = amf.read_from_bridge(bridge)
    print(f"read the resident multi {multi.name!r} (32-byte header + {amf.PART_COUNT} parts)", flush=True)
    return multi


def report(lines, a_label, b_label):
    if not lines:
        print(f"\nIDENTICAL: {a_label} and {b_label} agree on every byte except the pointers and 'last note played' (ignored).")
        return 0
    print(f"\n{len(lines)} DIFFERENCE(S), {a_label} vs {b_label}:")
    for line in lines:
        print(f"  {line}")
    return 1


def read_program_names():
    """The resident program names (RPLIST, read only), or None if they can't be read."""
    from core import app_config, midi_manager as mm
    from core import program_editor_bridge as peb

    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    bridge = peb.connect(midi, "akai_s2000_s3000")
    try:
        return list(bridge.program_list())
    except Exception as e:  # noqa: BLE001 - a plan without the program list is still useful
        print(f"couldn't read the program list ({e}); programs are reported as unknown")
        return None


def print_plan(plan, file_label):
    print(f"\nLoading {file_label} would change {len(plan.changes)} field(s); {plan.unchanged} loadable field(s) already match.")
    if plan.changes:
        width = max(len(f"{c.scope} {c.name}") for c in plan.changes)
        print("\n  evidence: proven = the Multi tab writes it today; measured = s3ked measured the write; UNPROVEN = nobody has written it from here")
        for c in plan.changes:
            tag = "UNPROVEN" if c.evidence == "unproven" else c.evidence
            line = f"  {c.scope + ' ' + c.name:<{width}}  {amf.format_value(c.name, c.current)} -> {amf.format_value(c.name, c.new)}   [{tag}]"
            print(line + (f"  ({c.note})" if c.note else ""))
    print("\nPrograms (a part is bound by a Program Change on its channel, by name; a load can't unassign a part):")
    for b in plan.bindings:
        if b.state == "same" and not b.new:
            continue
        print(f"  part {b.part:2}: {b.current!r:16} -> {b.new!r:16} [{b.state}]")
    if plan.not_loaded:
        print("\nNot loaded:")
        for line in plan.not_loaded:
            print(f"  {line}")
    unproven = sum(1 for c in plan.changes if c.evidence == "unproven")
    if unproven:
        print(f"\n{unproven} of these writes have never been tried from this app (UNPROVEN). Nothing was written: this is a dry run.")
    else:
        print("\nNothing was written: this is a dry run.")


def run_plan(rest):
    against, programs = None, None
    if "--against" in rest:
        at = rest.index("--against")
        against = rest[at + 1]
        rest = rest[:at] + rest[at + 2 :]
    if "--programs" in rest:
        at = rest.index("--programs")
        programs = [name.strip() for name in rest[at + 1].split(",") if name.strip()]
        rest = rest[:at] + rest[at + 2 :]
    target = load(rest[0])
    if against:
        current = load(against)
        label = f"{rest[0]} over {against}"
    else:
        current = read_resident()
        programs = read_program_names() if programs is None else programs
        label = f"{rest[0]} over the resident multi {current.name!r}"
    print(summary("the file", target))
    print_plan(amf.plan_load(current, target, programs), label)
    return 0


def main(argv):
    if len(argv) < 2 or argv[0] not in ("show", "save", "compare", "diff", "plan"):
        print(__doc__)
        return 2
    command, rest = argv[0], argv[1:]
    if command == "plan":
        return run_plan(rest)
    if command == "show":
        print(summary(rest[0], load(rest[0])))
        return 0
    if command == "diff":
        a, b = load(rest[0]), load(rest[1])
        print(summary(rest[0], a))
        print(summary(rest[1], b))
        return report(amf.compare(a, b), rest[0], rest[1])
    save_path = None
    if "--save" in rest:
        at = rest.index("--save")
        save_path = rest[at + 1]
        rest = rest[:at] + rest[at + 2 :]
    if command == "save":
        save_path = save_path or rest[0]
    resident = read_resident()
    mine = would_save(resident)
    if save_path:
        with open(save_path, "wb") as handle:
            handle.write(amf.build_file(resident.header, resident.parts))
        print(f"wrote {save_path} ({amf.MULTI_FILE_SIZE} bytes)")
    print(summary("from the sampler (as Save Multi would write it)", mine))
    if command == "save":
        return 0
    real = load(rest[0])
    print(summary(f"real file {rest[0]}", real))
    return report(amf.compare(mine, real), "our file", "the real file")


if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    os._exit(code)  # (the MIDI ports' native threads can keep a plain exit waiting)
