# tests for ui/note_spinbox.py's note-name parsing - specifically that it
# stays the exact inverse of core/midi_notes.midi_note_to_name, since a
# previous octave-offset fix there (see test_midi_notes.py) would have
# silently broken round-tripping (type "C3", get back a different note)
# if this side weren't updated to match

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.midi_notes import midi_note_to_name
from ui.note_spinbox import _note_name_to_midi


def test_parses_middle_c_to_the_matching_raw_note():
    assert _note_name_to_midi("C3") == 60


def test_parses_a_sharp():
    assert _note_name_to_midi("C#3") == 61


def test_round_trips_every_note_in_range():
    for note in range(0, 128):
        name = midi_note_to_name(note)
        assert _note_name_to_midi(name) == note
