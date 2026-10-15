# Ensoniq (EPS / ASR) sample transmission

Written 2026-10-15. **Status: research, plus `core/ensoniq_sysex.py` (the codec, with golden tests) and a placeholder Sampler Type `ensoniq_eps_asr` that refuses every transfer. No engine, no Ensoniq unit has been tried, no tester has reported back yet.**
Everything below is a reading of Ensoniq's own spec documents (scanned PDFs, read page by page as images) plus one third-party tool's
source. Grades: **SPEC** = stated in an Ensoniq document, **TOOL** = seen in a community implementation, **GUESS** = ours. Standing rules
and the code map: `AGENTS.md`. **A cross-check of this plan against the EPS spec's text layer (2026-10-15) is in `ensoniq-spec-crosscheck.md`: read it
before coding. It corrects the parameter-number encoding, answers open question 7 and adds a 20-bit range limit.** The closest precedent for the work is `s950-support-plan.md` (also a separate protocol written without hardware).

## Why this exists

An ASR-X owner tried Generic SDS mode: the Dashboard showed progress, the Sampler Test identified the machine, and no sample appeared in
the ASR-X's Scratch Pad. A tester log is still pending. The documents say why:

- **The ASR-X Pro Reference Manual never mentions the MIDI Sample Dump Standard.** Its MIDI implementation chart says System Exclusive:
  "see ASR-X Pro SysEx Specification" and "recognizes MIDI Tuning Dump Standard and Single-Note Tuning Change". Its only sample-transfer
  route is **SMDI over SCSI** (manual p.86, "SMDI Transfers"); everything else is a DOS-format disk with AIFF/WAV files.
- **The whole EPS/ASR line uses Ensoniq's own SysEx, manufacturer `0F`.** A 1988 *Music Technology* review of the EPS: "Nor does the EPS
  employ the MIDI Sample Dump Standard."
- The Sampler Test passing proves little: its identity request goes to device ID `7F` (everything), so it shows the cables work, not that
  the unit will take SDS on our channel. With SDS ignored, our sender sees no ACK, falls back to open loop and still shows progress.

**What the pending log should show if this is right:** `send finished` with `acks=0` / `open_loop_fallback=True`, and nothing from the
unit after the outgoing `F0 7E <ch> 01 ...` header. Real ACKs followed by a missing sample would point somewhere else.

## Sources (what was actually read)

| Document | Where | Read |
|---|---|---|
| ASR-10 External Command Specification, rev. 6 Jan 1993, 59 pp | R-Massive Ensoniq documents page (Mega link) | Sections 1-5, 7, 8, 9.1-9.5, 10, Appendix A and B. Scanned, no text layer, no OCR: values were read by eye |
| EPS-16 PLUS External Command Specification v1.30, 62 pp | same | TOC, sections 1-4.2 only (same structure as the ASR-10 one) |
| ASR-X Pro Reference Manual (PDF with text) | audiofanzine.com mirror | MIDI, SysEx, SMDI and disk sections; no sample-dump chapter exists |
| `summitt/EnsoniqEPS16Plus` `eps.js` (web-MIDI EPS-16+ uploader) | GitHub | all of the SysEx code. **No licence file: do not copy code from it** (same stance as `dxzl/akai-s950`) |

**Not available: the ASR-X's own SysEx specification** (the R-Massive page lists it as "currently unavailable"). Also in
`~/Downloads/Ensoniq research/` but not read: the EPS/EPSm, Mirage, TS-10/12, SQ-80, SQ-1/2, VFX, VFX-SD, SD-1, DP/4, ESQ-1 and Fizmo specs
(synths and older samplers, not needed for ASR sample transfer; the Mirage and EPS ones matter only if those models are ever wanted).

**Biggest caveat:** every wire detail below is the **ASR-10's** (and the EPS-16+'s). The ASR-X is a later machine with a different data
model ("waves" and "sounds" and a Scratch Pad, not instruments, layers and wavesamples). Whether its SysEx matches, extends or replaces
the ASR-10 one is unknown until its own spec or a capture turns up. The ASR-10 spec says it applies to the ASR-10 only, "not the Original
EPS or EPS-16 PLUS", yet the two specs are nearly page-for-page identical, so the ASR-10 document is the best available reference.

## Wire format (SPEC, ASR-10 / EPS-16+, section 2-3)

```
F0 0F 03 0n  <command> <body...>  F7
      |  |  |
      |  |  +-- n = "Base MIDI channel minus 1" (0-15)
      |  +----- product ID: EPS / EPS-16 PLUS / ASR-10
      +-------- Ensoniq manufacturer ID
```

- **Channel byte.** The ASR-10 spec says it is the base MIDI channel minus 1. The ASR-X Pro manual instead has a separate **"SysEx Device ID"**
  (0-127, default 000) under System/MIDI. Which one the ASR-X wants in that byte is **unknown** (GUESS: the Device ID, default 0). The EPS-16+
  tool sends `00`. The ASR-10 also has a "MIDI SYSEX Enable" system parameter (page `34`, item `12`); a SysEx-disabled unit would ignore us.
- **Word formats.** *12-bit*: two bytes `00xxxxxx 00yyyyyy` (the low 12 bits, high half first). *16-bit*: three bytes `0000HHHH 00hhhhhh 00llllll`
  (4 + 6 + 6 bits, high first). Sample words are **16-bit signed** (SDS words are unsigned, so `sample_to_sds_bytes` is not reusable as is).
- **Offsets** are 20-bit, sent as two 12-bit words = **four bytes, 6 bits each, high first**: `0AAAA` -> `00 0A 2A 2A`. The EPS-16+ spec's example
  says `00200` -> `00 00 08 00`. The **ASR-10 spec prints `00 00 40 00` for the same value: that is a typo** (0x200 >> 6 = 8). Use the arithmetic.
- **Numbering.** Instrument 0-7 and layer 0-7 are zero-based on the wire (the spec's own example calls `00 00` "Instrument 1"); wavesamples are 1-127.
  Each command's body starts with the *edit context*: `00 <inst> 00 <layer> <ws hi> <ws lo>` (all in 12-bit words).
- **No checksum.** Reliability comes from the response and the 2 s timer.

### The conversation (SPEC, section 3)

- **One command in flight each way.** The sender starts a **2 s timer**, must not send another until a response or timeout, and waits the full
  2 s even for the few commands that get none. An open loop (one cable) is allowed: the sender just waits the timer out. The receiver should
  answer a command that arrives while it is busy with NAK.
- **RESPONSE (`01`)**: body `00 0a`, `a` = status. **WAIT (`01`)**: the receiver needs longer; the sender restarts a **30 s** timer and answers
  with ACK to continue or **CANCEL (`02`)** to abort (CANCEL is only valid in WAIT mode).
- **PUT commands that carry a block go in two messages** (command + context, then the data alone). The receiver ACKs the first, the sender sends
  the second, the receiver ACKs that. The unit never retransmits the second part.
- **GET**: we send `GET`, the unit answers ACK then a PUT (first part), we ACK, it sends the data part, we ACK. The unit itself never sends a GET.

### Status codes (SPEC, section 5, hex)

`00` ACK, `01` WAIT, `02` insert system disk, `03` invalid parameter number, `04` invalid parameter value, `05` invalid instrument, `06` invalid layer,
`07` layer in use, `08` invalid wavesample, `09` wavesample in use, `0A` invalid wavedata range, `0B` file not found, `0C` memory full,
`0D` instrument in use, `0E` no more layers, `0F` no more wavesamples, `10` reserved, `11` wavesample is a copy, `12` zone too big,
`13` sequencer must be stopped, `14` disk access in progress, `15` disk full, `16` loop too long, `17` NAK, `18` no layer edit, `19` no more
pitch tables, `1A`-`1D` cross-fade / loop-position errors, `1E` quiet layer.

### Commands a sample send needs (SPEC, section 4)

| Code | Name | Body (after the edit context) | Notes |
|---|---|---|---|
| `03` / `05` | GET INSTRUMENT / GET WAVESAMPLE PARAMETERS | none | Answer: ACK, then a PUT. GUESS: a missing instrument answers status `05` (invalid instrument), which would be **the only way to see which instruments exist** (no list command was found) |
| `15` | CREATE INSTRUMENT | none | New instrument, no layers. Must not already exist |
| `16` | CREATE LAYER | none | One new layer **with one wavesample**; must not already exist |
| `19` | CREATE WAVESAMPLE | none | Adds a wavesample holding a single-cycle square wave |
| `1E` | TRUNCATE WAVESAMPLE | none | Drops data before start / after end (TOOL: used to shrink the placeholder before an upload) |
| `0E` | PUT WAVESAMPLE PARAMETERS | 139 words, 16-bit format, value in the high byte | Name, root key, fine tune, filters, loop mode, offsets, **sample rate** (below) |
| `0F` | PUT WAVESAMPLE DATA | start offset (4 bytes), end offset (4 bytes), then the 16-bit signed words | Second message = data only |
| `11` | PUT PARAMETER | param page, param item, then the value as two 12-bit words | No response unless the number or value is bad. Absolute wave offsets live here (hex wire bytes `20`: start `15`, end `16`, loop start `17`, loop end `18`, loop position `19`, loop mode `00`) |
| `14` | SAVE INSTRUMENT | -- | Writes to the current disk directory and **always overwrites a file of the same name without warning**. Never send it |
| `1C`, `17`, `1A` | DELETE instrument / layer / wavesample | -- | Never send from the app without a deliberate user action |

WaveSample parameter block (SPEC, section 7.3, word offsets, value in the high byte unless noted): 0-11 name (one ASCII char per word), 12/13 copy
number/layer, 14-79 three envelopes of 22 words, **80 root key (MIDI note)**, **86 fine tune**, 114 loop mode (Table 8: 0 forward no loop, 1 backward
no loop, 2 loop forward, 3 bidirectional, 4 loop and release), **115-118 start, 119-122 end, 123-126 loop start, 127-130 loop end** (each a 32-bit field
spread over four words' high bytes, *shift right 9 for the word offset*; loop end is shifted 5 to keep a 4-bit fraction), **131 sample rate**
(*"sample period = rate x 1.6 microseconds"*), 132/133 key range lo/hi. Layout notes and the full table: spec pp.36-38.

### Time on the wire (our arithmetic, GUESS about overheads)

A 16-bit word is 3 bytes; MIDI carries about 3125 bytes/s, so about **1040 words/s**. A 10 s mono sample at 44.1 kHz is 441,000 words, about **7 minutes**
before any per-chunk waiting. The EPS-16+ tool pauses 700 ms after every message and sends 5000 words per chunk (its own choices, not limits; the spec
states no maximum data length). The spec also says that with a SCSI cable attached the unit expects the data over SCSI: for a GET it waits 1 s for a
SCSI read before sending over MIDI, and for a PUT it listens on SCSI and MIDI at once (Appendix A). Worth knowing if a tester has a SCSI card installed.

## What the EPS-16+ tool does (TOOL, for sequencing only)

Create instrument (`15`) -> create layer (`16`) -> create wavesample (`19`, the square wave) -> PUT PARAMETER loop mode / loop and sample positions ->
`TRUNCATE` (`1E`) -> PUT WAVESAMPLE DATA in 5000-word chunks at rising offsets (each a command, ACK, data, ACK) -> final ACKs -> PUT PARAMETER sets
the loop end to the sample length. It needs an existing instrument/layer/wavesample first and says "if you start getting NAK messages just refresh
and try again". **How a wavesample grows past its placeholder length is not stated in the spec; the tool relies on the PUT range doing it** (open
question 4).

## Proposed design (not started)

A new **protocol family** `"ensoniq"` and Sampler Type `ensoniq_asr_eps` ("Ensoniq EPS/ASR (experimental)"), following the S950 rules from AGENTS.md:
**nothing may fall through to the akai/generic branches** (`SamplerController` delegates the Ensoniq entry points and refuses the rest, like
`_s950_not_supported`).

| Piece | File | Notes |
|---|---|---|
| Codec | `core/ensoniq_sysex.py` | 12/16-bit words, offsets, framing, status codes, parameter-block pack/unpack. Pure, golden tests from the spec's own examples (`0AAAA` -> `00 0A 2A 2A`, `00200` -> `00 00 08 00`, the PUT PARAMETER examples on spec pp.39-40) |
| Engine | `controller/ensoniq_transfers.py` | ONE operation at a time, event-driven on the GUI thread like `S950Transfers`/`YamahaSession`; 2 s command timer, 30 s WAIT mode, NAK handling, chunked data. Logs every command and response (`SysexTrace` already covers the raw bytes) |
| Fake | `core/demo_ensoniq.py` | Fakes the MIDI ports (like `FakeS950`), enforces one-in-flight and the status codes, so the real engine runs on top |
| Models | `core/sampler_models.py` | New constant, `FAMILY_ENSONIQ`, `is_ensoniq()` |
| UI | Dashboard | Send only at first; "Open Editor" stays disabled; no memory bar; same one-time experimental warning as the S950 |
| Tools | `tools/ensoniq_discovery.py` | **Read-only** (identity, GET INSTRUMENT 0-7, GET PARAMETER on system settings). Request/reply with one outstanding request, stop after a few unanswered reads, per the Real-hardware safety rules |
| Test plan | `tests/ensoniq_test_plan.md` | What a tester does, in the order below |

**Send flow (proposed):** discovery read -> find the first instrument number that answers "invalid instrument" -> `15`, `16`, then the data and parameters
-> read the wavesample parameters back and compare. **Never overwrite**: create only in an unused instrument, never send `14`, `1C`, `17` or `1A`
from a send. 16-bit mono only (stereo = two layers, companion pair; defer). The spec's error codes (`13` sequencer must be stopped, `14` disk
access in progress, `0C` memory full) become short user messages; the log gets the code.

**Order of work:** (0) the pending log and a discovery capture from an ASR-X; (1) codec + golden tests; (2) fake + engine + send; (3) hardware
test on the owner of an EPS-16+/ASR-10 if one appears (the ASR-X path may differ); (4) receive (`06`, GET WAVESAMPLE DATA) if wanted. SMDI over SCSI is
out of scope (no SCSI stack here).

## Open questions (each needs the ASR-X's own spec or a capture)

1. **Does the ASR-X speak the ASR-10 protocol at all?** `0F 03` product ID? Its manual references an "ASR-X Pro SysEx Specification" we don't have.
   The RCS (Chicken Systems) news says an ASR-X sequencer track can transmit SysEx "for any ENSONIQ product" and that an ASR-X parameter dump of an
   EPS/ASR-translated sound is incomplete, which hints at an ASR-10-like parameter format but proves nothing about wave data.
2. **Channel byte:** base channel minus 1 (ASR-10 spec) or SysEx Device ID (ASR-X manual)?
3. **Where does a send land on the ASR-X?** Its Scratch Pad holds waves, not instruments; the instrument/layer/wavesample commands may map to a sound
   plus wave, or not exist.
4. **How a wavesample is lengthened** by PUT WAVESAMPLE DATA (the tool's `TRUNCATE`-then-PUT order), and what the unit does with a range past its end.
5. **Sample rate field:** what value to write for 44.1 / 30 kHz (the ASR-10 spec: period = rate x 1.6 us, range 1-127; page `20` item `0D` in the
   parameter table, marked "receive only", which per the spec's Note 1 only means the unit doesn't transmit it when edited from the front panel).
   Resample to the unit's own rate or tell it ours? Unknown.
6. **Maximum PUT data length per message**, and whether a 7-minute transfer survives the unit's own timeouts.
7. Memory limits: the instrument size parameter counts 256-word blocks (spec p.34). **Free RAM is readable: GET PARAMETER `34 00` "Free System Blocks"** (see the cross-check; the block unit is still a guess).
8. Whether names need uppercase/limited characters (the spec says 12 ASCII bytes).

## What to ask the ASR-X tester

OS version (System/MIDI or the splash screen); the SysEx Device ID and base channel they have set; whether MIDI SysEx is enabled; whether the unit has
the SCSI option and a drive attached; the AKAISDS log from the Generic SDS attempt (`~/.akaisds/akaisds.log`, taken straight after) and the exact
`send finished` line; whether the ASR-X's own manual lists any "MIDI sample dump" or SysEx data-transfer option in its menus.

## Licensing and references

- Ensoniq's specs are copyright documents, **not in the repo** (like the Akai manuals): the repo names them and says which pages matter. Source:
  <https://zine.r-massive.com/ensoniq-technical-documents-and-schematics/>.
- ASR-X Pro Reference Manual: <https://medias.audiofanzine.com/files/asrx-pro-reference-manual-476445.pdf> (pp.86 SMDI, 97 SysEx Device ID, MIDI chart).
- `summitt/EnsoniqEPS16Plus` has **no licence**: read for sequencing only, nothing copied. Implement from the Ensoniq spec.
- *Music Technology*, Oct 1988, "Ensoniq EPS": <https://www.muzines.co.uk/articles/ensoniq-eps/371>. Chicken Systems ASR-X news (SMDI arrived in OS 2.53):
  <http://www.chickensys.com/news/asrx/asrxnews.html>.
