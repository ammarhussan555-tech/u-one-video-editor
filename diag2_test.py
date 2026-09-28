"""Granular diagnostic: steps through MainWindow.__init__ to find the crash."""
import sys
import os
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

def log(msg):
    print(f"DIAG2: {msg}", flush=True)

def main():
    log("starting")
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    log("QApplication created")

    from src.ui import main_window as mw_mod
    log("module imported")

    # Manually step through __init__
    from PySide6.QtWidgets import QMainWindow
    win = QMainWindow.__new__(mw_mod.MainWindow)
    log("instance allocated")
    QMainWindow.__init__(win)
    log("QMainWindow.__init__ done")

    win.setWindowTitle("test")
    log("setWindowTitle done")
    win.resize(1500, 950)
    log("resize done")

    try:
        win._apply_modern_theme()
        log("_apply_modern_theme done")
    except Exception as e:
        log(f"_apply_modern_theme FAILED: {e}")

    try:
        from src.project import Project
        win.project = Project("Untitled")
        log("Project created")
    except Exception as e:
        log(f"Project FAILED: {e}")
        traceback.print_exc()

    try:
        win._build_ui()
        log("_build_ui done")
    except Exception as e:
        log(f"_build_ui FAILED: {e}")
        traceback.print_exc()

    log("showing window")
    win.show()
    log("window shown OK - no crash!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"TOP LEVEL FAILED: {e}")
        traceback.print_exc()
