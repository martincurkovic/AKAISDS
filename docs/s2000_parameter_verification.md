# docs/s2000_parameter_verification.md

Fields confirmed against a real Akai S2000, since s3ked's own testing has
never included this model. "Direct" means the value read via s3k matches
exactly what the panel shows, no translation needed.

| Parameter | Region   | Result    | Notes                                                                 |
|-----------|----------|-----------|------------------------------------------------------------------------|
| PRNAME    | program  | direct    | program name string                                                    |
| LONOTE    | keygroup | direct    | raw MIDI note number, not note name                                    |
| HINOTE    | keygroup | direct    | same as LONOTE                                                          |
| POLYPH    | program  | decoded   | 1-32, matches panel - get_parameter already applies the +1 display offset |
| PMCHAN    | program  | raw       | 0-15 = MIDI ch 1-16 (add 1 to display), 255 = OMNI - get_parameter returns the raw byte, no translation applied |
| FILFRQ    | keygroup | direct    | 0-99, matches panel exactly                                             |
| FILQ      | keygroup | direct    | 0-15, matches panel exactly                                             |
| ATTAK1    | keygroup | direct    | filter envelope attack, 0-99, matches panel                            |
| DECAY1    | keygroup | direct    | filter envelope decay, 0-99, matches panel                             |
| SUSTN1    | keygroup | direct    | filter envelope sustain, 0-99, matches panel                           |
| RELSE1    | keygroup | direct    | filter envelope release, 0-99, matches panel                           |
| ATTAK2    | keygroup | direct    | amp envelope attack, 0-99, matches panel                               |
| DECAY2    | keygroup | direct    | amp envelope decay, 0-99, matches panel                                |
| SUSTN2    | keygroup | direct    | amp envelope sustain, 0-99, matches panel                              |
| RELSE2    | keygroup | direct    | amp envelope release, 0-99, matches panel                              |
| PANPOS    | program  | direct    | -50 to +50, 0 = center, matches panel exactly                         |
| LFORAT    | program  | direct    | LFO1 speed, 0-99, matches panel                                        |
| LFODEP    | program  | direct    | LFO1 depth, 0-99, matches panel                                        |
| LFODEL    | program  | direct    | LFO1 delay, 0-99, matches panel                                        |
| LFO1WAVE  | program  | untested  | 0=Triangle, 1=Sawtooth, 2=Square, 3=Random - values confirmed to exist on this hardware by panel inspection, not yet read via s3k |
| LFO2WAVE  | program  | untested  | second, independent LFO exists on this hardware - not yet explored via s3k at all |
| PTUNO     | program  | scaled    | raw units are 1/256 semitone, snapped to nearest cent (2.56 raw/cent). Confirmed against real hardware: cents 1/2/3/4 -> raw 2/5/7/10, exactly matching s3ked's documented trunc(cents*2.56) formula. Display as raw/256 semitones. |
| KGTUNO    | keygroup | untested  | keygroup-wide tune offset (same field shape as PTUNO). Distinct from VTUNO1-4, which are per-zone. Location on the S2000's own panel not yet found - what was assumed to be this field was actually VTUNO1 (see below). |
| VTUNO1    | keygroup | identified | zone 1 tuning offset. What was originally mistaken for KGTUNO on the panel - editing "tune" for a specific zone changes this, not the keygroup-wide field. Same trunc(cents*2.56) formula as PTUNO (not yet independently re-confirmed under this name, but same underlying mechanism already verified). |

## Important correction - envelope assignment was backwards

Confirmed directly from the S2000 Operator's Manual (v1.30), not
inferred: **ENV1 is the amplitude envelope** ("a simple ADSR type"),
and **ENV2 is the filter envelope** - a 4-stage, freely-shapeable
rate/level generator (RATE1->LEVEL1, RATE2->LEVEL2, RATE3->LEVEL3
"sustain level", RATE4->LEVEL4), NOT a simple ADSR. Levels can rise or
fall in any combination between stages - the manual shows several
non-monotonic example shapes.

s3ked's ATTAK2/DECAY2/SUSTN2/RELSE2 names are legacy aliases for what
the manual calls RATE1/LEVEL1/RATE2/LEVEL2 etc (confirmed by ATTAK2's
own table note: "also called ENV2R1 in later OS versions").

| Parameter          | Region   | Result | Notes |
|--------------------|----------|--------|-------|
| ATTAK1/DECAY1/SUSTN1/RELSE1 | keygroup | direct | ENV1 = amplitude envelope, genuinely simple ADSR, confirmed by manual |
| ATTAK2/DECAY2/SUSTN2/RELSE2 | keygroup | direct (values match panel) | ENV2 = filter envelope, NOT ADSR-shaped - a 4-stage rate->level generator per the manual's own diagram. Raw values already verified against panel; the SHAPE this app draws from them needs its own widget, not the ADSR graph. |
