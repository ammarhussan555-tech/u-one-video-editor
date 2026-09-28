"""Final verification: create real MainWindow."""
import sys
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

def log(msg):
    print(f"FINAL: {msg}", flush=True)

def main():
    log("starting")
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    log("QApplication created")
    from src.ui.main_window import MainWindow
    log("import OK")
    win = MainWindow()
    log("MainWindow created OK - NO CRASH!")
    win.show()
    log("window shown - SUCCESS!")

if __name__ == "__main__":
    main()
