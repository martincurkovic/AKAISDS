_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_note_to_name(note_number):
    # midi note number -> name matching the S3000XL's own front panel, which
    # calls note 60 (middle C) "C3" - one octave below the general-MIDI
    # convention (where 60 is C4) that the -1 offset used to match here
    name = _NOTE_NAMES[note_number % 12]
    octave = (note_number // 12) - 2
    return f"{name}{octave}"
