# AKAISDS
---
A simple way to send and receive audio samples (WAV, AIFF, FLAC) to an Akai S1000/S2000/S3000 series sampler over MIDI using the sample dump standard (MIDI SDS). Also supports generic MIDI SDS transmission for non-Akai samplers.

## Features
- Akai-native sample browsing & management (list, rename, delete) as well as universal generic SDS support for non-Akai hardware
- WAV/AIFF/FLAC support up to signed 32 bit. Support for 32 bit float files is not fully tested at this time.
- Drag-and-drop support for adding samples
- Batch transmission queue for sending and receiving samples
- Open-loop fallback for one-way MIDI cable setups, including timeout detection if a cable is unplugged mid-transfer
- Built in MIDI loopback diagnostics, for testing whether your MIDI interface can support MIDI SysEx traffic
- Native builds for macOS (Apple Silicon & Intel), Linux and Windows (note that Windows support has not been thoroughly tested yet)

---
## Download & Installation
TODO: (link to download goes here, update once first build is done)

#### macOS Instructions:
Unzip the download and drag AKAISDS to your applications folder. See the disclaimer section below for instructions about how to bypass Gatekeeper security.

#### Windows Instructions:
Unzip the download and run AKAISDS.exe from wherever you downloaded it, _or_ move it to anywhere conventient like your Desktop or Documents folder. I am in the process of planning out creating a proper Windows installer, but I'm not much of a Windows user. If you happen to know more about creating Windows installers, your contribution would be much appreciated. See the disclaimer section below for instructions about how to bypass Windows SmartScreen & Defender.

#### Linux Instructions:
Unzip the download and run the AKAISDS.bin file from any directory. Depending on your distribution you may need to `chmod +x AKAISDS.bin` to allow execution. No security tweaks should be necessary, at least not on Ubuntu 26.04 LTS. I am currently looking at creating AKAISDS in different packaging formats. If you are knowledgeable about Linux packaging formats, I would LOVE a hand. 

---
## Quick Start & Screenshots
TODO: (insert a short walkthru with screenshots)

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
2. Fine, if you insist on Windows: open Windows Security → Virus & threat protection → Protection history, find AKAISDS in the list and restore it.
3. To stop it happening on every future download: same app → Manage settings → Exclusions → Add an exclusion → File → Select AKAISDS.exe
4. Alternatively you can build AKAISDS on your machine locally and it _might potentially_ bypass Windows Defender, but YMMV

Generic SDS mode has been implemented and tested via simulation but only has limited verification on real hardware. Bugs will likely be present, so if you encounter one please log an issue on GitHub and be as descriptive as possible :) 

Note that some USB MIDI interfaces do not support MIDI SysEx messages. Please check your MIDI hardware supports SysEx transmission. AKAISDS contains a MIDI SysEx loopback test to assess MIDI interface compatibility. Otherwise please check the table below for a list of tested hardware. If you've tested your interface, please feel free to contribute and add your findings to the table below.

|Interface|macOS|Linux|Windows|Notes|
|---|---|---|---|---|
|   iCON MIDIPORT V1.01  |❌ |  ⚠️ |  ⚠️ |  Max SysEx 256 bytes |
|  MIDIPLUS MIDI 2x2 |  ⚠️ |  ⚠️ |  ⚠️ |  Max SysEx 256 bytes |
|   PreSonus Studio 26  |  ✅ |  ✅ |  ⚠️ (max 512 bytes) | Requires driver on Windows  |

---
## License & Acknowledgements
Written in Python using PySide6 (Qt), mido, python-rtmidi, soundfile and Nuitka.

This project would not have been possible without the amazing work of Frank Neumann who transcribed the entire Akai SysEx implementation by hand from printed documentation. [That documentation can be found here](https://lakai.sourceforge.net/documentation.shtml.html)

_Here I stand on the shoulders of giants._

I make exactly zero dollars from AKAISDS. If you find this project useful in any way shape or form, I would appreciate any support you can offer, be it by spreading the word of AKAISDS, donations via Ko-Fi (TODO: insert link here) or by listening to my music (TODO: insert link here) on whatever platform works best for you (sketchy torrent sites included).

Much love,
Martin Curkovic

---
