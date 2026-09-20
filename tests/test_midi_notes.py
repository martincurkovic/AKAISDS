# tests for core/midi_notes.py - the note-number <-> note-name convention
# shown throughout the editor (keygroup ranges, the note lo/hi spinboxes)

from core.midi_notes import midi_note_to_name


def test_middle_c_matches_the_s3000xls_own_front_panel():
    # the S3000XL's front panel calls note 60 "C3" - one octave below the
    # general-MIDI convention (C4) that a previous version of this function
    # used, which is what made the editor read one octave higher than the
    # hardware for every note
    assert midi_note_to_name(60) == "C3"


def test_sharps_are_named_with_a_hash():
    assert midi_note_to_name(61) == "C#3"


def test_octave_boundary_at_c():
    assert midi_note_to_name(59) == "B2"
    assert midi_note_to_name(60) == "C3"


def test_lowest_and_highest_keyrange_notes():
    # LONOTE/HINOTE's documented range - pinning the endpoints down
    # directly against this convention (not the s3k parameter registry's
    # own "21 to 127 represents A1 to G8" note, which turns out not to be
    # self-consistent under a single octave formula and isn't a reliable
    # cross-check here)
    assert midi_note_to_name(21) == "A-1"
    assert midi_note_to_name(127) == "G8"
