# Ensoniq spec cross-check (EPS 1989 vs the plan)

Written 2026-10-15. Step 1 of `ensoniq-support-plan.md`. **Research only, nothing built, no Ensoniq unit tried.**

The EPS and EPSm External Command Specification (MKB2, 12 June 1989, 50 pp) is the only sampler spec in `~/Downloads/Ensoniq research/` with a
text layer (OCR: typos like "Paranieter" and "Toe EPS" are in it, so a value is trusted only where a second source or the arithmetic agrees).
I read all of it and compared it with the plan, which was written from the scanned ASR-10 and EPS-16+ specs. I also looked again at ASR-10
pp.8, 9, 41 and 44 as images. Grades as in the plan: **SPEC**, **TOOL**, **GUESS**.

## Confirmed (the plan's by-eye reading of the ASR-10 matches an independent source)

- Framing `F0 0F 03 0n`, 12-bit and 16-bit words, the 2 s and 30 s timers, RESPONSE (`01`) and CANCEL (`02`), the two-message PUT pattern.
- Status codes `00`-`1E`: the same names and numbers as the plan.
- Command codes: `03`-`08` GET, `0C`-`0F` and `10` PUT, `11` PUT PARAMETER, `15`/`16`/`19` CREATE instrument/layer/wavesample, `17`/`1A`/`1C` DELETE,
  `1E` TRUNCATE.
- Wavesample block (139 words, 0-138): name 0-11, copy 12/13, root key 80, fine tune 86, loop mode 114, start 115-118, end 119-122,
  loop start 123-126, loop end 127-130 (shift 5, 4-bit fraction), sample rate 131, key range 132/133. Every offset the plan quotes matches.
- The offsets-example typo is real, and both older specs have it. **The EPS and ASR-10 specs print `00 00 40 00` for 0x00200; the EPS-16+ spec prints
  `00 00 08 00`.** Use the arithmetic: the EPS spec's own scale-factor examples split a value the same way and agree with it (1.0 = 0x0100 ->
  `00 00 04 00`, 0xFF7F -> `00 0F 3D 3F`), and `40` is not a legal 6-bit byte.

## New, or corrects the plan

1. **Parameter numbers are ONE 12-bit word, not "page byte, item byte".** `word = page << 8 | item`, sent as `(page << 2 | item >> 6)`, `(item & 0x3F)`.
   The plan's "page, item" reading is only equal for items below 64 (every item in the wavesample table is). The ASR-10 prints pages and items in
   hex (system `34`, wavesample `20`, items `15`-`19` for the absolute offsets), the EPS prints decimal items and a "high byte x 4" rule: the same wire values.
   So the plan's "page `34` item `12`" and "page `20`: start `15`..." are hex wire bytes. Values are 24-bit, sent as two 12-bit words.
2. **Parameter item numbers differ between the EPS and the ASR-10, so each model needs its own table.** MIDI SysEx Enable is page `30` item `05`
   on the EPS and page `34` item `12` (hex) on the ASR-10. The wavesample page (`20`) lines up on both for the items checked (loop mode `00`, absolute
   start/end/loop start/loop end `15`-`18`, loop position `19`, sample rate `0D`).
3. **Open question 7 (free memory) is mostly answered.** GET PARAMETER `34 00` is "Free System Blocks" (read only, 0-10000) on BOTH, and
   `34 0A` is "Free Disk Blocks" on the ASR-10 (EPS: item 1). SPEC for the parameter; GUESS that the unit is the instrument-size one (256 words per block).
   That is a pre-send memory check, and the instrument block's word 15 (total size in blocks) gives each instrument's size.
4. **Names and a sample list are available.** Instrument block (323 words): name 0-11 (one ASCII char per word, value in the HIGH byte), size 15.
   Layer block (107 words): name 0-11. So a list is GET INSTRUMENT / GET LAYER / GET WAVESAMPLE PARAMETERS. Still no list command, so
   instruments 0-7 are probed one by one.
5. **Status `05` is defined as "the instrument doesn't exist, i.e. is not loaded".** That upgrades the plan's guess about how to find a free
   instrument from "invented" to "the documented meaning of the error". Whether GET INSTRUMENT on an empty slot really returns it is still unverified.
6. **A single data range is limited to 20 bits.** GET/PUT WAVESAMPLE DATA offsets are 0-FFFFF (SPEC on both specs). If they count words (the
   block's "shift right 9 for word offset" suggests so, GUESS), one wavesample is addressable up to 1,048,575 words, about 23.8 s at 44.1 kHz.
   The absolute PUT PARAMETER offsets are 24-bit (0-FFFFFF). **The unit of that 24-bit value is not stated** (GUESS: 32-bit block field >> 8 would count
   bytes, not words). Prefer setting offsets through the PUT WAVESAMPLE PARAMETERS block (>> 9 = words) and read them back before trusting either.
   Whether an ASR-10 wavesample can be longer than 2 MB is unknown. Until a tester shows otherwise, refuse longer audio with a short message.
7. **Wavesample number 0 in PUT PARAMETER means "every wavesample on the layer"** and is refused for offsets with status `18`. Never use `0` in
   a send. (The EPS parameter table lets Edit Wavesample run 0-128.)
8. **SAVE INSTRUMENT (`14`) is not an EPS command** ("unused code" in the 1989 spec). It is in the later specs. Still never sent.
9. **Receive timing (ASR-10 p.8):** after a GET, "if EXT then sends an ACK before the 2 second timeout limit, the ASR-10 will respond by
   sending the data block". The receive engine must ACK the first part promptly: no work on the GUI thread between the two messages.
10. **The unit never retransmits the second part of a PUT** (EPS section 8, step 6): a bad data block means starting the GET again.
    A receive therefore verifies each chunk's length against the range asked for and re-requests the whole chunk on any error.
11. Neither unit ever sends a GET (commands `03`-`0A`).

12. **The sample-rate field is coarse (open question 5, partly answered).** Word 131 is a period of 1.6 microseconds per step (SPEC, section 7.3),
    so a rate is 1,000,000 / (code x 1.6) Hz: code 20 = 31,250 Hz, code 12 = 52,083 Hz (GUESS: these match the EPS's own rates, from memory).
    **44.1 kHz is not representable**: the nearest codes are 14 (44,643 Hz, about 1.2% sharp) and 15 (41,667 Hz). A send must resample to a
    code's exact rate or accept a tuning error. Which codes a unit accepts (0-127 on paper) and which it plays properly is still unknown.
    `ensoniq_sysex.rate_code_to_hz` / `hz_to_rate_code` encode the formula, tested.

## Not cross-checked

- The EPS-16+ spec (read only to section 4.2) and the ASR-10 blocks in sections 7.1-7.3 were not re-read this time. The EPS text confirms the
  plan's wavesample numbers, but ASR-10-only fields (stereo layers, effects, sequences) are not in the EPS spec and the plan has not covered them.
- Nothing about the ASR-X. Still no spec.
- The sample-rate field's behaviour on a unit (item 12 only gives the formula), how a wavesample grows past the CREATE WAVESAMPLE placeholder (4), and the channel byte (2) are not
  answered by any of the three specs.

## Consequences for the design

- The codec needs a **per-model parameter table** (EPS vs ASR-10) and a one-word parameter-number encoder. The wavesample-block layout is shared.
- The sample list and names come from reads only; a send plan can show free memory before it starts.
- A UI length limit near 1M words, until a tester shows more.
- The golden tests can use: `0AAAA` -> `00 0A 2A 2A`, `00200` -> `00 00 08 00`, the EPS scale-factor examples above, and parameter `34 00`
  (Free System Blocks), `30 05` (EPS SysEx Enable), `20 15` (wavesample start).
