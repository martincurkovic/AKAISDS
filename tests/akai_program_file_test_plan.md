# Program files (.p1 / .p3) - real-hardware test plan

Nothing in `core/akai_program_file.py` or the Programs tab's Save.../Load... has been run against a
sampler or against a file from another tool. Each step below is a guess until measured.

## What is a guess
1. **File layout** (150/192-byte header + N same-size keygroups, no extra file header, N at offset 42)
   comes from the S3000 disk-format page and lakai's SysEx docs, not from a real exported file.
2. **File-relative pointers** (FIRSTKG = block size, NXTKG = next block's offset, last = EOF) match what a
   real S1000 reported for a program at address 0, but nothing says another tool requires or ignores them.
3. **What a sampler does with the pointer bytes of an incoming PDATA/KDATA** (store them, ignore them, recompute?). The load
   is written to not depend on it: it only sends pointers read back from the sampler, except in the create PDATA, and aborts
   before writing keygroups if that PDATA's FIRSTKG ended up equal to another program's. The fake used in the tests models a
   sampler that allocates addresses (and one that honours the incoming FIRSTKG) from the spec + the measured appends - not a real one.
4. Real S2000/S3000 blocks are exactly 192 bytes for BOTH program and keygroup (the existing duplicate flow assumes it).
5. An S1000's real block length is 150 (one tester log). Any other length makes Save refuse with a message.

## Steps (throwaway program, app log kept: Help > Open Log Folder)
1. Save a program with 1 keygroup, then one with several. Note the extension and file size (150*(n+1) / 192*(n+1)).
2. Open the saved file in another tool (akaiutil, an Akai disk image tool, or copy to a disk image) - does it list the
   program, name and keygroups correctly? **This is the compatibility check - report any failure with the file.**
3. Do the reverse: take a `.p1`/`.p3` made by another tool and Load it. Compare what the editor shows.
4. Load a file you just saved: it must come back as a NEW program (clashing name -> prompted for another),
   with every keygroup, and play with its samples resident.
5. Load into a sampler WITHOUT the samples: the confirmation lists them; zones are silent until loaded.
6. In the log (Help > Open Log Folder) find the `program load:` lines. Report: `FIRSTKG sent X, sampler holds Y` (X != Y means the sampler
   ignores/recomputes it - the good case), where each keygroup was `placed`, and `zone SBADD read back ... (file had ...)`
   (different = the sampler recomputes the sample-header address itself; identical non-zero values from another disk = it may be
   trusting a stale address - check which sample plays).
7. S1000 only: after a Load, read every keygroup of the new program and of the others (Refresh) - a
   "block identifier is 3, expected 2" error means the chain was damaged, stop and send the log.
