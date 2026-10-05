# Akai S900/S950 support - real-hardware test plan

S900/S950 support (Dashboard transfers + the program editor) was written from
s950tools' notes with **no S900/S950 to test against** (see AGENTS.md, "Akai S900/S950
support"). This is the plan for the first person who has one. It runs from safest to
riskiest - **stop at the first step that misbehaves and send the log**
(`~/.akaisds/akaisds.log`, every request is in it as hex) rather than pressing on.

## Before you start

1. **Use MIDI in both directions.** The S900/S950 answers on your MIDI input; with only an
   output selected, Refresh/Receive/Send/the viewer all refuse (by design - no open-loop send).
2. In AKAISDS: **Settings > Sampler Type > Akai S900/S950**, pick the MIDI input AND output,
   press OK. The SysEx channel in Settings should match the sampler's MIDI channel setting.
3. On the sampler, enable MIDI/RS-232 system-exclusive reception if it has such a setting.
4. Do anything that **writes** (Send, Rename, and every program edit) to a scratch sample or
   program you can lose. Reading never writes.
5. **Save everything on the sampler to disk (floppy) first**, as for any experimental write.
   AKAISDS also backs up each program it overwrites (see section D), but that backup is itself
   untested.

## A. Connection and read-only (nothing is written)

- [ ] Settings > **Run Hardware Test** gets a catalog reply (an RCAT round trip).
- [ ] **Refresh**: the hardware list shows your sample names against their real slot
      numbers (slots can be sparse - 3 and 40 is fine).
- [ ] Click a sample's info (Edit): name, rate and length look right. Root note and detune
      show as unavailable on purpose.
- [ ] **Receive one small sample** (a second or less) to a WAV: it plays back correctly and
      at the right speed. A timeout here on a *small* sample may mean the MIDI backend splits
      long SysEx messages - say so in the report.

## B. Program editor - reading (nothing is written)

Open it with the Dashboard's **Open Editor** button (or Ctrl+E). Until you press a Write button,
nothing here writes.

- [ ] The Programs list matches the sampler's own program list (names and order are
      guesses - note any difference).
- [ ] Click a program: the Program card shows its name, MIDI program number and keygroup count.
- [ ] The keygroup table and the range bar match the sampler's own key ranges. Note names use
      the S3000XL convention (middle C = C3) - say which octave your sampler's panel uses.
- [ ] For each keygroup, the soft/loud sample names are right. A name flagged
      "(not on sampler)" really isn't in the sample list (programs find samples by NAME).
- [ ] Envelope values (attack/decay/sustain/release, amplitude and filter), velocity switch,
      filter key tracking, LFO and mod wheel/aftertouch values match the sampler's screen.
      **Any value that reads as nonsense is more likely a wrong byte offset in
      `core/s950_program.py` than a fault in the sampler** - send the log.
- [ ] A program with MANY keygroups (up to 31) reads and lists them all.
- [ ] Refresh keeps the selected program and re-reads it.
- [ ] Switching to the Dashboard (Ctrl+T) and back works; closing is refused while a read is
      in flight.

## C. Sample writes (SCRATCH sample slot only)

- [ ] **Send** a short mono WAV (22.05 kHz or 44.1 kHz). Expect the one-time experimental
      warning, then a new sample in the first empty slot (never an occupied one), named after
      the file (uppercased, 10 characters).
- [ ] It plays correctly on the sampler. Note the rate it shows against what you sent.
- [ ] **Rename** that sample from the Dashboard. Check it on the sampler's screen.
      *Renaming does not update programs that use the sample - a keygroup pointing at the old
      name goes silent. Rename only samples no program uses.*
- [ ] Send a second file with the same name: it becomes `NAME-2`, not a clash.

## D. Program writes (SCRATCH program only)

Use a throwaway program - make one on the sampler first (the editor can't create or delete
programs). **Each step must pass before the next; stop at the first surprise** and send the log.
Every write: (1) re-reads the program, (2) saves that original as a `.syx` in
`~/.akaisds/s950_backups/`, (3) sends only the fields you changed, (4) reads it back and compares.

- [ ] **D1 - the no-op.** Hardware menu > *Write Program Back Unchanged (test)*. Expect
      "written and verified" and the sampler's program to be exactly as before (screen and
      sound). *This proves the write path itself changes nothing. If D1 doesn't verify, do not
      continue.*
- [ ] **D2 - one value.** Change one keygroup's Attack by a few steps, press **Write to Sampler**
      (a one-time warning appears first). Expect "written and verified". **Check the sampler's own
      screen AND play the keygroup** - the read-back can pass while the sound hasn't changed (see
      guess 5 below). Note whether you needed to re-select the program on the sampler (ENT) to
      hear it.
- [ ] **D3 - restore.** Press **Restore Previous**: the value returns and verifies.
- [ ] **D4 - more fields**, one card at a time, checking the sampler each time: filter envelope,
      velocity switch, key range (lowest/highest key), soft/loud sample transpose/filter/loudness,
      LFO and mod wheel/aftertouch, pitch warp, output, MIDI channel offset.
- [ ] **D5 - sample assignment.** Pick a different sample from a keygroup's Sample list, write,
      play it.
- [ ] **D6 - program header.** MIDI program number, "respond to program change", key tilt,
      positional crossfade.
- [ ] **D7 - rename the program** (uppercase, up to 10 characters). The program list should show
      the new name after the write. Also try a name with punctuation or a digit, and note what
      the sampler shows.
- [ ] **D8 - extremes.** Only now: the ends of a range (0 and 99, -50 and +50). Note any value the
      sampler shows differently from what you set (the editor reports a mismatch if it reads back
      differently).
- [ ] **D9 - a many-keygroup program** (up to 31): change one value in the last keygroup. The
      message is ~4 KB; expect a longer wait (about 1.4 s of wire time).
- [ ] **D10 - the backup.** Open a `.syx` from `~/.akaisds/s950_backups/` in a SysEx librarian and
      send it back to the sampler: the program should return to the state it had before that
      write. *(This relies on the same assumption as every program write - see guess 1.)*

**Not offered, on purpose:** creating a program, deleting one (there is no MIDI delete),
adding/removing keygroups, and editing the keygroup "Options" bits (read-only - their meaning is
inferred).

## What is a guess

Nothing in this feature has been run on a unit. Everything below is inferred; this is the list the
first report should confirm or refute. Where it says **(s950tools)**, that project's notes or
behaviour is the only evidence - they describe a real unit but were not reproduced here.

### The write mechanism
1. **A PRGM write to an occupied slot replaces that program in place.** (s950tools' GUI does
   exactly this when a user edits a program, debounced live - so it's the most-exercised path - but
   no note records the exact behaviour, and its UI comments hedge: "the device may NAK on slot
   collisions".) A NAK during a write is detected and reported.
2. **The unit sends no reply to a PRGM write.** (s950tools.) We therefore can't tell a good write
   from a lost one except by reading the program back - that is what "verified" means.
3. **Wire timing**: the wait after sending is the wire time at 3125 bytes/s plus 10%, then a 500 ms
   NAK window, then a 200 ms settle before the read-back. All copied from s950tools' *sample*
   handling; the PRGM-specific settle time is untested. If a verify fails with "no reply" or shows
   the OLD values, a longer settle is the first thing to try (`sprm_settle_ms` in
   `S950Transfers`).
4. **Read-back = what is stored.** s950tools documents an "active edit buffer" the unit refreshes
   only on re-select, **for sample parameters** (the Reverse flag). Whether programs behave the same
   is unknown. If they do, a write can verify (stored value right) while the sound doesn't change
   until the program is re-selected on the sampler.
5. **Whether it is safe to write the program that is currently selected or playing.**

### The data
6. **Keygroup byte offsets** are only as good as s950tools' copy of an unlicensed C++ project; the
   *program header* offsets are pinned by two real captures. A wrong keygroup offset means a field
   reads (and so writes) the wrong byte. A write only touches fields you changed, but a wrongly
   mapped field you change WILL write to the wrong place - which is why D2/D4 go one value at a time
   and check the sampler each time.
7. **Limits**: every range in `core/s950_params.py` is s950tools' documented range (0-99 for most
   knobs, +-50 for the signed ones, 24-127 for key range, 0-128 for the velocity switch, 0-15 for the
   MIDI offset, output 0-9 or "all"), none verified. **The +-24 semitone transpose limit is ours**,
   not the hardware's. A real unit may accept more or clamp earlier; the editor reports a read-back
   that differs.
8. **Meanings**: transpose in 1/16 semitone; loudness 0.375 dB per unit; "velocity switch" = the loud
   sample plays above that velocity and 128 means off; filter-by-velocity, pitch-warp and the other
   routings are as s950tools names them; output 8/9 = left/right groups. The keygroup "Options"
   bits (shown read-only) are named from dxzl's struct and are the least certain of all.
9. **Names**: program and sample names are shown/written uppercase, up to 10 plain ASCII
   characters. Whether the unit accepts lowercase or punctuation is unknown. An *edited* name is
   space-padded to 10 characters (a guess that the unit accepts that); an *unedited* name keeps its
   exact bytes. **Two programs sharing a name**: the editor refuses it, because what the unit does
   is unknown.
10. **Programs find samples by name**, with the match assumed to be exact. The editor flags a name
    missing from the catalog case-insensitively, which could hide a case-sensitive mismatch.
11. **Unmodelled bytes** (reserved/undefined areas) are written back exactly as read. That is safe
    unless the unit regenerates them, in which case the read-back shows them differing (logged as a
    warning, not a failure).
12. **Messages up to ~4.4 KB** (31 keygroups) are assumed to be delivered whole by the MIDI backend
    both ways (the sample-receive risk, but 200x smaller).

### The safety net
13. **The backup `.syx` restores a program.** It is the unit's own PRGM reply, byte for byte, so it
    is only as restorable as guess 1.
14. **"Restore Previous"** writes the earlier values back through the same path; it is exactly as
    trustworthy as a normal write, no more.

## What to send back

`~/.akaisds/akaisds.log`, the sampler model and OS version, and for any wrong value: what the
sampler's own screen shows vs. what AKAISDS shows. For a write that didn't verify, also the
`.syx` backup of that program from `~/.akaisds/s950_backups/` and the program's name/slot. Wire
captures of an RCAT reply, an SPRM and a keygroup-bearing PRGM (the log has them in hex) would let
us pin the unverified offsets.
