"""Diag5: test emoji/unicode widgets."""
import sys
import os
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

def log(msg):
    print(f"DIAG5: {msg}", flush=True)

def main():
    log("starting")
    from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QSlider
    from PySide6.QtCore import Qt
    app = QApplication(sys.argv)
    log("QApplication created")

    w = QWidget()
    lay = QVBoxLayout(w)

    log("creating button with play symbol")
    b1 = QPushButton("▶")
    lay.addWidget(b1)
    log("play button OK")

    log("creating button with emoji")
    b2 = QPushButton("🎬  CREATE VIDEO")
    lay.addWidget(b2)
    log("emoji button OK")

    log("creating slider")
    s = QSlider(Qt.Horizontal)
    lay.addWidget(s)
    log("slider OK")

    log("showing widget")
    w.show()
    log("ALL OK - no crash!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FAILED: {e}")
        traceback.print_exc()
