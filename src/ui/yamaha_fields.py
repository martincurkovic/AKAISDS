"""Widgets for the Yamaha A4000 editor, built from `core/yamaha_params.py` rows.

A `FieldPanel` owns the editing widgets of ONE parameter scope ("program", "easy_edit" or "sample"):
it makes a widget for each `Field` a page asks for (range, enum labels and signedness all come from the
table row, so a page can't disagree with the table), fills them all from a bulk payload in one go
(`fill`), and emits `edited(key, value)` when the user changes one. Widgets start NOT editable
(`set_editable`) - the first editor release is view-only and edits arrive with the write stage.

Layout helpers come from ui/editor_layout.py so the pages look like the S3000/S950 editors'.
"""

import dataclasses

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QSpinBox

from core import yamaha_params as yp
from ui.editor_layout import build_knob_column
from ui.knob import Knob
from ui.note_spinbox import NoteSpinBox


@dataclasses.dataclass(frozen=True)
class Field:
    key: str  # the row's key within the panel's scope
    label: str
    kind: str  # "knob" | "spin" | "note" | "combo" | "check" | "text"
    size: int = 40  # knob diameter, px
    tip: str = ""
    fmt: object = None  # text kind: callable(value) -> str
    specials: object = None  # {value: text} shown instead of the number/note name (e.g. {-1: "Original"})


class SpecialNoteSpinBox(NoteSpinBox):
    """A note spinbox where some values are not notes: key range low -1 / high 128 mean "the sample's
    original", an LFO reset note of -1 means "all notes"."""

    def __init__(self, specials, parent=None):
        self._specials = dict(specials)  # before super().__init__, which asks for text
        super().__init__(parent)

    def textFromValue(self, value):
        if value in self._specials:
            return self._specials[value]
        return super().textFromValue(value)

    def valueFromText(self, text):
        for value, special in self._specials.items():
            if text.strip().lower() == special.lower():
                return value
        return super().valueFromText(text)


class FieldPanel(QObject):
    #: (key, value) - the user changed a widget (never emitted while `fill` is running)
    edited = Signal(str, object)

    def __init__(self, scope):
        super().__init__()
        self.scope = scope
        self.widgets = {}  # key -> widget
        self._fields = {}  # key -> Field
        self._filling = False
        self._editable = False

    # -- building -----------------------------------------------------------------------------------

    def param(self, key):
        return yp.get(self.scope, key)

    def widget(self, field):
        """The (single, cached) widget for `field`."""
        if field.key in self.widgets:
            return self.widgets[field.key]
        p = self.param(field.key)
        key = field.key
        if field.kind == "knob":
            w = Knob()
            w.setRange(p.lo, p.hi)
            w.setDefaultValue(min(max(0, p.lo), p.hi))
            w.setFixedSize(field.size, field.size)
            w.valueChanged.connect(lambda v: self._on_edit(key, int(v)))
        elif field.kind == "spin":
            w = QSpinBox()
            w.setRange(p.lo, p.hi)
            for value, text in (field.specials or {}).items():
                if value == p.lo:
                    w.setSpecialValueText(text)  # e.g. -1 = "=Sample"
            w.valueChanged.connect(lambda v: self._on_edit(key, int(v)))
        elif field.kind == "note":
            w = SpecialNoteSpinBox(field.specials or {})
            w.setRange(p.lo, p.hi)
            w.valueChanged.connect(lambda v: self._on_edit(key, int(v)))
        elif field.kind == "combo":
            w = QComboBox()
            for value, text in sorted(yp.ENUMS[p.enum].items()):
                w.addItem(text, value)
            w.activated.connect(lambda _i, w=w: self._on_edit(key, w.currentData()))
        elif field.kind == "check":
            w = QCheckBox()
            w.toggled.connect(lambda v: self._on_edit(key, int(bool(v))))
        elif field.kind == "text":
            w = QLabel("-")
        else:
            raise ValueError(f"unknown field kind {field.kind!r}")
        if field.tip:
            w.setToolTip(field.tip)
        elif p.kind == "int" and field.kind != "text":
            w.setToolTip(f"{p.name} ({p.lo} to {p.hi})")
        self.widgets[key] = w
        self._fields[key] = field
        w.setEnabled(self._editable and field.kind != "text")
        return w

    def knob_column(self, field):
        """Label above, knob, value readout below (the same column the S3000 editor's knobs use)."""
        knob = self.widget(field)
        column, value_label = build_knob_column(field.label, knob)
        knob.setProperty("valueReadout", value_label)  # a knob set to 0 on a fresh widget emits nothing
        return column

    def knob_row(self, fields, spacing=10):
        row = QHBoxLayout()
        row.setSpacing(spacing)
        for field in fields:
            row.addLayout(self.knob_column(field))
        return row

    def labeled_row(self, field, label_width=110):
        """"Label  [widget]" on one line, like the S3000 editor's Note Range / Tune rows."""
        row = QHBoxLayout()
        label = QLabel(field.label)
        label.setFixedWidth(label_width)
        row.addWidget(label)
        row.addWidget(self.widget(field))
        row.addStretch()
        return row

    # -- values -------------------------------------------------------------------------------------

    def _on_edit(self, key, value):
        if not self._filling:
            self.edited.emit(key, value)

    def set_editable(self, editable):
        self._editable = editable
        for key, w in self.widgets.items():
            w.setEnabled(editable and self._fields[key].kind != "text")

    def fill(self, data, slot=None):
        """Show the values held in the bulk payload `data` (for easy_edit panels: that slot's block)."""
        self._filling = True
        try:
            for key, w in self.widgets.items():
                p = self.param(key)
                self.set_value(key, yp.extract(p, data, slot))
        finally:
            self._filling = False

    def set_value(self, key, value):
        w, field, p = self.widgets[key], self._fields[key], self.param(key)
        if isinstance(w, QLabel):
            w.setText(field.fmt(value) if field.fmt else str(value))
        elif isinstance(w, QComboBox):
            index = w.findData(value)
            if index < 0:
                w.addItem(f"{value} (unexpected)", value)
                index = w.findData(value)
            w.setCurrentIndex(index)
        elif isinstance(w, QCheckBox):
            w.setChecked(bool(value))
        else:  # knob / spin / note: show a value the unit holds even if the manual's range says otherwise
            if value < w.minimum():
                w.setMinimum(value)
            if value > w.maximum():
                w.setMaximum(value)
            w.setValue(value)
            readout = w.property("valueReadout")
            if readout is not None:
                readout.setText(str(value))

    def value(self, key):
        w = self.widgets[key]
        if isinstance(w, QComboBox):
            return w.currentData()
        if isinstance(w, QCheckBox):
            return int(w.isChecked())
        if isinstance(w, QLabel):
            return w.text()
        return w.value()
