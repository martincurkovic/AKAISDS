# Contributing to AKAISDS

First of all, thank you for even _considering_ contributing to AKAISDS. This is a small, single person hobby project that just so happened to get enough steam to even make it out into the wild, so the documentation is intentionally short. If something in the documentation is unclear, it's not you, it's me.

## Ways to contribute that aren't code

Given this project talk to physical hardware, often decades old at this point, over MIDI, some of the most useful contributions arent even code related.

- **Testing on real hardware that you own** and reporting back with any relevant findings. Especially on anything outside an Akai S1000/S2000/S3000 family of samplers. Generic SDS implementation in this project has only been very minimally tested, mostly because I don't have access to anything else other than my trusty ol' Akai S2000 :)
- **Adding your MIDI interface to the compatibility table** in the README. If you've run the loopback test on your hardware and platform, I'm sure it would greatly help others who may have the same MIDI interface
- **Bug reports** - see below for what's useful to include in a bug report

## Reporting bugs

Open a GitHub Issue. The more info the better, especially when it comes to anything hardware related. Ideally you should include:

- Your OS and which version of AKAISDS you are running
- Your sampler type (Akai S1000, Akai S2000/S3000, Akai S900/S950, Yamaha A4000/A5000 or Generic SDS, and the name of your hardware)
- Your MIDI interface
- Whatever the app's status bar/console output displayed at the time

## Contributing code

#### Setup

```
git clone https://github.com/martincurkovic/AKAISDS
cd AKAISDS
uv sync
```

#### Running the app without a sampler

You don't need hardware to work on the UI. The "demo samplers" are fakes that speak the real wire format, so the real transfer and editor code runs on top of them; only the MIDI ports are fake. From the repo root:

One launcher runs every demo sampler: `uv run python tools/demo.py <sampler>` (`demo.py list` shows them).

| What you want | Command |
|---|---|
| The **S2000/S3000 Program Editor** on a fake sampler with sample audio | `uv run python tools/demo.py s3000` |
| The **S1000 Program Editor** on `FakeS1000` | `uv run python tools/demo.py s1000` |
| The Dashboard on a fake **Akai S900/S950** (Open Editor for the S950 editor) | `uv run python tools/demo.py s950` |
| The Dashboard on a fake **Yamaha A4000** (Yamaha editor, Samples tab, edits, Slice Editor) | `uv run python tools/demo.py a4000` |
| ...straight into its Program Editor's Samples tab | `uv run python tools/demo.py a4000 --editor` |
| The Dashboard on a fake **Ensoniq EPS/ASR** or **ASR-X** | `uv run python tools/demo.py ensoniq` / `asrx` |
| The whole app with the S1000 / S2000 / S3000 editor on a fake sampler | `AKAISDS_DEMO_SAMPLER=1 uv run python src/main.py`, then pick ports and click Open Editor. Choose the **S1000** Sampler Type to get the S1000 fake instead of the S2000/S3000 one |

On Windows (PowerShell) set the variable first: `$env:AKAISDS_DEMO_SAMPLER = "1"`.

**Speed.** The demos pace themselves like the real wire by default (a Yamaha send waits out MIDI-speed wire time, a link waits several seconds, an S2000 sample load takes about a millisecond per word), because that is what makes them feel real. To skip all of that, add **`--instant`** (to any demo that supports it; `demo.py list` shows which) or set **`AKAISDS_DEMO_INSTANT=1`**; the same Yamaha edit send goes from about 10 seconds to instant.

A few things worth knowing: the Settings dialog is not usable in the A4000/S950 demos (it would reopen real ports); in the `AKAISDS_DEMO_SAMPLER` mode the Dashboard's own Send/Receive still talk to whatever MIDI ports you selected, so only the editors are fake; the demo A4000 starts with "DEMO LOOP", "DEMO STEREO" and "DEMO BREAK" (a loop, a stereo sample and something to slice); and `uv run python tools/make_screenshots.py` regenerates the manual's pictures from the same fakes. The details are in AGENTS.md ("Developing without hardware").

#### Before you open a PR

Run the test suite - see TESTING.md for details

`uv run pytest tests/ -v`

CI won't actually build anything if the tests fail (ie, every build job is reliant on the tests passing first). So if a red X on your PR is present, it might be related to the tests, not necessarily because your PR was rejected.

If you're fixing a real bug, consider adding a regression test for if it fits the existing suite. Again, see TESTING.md for more details and why it might be important.

#### Code Style

No enforced formatter or linter at the moment - just try to be respectful of your code's neighbours :)

#### Submitting

1. Fork the repo
2. Create a branch off `master`
3. Make your change
4. Push and open a PR against `master`

#### License

By contributing you agree that your contribution is licensed under the same terms as the rest of the project (that being GPLv3 - see `LICENSE`).

## For maintainers…

Well ok it's just me rn, but documenting it anyway just in case.

Releases are cut by pushing a version tag, which triggers the whole build + release pipeline.

```
git tag v1.0.0
git push origin v1.0.0
```

That builds for all platforms currently supported. If all tests and builds pass, then a GitHub Release is created with all the artifacts attached. See BUILDING.md for the manual build process.
