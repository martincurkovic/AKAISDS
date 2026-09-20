import re

from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QSpinBox

from core.midi_notes import midi_note_to_name

_NOTE_TO_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE_RE = re.compile(r"^([A-Ga-g])(#?)(-?\d+)$")
_PARTIAL_NOTE_RE = re.compile(r"^[A-Ga-g]#?-?\d*$")


def _note_name_to_midi(text):
    match = _NOTE_RE.match(text.strip())
    if not match:
        return None
    letter, sharp, octave = match.groups()
    semitone = _NOTE_TO_SEMITONE[letter.upper()] + (1 if sharp else 0)
    # inverse of midi_notes.midi_note_to_name's octave offset - keep in sync
    return semitone + (int(octave) + 2) * 12


class NoteSpinBox(QSpinBox):
    """A QSpinBox for a MIDI note number, displayed/typed as a note name
    (C4) rather than a raw number - matches the note names already shown
    in the keygroup list.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 127)

    def textFromValue(self, value):
        return midi_note_to_name(value)

    def valueFromText(self, text):
        note = _note_name_to_midi(text)
        return note if note is not None else self.value()

    def validate(self, text, pos):
        stripped = text.strip()
        if stripped == "" or _PARTIAL_NOTE_RE.match(stripped):
            note = _note_name_to_midi(stripped)
            if note is None:
                return (QValidator.State.Intermediate, text, pos)
            if self.minimum() <= note <= self.maximum():
                return (QValidator.State.Acceptable, text, pos)
            return (QValidator.State.Intermediate, text, pos)
        return (QValidator.State.Invalid, text, pos)
