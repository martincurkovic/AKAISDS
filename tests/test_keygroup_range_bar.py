# tests for ui/keygroup_range_bar.py's keygroup_color() - shared between the
# range bar's segments and the keygroup list's row swatches
# (program_editor_window.py), so a keygroup's identity only holds together
# if both call sites agree on the same color for the same index. No
# QApplication needed - it's a plain dict lookup + QColor construction.

from ui.keygroup_range_bar import keygroup_color


def test_first_eight_keygroups_get_eight_distinct_colors():
    colors = [keygroup_color(i).name() for i in range(8)]
    assert len(set(colors)) == 8


def test_color_cycles_after_the_eighth_keygroup():
    # a 9th+ keygroup reuses colors rather than erroring or going blank -
    # the dataviz categorical palette only has 8 defined slots
    assert keygroup_color(8).name() == keygroup_color(0).name()
    assert keygroup_color(9).name() == keygroup_color(1).name()


def test_color_is_stable_for_the_same_index():
    # identity has to hold across repeated paints/list rebuilds, not just
    # within a single render
    assert keygroup_color(3).name() == keygroup_color(3).name()
