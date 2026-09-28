"""Diag4: replicates _build_ui EXACTLY step by step."""
import sys
import os
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

def log(msg):
    print(f"DIAG4: {msg}", flush=True)

def main():
    log("starting")
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSplitter
    from PySide6.QtCore import Qt
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
    # Set the attributes __init__ sets before _build_ui
    import threading
    win.pipeline = None
    win.worker = None
    win.cancel_event = threading.Event()
    from src import app_paths
    win.work_dir = str(app_paths.work_root() / "current")
    os.makedirs(win.work_dir, exist_ok=True)
    log("pre-build_ui init done")

    # Replicate _build_ui step by step
    log("creating central widget")
    central = QWidget()
    win.setCentralWidget(central)
    log("setCentralWidget done")
    main = QVBoxLayout(central)
    log("main layout done")

    log("creating header")
    header = QHBoxLayout()
    brand_col = QVBoxLayout()
    title = QLabel("U One")
    sub = QLabel("AI Automatic Video Editor")
    brand_col.addWidget(title)
    brand_col.addWidget(sub)
    header.addLayout(brand_col)
    header.addStretch(1)
    log("header done")

    log("calling check_ffmpeg")
    from src.renderer import check_ffmpeg
    win.ffmpeg_ok = check_ffmpeg()
    log(f"check_ffmpeg done: {win.ffmpeg_ok}")

    log("creating vsplit")
    vsplit = QSplitter(Qt.Vertical)
    main.addWidget(vsplit, 1)
    log("vsplit done")

    log("creating hsplit")
    hsplit = QSplitter(Qt.Horizontal)
    vsplit.addWidget(hsplit)
    log("hsplit added to vsplit")

    log("adding left panel")
    hsplit.addWidget(win._left_panel())
    log("left panel added")

    log("adding center panel")
    hsplit.addWidget(win._center_panel())
    log("center panel added")

    log("adding right panel")
    hsplit.addWidget(win._right_panel())
    log("right panel added")

    log("setSizes hsplit")
    hsplit.setSizes([340, 620, 380])
    log("hsplit sizes done")

    log("creating TimelineWidget")
    from src.ui.timeline_widget import TimelineWidget
    win.timeline_widget = TimelineWidget()
    log("TimelineWidget created")

    log("connecting signal")
    win.timeline_widget.sceneSelected.connect(lambda *a: None)
    log("signal connected")

    log("adding timeline to vsplit")
    vsplit.addWidget(win.timeline_widget)
    vsplit.setSizes([700, 250])
    log("vsplit done")

    log("ALL DONE - no crash!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FAILED: {e}")
        traceback.print_exc()
