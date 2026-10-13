"""How far a PC preview of a sample has to be shifted to sound as the sampler plays it (Qt/MIDI-independent).

Every editor's click-to-preview and every edit dialog's Preview play the audio through `core.audio_preview.SlicePreviewPlayer`, which
takes a `pitch_shift_semitones`. The sampler plays a sample transposed from its root note to the key that is pressed, and shifted by
its tune settings; this module holds the shared note and the Yamaha formula. The Akai formula needs the sample's header fields
(SBANDW baseline, STUNO) and lives in `ProgramEditorWindow._preview_pitch_shift_semitones`.
"""

#: the key every preview plays at: the S3000XL front panel's PLAY button previews at C3 (this app's note 60), whatever the sample's root
#: note. A deliberate approximation of the Akai factory default, not read from the unit. The Yamaha preview uses the same note.
PREVIEW_ROOT_NOTE = 60


def yamaha_shift_semitones(original_key, coarse_tune, fine_tune, fine_steps_per_cent=1.0):
    """The shift for a Yamaha A4000/A5000 sample: from its Original key up/down to the preview note, plus Coarse tune (semitones) and Fine
    tune (`fine_steps_per_cent` steps to the cent).

    UNVERIFIED on a unit: that a positive Coarse/Fine tune raises the pitch (Detect Pitch makes the same assumption), that Coarse tune is in
    semitones, and the size of a Fine tune step (`yamaha_samples_tab.FINE_TUNE_STEPS_PER_CENT`, itself unmeasured)."""
    return (PREVIEW_ROOT_NOTE - original_key) + coarse_tune + fine_tune / (100.0 * fine_steps_per_cent)


def played_seconds(frames, framerate, pitch_shift_semitones):
    """How long `frames` last when played shifted by `pitch_shift_semitones` (a higher pitch plays faster, so it is shorter)."""
    if framerate <= 0:
        return 0.0
    return frames / (framerate * 2.0 ** (pitch_shift_semitones / 12.0))
