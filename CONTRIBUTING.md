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
- Your sampler type (Akai or Generic SDS and the name of your hardware)
- Your MIDI interface
- Whatever the app's status bar/console output displayed at the time

## Contributing code

#### Setup

```
git clone https://github.com/martincurkovic/AKAISDS
cd AKAISDS
pip install -r requirements.txt
pip install -r requirements-dev.txt
``` 
It is also recommended you create a `venv` when installing dependencies. I'll leave that choice up to you tho.

#### Before you open a PR

Run the test suite - see TESTING.md for details

`pytest tests/ -v`

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

By contributing you agree that your contribution is licensed under the same terms as the rest of the project (that being LGPLv3 - see `LICENSE` and `COPYING`).

## For maintainers…

Well ok it's just me rn, but documenting it anyway just in case.

Releases are cut by pushing a version tag, which triggers the whole build + release pipeline.
```
git tag v1.0.0
git push origin v1.0.0
```
That builds for all platforms currently supported. If all tests and builds pass, then a GitHub Release is created with all the artifacts attached. See BUILDING.md for the manual build process.