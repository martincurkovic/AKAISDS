# AKAISDS Help

---

## Contents

- [MIDI Setup](#midi-setup)
- [Using a DAW at the Same Time](#using-a-daw-at-the-same-time)
- [Sending & Receiving Samples](#sending--receiving-samples)
- [Program & Keygroup Editor](#program--keygroup-editor)
- [Yamaha A4000/A5000 Editor](#yamaha-a4000a5000-editor-experimental)
- [MIDI Troubleshooting & Diagnostics](#midi-troubleshooting--diagnostics)

---

## MIDI Setup

**1. Connect your sampler**

Connect your sampler to your MIDI interface. The MIDI output of your sampler should be connected to your interface's MIDI input, and vice versa.

![Connecting a sampler](diagrams/midi-sampler-cable.png "Connecting a sampler")

**2. Configure MIDI Settings**

Select your MIDI Input/Output ports and choose your sampler type: Akai S1000, Akai S2000/S3000, Akai S900/S950 (experimental), Ensoniq ASR-X (untested, experimental), Ensoniq EPS/ASR (untested, experimental), Generic SDS or Yamaha A4000/A5000 (experimental). Set the SysEx Device ID (usually 0; this is not your MIDI channel, and a wrong one gets no reply).

- The **S900/S950** uses its own protocol, needs both a MIDI input and output, and hasn't been tested on real hardware yet - back up your sampler first.
- The **Ensoniq EPS/EPS-16+/ASR-10** uses Ensoniq's own protocol (not Generic SDS), needs both a MIDI input and output, and has never been tried on real hardware - back up the unit first. Set the Device ID to its base MIDI channel minus 1 (0-15) and turn *MIDI SysEx Enable* on. Listing, receiving and sending samples work (each sent file becomes a new instrument; nothing is overwritten); there is no editor. The ASR-X has its own entry below.
- The **Ensoniq ASR-X** has no MIDI Sample Dump Standard, so Generic SDS cannot work with it; it has its own *Ensoniq ASR-X* sampler type, which needs both a MIDI input and output and has never been tried on real hardware - back up the unit first. Set the Device ID to the unit's *SysEx Device ID* (System/MIDI page, default 0) and turn *SysEx Recv* on. Listing, receiving and sending samples work (each sent file becomes a new sample, a stereo file two; nothing is overwritten); transfers run at MIDI speed, about 5 minutes for 10 seconds of audio; there is no editor.
- The **Yamaha A4000/A5000** needs a *Device Number* set on the sampler (0 is what AKAISDS expects) and *Bulk Protect* switched **off**. See [Yamaha A4000/A5000 Editor](#yamaha-a4000a5000-editor-experimental).

You can test your MIDI setup in the **Troubleshooting** tab. If you're having connectivity issues, check your MIDI connections to ensure they are not reversed. If you still don't see your sampler listed in the MIDI Hardware Test tab, see [MIDI Troubleshooting & Diagnostics](#midi-troubleshooting--diagnostics) for details.

![Audio MIDI Settings](screenshots/settings-audio-midi.png "Audio MIDI Settings")

The **Appearance** setting (Settings tab) switches between *System* (follows your computer's light/dark mode), *Light* and *Dark*. It applies straight away.

---

## Using a DAW at the Same Time

You can use AKAISDS and a DAW (Ableton Live, Logic, etc.) with the same MIDI interface, just **not at the same time**. In your DAW don't route the sampler's MIDI input into a MIDI track that outputs back to it.

Why? If another program sends your sampler's own replies back to it, the sampler treats them as commands. At best that changes or undoes a setting you just edited. At worst the sampler can be told to load, delete or save data.

If AKAISDS notices this happening, it shows a **Stray MIDI replies** warning. Close or reconfigure the other program before you carry on editing. The warning comes back if it happens again.

![Stray MIDI replies warning](screenshots/stray-midi-replies-dialog.png "Stray MIDI replies warning")

---

## Sending & Receiving Samples

![Main Window Dark Mode](screenshots/main-window-dark.png)

**1. Drag audio files into the queue**

WAV, AIFF, FLAC, MP3, Ogg Vorbis and Opus are all supported (compressed files are converted to PCM when sent), as are ReCycle `.rx2` loops, which are queued as one row and sent as individual slices. Dragging in entire folders works too. AAC/M4A is not supported.

![File Queue](screenshots/file-queue.png)

**2. Adjust quality settings (optional)**

You can set quality globally via the Transmission Settings button or individually via the Edit button next to each sample. Reducing quality will significantly reduce the transfer time (MIDI Sample Dumps are notoriously slowwwww 🐌).

![Sample Settings](screenshots/sample-settings.png "Sample Settings")

![Sample Info](screenshots/sample-info.png)

**3. Hit Send!**

If you've never used MIDI sample dumps before, expect them to be slow. Very slow. Reducing bit depth or sample rate will help speed up the transfer, at the expense of sound quality.

If the sampler stops replying part way through a send (for example, its MIDI cable comes unplugged), AKAISDS re-sends the packet it's stuck on a few times. If there's still no reply, it finishes sending the current sample without confirmation, tells you the sample is unverified (check it on the sampler), and cancels any other files still waiting in the queue.

**Receiving Samples**

In the right hand list of *Currently Loaded Samples*, select which samples you would like to receive from the sampler. Then click the *Receive Samples* button, which will then ask where you want to save the samples. Transmission will commence once you have selected a location to save the received samples.

With an Akai sampler selected, the **Save received as** menu next to the list chooses what is saved: a **WAV**, an **Akai sample** file (`.s3`, or `.s1` for an S1000) which keeps the sampler's own header (name, root note, tuning and loops), or both.

**Approximate transfer times for a 5-second sample:**

| Sample Rate | 16-bit   | 8-14 bit |
| ----------- | -------- | -------- |
| 44100 Hz    | ~3.7 min | ~2.5 min |
| 30000 Hz    | ~2.5 min | ~1.7 min |
| 22050 Hz    | ~1.9 min | ~1.2 min |
| 15000 Hz    | ~1.3 min | ~50 sec  |
| 11025 Hz    | ~56 sec  | ~37 sec  |
| 8000 Hz     | ~41 sec  | ~27 sec  |

Note that **bit depth** is a two-tier step, not a smooth scale. An 8-bit sample will take just as long as a 14-bit sample to transfer. So there is no benefit to reducing to less that 14-bit specifically for speed. Reducing **sample rate** does scale linearly. Half the sample rate means half the transfer time, but at the expense of higher frequency audio content.

These are theoretical best-case figures based on MIDI's fixed wire speed of 31,250 baud. Real world transfers may be slower, depending on how quickly your specific sampler responds to each data packet.

---

## Program & Keygroup Editor

The editor is powered under the hood by *s3ked*. It offers control over the most commonly used functions in an Akai S2000 and S3000 series samplers. Not all functions have controls (yet). The intention was to keep the interface as easy to understand as possible at first glance.

The editor also supports the Akai S1000 - set the Sampler Type to *Akai S1000* in Settings first. An S1000 has no Multi tab, Global tab, modulation matrix, LFO2, portamento, mute groups or velocity start, so those controls are hidden. Its Envelope 2 is a plain ADSR, and its fixed controller routing (what velocity, key, pressure and the mod wheel do) is editable in the *Controllers* cards instead of a modulation matrix.

With the Sampler Type set to *Akai S900/S950 (experimental)*, the Open Editor button opens a separate, smaller editor for the S900/S950: programs and keygroups can be edited and written back to the sampler in one go (the previous values are backed up first). On its Samples tab only the replay mode (O/L/A) and direction (N/R) can be changed, with the same backup and **Write to Sampler** step; everything else there is read-only for now. With *Yamaha A4000/A5000 (experimental)* it opens the [Yamaha editor](#yamaha-a4000a5000-editor-experimental).

By default the editor opens up on the Programs page. It will take a second or two to load, especially if you have lots of Programs and Keygroups on your Akai sampler.

This is not an extensive deep dive into the editing functions of an Akai sampler, it is assumed you are already familiar with the workflow of Akai samplers.

**Keyboard shortcuts:** Command/Ctrl + 1, 2, 3 and 4 switch between the Multi, Programs, Samples and Global tabs. Command/Ctrl + T goes back to the Transfer Dashboard. Command/Ctrl + K opens the [Audition Keyboard](#audition-keyboard). Command/Ctrl + Shift + I is File > Import Audio. **Help > Keyboard Shortcuts...** lists every shortcut for the window you are in.

**Tidying the window:** every card has a small arrow beside its title. Click it to fold the card away (cards that sit side by side fold together). AKAISDS remembers which ones you folded. The sample memory bar on the Samples tab (and the Dashboard) shrinks to a thin strip if you double click it.

**Number boxes and the mouse wheel:** drag up or down on any number box to change it (hold *Shift* for finer steps), or click it and type a value. Scrolling the mouse wheel over a control no longer changes it by accident while you scroll the page: click a control first and the wheel adjusts it, shown by an outline, until you click somewhere else.

**Drag and drop:** drop a `.p1` or `.p3` program file onto the editor window to load it, the same as **Load...**. Drag a program out of the Programs list onto your desktop to save it as a file.

### Programs Tab

![Programs Tab](screenshots/editor-program-page.png)

To create new Programs or Keygroups, right click on an existing Program or Keygroup, then click *Duplicate Program...* or *Duplicate Keygroup...*

To open the Keygroup settings, select a Keygroup in the list. To go back to the Program settings page, select a Program in the list.

**Saving and loading programs**

The **Save...** and **Load...** buttons under the Programs list (or right click a program) save a program to a file in Akai's own program format (`.p3` for S2000/S3000, `.p1` for S1000) and load one back in. A loaded program always arrives as a **new** program - it never overwrites one, and if its name is already used on the sampler you'll be asked for another.

Samples are **not** stored in the file. A program refers to its samples by name, so load the samples onto the sampler first (the Load dialog lists any the sampler doesn't have yet). A `.p1` file loaded onto an S2000/S3000 is converted first - AKAISDS asks you to confirm, and the sampler's own conversion of some S1000 settings isn't replicated. This feature is experimental, so back up first.

### Keygroup Page

![Keygroup Page](screenshots/editor-keygroup-page.png)

**Mute Group** (Range card) makes keygroups cut each other off, like a closed hi-hat choking an open one. Keygroups in the same group (1-32) silence each other when one plays; **Off** lets a keygroup ring freely. Be careful with keygroups that cover the same notes: if they are all in the same group only one of them will sound. A duplicated keygroup keeps its mute group, so check it on the copy.

**Vel. start** (next to *Velocity High* on each zone) moves the sample's start point depending on how hard you play, from -9999 to +9999 sample points. 0 means the start never moves. It's handy for drum samples. Hold *Shift* while dragging for fine control, or click the knob and type a number.

*(S2000/S3000 only - an S1000 doesn't have these two controls.)*

The **envelope graphs** are editable too: drag a dot to change the stage it sits on (or the flat sustain line, up or down, for just the sustain level) (the knobs follow as you drag, and the sampler is only updated when you let go). Hold *Shift* for fine control, double-click a dot to reset it, or press *Esc* mid-drag to cancel.

### Multi Tab

The Multi tab is where you can assign Programs to various parts of a Multi. You can also rename your Multi if desired.

![Multi Tab](screenshots/editor-multi-page.png)

Each part also has a **Key range** (the lowest and highest note it plays), **Semi** and **Cents** (transpose and fine tune, +-50) and a note **Priority** (Low, Norm, High or Hold). Changes are sent as you make them.

**Save to File...** writes the Multi as an Akai `.m3` file. **Load from File...** reads one back into the sampler's current Multi: it shows what will change first, saves the current Multi to `~/.akaisds/multi_backups` before writing anything, checks the result afterwards, and assigns programs by name (a program the sampler doesn't have is skipped). Output assignment and effects are not loaded, as no hardware was available during development to test this.

### Samples Tab

![Samples Tab](screenshots/editor-sample-page.png)

The Samples tab is where you can edit loop points, edit the audio itself (trim, reverse, fade, normalise, filter, time stretch, pitch shift, reverb), rename and duplicate samples, set root note and tuning, and save samples as Akai files. A bar under the list shows how much sample memory is used.

The list of samples will appear on the left. You can right click a sample to delete or rename it.

To load the sample's waveform, double click on the placeholder window. Loading of a sample's waveform is very slow (same speed as sending and receiving samples over SDS). It will also freeze the interface until loading is complete.

Loop points and sample start/edit markers can be edited visually without needing to load the sample's waveform. Hold down *Shift* when dragging sample markers around for more precision. Alternatively you can type a value in to the boxes below the waveform. The audio can also be previewed (including the loop points) by clicking once on the waveform. The audio preview is an approximation, not an exact 1:1 replica of an Akai sampler. Where a start/end marker and a loop marker sit on top of each other, the upper half of the waveform grabs the start/end marker and the lower half grabs the loop marker. The **Loop Preview** card has **-**, **+** and **1:1** buttons (Ctrl + scroll also works) to zoom in on the loop join.

The **Root Note & Tune** section sets which key plays the sample at its original pitch and its fine tuning. **Detect Root Note** works it out for you from the loaded waveform (best with a single, clean note): it shows its confidence and you can accept or ignore the result. It adjusts the tuning on top of any compensation the sampler already applied, rather than replacing it.

#### Edit Sample

![Edit Sample with an unsent working copy](screenshots/editor-sample-edit.png)

The **Edit Sample** card holds the audio editing actions: **Trim to Markers**, **Reverse**, **Fade In/Out**, **Normalise**, **Filter...**, **Stretch / Pitch...** and **Reverb...**. They run on the host computer, so the sample's waveform has to be loaded first.

**Edits don't touch the sampler.** Each one changes a *working copy* of the sample on your computer, marked by an amber dot in the sample list and an amber strip on the card ("Edited - not on the sampler yet"). Stack as many as you like, then:

- **Undo** steps back (up to 10 edits, then back to the sampler's version).
- **Revert** throws the working copy away.
- **Send to Sampler** replaces the sample on the sampler. AKAISDS sends the new audio under a temporary name, checks it arrived, deletes the original and renames the new one, so the original is never deleted before its replacement is safe. This goes over SDS, so it is slow. You are asked to confirm first.
- **Send as New Sample...** sends the result under a new name and leaves the original untouched.

The working copy survives selecting other samples. AKAISDS asks before you close the window with unsent edits. Marker and loop changes on a sample with a working copy are kept on the computer too and go with the next send.

**Filter...** (low/high pass), **Stretch / Pitch...** (change length or pitch independently, with a grain size setting - aka. Timestretch) and **Reverb...** each open a dialog with a **Preview** button, which becomes **Stop** while it plays (press *Space* to toggle it). Moving a knob while a preview plays does not restart it. Previews play at the sample's root note and tune, so they sound as they will on the sampler. Their *Send as New Sample...* buttons skip the working copy and send straight away.

![Reverb dialog](screenshots/reverb-dialog.png)

**Reverb** has settings for *Decay*, *Size*, *Damping* and *Mix* (with four presets). It makes the sample longer by the length of the tail, up to five seconds. **Stretch / Pitch** is a port of an Akai-style grain time stretcher: great on drums and loops, but a sustained tone can warble.

**Edit in External App...** exports the loaded audio to a WAV and opens it in your audio editor (choose one in Settings > External Audio Editor, or it uses your computer's default). When you save in the editor, **Load Edited Audio** brings the file back as the working copy; nothing is sent until you send it. Tick *Load automatically when the file is saved* in the same Settings section to skip that button.

**Duplicate** sends a copy of the sample under a new name, leaving the original alone. **Save as Akai File...** saves the sample as `.s3` (or `.s1` on an S1000) and **Load Akai File...** makes a new sample from one, restoring its loops, root note and tuning. Both are new and untested against files from other tools.

**File > Import Audio...** (Command/Ctrl + Shift + I) opens any audio file, or a ReCycle `.rx2` loop, in the Slice Editor without going through the Dashboard. An `.rx2` arrives with its slice points marked; stereo files stay stereo (each slice is sent as a left and a right sample, and the program puts them in two zones).

#### Slice Editor

Click **Slice Editor…** to chop a loaded sample, ReCycle style. Add slice markers by hand (double click the waveform, drag to move), use **Generate Equal Slices**, or drag the **Sensitivity** slider to have AKAISDS find the hits for you (it replaces all markers as you drag, so use it first). The **Detection** menu picks *Simple* (instant) or *SuperFlux (high quality)*, which finds hits more reliably but takes a moment after each slider move. Loaded stereo files show both channels. Click the waveform to preview a slice.

Every slice is exported to the sampler as a new one-shot sample, and the original is left alone. Tick the option to also create a **new program** and it will have one keygroup per slice on consecutive keys starting at C1, up to 92 slices. **Save WAVs...** saves every slice as a WAV file in a folder on your computer instead.

![Slice Editor](screenshots/slice-editor-manual.png)

![Slice Editor - automatic slicing](screenshots/slice-editor-auto.png)

### Global Tab

The Global tab holds settings of the *whole sampler* rather than one program - the ones you'd find on the sampler's own GLOBAL page. (S2000/S3000 only.)

![Global Tab](screenshots/editor-global-page.png "Global Tab")

- **Tuning & Output:** *Output level* (-18 dB to +18 dB). *Tune* and *Fine tune* are shown, but can't be changed from AKAISDS - the sampler accepts the value and then ignores it - so they're greyed out until I figure out how to get them working.
- **External Controller:** which controller the **External** modulation source follows: *Breath*, *Footpedal* or *Volume*. The same choice also appears in the Modulation card on the Programs tab, and the two always match.
- **MIDI:** the *Program change channel* (Off, 1-16 or Omni) is shown but locked, to be safe - change it on the sampler itself.
- **Play Button:** the *note*, *channel* and *velocity* the sampler's front-panel Play button plays.
- **Memory & OS:** the sampler's operating system version and how much sample memory is used.
- **SCSI:** *Disk ID*, *Sector size* and *Local ID* are shown but locked, to be safe - change those on the sampler itself.

Greyed-out controls explain themselves in their tooltip.

### Audition Keyboard

![Audition Keyboard](screenshots/editor-audition-keyboard.png)

**View > Audition Keyboard** (Command/Ctrl + K), or the **Keyboard** arrow along the bottom of the window, opens a piano for playing your sampler from the computer. Click the keys, or play them from your computer keyboard: `A W S E D F T G Y H U J K O L P ;` play a chord-friendly octave starting at C3, **Z** and **X** move the octave, **C** and **V** change the velocity, and **[** and **]** change the MIDI channel. Set the channel to the one your program or Multi part listens on. **Panic** stops every note.

While the keyboard is open it takes over the computer keyboard, so typing into a text box won't work until you close it (it starts closed every time). It sends plain MIDI notes only, and is switched off while a sample is loading or a transfer is running. The S900/S950 and Yamaha editors have the same keyboard (not tried on a real S900/S950 or A4000 yet).

### Refreshing Sampler Data

If you edit a parameter on the front display of your sampler, the edit is unlikely to be picked up by AKAISDS automatically. To get around this, you can use the *Refresh* button on the bottom left of the window (or by pressing Command + R or Ctrl + R depending on your platform).

---

## Yamaha A4000/A5000 Editor (experimental)

This editor was built and tested on an A4000. The A5000 should work for the same things but hasn't been tried, so treat it as experimental and back up any important data first.

**Setup:** set the Sampler Type to *Yamaha A4000/A5000 (experimental)*, pick your MIDI ports (both input and output are required), and on the sampler set its *Device Number* to 0 and switch *Bulk Protect* **off** (Utility > MIDI bulk). If Bulk Protect is on, the sampler ignores every edit and cancels sample transfers - check it first if something "does nothing". Then click **Open Editor**.

![Yamaha Programs Tab](screenshots/yamaha-programs-tab.png "Yamaha Programs Tab")

**Programs tab.** The A4000 always has 128 programs, so empty ones are hidden unless you tick *Show empty programs*. Select a program to see the samples assigned to it and the settings for the program and each assigned sample. Use **Assign Sample...** and **Remove** to add or take away samples. Edits are sent to the sampler as you make them (a moment after you stop moving a control).

**Samples tab.** Shows each sample's waveform (both channels of a stereo sample as one display), its loop and start/end markers, loop mode and its parameters. Double click the waveform to load the audio (slow - it comes over MIDI). Drag the markers or turn the knobs; click the waveform to preview. **Detect Pitch** works out the sample's original key and fine tune from the audio.

![Yamaha Samples Tab](screenshots/yamaha-samples-tab.png "Yamaha Samples Tab")

**Backups.** The first time you change a program or sample in a session, AKAISDS saves a copy of it. Use **Hardware > Restore from Backup...** to put an object back as it was, and **Hardware > Open Backup Folder** to find the files.

**Edits make new samples.** The A4000 can't delete or overwrite a sample over MIDI, so the **Edit Sample** card (trim, reverse, fade, normalise, filter, stretch / pitch) works like the Akai one: each edit changes a working copy on your computer (**Undo** and **Revert** are there), and **Send as New Sample** puts it on the sampler as a *new* sample named like `<name> EDIT` (or `<name> TRIM`, `<name> PITCH` from a dialog's own button). The original is never touched. **Edit in External App...** and **File > Import Audio...** (including `.rx2` loops) work here too. The **Slice Editor** works the same way, and can also fill an **empty program** with the slices (one per key, starting at C1).

**Sending and receiving samples** use the Transfer Dashboard as usual: *Currently Loaded Samples* lists the sampler's samples, and Receive saves them as WAV files (both channels of a stereo sample). Sending needs a MIDI input as well as an output, is slow (a stereo sample takes about twice as long as a mono one), and never overwrites: if the name is taken the new sample gets a `2` on the end.

**If the sampler goes quiet.** After samples have been sent, the sampler can show *MIDI Bulk Received* and stop answering over MIDI until you press **OK (Knob 5)** on it - most often when you assign a freshly sent sample to a program. AKAISDS tries to warn you when this happens, and tells you when to press **OK**. It's not perfect though, so always refer to your sampler's display after making an edit.

**Not supported:** creating or deleting programs and samples (the sampler has no MIDI command for it), effects, controller and system settings.

---

## MIDI Troubleshooting & Diagnostics

There are 2 MIDI tests in the AKAISDS Settings window.

1. [MIDI Hardware Test](#midi-hardware-test)
2. [MIDI Loopback Test](#midi-loopback-test)

If things behave strangely while another music program is open (settings changing by themselves, a *Stray MIDI replies* warning), see [Using a DAW at the Same Time](#using-a-daw-at-the-same-time).

### MIDI Hardware Test

![MIDI Troubleshooting](screenshots/settings-troubleshooting.png)

Use the Hardware test to check if your MIDI sampler is detected by your interface.

To use the hardware test, connect a MIDI cable from your MIDI interface's output to the sampler's input, and vice versa. Both input and output connections are required for this test.

Click the *Run Hardware Test* button and if successful, your sampler's details should appear in the window.

### MIDI Loopback Test

Use the Loopback Test to check if your MIDI interface can support MIDI SysEx traffic. Not all interfaces have full SysEx support.

To use the loopback test, take a MIDI cable and connect one end to the MIDI input of your interface, and the other end to the MIDI output of the same interface (ie, back into itself). Then click the *Run Loopback Test* button. The test may take up to 20 seconds depending on your hardware.

![MIDI loopback cable](diagrams/midi-loopback-cable.png "MIDI loopback cable")

Loopback test results will be shown in a dialog box, along with your MIDI interface, operating system and AKAISDS version. Use **Copy Results** or **Share on GitHub** (opens a GitHub issue with the results already filled in) to share them so your interface can be added to the tested list.

![MIDI Interface Test Results](screenshots/midi-interface-test-results.png)

**Reporting a problem**

Use **Help > Open Log Folder** (in the S900/S950 and Yamaha editors: **Hardware > Open Log Folder**) and send `akaisds.log` along with a description of what you were doing, which sampler you have and which MIDI interface you use. Send it straight after the problem happens, since older entries are eventually overwritten.

There is a table in the README of the GitHub repository of interfaces that have been tested using this method. If your interface isn't listed there, I encourage you to help out the community by sharing your results: click **Share on GitHub** in the results window (or submit a Pull Request).
