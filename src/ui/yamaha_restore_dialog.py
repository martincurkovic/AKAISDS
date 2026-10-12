"""The Restore dialog of the Yamaha editor: pick one of the `.syx` backups the editor saved (core/yamaha_backups.py), newest first.

It only CHOOSES a backup - reading the object, planning, confirming and writing is `controller/yamaha_restore.RestoreJob` driven by
the editor window. Backups of the object currently selected in the editor are shown by default ("Only this object" unticked shows all).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class RestoreDialog(QDialog):
    def __init__(self, backups, current=None, folder="", parent=None):
        """`backups`: [BackupEntry] newest first. `current`: (fmt, name) of the object selected in the editor, or None. `folder`:
        where the backups live (shown, and where Browse starts)."""
        super().__init__(parent)
        self.setWindowTitle("Restore from Backup")
        self.setMinimumWidth(460)
        self._backups = list(backups)
        self._current = current
        self._folder = folder
        self.chosen_path = None

        explain = QLabel(
            "Pick a backup to put back. Only the values the editor can write are restored, and the object as it is now is saved "
            "as a new backup first, so a restore can be undone."
        )
        explain.setWordWrap(True)
        self.only_current = QCheckBox("Only backups of the selected object")
        self.only_current.setChecked(current is not None)
        self.only_current.setVisible(current is not None)
        self.only_current.toggled.connect(self._fill)
        self.list = QListWidget()
        self.list.setObjectName("restoreList")
        self.list.itemDoubleClicked.connect(lambda _item: self._accept_current())
        self.empty = QLabel("")
        self.empty.setObjectName("mutedLabel")
        self.empty.setWordWrap(True)
        self.browse_button = QPushButton("Browse...")
        self.browse_button.setToolTip("Choose a .syx file from somewhere else")
        self.browse_button.clicked.connect(self._browse)
        self.buttons = QDialogButtonBox()
        self.restore_button = self.buttons.addButton("Restore...", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept_current)
        self.buttons.rejected.connect(self.reject)
        self.list.currentItemChanged.connect(lambda *_: self._update_buttons())

        layout = QVBoxLayout(self)
        layout.addWidget(explain)
        layout.addWidget(self.only_current)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.empty)
        layout.addWidget(self.browse_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.buttons)
        self._fill()

    def _shown(self):
        if self.only_current.isChecked() and self._current is not None:
            return [b for b in self._backups if (b.fmt, b.name) == self._current]
        return self._backups

    def _fill(self, _checked=None):
        self.list.clear()
        for entry in self._shown():
            item = QListWidgetItem(entry.text)
            item.setData(Qt.ItemDataRole.UserRole, entry.path)
            item.setToolTip(entry.path)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
            self.empty.setText("")
        else:
            where = f" in {self._folder}" if self._folder else ""
            self.empty.setText(f"No backups{where} for this object yet - one is saved before the first change you make to it.")
        self._update_buttons()

    def _update_buttons(self):
        self.restore_button.setEnabled(self.list.currentItem() is not None)

    def _accept_current(self):
        item = self.list.currentItem()
        if item is not None:
            self.chosen_path = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a backup", self._folder, "SysEx backups (*.syx)")
        if path:
            self.chosen_path = path
            self.accept()
