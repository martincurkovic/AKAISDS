# dev_docs

Working documents for people (and agents) developing AKAISDS - not user documentation (that is `README.md` /
`BUILDING.md` / `TESTING.md` in the repo root). Start with `AGENTS.md` in the repo root; these are the long-form
plans and handoffs it points to.

| File | What it is |
|---|---|
| `a4000-editor-roadmap.md` | Yamaha A4000/A5000 program/sample editor: research, measured protocol facts (verified on a real unit), the codec's state, seed data for the parameter tables, next steps. **In progress.** |
| `s950-support-plan.md` | Akai S900/S950 support: plan, staging and what was built (written without hardware). |
| `s3000-multi-file-format.md` | Akai S2000/S3000XL multi file (type `0xED`): the 4096-byte file layout (1024-byte header + 16 x 192-byte parts), how it relates to the SysEx layout, sources, what one real file confirmed, hardware gotchas, and what a Save/Load Multi would need. Research only - no code yet. |
| `macos12-support-handoff.md` | Handoff notes for macOS 12 support. |
| `packaging/` | Draft Homebrew / AUR packaging and the `publish-packages.yml` workflow, with `PACKAGING.md` explaining how to wire them up. Not active - the workflow is NOT under `.github/workflows/`. |

Scripts for poking at hardware or running the app against a fake sampler live in `../tools/`
(`a4000_discovery.py`, `s950_demo.py`, `rtmidi_list_ports.py`). `test_scripts/` (gitignored, local only) is
scratch space: it holds copyrighted manuals, a vendored third-party editor and one-off experiments that must not be
committed.

Copyrighted vendor manuals (Akai, Yamaha) are deliberately not in the repo; the docs name them and say which pages matter.
