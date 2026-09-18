from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
import s3k.params as p


class ProgramEditorWindow(QMainWindow):
    def __init__(self, main_window, bridge):
        super().__init__()
        self.setWindowTitle("AKAISDS - Program Editor")
        self.setMinimumSize(800, 500)

        self._main_window = main_window
        self._bridge = bridge

        # placeholder - real program, keygroup panels come later
        self.program_list = QListWidget()
        self.program_list.addItems(self._bridge.program_list())
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

        program_index = self.program_list.currentRow()
        keygroup_ranges = []
        keygroup_index = 0
        while True:
            try:
                lo = self._bridge.get_parameter(
                    p.lookup("LONOTE", "keygroup"),
                    program_index,
                    keygroup=keygroup_index,
                )
                hi = self._bridge.get_parameter(
                    p.lookup("HINOTE", "keygroup"),
                    program_index,
                    keygroup=keygroup_index,
                )
            except ValueError:
                break  # ran past the last real keygroup for this program
            keygroup_ranges.append(f"{lo} - {hi}")
            keygroup_index += 1
        self.keygroup_list.addItems(keygroup_ranges)

    def _on_keygroup_selected(self, current, previous):
        if current is not None:
            self.detail_label.setText(f"Keygroup range: {current.text()}")

    def closeEvent(self, event):
        # runs regardless of how the window closes (close button, command + w, etc)
        self._main_window.show()
        event.accept()
