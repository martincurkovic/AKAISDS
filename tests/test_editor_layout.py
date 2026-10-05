# tests for ui/editor_layout.py - the layout helpers shared by ProgramEditorWindow (S1000/S2000/
# S3000) and S950ProgramEditorWindow. The pixel-level proof that moving them changed nothing was a
# before/after render of the S3000 editor; these guard the contracts both editors rely on.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QVBoxLayout, QWidget

from ui import editor_layout as el
from ui.knob import Knob


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_knob_column_wires_its_value_label(qapp):
    knob = Knob()
    knob.setRange(0, 99)
    column, value_label = el.build_knob_column("Attack", knob)
    assert value_label.text() == "-"
    knob.setValue(42)
    assert value_label.text() == "42"
    assert column.itemAt(0).widget().text() == "Attack"


def test_knob_column_can_omit_the_name_label(qapp):
    knob = Knob()
    column, _ = el.build_knob_column("x", knob, show_label=False)
    assert column.count() == 2  # knob + value, no name


def test_knob_value_row_has_a_fixed_width_readout(qapp):
    knob = Knob()
    knob.setRange(-50, 50)
    row, value_label = el.build_knob_value_row(knob)
    assert value_label.width() == 28 or value_label.minimumWidth() == 28
    knob.setValue(-50)
    assert value_label.text() == "-50"


def test_keygroup_row_text_uses_the_s3000xl_note_names():
    assert el.keygroup_row_text(0, 60, 72) == "Keygroup 1: C3 - C4"


def test_keygroup_rows_are_widgets_with_untexted_items(qapp):
    lst = QListWidget()
    painted = []
    el.add_keygroup_row(lst, 0, 24, 59, painted.append)
    el.add_keygroup_row(lst, 1, 60, 127, painted.append)
    assert lst.count() == 2
    # the item has no text of its own: a row widget plus item text double-paints (AGENTS.md)
    assert lst.item(0).text() == ""
    assert [s.property("keygroupIndex") for s in painted] == [0, 1]
    assert all(s.property("swatchKind") == "keygroup" for s in painted)
    assert el.keygroup_row_label(lst, 1).text() == "Keygroup 2: C3 - G8"
    assert el.keygroup_row_label(lst, 5) is None


def test_equalize_card_heights_matches_the_taller(qapp):
    from PySide6.QtCore import QSize

    class Card(QWidget):
        def __init__(self, height):
            super().__init__()
            self._h = height

        def sizeHint(self):
            return QSize(100, self._h)

    a, b = Card(40), Card(90)
    el.equalize_card_heights(a, b)
    assert a.minimumHeight() == a.maximumHeight() == 90
    assert b.minimumHeight() == b.maximumHeight() == 90


def test_paired_row_is_one_to_one(qapp):
    a, b = QWidget(), QWidget()
    row = el.build_paired_row(a, b)
    assert row.stretch(0) == 1 and row.stretch(1) == 1


def test_list_column_has_a_bold_header_then_the_widgets(qapp):
    inner = QWidget()
    container = el.build_list_column("Programs", inner)
    labels = container.findChildren(QLabel)
    assert labels[0].text() == "<b>Programs</b>"
    assert inner.parent() is container


def test_zone_card_is_the_zone_card_object(qapp):
    from PySide6.QtWidgets import QHBoxLayout, QStackedWidget

    card = el.build_zone_card(QHBoxLayout(), QStackedWidget())
    assert card.objectName() == "zoneCard"
    assert card.findChild(QLabel, "sectionHeader").text() == "Zone"


def test_card_spacing_is_the_s950_editors_tighter_gap():
    assert el.CARD_SPACING == 6  # was 12 on the S3000 editor; 6 on the S950's (Fusion default)


def test_a_card_page_has_no_left_margin_so_the_gap_to_its_list_is_the_spacing_alone(qapp):
    layout = QVBoxLayout()
    el.style_card_page_layout(layout)
    m = layout.contentsMargins()
    assert (m.left(), m.top(), m.right(), m.bottom()) == (0, 0, 8, 0)
    assert layout.spacing() == el.CARD_SPACING
