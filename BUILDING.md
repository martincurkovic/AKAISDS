# Building From Source

## Prerequisites

- Python 3.10-3.13 recommended. I found that Python 3.14 works but some dependencies may lack pre-built wheels, requiring a C++ compiler to build from source
- Other dependencies are in the `requirements.txt` file which will be installed automatically when running one of the build scripts below
  
#### macOS

- Xcode Command Line Tools

#### Linux

- `build-essential` (can be installed with `sudo apt install build-essential` on Ubuntu-based distributions)

#### Windows

- A C++ compiler - Visual Studio Build Tools is what I used

## Setup and Build

1. Clone repository with `git clone https://github.com/martincurkovic/AKAISDS.git`
2. Run the relevant script for your platform (located in the `scripts` folder). Note that running a PowerShell script on Windows for the first time will trigger a warning but it can be mitigated by running `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` before running the build script. On Linux or macOS you _may_ need to run `chroot +x build_xxx.sh` on the relevant script for your platform
3. Final build will land in the `src` directory

## Running Tests

Right now there is a _very_ basic test suite included in the `tests` directory. These can be run with the `pytest tests/ -v` command. Remember to install the dependencies via: `pip install -r requirements.txt` and `pip install -r requirements-dev.txt`