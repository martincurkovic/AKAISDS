# S1000 memory layout and DELK - measured findings (moved out of AGENTS.md 2026-10-09)

Verbatim from AGENTS.md's S1000 section. The conclusions that matter day to day (keygroup delete is DISABLED, create-from-slices never deletes) stay in AGENTS.md; this is the evidence.
Real-sampler tester logs, 2026-10-05 and 2026-10-06. The rebuild alternative is described in AGENTS.md under "Program files".

**S1000 memory layout and DELK (measured, 2026-10-05 tester log) - why create-from-slices
never deletes**: programs, keygroups and sample headers are 150-byte blocks in ONE linked
memory area; bytes 1-2 of a program block (`FIRSTKG`) and of a keygroup block (`NXTKG`) are
ABSOLUTE addresses (program 0 at 0 with 10 keygroups ends at 0x672, the 12 sample headers
that follow end at 0xd7a where the next program starts). When a keygroup is appended the
sampler repoints the PREVIOUS keygroup's `NXTKG`, but the last one keeps whatever we sent -
a clone's last keygroup carries the template's stale terminator. A real `DELK` was
acknowledged but (a) left the program's `GROUPS` unchanged and (b) left the chain ending in
that stale pointer (into a sample header); the next "add keygroup" used the stale `GROUPS` as
its index, walked off the chain and linked the new keygroup into ANOTHER program (program 0
grew a keygroup 6 = the new clone, its 7-9 orphaned, "block identifier is 3, expected 2" on
every later read). That is what "Create new program with slices" did with a multi-keygroup
template (clone N, DELK N-1). So: `_handle_create_program(first_keygroup_only=True)` clones
only keygroup 0 on an S1000 and `_create_program_from_slices` refuses to continue if the clone
has more than one - **no DELK anywhere in that flow, don't reintroduce one**. The standalone
Delete Keygroup goes through `BridgeWorker._delete_keygroup_s1000`: snapshot (target program
in full + other programs within `_S1000_VERIFY_READ_BUDGET` reads), DELK, rewrite `GROUPS` via
PDATA if the sampler left it, snapshot again and compare keygroup CONTENT (pointer bytes
ignored, logged as `S1000 delete check:` lines). Any mismatch raises, shows a dialog, and sets
`s1000_keygroup_delete_blocked` (action disabled for the session).

**Standalone Delete Keygroup on an S1000 is DISABLED (`s1000_bridge.KEYGROUP_DELETE_SUPPORTED = False`) after TWO failed real-sampler
tests (2026-10-06 tester logs). Don't re-enable it, and don't try variants blind - get a measurement that explains the rejection first.**
What is known:
- **Flow 1 (failed)**: DELK of the chosen keygroup, then a PDATA rewriting `GROUPS`. DELK(program 2, keygroup 0) advanced the program's `FIRSTKG` by
  150 (no memory compaction), left `GROUPS` unchanged and left the last keygroup's stale `NXTKG`; the PDATA with `GROUPS=9` got `REPLY` error
  `47 00 16 48 01` (code 01); the program kept 9 reachable keygroups but `GROUPS=10` ("block identifier is 3, expected 2" on every read of
  keygroup 9).
- **Flow 2 (failed, `akaisds.log` of the second tester run)**: shift keygroups k+1..N-1 down with KDATA (each slot keeping its own `NXTKG`), DELK of
  the LAST keygroup, then PDATA `GROUPS=N-1`. Tried on the first, a middle and the last keygroup of 10- and 12-keygroup programs: the shifting
  KDATAs and the DELK were accepted ("Sampler confirmed: OK"), `GROUPS` read back UNCHANGED (10 -> 10, 12 -> 12), and the PDATA with the
  smaller `GROUPS` was rejected with the SAME error 01 - **even though `FIRSTKG` did not move** (program 0: bytes 1-2 `96 00` before and after),
  which REFUTES the "FIRSTKG must equal the program's own address + 150" hypothesis from Flow 1. After the DELK, reading keygroup N-1 returns a
  block with identifier 3 (a SAMPLE header): the DELK DID remove the keygroup and closed the memory up - the sampler just never decrements `GROUPS`,
  and refuses a PDATA that does. The program is left with a duplicated/junk last keygroup and `GROUPS` one too high (needs reloading - the tester's
  "filled the last keygroup with junk"); other loaded programs were untouched. The editor shows the "only partly applied" dialog and blocks further
  deletes for the session.
- **What the Flow 2 log shows about DELK itself** (`akaisds.log`, program 0 at address 0, 10 keygroups, keygroup i at 150*(i+1)): BEFORE the
  delete keygroup 8's `NXTKG` was 0x05dc (-> keygroup 9) and keygroup 9's own `NXTKG` was 0x0672 (1650 = the end of the program's 11 blocks);
  AFTER DELK(9) keygroup 8's `NXTKG` was 0x0672. So **DELK is a plain linked-list unlink (`prev.NXTKG = victim.NXTKG`), nothing more**: no memory
  compaction (the dead block's 150 bytes stay as a hole), no `GROUPS` update, and the new last keygroup now points at 1650 - a sample header -
  which is exactly why reading keygroup N-1 returns "block identifier is 3". Flow 1's DELK of keygroup 0 was the same unlink (`FIRSTKG` +150).
  **Working hypothesis (consistent with BOTH logs, unproven)**: the sampler accepts a PDATA only if its `GROUPS` matches the program's MEMORY
  EXTENT (every PDATA that ever worked - creates, editor writes, `GROUPS+1` after a KDATA append - did; extent here is still 11 blocks), so
  `GROUPS=N-1` is refused while the hole exists and `GROUPS=N` leaves a chain of N-1: **no sequence of DELK + PDATA can reach a consistent state**.
  Contrast: on an S3000XL, s3ked measured DELK on a 22-keygroup program as `GROUPS` 22 -> 21 with the rest shifting down (its
  `RESOLUTION_NOTES`/`TODO.md`, github.com/lentferj/s3ked) - so the S3000 firmware does the bookkeeping the S1000's DELK doesn't. The S1000 spec
  (lakai.sourceforge.net/docs/s1000_sysex.html, the only public protocol text; the S1000 V2.0 manual has no SysEx chapter) says nothing about DELK's
  effects beyond "If the argument ... exceeds the maximum, an error message will be given", and no other open-source S1000 editor was found
  (Lakai = Linux data-exchange tools; akaitools/akaiutil work on disk images, not over MIDI) - there is nothing to copy.
- **Only idea that could work, UNTESTED and risky**: don't DELK at all - REBUILD the program: create a new program (the create/duplicate flow:
  PDATA + KDATA appends with `GROUPS+1`, measured to work) with the N-1 wanted keygroups under a temporary name, DELP the original and put the new
  one back under the old name/position (program order, `PRGNUM` and the name-clash-deletes rule make that delicate). Needs a tester with a
  throwaway program and a full log; also worth asking their OS version (the S1000 and S1100 differ, and DELK may differ between OS versions).
- Duplicate Program/Keygroup still send the template's stale `NXTKG` terminator (they never failed in the logs); rewriting it to
  `FIRSTKG + 150*(n+1)` is a held-back hypothesis, not implemented.
