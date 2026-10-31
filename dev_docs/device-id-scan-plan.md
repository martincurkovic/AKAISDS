# Finding the sampler's Device ID: "scan all IDs" on failure

Written 2026-10-31. **Status: planned, nothing built, nothing measured on hardware.** Design is the owner's; the caveats and the measurement plan are mine.

## The problem

A sampler silently ignores any SysEx whose device byte isn't its own (AGENTS.md, "Transfer Dashboard > Device ID"). A wrong Device ID in Settings therefore looks like a dead connection: no reply, no
error from the sampler. The app only hints at it (`SamplerController`, "No reply from the sampler ... check the MIDI input port, cabling and Device ID", plus "(it is N; most Akais use 0)" when the
ID is not 0). An S1100 tester hit exactly this (2026-10-16: Settings said 1, the unit answered on 0).

## The design (owner, 2026-10-31)

When opening the editor (or a Dashboard operation) fails for lack of a reply, ASK the user whether to scan; if they agree, send one request to each of the 128 IDs, one at a time, with a
progress bar, and offer to switch to the ID that answers. Nothing is scanned without that yes.

Sketch of the dialogs (keep them short, per the short-UI-text rule):
1. "The sampler didn't answer on Device ID N. Look for it on the other IDs? This takes about X seconds." `[Scan]` `[Not now]`
2. Progress: "Checking ID k of 128..." with a bar and `[Cancel]`. Cancel is immediate and must leave the port idle.
3. Found: "A sampler answered on ID 5 (its panel shows 6)." `[Use ID 5]` `[Keep N]`. Not found: "No sampler answered on any ID. Check the ports, the cables and that it is on."
   X comes from the measured timeout below, not a guess.

## Rules it has to follow (AGENTS.md, "Real-hardware safety")

This is deliberately the one place the app sends into silence on purpose (127 of 128 requests get no answer by design), so every guard below is required, not optional:
- **User consent first, and a Cancel that works.** Never automatic.
- **One request outstanding.** Send ID k, wait for its answer or its timeout, only then ID k+1. Never pipeline.
- **Read-only requests only.** RSTAT (`47 <id> 00 48`, `midi_identity.build_rstat_request_message`) or the model's own list request. Nothing that writes, and never byte registers 6-9.
- **Accept only a reply that names what we asked for**: a STAT reply carrying the ID we just sent to. Our own request coming back (an interface or DAW echoing; the 2026-10-10 incident) is NOT an
  answer: compare against what we sent and ignore it. If echoes are seen, stop and say so (the app already has `BridgeWorker.stray_replies_detected` for the editor).
- **Stop at the first answer.** Don't keep sending after a hit. (Two samplers on one chain with different IDs would need "keep looking"; not planned.)
- **Not while anything else owns the wire** (a transfer, a load, the editor's own `BridgeWorker` job). Disk registers stall a sampler with disconnected drives; RSTAT is not one, but don't scan a unit that is mid-load.

## Where it goes

- **The scan itself**: a small pure helper (ID order, timeout, "is this reply the answer to that request") plus one `BridgeWorker` job for the editor (it is the only thing allowed to talk to `S3kBridge`;
  see "BridgeWorker"), and the equivalent on `SamplerController` for the Dashboard. Do not use a thread per request.
- **Switching the ID** reuses what Settings already does when the Device ID changes: `connect(exclusive_channel=)` and the editor's bridge reconnect. **Read the freeze order first** ("MIDI transport
  consolidation"): the window must be frozen (worker idle) before the swap and unfrozen only after the new bridge is in, every early return unfreezing. If doing that from inside an open editor turns out
  fragile, fall back to: save the new ID, close the editor, ask the user to open it again.
- **Failure sites to hook**: the Program Editor's initial list read and `connect()` failures (`program_editor_bridge.connect`, `ProgramEditorWindow`), and `SamplerController`'s "No reply from the sampler" branch.
  Offer the scan only for `akai` family (and only models where the request is known to work, see below).
- **Logging**: one INFO line per user action, prefix `IdScan:` (offered, accepted, cancelled, result with elapsed time and the ID found). Never log a line per ID at INFO.
- **Wording**: show the ID the way the Settings field does (raw 0-127) and add the panel number for models where it differs (S2000/S3000 show raw + 1; S900/S950 are Basic MIDI channel minus 1; S1000/S1100
  probably raw + 1, unproven: `akai-sysex-channel-rom-findings.md`). Keep the Settings label/tooltip wording ("not its MIDI channel", "usually 0").

## Per model

- **S2000/S3000**: RSTAT is known to work (the Sampler Test and the Memory bar use it). First target.
- **S1000/S1100**: RSTAT is **untried on a real S1000** (AGENTS.md). Until a tester confirms, the scan may find nothing even on the right ID; offer it only after that is known, or use another read-only
  request the S1000 is known to answer (the program/sample list reads work on an S1100).
- **S900/S950**: no separate SysEx channel; the byte is the Basic MIDI channel - 1, and with Omni ON the unit accepts ANY value, so a scan finds the first ID it tries. Don't offer it there.
- **Yamaha, Ensoniq, ASR-X**: out of scope (different ID semantics; the ASR-X has its own device-ID fallback).
- **Generic SDS**: out of scope.

## Open question: the broadcast identity request

`midi_identity.build_identity_request_message()` sends `F0 7E 7F 06 01 F7` (device 7F = everyone), and a Universal Identity reply carries the responder's own ID in its second byte
(`7E <id> 06 02 ...`). If an Akai answered it, ONE message would find the ID and no scan would be needed. The notes disagree: a comment in `midi_identity.py` says Akai "doesn't understand regular ID
requests", AGENTS.md says the S1000/S2000/S3000 "answer identity identically", and the comment's sample reply starts `7E 00 06 02 47 ...`. **Settle this first on the S2000**; if it answers and the byte
is the unit's ID, build that instead (or as step one, with the scan as the fallback).

## To measure on hardware (owner's S2000, app closed, no DAW or MIDI monitor)

Read-only; write it as a tool in the style of `tools/midi_echo_check.py` / `s2000_check.py` (one request outstanding, stop after a few unanswered reads), log everything.
1. **Reply time at the right ID**: RSTAT x 50, report min / median / p95 / max. Repeat while the unit is idle, and once right after a sample list read, so a "slow" case is on record.
2. **Silence at wrong IDs**: RSTAT to a few wrong IDs (e.g. 1, 5, 64, 127): confirm nothing comes back and the unit's panel and state are unchanged afterwards.
3. **The timeout** = a margin over the slowest reply seen (say 3x max, not less than the interface's own latency), then the whole scan time = 128 x timeout; the dialog's "about X seconds" comes from this.
   If that is more than ~30 s, think about ordering (0 first, then 1-15, then the rest) and a "keep going?" prompt after 0-15.
4. **Does the broadcast identity request get an answer, and what is its second byte?** (the open question above).
5. **Does a wrong ID matter when it is close**: e.g. does the unit answer to ID + 1 or to a masked value (the S950 masks to 4 bits)? If it answers to more than one ID, the scan must report the first and say so.
6. Note the S2000's OS version, the interface and its latency with the results, here and in the tool's docstring.

## Tests (no hardware)

- The pure helper: ID order, accepting only a reply that matches the asked ID, ignoring an echo of our own request, stopping at the first hit, cancel between two IDs.
- A fake bridge that answers only on one ID (`FakeS1000` / `DemoBridge` may not model the device byte: check, and add it rather than special-casing the test).
- A window test with the real `BridgeWorker` thread (`wait_until_idle()`, stop and wait in teardown, as the other editor tests do): decline leaves everything alone, accept scans with the progress
  dialog, cancel returns the worker to idle, found-and-accepted writes the ID and reconnects, found-and-declined changes nothing.
- The Dashboard variant against the fake `SamplerController` ports.

## Not doing

- Scanning automatically, or on a schedule.
- Probing with anything other than a read-only request, or probing an ID range wider than 0-127.
- Scanning Yamaha/Ensoniq/ASR-X or S900/S950.
