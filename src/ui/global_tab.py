"""The Program Editor's Global tab: the S2000/S3000's GLOBAL-page settings (`core/global_settings.py`) - tuning, output level, external controller,
MIDI program change and Play button, SCSI. Settings of the whole sampler, not of a program.

A view only: `ProgramEditorWindow` owns the `BridgeWorker`, so this emits `setting_chosen(key, value)` when the USER changes something and is fed with
`set_values()` - loading a value never emits (combos use `activated`; spinboxes are `blockSignals`'d while set). A control stays disabled until a value for
its key has been read, so nothing can be written that was never read. Spinbox and knob edits are debounced (a wheel over a box would
otherwise send a write + read-back per notch; a knob drag also commits on release); combos send at once.

The External controller combo here mirrors the one in the Program tab's Modulation card (`build_external_controller_combo`); the window keeps the two in step.
"""

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractSlider,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.global_settings import (
    EXTERNAL_CONTROLLER_LABELS,
    PROGRAM_CHANGE_OMNI,
    SECTOR_SIZE_LABELS,
)
from ui import tooltips as tt
from ui.editor_layout import (
    CARD_SPACING,
    build_knob_value_row,
    build_labeled_combo_column,
    build_paired_row,
    equalize_card_heights,
)
from ui.knob import Knob
from ui.note_spinbox import NoteSpinBox
from ui.qt_helpers import build_scroll_area, build_section_card

_SPINBOX_DEBOUNCE_MS = 250

# Settings the tab SHOWS (they read fine) but never offers to change, key -> why, for the tooltip. Measured on a real S2000, 2026-10-09:
# - tune_semitones / tune_cents: a write is accepted and reads back, and the register ends up identical to one set on the panel (a 768-register dump
#   of byte/word/dword banks 0-255 differs only in the panel-cursor byte 49) - yet it has no effect, so the sampler stores it without acting on it.
#   tools/s2000_tune_trigger_check.py looks for what makes it act.
# - program_change_channel: the sampler answers OK and the register keeps its old value, whatever is written.
# To re-enable one, delete its entry here once a write is known to take effect on the machine (see that tool's docstring).
DISABLED_SETTINGS = {
    "tune_semitones": "Disabled for now: the sampler doesn't apply a written value yet.",
    "tune_cents": "Disabled for now: the sampler doesn't apply a written value yet.",
    "program_change_channel": "Disabled for now: the sampler ignores writes to this setting.",
}
# the Multi tab's own page margin (ProgramEditorWindow._build_multis_tab): a page with no list
# beside it sits this far in from the tab frame on every side (the right also clears the scroll bar)
_PAGE_MARGIN = 14
_KNOB_SIZE = 28  # the editor's small-knob convention (Multi tab, modulation amounts)


def build_external_controller_combo():
    """The Breath/Footpedal/Volume combo - built here so the Modulation card's copy is identical."""
    combo = QComboBox()
    for label in EXTERNAL_CONTROLLER_LABELS:
        combo.addItem(label)
    combo.setCurrentIndex(-1)
    combo.setEnabled(False)
    combo.setToolTip(tt.EXTERNAL_CONTROLLER_COMBO)
    return combo


class GlobalSettingsTab(QWidget):
    setting_chosen = Signal(str, int)  # key, value

    def __init__(self, parent=None):
        super().__init__(parent)
        self._widgets = {}  # key -> QComboBox | QSpinBox | Knob
        self._timers = {}  # key -> debounce QTimer (spinboxes and knobs)
        self._knob_labels = {}  # key -> the value QLabel beside a Knob

        self.external_controller_combo = build_external_controller_combo()
        self._widgets["external_controller"] = self.external_controller_combo
        self.external_controller_combo.activated.connect(
            lambda i: self.setting_chosen.emit("external_controller", i)
        )

        tuning = self._knob("tune_semitones", -50, 50)
        fine = self._knob("tune_cents", -50, 50)
        output = self._combo(
            "output_level",
            [(f"{db:+d} dB" if db else "0 dB", db) for db in range(-18, 19, 6)],
        )
        tuning_card = build_section_card(
            "Tuning & Output",
            self._row(("Tune", tuning), ("Fine tune", fine), ("Output level", output)),
        )

        controller_note = QLabel('The source behind "External" in the modulation matrix.')
        controller_note.setObjectName("modFootnote")
        controller_note.setWordWrap(True)
        controller_row = QHBoxLayout()
        controller_row.addLayout(
            build_labeled_combo_column("Controller", self.external_controller_combo, center=False)
        )
        controller_row.addStretch()
        note_row = QHBoxLayout()
        note_row.addWidget(controller_note)
        controller_card = build_section_card("External Controller", controller_row, note_row)

        program_change = self._combo(
            "program_change_channel",
            [("Off", 0)]
            + [(str(channel), channel) for channel in range(1, 17)]
            + [("Omni", PROGRAM_CHANGE_OMNI)],
        )
        midi_card = build_section_card(
            "MIDI", self._row(("Program change channel", program_change))
        )

        play_note = NoteSpinBox()
        play_note.setRange(21, 127)
        self._register("play_note", play_note)
        play_channel = self._combo(
            "play_channel", [(str(channel), channel) for channel in range(1, 17)]
        )
        play_velocity = self._spinbox("play_velocity", 0, 127)
        play_card = build_section_card(
            "Play Button",
            self._row(("Note", play_note), ("Channel", play_channel), ("Velocity", play_velocity)),
        )

        disk_id = self._combo("scsi_disk_id", [(str(i), i) for i in range(8)])
        sector = self._combo("scsi_sector", [(label, i) for i, label in enumerate(SECTOR_SIZE_LABELS)])
        local_id = self._combo("scsi_local_id", [(str(i), i) for i in range(8)])
        scsi_card = build_section_card(
            "SCSI",
            self._row(("Disk ID", disk_id), ("Sector size", sector), ("Local ID", local_id)),
        )

        equalize_card_heights(tuning_card, controller_card)
        equalize_card_heights(midi_card, play_card)

        self._unavailable_label = QLabel()
        self._unavailable_label.setObjectName("mutedLabel")
        self._unavailable_label.setWordWrap(True)
        self._unavailable_label.hide()

        page_layout = QVBoxLayout()
        page_layout.setSpacing(CARD_SPACING)
        page_layout.setContentsMargins(_PAGE_MARGIN, _PAGE_MARGIN, _PAGE_MARGIN, _PAGE_MARGIN)
        page_layout.addWidget(self._unavailable_label)
        page_layout.addLayout(build_paired_row(tuning_card, controller_card))
        page_layout.addLayout(build_paired_row(midi_card, play_card))
        page_layout.addWidget(scsi_card)
        page_layout.addStretch()
        page = QWidget()
        page.setLayout(page_layout)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(build_scroll_area(page))

        for key, widget in self._widgets.items():
            tip = tt.GLOBAL_TOOLTIPS[key]
            if key in DISABLED_SETTINGS:
                tip = f"{tip} {DISABLED_SETTINGS[key]}"
            widget.setToolTip(tip)
            widget.setEnabled(False)

    # -- construction helpers ----------------------------------------------------------

    def _register(self, key, widget):
        self._widgets[key] = widget
        if isinstance(widget, (QSpinBox, QAbstractSlider)):
            if isinstance(widget, QSpinBox):
                widget.setKeyboardTracking(False)
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(_SPINBOX_DEBOUNCE_MS)
            timer.timeout.connect(lambda k=key: self._emit_spinbox(k))
            self._timers[key] = timer
            widget.valueChanged.connect(lambda _v, t=timer: t.start())
        return widget

    def _spinbox(self, key, low, high, suffix=""):
        box = QSpinBox()
        box.setRange(low, high)
        if suffix:
            box.setSuffix(suffix)
        return self._register(key, box)

    def _knob(self, key, low, high):
        """A small bipolar knob with its value to the right; returns the widget to put in a row.
        A drag commits on release (the debounce alone would wait for the pointer to rest)."""
        knob = Knob()
        knob.setRange(low, high)
        knob.setDefaultValue(0)
        knob.setFixedSize(_KNOB_SIZE, _KNOB_SIZE)
        self._register(key, knob)
        knob.sliderReleased.connect(lambda k=key: self.flush(k))
        row, value_label = build_knob_value_row(knob)
        self._knob_labels[key] = value_label
        container = QWidget()
        # not the page background inside a card (see ProgramEditorWindow._build_multi_part_knob)
        container.setObjectName("transparentContainer")
        container.setLayout(row)
        return container

    def _combo(self, key, items):
        combo = QComboBox()
        for label, value in items:
            combo.addItem(label, value)
        combo.setCurrentIndex(-1)
        combo.activated.connect(
            lambda i, k=key, c=combo: self.setting_chosen.emit(k, c.itemData(i))
        )
        return self._register(key, combo)

    @staticmethod
    def _row(*labelled):
        row = QHBoxLayout()
        for label, widget in labelled:
            row.addLayout(build_labeled_combo_column(label, widget))
        row.addStretch()
        return row

    def _emit_spinbox(self, key):
        self.setting_chosen.emit(key, self._widgets[key].value())

    def flush(self, key):
        """Send a pending debounced spinbox/knob edit now (tests; also if the window closes mid-edit)."""
        timer = self._timers.get(key)
        if timer is not None and timer.isActive():
            timer.stop()
            self._emit_spinbox(key)

    # -- values ------------------------------------------------------------------------------

    def set_value(self, key, value):
        """Show a value read from the sampler and enable its control (unless it is in DISABLED_SETTINGS). Never emits."""
        widget = self._widgets[key]
        widget.blockSignals(True)
        try:
            if isinstance(widget, (QSpinBox, QAbstractSlider)):
                widget.setValue(value)
                if key in self._knob_labels:
                    # blockSignals also silenced the knob's own readout
                    self._knob_labels[key].setText(str(value))
            elif key == "external_controller":
                widget.setCurrentIndex(value)
            else:
                widget.setCurrentIndex(widget.findData(value))
        finally:
            widget.blockSignals(False)
        # a disabled setting still shows what the sampler holds
        widget.setEnabled(key not in DISABLED_SETTINGS)

    def set_values(self, values):
        """Show every value in `values`; a setting that isn't in it (couldn't be read) stays disabled."""
        for key, widget in self._widgets.items():
            if key in values:
                self.set_value(key, values[key])
            else:
                widget.setEnabled(False)
        self._unavailable_label.hide()

    def set_unavailable(self, text):
        """Nothing could be read: disable everything and say why (short)."""
        for widget in self._widgets.values():
            widget.setEnabled(False)
        self._unavailable_label.setText(text)
        self._unavailable_label.show()
