import sys

from PySide6.QtWidgets import QApplication, QLabel, QMainWindow


class ApocalypsoWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Apocalypso")
        self.resize(1000, 700)

        label = QLabel("APOCALYPSO\nOffline Survival Intelligence")
        label.setStyleSheet("""
            QLabel {
                font-size: 28px;
                font-weight: bold;
                padding: 30px;
            }
        """)

        self.setCentralWidget(label)


def main():
    app = QApplication(sys.argv)

    window = ApocalypsoWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()