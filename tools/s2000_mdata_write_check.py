"""
Standalone dev tool - NOT part of the app. HARDWARE CHECK (S2000/S3000) that WRITES the sampler's whole-block miscellaneous data (MDATA, 0x11).
Question it answers: does writing a global setting through the WHOLE BLOCK make the sampler act on it, where writing its byte register does not?
Two settings can be tested (one per run): the TUNE (block offset 7) and the PROGRAM CHANGE CHANNEL (block offset 0). It backs the block up first, changes
ONLY that one byte, asks you to listen/look, then writes the original block back and verifies it.

    uv run python tools/s2000_mdata_write_check.py plan      # prints what it will do, touches nothing (no MIDI)
    uv run python tools/s2000_mdata_write_check.py run                       # TUNE test (default +12 semitones = an octave up)
    uv run python tools/s2000_mdata_write_check.py run --semitones 7         # a different test tune
    uv run python tools/s2000_mdata_write_check.py run --setting pc          # PROGRAM CHANGE CHANNEL test (default channel 6)
    uv run python tools/s2000_mdata_write_check.py run --setting pc --channel 3
    uv run python tools/s2000_mdata_write_check.py restore ~/.akaisds/misc_probe/mdata_backup_<timestamp>.json   # put a saved block back (after a crash)

===================================================================================================================================================
FOR THE NEXT AGENT - background (everything measured on the user's real S2000, 2026-10-09)
===================================================================================================================================================
Problem (STATUS 2026-10-09: the app now writes these through the block - see AGENTS.md's "Global tab" section; this tool is the standalone way to test it): the byte registers cannot edit Tune / Fine tune / Program change channel.
Writing the tune through its misc BYTE register (64) is accepted and reads back, but the sampler neither shows it on the panel nor uses it - the panel's
TUNE stayed 0 while byte 64 read +12. A full dump (banks 1-3, indices 0-255) of a panel-set +50 equals one of an app-written +50 (only the panel-cursor
byte 49 differs), so no other register is involved. See `tools/s2000_tune_trigger_check.py` (candidate "triggers": none ever got to run properly) and
`core/global_settings.py` for the register map.

New lead: the OLDER whole-block misc data. `RMDATA` (0x10, no payload) is answered with `MDATA` (0x11): 96 payload bytes = a 48-byte block, nibbled
(`tools/s2000_misc_probe.py mdump`/`mdiff`). Measured block (decoded, all 48 bytes, the rest zero):
    offsets 0-2 = PROGRAM CHANGE: 0 = channel, 0-based; 1 = OMNI flag; 2 = ENABLED flag (0 = Off). Measured with `mdump` + `mdiff` over the panel states:
        ch 1 -> [0,0,1]   ch 6 -> [5,0,1]   Off -> [0,0,0]   Omni -> [15,1,1]   (the channel byte keeps its last value under Off/Omni)
        The byte register 71 (Off 0 / 1-16 / Omni 17) is a composite of these three, which is probably why every write to it is ignored.
    offset 6 = fine tune (cents + 50, same as byte 65; measured -50/0/+50 -> 0/50/100)    offset 7 = tune (semitones + 9, same as byte 64)
    offset 8 = output level (raw 12 = 0 dB, byte 15 - matched by value only, never varied in an `mdump`)
The tune byte read 21 (+12, left over from a failed tool run) while the panel showed 0 - i.e. it is another VIEW of the same stored byte, NOT a separate
"real" tune.
The hope, untested: the sampler's own handler for an incoming MDATA block (the S1000-style whole-block write) runs the apply/recalculate step that a single
misc-byte write skips (and, for the program change channel, maybe accepts what the byte register refuses). That is what this tool tests.

Safety (why this is not reckless, and what is NOT known):
  - `s3k` lists MDATA among the "destructive on write" commands because it REPLACES the whole block; the other 47 bytes are written back exactly as read,
    and the block is saved to `~/.akaisds/misc_probe/mdata_backup_<timestamp>.json` BEFORE anything is sent (no backup, no write).
  - It refuses to run unless the block is exactly 48 bytes, the sampler's tune is 0 st, and block offset 7 equals byte register 64 (so the layout assumptions
    above still hold). It restores the original block at the end (also on an error or Ctrl-C) and verifies it by reading it back.
  - UNKNOWN: whether the sampler accepts an MDATA write at all (it may answer with an error REPLY or ignore it - then the result is "no", fine), and
    whether writing the block has side effects on other global state. After a run, check the panel's GLOBAL page looks as it did, and re-run
    `tools/s2000_misc_probe.py dump` + `diff` against an earlier dump if anything seems off.

What the user must do (the app must be CLOSED - it owns the MIDI ports):
  a. Sampler on, MIDI connected as usual. Load a program with a sustained sound (a pad/organ) on MIDI channel 1.
  b. On the front panel: GLOBAL page, the TUNE screen, **TUNE set to 0** (set it by hand - earlier runs left it at +12 / +50). Leave the screen showing.
  c. Be able to play a HELD note (the Play button on the GLOBAL page, or a keyboard on the sampler's MIDI input).
  d. `uv run python tools/s2000_mdata_write_check.py run`, press Enter once you have heard the reference note, type YES when asked.
  e. After the write, play the held note again and look at the TUNE screen, then answer the two questions (y/n/?):
       - did the PITCH change (default test = +12 semitones, an octave up)?
       - did the TUNE value on the DISPLAY change?
  f. The tool then restores the block. Play the note once more: it should be back at the original pitch (if the pitch changed, the restore re-applies it
     the same way). If not, set TUNE to 0 on the panel by hand. The results go to `~/.akaisds/misc_probe/mdata_write_check_<timestamp>.json`.

  For the PROGRAM CHANGE CHANNEL instead (`run --setting pc`): put the sampler's program change channel on a plain channel 1-16 (NOT Off/Omni) on the
  GLOBAL page's program change screen and leave it showing; the tool changes it to channel 6 (or `--channel N`) through the block and asks whether the
  DISPLAY followed. Same backup/restore. (The byte register 71 refused every write, so a "yes" here is the first working path.)

How to read the result and what to do next:
  - PITCH (or display) CHANGES: the whole-block write is the way. Implement a read-modify-write in the app for tune/fine tune (and check offset 2 for the
    program change channel with `mdump pc1` / `mdump pc6` + `mdiff`): a `BridgeWorker` job that reads the block (RMDATA), changes one offset, writes it
    (MDATA), re-reads and compares, keeps the length the sampler sent, saves a backup like this tool does - modelled on `core/s1000_bridge.py`. Add tests,
    remove the setting from `DISABLED_SETTINGS`, update AGENTS.md's "Global tab" section.
  - NOTHING CHANGES but the block reads back the new value: same as the byte register - the stored copy is not what the sampler uses. Remaining ideas:
    the panel's own page-redisplay (T4 of `s2000_tune_trigger_check.py`, which never ran properly), a different address space (other misc banks 4-7:
    smpte/name/flag, indices > 255), or accept that tune cannot be set remotely and leave it disabled with the tooltip.
  - The sampler answers an ERROR REPLY / does not answer / the block does not read back: MDATA writes are not supported on this firmware; record the
    reply code (printed and saved) and stop.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import s2000_misc_probe as probe  # noqa: E402  (sets up sys.path/env; open_bridge, SNAPSHOT_DIR)
import s2000_tune_trigger_check as tune_check  # noqa: E402  (misc_read, ask helpers, TUNE_SEMITONES)
from s3k import messages as m  # noqa: E402
from s3k.bridge import DeviceError  # noqa: E402

BLOCK_LEN = 48
OFFSET_PROGRAM_CHANGE = 0  # 0-based channel (measured: panel channel 1 -> 0, 6 -> 5)
OFFSET_PC_OMNI = 1  # 1 = Omni (measured)
OFFSET_PC_ENABLED = 2  # 0 = Off (measured)
OFFSET_TUNE = 7
TUNE = tune_check.TUNE_SEMITONES
REGISTER_TUNE = TUNE.register  # byte 64
PC_SETTING = tune_check.GLOBAL_SETTINGS["program_change_channel"]
REGISTER_PROGRAM_CHANGE = PC_SETTING.register  # byte 71 (the one that refuses writes)
_IO_ERRORS = (ValueError, DeviceError, TimeoutError)
SETTLE_S = 1.0

PLAN = """\
Plan (nothing is sent by `plan`). `run` tests the TUNE; `run --setting pc` tests the PROGRAM CHANGE CHANNEL the same way:
  1. read the sampler's 48-byte misc block (RMDATA) and the setting's byte register; refuse unless: 48 bytes, the block agrees with the register,
     and the start state is known (tune: 0 st; pc: a plain channel 1-16 on the panel)
  2. save the block to ~/.akaisds/misc_probe/mdata_backup_<timestamp>.json (no backup, no write)
  3. you note the reference (tune: hear a held note; pc: read the channel on the panel); you type YES to continue
  4. write the SAME block with ONLY one byte changed (tune: offset 7 -> +12 st; pc: offset 0 -> channel 6) as MDATA; print the sampler's reply
  5. read the block back and show every offset that differs from the original (expected: only that one)
  6. you look at the panel (tune: also play the held note): did the setting change?
  7. write the ORIGINAL block back and verify it by reading it back (also on error / Ctrl-C)
Results -> ~/.akaisds/misc_probe/mdata_write_check_<timestamp>.json"""


def encode_block(block, channel):
    return m.build_frame(m.Command.MDATA, m.encode_nibbles(bytes(block)), exclusive_channel=channel)


def read_block(bridge, tries=4):
    """RMDATA -> the decoded block. Retries: the sampler intermittently answers with a frame that does not decode (see s2000_tune_trigger_check.py)."""
    channel = getattr(bridge, "exclusive_channel", m.DEFAULT_EXCLUSIVE_CHANNEL)
    frame = m.build_frame(m.Command.RMDATA, [], exclusive_channel=channel)
    last = None
    for _ in range(tries):
        try:
            reply = bridge.send_and_receive(frame, timeout=3.0)
            _chan, command, payload = m.parse_frame(reply)
            if command != m.Command.MDATA:
                raise DeviceError(f"expected MDATA, got command {int(command):#04x}")
            return bytes(m.decode_nibbles(list(payload)))
        except _IO_ERRORS as e:
            last = e
            time.sleep(0.3)
    raise last


def write_block(bridge, block):
    """Send an MDATA write and return the sampler's reply as text (an error REPLY or silence is a RESULT, not an exception)."""
    channel = getattr(bridge, "exclusive_channel", m.DEFAULT_EXCLUSIVE_CHANNEL)
    frame = encode_block(block, channel)
    bridge._drain()
    bridge._send(frame, write=True)
    try:
        reply = bridge._receive(3.0, accept=frozenset({int(m.Command.REPLY)}))
        parsed = m.Reply.decode(reply)
        return f"REPLY code {parsed.code} ({'OK' if parsed.ok else 'ERROR'})"
    except _IO_ERRORS as e:
        return f"no usable reply ({e})"


def block_diff(before, after):
    return [(i, before[i] if i < len(before) else None, after[i] if i < len(after) else None)
            for i in range(max(len(before), len(after)))
            if (before[i] if i < len(before) else None) != (after[i] if i < len(after) else None)]


def save_json(path, data):
    os.makedirs(probe.SNAPSHOT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)


def restore_block(bridge, original, results):
    print("\nRestoring the original block...", flush=True)
    try:
        results["restore_reply"] = write_block(bridge, original)
        print(f"    sampler answered: {results['restore_reply']}", flush=True)
        time.sleep(SETTLE_S)
        now = read_block(bridge)
        results["restored_matches"] = now == original
        if now == original:
            print("    block read back identical to the original.", flush=True)
        else:
            print(f"    BLOCK DIFFERS from the original at: {block_diff(original, now)}", flush=True)
            print("    -> put things right by hand on the panel (TUNE 0 st) and run `restore <backup file>` if needed.", flush=True)
    except _IO_ERRORS as e:
        results["restore_error"] = str(e)
        print(f"    RESTORE FAILED: {e}\n    -> run `restore <backup file>` or set TUNE to 0 st on the panel by hand.", flush=True)


def describe_setting(setting, block, register):
    """(offset, problems, what, test_value, shown_start, shown_test) for one of the two testable settings."""
    problems = []
    if setting == "tune":
        offset = OFFSET_TUNE
        if len(block) == BLOCK_LEN and block[offset] != register:
            problems.append(f"block offset {offset} ({block[offset]}) != byte register {REGISTER_TUNE} ({register}) - the layout changed")
        try:
            if TUNE.decode(register) != 0:
                problems.append("the sampler's tune is not 0 st - set TUNE to 0 on the panel first")
        except ValueError as e:
            problems.append(str(e))
        return offset, problems
    offset = OFFSET_PROGRAM_CHANGE
    if len(block) == BLOCK_LEN and not (0 <= block[offset] <= 15 and block[OFFSET_PC_OMNI] == 0 and block[OFFSET_PC_ENABLED] == 1):
        problems.append(f"the program change bytes are {list(block[:3])}: not a plain channel (channel, Omni 0, enabled 1) - "
                        "set the program change channel to a plain 1-16 on the panel first (not Off, not Omni)")
    elif len(block) == BLOCK_LEN and register != block[offset] + 1:
        # not fatal for the experiment, but it says the register and the block disagree - worth seeing
        print(f"    note: byte register {REGISTER_PROGRAM_CHANGE} reads {register}, block offset {offset} reads {block[offset]} "
              f"(expected register = block + 1)", flush=True)
    return offset, problems


def stage_run(setting, semitones, channel):
    if not sys.stdin.isatty():
        print("`run` is interactive - run it in a terminal.")
        sys.exit(2)
    bridge = probe.open_bridge()[1]
    original = read_block(bridge)
    register_index = REGISTER_TUNE if setting == "tune" else REGISTER_PROGRAM_CHANGE
    register = tune_check.misc_read(bridge, register_index)
    print(f"block: {len(original)} bytes: {list(original)}\nbyte register {register_index}: {register}", flush=True)
    offset, problems = describe_setting(setting, original, register)
    if len(original) != BLOCK_LEN:
        problems.append(f"the block is {len(original)} bytes, expected {BLOCK_LEN}")
    if setting == "tune":
        test_value = TUNE.encode(semitones)  # raises for an out-of-range tune
        what = f"tune {semitones:+d} st"
        question = ("PITCH", "Did the PITCH change from the reference?", "Did the TUNE value on the sampler's DISPLAY change?")
        reference = "play a HELD note and remember its pitch (the reference). The sampler's TUNE screen should be showing 0."
        after_prompt = "Play the held note again and look at the sampler's TUNE screen."
    else:
        if not 1 <= channel <= 16:
            print("--channel must be 1-16")
            sys.exit(2)
        if len(original) == BLOCK_LEN and original[offset] == channel - 1:
            channel = 1 if channel != 1 else 6  # writing the value it already holds proves nothing
            print(f"    the sampler already holds that channel - using {channel} as the test channel instead.", flush=True)
        test_value = channel - 1
        what = f"program change channel {channel}"
        question = (None, None, "Did the PROGRAM CHANGE CHANNEL shown on the sampler's display change to the new channel?")
        reference = ("go to the GLOBAL page's program change channel screen on the panel and read the channel it shows "
                     f"(it must be a plain channel 1-16, not Off/Omni; currently the block says {original[offset] + 1 if len(original) == BLOCK_LEN else '?'}).")
        after_prompt = "Look at the sampler's program change channel screen."
    if problems:
        print("\nNot running:\n  - " + "\n  - ".join(problems))
        os._exit(2)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(probe.SNAPSHOT_DIR, f"mdata_backup_{stamp}.json")
    save_json(backup, {"saved": stamp, "block_hex": original.hex(), "block": list(original)})
    print(f"backup saved: {backup}", flush=True)

    results = {"started": stamp, "setting": setting, "test": what, "backup": backup, "original_block": list(original)}
    path = os.path.join(probe.SNAPSHOT_DIR, f"mdata_write_check_{stamp}.json")
    written = False
    try:
        print(f"\nStep 0: {reference}")
        input("    Press Enter when you're ready... ")
        if input(f"\nThis writes the sampler's 48-byte misc block (ONLY offset {offset}, the {setting} byte, changes; backup saved above).\nType YES to continue: ").strip() != "YES":
            print("aborted - nothing was written.")
            os._exit(0)
        new_block = bytearray(original)
        new_block[offset] = test_value
        print(f"\nwriting the block with {what} (offset {offset}: {original[offset]} -> {test_value})...", flush=True)
        written = True
        results["write_reply"] = write_block(bridge, new_block)
        print(f"    sampler answered: {results['write_reply']}", flush=True)
        time.sleep(SETTLE_S)
        after = read_block(bridge)
        results["block_after"] = list(after)
        results["diff_after"] = block_diff(original, after)
        print(f"    block read back; offsets that differ from the original: {results['diff_after'] or 'none'}", flush=True)
        try:
            results["register_after"] = tune_check.misc_read(bridge, register_index)
            print(f"    byte register {register_index} now reads {results['register_after']}", flush=True)
        except _IO_ERRORS as e:
            results["register_after_error"] = str(e)
        print(f"\n    {after_prompt}")
        if question[0]:
            results["pitch_changed"] = tune_check.ask(question[1])
        results["display_changed"] = tune_check.ask(question[2])
        results["note"] = tune_check.ask_note()
        save_json(path, results)
    finally:
        if written:
            restore_block(bridge, bytes(original), results)
            print("    Look at / listen to the sampler once more: it should be back as it was.", flush=True)
        save_json(path, results)

    print("\nSummary")
    print(f"  test         : {what}")
    print(f"  write reply  : {results.get('write_reply')}")
    print(f"  block after  : differs at {results.get('diff_after') or 'no offset'}")
    print(f"  byte register: {results.get('register_after')}")
    print(f"  pitch changed: {results.get('pitch_changed')}   display changed: {results.get('display_changed')}")
    print(f"  restored     : {results.get('restored_matches')}")
    print(f"Saved: {path}\nGive that file to the next agent (see this tool's docstring for what to do with it).")
    os._exit(0)  # the MIDI ports are native - don't wait for their destructors


def stage_restore(backup_path):
    with open(os.path.expanduser(backup_path), encoding="utf-8") as fh:
        block = bytes(json.load(fh)["block"])
    if len(block) != BLOCK_LEN:
        print(f"the backup is {len(block)} bytes, expected {BLOCK_LEN} - not writing it.")
        sys.exit(2)
    bridge = probe.open_bridge()[1]
    results = {}
    restore_block(bridge, block, results)
    os._exit(0)


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("plan", "run", "restore"):
        print(__doc__)
        sys.exit(2)
    if args[0] == "plan":
        print(PLAN)
    elif args[0] == "restore":
        if len(args) != 2:
            print("restore needs the backup file path")
            sys.exit(2)
        stage_restore(args[1])
    else:
        semitones, channel, setting = 12, 6, "tune"
        if "--semitones" in args:
            semitones = int(args[args.index("--semitones") + 1])
        if "--channel" in args:
            channel = int(args[args.index("--channel") + 1])
        if "--setting" in args:
            setting = args[args.index("--setting") + 1]
        if setting not in ("tune", "pc"):
            print("--setting must be `tune` or `pc`")
            sys.exit(2)
        stage_run(setting, semitones, channel)


if __name__ == "__main__":
    main()
