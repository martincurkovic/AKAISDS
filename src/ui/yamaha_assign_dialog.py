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


class AssignDialog(QDialog):
    def __init__(self, candidates, program_label, durations=None, parent=None):
        """`candidates`: sample names that can still be assigned, in the unit's order. `durations`: optional {name: "0.45s"} shown
        in grey after each name (what the Samples tab already knows)."""
        super().__init__(parent)
        self.setWindowTitle("Assign Sample")
        self.setMinimumWidth(380)
        self.chosen = None
        durations = durations or {}

        explain = QLabel(
            f"Choose a sample to add to {program_label}. It is added after the samples already there, with its settings for this "
            "program at their defaults. Samples already in the program are not listed."
        )
        explain.setWordWrap(True)
        self.list = QListWidget()
        self.list.setObjectName("assignList")
        for name in candidates:
            suffix = durations.get(name, "")
            item = QListWidgetItem(f"{name}    {suffix}" if suffix else name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.list.addItem(item)
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
