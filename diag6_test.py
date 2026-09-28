"""Diag6: isolate addWidget crash."""
import sys
import os
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

def log(msg):
    print(f"DIAG6: {msg}", flush=True)

def main():
    log("starting")
    from PySide6.QtWidgets import QApplication, QSplitter, QWidget, QVBoxLayout, QLabel
    from PySide6.QtCore import Qt
    app = QApplication(sys.argv)
    log("QApplication created")

    from src.ui.main_window import MainWindow
    from PySide6.QtWidgets import QMainWindow
    win = QMainWindow.__new__(MainWindow)
    QMainWindow.__init__(win)
    win._apply_modern_theme()
    from src.project import Project
    win.project = Project("Untitled")
    from src.renderer import check_ffmpeg
    win.ffmpeg_ok = False
    log("init done")

    log("creating hsplit")
    hsplit = QSplitter(Qt.Horizontal)

    log("creating center panel standalone")
    panel = win._center_panel()
    log("center panel created OK")

    log("calling hsplit.addWidget(panel)")
    hsplit.addWidget(panel)
    log("addWidget OK - no crash!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FAILED: {e}")
        traceback.print_exc()
