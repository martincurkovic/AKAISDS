# Akai S900/S950 support

Written 2026-10-03, condensed 2026-10-10. **Status: built, never run against hardware.** The Dashboard transfers, a program editor
(view, staged edits, verified write) and a read-only Samples tab exist and pass their tests against our own fake (`core/demo_s950.py`).
There is no tester with an S900/S950. Everything here is a reading of someone else's code or a test against that fake; guesses say so.
What a tester should do, and the full list of guesses: `tests/s950_test_plan.md`. Standing rules and the code map: `AGENTS.md`.

## Why a separate protocol path

The S900/S950 is not a variant of the S1000 family. AKAISDS's Akai mode sends `F0 47 cc <func> 48 ...` (model `0x48`, SDATA, nibbled data,
12-char Akai names); the S900/S950 uses device byte `0x40`, function codes 0-11, every 8-bit value as two MIDI bytes, 10-char ASCII names and
XOR checksums. Generic SDS mode would very probably not work either (never tested), because the dump framing is non-standard:

| | Standard SDS (Generic mode) | S950 |
|---|---|---|
| Dump header | `F0 7E cc 01 ...`, 21 bytes, with a channel byte | `F0 7E 01 ...`, 19 bytes, no channel byte |
| Data | one `F0 7E cc 02 pp <data> cs F7` packet per block | bare 122-byte blocks (block#, 120 data bytes, XOR) in ONE SysEx with a single `F7` |
| Acknowledge | 6 bytes `F0 7E cc 7F pp F7` | 4 bytes `F0 7E code F7`; the 6-byte form is silently ignored |
| Request a dump | `F0 7E cc 03 ss ss F7` | `F0 7E 00 num 00 F7` |

With channel 0 a standard header's third byte is `0x00` - the S950's own "request sample dump" code. Don't try Generic mode on an S950.
The 12-bit word encoding does match Generic's 12-bit option, so the payload code in `core/sds_encoder.py` is reusable; the framing is not.

## S900 vs S950

One protocol family (s950tools' source: "Akai S900 MIDI System Exclusive Data Format V2.0, which applies to the S950"): same device byte,
function codes, block layouts and floppy format. Known differences: S950 sample rate 7.5-48 kHz (40 kHz was the S900's ceiling); the
"enable MIDI program change" flag is S950-only; the S950 has RS-232 and a few settings (controller select) ignore writes over MIDI. S900
OS-version differences are unknown. It is one Sampler Type; expect S900-specific surprises.

## Protocol facts (from s950tools; its comments mark what was hardware-verified)

- **Whole-block, like the S1000 adapter:** a program and its 1-31 keygroups travel as one `PRGM`; a sample's parameters as one 120-byte `SPRM`.
  No per-field access, so read-modify-write and keep unknown bytes.
- **Data types:** DB (8-bit as two bytes, low 7 bits then bit 7), DW/DD (16/32-bit, little-endian), TB (21-bit, three 7-bit groups), SW
  (12-bit sample word, two bytes). Framing `F0 47 <chan> <func> 40 <num> 00 <payload> <xor> F7`; the checksum is the XOR of the payload, masked.
- **Functions:** RDRS 0, ROVS 1, RPRGM 2, RCAT 3, RSPRM 4, SECRE 5, SECRD 6, DRS 7, OVS 8, PRGM 9, SPRM 10, CAT 11. No delete opcode.
- **Programs:** up to 31 keygroups, each with a soft and a loud sample and a velocity-switch threshold (not four velocity zones); one LFO;
  no modulation matrix, no ENV2, no Multi. **Samples:** 12-bit, 200-475,020 words, rate as Hz in `SPRM` and as nanosecond period in the dump
  header, loop mode one-shot/loop/alternating, pitch in 1/16 semitones (C3 = 960).
- **Timing:** an `RCAT` reply is the "device ready" barrier before an upload (a stale ACK satisfied a plain ping and green-lit a second dump);
  the S950 streams per-block ACKs during an open-loop dump; SPRM changes can sit in an "active edit buffer" (s950tools `device.go` ~line 174).

## Design facts

1. ONE operation at a time (`S950Transfers._op`); every request goes through `_request()` with a reply timeout and a (function, slot)
   match. A new request is a new op in that machine - not a second path, not a thread per action. `S3kBridge`/`BridgeWorker` speak the S3000
   protocol and are the wrong tools.
2. The unit does not answer `PRGM`/`SPRM` writes. The only evidence a write landed is a read-back after a settle delay (~200 ms).
3. Programs and samples reference each other BY NAME and the unit enforces no uniqueness, so the send path's `KICK-2` style suffix is safe
   only because it never renames something already referenced. Catalog numbers are separate for programs and samples and are sparse; a
   program's slot is NOT its MIDI program-change number.
4. Writing a program to a free slot creates it (all s950tools' `put-program` does). The editor never does; it edits existing programs.
5. `Program.num_keygroups` is derived; the wire count byte is rewritten on write; `raw`/`header_raw` are excluded from dataclass equality.
6. Trust levels: program HEADER offsets are pinned by two real captures (`tests/test_s950_program.py`, with an always-255 reserved byte at
   wire offset 44 as the alignment check). Keygroup and SPRM offsets are only as good as s950tools'. Control-bits meanings, `SNOMP`
   direction/units, the undefined bytes and SPRM start/end/loop semantics are inferred. If a field reads as nonsense on hardware, suspect an
   offset before the unit. Range limits in `core/s950_params.py` are s950tools' (the +-24 st transpose cap is ours).

## Not built (each needs a tester's answer first)

Sample editing (SPRM start/end/loop semantics are inferred, plus the edit-buffer caveat); creating/duplicating/deleting programs;
adding/removing keygroups (changes the message length); rewriting programs when a sample is renamed; the keygroup "Options" bits (shown,
never written).

## Open questions (answer with hardware or the spec)

1. Does an S950/S900 accept standard SDS framing on some OS versions? (Only evidence: s950tools says the 6-byte ACK is ignored.)
2. What does writing a program or sample under an existing name do (replace, duplicate, delete)?
3. Exactly what is the "active edit buffer" and which writes does it affect? Check by ear/screen, not just the read-back.
4. Real message sizes (the S1000 showed spec lengths can't be trusted); which settings are write-protected over the wire; any
   undocumented delete/rename opcode; S900-specific differences.

## Unmeasured risks

- **Large receives:** one SysEx up to ~1 MB. CoreMIDI hands rtmidi the whole message; a backend that fragments long SysEx would lose it
  (`MidiManager._on_raw_message` drops fragments not starting with F0). s950tools sets a 2 MiB rtmidi buffer; python-rtmidi has no equivalent.
  Large sends are one `send_message` through `SharedMidiOutput`, also untested on a real port.
- Waits (3125 B/s + 10%, 500 ms NAK window, 200 ms settle) are s950tools' values; all are attributes on `S950Transfers`.
- Open loop is not supported (slot choice, readiness and naming read replies), so Send needs a MIDI input.

## Before the first real tester

Ask the s950tools author for real captures (SPRM, a keygroup-bearing PRGM, an RCAT reply, an RSD dump) - they would pin the offsets above.
Obtain the "Akai S900 MIDI System Exclusive Data Format V2.0" document (not found online; neither owner's manual has it) and diff it against
`s950_sysex.py`/`s950_program.py`. Hand the tester `tests/s950_test_plan.md` and ask for `~/.akaisds/akaisds.log` (everything is logged, requests
in hex) plus the `.syx` backups (`~/.akaisds/s950_backups`) of any program that didn't verify.

## Licensing and references

- [s950tools](https://github.com/diemonster/s950tools) (Go, **MIT**, one author) is the main reference; the Python port keeps its notice
  (`THIRD_PARTY_NOTICES.md`) and says which files derive from it. Not usable as a dependency (Go + cgo, opens its own MIDI ports).
  Most useful: `internal/sysex/codec.go`, `internal/protocol/` (builders, parsers, layouts), `internal/device/device.go` (its comments
  record real sessions) and `wirelog_decode_test.go` (real captures).
- [dxzl/akai-s950](https://github.com/dxzl/akai-s950) (C++ Builder) has **no licence**: cross-reference facts only, never copy code or prose.
- In this repo: `core/s1000_bridge.py` and `core/demo_s1000.py` are the patterns this copied (read-modify-write, fake ports).
