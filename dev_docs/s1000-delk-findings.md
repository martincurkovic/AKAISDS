# S1000 memory layout and DELK - measured findings

Real-sampler tester logs, 2026-10-05 and 2026-10-06; condensed 2026-10-10. The conclusions that matter day to day are in `AGENTS.md`
("Akai S1000 support"): standalone Delete Keygroup is DISABLED and create-from-slices never deletes. This is the evidence.

## Memory layout

Programs, keygroups and sample headers are 150-byte blocks in ONE linked memory area. Bytes 1-2 of a program block (`FIRSTKG`) and of a keygroup
block (`NXTKG`) are ABSOLUTE addresses the sampler assigns (program 0 at 0 with 10 keygroups ends at 0x672; the 12 sample headers that follow end
at 0xd7a, where the next program starts). When a keygroup is appended the sampler repoints the PREVIOUS keygroup's `NXTKG`, but the last one keeps
whatever we sent - a clone's last keygroup carries its template's stale terminator.

## DELK is a plain linked-list unlink

From the second tester log (program 0 at address 0, 10 keygroups, keygroup i at `150*(i+1)`): before the delete keygroup 8's `NXTKG` was `0x05dc`
(-> keygroup 9) and keygroup 9's was `0x0672` (the end of the program's 11 blocks); after DELK(9) keygroup 8's `NXTKG` was `0x0672`. So
`prev.NXTKG = victim.NXTKG` and nothing more: no memory compaction (the dead block's 150 bytes stay as a hole), no `GROUPS` update, and the new last
keygroup now points at a sample header - which is why reading keygroup N-1 returns "block identifier is 3, expected 2". Flow 1's DELK of
keygroup 0 was the same unlink (`FIRSTKG` + 150).

## The two failed flows

- **Flow 1:** DELK of the chosen keygroup, then a PDATA rewriting `GROUPS`. DELK(program 2, keygroup 0) advanced `FIRSTKG` by 150, left `GROUPS`
  unchanged and left the stale `NXTKG`; the PDATA with `GROUPS=9` got `REPLY` error `47 00 16 48 01`; the program kept 9 reachable keygroups but `GROUPS=10`.
- **Flow 2:** shift keygroups k+1..N-1 down with KDATA (each slot keeping its own `NXTKG`), DELK the LAST keygroup, then PDATA `GROUPS=N-1`. Tried on
  the first, a middle and the last keygroup of 10- and 12-keygroup programs: the KDATAs and DELK were accepted, `GROUPS` read back UNCHANGED, and the
  PDATA with the smaller `GROUPS` was rejected with the SAME error 01 even though `FIRSTKG` did not move - which refutes the "FIRSTKG must equal the
  program's own address + 150" hypothesis from Flow 1. The program was left with a junk last keygroup and `GROUPS` one too high; other programs untouched.

## Working hypothesis (consistent with both logs, unproven)

The sampler accepts a PDATA only if its `GROUPS` matches the program's MEMORY EXTENT (every PDATA that ever worked did), so `GROUPS=N-1` is refused
while the hole exists and `GROUPS=N` leaves a chain of N-1: **no sequence of DELK + PDATA reaches a consistent state.** A stale `GROUPS` also made
the next "add keygroup" link into ANOTHER program (program 0 grew a keygroup 6 = the new clone, its 7-9 orphaned) - that is what "Create new program with
slices" did with a multi-keygroup template (clone N, DELK N-1), hence `first_keygroup_only=True`. Contrast: on an S3000XL s3ked measured DELK on a
22-keygroup program as `GROUPS` 22 -> 21 with the rest shifting down, so S3000 firmware does the bookkeeping the S1000's DELK doesn't. The S1000 spec
(lakai.sourceforge.net/docs/s1000_sysex.html, the only public protocol text) says nothing about DELK's effects, and no open-source S1000 MIDI editor
was found to copy.

## Untested ideas

- **REBUILD** (`KEYGROUP_DELETE_BY_REBUILD`, off): create a new program with the N-1 wanted keygroups under a temporary name (the create flow
  works), DELP the original and rename the copy. Needs a tester with a throwaway program and a full log; also ask their OS version (S1000 and S1100
  may differ).
- Duplicate Program/Keygroup still send the template's stale `NXTKG` (never failed in the logs); rewriting it to `FIRSTKG + 150*(n+1)` is a
  held-back hypothesis.
