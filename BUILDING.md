# Building From Source

## Prerequisites

- Python 3.12 or higher
- A C++17 compiler: the build scripts compile the small REX2 reader (`third_party/velociloops`, via `tools/build_velociloops.py`) for your machine
- Other dependencies are in the `pyproject.toml` file which will be installed automatically when running one of the build scripts below

#### macOS

- Xcode Command Line Tools

#### Linux

- `build-essential` and `patchelf` (can be installed with `sudo apt install build-essential patchelf` on Ubuntu-based distributions)

#### Windows

- A C++ compiler

## Setup and Build

1. Clone repository with `git clone https://github.com/martincurkovic/AKAISDS.git`
2. Run the relevant script for your platform (located in the `scripts` folder). Note that running a PowerShell script on Windows for the first time will trigger a warning but it can be mitigated by running `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` before running the build script. On Linux or macOS you _may_ need to run `chmod +x build_xxx.sh` on the relevant script for your platform
3. Final build will land in the `src` directory

## Running Without a Sampler

The app, the editors and the demo samplers can all be run from source without any hardware - see "Running the app without a sampler" in [CONTRIBUTING.md](CONTRIBUTING.md) (`uv run python tools/demo.py list`, `AKAISDS_DEMO_SAMPLER=1 uv run python src/main.py`, and `--instant` to skip the MIDI-speed emulation).

## Running Tests

There's a pytest suite in the `tests` directory (about 3-4 minutes) - see [TESTING.md](TESTING.md). Sync the environment first with `uv sync`, then run `uv run pytest tests/ -q`.
