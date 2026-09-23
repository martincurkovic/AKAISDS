# Quick Start & Screenshots

**1. Connect your sampler and open MIDI Settings**

Select your MIDI Input/Output ports and choose your sampler type (Akai or Generic SDS). You can test your MIDI setup in the **MIDI Hardware Test** tab. If you're having connectivity issues, you can check your interface's MIDI SysEx compatibility by doing a loopback test in the **MIDI Interface Test** tab. Not all MIDI interfaces are able to support SysEx messages.

![MIDI Settings](screenshots/midi-settings.png "MIDI Settings")

**2. Drag audio files into the queue**

WAV, AIFF and FLAC are all supported. Dragging in entire folders works too.

![File Queue](screenshots/file-queue.png)

**3. Adjust quality settings (optional)**

You can set quality globally via the Transmission Settings button or individually via the Edit button next to each sample. Reducing quality will significantly reduce the transfer time (MIDI Sample Dumps are notoriously slowwwww 🐌).

![Transmission Settings](screenshots/transmission-settings.png)

**4. Hit Send!**

If you've never used MIDI sample dumps before, expect them to be slow. Very slow. Reducing bit depth or sample rate will help speed up the transfer, at the expense of sound quality.

![Main Window Dark Mode](screenshots/main-window-dark.png)

**Approximate transfer times for a 5-second sample:**

|  Sample Rate |  16-bit |  8-14 bit |
|---|---|---|
|  44100 Hz |  ~3.7 min |  ~2.5 min |
|  30000 Hz |  ~2.5 min |  ~1.7 min |
|  22050 Hz |  ~1.9 min |  ~1.2 min |
|  15000 Hz |  ~1.3 min |  ~50 sec |
|  11025 Hz |  ~56 sec |  ~37 sec |
|  8000 Hz |  ~41 sec |  ~27 sec |

Note that **bit depth** is a two-tier step, not a smooth scale. An 8-bit sample will take just as long as a 14-bit sample to transfer. So there is no benefit to reducing to less that 14-bit specifically for speed. Reducing **sample rate** does scale linearly. Half the sample rate means half the transfer time, but at the expense of higher frequency audio content.

These are theoretical best-case figures based on MIDI's fixed wire speed of 31,250 baud. Real world transfers may be slower, depending on how quickly your specific sampler responds to each data packet.
