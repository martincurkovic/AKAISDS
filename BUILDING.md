# Building From Source
---
## Prerequisites
- Python 3.10-3.13 recommended. I found that Python 3.14 works but some dependencies may lack prebuilt wheels, requiring a C++ compiler to build from source
  
#### macOS:
- Xcode Command Line Tools

#### Linux:
- `build-essential` (can be installed with `sudo apt install build-essential` on Ubuntu-based distributions)

#### Windows:
- A C++ compiler - Visual Studio Build Tools is what I used

## Setup and Build
1. Clone repository
2. Run the relevant script for your platform (located in the `scripts` folder). Note that running a PowerShell script on Windows for the first time will trigger a warning but it can be bypassed by running `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` before running the build script
3. Final build will land in the `src` directory 