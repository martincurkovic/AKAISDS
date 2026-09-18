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
        self._fake_keygroups_by_program = {
            "Bass stab": ["C1 - F2", "F#2 - C4", "C#4 - C6"],
            "EPiano warm": ["A0 - G3", "G#3 - C8"],
            "Brass hit": ["C1 - C8"],
            "Pad strings": ["C1 - E3", "F3 - C6", "C#6 - C8"],
        }
        self.program_list = QListWidget()
        self.program_list.addItems(list(self._fake_keygroups_by_program.keys()))
        self.program_list.setFixedWidth(160)

        self.keygroup_list = QListWidget()
        self.keygroup_list.setFixedWidth(160)
        self.keygroup_list.currentItemChanged.connect(self._on_keygroup_selected)

        self.detail_label = QLabel("Select a program")

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)

        content_layout = QHBoxLayout()
        content_layout.addWidget(self.program_list)
        content_layout.addWidget(self.keygroup_list)
        content_layout.addWidget(self.detail_label, stretch=1)

        main_layout = QVBoxLayout()
        main_layout.addLayout(content_layout)
        main_layout.addWidget(close_button)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        self.program_list.currentItemChanged.connect(self._on_program_selected)
        self.program_list.setCurrentRow(0)

    def _on_program_selected(self, current, previous):
        self.keygroup_list.clear()
        self.detail_label.setText("Select a keygroup")
        if current is None:
            return
        keygroups = self._fake_keygroups_by_program[current.text()]
        self.keygroup_list.addItems(keygroups)

    def _on_keygroup_selected(self, current, previous):
        if current is not None:
            self.detail_label.setText(f"Keygroup range: {current.text()}")

    def closeEvent(self, event):
        # runs regardless of how the window closes (close button, command + w, etc)
        self._main_window.show()
        event.accept()
