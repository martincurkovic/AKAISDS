from PySide6.QtCore import Signal
from PySide6.QtWidgets import QAbstractItemView, QListWidget


class ProgramListWidget(QListWidget):
    """The Program Editor's program list. A program can be dragged OUT of it (to Finder, Explorer, a folder): the list only says which
    row was dragged (drag_out_requested) and the editor does the rest - it has to read the program from the sampler to make the file, so it
    owns that (ProgramEditorWindow._drag_program_out). Nothing is dragged INTO the list: a dropped program file is handled by the window."""

    drag_out_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)

    def startDrag(self, supported_actions):
        row = self.currentRow()
        if row >= 0:
            self.drag_out_requested.emit(row)
