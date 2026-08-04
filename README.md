# AKAISDS

---

A simple way to send and receive audio samples (WAV, AIFF, FLAC) to an
Akai S1000/S2000/S3000 series sampler over
MIDI using the sample dump standard (MIDI SDS). Also supports generic MIDI SDS
transmission for non-Akai samplers.

Note that some USB MIDI interfaces do not support MIDI SysEx messages.
Please check your MIDI hardware supports SysEx transmission. AKAISDS contains
a MIDI SysEx loopback test to assess MIDI interface compatibility. Otherwise
please check the table below for a list of tested hardware.

|Interface|macOS|Linux|Windows|Notes|
|---|---|---|---|---|
|   iCON MIDIPORT V1.01  |❌ |  ✅* |  ？ |  Not supported under macOS. Max SysEx length = 256 bytes |
|  MIDIPLUS MIDI 2x2 |  ✅* |  ✅* |  ？ |  Max SysEx length = 256 bytes |
|   PreSonus Studio 26  |  ✅ |  ✅ |  ？ |   |

Currently only closed loop transmission is supported (ie, ACK/NAK handshakes
are required by the receiving hardware). Support for open loop communication
(ie, no handshaking) is planned and currently in progress.

Written in Python using Qt6 and mido.

---
