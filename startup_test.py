"""Minimal startup test: just opens a window. No multimedia, no heavy imports."""
import sys

def main():
    print("TEST: starting", flush=True)
    from PySide6.QtWidgets import QApplication, QMainWindow, QLabel
    print("TEST: QtWidgets imported", flush=True)
    app = QApplication(sys.argv)
    print("TEST: QApplication created", flush=True)
    win = QMainWindow()
    win.setWindowTitle("U One - Startup Test")
    win.resize(400, 300)
    win.setCentralWidget(QLabel("If you see this, the build works!"))
    win.show()
    print("TEST: window shown", flush=True)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
