from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ProgramEditorWindow(QMainWindow):
    def __init__(self, main_window):
        super().__init__()
        self.setWindowTitle("AKAISDS - Program Editor")
        self.setMinimumSize(800, 500)

        self._main_window = main_window

        # placeholder - real program, keygroup panels come later
        fake_programs = ["BASS STAB", "EPIANO", "BRASS HIT", "PAD STRINGS"]

        self.program_list = QListWidget()
        self.program_list.addItems(fake_programs)
        self.program_list.setFixedWidth(180)
        self.program_list.currentItemChanged.connect(self._on_program_selected)

        self.detail_label = QLabel("Select a program")

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)

        content_layout = QHBoxLayout()
        content_layout.addWidget(self.program_list)
        content_layout.addWidget(self.detail_label, stretch=1)

        main_layout = QVBoxLayout()
        main_layout.addLayout(content_layout)
        main_layout.addWidget(close_button)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

    def _on_program_selected(self, current, previous):
        if current is not None:
            self.detail_label.setText(f"Selected: {current.text()}")

    def closeEvent(self, event):
        # runs regardless of how the window closes (close button, command + w, etc)
        self._main_window.show()
        event.accept()
