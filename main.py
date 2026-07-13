import sys
from PySide6.QtWidgets import QApplication, QMainWindow
from components import TransferDashboard


class ApplicationWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AKAI SDS")
        self.setFixedSize(750, 480)

        # instantiate custom UI layout components
        self.dashboard_view = TransferDashboard()

        # mount layout into core central display panel area
        self.setCentralWidget(self.dashboard_view)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ApplicationWindow()
    window.show()
    sys.exit(app.exec())
