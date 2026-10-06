# Program files (.p1 / .p3) and S1000 keygroup delete - real-hardware test plan

Covers two features, both written and unit-tested against FAKE samplers only (`tests/test_akai_program_file.py`,
`tests/test_s1000_keygroup_delete_rebuild.py`). Nothing here has run on a real sampler or on a file made by another
tool. Design and the reasoning behind the pointer handling: AGENTS.md, "Program files: Save/Load as `.p1`/`.p3`".

Same rules as `s1000_test_plan.md`: **safest to riskiest, and stop at the first step that misbehaves and send the log.**

## Before you start (every part)

1. **Save everything on the sampler to disk** (floppy/SCSI) first. Loading writes new program/keygroup blocks.
2. Use a **throwaway program** (2-3+ keygroups, zones pointing at samples that are loaded) for everything that writes.
3. Settings > Sampler Type must match the sampler (S1000 vs S2000/S3000) - a `.p1` and a `.p3` are different sizes and
   the other family's file is refused on purpose.
4. Know where the evidence is: **Help > Open Log Folder** -> `akaisds.log` (rotates at 8 MB - copy it right after a problem).
   Quick extract:
   `grep -E "program load:|rebuild|dialog \[|status:" ~/.akaisds/akaisds.log`
5. Pre-write backups of a rebuilt program land in `~/.akaisds/program_backups/` (`.p1`).

## Part 0 - no hardware needed

- [ ] `AKAISDS_DEMO_SAMPLER=1`, open the Program Editor, **Save...** a program. Expect a `.p3`, size = 192 x (keygroups + 1)
      (demo "BASS ROUND" has 2 keygroups -> 576 bytes). **Load... is disabled in demo mode** (no add-program primitive) - expected.
- [ ] Open that `.p3` in any other Akai tool you have (akaiutil / a disk-image tool / a hardware-emulating editor). Does it list the
      program, its name and keygroups? **This is the only check of the file layout itself - report any failure with the file attached.**
- [ ] If you can get a **real `.p1`/`.p3` from another tool or disk** (any Akai library disc), keep it for step B4 below. Layout was
      cross-checked only against documentation and two open-source tools' notes (`header + N x block == file size`, N at byte 42).

## Part 1 - S2000/S3000 (you)

**A. Save (read-only on the sampler)**
- [ ] Select the scratch program, **Save...** (button under the Programs list, or right-click). Takes a few seconds per keygroup.
- [ ] File is `NAME.p3`, size 192 x (N + 1). Status bar: `Saved "NAME" (N keygroups)`.
- [ ] Cancel the file dialog -> `Save cancelled`, nothing written.
- [ ] Save the same program twice: the two files are byte-identical (nothing in the file depends on the time).

**B. Load**
- [ ] B1. Load the file you just saved. Expect a prompt for a **different name** (the original is resident - loading the same name would
      make the sampler delete it). Pick one. The confirmation lists the samples the program uses and says whether they are on the sampler.
- [ ] B2. Afterwards: a NEW program with that name, **the new program is selected** (not row 0), same keygroup count, same key ranges,
      zones show the same samples. On the sampler's own screen the program looks identical to the original.
- [ ] B3. **Play it** - same sound as the original, every key range and velocity layer.
- [ ] B4. Load a real `.p3` made by another tool (see Part 0). Compare with what the editor and the sampler show.
- [ ] B5. Load a program whose samples are NOT on the sampler: the confirmation names the missing ones; the zones are silent until the
      samples are loaded (then they should play without reloading the program - samples are found by NAME).
- [ ] B6. Check the other programs on the sampler are unchanged (play one, or compare their parameters before/after).

**C. Refusals and errors (none should write anything)**
- [ ] Load a `.p1` while set to S2000/S3000 -> a message about the sampler type, nothing sent.
- [ ] Load a non-program file renamed `x.p3` -> "Couldn't read this file", nothing sent.
- [ ] Load a truncated copy of a real file (cut a few bytes off) -> "the file is truncated".
- [ ] Pull the MIDI cable partway through a Load -> an error that says a partly loaded program may remain; refresh and delete it.

**D. Evidence to send back (grep the log)**
- [ ] `program load: new program N FIRSTKG sent X, sampler holds Y`  - **X != Y = the sampler ignores/recomputes the pointer (the good case).**
      X == Y means it stores what it is sent (the load's safety check then matters).
- [ ] `program load: keygroup K placed at ADDRESS (previous terminator T)`.
- [ ] `program load: keygroup K NXTKG .., zone SBADD read back [...] (file had [...])` - SBADD is the sample-header address. Read back
      DIFFERENT from the file = the sampler recalculates it from the name (good). Identical and non-zero for a file from another disk = it may be
      trusting a stale address: check which sample each zone really plays.

## Part 2 - S1000 tester

Everything in Part 1, with Sampler Type = **Akai S1000** and `.p1` files (150 x (N + 1) bytes), plus:
- [ ] After EVERY Load: **Refresh**, then click every keygroup of the new program AND of two or three other programs. A
      `block identifier is 3, expected 2` error means a chain is damaged - **stop, do not write anything more, send the log.**
- [ ] The first Load ever: watch for the message `... BEFORE any keygroup was written` - it means the sampler stored the pointer we had to send
      for the create step and linked the new program into an existing one. Nothing further was written; an empty program may remain
      (delete it on the front panel). Send the log.
- [ ] Any message starting `... did not check out afterwards` -> **stop**; it names what differed (another program changed, bytes that differ,
      keygroup addresses out of order). Reload the sampler from the disk backup you made. Send the log.
- [ ] Note the block lengths in the log (`S1000Bridge: read ... N bytes`). Anything other than 150 makes Save refuse with a message - that
      itself is useful information.
- [ ] Re-sending a block under its own name is assumed to be an in-place replace (see `s1000_test_plan.md`) - Load never relies on it, but
      the rename step of the rebuild below does.

## Part 3 - S1000 Delete Keygroup by rebuild (flag OFF by default)

**Only after Part 2 passes on a real S1000.** The editor says "not reliable" until the flag is on. From a source checkout set
`KEYGROUP_DELETE_BY_REBUILD = True` in `src/core/s1000_bridge.py` (a packaged app can't be switched). It takes ~1 minute for a big program.
Use a throwaway program with **3+ keygroups**, and a fresh disk backup.

- [ ] The confirm dialog says the program will be rebuilt, backed up first, and end up last in the list.
- [ ] Delete the **middle** keygroup. Expect, in order: a `.p1` appears in `~/.akaisds/program_backups`; a temporary program `NAME-TMP`
      shows up on the sampler; then only the program under its original name, **one keygroup fewer**, last in the list and selected.
- [ ] The surviving keygroups are the right ones, in order (compare key ranges and zones with the original / the backup `.p1`). Play it.
- [ ] Other programs unchanged (play / compare). Refresh and click through every keygroup of every program: no `block identifier` errors.
- [ ] Repeat for the **first** and the **last** keygroup, and for a program with a 12-character name (the temp name truncates it).
- [ ] The program's MIDI program number is unchanged (it keeps the file's value).
- [ ] A one-keygroup program: Delete Keygroup refuses (use Delete Program).
- [ ] Log: `S1000 delete keygroup K of program P 'NAME' by rebuild: backup <path>` ... `done`; and whether `program N's addresses moved after DELP
      (content intact)` appears - **that tells us whether a real S1000 compacts memory on DELP** (not yet measured).
- [ ] Failure behaviour you CAN safely check: with a nearly-full program memory (if you can arrange it) a rebuild should fail at the copy step
      with `The original program "NAME" was NOT changed` and leave the original alone.

**If a rebuild ends in an error after the delete** (`"NAME" was deleted but the check afterwards failed`): the program without that keygroup is
on the sampler as `NAME-TMP` and the original is in `~/.akaisds/program_backups`. Loading is disabled for the session. Check the programs, rename
`-TMP` by hand if it is fine, send the log.

## What to send back

The whole `akaisds.log` (or the extract above), which sampler and OS version, MIDI interface, the program's keygroup count, and for any
failure the exact dialog text. For file-layout problems, the file itself.

## What is a guess (so a surprise here is information, not a bug in your setup)

1. **File layout** (150/192-byte header + N same-size keygroups, no extra file header, N at offset 42): from the S3000 disk-format page, the S1000
   SysEx spec and two open-source tools' notes (2000+ real programs satisfy `header + N x block == file size`) - not from a file this app made
   being opened elsewhere.
2. **File-relative pointers** (FIRSTKG = block size, NXTKG = next block's offset, last = EOF): match the spec's defaults (`150,0`, `44,1`=300, 450...)
   and an S1000 log, nothing says another tool needs them.
3. **What a sampler does with the pointer bytes of an incoming PDATA/KDATA** (store, ignore, recompute) is unknown. The load never sends a pointer the
   sampler didn't give it, except in the create PDATA, and aborts before writing keygroups if that PDATA's FIRSTKG ended up equal to another
   program's. The fake used in the tests models both a sampler that allocates addresses and one that honours the pointer - neither is a real unit.
4. S2000/S3000 blocks are exactly 192 bytes for both program and keygroup (the existing duplicate flow assumes it); an S1000's are 150 (one tester log).
5. Whether DELP on an S1000 leaves other programs' content intact, and whether there is room for a second copy of the program during a rebuild.
6. Zone sample-header addresses (`SBADD`) and crossfade factors are left as the file has them and checked only by read-back/logging.
