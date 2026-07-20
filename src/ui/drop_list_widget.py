from PySide6.QtWidgets import QListWidget
from PySide6.QtCore import Signal


class DropListWidget(QListWidget):
    # QListWidget that also accepts files dragged in from outside the app
    # in addition to whatever internal drag behaviour its already configured with (ie reordering)
    filesDropped = Signal(list)  # list of local file paths

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [
                url.toLocalFile()
                for url in event.mimeData().urls()
                if url.isLocalFile()
            ]
            paths = [p for p in paths if p]
            if paths:
                self.filesDropped.emit(paths)
            event.acceptProposedAction()
        else:
            # not a file drop (ie, internal reorder drag)
            # let QListWidget do its thing
            super().dropEvent(event)
