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
OPEN_EDITOR_NEEDS_AKAI_DEVICE_TYPE = (
    'Set Sampler Type to Akai S1000 or Akai S2000/S3000 in Settings first'
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
    "The SysEx device ID your hardware is set to (0-127)\n"
    "Only matters if you have more than one sampler on the\n"
    "same MIDI chain. Note that some hardware may report\n"
    "SysEx channels to be 1-128 instead of 0-127."
)

SAMPLER_TYPE = (
    "Akai S1000 and Akai S2000/S3000 unlock browsing/renaming/\n"
    "deleting samples on the hardware (Akai-specific extension to\n"
    "the SDS standard). Pick the one that matches your sampler:\n"
    "the Program Editor speaks a different protocol to each.\n"
    "Generic SDS uses only the universal standard - sending and\n"
    "receiving still work, but by sample number only, with no way\n"
    "to browse, rename or delete what's on the device."
)

AUDIO_PREVIEW_BUFFER_SIZE = (
    "How much audio is buffered ahead during preview playback.\n"
    "Lower values react faster but may click/pop on a slower\n"
    "output device or interface; higher values are more reliable\n"
    "but add latency before playback audibly starts."
)

# new
HARDWARE_TEST_BUTTON = (
    "Sends a universal MIDI Identity Request and confirms the device "
    "answers - proves the MIDI IN/OUT wiring and port selection are "
    "correct before you try an actual sample transfer."
)

LOOPBACK_TEST_BUTTON = (
    "Sends a batch of SysEx messages out through the interface's own MIDI "
    "OUT and reads them back in on its MIDI IN (cabled back into itself) - "
    "confirms the interface can carry real SysEx traffic reliably, not "
    "just simple messages."
)

# =============================================================================
# ui/sample_settings_dialog.py
# =============================================================================

BIT_DEPTH = "Bit depths of 14 or lower will transmit faster than 16 bit samples"

MONO_ONLY_CHECKBOX = (
    "Only applies to stereo files - sends just the left\n"
    "channel as a single sample instead of a -L/-R pair."
)

STARTING_SAMPLE_NUMBER = (
    "Required for a Generic SDS device - it has no way to\n"
    "auto-detect which slots are already in use, unlike an\n"
    "Akai. Ignored entirely when talking to an Akai sampler,\n"
    "which figures this out on its own."
)

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
    "Play the filtered result through your computer's own speakers - "
    "doesn't write anything to the sampler, just lets you audition the "
    "settings first."
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

CREATE_PROGRAM_CHECKBOX = (
    "Also create a new program with one keygroup per slice, mapped to "
    "its own key starting at C1, in Const Pitch (no key tracking) and "
    "one-shot mode - the same layout ReCycle's own \"export to Akai "
    'sampler format" produces.'
)

EXPORT_SLICES_DEMO_MODE = (
    "Exporting slices to the sampler needs a real hardware "
    "connection - not available in demo mode. Marker placement "
    "and click-to-preview still work fully."
)

CREATE_PROGRAM_DEMO_MODE = (
    "Creating a program needs a real hardware connection - not available in demo mode."
)

CREATE_PROGRAM_NO_TEMPLATE = (
    "No resident program is available to use as a starting "
    "template for the new program."
)

# new
EQUAL_SLICES_BUTTON = "Replace any existing slice markers with N evenly-spaced ones."

TRANSIENT_SENSITIVITY_SLIDER = (
    "Live transient detection - replaces the current slice markers as you "
    "drag. 0% is off; higher sensitivity detects more, quieter transients."
)

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
    "Loops continuously while the note is held, then finishes the "
    "current loop pass before moving into the amp envelope's "
    "release stage - avoids cutting off mid-loop on note-off."
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
PORTAMENTO_TYPE_TIME = (
    "The pitch glide always takes the same amount of time to "
    "complete, so a wider interval between notes glides faster to "
    "still finish in that time."
)

# --- Programs/Keygroups list context menus ----------------------------------

DUPLICATE_PROGRAM_DEMO_MODE = (
    "Not available in demo mode - the fake sampler has no way to create new programs"
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
    "How long the loop dwells before releasing: Off (no loop, far "
    "left), Hold (loops forever, far right), or a 1-9998ms dwell "
    "time in between. Click and type a number to set an exact value."
)

TRIM_SAMPLE_BUTTON = (
    "Cut the sample down to the current Start/End markers, "
    "overwriting it on the sampler. Cannot be undone."
)
REVERSE_SAMPLE_BUTTON = (
    "Play the sample backwards, overwriting it on the sampler. Cannot be undone."
)
FADE_SAMPLE_BUTTON = (
    "Linearly fade in from frame 0 up to the Start marker, and "
    "fade out from the End marker to the last frame, overwriting "
    "the sample on the sampler. Cannot be undone."
)
NORMALIZE_SAMPLE_BUTTON = (
    "Gain up the whole sample until its loudest point hits maximum "
    "amplitude, overwriting it on the sampler. Cannot be undone."
)
FILTER_SAMPLE_BUTTON = (
    "Apply a highpass or lowpass filter to the whole sample, "
    "overwriting it on the sampler. Cannot be undone."
)
DUPLICATE_SAMPLE_BUTTON = (
    "Send this sample's already-loaded audio to the sampler under "
    "a new name, copying its loop points and tuning across."
)
SLICE_EDITOR_BUTTON = (
    "Chop this sample's audio into slices "
    "and export them back to the sampler as new samples."
)
DETECT_ROOT_NOTE_BUTTON = (
    "Estimate this sample's fundamental pitch from its "
    "loop region (if one is set). Otherwise analyses a short chunk of "
    "the sample."
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
    "Keytracking - how much the cutoff frequency rises as you play higher "
    "notes. 0 is no tracking (cutoff stays fixed across the keyboard); "
    "positive values open the filter further for higher notes, negative "
    "values close it further."
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
    "How strongly this slot's source affects its destination. Negative "
    "values invert the effect; 0 disables this slot without changing its "
    "assigned source."
)

# --- Multis tab: per-part level/pan knobs - new -----------------------------
# shared across every knob built via _build_multi_part_knob for the 16
# parts' own Level/Pan controls
MULTI_PART_LEVEL_KNOB = (
    "This part's own output level, independent of the program's own."
)
MULTI_PART_PAN_KNOB = (
    "This part's own stereo position, independent of the program's own."
)

# --- Keygroup tab: Zone 1-4 tabs - new ---------------------------------------
ZONE_TAB = (
    "This keygroup's velocity zone {zone_number} - up to 4 samples can be "
    "layered/switched per keygroup based on how hard a key is struck."
)

# --- Voice & MIDI / Pitch section combos - new ------------------------------
MIDI_CHANNEL_COMBO = (
    'Which MIDI channel this program responds to. "Omni" '
    "responds on every channel at once."
)
POLYPHONY_COMBO = "The maximum number of voices this program can sound at once."
NOTE_PRIORITY_COMBO = (
    "Dictates which notes get dropped first once Polyphony's own voice "
    "limit is exceeded: Low/High drop the extreme end of the range first, "
    "Normal drops the oldest note, Hold never drops a held note."
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
