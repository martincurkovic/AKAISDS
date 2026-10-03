# Akai S1000 editor - real-hardware test plan

The S1000 Program Editor was written from Akai's documentation with no S1000 to
test against (see AGENTS.md, "Akai S1000 support"). This is the plan for the
first person who has one. It runs from safest to riskiest - **stop at the first
step that misbehaves and send the log** rather than pressing on.

## Before you start

1. **Save everything on the sampler to disk** (floppy/SCSI). Every edit rewrites
   a whole program, keygroup or sample header block on the machine.
2. In AKAISDS: **Settings > Sampler Type > Akai S1000** (the default is
   S2000/S3000), pick your MIDI ports, press OK. Then open the Program Editor
   (Window menu / Ctrl+E). A one-time "experimental" warning appears.
3. Use a **scratch program** for anything that writes. Load or build a throwaway
   one - not your only copy of anything.

## A. Read-only (nothing is written)

- [ ] The Programs list shows your program names; the Samples list shows your
      sample names (with durations once loaded).
- [ ] Click a program: its **keygroups appear** (ranges shown as e.g. C0 - B2).
      *(This was the original bug: only names loaded and keygroups were missing.)*
- [ ] Click each keygroup: the Zone sample names, velocity ranges, envelopes and
      Controllers cards fill in with plausible values (compare against the
      sampler's own screen).
- [ ] Click a sample on the Samples tab: loop markers/root note/tune look right.
- [ ] Hit **Refresh**: nothing changes and nothing errors.
- [ ] The Multi tab is absent, and the S3000-only cards (modulation matrix,
      portamento, LFO2) are hidden. Envelope 2 is an ADSR.

## B. Single-field edits on the scratch program

For each, change one thing, then **check the sampler's own screen** (and ideally
play it), then change it back.

- [ ] Program: Loud, Pan, Tune, Bend range (0-12), Polyphony, MIDI channel,
      Note priority, LFO1 rate/depth/delay.
- [ ] Keygroup: Cutoff, Key Filter Track, Envelope 1 and Envelope 2 ADSR.
- [ ] Keygroup note range - **expect nothing to be lost** after Refresh.
- [ ] Zone: sample choice, velocity range, tune, loop type, keytrack.
- [ ] **Controllers cards** (the S1000's fixed controller routing). Set a
      **negative** value (e.g. -20) on one and confirm the sampler shows -20
      (this is where a sign bug would show up). Spot-check Key > Loudness,
      Envelope 2 > Filter, Velocity > Attack.
- [ ] Pan LFO rate/depth/delay.
- [ ] After every edit, press **Refresh** and confirm the value stuck.

## C. The two things most likely to go wrong

- [ ] **Is anything deleted by an edit?** After a handful of edits to a program,
      check the program list and its keygroup count on the sampler itself. The
      S1000 spec says a program written with a name matching *another* program
      deletes that other one; the editor assumes re-sending a program under its
      *own* unchanged name is a plain in-place replace. That assumption is
      untested - if a program or keygroup disappears or the list reorders, stop
      and report it.
- [ ] **Rename** a scratch program and a scratch sample to a free name. Try
      renaming one to a name another program/sample already has - the editor
      should refuse.

## D. Structural edits (scratch program only)

- [ ] **Duplicate Program**, then check keygroup count/contents on the sampler.
- [ ] **Duplicate Keygroup**, then check the program's keygroup count.
- [ ] **Delete Keygroup**, **Delete Program** (note: the last remaining program
      can't be deleted).

## E. Sample editing (destructive - scratch sample only)

These send a replacement sample over MIDI and delete the original, then copy
header fields back - untested on an S1000.

- [ ] Loop point drag/type edits on the Samples tab; Refresh to confirm.
- [ ] Trim / Reverse / Fade / Normalise on a scratch sample.
- [ ] Slice Editor export of a scratch sample (and "create program").

## What to send back

- `akaisds.log` - macOS/Linux `~/.akaisds/akaisds.log`, Windows
  `C:\Users\<you>\.akaisds\akaisds.log`. Send it **straight after** a problem
  (it rotates at 8 MB). It records every block the S1000 sends, in hex - the
  real block sizes are what we most want to learn.
- Which step failed, what the sampler showed vs what the editor showed.
- Your sampler's OS version (the Hardware Test in Settings > Troubleshooting
  reports it) and your MIDI interface.
- On a crash: the OS crash report.

## Known noise (not bugs)

- The Dashboard status bar may flash "unexpected SDATA" / the log may show
  "unrecognised Akai message" while the editor is open - the editor's own
  replies are also seen by the Transfer Dashboard.
