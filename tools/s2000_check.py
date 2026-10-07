"""
Standalone dev tool - NOT part of the app. Drives the Program Editor's OWN bridge code (BridgeWorker handlers over a real S3kBridge) against a
real Akai S2000/S3000 on the saved MIDI ports. App CLOSED. Stages:

    uv run python tools/s2000_check.py snapshot           # read-only: every program's header + keygroups, names, PRGNUMs
    uv run python tools/s2000_check.py create [TEMPLATE]  # WRITES a new program cloned from TEMPLATE; checks where it landed and that nothing else changed
    uv run python tools/s2000_check.py p3 [PROGRAM]       # Save PROGRAM to a .p3, then Load it back under a new name (WRITES a new program)
"""

import hashlib
import os
import sys
import tempfile
import time

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from s3k import params as p

from core import akai_program_file as apf
from core import app_config, midi_manager as mm
from core import program_editor_bridge as peb

RESULTS = []


def say(kind, text):
    RESULTS.append(kind)
    print(f"{kind:5} {text}", flush=True)


def check(cond, text):
    say("PASS" if cond else "FAIL", text)
    return bool(cond)


def open_bridge():
    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    bridge = peb.connect(midi, "akai_s2000_s3000")
    say("INFO", f"ports {in_name} / {out_name}")
    return midi, bridge


def strip_ptr(block):
    return block[:1] + b"\x00\x00" + block[3:]


def snapshot(bridge):
    """{index: {"name", "prgnum", "groups", "header": bytes, "keygroups": [bytes, ...]}} for every program."""
    names = bridge.program_list()
    snap = {}
    for i, name in enumerate(names):
        header = bytes(bridge.get_header_bytes("program", i, 0, 192))
        groups = bridge.get_parameter(p.lookup("GROUPS", "program"), i)
        prgnum = bridge.get_parameter(p.lookup("PRGNUM", "program"), i)
        kgs = [bytes(bridge.get_header_bytes("keygroup", i, 0, 192, selector=k)) for k in range(groups)]
        snap[i] = {"name": name, "prgnum": prgnum, "groups": groups, "header": header, "keygroups": kgs}
    return snap


def show(snap):
    for i, s in snap.items():
        digest = hashlib.sha1(s["header"] + b"".join(s["keygroups"])).hexdigest()[:8]
        say("INFO", f"  [{i}] {s['name']!r} PRGNUM(display)={s['prgnum']} keygroups={s['groups']} sha1={digest}")


def stage_snapshot(bridge):
    snap = snapshot(bridge)
    say("INFO", f"{len(snap)} programs; samples: {bridge.sample_list()}")
    show(snap)
    return snap


def stage_create(bridge, template=None):
    before = snapshot(bridge)
    if len({x["prgnum"] for x in before.values()}) < len(before) and len(before) >= 2:
        say("INFO", "program numbers are not distinct - renumbering (what the app does before a Program Change)")
        bridge.renumber_programs()
        before = snapshot(bridge)
    if len(before) < 2:
        # recreate the bad day: a second program with a HIGHER program number, so a clone of the first (same number) sorts BETWEEN them
        say("INFO", "setup: cloning a second program, then giving the programs distinct program numbers")
        setup = peb.BridgeWorker(bridge)
        setup.program_created.connect(lambda *a: None)
        setup._handle_create_program(0, "SKIBIDI2")
        bridge.renumber_programs()
        before = snapshot(bridge)
    show(before)
    names = [s["name"] for s in before.values()]
    src = names.index(template) if template in names else 0
    say("INFO", f"cloning [{src}] {names[src]!r} as 'FIXTEST'")
    worker = peb.BridgeWorker(bridge)
    created, failed = [], []
    worker.program_created.connect(lambda *a: created.append(a))
    worker.program_create_failed.connect(lambda *a: failed.append(a))
    worker._handle_create_program(src, "FIXTEST")
    check(not failed and created, f"create finished: created={created} failed={failed}")
    after = snapshot(bridge)
    show(after)
    names_after = [s["name"] for s in after.values()]
    new_index = names_after.index("FIXTEST") if "FIXTEST" in names_after else None
    check(new_index is not None, f"FIXTEST is in the list at index {new_index}")
    if created and new_index is not None:
        check(created[0][1] == new_index, f"the signal reported new_index {created[0][1]} (the real one: {new_index})")
    # every OTHER program must be byte-identical to before (matched by name)
    for i, s in before.items():
        j = names_after.index(s["name"])
        same = after[j]["header"] == s["header"] and after[j]["keygroups"] == s["keygroups"]
        check(same, f"{s['name']!r} untouched (was [{i}], now [{j}])")
    if new_index is not None:
        new = after[new_index]
        tmpl = before[src]
        check(new["groups"] == tmpl["groups"], f"FIXTEST has {new['groups']} keygroups (template {tmpl['groups']})")
        same_kgs = all(strip_ptr(a) == strip_ptr(b) for a, b in zip(new["keygroups"], tmpl["keygroups"]))
        check(same_kgs, "FIXTEST's keygroups are the template's, byte for byte (the NXTKG pointer bytes 1-2 are the sampler's own)")
        say("INFO", f"FIXTEST PRGNUM {new['prgnum']} (template {tmpl['prgnum']})")
    return after


def stage_p3(bridge, program=None):
    before = snapshot(bridge)
    show(before)
    names = [s["name"] for s in before.values()]
    src = names.index(program) if program in names else 0
    worker = peb.BridgeWorker(bridge)
    exported, failed = [], []
    worker.program_exported.connect(lambda i, f: exported.append((i, f)))
    worker.program_export_failed.connect(lambda *a: failed.append(a))
    worker._handle_export_program(src)
    if not check(exported and not failed, f"saved [{src}] {names[src]!r}: failed={failed}"):
        return
    pf = exported[0][1]
    say("INFO", f"file: {pf.extension}, name {pf.name!r}, {len(pf.keygroups)} keygroups, block size {pf.block_size}")
    data = apf.build_file(pf.program, pf.keygroups)
    path = os.path.join(tempfile.mkdtemp(prefix="s2000check_"), f"{pf.name}{pf.extension}")
    open(path, "wb").write(data)
    say("INFO", f"wrote {path}")
    check(len(data) == pf.block_size * (len(pf.keygroups) + 1), f"file size {len(data)} == {pf.block_size} x ({len(pf.keygroups)} + 1)")
    again = peb.BridgeWorker(bridge)
    ex2 = []
    again.program_exported.connect(lambda i, f: ex2.append(f))
    again._handle_export_program(src)
    check(ex2 and apf.build_file(ex2[0].program, ex2[0].keygroups) == data, "saving twice gives byte-identical files")
    parsed = apf.parse_file(open(path, "rb").read())
    load_name = f"P3-{int(time.time()) % 100000}"
    imported, ifailed = [], []
    worker.program_imported.connect(lambda i, n: imported.append((i, n)))
    worker.program_import_failed.connect(lambda n, m: ifailed.append((n, m)))
    worker._handle_import_program(parsed, load_name)
    check(imported and not ifailed, f"loaded as {load_name!r}: imported={imported} failed={ifailed}")
    after = snapshot(bridge)
    show(after)
    names_after = [s["name"] for s in after.values()]
    if load_name in names_after:
        j = names_after.index(load_name)
        new, orig = after[j], before[src]
        check(new["groups"] == orig["groups"], f"same keygroup count ({new['groups']})")
        diffs = [k for k, (a, b) in enumerate(zip(new["keygroups"], orig["keygroups"])) if strip_ptr(a) != strip_ptr(b)]
        check(not diffs, f"keygroups equal to the original apart from the pointer bytes (differing: {diffs})")
    for i, s in before.items():
        j = names_after.index(s["name"])
        check(after[j]["header"] == s["header"] and after[j]["keygroups"] == s["keygroups"], f"{s['name']!r} untouched")


def stage_multi(bridge, _arg=None):
    """4 keygroups with distinct key ranges -> clone it (create program) -> save .p3 -> load it back. Everything compared modulo pointer bytes."""
    names = bridge.program_list()
    base = "MULTIBASE"
    worker = peb.BridgeWorker(bridge)
    if base not in names:
        worker._handle_create_program(0, base)
        names = bridge.program_list()
    bi = names.index(base)
    have = bridge.get_parameter(p.lookup("GROUPS", "program"), bi)
    for _ in range(4 - have):
        worker._handle_create_keygroup(bi, 0)
    for k in range(4):
        bridge.set_parameter(p.lookup("LONOTE", "keygroup"), bi, 36 + 12 * k, keygroup=k)
        bridge.set_parameter(p.lookup("HINOTE", "keygroup"), bi, 47 + 12 * k, keygroup=k)
    before = snapshot(bridge)
    show(before)
    names = [s["name"] for s in before.values()]
    check(before[names.index(base)]["groups"] == 4, "MULTIBASE has 4 keygroups")
    src = names.index(base)
    created = []
    worker.program_created.connect(lambda *a: created.append(a))
    worker._handle_create_program(src, "MULTICLONE")
    after = snapshot(bridge)
    show(after)
    names2 = [s["name"] for s in after.values()]
    ci = names2.index("MULTICLONE")
    check(created and created[0][1] == ci, f"signal index {created} == real index {ci}")
    check(after[ci]["groups"] == 4, "clone has 4 keygroups")
    check(all(strip_ptr(a) == strip_ptr(b) for a, b in zip(after[ci]["keygroups"], before[src]["keygroups"])), "clone's 4 keygroups equal the base's (distinct key ranges preserved, in order)")
    for i, s in before.items():
        j = names2.index(s["name"])
        check(after[j]["header"] == s["header"] and after[j]["keygroups"] == s["keygroups"], f"{s['name']!r} untouched")
    stage_p3(bridge, base)


def stage_p1(bridge, program=None):
    """Cut a program's blocks to 150 bytes (what an S1000 program is), convert them to the S2000 layout, load that, compare with the original."""
    before = snapshot(bridge)
    names = [s["name"] for s in before.values()]
    src = names.index(program) if program in names else 0
    worker = peb.BridgeWorker(bridge)
    exported = []
    worker.program_exported.connect(lambda i, f: exported.append(f))
    worker._handle_export_program(src)
    pf = exported[0]
    p1 = apf.parse_file(apf.build_file(pf.program[:150], [k[:150] for k in pf.keygroups]))
    check(p1.block_size == 150 and p1.extension == ".p1", f"made a {p1.extension} from {pf.name!r} ({len(p1.keygroups)} keygroups)")
    conv = apf.convert_s1000_to_s3000(p1)
    check(conv.block_size == 192, "converted to 192-byte blocks")
    load_name = f"P1-{int(time.time()) % 100000}"
    imported, failed = [], []
    worker.program_imported.connect(lambda i, n: imported.append((i, n)))
    worker.program_import_failed.connect(lambda n, m: failed.append((n, m)))
    worker._handle_import_program(conv, load_name)
    check(imported and not failed, f"loaded {load_name!r}: imported={imported} failed={failed}")
    after = snapshot(bridge)
    show(after)
    names2 = [s["name"] for s in after.values()]
    if load_name in names2:
        new, orig = after[names2.index(load_name)], before[src]
        check(new["groups"] == orig["groups"], f"same keygroup count ({new['groups']})")
        # the whole header apart from pointer bytes (1-2) and the name (3-14)
        mask = lambda b: bytes(b[:1]) + bytes(14) + bytes(b[15:])  # noqa: E731
        check(mask(new["header"]) == mask(orig["header"]), "program header identical to the original apart from pointer and name")
        diffs = [i for i in range(192) if mask(new["header"])[i] != mask(orig["header"])[i]]
        if diffs:
            say("INFO", f"   differing header bytes: {diffs}")
        for k, (a, b) in enumerate(zip(new["keygroups"], orig["keygroups"])):
            same = strip_ptr(a) == strip_ptr(b)
            check(same, f"keygroup {k + 1} identical to the original apart from the pointer bytes")
            if not same:
                say("INFO", f"   differing bytes: {[i for i in range(192) if strip_ptr(a)[i] != strip_ptr(b)[i]]}")
    for i, s in before.items():
        j = names2.index(s["name"])
        check(after[j]["header"] == s["header"] and after[j]["keygroups"] == s["keygroups"], f"{s['name']!r} untouched")


STAGES = {"p1": stage_p1, "multi": stage_multi, "snapshot": stage_snapshot, "create": stage_create, "p3": stage_p3}


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    midi, bridge = open_bridge()
    STAGES[stage](bridge, *sys.argv[2:3]) if stage != "snapshot" else STAGES[stage](bridge)
    print(f"\n{RESULTS.count('PASS')} passed, {RESULTS.count('FAIL')} failed")
    os._exit(1 if "FAIL" in RESULTS else 0)


if __name__ == "__main__":
    main()
