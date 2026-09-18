# docs/s2000_parameter_verification.md

Fields confirmed against a real Akai S2000, since s3ked's own testing has
never included this model. "Direct" means the value read via s3k matches
exactly what the panel shows, no translation needed.

| Parameter | Region   | Result | Notes                                                                 |
|-----------|----------|--------|------------------------------------------------------------------------|
| PRNAME    | program  | direct | program name string                                                    |
| LONOTE    | keygroup | direct | raw MIDI note number, not note name                                    |
| HINOTE    | keygroup | direct | same as LONOTE                                                          |
| POLYPH    | program  | ?      | table has display_offset=1 - confirm whether get_parameter already applies it |
| PMCHAN    | program  | ?      | table has an OMNI sentinel at 255 - confirm whether get_parameter returns "OMNI" or raw 255 |
| FILFRQ    | keygroup | direct | 0-99, matches panel exactly                                             |
| FILQ      | keygroup | direct | 0-15, matches panel exactly                                             |
| ATTAK1    | keygroup | direct | filter envelope attack, 0-99, matches panel                            |
| DECAY1    | keygroup | direct | filter envelope decay, 0-99, matches panel                             |
| SUSTN1    | keygroup | direct | filter envelope sustain, 0-99, matches panel                           |
| RELSE1    | keygroup | direct | filter envelope release, 0-99, matches panel                           |
| ATTAK2    | keygroup | direct | amp envelope attack, 0-99, matches panel                               |
| DECAY2    | keygroup | direct | amp envelope decay, 0-99, matches panel                                |
| SUSTN2    | keygroup | direct | amp envelope sustain, 0-99, matches panel                              |
| RELSE2    | keygroup | direct | amp envelope release, 0-99, matches panel                              |
| PANPOS    | program  | direct | -50 to +50, 0 = center, matches panel exactly                         |
