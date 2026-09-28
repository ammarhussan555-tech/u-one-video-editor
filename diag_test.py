"""Diagnostic: prints each startup step to find the hang."""
import sys
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

def log(msg):
    print(f"DIAG: {msg}", flush=True)

def main():
    log("starting")
    log("importing QApplication")
    from PySide6.QtWidgets import QApplication
    log("creating QApplication")
    app = QApplication(sys.argv)
    log("importing MainWindow")
    from src.ui.main_window import MainWindow
    log("MainWindow imported OK")
    log("creating MainWindow...")
    win = MainWindow()
    log("MainWindow created OK")
    log("showing window...")
    win.show()
    log("window shown, entering event loop")
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
