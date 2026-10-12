"""
Standalone dev tool - NOT part of the app. HARDWARE CHECK (S2000/S3000): finds out what makes the sampler ACT on a global-tune value written over SysEx.
It WRITES (the global tune register, and the front-panel page), puts everything back at the end, and needs a person at the sampler to listen/look.

    uv run python tools/s2000_tune_trigger_check.py plan       # prints what it will do, touches nothing (no MIDI)
    uv run python tools/s2000_tune_trigger_check.py status     # READ-ONLY: current tune registers + panel page
    uv run python tools/s2000_tune_trigger_check.py run        # the real check (interactive - needs a terminal)
    uv run python tools/s2000_tune_trigger_check.py run --semitones 7     # a different test tune (default +12 = an octave up)

===================================================================================================================================================
FOR THE NEXT AGENT - background (everything below was measured on the user's real S2000, 2026-10-09)
===================================================================================================================================================
The Program Editor's Global tab (`ui/global_tab.py`, registry in `core/global_settings.py`) edits the S2000/S3000 GLOBAL-page settings as misc BYTE
registers. Almost all of them work. THREE are shown but DISABLED (`ui/global_tab.py` `DISABLED_SETTINGS`), because writing them doesn't do what the
panel does:

  1. Tune (semitones, byte 64) and 2. Fine tune (cents, byte 65): the write is ACCEPTED ("Sampler confirmed: OK") and READS BACK exactly - but has no
     effect. Evidence: `~/.akaisds/misc_probe/panel_tune_zero.json`, `panel_tune_plus50.json`, `app_tune_plus50.json` are full dumps (byte, word AND
     dword banks, indices 0-255 = 768 registers each) with tune set to 0 / +50 on the PANEL and to +50 by this APP. The last two are IDENTICAL except
     byte 49 (the panel's cursor-value register - noise, see s3k `_MISC_CURSOR_VALUE`). So there is NO hidden "applied tune" register in that range:
     what the panel does beyond writing byte 64 is invisible to RMISCDATA. The machine keeps the stored value without acting on it - the same class
     of behaviour `s3k` documents for the SCSI ID ("stores the choice and does NOT send the machine to look at it"; see `S3kBridge._force_reread`,
     whose trigger is a write to byte 4 or the partition byte). So the open question is: WHAT MAKES IT ACT?
     That is what this tool tries (below).
  3. Program change channel (byte 71): the sampler answers OK but the register KEEPS ITS OLD VALUE whatever is written (6, 0, 15, 5 all read back 1;
     only writing the value it already held "worked"). A different failure from tune: a refused write, not an unapplied one. NOT covered by this tool.
     (UPDATE: the whole-block misc data holds the channel at offset 0 - see `tools/s2000_mdata_write_check.py --setting pc`, the thing to try first.) To investigate it otherwise, take the same wider dumps as for tune - `dump pc1 --max 255 --banks 1,2,3`, then set the channel to 6 ON THE PANEL and
     `dump pc6 --max 255 --banks 1,2,3`, then `diff pc1 pc6` (tools/s2000_misc_probe.py) - and look for ANOTHER register that moves (the byte-71
     dumps from 2026-10-09 only covered indices 0-127 of the byte/word banks). Also worth trying: write it while the panel is on the GLOBAL page vs
     another page (the register may only be writable in some state - s3k notes byte 4 "answers OK and ignores the write in one state").
     Register map for everything else: `core/global_settings.py`'s docstring.

The user said "output level works" (and every other Global control), so not every audible setting needs a trigger - only tune is known not to act.

History of this check (2026-10-09, all on the user's S2000):
  - 1st attempt: the sampler's tune was already +12 (left by an earlier aborted attempt), so T1/T2 wrote +12 over +12 and proved nothing - `run` now
    REFUSES to start unless the tune is 0 st. It also crashed in T3 and its restore failed, because of the next point.
  - The sampler INTERMITTENTLY answers a misc read (or a write's read-back) with a 1-byte-body frame that s3k cannot decode ("extended data: expected
    at least a 7-byte body, got 1"). It moved between registers and runs (byte 91 and byte 65 in different attempts; byte 64 mostly fine). The tool now
    retries, writes with a retried READ as the verdict, survives a failed step and prints a "[diagnostic] register N raw reply: ..." line the first time
    a register fails - if that line shows up, KEEP IT: it is the raw frame, and nobody has seen it yet (likely an OK `REPLY` where data was expected;
    unconfirmed). The app's own `_handle_set_global_setting` uses s3k's `_misc_write_verify`, so it can in principle hit the same error - the app log
    (`~/.akaisds/akaisds.log`, "global setting ...") showed none in ~40 writes, but if the Global tab ever reports a spurious failure, give
    `BridgeWorker` the same retry-the-read treatment (see `misc_write_verified` here).
  - ANSWERED by the user (same day): the panel's TUNE screen stayed at 0 the whole time, while byte 64 read +12 for at least two sessions (and the
    sampler really was never detuned). So byte 64 is NOT merely "stored but not yet applied" - it is a copy the sampler neither shows nor uses, and the
    real tune variable lives somewhere else. That makes the triggers below (T1-T4) a long shot - T4 (page round trip, which makes the panel redisplay) was
    never reached in the failed first attempt - so DO THE `mdump` EXPERIMENT FIRST if time is short: the whole-block misc data (RMDATA/MDATA, 0x10/0x11)
    is the most likely home of the real settings. `tools/s2000_misc_probe.py mdump <label>` / `mdiff` (read-only; see its docstring): dump with TUNE at 0
    and at +50 set ON THE PANEL and diff. If an offset moves, the fix is a read-modify-write of that block (like core/s1000_bridge.py does for the S1000;
    keep the length the sampler sent, back it up first, test on a spare unit state) - NOT more byte-register writes. The same experiment should be repeated
    for the program change channel (1 vs 6 on the panel), which also refuses byte writes.

What the user must do (they will run this later; the app must be CLOSED - it owns the MIDI ports):
  a. Connect the S2000 as usual (same MIDI ports as the app: `~/.akaisds/config.json`). Load any program with a sustained sound (a pad/organ) on
     MIDI channel 1 and make sure the sampler is in SINGLE or GLOBAL mode (not busy loading/saving).
  b. On the sampler's front panel go to the GLOBAL page and to the screen showing "TUNE" (semitones/cents). Leave it there.
  c. Have a way to play a HELD note and compare its pitch (the sampler's own Play button on GLOBAL works, or a keyboard on the sampler's MIDI input).
  d. `uv run python tools/s2000_tune_trigger_check.py status`  - confirm it talks to the sampler and shows tune 0 (note the page it reports; "unknown"
     is fine - the page register is the one that most often answers with the undecodable frame). TUNE MUST BE 0 ON THE PANEL: `run` refuses otherwise.
  e. `uv run python tools/s2000_tune_trigger_check.py run` and follow the prompts. After each step it asks two questions, answered y/n/?:
       - Did the PITCH of a held note change (compared with the reference note it asks you to hear first)?
       - Did the TUNE value on the sampler's DISPLAY change (the TUNE screen from step b)?
     The tool tries these triggers, ONE AT A TIME, after writing the test tune (default +12 semitones = clearly one octave up):
       T1  the plain write itself (the baseline - we expect "no" for pitch)
       T2  the same value written again
       T3  the fine-tune register (byte 65) re-written with its CURRENT value (maybe the engine recombines both on a cents write)
       T4  a page round trip: the panel is sent to another main-menu page (MULTI, or SINGLE if already there) and back to GLOBAL - the panel's own
           redisplay/recompute when entering the page is the most likely trigger
     It stops at the first trigger that changes the pitch (the "winner"), then restores the tune it started with, RE-FIRES the winning trigger so the
     restore takes effect on the machine too, and tells you to confirm the pitch is back. If nothing changes the pitch it just restores byte 64.
  f. The answers are saved to `~/.akaisds/misc_probe/tune_trigger_check_<timestamp>.json` - read that file, don't trust the console scrollback.
  Safety: it only ever writes byte 64 (tune; value taken from `core/global_settings.py`, range-checked), re-writes byte 65 with the value it read, and
  the page register (91) with 0, 2 or 8 - never 11 (documented in s3k to hang the machine). If it dies midway, set TUNE back on the panel (0 st).

What to do with the result (then delete this paragraph's open question and update AGENTS.md's "Global tab" section):
  - A trigger WORKS (pitch changes): add it to `BridgeWorker._handle_set_global_setting` (core/program_editor_bridge.py) for the tune settings -
    e.g. after the verified write, fire the trigger - add a test in tests/test_program_editor_bridge.py, remove "tune_semitones"/"tune_cents" from
    `DISABLED_SETTINGS` (ui/global_tab.py) and update its tests. Check the side effect (a page round trip moves the panel; say so in the UI or find a
    quieter trigger).
  - NOTHING works: ideas NOT tried here - writing byte 49 (the cursor-value register, which the panel writes and the machine seems not to read - but
    maybe it triggers the edit handler); writing the word-bank twin of a register; a different unit/firmware may behave differently; ask the user
    whether the sampler's display changes at all after a write (if neither display nor pitch change, the machine never re-reads the register).
    Then leave Tune disabled and say why in the tab's tooltip.
"""

import json
import os
import sys
import time

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.global_settings import GLOBAL_SETTINGS
from s3k import messages as m
from s3k.bridge import DeviceError

SNAPSHOT_DIR = os.path.join(os.path.expanduser("~"), ".akaisds", "misc_probe")
TUNE_SEMITONES = GLOBAL_SETTINGS["tune_semitones"]
TUNE_CENTS = GLOBAL_SETTINGS["tune_cents"]
# s3k's main-menu page values (S3kBridge.MODES). Only these are ever written; 11 hangs the machine.
PAGE_SINGLE, PAGE_MULTI, PAGE_GLOBAL = 0, 2, 8
PAGE_NAMES = {0: "SINGLE", 1: "SINGLE EDIT", 2: "MULTI", 3: "MULTI EDIT", 4: "SAMPLE", 5: "SAMPLE EDIT",
              6: "EFFECTS", 7: "EFFECTS EDIT", 8: "GLOBAL", 9: "SAVE", 10: "LOAD"}
SETTLE_S = 1.0  # let the machine finish a page change / write before the next step

PLAN = """\
Plan (nothing is written by `plan`):
  0. read the current tune (byte 64), fine tune (byte 65) and panel page (byte 91) - refuses to go on unless the tune is 0 st; the values are put back at the end
  0. you hear a REFERENCE held note at the current tune
  1. T1  write the test tune to byte 64 (default +12 semitones) and read it back  -> pitch? display?
  2. T2  write the same value again                                                  -> pitch? display?
  3. T3  re-write fine tune (byte 65) with its CURRENT value                         -> pitch? display?
  4. T4  page round trip: panel -> MULTI (or SINGLE) -> GLOBAL                       -> pitch? display?
  stop at the first step where the pitch changes (the winner); then restore byte 64 and re-fire the winner so the restore takes effect.
Results are saved to ~/.akaisds/misc_probe/tune_trigger_check_<timestamp>.json"""


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


_IO_ERRORS = (ValueError, DeviceError, TimeoutError)
_TRIES = 4
_diagnosed = set()


def diagnose(bridge, index):
    """Once per register: print the raw frame the sampler answered a read with, so a failure leaves evidence (the decode error alone hides it)."""
    if index in _diagnosed or not hasattr(bridge, "send_and_receive"):
        return
    _diagnosed.add(index)
    try:
        frame = m.HeaderRequest(command=m.Command.RMISCDATA, index=index, selector=1, offset=0, count=1,
                                exclusive_channel=getattr(bridge, "exclusive_channel", m.DEFAULT_EXCLUSIVE_CHANNEL)).encode()
        reply = bridge.send_and_receive(frame, timeout=1.0)
        print(f"    [diagnostic] register {index} raw reply: {bytes(reply).hex(' ')}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"    [diagnostic] register {index}: {e}", flush=True)


def misc_read(bridge, index, tries=_TRIES):
    """One misc byte. On 2026-10-09 the sampler intermittently answered a read (and a write's read-back) with a 1-byte-body frame s3k cannot
    decode ("extended data: expected at least a 7-byte body, got 1") - the failures moved between registers and runs - so retry before giving up."""
    last = None
    for attempt in range(tries):
        try:
            return bridge._misc_byte(index)
        except _IO_ERRORS as e:
            last = e
            if attempt == 0:
                diagnose(bridge, index)
            time.sleep(0.3)
    raise last


def misc_write_verified(bridge, index, value, what, tries=_TRIES):
    """Write a misc byte and believe a retried READ, not the ack or s3k's own read-back (which is what raised the decode error)."""
    last = None
    for _ in range(tries):
        try:
            bridge._misc_byte(index, value)
        except _IO_ERRORS:
            pass  # the write may well have taken; the read decides
        time.sleep(0.15)
        try:
            got = misc_read(bridge, index, tries=2)
        except _IO_ERRORS as e:
            last = e
            continue
        if got == value:
            return got
        last = DeviceError(f"{what}: asked for {value}, register reads {got}")
    raise last


def read_page(bridge):
    """The panel page (byte 91), or None. Tolerant on purpose: on 2026-10-09 the sampler answered this read with a frame s3k could not decode
    ("extended data: expected at least a 7-byte body, got 1") while the tune registers read fine - so nothing here may depend on it."""
    try:
        return misc_read(bridge, 91, tries=2)
    except _IO_ERRORS as e:
        print(f"    (could not read the panel page: {e})", flush=True)
        return None


def set_page(bridge, target):
    """Write the page register without trusting the ack OR the read-back (both have misbehaved). Returns the page it reads back, or None."""
    assert target in (PAGE_SINGLE, PAGE_MULTI, PAGE_GLOBAL)  # never anything else - 11 hangs the machine
    try:
        bridge._misc_byte(91, target)
    except _IO_ERRORS as e:  # the write may well have taken
        print(f"    (page write to {target} reported: {e})", flush=True)
    time.sleep(SETTLE_S)
    return read_page(bridge)


def read_state(bridge):
    raw_semi = misc_read(bridge, TUNE_SEMITONES.register)
    raw_cents = misc_read(bridge, TUNE_CENTS.register)
    return {"raw_semitones": raw_semi, "raw_cents": raw_cents, "page": read_page(bridge)}


def describe(state):
    def shown(setting, raw):
        try:
            return f"{setting.decode(raw):+d}"
        except ValueError:
            return f"? (raw {raw})"

    page = state["page"]
    page_text = "unknown (not readable)" if page is None else f"{page} ({PAGE_NAMES.get(page, 'unknown')})"
    return (f"tune {shown(TUNE_SEMITONES, state['raw_semitones'])} st, fine tune {shown(TUNE_CENTS, state['raw_cents'])} ct, "
            f"panel page {page_text}")


def ask(question):
    while True:
        answer = input(f"    {question} [y/n/?] ").strip().lower()
        if answer in ("y", "n", "?"):
            return answer


def ask_note():
    return input("    anything else you noticed (optional, Enter to skip): ").strip()


def write_tune(bridge, raw):
    misc_write_verified(bridge, TUNE_SEMITONES.register, raw, "setting the global tune")


def trigger_plain_write(bridge, ctx):
    write_tune(bridge, ctx["test_raw"])


def trigger_same_value_again(bridge, ctx):
    write_tune(bridge, ctx["test_raw"])


def trigger_fine_tune_rewrite(bridge, ctx):
    raw = misc_read(bridge, TUNE_CENTS.register)
    TUNE_CENTS.decode(raw)  # refuse to re-write something the editor couldn't have shown
    misc_write_verified(bridge, TUNE_CENTS.register, raw, "re-writing the fine tune")


def trigger_page_round_trip(bridge, ctx):
    page = read_page(bridge)  # None if unreadable: assume the sampler is on GLOBAL, as the instructions say
    away = PAGE_MULTI if page != PAGE_MULTI else PAGE_SINGLE
    for target in (away, PAGE_GLOBAL):
        got = set_page(bridge, target)
        print(f"    page -> {target} ({PAGE_NAMES[target]}), register now reads {got}", flush=True)


TRIGGERS = [
    ("T1", "the plain write of the test tune (baseline)", trigger_plain_write),
    ("T2", "the same value written again", trigger_same_value_again),
    ("T3", "fine tune (byte 65) re-written with its current value", trigger_fine_tune_rewrite),
    ("T4", "page round trip (another page, then back to GLOBAL)", trigger_page_round_trip),
]


def stage_run(semitones):
    if not sys.stdin.isatty():
        print("`run` is interactive - run it in a terminal.")
        sys.exit(2)
    TUNE_SEMITONES.encode(semitones)  # raises for an out-of-range value
    bridge = open_bridge()
    original = read_state(bridge)
    print("sampler is at:", describe(original), flush=True)
    if TUNE_SEMITONES.decode(original["raw_semitones"]) != 0:
        # a meaningful pitch comparison needs a known start, and a restore to a non-zero tune would leave the sampler detuned: the first
        # real attempt (2026-10-09) started at +12, wrote +12 over +12 and proved nothing
        print("\nThe sampler's tune is not 0 st. Set TUNE to 0 on the panel (and make sure the pitch is back to normal), then run this again.")
        os._exit(2)
    test_raw = TUNE_SEMITONES.encode(semitones)
    results = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "original": original, "test_semitones": semitones, "steps": []}
    winner = None
    path = os.path.join(SNAPSHOT_DIR, f"tune_trigger_check_{time.strftime('%Y%m%d_%H%M%S')}.json")
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)

    def save():
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=1)

    try:
        print("\nStep 0: play a HELD note and remember its pitch (the reference). The sampler's TUNE screen should be showing.")
        input("    Press Enter when you've heard it... ")
        ctx = {"test_raw": test_raw}
        for name, what, trigger in TRIGGERS:
            print(f"\n{name}: {what}", flush=True)
            if name == "T1":
                print(f"    writing test tune {semitones:+d} st (raw {test_raw})...", flush=True)
            try:
                trigger(bridge, ctx)
                time.sleep(SETTLE_S)
                state = read_state(bridge)
            except _IO_ERRORS as e:
                # a failed step must not abort the run (or skip the restore): record it and go on
                print(f"    STEP FAILED ({e}) - recorded, moving on.", flush=True)
                results["steps"].append({"step": name, "what": what, "error": str(e)})
                save()
                continue
            print("    sampler now reports:", describe(state), flush=True)
            print("    Play the held note again and look at the sampler's TUNE screen.")
            pitch = ask("Did the PITCH change from the reference?")
            display = ask("Did the TUNE value on the sampler's DISPLAY change?")
            step = {"step": name, "what": what, "pitch_changed": pitch, "display_changed": display, "register_after": state,
                    "note": ask_note()}
            results["steps"].append(step)
            save()
            if pitch == "y":
                winner = (name, trigger)
                print(f"\n>>> {name} made the sampler act on the stored tune. Stopping the search.", flush=True)
                break
    finally:
        print("\nRestoring the original tune...", flush=True)
        try:
            write_tune(bridge, original["raw_semitones"])
            if winner is not None:
                print(f"    re-firing {winner[0]} so the restore takes effect on the machine", flush=True)
                winner[1](bridge, {"test_raw": original["raw_semitones"]})
            now = read_state(bridge)
            results["restored"] = now
            print("    sampler is now:", describe(now), flush=True)
            if original["page"] in (PAGE_SINGLE, PAGE_MULTI, PAGE_GLOBAL) and original["page"] != now["page"]:
                got = set_page(bridge, original["page"])
                print(f"    page put back to {original['page']} ({PAGE_NAMES.get(original['page'])}), register reads {got}", flush=True)
        except Exception as e:  # noqa: BLE001 - never lose the measurements to a failed cleanup
            results["restore_error"] = str(e)
            print(f"    RESTORE FAILED: {e}\n    -> put the sampler back on the panel by hand: {describe(original)}", flush=True)
        results["winner"] = winner[0] if winner else None
        save()

    print("\nSummary")
    for step in results["steps"]:
        if "error" in step:
            print(f"  {step['step']}: FAILED ({step['error']})  {step['what']}")
        else:
            print(f"  {step['step']}: pitch={step['pitch_changed']} display={step['display_changed']}  {step['what']}")
    print("  ->", f"{results['winner']} makes the sampler act on a written tune." if winner else "no trigger changed the pitch.")
    print("Now play the held note: it should be back at the reference pitch (if not, set TUNE back on the panel).")
    print(f"Saved: {path}\nGive that file to the next agent (see this tool's docstring for what to do with it).")
    os._exit(0)  # the MIDI ports are native - don't wait for their destructors


def stage_status():
    bridge = open_bridge()
    print(describe(read_state(bridge)))
    os._exit(0)


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("plan", "status", "run"):
        print(__doc__)
        sys.exit(2)
    if args[0] == "plan":
        print(PLAN)
    elif args[0] == "status":
        stage_status()
    else:
        semitones = 12
        if "--semitones" in args:
            semitones = int(args[args.index("--semitones") + 1])
        stage_run(semitones)


if __name__ == "__main__":
    main()
