"""Centralized tooltip text for the whole app.

Edit tooltip WORDING here, without touching any other source file. Grouped
by which UI file each tooltip belongs to, in roughly the order that file
builds its widgets - matches each ui/*.py file's own control-construction
order, not alphabetical, so a wording pass can follow one window top to
bottom rather than hunting through this file too.

Plain module-level string constants, not a dict - an editor's own "find
usages" then shows every real call site a given tooltip actually reaches,
and a typo in a name fails the import loudly (NameError) instead of a dict
lookup silently returning None and a widget quietly getting no tooltip.

Some tooltips have a runtime-computed SUFFIX or substitution appended at
the call site (a resolved keyboard shortcut, a live count, a device name) -
those are marked in a comment above the constant; the text here is only
the static part.

PLACEHOLDER marks a widget identified as worth a tooltip, where the exact
hardware-jargon field it controls wasn't confidently known well enough to
word correctly rather than guess - fill these in by hand, then grep this
file for "PLACEHOLDER" to find whatever's left.
"""

PLACEHOLDER = "PLACEHOLDER - write this tooltip by hand"

# =============================================================================
# ui/dashboard.py
# =============================================================================

OPEN_EDITOR_NEEDS_MIDI_PORTS = (
    "Select both a MIDI Input and MIDI Output in Settings first"
)
OPEN_EDITOR_NEEDS_AKAI_DEVICE_TYPE = "Set Sampler Type to Akai S1000, Akai S2000/S3000, Akai S900/S950 or Yamaha A4000/A5000 in Settings first"
OPEN_EDITOR_YAMAHA = "Opens the Yamaha A4000/A5000 program editor (experimental - edits go straight to the sampler's memory)"
# Yamaha A4000/A5000 (see controller/yamaha_session.py): every answer comes back on the MIDI input
YAMAHA_NEEDS_MIDI_INPUT = (
    "The Yamaha A4000/A5000 answers on your MIDI input, so the editor needs\n"
    "one - select it in Settings."
)
# ...and so does listing/receiving samples (controller/yamaha_transfers.py); it has no known delete opcode
YAMAHA_LIST_NEEDS_MIDI_INPUT = (
    "The Yamaha A4000/A5000 answers on your MIDI input, so browsing and\n"
    "receiving samples needs one - select it in Settings."
)
YAMAHA_SEND_NEEDS_MIDI_INPUT = (
    "Sending to the A4000/A5000 needs a MIDI input too (the unit never answers a load, so each sample is "
    "checked afterwards). Select one in Settings."
)
YAMAHA_CANT_DELETE = (
    "Deleting samples over MIDI isn't supported for the Yamaha A4000/A5000 -\n"
    "use its front panel (COMMAND > DELETE)."
)
YAMAHA_NO_SAMPLE_INFO = (
    "Sample details and renaming aren't available here for the Yamaha A4000/A5000 -\n"
    "use the Program Editor's Samples tab."
)
OPEN_EDITOR_S950 = "Opens the Akai S900/S950 program editor (experimental - writes are untested on hardware)"
# Akai S900/S950 (see controller/s950_transfers.py): it answers on the MIDI
# input, so sending/receiving/browsing all need one; and it has no delete
# opcode at all
S950_NEEDS_MIDI_INPUT = (
    "The Akai S900/S950 answers on your MIDI input, so sending, receiving\n"
    "and browsing samples needs one - select it in Settings."
)
S950_CANT_DELETE = (
    "The Akai S900/S950 can't delete samples over MIDI -\nuse its front panel."
)
# shown on both btn_open_editor and btn_settings (and mirrored onto their
# menu actions - see TransferDashboard._sync_menu_actions) while a transfer
# is in flight - opening either window mid-transfer risks a second MIDI
# consumer racing the in-flight one on the same shared connection (Settings
# can also reopen the ports outright), see AGENTS.md's "Follow-up, first
# real-hardware session: a confirmed dual-connection MIDI race" for why
BUSY_BLOCKS_OTHER_WINDOWS = "Can't do this while a MIDI transfer is in progress"

# =============================================================================
# ui/settings_dialog.py
# =============================================================================

DEVICE_ID_CHANNEL = (
    "The sampler's SysEx ID, not its MIDI channel: almost always 0. It must match the unit; if it shows 1-128, "
    "subtract 1."
)

# Ensoniq EPS/ASR (see dev_docs/ensoniq-support-plan.md): listing, receiving and sending samples (no editor); it answers on the MIDI input
ENSONIQ_NEEDS_MIDI_INPUT = (
    "The Ensoniq answers on your MIDI input, so listing, receiving\n"
    "and sending samples needs one - select it in Settings."
)
ENSONIQ_NO_EDITOR = "There is no editor for the Ensoniq EPS/ASR - only listing, receiving and sending samples."
ENSONIQ_CANT_DELETE = (
    "Deleting and renaming samples isn't available for the Ensoniq EPS/ASR."
)

SAMPLER_TYPE = (
    "Pick the model that matches your sampler; each speaks its own protocol.\n"
    "Akai S1000, S2000/S3000: browse, rename and delete samples, plus the Program Editor.\n"
    "Akai S900/S950 (experimental): send, receive and rename; no delete.\n"
    "Yamaha A4000/A5000 (experimental): list, send and receive, plus an editor.\n"
    "Ensoniq EPS/ASR (untested): list, receive and send; each sent file becomes a new instrument.\n"
    "Set the Device ID to the unit's base MIDI channel minus 1.\n"
    "Ensoniq ASR-X (untested): list, receive and send. Set the Device ID to the unit's SysEx ID\n"
    "and turn SysEx Recv on.\n"
    "Generic SDS: send and receive by sample number only."
)

THEME = (
    "System follows your computer's light/dark setting live; Light and Dark keep the app on that look. Applies "
    "as soon as you pick."
)

AUDIO_PREVIEW_BUFFER_SIZE = (
    "Audio buffered during preview. Lower reacts faster but may click on a slow output; higher is steadier but "
    "delays the start."
)

# new
HARDWARE_TEST_BUTTON = (
    "Sends a MIDI Identity Request and checks the device answers: confirms wiring and port selection before a "
    "transfer."
)

LOOPBACK_TEST_BUTTON = (
    "Sends SysEx out the interface's MIDI OUT and reads it back on its MIDI IN (cable them together) to check "
    "it carries real SysEx reliably."
)

# =============================================================================
# ui/sample_settings_dialog.py
# =============================================================================

BIT_DEPTH = "Bit depths of 14 or lower will transmit faster than 16 bit samples"

MONO_ONLY_CHECKBOX = (
    "Only applies to stereo files - sends just the left\n"
    "channel as a single sample instead of a -L/-R pair."
)

STARTING_SAMPLE_NUMBER = "Generic SDS only: it can't tell which slots are in use. Ignored for Akai samplers."

# =============================================================================
# ui/quickstart_dialog.py
# =============================================================================

FIND_PREVIOUS_MATCH = "Previous match"
FIND_NEXT_MATCH = "Next match"
CLOSE_SEARCH_BAR = "Close (Esc)"

# =============================================================================
# ui/filter_sample_dialog.py
# =============================================================================

# new
FILTER_PREVIEW_BUTTON = (
    "Audition the filtered result on your computer's speakers; nothing is written to the sampler. Click again "
    "or press Space to stop."
)

# =============================================================================
# ui/slice_editor_window.py
# =============================================================================

# base text only - the resolved keyboard shortcut is appended at the call
# site, e.g. "Zoom out (Ctrl+-)"
ZOOM_OUT = "Zoom out"
ZOOM_IN = "Zoom in"
ZOOM_FIT = "Zoom to fit the whole sample"

# new - shown whenever the button/checkbox is actually enabled; the demo-
# mode/no-template messages below explain the two DISABLED cases instead
EXPORT_SLICES_BUTTON = "Send every slice to the sampler as its own new one-shot sample."

SAVE_SLICES_WAV_BUTTON = (
    "Save each slice as a 16-bit WAV file in a folder on this computer. "
    "Doesn't need a sampler."
)

CREATE_PROGRAM_CHECKBOX = (
    "Also create a program with one keygroup per slice, one key each from C1, in Const Pitch and one-shot mode "
    "(like ReCycle's Akai export)."
)

EXPORT_SLICES_DEMO_MODE = "Exporting needs a real sampler, so it's not available in demo mode. Markers and preview still work."

CREATE_PROGRAM_DEMO_MODE = (
    "Creating a program needs a real hardware connection - not available in demo mode."
)

CREATE_PROGRAM_NO_TEMPLATE = (
    "No resident program is available to use as a starting "
    "template for the new program."
)

# new
EQUAL_SLICES_BUTTON = "Replace any existing slice markers with N evenly-spaced ones."

SLICE_DETECTION_MODE = (
    "Simple: fast, follows loudness, updates as you drag. SuperFlux: finds hits by how the sound changes "
    "(better on drum loops), but needs a moment after you pause."
)
SLICE_DETECTION_SUPERFLUX_UNAVAILABLE = (
    "SuperFlux isn't available - the VelociLoops library didn't load."
)
TRANSIENT_SENSITIVITY_SLIDER = "Replaces the slice markers as you drag. 0% is off; higher detects more, quieter hits."

# =============================================================================
# ui/program_editor_window.py
# =============================================================================

# --- ZPLAY / SPTYPE / PORTYPE option tooltips (shown per dropdown item, and
# copied onto the combo's own tooltip on selection - see _LOOP_TYPE_OPTIONS/
# _SAMPLE_PLAYBACK_TYPE_OPTIONS/_PORTAMENTO_TYPE_OPTIONS) -------------------

LOOP_TYPE_AS_SAMPLE = (
    "Uses whichever loop points and loop type are already stored "
    "on the sample itself, rather than overriding them for this zone."
)
LOOP_TYPE_LOOP_IN_RELEASE = (
    "Loops while the note is held, then finishes the current pass before the amp release, so it doesn't cut "
    "off mid-loop."
)
LOOP_TYPE_LOOP_TIL_RELEASE = (
    "Loops continuously until note-off, then jumps straight into "
    "the release stage from wherever the loop currently is."
)
LOOP_TYPE_NO_LOOPS = (
    "Ignores the sample's loop points and plays straight through "
    "once, gated by the amp envelope as usual."
)
LOOP_TYPE_ONE_SHOT = (
    "Ignores note-off and always plays through to the physical "
    "end of the sample, regardless of when the key is released."
)

PORTAMENTO_TYPE_RATE = (
    "The pitch glide always moves at a fixed speed, so a wider "
    "interval between notes takes proportionally longer to glide "
    "through."
)
PORTAMENTO_TYPE_TIME = "The glide always takes the same time, so wider intervals glide faster."

# --- Programs/Keygroups list context menus ----------------------------------

DUPLICATE_PROGRAM_DEMO_MODE = (
    "Not available in demo mode - the fake sampler has no way to create new programs"
)
LOAD_PROGRAM_DEMO_MODE = (
    "Not available in demo mode - the fake sampler has no way to create new programs"
)
SAVE_PROGRAM_BUTTON = (
    "Save the selected program as an Akai program file (.p1 for an S1000, .p3 for an S2000/S3000). Samples "
    "aren't included. You can also drag it out of the list."
)
LOAD_PROGRAM_BUTTON = (
    "Load an Akai program file (.p1/.p3) as a new program, or drop one on the window. Its samples must be "
    "loaded separately."
)
DUPLICATE_KEYGROUP_DEMO_MODE = (
    "Not available in demo mode - the fake sampler has no way to create new keygroups"
)

# base text only - the resolved keyboard shortcut is appended at the call
# site (e.g. "(⌘R)" on macOS, "(Ctrl+R)" elsewhere) - see the comment where
# it's appended for why this can't just be hardcoded to one platform's symbol
REFRESH_BUTTON = (
    "Reload the current program/keygroup from the hardware - "
    "use this if you've changed something on the sampler's own front panel"
)

# base text only - same "resolved shortcut appended at the call site"
# convention as REFRESH_BUTTON above, not hardcoded to one platform's symbol.
# Only ever visible while a real MIDI transfer this window started is in
# progress - see ProgramEditorWindow._set_hardware_busy_ui
CANCEL_TRANSFER_BUTTON = "Cancel the in-progress transfer"

# --- Samples tab: waveform zoom buttons -------------------------------------

SAMPLE_ZOOM_OUT = "Zoom out (Ctrl+scroll on the waveform also works)"
SAMPLE_ZOOM_IN = "Zoom in (Ctrl+scroll on the waveform also works)"
SAMPLE_ZOOM_FIT = "Reset zoom to show the whole sample"

# --- Samples tab: loop hold knob, and Trim/Reverse/Fade/Normalise/Filter/
# Duplicate/Slice Editor buttons ---------------------------------------------

SAMPLE_LOOP_TUNE_KNOB = (
    "Fine tuning offset (+/-50 cents). Applied only while the "
    "loop is sounding, on top of the sample's own overall Tune."
)

SAMPLE_LOOP_HOLD_KNOB = (
    "Loop dwell before release: Off (far left), Hold (forever, far right), or 1-9998 ms. Click and type for an "
    "exact value."
)

TRIM_SAMPLE_BUTTON = (
    "Cut the sample down to the current Start/End markers. "
    "Stays here until you click Send to Sampler."
)
REVERSE_SAMPLE_BUTTON = (
    "Play the sample backwards. Stays here until you click Send to Sampler."
)
FADE_SAMPLE_BUTTON = "Fade in up to the Start marker and out from the End marker. Stays local until you click Send to Sampler."
NORMALIZE_SAMPLE_BUTTON = (
    "Gain up the whole sample until its loudest point hits maximum "
    "amplitude. Stays here until you click Send to Sampler."
)
SEND_AS_NEW_SAMPLE_BUTTON = "Send the result as a NEW sample (you choose its name); the original isn't changed."
LOAD_AUDIO_ACTION = (
    "Load this sample's audio from the sampler (the same as double-clicking the waveform). "
    "Every edit needs it."
)
TIME_STRETCH_BUTTON = "Akai-style Time Stretch"
TIME_STRETCH_MODE = "Time Stretch keeps the pitch and changes the length. Pitch Shift keeps the length and changes the pitch."
TIME_STRETCH_LENGTH = "The new length as a percentage of the old one: 200% is twice as long, 50% half. Double-click to reset."
PITCH_SHIFT_SEMITONES = (
    "How far to shift the pitch, in semitones (12 = an octave). Double-click to reset."
)
PITCH_SHIFT_CENTS = "Fine pitch, in hundredths of a semitone. Double-click to reset."
TIME_STRETCH_GRAIN = (
    "Size of the pieces the audio is cut into. Short grains suit drums and loops; 60-100 ms holds a sustained "
    "tone's pitch better. Double-click to reset."
)
TIME_STRETCH_PREVIEW_BUTTON = "Play the result through your computer's speakers - doesn't write anything to the sampler. Click again (or press Space) to stop it."
SAVE_AKAI_SAMPLE_BUTTON = (
    "Save this sample as an Akai sample file (.s3, or .s1 for an S1000): the sampler's header plus the audio "
    "shown. Read-only; not yet confirmed on a real sampler."
)
LOAD_AKAI_SAMPLE_BUTTON = (
    "Load an Akai sample file (.s1/.s3) as a NEW sample, with its root note, tuning, start/end and loops. Not "
    "yet tried on a real sampler."
)
RECEIVE_FORMAT = (
    "What Receive saves: the Akai sample file (.s3, or .s1; with loops, tuning and root note), a WAV (audio "
    "only), or both. Falls back to WAV if the Akai file fails."
)
SAVE_MULTI_BUTTON = (
    "Save the sampler's multi (name, effects, all 16 parts) as a multi file. Read-only; programs and samples "
    "aren't included. Not yet confirmed on a real sampler."
)
LOAD_MULTI_BUTTON = (
    "Load a multi file: its name, effects and each part's settings; programs are assigned if present. Your "
    "current multi is saved first and you confirm before anything is written."
)
S950_REPLAY_MODE = "How the sample plays: once, looping, or looping back and forth. Written with Write to Sampler."
S950_DIRECTION = "Play the sample forward or reversed. The S950 may need ENT pressed on the sample before it follows."
S950_SAMPLE_WRITE = "Write the changed replay mode / direction to the sampler. The sample's settings are saved first and read back afterwards."
S950_SAMPLE_REVERT = "Put the replay mode and direction back to what the sampler holds."
LOAD_MULTI_DEMO_MODE = "Loading a multi isn't available in demo mode."
REVERB_BUTTON = "Bake a reverb into the sample"
REVERB_PRESET = "A starting point for the four knobs. Moving a knob afterwards switches this back to Custom."
REVERB_DECAY = "How long the tail takes to fall 60 dB, from 0.1 s to 5 s. A longer decay makes a longer sample. Double-click to reset."
REVERB_SIZE = (
    "The size of the room: the delay lengths, from x0.4 to x2.0. Double-click to reset."
)
REVERB_DAMPING = "How quickly the tail loses treble: none, then five steps up to dark. Double-click to reset."
REVERB_MIX = "Dry against reverb: 0 is the sample as it is, 100 is only the reverb. Double-click to reset."
FILTER_SAMPLE_BUTTON = (
    "Apply a highpass or lowpass filter to the whole sample (with a preview). "
    "Stays here until you click Send to Sampler."
)
DUPLICATE_SAMPLE_BUTTON = (
    "Send this sample's already-loaded audio to the sampler under "
    "a new name, copying its loop points and tuning across."
)
SLICE_EDITOR_BUTTON = (
    "Chop this sample's audio into slices "
    "and export them back to the sampler as new samples."
)
EXTERNAL_EDIT_BUTTON = (
    "Open this sample's audio in your audio editor (choose it in Settings). "
    "Save the file there, then click Send Edited Audio."
)
EXTERNAL_LOAD_BUTTON = (
    "Bring your saved version into the app. It isn't sent to the sampler until you click Send to Sampler. "
    "Enabled once the file is saved."
)
STAGE_SEND_BUTTON = (
    "Replace this sample on the sampler with the edited version shown here. "
    "A copy of the original is saved first."
)
STAGE_UNDO_BUTTON = "Undo the last edit to this sample. The first one goes back to what is on the sampler."
STAGE_REVERT_BUTTON = "Throw away the edits that have not been sent and go back to what is on the sampler."
EXTERNAL_AUTO_LOAD = (
    "Bring in the edited audio as soon as the external editor saves it. It's only a working copy; Undo goes "
    "back."
)
EXTERNAL_EDITOR_SETTING = (
    "The audio editor that Edit in External App opens samples in.\n"
    "Leave empty to use your system's default app for WAV files."
)
IMPORT_AUDIO_ACTION = (
    "Open an audio file (WAV, AIFF, FLAC, MP3, Ogg) or ReCycle .rx2 loop in the Slice Editor, and export its "
    "slices as samples."
)
DETECT_ROOT_NOTE_BUTTON = (
    "Estimate this sample's fundamental pitch from its "
    "loop region (if one is set). Otherwise analyses a short chunk of "
    "the sample."
)

YAMAHA_DETECT_PITCH_BUTTON = (
    "Estimate the pitch from the loop (or a short chunk) and offer to set Original key and Fine tune. Needs "
    "the audio loaded."
)

# --- Filter section (Program/Keygroup tabs) - new ---------------------------

FILTER_CUTOFF_KNOB = (
    "Filter cutoff frequency. 99 is fully open (no "
    "filtering); lower values darken the sound progressively more."
)
FILTER_RESONANCE_KNOB = (
    "Filter resonance - emphasizes frequencies right at the cutoff point. Higher "
    "values give a more pronounced, resonant/peaky character."
)
FILTER_KEY_TRACK_KNOB = (
    "How much the cutoff follows the note: 0 = fixed, positive opens the filter for higher notes, negative "
    "closes it."
)

# --- Envelope 1 (ADSR) / Envelope 2 knobs - new -----------------------------

ENV1_ATTACK_KNOB = "Envelope 1 (amp) attack time - how long the sound takes to reach full volume after a key is pressed."
ENV1_DECAY_KNOB = "Envelope 1 (amp) decay time - how long the sound takes to fall from its attack peak down to the sustain level."
ENV1_SUSTAIN_KNOB = "Envelope 1 (amp) sustain level - the volume held for as long as the key stays down, after attack and decay finish."
ENV1_RELEASE_KNOB = "Envelope 1 (amp) release time - how long the sound takes to fade to silence after the key is released."

# ENV2 is a generic 4-stage Rate/Level envelope (not ADSR-shaped like ENV1 -
# see ui/envelope_graph.py's Envelope2Graph), built in a loop over its 4
# stages - one shared Rate/Level tooltip covers all 4, with the stage
# number substituted in at the call site via .format(stage=i)
ENV2_RATE_KNOB = (
    "Envelope 2, stage {stage}: how long this stage takes to reach its own Level."
)
ENV2_LEVEL_KNOB = "Envelope 2, stage {stage}: the level this stage rises or falls to."

# --- Volume, Pan & Velocity / per-zone equivalents - new --------------------

PROGRAM_PAN_KNOB = "The program's overall stereo pan position, before any per-zone or modulation offset."
PROGRAM_LOUDNESS_KNOB = "The program's overall output level."
PROGRAM_VELOCITY_KNOB = "Velocity sensitivity - how much velocity (how hard a key is struck) affects loudness."

# per velocity zone (up to 4 per keygroup) - added on TOP of the program's
# own Pan/Loud above, not a replacement for it
ZONE_LOUDNESS_KNOB = (
    "This zone's own loudness offset, added to the program's overall Loud."
)
ZONE_PAN_KNOB = "This zone's own pan offset, added to the program's overall Pan."

# --- LFO1/LFO2 knobs - new ---------------------------------------------------

LFO1_RATE_KNOB = "LFO1's speed."
LFO1_DEPTH_KNOB = (
    "LFO1's modulation depth (how strongly it affects whatever it's routed to)."
)
LFO1_DELAY_KNOB = (
    "How long LFO1 waits after a key is pressed before it starts modulating."
)

# LFO2 is hardwired to auto-pan (see AGENTS.md's "LFO2 and the modulation
# matrix" section) - its rate/depth/delay are PANRAT/PANDEP/PANDEL, stored
# in the program's own pan fields despite being LFO2's controls
LFO2_RATE_KNOB = "LFO2's speed."
LFO2_DEPTH_KNOB = (
    "LFO2's modulation depth (how strongly it affects whatever it's routed to)."
)
LFO2_DELAY_KNOB = (
    "How long LFO2 waits after a key is pressed before it starts modulating."
)

# --- Portamento - new --------------------------------------------------------

PORTAMENTO_RATE_KNOB = (
    "How fast the pitch glides between notes (see Portamento "
    "Type for whether this is a fixed speed or a fixed total time)."
)

# --- Modulation matrix amount knobs - new -----------------------------------
# shared across every amount knob built via _build_mod_amount_knob (Pan/
# Loudness/Filter Freq slots, LFO1 Rate/Depth/Delay, Pitch) - the specific
# (source, destination) pairing already reads from the row/column headers
# in the UI itself (see AGENTS.md's own "Modulation" card description), so
# one shared explanation of what the AMOUNT knob itself does is accurate
# for all of them rather than needing per-slot wording
MOD_MATRIX_AMOUNT_KNOB = (
    "How strongly this slot's source affects its destination. Negative inverts; 0 disables the slot but keeps "
    "its source."
)

# --- Keygroup tab: mute group and velocity-driven sample start ---------------------------------
MUTE_GROUP_COMBO = (
    "Keygroups in the same mute group cut each other off - a closed hi-hat "
    "choking an open one. Off lets this keygroup ring freely."
)
VELOCITY_START_KNOB = (
    "Moves this zone's start point with velocity, in sample points (-9999 to +9999); 0 = never moves. "
    "Shift-drag for fine control."
)

# --- Global tab (core/global_settings.py) - settings of the whole sampler ----------------
EXTERNAL_CONTROLLER_COMBO = (
    "Which controller the External modulation source follows. A setting of the whole sampler (Global page), "
    "not this program."
)
GLOBAL_TOOLTIPS = {
    "tune_semitones": "Tunes the whole sampler, in semitones.",
    "tune_cents": "Fine tuning of the whole sampler, in cents.",
    "output_level": "Master output level of the sampler.",
    "external_controller": EXTERNAL_CONTROLLER_COMBO,
    "program_change_channel": (
        "The MIDI channel the sampler accepts Program Change messages on. "
        "Off ignores them; Omni accepts every channel."
    ),
    "play_note": "The note the sampler's front-panel Play button plays.",
    "play_channel": "The MIDI channel the Play button plays on.",
    "play_velocity": "The velocity the Play button plays with.",
    "scsi_disk_id": "The SCSI ID of the disk the sampler loads from and saves to.",
    "scsi_sector": "The sector size the sampler uses on its SCSI disk.",
    "scsi_local_id": "The SCSI ID the sampler itself answers to.",
}

# --- Multis tab: per-part level/pan knobs - new -----------------------------
# shared across every knob built via _build_multi_part_knob for the 16
# parts' own Level/Pan controls
MULTI_PART_LEVEL_KNOB = (
    "This part's own output level, independent of the program's own."
)
MULTI_PART_PAN_KNOB = (
    "This part's own stereo position, independent of the program's own."
)
MULTI_PART_PRIORITY = (
    "Which of this part's voices are stolen first when the sampler runs out of voices: Low goes first, Hold is kept the longest."
)
MULTI_PART_LOW_NOTE = "The lowest note this part plays. Notes below it are ignored by this part."
MULTI_PART_HIGH_NOTE = "The highest note this part plays. Notes above it are ignored by this part."
MULTI_PART_TRANSPOSE_KNOB = "Shifts this part's incoming MIDI notes by up to 50 semitones. Double-click to reset to 0."
MULTI_PART_TUNE_KNOB = "Tunes this part in cents (hundredths of a semitone), -50 to +50. Double-click to reset to 0."

# --- Keygroup tab: Zone 1-4 tabs - new ---------------------------------------
ZONE_TAB = "This keygroup's velocity zone {zone_number}: up to 4 samples can be layered or switched by velocity."

# --- Voice & MIDI / Pitch section combos - new ------------------------------
MIDI_CHANNEL_COMBO = (
    'Which MIDI channel this program responds to. "Omni" '
    "responds on every channel at once."
)
POLYPHONY_COMBO = "The maximum number of voices this program can sound at once."
NOTE_PRIORITY_COMBO = (
    "Which notes are dropped first when Polyphony runs out: Low/High drop that end of the range, Normal the "
    "oldest note, Hold never a held note."
)
BEND_UP_COMBO = (
    "How many semitones the pitch bend wheel raises the pitch at full deflection."
)
BEND_DOWN_COMBO = (
    "How many semitones the pitch bend wheel lowers the pitch at full deflection."
)
LFO1_SYNC_COMBO = (
    "On = restarts LFO1's cycle fresh on every new note (in phase "
    "across notes); Off = lets it free-run continuously instead."
)
LFO1_SHAPE_COMBO = "LFO1's waveform shape"
LFO2_SHAPE_COMBO = "LFO2's waveform shape."
LFO2_TRIG_COMBO = (
    "On = restarts the LFO's cycle on every new note; "
    "Off = lets it free-run continuously instead."
)

# --- Samples tab: start/loop/end marker knobs - new -------------------------
SAMPLE_START_KNOB = "Where sample playback begins. Audio before this point is ignored."
LOOP_START_KNOB = "Where the loop region begins."
LOOP_END_KNOB = (
    "Where the loop region ends - playback repeats between Loop Start "
    "and Loop End for as long as a note is held."
)
SAMPLE_END_KNOB = "Where sample playback ends. Audio after this point is ignored."

# Envelope 2 on an Akai S1000 is a plain ADSR filter envelope (see
# ProgramEditorWindow._build_s1000_env2_adsr), not the S3000's 4-stage one
S1000_ENV2_ATTACK_KNOB = "Envelope 2 (filter) attack time - how long the filter takes to open after a key is pressed."
S1000_ENV2_DECAY_KNOB = "Envelope 2 (filter) decay time - how long the filter takes to fall from its attack peak down to the sustain level."
S1000_ENV2_SUSTAIN_KNOB = "Envelope 2 (filter) sustain level - where the filter stays for as long as the key is held, after attack and decay finish."
S1000_ENV2_RELEASE_KNOB = "Envelope 2 (filter) release time - how long the filter takes to close after the key is released."

# the S1000's single pitch-bend field (see ProgramEditorWindow._apply_s1000_gating)
S1000_BEND_RANGE_COMBO = (
    "Semitones the bend wheel moves the pitch at full deflection (0-12). The S1000 has one range, not separate "
    "up and down."
)

# Akai S1000 controller-routing knobs (ProgramEditorWindow's S1000-only
# "Controllers"/"Envelope response" cards) - keyed by the SysEx field each
# one writes. All +/-50 unless noted: a negative amount inverts the effect.
S1000_CONTROLLER_TOOLTIPS = {
    # program
    "K_LOUD": "How much the note's position on the keyboard changes its loudness (Key > Loudness). Negative values get quieter as notes go up.",
    "P_LOUD": "How much channel pressure (aftertouch) changes loudness (Pressure > Loudness).",
    "K_PANP": "How much the note's position on the keyboard moves its pan position (Key > Pan).",
    "MW_PAN": "How much the modulation wheel moves the pan position (Modwheel > Pan).",
    "P_PTCH": "How many semitones channel pressure (aftertouch) bends the pitch at full pressure (-12 to +12).",
    "MWLDEP": "How much the modulation wheel adds to the LFO1 depth (0-99).",
    "PRSDEP": "How much channel pressure (aftertouch) adds to the LFO1 depth (0-99).",
    "VELDEP": "How much note-on velocity adds to the LFO1 depth (0-99).",
    "K_LRAT": "How much the note's position on the keyboard changes the LFO1 rate.",
    "K_LDEP": "How much the note's position on the keyboard changes the LFO1 depth.",
    "K_LDEL": "How much the note's position on the keyboard changes the LFO1 delay.",
    # keygroup
    "V_FREQ": "How much note-on velocity moves the filter frequency (Velocity > Filter).",
    "P_FREQ": "How much channel pressure (aftertouch) moves the filter frequency (Pressure > Filter).",
    "E_FREQ": "How far Envelope 2 moves the filter frequency - the filter envelope's depth. 0 means Envelope 2 has no audible effect on the filter.",
    "V_ENV2": "How much note-on velocity scales Envelope 2's output (Velocity > Envelope 2 level).",
    "E_PTCH": "How far Envelope 2 moves the pitch (Envelope 2 > Pitch).",
    "KV_LO": "A per-keygroup offset added to the program's Velocity > Loudness amount.",
    "V_ATT1": "How much note-on velocity changes Envelope 1's (amp) attack time.",
    "V_REL1": "How much note-on velocity changes Envelope 1's (amp) release time.",
    "O_REL1": "How much note-off (release) velocity changes Envelope 1's (amp) release time.",
    "K_DAR1": "How much the note's position on the keyboard changes Envelope 1's (amp) decay and release times.",
    "V_ATT2": "How much note-on velocity changes Envelope 2's (filter) attack time.",
    "V_REL2": "How much note-on velocity changes Envelope 2's (filter) release time.",
    "O_REL2": "How much note-off (release) velocity changes Envelope 2's (filter) release time.",
    "K_DAR2": "How much the note's position on the keyboard changes Envelope 2's (filter) decay and release times.",
}
S1000_LOUDNESS_VELOCITY_NOTE = (
    "Velocity > Loudness is the Velocity knob in Volume, Pan & Velocity above."
)
S1000_PAN_LFO_RATE = "Speed of the Pan LFO - the S1000's automatic left/right panning."
S1000_PAN_LFO_DEPTH = "How far the Pan LFO swings the sound left and right."
S1000_PAN_LFO_DELAY = "How long after a note starts before the Pan LFO fades in."


# --- Yamaha A4000/A5000 Samples tab: edits make a NEW sample (the unit can't delete or overwrite one over MIDI) -----------------------------
_YAMAHA_COPY_NOTE = " Stays here until you click Send as New Sample. Needs the audio loaded (double-click the waveform)."
YAMAHA_TRIM_SAMPLE_BUTTON = "Trim to the Start/End markers. Nothing is sent until Send as New Sample. Needs the audio loaded."
YAMAHA_REVERSE_SAMPLE_BUTTON = "Play the sample backwards. Nothing is sent until Send as New Sample. Needs the audio loaded."
YAMAHA_FADE_SAMPLE_BUTTON = (
    "Fade in up to the Start marker and out from the End marker. Nothing is sent until Send as New Sample. "
    "Needs the audio loaded."
)
YAMAHA_NORMALISE_SAMPLE_BUTTON = (
    "Raise the gain so the loudest point hits maximum (one gain for both channels). Nothing is sent until Send "
    "as New Sample. Needs the audio loaded."
)
YAMAHA_TIME_STRETCH_BUTTON = (
    "Change the length without the pitch, or the pitch without the length, with a preview. Nothing is sent "
    "until Send as New Sample. Needs the audio loaded."
)
YAMAHA_REVERB_BUTTON = (
    "Bake a reverb into the sample, with a preview. A stereo sample's two channels each go through it. Nothing is sent until Send as New "
    "Sample. Needs the audio loaded."
)
YAMAHA_FILTER_SAMPLE_BUTTON = (
    "Apply a highpass and/or lowpass filter, with a preview. Nothing is sent until Send as New Sample. Needs "
    "the audio loaded."
)
YAMAHA_SLICE_EDITOR_BUTTON = "Chop the sample into slices and send each as a new sample. Needs the audio loaded."

YAMAHA_EXTERNAL_EDIT_BUTTON = (
    "Open this sample's audio in your audio editor (choose it in Settings). "
    "Save the file there, then click Send Edited Audio."
)
YAMAHA_EXTERNAL_LOAD_BUTTON = (
    "Bring your saved version into the app. It isn't on the unit until you click Send as New Sample. Enabled "
    "once the file is saved."
)
YAMAHA_STAGE_SEND_BUTTON = "Send the edited version to the unit as a NEW sample. The A4000 can't overwrite a sample over MIDI, so the original stays."
