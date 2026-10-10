# dev_docs

Long-form working documents for people and agents developing AKAISDS - not user documentation (that is `README.md`, `BUILDING.md`,
`TESTING.md` in the repo root). Start with `AGENTS.md`; these are the evidence and plans it points to.

| File | What it is |
|---|---|
| `a4000-editor-roadmap.md` | Yamaha A4000/A5000 editor: measured protocol facts (real unit), open items, hardware tools, manual page map. **Active.** |
| `a4000-native-load-findings.md` | How native sample loading (wave + sample bulk dumps) behaves, as measured. Implemented. |
| `s1000-delk-findings.md` | Akai S1000 memory layout and the two failed DELK tests behind AGENTS.md's "never DELK" rules. |
| `s950-support-plan.md` | Akai S900/S950 protocol notes, design facts, guesses and open questions. Built, never run on hardware. |
| `s3000-multi-file-format.md` | The S2000/S3000XL multi file (4096 bytes) and what a Save/Load Multi would need. Research only - no code. |
| `macos12-support-handoff.md` | What blocks macOS 12 (Qt 6.11, NumPy wheel), the experiments and the plan. Not started. |
| `packaging/` | Draft Homebrew / AUR packaging and a publish workflow with `PACKAGING.md`. Not active. |

Scripts for poking at hardware or running against a fake sampler are in `../tools/`; each has a docstring with its steps. `test_scripts/` (gitignored)
is local scratch space for copyrighted manuals and one-off experiments. Vendor manuals (Akai, Yamaha) are not in the repo; the docs name them and
say which pages matter.
