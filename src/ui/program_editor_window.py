from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from core.program_editor_bridge import KeygroupLoader


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
        self._loader = KeygroupLoader(self._bridge, program_index)
        self._loader.keygroups_loaded.connect(self._on_keygroups_loaded)
        self._loader.load_failed.connect(self._on_load_failed)
        self._loader.start()

    def _on_keygroups_loaded(self, program_index, keygroup_ranges):
        # ignore result for program the user has already clicked away from
        if program_index != self.program_list.currentRow():
            return
        self.keygroup_list.addItems(keygroup_ranges)
        self.detail_label.setText("Select a keygroup")

    def _on_load_failed(self, program_index, error_message):
        if program_index != self.program_list.currentRow():
            return
        self.detail_label.setText(f"Couldn't load keygroups: {error_message}")

    def _on_keygroup_selected(self, current, previous):
        if current is not None:
            self.detail_label.setText(f"Keygroup range: {current.text()}")

    def closeEvent(self, event):
        # runs regardless of how the window closes (close button, command + w, etc)
        self._main_window.show()
        event.accept()
