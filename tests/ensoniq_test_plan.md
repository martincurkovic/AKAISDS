# Ensoniq EPS / EPS-16 PLUS / ASR-10 support - real-hardware test plan

Listing, receiving and sending samples for the Ensoniq EPS family was written from Ensoniq's own External Command Specification with **no
Ensoniq to test against** (see AGENTS.md, "Ensoniq EPS/EPS-16+/ASR-10", and `dev_docs/ensoniq-support-plan.md`). This is the plan for the
first person who has one. **Sections A and B only READ the sampler - nothing is written, deleted or saved on it. Section C WRITES**: it
creates new instruments (never overwrites, never deletes, never saves to disk), so do it last, with a unit whose contents you can afford
to lose or have saved. Stop at the first step that misbehaves and send the log (`~/.akaisds/akaisds.log`; every request is in it as hex).

Not covered: the ASR-X (a different dialect with its own Sampler Type: see `tests/asrx_test_plan.md`), Mirage, SQ/VFX/SD-1 and other Ensoniq synths.

## Before you start

1. **Use MIDI in both directions.** The unit answers on your MIDI input; with only an output selected, Refresh and Receive refuse.
2. In AKAISDS: **Settings > Sampler Type > Ensoniq EPS/ASR (untested, experimental)**, pick the MIDI input AND output.
3. **Device ID:** set it to the unit's **base MIDI channel minus 1** (base channel 1 = 0). That is what the ASR-10 spec says goes in that
   byte; the ASR-X manual instead has a separate "SysEx Device ID" - if 0 gets no answer, try the unit's own Device ID (0-15 only). Say
   which one worked.
4. On the unit: **System/MIDI > MIDI SysEx Enable = on** (ASR-10), and if you have a SCSI card, try with the SCSI cable **unplugged** - the
   spec says a unit with SCSI attached expects wave data over SCSI.
5. Close any DAW or MIDI monitor that shares the interface (it can echo the unit's replies back at it).
6. Load one or two small instruments into the unit (a one-shot a second or less is ideal for the first receive). **Nothing is changed
   on the unit by any step below.**

## A. Connection

- [ ] Settings > **Run Hardware Test** says "Ensoniq EPS/ASR SysEx answered", whether instrument 1 is loaded ("isn't loaded" is a
      normal answer) and, if the unit gives one, its MIDI identity (manufacturer, family, model, version - **please copy this line into your
      report**; "No MIDI identity reply" is common). No answer at all: check the points above, then send the log - it records every byte sent and received.
- [ ] **Refresh**: the hardware list shows your wavesample names. A wavesample name used twice gets "(instrument.layer.wavesample)" after it.
      The unit has no list command, so this takes a few seconds (it asks about every instrument and layer): note how long it took.
- [ ] The **memory bar** under the list shows a percentage. It is free blocks plus the size of every instrument, so it should roughly match the
      unit's own free-memory figure (System/Memory screens). Say how far off it is, or whether it stays "memory: -" (the unit didn't answer).
- [ ] Do the names match the unit's? Are any wavesamples missing, or any shown that aren't on the unit? (The list comes from each layer's
      key map; a wavesample no key points at would be missed.)

## B. Receive

- [ ] **Receive one short, mono wavesample** to a WAV. It plays back correctly and **at the right speed**.
- [ ] Note the speed carefully: the unit's rate code is a multiple of 1.6 microseconds, so the WAV's rate is a code's exact rate (31,250 Hz
      for code 20, 44,643 Hz for 14). Compare against the unit's own playback.
- [ ] Is the **last word** missing or does the WAV end with a click? (The unit's end offset may be inclusive or exclusive; the log line
      `data range end looks INCLUSIVE` says what the app saw.)
- [ ] A longer sample (5-10 s): it arrives in full. It takes minutes (about 1000 words per second over MIDI), the progress bar moves, and
      **Cancel** stops it.
- [ ] A wavesample that is a **copy** of another one says so and is skipped.
- [ ] A **stereo** instrument shows each side as its own wavesample (two layers).
- [ ] The unit itself keeps working during and after a transfer (its sequencer must be stopped; "sequencer must be stopped" and "disk
      access in progress" are reported by name).

## C. Send (this WRITES: a new instrument per file)

Back up the unit's RAM to disk first. **A failed send can leave a half-built instrument that only the unit's front panel can delete.**
Each file becomes a NEW instrument in the first free slot (of 8), with one layer per channel and one wavesample per layer. Nothing that
is already on the unit is changed, and the instrument keeps whatever default name the unit gives it (the wavesample carries the name:
rewriting an instrument means writing RAM pointers, which this app never does).

- [ ] Free a slot on the unit if all 8 are in use (the app refuses: "All 8 instrument slots ... are in use").
- [ ] Queue **one short mono WAV at 31,250 Hz or 52,083 Hz** (a rate the unit can represent exactly) and press **Send**. A one-time
      warning appears. Watch the unit's display while it runs.
- [ ] The unit shows a new instrument containing a wavesample with the file's name. It **plays the right audio at the right pitch**,
      one-shot, from the start to (almost) the end. Say whether the sound ends a hair early (the end offset is written one word short on
      purpose: the spec doesn't say whether it is inclusive).
- [ ] The log says what the new wavesample looked like before the upload (`new wavesample before the upload: start=.. end=..`) and after.
      **This is the most useful thing in the log**: how the unit lets a wavesample grow beyond its placeholder is the least certain part.
      If the send stops at the first audio chunk, send the log: the status code the unit gave is in it.
- [ ] The Dashboard says whether the wavesample read back the same ("doesn't read back the same" is a failure to report).
- [ ] A **44,100 Hz** file: it is resampled to 44,643 Hz (the nearest rate code the formula allows). Does it play at the right pitch? Which
      sample rate does the unit say the wavesample has?
- [ ] A **stereo** file: one instrument, two layers ("name L", "name R"), left hard left, right hard right (the Pan values are guesses).
- [ ] A **longer** sample (5-10 s) and one close to the unit's free memory: the transfer takes minutes (about 1000 words a second),
      **Cancel** works and says which instrument may be incomplete, and the unit isn't wedged afterwards.
- [ ] Several files in one queue each get their own instrument.

## What to send back

The log straight after the attempt, the unit's model and **OS version**, the Device ID and base channel you used, whether SysEx is enabled,
whether a SCSI card or drive is attached, and anything on the unit's display. The lines worth reading start with `EnsoniqTransfers:`;
the first failure is usually the one that matters.
