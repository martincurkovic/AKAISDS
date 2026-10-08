"""The "Assign Sample" dialog of the Yamaha editor: pick one sample that is not yet in the program.

It only CHOOSES a name - the assignment itself (a backup, the object link message, asking the unit to confirm) is
`YamahaSession.change_link`, driven by the editor window.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from ui.editor_layout import build_sample_list_row_widget

#: the small grey mark after a sample this app sent over MIDI, and what it means (shown under the list)
SENT_MARK = "*"
SENT_NOTE = f"{SENT_MARK} Sent with AKAISDS"


class AssignDialog(QDialog):
    def __init__(self, candidates, program_label, durations=None, parent=None, midi_loaded=()):
        """`candidates`: sample names that can still be assigned, in the unit's order. `durations`: optional {name: "0.45s"} shown
        in grey after each name (what the Samples tab already knows). `midi_loaded`: names this app loaded onto the sampler over
        MIDI - marked with a grey asterisk, because assigning one has left a real A4000 waiting for OK (the editor asks first)."""
        super().__init__(parent)
        self.setWindowTitle("Assign Sample")
        self.setMinimumWidth(380)
        self.chosen = None
        durations = durations or {}
        midi_loaded = set(midi_loaded)

        explain = QLabel(
            f"Choose a sample to add to {program_label}. It is added after the samples already there, with its settings for this "
            "program at their defaults. Samples already in the program are not listed."
        )
        explain.setWordWrap(True)
        self.list = QListWidget()
        self.list.setObjectName("assignList")
        for name in candidates:
            # the same row the Samples tab's list uses: the name, and a grey label for what is known about it. The item has NO text of
            # its own (it would be painted twice under the row widget); the name is in UserRole.
            row_widget, _name_label, grey_label = build_sample_list_row_widget(name)
            grey_label.setText(" ".join(x for x in (durations.get(name, ""), SENT_MARK if name in midi_loaded else "") if x))
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setSizeHint(row_widget.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row_widget)
        self.midi_note = QLabel(SENT_NOTE)
        self.midi_note.setObjectName("mutedLabel")
        self.midi_note.setVisible(any(name in midi_loaded for name in candidates))
        self.empty = QLabel("Every sample on the sampler is already in this program." if not candidates else "")
        self.empty.setObjectName("mutedLabel")
        self.empty.setVisible(not candidates)
        self.buttons = QDialogButtonBox()
        self.assign_button = self.buttons.addButton("Assign", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept_current)
        self.buttons.rejected.connect(self.reject)
        self.list.itemDoubleClicked.connect(lambda _item: self._accept_current())
        self.list.currentItemChanged.connect(lambda *_: self._update_buttons())

        layout = QVBoxLayout(self)
        layout.addWidget(explain)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.midi_note)
        layout.addWidget(self.empty)
        layout.addWidget(self.buttons)
        if candidates:
            self.list.setCurrentRow(0)
        self._update_buttons()

    def _update_buttons(self):
        self.assign_button.setEnabled(self.list.currentItem() is not None)

    def _accept_current(self):
        item = self.list.currentItem()
        if item is not None:
            self.chosen = item.data(Qt.ItemDataRole.UserRole)
            self.accept()
