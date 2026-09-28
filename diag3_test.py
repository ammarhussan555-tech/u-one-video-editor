"""Diag3: steps through _build_ui panels to find the segfault."""
import sys
import os
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

def log(msg):
    print(f"DIAG3: {msg}", flush=True)

def main():
    log("starting")
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
    app = QApplication(sys.argv)
    log("QApplication created")

    from src.ui.main_window import MainWindow
    from PySide6.QtWidgets import QMainWindow
    win = QMainWindow.__new__(MainWindow)
    QMainWindow.__init__(win)
    win.setWindowTitle("test")
    win.resize(1500, 950)
    win._apply_modern_theme()
    from src.project import Project
    win.project = Project("Untitled")
    log("basic init done")

    # Test each panel
    for name in ["_left_panel", "_center_panel", "_right_panel"]:
        try:
            log(f"calling {name}...")
            panel = getattr(win, name)()
            log(f"{name} OK")
        except Exception as e:
            log(f"{name} FAILED: {e}")
            traceback.print_exc()

    # Test TimelineWidget
    try:
        log("creating TimelineWidget...")
        from src.ui.timeline_widget import TimelineWidget
        tw = TimelineWidget()
        log("TimelineWidget OK")
    except Exception as e:
        log(f"TimelineWidget FAILED: {e}")
        traceback.print_exc()

    # Test check_ffmpeg
    try:
        log("calling check_ffmpeg...")
        from src.renderer import check_ffmpeg
        result = check_ffmpeg()
        log(f"check_ffmpeg OK: {result}")
    except Exception as e:
        log(f"check_ffmpeg FAILED: {e}")
        traceback.print_exc()

    log("all tests done - no crash!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"TOP LEVEL FAILED: {e}")
        traceback.print_exc()
