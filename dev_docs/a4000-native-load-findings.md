# Yamaha A4000: native sample loading (wave + sample bulk dumps) - measured findings

Measured 2026-10-06 on the owner's A4000 (device number 0, Bulk Protect OFF, RAM only, throwaway `T-...` objects), condensed 2026-10-10.
**Implemented in the app** (`core/yamaha_load.py`, `YamahaTransfers.send_file_queue`; see `AGENTS.md`). Tools: `tools/a4000_load_probe.py`
(stages `capture`, `same`, `rename`, `sample`, `invert`, `pair`, `synth`) and `tools/a4000_load_lab.py` (the library the experiments used).
Run them with the app closed. Later behaviour (the silent unit after a link) is in `a4000-editor-roadmap.md`.

A **load** = the wave message(s) of each channel (`yamaha_wave.build_wave_messages`) followed by one SP (`yamaha_sysex.build_bulk_dump`;
payload = a user-sample template's SP with the name, wave names, wave start/length/end, loop start/length/end and rate set), each message
sent after `wire time + 0.4 s`. The unit never sends anything back during or after a load ("MIDI Bulk Received" on the LCD only).

## How a load behaves

- A WD alone is silently dropped. A WD + the SP that names it creates the wave object(s) and the sample, in EITHER order (SP first works too).
  An SP naming a wave the unit lacks is rejected. The unit stages a wave and commits it when the SP arrives; the stage does not survive an
  unrelated load in between.
- A missing, short, duplicated or reordered wave message creates NOTHING and leaves no partial object - the only error is "the sampler did
  not accept it" (suspects: wave memory full, Bulk Protect on, wrong device number). No cleanup is ever needed.
- **Names.** A wave under an existing wave's name is overwritten IN PLACE, even if another sample uses it (that sample becomes inconsistent):
  waves are shared by NAME, so never reuse one (the app uses random `SMP nnnnnn`; names must be <= 16 chars and unique on the unit).
  A sample under an existing SAMPLE name is replaced in place and **all its parameters are reset to the SP we send**; assignments and the
  program's Easy Edit values are kept. The app never overwrites: a clash gets ` 2`. Duplicate objects with one name are never created.
  A wave nothing references any more is garbage-collected (stereo -> mono resend removed the right wave).
- **Lengths and rates:** frames 1, 4, 100, ... 60000 all load with no trimming (unlike SDS, which trims 4) and read back byte-exact; rates 1..65535
  are stored verbatim. For a mono sample set `sampling_frequency_r` equal to L. The unit rewrites a few SP bytes on load (ids, wave addresses,
  sizes - not parameters).
- **Speed:** ~440-590 frames/s per channel with `wire + 0.4 s` pacing (3000 frames 5.8 s, 60000 frames 102 s; stereo takes twice as long). The
  wire alone is ~620 frames/s. There is no flow control: drive the progress bar from bytes sent.
- **After a load** the unit answers an identity request 2-4 s after the last message. The first read after a load is sometimes unanswered:
  retry the object-list/SP/wave reads 2-3 times, 3 s apart, before calling a load failed. Sending needs a MIDI input to verify.

## Verification the app does

Wait for an identity answer, request the object list (retry), check the sample and each wave name are present, read the SP and compare wave
start/length/end, loop, rate and wave names with what was sent. A full audio read-back doubles the time and is optional (it passed whenever run).
The check must expect the CARRIED `wave_length` for an edit copy, not the audio's frame count (a past bug).

## Things to tell the user

Replacing a sample resets its parameters; wave names must be unique; the unit shows "MIDI Bulk Received" after each load; linking a sample
right after loading can leave the unit silent until OK is pressed (see the roadmap, "Session 6"); Bulk Protect must be OFF; stereo and long
samples take minutes. There is no delete over MIDI, so a test send can only be undone from the front panel or by a power cycle.

## Not measured

Minimum pacing (only `wire + 0.4 s` was used; try `gap=0.1`/`0.0` and check the read-back); odd names (16 characters, lower case, trailing
spaces, `/`, non-ASCII); whether a non-template key range/original key survives a fresh load; native vs SDS-loaded SP row by row; SP-first
with incomplete waves and how long a staged WD survives; stereo at large sizes and > 60000 frames; real wave-memory exhaustion (the unit shows
"Wave memory full" on the LCD only - from here it looks like "no object appeared"); a failed REPLACE of an existing name; whether a WAV `smpl`
chunk maps cleanly to loop mode 1/2 (the SP carries the loop addresses, so probably).
