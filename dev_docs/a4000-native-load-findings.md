# Yamaha A4000 - NATIVE sample loading (WD + SP bulk loads): measured findings

Measured 2026-10-06 on the user's A4000 (device number 0, PreSonus Studio 26, Bulk Protect OFF), RAM only, throwaway objects. The
hardware-test fork wrote this at the user's request when the session ended; the parent session's earlier probe results come first.
**Nothing here is in the app yet.** Tools: `tools/a4000_load_probe.py` (stages `capture`, `same`, `rename`, `sample`, `invert`, `pair`,
`synth`) and `tools/a4000_load_lab.py` (library used by the experiments below: `Lab`, `Lab2`, `quick`, `report`, `poll_identity`, `tone`).
Every experiment below was a tiny script of the form

```
import sys; sys.path.insert(0, "tools"); from a4000_load_lab import *
lab = Lab2(); report(lab, "label", "T-COL", [tone(3000, 22050, 220)], 22050); lab.close()
```
run with `QT_QPA_PLATFORM=offscreen uv run python <script>` (app closed). A "load" = the wave message(s) of each channel
(`core/yamaha_wave.build_wave_messages`) followed by the SP (`yamaha_sysex.build_bulk_dump`, payload = the template sample `MIDI 00102`'s
SP with name, wave names, wave start/length/end, loop start/length/end = (0, n, n, n, 0, n), loop mode 0, rate set), each message
sent after `wire time + 0.4 s` (`(len+2)*10/31250`).

## 0. Established before the fork (parent session)

* A WD sent ALONE is silently dropped (the unit shows "MIDI bulk received", no object appears, nothing is sent back). A WD + an SP that
  links it, sent back to back in EITHER order, creates the wave object(s) and the sample object. So the unit stages a wave and commits
  it when the SP arrives (see 3b: the stage does not survive an unrelated load in between).
* A WD under an EXISTING wave's own name replaces its audio.
* An SP naming a wave the unit does not have is rejected (no object).
* Built from scratch it works: mono 8000 frames @ 22050, stereo 12000 @ 44100 (left + right wave objects, right name at SP @80); the user
  confirmed both sound right on the unit.
* The unit never sends anything back during or after a load (0 messages in every test below).

## 1. Name collision (measured)

Setup: `T-COL` loaded (3000 frames mono), assigned to program 128 (slot 2), `filter_cutoff` 33, `pan` 17, `original_key_l` 64 written through
the session, program 128 Easy Edit `level_offset` 21 on its slot. Each step below re-sent a COMPLETE load under the name `T-COL`:

| resend | object count | result |
|---|---|---|
| (a) same name, same length (3000), new audio | 162 -> 162 | sample REPLACED IN PLACE. Wave audio = the new audio (read back identical). **SP parameters REPLACED, not merged**: filter_cutoff 33 -> 127, pan 17 -> 0, original_key 64 -> 60 (the values in our sent template). **Assignment KEPT**: sample still lists program 128, program 128 slot still `T-COL`, its Easy Edit `level_offset` still 21. |
| (b) shorter (1500) | 162 -> 162 | same: replaced, geometry = the new one (len/end 1500), wave read back 1500 and identical, assignment kept. |
| (c) longer (6000) | 162 -> 162 | same, wave 6000 identical, assignment kept. |
| (d) mono -> stereo | 162 -> 163 | added `T-COL-R`; sample now names L+R; `sampling_frequency_r` = 22050 (we set it); both waves identical; assignment kept. |
| (d') stereo -> mono | 163 -> 162 | **the now-unreferenced right wave `T-COL-R` was REMOVED by the unit (orphan garbage collection)**; SP right name empty; assignment kept. |
| new SAMPLE name `T-NEW` using the EXISTING wave name `T-COL-L` with a different length (2000) | 162 -> 163 (only `T-NEW`) | **`T-COL-L` was OVERWRITTEN in place (now 2000 frames)** - the old sample `T-COL`, which also uses it, still says 3000 frames: it is now inconsistent. Waves are shared by NAME across samples: never reuse a wave name that another sample owns. |
| same sample `T-COL`, new wave name `T-COL2-L` | 163 -> 164 | new wave added; the old `T-COL-L` stayed because `T-NEW` still references it (reference counted). Orphans are only removed when nothing references them (see (d')). |

Duplicate objects with the same name were NEVER created; the unit never refused a same-name resend. Because the SP params are replaced
wholesale, a "replace" load destroys the user's edits of that sample (filter, pan, key range, envelopes...) - warn, or read the old SP first
and carry the editable values over.

Evidence/commands: scripts `q1a..q1f` (scratchpad; same shape as the snippet above using `report(...)`, `Lab2.link/write/write_easy/prog`).

## 2. Lengths and rates (measured)

* Lengths that loaded and read back byte-exact (SP geometry exactly what we set; wave frames == sent frames, **no trimming** - unlike SDS,
  which trims 4): **1, 4, 100, 1000, 1500, 2000, 3000, 5000, 6000, 60000** frames. (40000 @ 44100 also loaded: SP len/end 40000, 68.4 s;
  its wave read-back timed out once, but it was then overwritten by the 60000 test and the 60000 wave read back identical in 101 s.)
* Send speed ~**440-590 frames/s per channel** with our `wire + 0.4 s` pacing (3000 frames 5.8 s; 6000 10.9 s; 40000 68.4 s; 60000 102.4 s; stereo
  3000x2 11.0 s, 2000x2 8.3 s). Stereo = twice the mono time. The wire (31250 baud, 2 bytes per nibbled word... ) itself is ~620 frames/s; the
  0.4 s per ~4 KB message costs the difference.
* Sampling rates 1000, 5512, 8000, 11025, 22050, 44100, 48000, **65535**: all accepted and read back VERBATIM in `sampling_frequency_l`
  (no rounding to 44100/22050...). Note: for a MONO sample `sampling_frequency_r` stays whatever the template had (44100) - set it equal
  to L (the app should), or it is meaningless but visible.
* Loop/geometry fields: exactly what we sent (wave start 0, length = end = n, loop start = end = n, loop length 0, loop end = n, mode 0) in every
  case. The unit did not alter them.
* Still unmeasured: stereo at large sizes; > 60000 frames; real wave memory exhaustion ("Wave memory full" - the unit shows it on the LCD
  only; our probe sees nothing: a full memory would look like "no object appeared").

Commands: `q2a` (loop over `quick(lab, "T-LEN", ...)` for n in 1,4,100,1000,5000 and `quick(lab, "T-RATE", [tone(1000,...)], rate)` for the rates),
`q2c` (40000 @ 44100, then 60000 @ 22050 under `T-BIG`), `q2d` (re-reads).

## 3. Failure modes (measured)

All of these were SENT with the normal pacing and checked with the object list + SP/WD read-backs:

* (a) first 3 of 6 wave messages then the SP: **nothing created** (no wave, no sample), unit silent.
* (a') one middle message of 4 skipped, then the SP: nothing created. Messages out of order (2,1,3,4): nothing created. A message
  duplicated (1,2,2,3,4): nothing created. => the unit wants the exact sequence; a damaged/short wave leaves NO partial object.
* (b) WD only (no SP): not committed; a later unrelated complete load worked normally and did not "leak" the staged wave; a later SP naming that
  earlier wave was rejected (staging does not survive another load). How long a stage survives with no other traffic is untested.
* (c) SP only naming a wave the unit lacks: rejected, no side effects (re-confirmed, 179 -> 179 objects).
* (d) stereo with only the L wave then an SP naming L+R: nothing created; only the R wave then the SP: nothing created.
* **SP FIRST, then the wave messages (stereo: SP, L, R), works too**: `T-SPF` + `T-SPF-L` + `T-SPF-R` were created. So the SP can precede its
  waves (the unit holds the sample until its waves complete). What happens if the waves never complete after an SP-first send is UNTESTED
  (T-GHOST-style SP-only alone was rejected; an SP-first + partial waves was not tried).
* Recovery from a failed/aborted send: nothing to clean up on the unit in any of the failure cases above (no objects appear), and there is still
  no delete opcode (not needed for these cases; orphans are collected by the unit itself, see 1 (d')).
* After a successful load the first request may go unanswered: in `q2a` an SP read after n=100 and a wave read after n=5000 returned nothing once
  each (re-runs were fine). The app's verification step should RETRY a read once or twice (2-4 s apart) before calling a load failed. A plain
  "unit answers identity" poll after the load succeeded 1.9-4.2 s after the last message in every test.
* A link (`change_link`) issued right after loads made the unit unresponsive for a long time TWICE: 28 s (T-BUSY) and ~8 min (T-COL; the first
  session run also timed out its link/backup/writes: "didn't confirm", "its backup could not be made"). The links DID take effect
  (program 128 lists the sample); the session's confirmation request just got no answer in time. A load by itself never did this (back to
  back loads each answered within ~4 s). Not understood. If the app assigns a freshly loaded sample, it must tolerate a long silence.

## 4. Pacing (NOT measured)

Not done: the minimum gap that still works. We only used `wire + 0.4 s`. The unit never answers, so there is no flow control to rely on.
Re-run later: `quick(lab, "T-PACE", [tone(12000, 22050, 220)], 22050, gap=0.1)` and `gap=0.0` (the `gap` argument is the extra seconds after each
message's wire time) - check the read-back is exact each time.

## 5. Names (NOT measured)

Not done (names tried so far were upper-case `T-...`, 'sine wave', 'MIDI 00102', 'SYNTH 1-L', 'PROBE SAMP', 'T-COL2-L'; all stored verbatim).
Wave names are derived by the app (`<sample>-L`/`-R`, 16 chars max, so a 15/16-char sample name would clash/truncate - the sender must derive wave
names that are unique and <= 16 chars). Still to test: 16-char names, lower/mixed case, trailing spaces, '/' and non-ASCII.

## 6. Parameters (partly measured)

* The unit keeps what the SP says for: key range low/high, original key, fine/coarse tune (read back as sent in every loaded sample) and the
  geometry/loop fields. Evidence: in (a)-(c) the replaced samples read back the template's 60 / 0..127 / 0 / 0 and the written 64 was replaced.
  A non-template original_key/key range being KEPT on a fresh load was not separately tested.
* The unit rewrites a few bytes of the SP on load (earlier probe: bytes 22-23, 60-63, 98-99, 106-107 for mono; more for stereo: 100-103, 108-111):
  object ids / wave addresses / sizes, not parameters.
* NOT done: a row-by-row comparison of a natively loaded sample against an SDS-loaded one.

## 2'. The unacknowledged "MIDI Bulk Received" message (measured)

From the moment the user stopped pressing OK on the A4000, **the unit kept working normally with the message left on screen** (assuming it
stayed displayed - the user said they would not touch it): four more complete loads in a row (`T-ACK1` mono, `T-ACK2` mono, `T-ACK3` stereo,
`T-ACK4` mono; each created exactly the expected objects, 172 -> 179 objects, SP and wave read-backs identical), plus identity requests, object
list requests, SP/WD reads and a full 60000-frame wave read (`T-BIG`) all answered as usual. No hang, no queueing, no delay. So the app does NOT need
to prompt the user to press OK after a load. (Caveat: we cannot see the LCD; if the message actually self-clears, this says nothing about a held one.
The user's own report: it appeared after every send.)

## 3'. The accidental ABORT

The user hit ABORT on the front panel while the unit was sending data back during the `q2b` re-checks of `T-LEN`. Which results are suspect:
* `q2b` itself (re-send of 100 and 5000 frames to `T-LEN` with read-backs): every read-back was exact (SP and wave identical) - and an abort of an
  outbound dump would only truncate a READ, never a load, so these are valid.
* The two single failed reads in `q2a` (n=100 "no sample", n=5000 wave None) and `T-BIG` 40000's wave read (None) / 60000's SP read (none) in `q2c`
  came BEFORE `q2b`; all were re-read successfully afterwards (`q2b`, `q2d`: `T-BIG` 60000 wave read 60000 frames identical in 101 s). Treat the
  "occasionally the first read after a load gets no answer" observation as real but unrelated to the abort; the 40000-frame wave read-back was never
  verified (its SP geometry was).
* The ~8 minute silence earlier (during `q1a`) predates the abort; cause unknown (see 3, link note).

## 4'. NOT done / needs a re-run, in priority order

1. Why does a LINK right after a load make the unit unresponsive for 28 s - 8 min? (assign with `Lab2.link("T-X")` then `poll_identity`; compare
   linking a sample loaded long ago, and linking a SYNTH-style sample immediately after load; check whether it depends on the number of samples
   already in program 128 - it had 2, then 3, then 4.) Matters for "send and assign".
2. Pacing minimum (section 4).
3. Names (section 5).
4. Parameters: native vs SDS-loaded SP row by row; non-template original key / key range kept?
5. SP-first with INCOMPLETE waves (what is left behind?); how long a staged WD survives with no other traffic (`lab.send(wd)`, sleep 10/30/60 s, then
   the SP).
6. 40000-frame wave read-back verification; stereo with large waves; wave-memory-full behaviour (fill with big loads on a throwaway unit; look for
   "no object appeared" + the LCD message).
7. Does the loop/key setup of a file (WAV `smpl` chunk) map to loop_mode 1/2 and loop addresses cleanly (writes are accepted on user samples,
   see core/yamaha_markers.py: set the four addresses before/with the SP; the SP carries them, so likely fine) - test by loading with
   `sets={"loop_mode": 1, "loop_start_address": 500, "loop_length": 1000, "loop_end_address": 1500}` via `Lab.build(..., sets=...)`.

## 5'. Recommendation for the app's native sender

* **Order/framing:** per sample: all wave messages of the left channel, then the right channel's, then the SP (or SP first - both work; waves-first is
  proven more times). Build waves with `yamaha_wave.build_wave_messages`; build the SP from a USER-sample template (a captured SP, not the factory sine)
  with: name, left wave name @64, right wave name @80 (all-zero bytes for mono), wave start 0, wave length = end = frames, loop start = end = frames,
  loop length 0, loop end = frames, loop mode 0 (or from the file's loop points), `sampling_frequency_l` AND `_r` = the sample's rate (no rounding
  needed, 1..65535 verbatim). No frames are trimmed. Mono: right wave name blank.
* **Wave names:** `<sample>-L` / `<sample>-R`, <= 16 chars, and **unique across the whole unit**. A wave name that already exists is overwritten
  IN PLACE even if another sample uses it (it silently corrupts that other sample). So before sending: read the object list; refuse (or rename) when a
  wave name we are about to send is used by a DIFFERENT sample; truncate long sample names so `-L`/`-R` still fit.
* **Name-collision policy for the SAMPLE name:** the unit replaces the sample in place (same object, assignments and program Easy Edit values kept,
  duplicate never created, never refused) but REPLACES ALL ITS PARAMETERS with the SP we send. So: if the name exists, ask "Replace sample X? Its
  settings (filter, envelopes, key range...) will be reset" with the default being a unique new name (`NAME 2`...) - OR read the old SP first and merge
  its parameter values into the new SP (keeping the user's edits) if a replace-keeping-settings option is wanted. Length and mono/stereo may change freely.
* **Pacing:** `wire time + 0.4 s` after every message is proven (about 440-590 frames/s per channel; stereo twice as long). The unit gives no
  acknowledgement at all, so the progress bar must be driven by bytes sent. Shorter gaps are untested.
* **Verification after the last message:** wait for the unit to answer an identity request (it answers 2-4 s after the last message), then request the
  object list (retry up to 3x, 3 s apart - a first request after a load is sometimes dropped), check the sample and each wave name are present, then read
  the SP and check wave start/length/end/loop/rate/wave names equal what was sent. A full audio read-back doubles the time (it is the same MIDI-speed
  dump) - make it optional (it passed every time it was run).
* **Failure handling:** a failed/partial send leaves NOTHING on the unit (a missing, short, duplicated or reordered wave message -> no objects), so the
  error is simply "the sampler did not accept the sample" (suspects: wave memory full, Bulk Protect on, wrong device number). Do not retry blindly; there
  is no cleanup needed. If the sample name existed, the previous version is unchanged after a failed replace (shown for the partial-wave case with a
  NEW name; for a failed REPLACE of an existing name this was not tested - test before promising it).
* **Warn the user:** (1) replacing a sample resets its parameters; (2) never reuse a wave name; (3) the unit shows "MIDI Bulk Received" after each load and
  does NOT need OK to continue (SUPERSEDED 2026-10-07: a later LINK hangs the unit until OK is pressed - AGENTS.md "Silent unit"); (4) linking a sample to a program right after loading can leave the unit silent for a long time (unexplained - give the
  link a generous timeout and don't report failure early); (5) Bulk Protect must be OFF; (6) transfers are slow (stereo, long samples: minutes).
* **No delete exists over MIDI**, so a test/aborted send cannot be undone except via the front panel or a power cycle (RAM only). The unit
  garbage-collects wave objects nothing references (stereo -> mono resend removed the right wave).

## 6'. Objects on the unit at the end (184 objects total: 128 programs + 56)

Left by the parent session: `PROBE SAMP` (sample, shares wave `SMP 004492`), `PAIR A` + `PAIR WAVE A`, `PAIR B` + `PAIR WAVE B`, `SYNTH 1` + `SYNTH 1-L`,
`SYNTH ST` + `SYNTH ST-L` + `SYNTH ST-R`. Also, from earlier probes, `MIDI 00101`'s wave `SMP 156452` holds POLARITY-INVERTED audio and `MIDI 00102`'s wave
`SMP 004492` was re-sent unchanged (byte 69 of its first message differs). Program 128 holds `SYNTH 1`, `SYNTH ST`, `T-COL` (Easy Edit level_offset 21), `T-BUSY`.

Created by the fork (all `T-`, all samples/waves listed by name; every one is disposable):
`T-BUSY` (+`T-BUSY-L`, assigned to program 128) - `T-COL` (assigned to program 128; now uses wave `T-COL2-L`, mono 3000 @ 22050, params reset to the template) -
`T-COL-L` (wave, now 2000 frames, used by `T-NEW`) - `T-NEW` (sample, 2000 frames) - `T-COL2-L` (wave) - `T-RATE`+`T-RATE-L` (last: 1000 frames @ 1000 Hz) -
`T-LEN`+`T-LEN-L` (5000 frames) - `T-BIG`+`T-BIG-L` (60000 frames @ 22050) - `T-ACK1`/`T-ACK2`/`T-ACK4` (+ `-L`) and `T-ACK3` (+ `-L`, `-R`, stereo) -
`T-OTHER`+`T-OTHER-L` - `T-SPF`+`T-SPF-L`+`T-SPF-R` (stereo, created by an SP-first send).
Also `T-COL-R` was created and removed by the unit. Attempted but NOT created (rejected sends): `T-PART`, `T-GHOST`, `T-STAGE`, `T-STL`, `T-STR`,
`T-SKIP`, `T-ORD`, `T-DUP`.
A power cycle clears all of it. Wave memory used by the T- objects: ~190k frames (dominated by `T-BIG`'s 60000).
