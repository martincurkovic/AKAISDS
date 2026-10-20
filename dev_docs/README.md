# dev_docs

Long-form working documents for people and agents developing AKAISDS - not user documentation (that is `README.md`, `BUILDING.md`,
`TESTING.md` in the repo root). Start with `AGENTS.md`; these are the evidence and plans it points to.

| File | What it is |
|---|---|
| `a4000-editor-roadmap.md` | Yamaha A4000/A5000 editor: measured protocol facts (real unit), open items, hardware tools, manual page map. **Active.** |
| `a4000-native-load-findings.md` | How native sample loading (wave + sample bulk dumps) behaves, as measured. Implemented. |
| `s1000-delk-findings.md` | Akai S1000 memory layout and the two failed DELK tests behind AGENTS.md's "never DELK" rules. |
| `s950-support-plan.md` | Akai S900/S950 protocol notes, design facts, guesses and open questions. Built, never run on hardware. |
| `s950-notes.md` | S900/S950 engineering notes (transfers, program editor, Samples tab, s950tools' caveats, pitfalls) moved out of AGENTS.md. |
| `s950-sample-editing.md` | What sample editing could look like on an S900/S950 (new samples only, no delete): what is possible, limits, order of work. Analysis only, nothing built. |
| `yamaha-a4000-notes.md` | Yamaha A4000/A5000 editor engineering notes (wire rules, writes, edits, native loading, UI decisions) moved out of AGENTS.md. |
| `ensoniq-notes.md` | Ensoniq EPS/ASR engine, sending sequence, assumptions a real unit must confirm, moved out of AGENTS.md. |
| `asrx-notes.md` | Ensoniq ASR-X Dashboard dialect: what it does, the free-slot rule, device-ID fallback, unknowns, moved out of AGENTS.md. |
| `s700-rom-findings.md` | Akai S700 SysEx as read from the v2.1 and v1.6 ROMs (Z80): message set, sizes, differences from the S950. Static reading, nothing built or run. |
| `s612-rom-findings.md` | Akai S612 SysEx as read from the v1.1 ROM (Z80): a small ASCII-letter dialect that moves whole sample memory and a 32-byte parameter block. Static reading, nothing built or run. |
| `asrx-rom-findings.md` | Ensoniq ASR-X SysEx as read from the v2.67 ROM (68k): no MIDI SDS at all, its own `F0 0F 10 01` object protocol (samples are `SMP1` objects), and why the ASR-10 dialect in `core/ensoniq_sysex.py` can't talk to an ASR-X. Static reading; the Dashboard dialect built from it (Sampler Type `ensoniq_asrx`) has never run on a unit. |
| `ensoniq-support-plan.md` | Ensoniq EPS/ASR sample transmission: the proprietary SysEx format (from the ASR-10 / EPS-16+ specs), why Generic SDS can't work, the design and open questions. Built (list, receive, send), never run on hardware. |
| `ensoniq-spec-crosscheck.md` | The EPS spec's text layer checked against the plan: what matched, and the corrections (parameter numbers, the 20-bit range limit, the memory parameters, the rate code). Read before touching `core/ensoniq_sysex.py`. |
| `s3000-multi-file-format.md` | The S2000/S3000XL multi file (4096 bytes) and what a Save/Load Multi would need. Research only - no code. |
| `macos12-support-handoff.md` | What blocks macOS 12 (Qt 6.11, NumPy wheel), the experiments and the plan. Not started. |
| `packaging/` | Draft Homebrew / AUR packaging and a publish workflow with `PACKAGING.md`. Not active. |

Scripts for poking at hardware or running against a fake sampler are in `../tools/`; each has a docstring with its steps. `test_scripts/` (gitignored)
is local scratch space for copyrighted manuals and one-off experiments. Vendor manuals (Akai, Yamaha) are not in the repo; the docs name them and
say which pages matter.
