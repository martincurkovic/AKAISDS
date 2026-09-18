from PySide6.QtWidgets import QMainWindow, QLabel


class ProgramEditorWindow(QMainWindow):
    def __init__(self, main_window):
        super().__init__()
        self.setWindowTitle("AKAISDS - Program Editor")
        self.setMinimumSize(800, 500)

        self._main_window = main_window

        # placeholder - real program, keygroup panels come later
        placeholder = QLabel("Program editor - coming soon")
        self.setCentralWidget(placeholder)

    def closeEvent(self, event):
        # runs regardless of how the window closes (close button, command + w, etc)
        self._main_window.show()
        event.accept()
