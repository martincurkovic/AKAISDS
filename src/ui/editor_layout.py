"""Layout building blocks shared by the two program editors - `ProgramEditorWindow`
(Akai S1000/S2000/S3000) and `S950ProgramEditorWindow` (Akai S900/S950) - so their look
can't drift apart. Everything here is a plain function of widgets/layouts: no editor state,
no hardware knowledge, no `self`.

Originally methods of `ProgramEditorWindow` (the comments below carry their history), moved
here unchanged once the S900/S950 editor needed the same pieces. Section cards and scroll
areas live in `ui/qt_helpers.py` (`build_section_card`, `build_scroll_area`), which this
module sits next to.

Change a measurement here (knob-column spacing, row margins, the keygroup row) and BOTH
editors change - re-check both. The windows themselves stay separate classes on purpose:
the S3000's writes each field as it changes through `BridgeWorker`, the S950's stages edits
and writes a whole program once (see AGENTS.md).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.midi_notes import midi_note_to_name


# -- knobs -----------------------------------------------------------------------------------


def build_knob_column(label_text, knob, *, show_label=True):
    # show_label=False skips the name label entirely - used by the
    # Modulation matrix grid, which shows "Amount" once as a
    # column header instead of repeating it above every single knob
    column = QVBoxLayout()
    column.setSpacing(4)  # fixed gap, in pixels - never stretches

    if show_label:
        name_label = QLabel(label_text)
        name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        name_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        column.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignHCenter)

    value_label = QLabel("-")
    value_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

    column.addWidget(knob, alignment=Qt.AlignmentFlag.AlignHCenter)
    column.addWidget(value_label)

    knob.valueChanged.connect(lambda v: value_label.setText(str(v)))

    return column, value_label


def build_knob_value_row(knob):
    # knob + live numeric readout side by side, same compact row shape
    # as the Multis tab's knobs - unlike build_knob_column's vertical
    # (knob, then value below) layout, this is for grids that already carry
    # their own row/column headers (the S3000's Modulation cards' "Slot N"/
    # "Amount" header, and ENV2's "Stage N"/"Rate"/"Level" grid), so there's
    # no per-knob name label to stack above the value here, just the knob
    # and its readout beside each other. Fixed width so the label's own
    # width doesn't change as its text does ("0" vs "-50") - unfixed,
    # a value crossing a digit-count boundary reflowed the row's total
    # width, which visibly nudged the knob sideways since these rows
    # sit centered in their grid cell.
    value_label = QLabel("-")
    value_label.setFixedWidth(28)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    row.addWidget(knob)
    row.addWidget(value_label)
    knob.valueChanged.connect(lambda v: value_label.setText(str(v)))
    return row, value_label


def build_labeled_combo_column(label_text, combo, *, center=True):
    # same "label above, centered" shape as build_labeled_spinbox_column,
    # for a combo instead of a spinbox. center=False instead left-aligns the
    # label flush with the combo's own (left-aligned) displayed text - for
    # the Zone card's Sample combo, which stretches to fill whatever width
    # its row gives it: centering that label put it in the middle of the
    # whole stretched width, nowhere near "BASS C1" etc.
    name_label = QLabel(label_text)
    column = QVBoxLayout()
    column.setSpacing(4)
    if center:
        name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignHCenter)
    else:
        column.addWidget(name_label)
    column.addWidget(combo)
    return column


def build_labeled_knob_value_column(label_text, knob, *, center=True):
    # label above, knob+value beside each other below it - the Zone
    # card's Loud/Pan knobs' own shape, sharing a row with the Sample combo
    # (build_labeled_combo_column). Neither build_knob_column (stacks the
    # value below the knob - taller than this row has room for next to a
    # combo) nor build_knob_value_row (no label at all) fit. center=False
    # left-aligns the label instead, flush with the knob rather than
    # floating over its middle - same reasoning as build_labeled_combo_column.
    name_label = QLabel(label_text)
    column = QVBoxLayout()
    column.setSpacing(4)
    if center:
        name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignHCenter)
    else:
        column.addWidget(name_label)
    knob_row, value_label = build_knob_value_row(knob)
    column.addLayout(knob_row)
    return column, value_label


# -- cards and rows ----------------------------------------------------------------------------


def equalize_card_heights(*cards):
    # pairs like Range+Filter or the two Envelope cards read oddly
    # when one is visibly taller than its neighbor sitting right next
    # to it - build_section_card pins every card to its OWN sizeHint,
    # so nothing does that matching automatically the way a horizontal 1:1
    # stretch already equalizes WIDTH. setFixedHeight (not just a min/max)
    # since these cards' own vertical policy is already Fixed - this
    # is that same fixed value, just the larger of the two rather than
    # each one's own independently measured sizeHint. Call this after
    # every row/widget has already been added to each card, since
    # sizeHint() needs the real final content to measure correctly.
    target_height = max(card.sizeHint().height() for card in cards)
    for card in cards:
        card.setFixedHeight(target_height)


def build_paired_row(left, right):
    # two cards side by side, 1:1 - never anything but 1:1 on either editor
    row = QHBoxLayout()
    row.addWidget(left, stretch=1)
    row.addWidget(right, stretch=1)
    return row


def build_centered_row(widget):
    # e.g. an envelope graph centered over its knobs
    row = QHBoxLayout()
    row.addStretch()
    row.addWidget(widget)
    row.addStretch()
    return row


def build_zone_card(selector_row, stack):
    # the "Zone" card: a row of buttons (Zone 1-4 on the S3000, Soft/Loud sample on the
    # S900/S950) over a stack of one page per button
    zone_header = QLabel("Zone")
    zone_header.setObjectName("sectionHeader")
    zone_section = QVBoxLayout()
    zone_section.setContentsMargins(12, 10, 12, 12)
    zone_section.setSpacing(10)
    zone_section.addWidget(zone_header)
    zone_section.addLayout(selector_row)
    zone_section.addWidget(stack)
    zone_card = QWidget()
    zone_card.setObjectName("zoneCard")
    zone_card.setLayout(zone_section)
    return zone_card


# -- the Programs | Keygroups | detail skeleton ------------------------------------------------


def build_list_column(title, *widgets):
    # bold header + the widgets under it, same layout shape as the dashboard's
    # queue/hardware panels - returned as a container widget
    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(6)
    column.addWidget(QLabel(f"<b>{title}</b>"))
    for widget in widgets:
        column.addWidget(widget)
    container = QWidget()
    container.setLayout(column)
    return container


def build_content_row(programs_container, keygroups_container, detail_widget):
    # the editor's body: Programs column, Keygroups column, then the detail area taking
    # the rest of the width
    content_layout = QHBoxLayout()
    content_layout.addWidget(programs_container)
    content_layout.addWidget(keygroups_container)
    content_layout.addWidget(detail_widget, stretch=1)
    return content_layout


# -- keygroup list rows ------------------------------------------------------------------------


def keygroup_row_text(index, lo, hi):
    return f"Keygroup {index + 1}: {midi_note_to_name(lo)} - {midi_note_to_name(hi)}"


def add_keygroup_row(list_widget, index, lo, hi, refresh_swatch):
    # colored swatch + range text, same row-widget approach as the
    # dashboard's queue/hardware panels - keeps each row's identity tied
    # to its KeygroupRangeBar segment (same color, same order) rather
    # than color alone. The ITEM gets no text of its own: a QListWidgetItem
    # with text AND a row widget double-paints (see AGENTS.md).
    # `refresh_swatch(label)` colors the swatch from its "swatchKind"/
    # "keygroupIndex" properties - each editor owns that (the colors come from
    # the palette, so they must be recolored on a theme change).
    item = QListWidgetItem(list_widget)

    row_widget = QWidget()
    row_widget.setObjectName("transparentContainer")
    row_layout = QHBoxLayout(row_widget)
    row_layout.setContentsMargins(8, 6, 8, 6)
    row_layout.setSpacing(8)

    swatch = QLabel()
    swatch.setFixedSize(10, 10)
    swatch.setProperty("swatchKind", "keygroup")
    swatch.setProperty("keygroupIndex", index)
    refresh_swatch(swatch)
    row_layout.addWidget(swatch)

    label = QLabel(keygroup_row_text(index, lo, hi))
    label.setObjectName("keygroupRangeLabel")
    row_layout.addWidget(label, stretch=1)

    item.setSizeHint(row_widget.sizeHint())
    list_widget.setItemWidget(item, row_widget)


def keygroup_row_label(list_widget, row):
    # the range-text QLabel of a row built by add_keygroup_row, or None
    widget = list_widget.itemWidget(list_widget.item(row))
    return widget.findChild(QLabel, "keygroupRangeLabel") if widget is not None else None
