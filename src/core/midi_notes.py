_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_note_to_name(note_number):
    # midi note number -> name under convention such that note 60 = middle C (aka C4)
    name = _NOTE_NAMES[note_number % 12]
    octave = (note_number // 12) - 1
    return f"{name}{octave}"
