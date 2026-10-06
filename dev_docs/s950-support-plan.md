# AKAISDS: Akai S900/S950 support plan

Written 2026-10-03, last revised 2026-10-05. **Status: built and ready for a first tester.**
Dashboard transfers, a program editor (view + staged edits + verified write) and a read-only
Samples tab all exist and pass their tests against our own fake (`core/demo_s950.py`).
**Nothing has been tried against real hardware** - there is still no tester with an S900/S950 and
no S900 of your own. Everything below is a reading of someone else's code/docs or a test against
that fake; where something is a guess rather than a measurement, it says so.
**Section 9 is the handoff for whoever picks this up next - read it first.** What a tester should
do, and the full list of guesses, is `tests/s950_test_plan.md` (in the repo).

## 1. Where AKAISDS stands today

| Mode | Works on an S900/S950? | Why |
|---|---|---|
| **Akai mode** (S1000 / S2000/S3000 entries) | **No** | Every message is `F0 47 cc <func> 48 ...` (model byte `0x48`, the S1000 family: SDATA, ASPACK, SLIST, nibbled data, 12-char Akai charset names). The S900/S950 uses device byte `0x40`, different function codes, 10-char ASCII names, checksums. It would not answer even a status/list request. |
| **Generic SDS mode** | **Very probably not** (never tested) | Sends *standard* SDS framing. The S950's dump framing is non-standard (table below). Payload encoding for 12-bit words does match; framing and handshakes do not. |

The standard-SDS vs S950 framing difference, per `s950tools` (whose comments say each point was
hardware-verified):

| | Standard SDS (what AKAISDS Generic sends) | S950 |
|---|---|---|
| Dump header | `F0 7E cc 01 ...` - 21 bytes, **with** a device/channel byte | `F0 7E 01 ...` - 19 bytes, **no** channel byte |
| Data | one `F0 7E cc 02 pp <data> cs F7` packet per block | bare blocks (block#, 120 data bytes, XOR checksum = 122 bytes) all inside **one** SysEx with a single `F7` at the very end |
| Acknowledge | 6 bytes `F0 7E cc 7F pp F7` | 4 bytes `F0 7E code F7`; the 6-byte form is **silently ignored** |
| Request a dump | `F0 7E cc 03 ss ss F7` | `F0 7E 00 num 00 F7` |

Note: with channel 0, AKAISDS's header's third byte is `0x00` - which is the S950's own
"request sample dump" code. Don't try Generic mode on an S950 casually; expect nonsense, not a
clean failure. (Probably harmless, but unverified.)

Generic mode's 12-bit option encodes words the same way the S950 wants them (left-justified, two
bytes per word, 60 words / 120 bytes per block), so the *payload* code in `core/sds_encoder.py` is
reusable; the *framing* is what needs a new path.

## 2. S900 vs S950

From `s950tools` (one codebase, one spec doc: "Akai S900 MIDI System Exclusive Data Format V2.0,
which applies to the S950"): **one protocol family** - same `0x40` device byte, function codes,
and program / keygroup / sample-parameter block layouts, and the same floppy format. Known
differences it mentions:

- Sample rate: S950 is 7.5-48 kHz continuously variable; 40 kHz was the S900's ceiling.
- The "enable MIDI program change" flag in the program header is marked **S950 only**.
- RS-232 (the S950 has a serial port; a few settings, e.g. controller-select, ignore writes over
  the wire).

Not known from that repo: S900 OS-version differences, and whether an S900 honours everything the
S950 does. **Treat "S900/S950" as one Sampler Type, but expect S900-specific surprises** and get
the actual spec document (see section 6).

## 3. What the S900/S950 protocol is like (vs. what AKAISDS already handles)

- **Whole-block, like the S1000 adapter** - a program and all its keygroups travel as one `PRGM`
  message (header + 1-31 keygroups); a sample's parameters as one 120-byte `SPRM`. No per-field
  read/write. So the `S1000Bridge` pattern (read-modify-write, preserve unknown bytes, never trust
  a block length) applies directly.
- **Data types:** every 8-bit value is sent as **two** MIDI bytes (low 7 bits, then bit 7 - "DB");
  16/32-bit values are 2/4 of those (little-endian); 21-bit "TB" values are three 7-bit groups;
  12-bit sample words ("SW") are two bytes. Names are **10 ASCII characters** (catalog names are
  plain ASCII, program/sample names inside blocks are DB-encoded).
- **Framing:** `F0 47 <chan> <func> 40 <num> 00 <payload> <xor checksum> F7`. Checksum is the XOR
  of every payload byte, masked to 7 bits.
- **Function codes (0-11):** RDRS 0, ROVS 1, RPRGM 2, RCAT 3, RSPRM 4, SECRE 5, SECRD 6, DRS 7,
  OVS 8, PRGM 9, SPRM 10, CAT 11. (Request = low numbers, data = high.)
- **No delete opcode** in the documented surface (a `TODO` in `s950tools` says the same - front
  panel only, unless something undocumented exists). So no delete/rename-by-opcode like the Akai
  family has; rename = write a new `SPRM`/`PRGM`.
- **Program model:** up to 31 keygroups; each keygroup has a *soft* and a *loud* sample (name +
  transpose + filter + loudness) and a velocity-switch threshold - not four velocity zones. Amp
  and filter ADSRs, one LFO, a handful of velocity/aftertouch/modwheel routings, per-keygroup
  output assign. No modulation matrix, no 4-stage ENV2, no Multi.
- **Sample model:** 12-bit, 200-475,020 words, rate as a 16-bit Hz value in `SPRM` and as a
  period in ns in the dump header, loop mode one-shot/loop/alternating, 1/16-semitone pitch units
  (C3 = 960).
- **Timing quirks learned the hard way by `s950tools`:** it uses a catalog request (`RCAT`) as a
  "device ready" barrier before an upload, because a stale `ACK` from the previous upload satisfied
  a plain "any reply" ping and green-lit a second dump while the S950 was still busy. The S950
  streams per-block ACKs during an open-loop dump. There is a note about an "active edit buffer"
  the S950 only refreshes in certain situations (`internal/device/device.go` around line 174 - read
  this before designing program writes).

## 4. Why `s950tools` is the starting point (and what it is not)

`github.com/diemonster/s950tools` - Go CLI + Wails GUI, **MIT**, ~17k lines, one author (Brandon
Ivers), last pushed June 2026, thorough tests, comments that reference real hardware sessions.

- **Not usable as a dependency:** Go + cgo RtMidi, no library API for Python, and it opens its own
  MIDI ports - which is the "second connection to the same port" race the shared MIDI transport
  exists to prevent. Shelling out to a bundled binary per platform would also complicate CI.
- **Very useful as a reference to port from:**
  - `internal/sysex/codec.go` - the DB/DW/DD/TB/SW/name codecs and XOR checksum (~100 lines to port).
  - `internal/protocol/messages.go`, `program.go`, `overall.go` - message builders/parsers and
    the 76-byte program header, 140-byte keygroup, 120-byte `SPRM` layouts with field names,
    ranges, defaults, and the "keep raw bytes, overwrite only known fields" approach.
  - `internal/device/device.go` - upload/download flows, open-loop dump, readiness barrier,
    NAK window, slot picking.
  - Tests, especially `wirelog_decode_test.go` and the protocol tests: **golden byte sequences to
    port as Python test fixtures** - the closest thing to ground truth without an S950.
  - `internal/sample/` - 16-bit <-> 12-bit offset-binary conversion, resampling aliases.
- **Not relevant:** `internal/akaidisk/` (floppy images), the RS-232 `transport/serial.go` and MIDI
  thru, the Wails GUI.
- It cross-checks its layouts against `dxzl/akai-s950` (C++ Builder), which has **no license**.
  Don't copy anything from that repo directly.

## 5. Staging - what exists and what doesn't

Each stage was built shippable (as experimental) and tested against a fake first, as the S1000 work
was (`core/demo_s1000.py` faking the MIDI *ports*).

| Stage | Status | Where |
|---|---|---|
| 0 - prerequisites | **NOT done** (see below) | - |
| 1 - protocol layer (codecs, framing, SPRM/PRGM/CAT, sample dump, `FakeS950`) | done | `core/s950_sysex.py`, `core/s950_program.py`, `core/demo_s950.py` |
| 2 - a Sampler Type with its own protocol family `"s950"` | done | `core/sampler_models.py`, `sampler_controller.py`, `dashboard.py`, `settings_dialog.py` |
| 3 - transfers (catalog, send, receive, rename, info) | done | `controller/s950_transfers.py` |
| 4 - program editor, read side (a separate small window, not `ProgramEditorWindow`) | done | `ui/s950_program_editor.py` |
| 5 - program editing: staged, backed up, verified | done, for EXISTING programs | `core/s950_params.py`, `S950Transfers.write_program` |
| Samples tab (read-only) + tabs, auto-selected first program | done | `ui/s950_samples_tab.py` |

**Stage 0 - still outstanding**
- Obtain the Akai S900 MIDI SysEx spec (V2.0) and read it against `s950tools`; resolve any
  disagreement in favour of the spec + a real device.
- A tester with an S900/S950, or your own S900. Ask the `s950tools` author whether they would share
  wire captures (`s950-tools --verbose` hex-dumps every SysEx) and whether they would be happy to
  be credited/consulted.

**Deliberately NOT built** (each needs a tester's answer first):
- **Sample editing** (loop points, replay mode, start/end, trim ...): SPRM writes whose start/end/loop
  semantics are only inferred, plus an "active edit buffer" caveat.
- **Create/duplicate/delete a program** (there is no delete opcode; create must pick a free slot and
  never overwrite), and **adding/removing keygroups** (changes the message length).
- **Renaming a sample does not rewrite the programs that use it.** Programs find samples BY NAME, so
  a rename silently breaks any keygroup pointing at the old name.
- Keygroup "Options" bits (inferred meaning) are shown, never written.
- A parameter-table `S950Bridge` in the style of `S1000Bridge` was planned and never needed: the
  S950 has no per-field access, and whole-program read/write through `S950Transfers` covers it.

## 6. Open questions (answer these with hardware or the spec)

*Status 2026-10-05: none of these is answered - no hardware yet. The code was written so each one
is cheap to settle once a tester exists (everything is logged in hex at DEBUG, and the fake's
docstring lists every guess). Questions 2 and 3 also bear on program writes, which now exist.*

1. Does the S950 (or S900) accept the *standard* SDS framing too, on some OS versions? If yes,
   Generic mode might already half-work. (`s950tools` says the 6-byte standard ACK is ignored; that
   is the only evidence.)
2. What does writing a program/sample under an existing name do (replace, duplicate, delete)?
3. What exactly is the "active edit buffer" behaviour and which writes does it affect?
4. Real-hardware block/message sizes - the S1000 work showed the spec's lengths can't be trusted
   blindly. Log raw messages in hex at DEBUG from day one (as `S1000Bridge` does).
5. Which settings are write-protected over the wire (`s950tools` found controller-select silently
   ignores writes)?
6. S900-specific differences (older OS versions, 40 kHz ceiling, missing fields).
7. Is there any undocumented delete/rename opcode? (`s950tools`'s `TODO` suggests checking
   `dxzl/akai-s950`; verify on hardware before trusting anything it hints at.)

## 7. Licensing notes

- `s950tools`: **MIT**. Translating its code or tests into Python is a derivative work - keep its
  copyright notice (Copyright (c) 2026 Brandon Ivers) and the MIT text in the ported files or in a
  `THIRD_PARTY` / `NOTICE` file, and say which files are derived. Credit in the README too.
- `dxzl/akai-s950`: **no licence stated** -> all rights reserved by default. Do not copy code or
  prose. Byte offsets are facts about hardware, but prefer the Akai spec as the source.
- `akaiutil` (referenced by `s950tools` for the floppy format): only relevant if you ever touch
  disk images, which this plan does not.
- I am not a lawyer; if the project ever becomes commercial, get actual advice.

## 8. References

- `https://github.com/diemonster/s950tools` - Go CLI + GUI, MIT (the main reference).
- `https://github.com/dxzl/akai-s950` - C++ Builder RS-232 uploader, no licence (cross-reference
  only; do not copy).
- Akai S900 MIDI System Exclusive Data Format V2.0 - **to obtain**; applies to the S950.
- In this repo: `AGENTS.md` ("Akai S1000 support" - the adapter pattern, fake-sampler testing and
  hardware-test-plan workflow this plan copies), `core/s1000_bridge.py`, `core/demo_s1000.py`,
  `tests/s1000_test_plan.md`, `core/sampler_models.py`, `core/sds_encoder.py`,
  `controller/sampler_controller.py` (the `device_type == "akai"/"generic"/"s950"` branches).

## 9. Handoff notes

`AGENTS.md`'s "Akai S900/S950 support" section holds the standing rules; this is the longer "what I'd
want to know" list.

### Where everything is
| What | File |
|---|---|
| Wire format: codecs, framing, catalog, SPRM, sample dump, 16<->12-bit | `src/core/s950_sysex.py` |
| Program (76-byte header + 1-31 x 140-byte keygroups) | `src/core/s950_program.py` |
| Editable fields, limits, diff/validate/apply for a write | `src/core/s950_params.py` |
| Fake S950 on rtmidi-style ports (like `FakeS1000`) | `src/core/demo_s950.py` |
| Catalog / send / receive / rename / info / program read+write / sample-parameter read state machine | `src/controller/s950_transfers.py` |
| Sampler Type + protocol family | `src/core/sampler_models.py` (`FAMILY_S950`) |
| Controller delegation + refusals | `src/controller/sampler_controller.py` (`_s950`, `_s950_not_supported`) |
| Dashboard gating, sparse slot list, experimental warning, opening the editor | `src/ui/dashboard.py` |
| Program editor window / Samples tab | `src/ui/s950_program_editor.py` / `src/ui/s950_samples_tab.py` |
| Layout pieces shared with the S1000/S2000/S3000 editor | `src/ui/editor_layout.py` |
| Settings "Run Hardware Test" (RCAT round trip) | `src/ui/settings_dialog.py` |
| Licence notice for the ported code | `THIRD_PARTY_NOTICES.md` |
| What a tester does + every guess | `tests/s950_test_plan.md` |
| Run the real Dashboard on the fake, no hardware | `tools/s950_demo.py` |
| Where each overwritten program is backed up before a write | `~/.akaisds/s950_backups/*.syx` |

Reference clone of s950tools (read-only, for porting more): `git clone
https://github.com/diemonster/s950tools` - the useful bits are `internal/protocol/program*.go`,
`internal/device/device.go` (read the comments, they record real hardware sessions) and
`internal/protocol/wirelog_decode_test.go` (real captures).

### How trustworthy each layout is
- **Program HEADER offsets**: pinned by two real S950 captures (in `tests/test_s950_program.py`).
  The always-255 reserved byte at wire offset 44 is the alignment check.
- **Keygroup and SPRM offsets**: only as good as s950tools' copy (which cross-checked an unlicensed
  C++ project). No real capture here. If a field reads as nonsense on hardware, suspect an offset
  before the unit. The ranges in `core/s950_params.py` are s950tools' documented ones, unverified
  (the +-24 semitone transpose cap is ours).
- Control-bits meanings, `SNOMP` direction/units, the undefined bytes (`44 01 44 01` etc.) and what
  SPRM start/end/loop do - all inferred. The Dashboard deliberately shows root note/detune as
  unavailable for this reason.
- `to_payload()` does not range-check (values mask silently); validation lives in `core/s950_params.py`
  and the UI. It re-encodes a field only if it changed, so untouched bytes stay byte-identical.

### Design facts
1. **One operation at a time.** `S950Transfers._op` is the single in-flight thing; every request goes
   through `_request()` with a reply timeout and a pending-reply match on (function, slot). A new
   kind of request is a new op in that same machine (`_begin(...)`, add it to `_BUSY_OPS`, tests in
   the same style) - NOT a second code path, NOT a thread-per-action (see AGENTS.md's `BridgeWorker`
   history). `S3kBridge`/`BridgeWorker` are the wrong tools here: they speak the S3000 protocol.
2. **The unit does not answer PRGM or SPRM writes.** The only evidence a write landed is reading it back
   (RPRGM/RSPRM) after a settle delay (s950tools waits ~200 ms).
3. **Programs and samples reference each other BY NAME.** The unit enforces no uniqueness. The send
   path's unique-suffix naming ("KICK-2") is safe only because it never renames something already
   referenced.
4. **Catalog numbers**: programs and samples have separate numbering (`P`/`S` entries); slots are
   sparse; a program's slot is its catalog number, NOT its MIDI program-change number
   (`midi_program_number` is a field inside the program; `enable_midi_program` is S950-only).
5. **Writing a program to a free slot creates it** (that is all s950tools' `put-program` does). The
   editor never does this: it only edits programs that already exist.
6. Everything is asynchronous and Qt-timer driven; nothing blocks the GUI thread except
   `sds_encoder` resampling/reading in `prepare_words` (same as the Akai path).
7. `Program.num_keygroups` is derived (`len(keygroups)`); the wire's own count byte is rewritten on
   write. `raw` / `header_raw` are excluded from dataclass equality on purpose.
8. **The "active edit buffer"** (`device.go`): s950tools documents it for SPRM only - e.g. a Reverse
   change doesn't take effect until the sample is re-selected on the unit. Nothing says programs behave
   the same, which is why the test plan has the tester check by ear/screen, not just trust the
   read-back. s950tools' own GUI live-syncs program edits by overwriting the slot with PRGM (400 ms
   debounce) - the only evidence that an in-place overwrite works.

### Things that are guessed
The full, current list is `tests/s950_test_plan.md` ("What is a guess") and `core/demo_s950.py`'s
docstring (what the fake assumes). The ones that matter most:
- the number of ACKs a dump needs (the fake assumes one per block; the real engine streams one every
  50 ms and relies on that being enough)
- what a NAK'd upload leaves behind (the fake stores nothing; s950tools says "may be truncated")
- catalog ordering, the default name of an uploaded sample, silence vs NAK for requests on empty slots
- how the dump header's loop fields map onto SPRM start/end/loop length
- whether the S900 honours everything the S950 does; whether 44.1 kHz is accepted on an S900 (its
  ceiling was 40 kHz) - the send path clamps to the dump header's 2-65.5 kHz range, not the hardware's
  7.5-48 kHz
- everything about overwriting a program in place (see the test plan, guesses 1-5)

### Known unmeasured risks
- **Large receives**: one SysEx of up to ~1 MB. macOS CoreMIDI hands rtmidi the whole message;
  backends that fragment long SysEx would lose it, because `MidiManager._on_raw_message` drops any
  fragment that doesn't start with F0. s950tools sets a 2 MiB rtmidi SysEx buffer for this reason;
  python-rtmidi exposes no equivalent that we've found. The receive timeout message names this.
- **Large sends**: one `send_message` of up to ~1 MB through `SharedMidiOutput`. Untested on a real
  port; s950tools does the same in Go.
- **Open loop is not supported** for the S950 (slot choice, readiness and naming all read replies), so
  Send needs a MIDI input selected - a deliberate difference from the Akai path.
- Timing waits (3125 B/s + 10%, 500 ms NAK window, 200 ms settle before a read-back) are copied from
  s950tools' MIDI-tuned values; all are attributes on `S950Transfers` so they can be tuned.

### Before the first real tester
1. Ask the s950tools author for real wire captures (SPRM, a keygroup-bearing PRGM, an RCAT reply, an RSD
   dump) - they would turn the unverified offsets above into pinned ones.
2. Obtain the actual "Akai S900 MIDI System Exclusive Data Format V2.0" document (not found online).
   Neither the S950 owner's manual nor the S900 manual contains it. A searchable PDF would be worth
   diffing against `s950_sysex.py` / `s950_program.py` line by line.
3. Hand the tester `tests/s950_test_plan.md` and ask for `~/.akaisds/akaisds.log` (everything is logged,
   requests in hex) plus the `.syx` backups of any program that didn't verify.

### Pitfalls hit while building this (so you don't)
- **`QListWidget.clear()` defers deleting row widgets.** Any test that repopulates a list with row
  widgets must flush deferred deletes (`QCoreApplication.sendPostedEvents(None,
  QEvent.Type.DeferredDelete.value)`) or the delete fires later inside another test's nested event
  loop (`_wait_for_any_signal`) and segfaults in `QWidget::destroy`. The `dashboard` fixture does it.
- **A fresh `Knob.setValue(0)` emits no `valueChanged`**, so a readout wired only to that signal keeps
  showing "-" for a zero. Fill it explicitly when loading.
- **A sub-layout's spacing defaults to its parent's**, and a page inside a scroll area has Fusion's
  default 9px margins - together they made "6px" spacing look like 15px. See AGENTS.md's "Shared
  editor layout".
- **Don't `git stash` here** while the user is committing - a backgrounded run once left the tree
  stashed for minutes. Use plain diffs, or a worktree.
- macOS `sed -i` needs `-i ''`; use Python for multi-line edits.
- The unit's replies to a standard-SDS ACK on channel 0 (`F0 7E 00 pp F7`) collide with the S950's own
  request-sample-dump (`F0 7E 00 nn 00 F7`) - never test Generic SDS against an S950 casually.
- `test_s950_transfers.py` shrinks every wait (`MIDI_BYTES_PER_SECOND`, the `*_ms` attributes) and
  delivers fake replies via `QTimer.singleShot(0, ...)` so a reply never lands inside the send call
  (real replies don't). Keep that when adding tests - a synchronous fake hides ordering bugs.
- A script run with `os._exit(...)` doesn't flush a piped stdout - `print(..., flush=True)`.

### Running things
- Whole suite: `uv run pytest tests -q` (~40 s).
- Just this feature: `uv run pytest tests/test_s950_sysex.py tests/test_s950_program.py
  tests/test_s950_params.py tests/test_demo_s950.py tests/test_s950_transfers.py
  tests/test_s950_program_editor.py tests/test_s950_samples_tab.py -q` (a few seconds).
- See it: `uv run python tools/s950_demo.py` (headless check: add `--smoke` with
  `QT_QPA_PLATFORM=offscreen`), then press Open Editor.
- One harmless warning in the suite: `Wave_write.__del__` from stdlib `wave`, when
  `sds_encoder.write_wav_file` is given an unwritable path (a test exercises exactly that).
