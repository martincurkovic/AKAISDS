# AKAISDS

[![Build](https://github.com/martincurkovic/AKAISDS/actions/workflows/build.yml/badge.svg)](https://github.com/martincurkovic/AKAISDS/actions/workflows/build.yml)
[![Downloads](https://img.shields.io/github/downloads/martincurkovic/AKAISDS/total?label=downloads)](https://github.com/martincurkovic/AKAISDS/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Qt 6.11](https://img.shields.io/badge/Qt-6.11-41CD52?logo=qt&logoColor=white)](https://doc.qt.io/qtforpython-6/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Platforms: macOS, Windows, Linux](https://img.shields.io/badge/platforms-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey)](#download--installation)
[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue)](LICENSE)

A simple way to send and receive audio samples (WAV, AIFF, FLAC, MP3, Ogg, Opus) to an Akai S1000/S2000/S3000 series sampler over MIDI using the sample dump standard (MIDI SDS), plus an editor for its programs, keygroups, multis and samples. Also supports generic MIDI SDS transmission for non-Akai samplers.

**Need help? Read the [Quick Start Guide](src/ui/help/quickstart.md).**

![Main window dark](src/ui/help/screenshots/main-window-dark.png "Main window dark")

## Features

- Akai-native sample browsing & management (list, rename, delete) as well as universal generic SDS support for non-Akai hardware
- Program, Keygroup, Multi and Sample editor for Akai S1000 and S2000/S3000 series samplers, with draggable envelope graphs and collapsible cards
- Sample editing features: trim, reverse, fade, normalise, filter, time stretch / pitch shift and reverb, with Undo and Revert
- Edit in External App: open a sample in your audio editor of choice and send it back to the sampler
- Audition Keyboard (Cmd/Ctrl+K): play your sampler from the on-screen piano or the computer keyboard
- Per-part key range, transpose, tune and priority on the Multi tab, and save/load Multis as `.m3` files (S2000/S3000)
- Save and load Akai sample files (`.s3`/`.s1`), and save received samples as Akai files
- Experimental Yamaha A4000/A5000 editor support
- Experimental Akai S900/S950 support: sample send/receive plus a program editor
- Experimental Ensoniq sampler support: list, receive and send samples, written from the ASR-X's firmware (it has no MIDI Sample Dump Standard, so Generic SDS cannot work). It has never been tried on a real unit, so back up the sampler first and please report what you find (the log can be found under `Help > Show log folder`)
- ReCycle style sample slicing
  - Transient detection: a fast simple detector, or SuperFlux (high quality) for drum loops
  - Audio preview
  - Generate new Program with slices
- Import ReCycle `.rx2` loops: each slice becomes a sample, optionally with a program built from them
- File > Import Audio in the editors: open any audio file (or `.rx2`) in the Slice Editor to trim, slice and send it, optionally as a program
- Drag program files (.p1/.p3) onto the Akai editor to load them, or drag a program out of the list to save it
- WAV/AIFF/FLAC/MP3/Ogg Vorbis/Opus support
- Audio preview for samples
- Light and dark themes
- Built in MIDI diagnostic tests
  - MIDI interface test to check if your interface can support MIDI SysEx traffic
  - Hardware connection test to check if your MIDI sampler is connected correctly
- Native builds for macOS 15+ (Apple Silicon & Intel), Linux and Windows

## Quick Start & Screenshots

See [Quick Start Guide](src/ui/help/quickstart.md) for a walkthrough of connecting your sampler, queuing up files and sending your first sample dump, along with approximate transfer times per sample rate/bit depth. The same guide is also available in-app via **Help > Quick Start Guide**.

![Editor Program Page](src/ui/help/screenshots/editor-program-page.png)

![Editor Keygroup Page](src/ui/help/screenshots/editor-keygroup-page.png)

![Sample Editor](src/ui/help/screenshots/editor-sample-page.png)

![Audition Keyboard](src/ui/help/screenshots/editor-audition-keyboard.png)

Click a picture below for the full size.

<table>
  <tr>
    <td width="50%" valign="top">
      <a href="src/ui/help/screenshots/editor-multi-page.png"><img src="src/ui/help/screenshots/editor-multi-page.png" alt="Multi tab"></a>
      <br><sub>Multi tab: programs, levels, key ranges and priority for all 16 parts</sub>
    </td>
    <td width="50%" valign="top">
      <a href="src/ui/help/screenshots/editor-sample-edit.png"><img src="src/ui/help/screenshots/editor-sample-edit.png" alt="Sample editing on a working copy"></a>
      <br><sub>Sample editing on a working copy, not yet sent to the sampler</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <a href="src/ui/help/screenshots/slice-editor-auto.png"><img src="src/ui/help/screenshots/slice-editor-auto.png" alt="Slice Editor with automatic slicing"></a>
      <br><sub>Slice Editor: SuperFlux finds the hits in a stereo loop</sub>
    </td>
    <td width="50%" valign="top">
      <a href="src/ui/help/screenshots/slice-editor-manual.png"><img src="src/ui/help/screenshots/slice-editor-manual.png" alt="Slice Editor with hand-placed markers"></a>
      <br><sub>Slice Editor: markers placed by hand</sub>
    </td>
  </tr>
</table>

## Download & Installation

Download the latest release from the Releases tab on GitHub.

#### macOS Instructions

Unzip the download and drag AKAISDS to your applications folder. See the disclaimer section below for instructions about how to bypass Gatekeeper security.

Or install with [Homebrew](https://brew.sh): `brew install --cask martincurkovic/tap/akaisds`. Gatekeeper still applies on first launch.

macOS 15+ support only for now. There are plans to backport AKAISDS to earlier macOS versions, but that work hasn't been started yet.

#### Windows Instructions

Unzip the download and run AKAISDS-Setup.exe to install. It should create a shortcut on your Desktop and in your Start Menu. See the disclaimer section below for instructions about how to bypass Windows SmartScreen & Defender.

#### Linux Instructions

Unzip the download and run the AKAISDS AppImage from any directory. Depending on your distribution you may need to run `chmod +x AKAISDS-x86_64.AppImage` to allow execution. No security tweaks should be necessary, at least not on Ubuntu 26.04 LTS. I am currently looking into packaging AKAISDS in different Linux package formats. If you are knowledgeable about Linux packaging formats, I would LOVE a hand - see [CONTRIBUTING](CONTRIBUTING.md).

## Disclaimer

macOS and Windows builds WILL trigger security warnings. This is because the code hasn't been "signed". For an open source project maintained and developed by a single person (who is also working on this in their free time), paying for a code signing license is difficult to justify right now. I am currently looking in to alternatives and ways around this. For now, the builds are safe (just trust me bro), but if you're concerned, you're welcome to download the source code, inspect it and build it on your own machine.

To bypass macOS Gatekeeper you will need to do the following:

1. Attempt to open the app normally
2. Open System Settings and scroll down to Privacy & Security
3. Scroll down and click "Open Anyway"
4. You will be prompted to authenticate with your password or Touch ID
5. ???
6. Profit

To bypass Windows SmartScreen & Defender you will need to do the following:

1. Delete Windows 11 immediately and switch to Linux
2. Fine, if you insist on Windows: open Windows Security → Virus & threat protection → Protection history, find AKAISDS-Setup.exe in the list and restore it.
3. To stop it happening on every future download: same app → Manage settings → Exclusions → Add an exclusion → File → Select AKAISDS-Setup.exe
4. Alternatively you can build AKAISDS on your machine locally and it _might_ potentially bypass Windows Defender, but YMMV

Generic SDS mode has been implemented and tested via simulation but only has limited verification on real hardware. Bugs will likely be present, so if you encounter one please log an issue on GitHub and be as descriptive as possible :) See [CONTRIBUTING.md](CONTRIBUTING.md#reporting-bugs) for what's helpful to include.

Note that some USB MIDI interfaces do not support MIDI SysEx messages. Please check your MIDI hardware supports SysEx transmission. AKAISDS contains a MIDI SysEx loopback test to assess MIDI interface compatibility. Otherwise please check the table below for a list of tested hardware. If you've tested your interface, please share your findings: the loopback test's results window has a **Share on GitHub** button that opens an issue with everything filled in (or add a row to the table below with a Pull Request).

| Interface           | macOS | Linux | Windows           | Notes                      |
| ------------------- | ----- | ----- | ----------------- | -------------------------- |
| iCON MIDIPORT V1.01 | ❌     | ⚠️     | ⚠️                 | Max SysEx 256 bytes        |
| MIDIPLUS MIDI 2x2   | ⚠️     | ⚠️     | ⚠️                 | Max SysEx 256 bytes        |
| PreSonus Studio 26  | ✅     | ✅     | ⚠️ (max 512 bytes) | Requires driver on Windows |

## Reporting Problems

If something goes wrong, open **Help > Open Log Folder** (in the S900/S950 and Yamaha editors: **Hardware > Open Log Folder**) and attach `akaisds.log` to your bug report, along with which sampler and MIDI interface you're using. Send it straight after the problem happens - it rotates at 8 MB. If the app crashed outright, `crash.log` in the same folder is useful too.

## Building From Source

Want to build AKAISDS yourself or help contribute to the project? See [BUILDING.md](BUILDING.md) for setup instructions, [TESTING.md](TESTING.md) for how the test suite works, and [CONTRIBUTING.md](CONTRIBUTING.md) for everything else

## License & Acknowledgements

Licensed under [GPLv3](LICENSE) - see [LICENSE](LICENSE) for details

Written in Python using PySide6 (Qt), mido, python-rtmidi, soundfile, miniaudio, s3ked and Nuitka.

This project would not have been possible without the amazing work of Frank Neumann who transcribed the entire Akai SysEx implementation by hand from printed documentation. [That documentation can be found here](https://lakai.sourceforge.net/documentation.shtml.html)

Additionally the program and keygroup editor is built on top of the [s3ked](https://github.com/lentferj/s3ked) project by _Jan Lentfer._

The Akai S900/S950 support is partly ported from [s950tools](https://github.com/diemonster/s950tools) by _Brandon Ivers_ (MIT licensed). See [THIRD_PARTY_NOTICES](src/ui/help/THIRD_PARTY_NOTICES.md) for the licence text and exactly which files are derived.

The Time Stretch edit is a port of [potenza-time-stretch](https://github.com/dar-io-p/potenza-time-stretch) by _dar-io-p_ (GPLv3). See [THIRD_PARTY_NOTICES](src/ui/help/THIRD_PARTY_NOTICES.md).

`.rx2` (ReCycle) import uses [VelociLoops](https://github.com/kunitoki/VelociLoops) by _kunitoki_ (public domain), vendored in `third_party/velociloops/`. See [THIRD_PARTY_NOTICES](src/ui/help/THIRD_PARTY_NOTICES.md).

_I stand on the shoulders of giants._

I make exactly zero dollars from AKAISDS. If you find this project useful in any way shape or form, I would appreciate any support you can offer, be it by spreading the word of AKAISDS, [donating via Ko-Fi](https://ko-fi.com/martincurkovic) or by [listening to my music](https://martin.fanlink.tv/putitdown) on whatever platform works best for you (sketchy torrent sites included).

Much love,

Martin Curkovic

---
