# Sample editing on the Akai S900/S950: what is possible

Written 2026-10-20 from the AKAISDS code, `dev_docs/s950-support-plan.md`, `tests/s950_test_plan.md` and the notes from s950tools.
**Nothing here has run on a real S900/S950.** Every "possible" below means "the protocol and our code allow it", not "tested".
Items marked *(unverified)* need a tester with a unit before anyone relies on them.

## The one constraint that shapes everything

There is **no delete opcode** and a **sample can't be overwritten in place**:

- Uploads always go to an EMPTY slot. Sending to an occupied slot is reported to cause a NAK storm (s950tools, our notes). *(unverified)*
- No delete or clear command is known (`AGENTS.md`: don't invent one). Only the unit's panel can free a slot.
- So, as on the Yamaha A4000, an edited sample can only ever arrive as a **new sample**. The original stays until the user deletes it on the unit.

Rename is the open question. We already write a sample's name by a read-modify-write of its 120-byte parameter block (SPRM) right after
an upload, so renaming an EXISTING sample very probably works the same way. It has only been designed, never tried on an occupied slot. *(unverified)*

## What we can do today (code that exists)

| Capability | Status |
|---|---|
| List samples (catalog), read each sample's parameters | built, untested on hardware |
| Receive a sample's audio into a WAV (one SysEx, up to ~1 MB, we stream a 4-byte ACK every 50 ms) | built, untested |
| Send a WAV to the first free slot, then name it (SPRM write) | built, untested |
| Write **replay mode** (one-shot / loop / alternating) and **direction** (normal / reverse) via SPRM, with backup, read-back and compare | built, untested |
| Show start / end / loop markers (read-only) | built |

The Samples tab already loads audio into a waveform view, so a working copy has somewhere to live.

## What editing could look like

All of these change AUDIO on the computer and then send the result as a new sample, so none of them depends on unknown SPRM semantics.

| Edit | Feasible? | Notes |
|---|---|---|
| Trim, Reverse, Fade in/out, Normalise | Yes | pure maths in `core/sample_editing.py`, already shared by the Akai and Yamaha editors |
| Filter, Stretch / Pitch | Yes | same shared engines; Stretch / Pitch is slow in pure Python (about 0.7 s per 10 s of mono audio) |
| Reverb | Yes | mono only, which suits the S950; tail capped at 5 s |
| Detect root note / tune | Not useful | the S950 sample parameters have pitch, but writing it is untested |
| Slice a sample into several new samples | Yes | each slice a new sample; **creating a program from the slices is not built** (no create-program path) |
| Change start / end / loop points on the unit | **No, not yet** | SPRM start/end/loop semantics are inferred, and the two reference readings disagree on where a loop sits (end-anchored here, start-anchored in s950tools). Needs a listening test on a unit. |
| Change replay mode / direction | Yes, already | the only SPRM fields we write; direction may need the sample re-selected (ENT) on the unit before it takes effect |
| Delete the original | **No** | user deletes on the panel |
| Replace in place | **No** | see above |

### "Replace" without delete: the rename swap *(unverified, needs rename proven first)*

Programs refer to samples BY NAME and the unit doesn't enforce unique names, so two samples with one name make every zone using it ambiguous.
A swap would avoid that window: send the edit under a temporary name, rename the original to a backup name (`NAME-OLD`), rename the
edit to the original name, then tell the user the backup can be deleted on the panel. The programs now play the edit, and the old audio is still
there if the edit is wrong. It needs a proven SPRM rename on an occupied slot and costs the full size of the original in memory until it's deleted.

## Limits every edit has to respect

- **12-bit mono only.** Stereo is averaged to mono on send. Edits can be done at 16-bit on the working copy, but the unit stores 12-bit: tell the user.
- **Length:** 200 to 475,020 words. 475,020 words is about 712 KB at 12 bits, 21.5 s at 22.05 kHz, 9.9 s at 48 kHz. Shorter is padded with silence; longer is refused, never truncated.
- **Rate:** the dump header allows 2 kHz and up (our clamp). The S950's playable range is 7.5-48 kHz (the S900's is lower). Stretch / Pitch changes length, so check the limit again after it.
- **Names:** 10 characters, uppercase, unique. The existing `unique_name` adds `-2`, `-3`. An `EDIT` suffix leaves five characters of the original, so a short suffix is better.
- **Memory and slots:** total sample memory is not in our sources (commonly quoted as 750 KB standard, up to 2.25 MB expanded; verify). The code's `SLOT_COUNT` is 100, a numbering limit, not proof the unit holds that many. **Because nothing can be deleted over MIDI, every edit permanently uses memory and a slot.** The UI should show free memory if the unit can report it (no memory bar exists for the S950 today) and warn before sending.
- **One operation at a time, and a MIDI input is required.** Slot choice, readiness and naming all read replies. Large messages are one SysEx; a backend that fragments long SysEx would lose a receive.

## Suggested shape if we build it (modelled on the Yamaha editor)

- Working copy per sample on the Samples tab, with an Edit Sample card, Undo and Revert. Edits send nothing.
- Only **Send as New Sample** touches the unit, through the existing `send_file_queue` and SPRM naming. No "Send to Sampler", no replace.
- A first-send experimental warning and a `.syx` backup of the source sample's SPRM, as the replay-mode write already does.
- A free-memory / free-slot check before sending, and a clear "the original is still on the unit" message afterwards.
- Slice export: new samples only, no program creation.

## Order of work, and what a tester must confirm first

1. **Tester (nothing new built):** `tests/s950_test_plan.md` sections A-C: receive a sample, send a sample, check the name landed, check 12-bit audio sounds right. If receive or send fails, editing is moot.
2. **Tester:** rename an existing sample with an SPRM write (new). Overwrite behaviour of an occupied slot (expect a NAK storm; confirm it does no harm).
3. **Then build:** Trim / Reverse / Fade / Normalise + Send as New, marked experimental.
4. Filter, Stretch / Pitch, Reverb, slicing to samples.
5. Rename swap, only if step 2 proves rename.
6. Start / end / loop editing, only after the loop listening test settles where a loop sits.

## Risk summary

- Writes only go to an empty slot and a new name, so an edit can't corrupt an existing sample, but it **can fill the unit** and it can leave a half-written slot if a send fails (`_partial_note` style message needed).
- The unproven parts are the existing receive, send and naming paths. Editing adds UI on top of them, not new protocol, apart from the rename swap.
- Recommendation: don't ship in 1.4.0; build after a tester confirms steps 1-2.
