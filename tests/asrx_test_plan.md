# Ensoniq ASR-X support - real-hardware test plan

Listing, receiving and sending samples for the ASR-X was written from a **static reading of the v2.67 firmware** (`dev_docs/asrx-rom-findings.md`) with **no ASR-X to test
against**. This is the plan for the first person who has one. **Sections A and B only READ the unit. Section C WRITES**: it adds new samples (never overwrites, never
deletes), so do it last with a unit whose contents you can afford to lose. **Stop at the first step that misbehaves and send the log** (`~/.akaisds/akaisds.log`): the app
writes a line for everything it sends and receives, and when something fails it also writes the last messages before the failure.

## Before you start

1. Use MIDI in **both** directions. The unit answers on your MIDI input; with only an output selected, Refresh/Receive/Send refuse.
2. AKAISDS: **Settings > Sampler Type > Ensoniq ASR-X (untested, experimental)**, pick the MIDI input AND output.
3. **Device ID** in AKAISDS = the unit's **SysEx Device ID** (System/MIDI page, default 0). `127` means "all devices" and also works.
4. On the unit: turn **SysEx Recv on** (System/MIDI page).
5. Close any DAW or MIDI monitor that shares the interface (it can echo the unit's replies back at it).
6. Put one short sample (a second or less) in the unit for the first receive.
7. Optional, for a very detailed log: start AKAISDS with the environment variable `AKAISDS_ASRX_TRACE=1`. It logs every message in full (big log, only for a debugging session).

## A. Connection (reads only)

- [ ] Settings > **Run Hardware Test** says "Ensoniq ASR-X SysEx answered" and whether sample 0 has a name or is empty (both are fine). If the unit gives a MIDI identity, **copy that
      line into your report**. No answer: check the points above, then send the log.
- [ ] **Refresh** lists your sample names. Does it match the unit's sample list (all there, none extra)? How long did it take? (A few seconds is normal: one question per slot.)
- [ ] The memory bar says it is unknown. That is expected.

## B. Receive (reads only)

- [ ] **Receive one short mono sample** to a WAV. It plays back correctly **and at the right speed**. (The speed comes from a value in the sample's header that was worked out from the
      firmware: please say if it is wrong, and by how much.)
- [ ] Look at the log lines `slot N header:` and `slot N decoded:` for that sample and put them in your report **together with what you know about the sample** (its real name, sample
      rate, length, root key, whether it loops). They are how the unknown header fields get confirmed.
- [ ] The log says `fetched ... via range reads`. If it says `whole-object download` or shows `falling back`, the unit did not answer the partial-read command: say so.
- [ ] A longer sample (5-10 s): it arrives in full. It takes minutes (MIDI speed, ~2.5 KB/s of audio), the progress bar moves and **Cancel** stops it.
- [ ] A sample with a loop: the WAV has no loop (that is expected); the log says `loop ... not saved in the WAV`.
- [ ] A stereo pair on the unit arrives as two WAVs (left and right are separate samples there).
- [ ] The unit keeps working normally during and after a transfer.

## C. Send (this WRITES: a new sample per file)

**Save the unit's contents first.** Each file becomes a NEW sample in the first free slot; a stereo file becomes two ("NAME-L", "NAME-R"). Nothing already on the unit is changed. A failed
send can leave a half-written sample that only the unit's front panel can delete.

- [ ] Queue **one short mono WAV** (44.1 kHz, about a second) and press **Send**. A one-time warning appears. Watch the unit's display while it runs (it shows "RCV ..." text).
- [ ] The Dashboard says `Sent 1 file` with no "didn't read back the same". The unit lists a new sample with the file's name that **plays the right audio at the right pitch**.
      Say if it is a semitone/octave off, or too fast or slow (the rate-to-pitch rule is the most guessed part).
- [ ] The log lines `header sent:` and `header decoded:` (what was uploaded) and `verify slot N:` (what the unit holds afterwards) are the most useful lines. Please send them.
- [ ] The file's name shows correctly on the unit (16 characters, not truncated oddly).
- [ ] A **stereo** file: two samples, left and right.
- [ ] A **file at another rate** (22.05 kHz or 48 kHz): still plays at the right pitch.
- [ ] A **longer** sample (5-10 s): takes minutes (the app estimates and shows it), **Cancel** works and warns that the unit is still taking in what was sent, and the unit is not wedged afterwards.
- [ ] Several files in one queue each get their own slot.
- [ ] Is the sample fully usable on the unit (assign it to a sound, play it, loop it, save it to disk)? Anything odd (clicks at the end, a lost last piece, wrong length on its display)?

## What to send back

The log straight after the attempt, the unit's model and **OS version**, the Device ID you used, whether SysEx Recv is on, and anything shown on the unit's display. The lines worth
reading start with `AsrxTransfers:`; the first `FAILED` or `no answer` line is usually the one that matters.
