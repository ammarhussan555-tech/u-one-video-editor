"""U One — AI Automatic Video Editor. Main window: left input | center preview | bottom timeline | right scenes."""
import glob
import os
import re
import shutil
import sys
import threading
import time

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTextEdit, QLabel, QPushButton, QComboBox, QCheckBox, QProgressBar,
    QFileDialog, QMessageBox, QTabWidget, QListWidget, QListWidgetItem,
    QTableWidget, QTableWidgetItem, QRadioButton, QButtonGroup, QGroupBox,
    QFormLayout, QLineEdit, QSpinBox, QScrollArea, QFrame, QSlider)
from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QPixmap
# NOTE: QtMultimedia / QtMultimediaWidgets are imported lazily inside
# _ensure_player() — importing them at startup can hang on some systems
# during the FFmpeg backend init.

from src.render_engine import RenderEngine, STAGES
from src.project import Project
from src.project_manager import ProjectManager
from src.settings import Settings
from src.renderer import check_ffmpeg
from src.ui.timeline_widget import TimelineWidget
from src.text_captions import youtube_suggest
from src.media_search import Asset, test_provider
from src import secure_store, app_paths
from src.version import __version__, __display_name__

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class PipelineWorker(QThread):
    progress = Signal(int, str, str)
    log = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, pipeline, preview):
        super().__init__()
        self.pipeline = pipeline
        self.preview = preview
        # RenderEngine takes progress/log callbacks via constructor, not run().
        pipeline.progress_cb = lambda i, t, s: self.progress.emit(i, t, s)
        pipeline.log_cb = lambda m: self.log.emit(m)

    def run(self):
        try:
            out = self.pipeline.run(preview=self.preview)
            self.finished.emit(out)
        except Exception as e:  # noqa: BLE001
            # Include structured technical details so the error dialog
            # can show the actual cause (stage, FFmpeg output), not just
            # the user-friendly message.
            msg = str(e)
            details = []
            for attr in ("stage", "scene", "exit_code", "reason",
                         "recovery_hint", "stderr_tail"):
                v = getattr(e, attr, None)
                if v:
                    details.append(f"{attr}: {v}")
            if details:
                msg = msg + "\n\n--- Technical details ---\n" + "\n".join(details)
            self.error.emit(msg)


class CaptionEditWorker(QThread):
    """Re-burn captions in the background (fast single ffmpeg pass)."""
    log = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, session_path, template, font_size):
        super().__init__()
        self.session_path = session_path
        self.template = template
        self.font_size = font_size

    def run(self):
        try:
            from src.caption_editor import load_edit_session, reburn_captions
            from src.ffmpeg_util import find_ffmpeg
            from src.ffmpeg_engine import FFmpegEngine
            data = load_edit_session(self.session_path)
            template = self.template
            size = self.font_size
            out = data["output_video"]
            # Hardware encoder for 3-5x faster burn-in when available.
            hw_enc = None
            try:
                hw_enc = FFmpegEngine(
                    find_ffmpeg()).detect_hw_encoder("h264")
            except Exception:  # noqa: BLE001
                pass
            self.log.emit(f"Applying caption style: {template}, size {size}..."
                          + (" (hardware accelerated)" if hw_enc else ""))
            reburn_captions(
                data["clean_video"], data["sentence_timings"],
                data.get("word_timings"), template, size, out,
                fmt=data.get("fmt", "16:9"), res=data.get("res", "1080p"),
                ffmpeg_path=find_ffmpeg(), hw_enc=hw_enc,
                log_cb=lambda m: self.log.emit(m))
            # Remember the new style in the session.
            data["template"] = template
            data["font_size"] = size
            import json
            with open(self.session_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            self.finished.emit(out)
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))


class TimelineEditWorker(QThread):
    """Apply a CapCut-style timeline edit (delete/volume) in background."""
    log = Signal(str)
    finished = Signal(str, dict)  # (output_path, op_info)
    error = Signal(str)

    def __init__(self, input_path, operation, params):
        super().__init__()
        self.input_path = input_path
        self.operation = operation  # "delete_range" | "volume_range"
        self.params = params

    def run(self):
        try:
            import os
            import tempfile
            from src.timeline_editor import delete_range, volume_range
            from src.ffmpeg_util import find_ffmpeg
            from src.ffmpeg_engine import FFmpegEngine
            inp = self.input_path
            fd, out = tempfile.mkstemp(suffix=".mp4",
                                       prefix="uone_tl_")
            os.close(fd)
            hw_enc = None
            try:
                hw_enc = FFmpegEngine(
                    find_ffmpeg()).detect_hw_encoder("h264")
            except Exception:  # noqa: BLE001
                pass
            if self.operation == "delete_range":
                self.log.emit(
                    f"Deleting {self.params['start']:.1f}s–"
                    f"{self.params['end']:.1f}s from video..."
                    + (" (hardware accelerated)" if hw_enc else ""))
                delete_range(inp, self.params["start"],
                             self.params["end"], out,
                             ffmpeg_path=find_ffmpeg(), hw_enc=hw_enc,
                             log_cb=lambda m: self.log.emit(m))
            elif self.operation == "volume_range":
                f = self.params["factor"]
                what = ("Muting" if f == 0 else
                        f"Setting volume x{f:.2f}")
                self.log.emit(
                    f"{what} "
                    f"({self.params['start']:.1f}s–{self.params['end']:.1f}s)...")
                volume_range(inp, self.params["start"],
                             self.params["end"], f, out,
                             duration=self.params.get("duration"),
                             ffmpeg_path=find_ffmpeg(),
                             log_cb=lambda m: self.log.emit(m))
            else:
                raise ValueError(
                    f"unknown timeline operation: {self.operation}")
            self.finished.emit(out, {"op": self.operation,
                                     "params": self.params})
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))


class PanelDivider(QWidget):
    """Thin draggable divider between panels (CapCut-style resizing).

    Dragging it calls on_drag(dx_pixels). Implemented manually instead of
    QSplitter because QSplitter segfaulted on some Windows systems.
    """
    def __init__(self, on_drag, parent=None):
        super().__init__(parent)
        self.on_drag = on_drag
        self.setFixedWidth(8)
        self.setCursor(Qt.SplitHCursor)
        self.setMouseTracking(True)
        self._dragging = False
        self._last_x = 0
        self.setToolTip("Drag karke panel ka size badlo")

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor
        p = QPainter(self)
        # Subtle grip dots in the middle.
        p.setPen(QColor("#3a3f4b"))
        cx = self.width() // 2
        for y in range(self.height() // 2 - 20, self.height() // 2 + 21, 8):
            p.drawPoint(cx, y)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._last_x = event.globalPosition().x()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging:
            x = event.globalPosition().x()
            dx = int(x - self._last_x)
            self._last_x = x
            if dx:
                self.on_drag(dx)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._dragging = False
        event.accept()


class HPanelDivider(QWidget):
    """Horizontal divider: drag up/down to resize the timeline (CapCut-style)."""
    def __init__(self, on_drag, parent=None):
        super().__init__(parent)
        self.on_drag = on_drag
        self.setFixedHeight(8)
        self.setCursor(Qt.SplitVCursor)
        self.setMouseTracking(True)
        self._dragging = False
        self._last_y = 0
        self.setToolTip("Drag karke timeline ka size badlo")

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor
        p = QPainter(self)
        p.setPen(QColor("#3a3f4b"))
        cy = self.height() // 2
        for x in range(self.width() // 2 - 20, self.width() // 2 + 21, 8):
            p.drawPoint(x, cy)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._last_y = event.globalPosition().y()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging:
            y = event.globalPosition().y()
            dy = int(y - self._last_y)
            self._last_y = y
            if dy:
                self.on_drag(dy)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._dragging = False
        event.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(__display_name__)
        self.resize(1500, 950)
        self.setMinimumSize(1280, 720)
        self._apply_modern_theme()
        self.project = Project("Untitled")
        self.pipeline = None
        self.worker = None
        self.cancel_event = threading.Event()
        # writable data lives in the per-user app directory, never in the
        # install folder or the current working directory
        self.work_dir = str(app_paths.work_root() / "current")
        os.makedirs(self.work_dir, exist_ok=True)
        self._build_ui()
        self.project.start_autosave(60, directory=str(app_paths.projects_dir()))
        self._offer_autosave_recovery()

    def _apply_modern_theme(self):
        """Modern dark theme: deep backgrounds, cyan accent, rounded cards."""
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #12141a;
                color: #e8eaf0;
                font-family: "Segoe UI", "Inter", sans-serif;
                font-size: 13px;
            }
            /* Header branding */
            QLabel#brandTitle {
                font-size: 20px; font-weight: 800; color: #ffffff;
            }
            QLabel#brandSub {
                font-size: 11px; color: #8b93a7;
            }
            /* Section headings */
            QLabel#sectionHead {
                font-size: 14px; font-weight: 700; color: #ffffff;
                padding: 4px 0px;
            }
            /* Cards */
            QGroupBox {
                background-color: #1b1f2a;
                border: 1px solid #2a3042;
                border-radius: 10px;
                margin-top: 14px;
                padding-top: 10px;
                font-weight: 700; color: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px; padding: 0 6px;
                color: #7dd3fc;
            }
            /* Buttons */
            QPushButton {
                background-color: #232936;
                border: 1px solid #333c52;
                border-radius: 8px;
                padding: 8px 14px;
                color: #e8eaf0;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2c3446;
                border-color: #3d4a68;
            }
            QPushButton:pressed { background-color: #1a2030; }
            QPushButton:disabled { color: #5a6378; background-color: #1a1e28; }
            QPushButton#primaryBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0ea5e9, stop:1 #6366f1);
                border: none; border-radius: 10px;
                font-size: 16px; font-weight: 800; color: white;
                padding: 12px;
            }
            QPushButton#primaryBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #38bdf8, stop:1 #818cf8);
            }
            QPushButton#dangerBtn {
                background-color: #3a1d24; border: 1px solid #7f2d3a;
                color: #fda4af;
            }
            QPushButton#dangerBtn:hover { background-color: #4a2530; }
            /* Inputs */
            QTextEdit, QLineEdit, QPlainTextEdit {
                background-color: #161a23;
                border: 1px solid #2a3042;
                border-radius: 8px;
                padding: 8px;
                color: #e8eaf0;
                selection-background-color: #0ea5e9;
            }
            QTextEdit:focus, QLineEdit:focus {
                border: 1px solid #0ea5e9;
            }
            QComboBox {
                background-color: #161a23;
                border: 1px solid #2a3042;
                border-radius: 8px;
                padding: 6px 10px;
                color: #e8eaf0;
            }
            QComboBox:hover { border-color: #3d4a68; }
            QComboBox QAbstractItemView {
                background-color: #1b1f2a;
                border: 1px solid #2a3042;
                selection-background-color: #0ea5e9;
                color: #e8eaf0;
            }
            QCheckBox, QRadioButton { color: #c6cbd8; spacing: 8px; }
            QCheckBox::indicator, QRadioButton::indicator {
                width: 16px; height: 16px;
            }
            /* Progress */
            QProgressBar {
                background-color: #161a23;
                border: 1px solid #2a3042;
                border-radius: 8px;
                text-align: center;
                color: #e8eaf0;
                height: 22px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0ea5e9, stop:1 #6366f1);
                border-radius: 7px;
            }
            /* Tabs */
            QTabWidget::pane {
                border: 1px solid #2a3042;
                border-radius: 8px;
                background-color: #161a23;
            }
            QTabBar::tab {
                background-color: #1b1f2a;
                color: #8b93a7;
                padding: 8px 16px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-right: 4px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background-color: #232936;
                color: #7dd3fc;
            }
            /* Lists & tables */
            QListWidget, QTableWidget {
                background-color: #161a23;
                border: 1px solid #2a3042;
                border-radius: 8px;
                color: #e8eaf0;
                alternate-background-color: #1a1f2b;
            }
            QListWidget::item:selected, QTableWidget::item:selected {
                background-color: #0ea5e9;
                color: white;
            }
            QHeaderView::section {
                background-color: #1b1f2a;
                color: #8b93a7;
                border: none;
                padding: 6px;
                font-weight: 700;
            }
            /* Scrollbars */
            QScrollBar:vertical {
                background: transparent; width: 10px; margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: #2a3042; border-radius: 5px; min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: #3d4a68; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar:horizontal {
                background: transparent; height: 10px; margin: 2px;
            }
            QScrollBar::handle:horizontal {
                background: #2a3042; border-radius: 5px; min-width: 30px;
            }
            /* Splitters */
            QSplitter::handle {
                background-color: #2a3042;
            }
            QSplitter::handle:horizontal { width: 3px; }
            QSplitter::handle:vertical { height: 3px; }
            /* Status / log */
            QLabel#statusLabel { color: #8b93a7; font-size: 12px; }
            QLabel#stageLabel { color: #7dd3fc; font-weight: 600; }
            /* Seek slider */
            QSlider::groove:horizontal {
                background: #161a23;
                border: 1px solid #2a3042;
                height: 8px; border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #0ea5e9;
                width: 14px; height: 14px;
                margin: -4px 0; border-radius: 7px;
            }
            QSlider::sub-page:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0ea5e9, stop:1 #6366f1);
                border-radius: 4px;
            }
        """)

    def _offer_autosave_recovery(self):
        """Crash recovery: offer to restore the newest autosave left by an interrupted session."""
        try:
            cands = sorted(glob.glob(os.path.join(str(app_paths.projects_dir()), "*.autosave")),
                           key=os.path.getmtime, reverse=True)
            if not cands:
                return
            newest = cands[0]
            age = time.time() - os.path.getmtime(newest)
            r = QMessageBox.question(
                self, "Recover project?",
                f"Found an autosave from an interrupted session:\n"
                f"{os.path.basename(newest)} "
                f"({int(age // 60)} min ago)\n\nRestore it?",
                QMessageBox.Yes | QMessageBox.No)
            if r == QMessageBox.Yes:
                self.project = Project.load(newest)
                self.script_edit.setPlainText(self.project.script)
                self.timeline_widget.set_timeline(self.project.timeline)
                self._log(f"Recovered autosave: {newest}")
        except Exception:
            pass

    # ---------------- UI construction ----------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)
        main.setSpacing(10)
        main.setContentsMargins(14, 12, 14, 12)

        # CapCut-style top bar (~48px): branding left, project actions
        # center, CREATE VIDEO far right (like CapCut's Export button).
        header = QHBoxLayout()
        header.setSpacing(8)
        brand_col = QVBoxLayout()
        brand_col.setSpacing(0)
        title = QLabel("U One")
        title.setObjectName("brandTitle")
        sub = QLabel("AI Automatic Video Editor")
        sub.setObjectName("brandSub")
        brand_col.addWidget(title)
        brand_col.addWidget(sub)
        header.addLayout(brand_col)
        header.addStretch(1)
        # menu-ish buttons in header
        for label, fn in [("New", self._new_project), ("Save", self._save_project),
                          ("Open", self._open_project), ("Undo", self._undo),
                          ("Redo", self._redo)]:
            b = QPushButton(label)
            b.setMaximumHeight(32)
            b.clicked.connect(fn)
            header.addWidget(b)
        header.addStretch(1)
        # Build actions at far right (CapCut puts Export here).
        self.preview_btn = QPushButton("Quick Preview")
        self.preview_btn.setMaximumHeight(36)
        self.preview_btn.setToolTip("Fast low-res preview render")
        self.preview_btn.clicked.connect(lambda: self._create_video(preview=True))
        header.addWidget(self.preview_btn)
        self.final_btn = QPushButton("Final Render (MP4)")
        self.final_btn.setMaximumHeight(36)
        self.final_btn.setToolTip("Full-quality final video")
        self.final_btn.clicked.connect(lambda: self._create_video(preview=False))
        header.addWidget(self.final_btn)
        self.create_btn = QPushButton("🎬 CREATE VIDEO")
        self.create_btn.setObjectName("primaryBtn")
        self.create_btn.setMinimumHeight(40)
        self.create_btn.setMinimumWidth(170)
        self.create_btn.setToolTip("Build the video (like CapCut's Export)")
        self.create_btn.clicked.connect(self._create_video)
        header.addWidget(self.create_btn)
        main.addLayout(header)

        self.ffmpeg_ok = check_ffmpeg()
        if not self.ffmpeg_ok:
            banner = QLabel("FFmpeg was not found - rendering is disabled. "
                            "Please reinstall U One (FFmpeg is bundled with the installer). "
                            "Script editing and timelines still work.")
            banner.setStyleSheet("background:#7a2e2e;color:white;padding:8px;border-radius:8px;")
            main.addWidget(banner)

        # NOTE: QSplitter caused a segfault (access violation) on some Windows
        # systems when adding the center panel. Using CapCut-style manual
        # drag dividers instead — side panels are user-resizable, center
        # takes the remaining space.
        # CapCut proportions: left ~30% / preview ~45% / inspector ~25%.
        panels = QHBoxLayout()
        panels.setSpacing(0)

        self.left_panel = self._left_panel()
        self.left_panel.setFixedWidth(320)
        panels.addWidget(self.left_panel)

        def _drag_left(dx):
            w = max(280, min(520, self.left_panel.width() + dx))
            self.left_panel.setFixedWidth(w)
        panels.addWidget(PanelDivider(_drag_left))

        panels.addWidget(self._center_panel(), 1)

        def _drag_right(dx):
            w = max(260, min(560, self.right_panel.width() - dx))
            self.right_panel.setFixedWidth(w)
        panels.addWidget(PanelDivider(_drag_right))

        self.right_panel = self._right_panel()
        self.right_panel.setFixedWidth(300)
        panels.addWidget(self.right_panel)

        main.addLayout(panels, 55)

        def _drag_timeline(dy):
            h = max(120, min(500, self.timeline_widget.height() - dy))
            self.timeline_widget.setFixedHeight(h)
        main.addWidget(HPanelDivider(_drag_timeline))

        # CapCut-style timeline toolbar (full width, above the timeline).
        main.addWidget(self._timeline_toolbar())

        self.timeline_widget = TimelineWidget()
        self.timeline_widget.sceneSelected.connect(self._on_scene_selected)
        self.timeline_widget.clipRightClicked.connect(
            self._on_timeline_clip_menu)
        self.timeline_widget.setFixedHeight(220)
        main.addWidget(self.timeline_widget)

        # Status bar: render progress + cancel (CapCut shows export
        # progress in its own bar; ours lives here, always visible).
        sb = self.statusBar()
        self.stage_lbl = QLabel("Ready.")
        self.stage_lbl.setObjectName("stageLabel")
        sb.addWidget(self.stage_lbl, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, len(STAGES))
        self.progress.setMaximumWidth(220)
        self.progress.setMaximumHeight(16)
        sb.addPermanentWidget(self.progress)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("dangerBtn")
        self.cancel_btn.setMaximumHeight(28)
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.setEnabled(False)
        sb.addPermanentWidget(self.cancel_btn)
        if not self.ffmpeg_ok:
            self.create_btn.setEnabled(False)
            self.preview_btn.setEnabled(False)
            self.final_btn.setEnabled(False)

    def _timeline_toolbar(self):
        """CapCut-style toolbar strip directly above the timeline."""
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)
        lbl = QLabel("Timeline")
        lbl.setObjectName("sectionHead")
        lay.addWidget(lbl)
        # Scene navigation (prev/next) — quick CapCut-like jumping.
        prev_b = QPushButton("⏮ Prev Scene")
        prev_b.setMaximumHeight(30)
        prev_b.clicked.connect(lambda: self._jump_scene(-1))
        lay.addWidget(prev_b)
        next_b = QPushButton("Next Scene ⏭")
        next_b.setMaximumHeight(30)
        next_b.clicked.connect(lambda: self._jump_scene(1))
        lay.addWidget(next_b)
        lay.addStretch(1)
        zoom_lbl = QLabel("Zoom:")
        zoom_lbl.setObjectName("statusLabel")
        lay.addWidget(zoom_lbl)
        self.timeline_zoom = QSlider(Qt.Horizontal)
        self.timeline_zoom.setRange(50, 200)
        self.timeline_zoom.setValue(100)
        self.timeline_zoom.setMaximumWidth(140)
        self.timeline_zoom.setMaximumHeight(30)
        self.timeline_zoom.setToolTip("Timeline zoom")
        self.timeline_zoom.valueChanged.connect(self._timeline_zoom_changed)
        lay.addWidget(self.timeline_zoom)
        return bar

    def _jump_scene(self, direction):
        """Jump to previous/next scene in the scene list."""
        try:
            count = self.scene_list.count()
            if count == 0:
                return
            cur = self.scene_list.currentRow()
            nxt = (cur + direction) % count
            self.scene_list.setCurrentRow(nxt)
            self._on_scene_clicked(self.scene_list.item(nxt))
        except Exception:  # noqa: BLE001
            pass

    def _timeline_zoom_changed(self, value):
        """Scale timeline height with the zoom slider (CapCut-like)."""
        try:
            h = max(120, min(500, int(220 * value / 100)))
            self.timeline_widget.setFixedHeight(h)
        except Exception:  # noqa: BLE001
            pass

    def _about(self):
        QMessageBox.about(
            self, "About U One",
            f"<b>{__display_name__}</b><br>Version {__version__}<br><br>"
            "Script + voice in, complete edited video out.<br><br>"
            "Stock media: Pexels and Pixabay (optional free API keys in Settings).<br>"
            "Rendering: FFmpeg (bundled with the installer).<br>"
            "Voice: edge-tts with offline fallback.<br><br>"
            "Your projects, cache and logs live in your personal app folder - "
            "nothing is uploaded anywhere.")

    def _left_panel(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        script_head = QLabel("1. Script")
        script_head.setObjectName("sectionHead")
        lay.addWidget(script_head)
        self.script_edit = QTextEdit()
        self.script_edit.setPlaceholderText("Paste your script here. One idea per sentence works best...")
        lay.addWidget(self.script_edit, 2)

        g = QGroupBox("2. Voiceover")
        gl = QVBoxLayout(g)
        self.voice_group = QButtonGroup(self)
        self.rb_upload = QRadioButton("Upload existing voiceover")
        self.rb_gen = QRadioButton("Generate AI voice from script")
        self.rb_gen.setChecked(True)
        self.voice_group.addButton(self.rb_upload)
        self.voice_group.addButton(self.rb_gen)
        gl.addWidget(self.rb_upload)
        gl.addWidget(self.rb_gen)
        row = QHBoxLayout()
        self.voice_path_lbl = QLabel("no file")
        self.voice_path_lbl.setWordWrap(True)
        up = QPushButton("Browse...")
        up.clicked.connect(self._browse_voice)
        row.addWidget(up)
        row.addWidget(self.voice_path_lbl, 1)
        gl.addLayout(row)
        vl = QHBoxLayout()
        vl.addWidget(QLabel("AI voice:"))
        self.voice_combo = QComboBox()
        self.voice_combo.addItems(["en-US-AriaNeural", "en-US-GuyNeural",
                                   "en-GB-SoniaNeural", "en-AU-NatashaNeural"])
        vl.addWidget(self.voice_combo, 1)
        gl.addLayout(vl)
        lay.addWidget(g)

        s = QGroupBox("3. Settings")
        form = QFormLayout(s)
        self.cb_visual = QComboBox()
        self.cb_visual.addItems(["every_sentence", "every_2", "every_3", "ai_auto"])
        form.addRow("Visual mode:", self.cb_visual)
        self.cb_media = QComboBox()
        self.cb_media.addItems(["ai_auto", "video_first", "image_first"])
        form.addRow("Video/image:", self.cb_media)
        self.cb_repeat = QComboBox()
        self.cb_repeat.addItems(["never", "max2", "ai"])
        form.addRow("Repeat visuals:", self.cb_repeat)
        self.cb_style = QComboBox()
        self.cb_style.addItems(["Documentary", "News", "Cinematic", "Fast-paced",
                                "Storytelling", "Educational", "Science", "History",
                                "Calm", "Dramatic"])
        form.addRow("Style preset:", self.cb_style)
        self.cb_fmt = QComboBox()
        self.cb_fmt.addItems(["16:9", "9:16", "1:1"])
        form.addRow("Format:", self.cb_fmt)
        self.cb_res = QComboBox()
        self.cb_res.addItems(["1080p", "1440p", "4K"])
        form.addRow("Resolution:", self.cb_res)
        self.cb_fps = QComboBox()
        self.cb_fps.addItems(["24", "30", "60"])
        self.cb_fps.setCurrentText("30")
        form.addRow("FPS:", self.cb_fps)
        self.cb_codec = QComboBox()
        self.cb_codec.addItems(["h264", "h265"])
        form.addRow("Codec:", self.cb_codec)
        self.chk_text_overlays = QCheckBox("Show main text in video")
        self.chk_text_overlays.setChecked(True)
        self.chk_text_overlays.setToolTip(
            "Uncheck to skip the big title text overlays for this video.")
        form.addRow(self.chk_text_overlays)
        self.chk_captions = QCheckBox("Captions")
        self.chk_captions.setChecked(True)
        form.addRow(self.chk_captions)
        # Caption template selector (CapCut viral/trending styles)
        from src.text_captions import caption_template_labels
        self.cb_caption_template = QComboBox()
        for key, label in caption_template_labels():
            self.cb_caption_template.addItem(label, key)
        self.cb_caption_template.setToolTip(
            "Pick a caption style like CapCut's viral templates.")
        form.addRow("Caption style:", self.cb_caption_template)
        mrow = QHBoxLayout()
        self.music_lbl = QLabel("(optional)")
        mb = QPushButton("Music folder...")
        mb.clicked.connect(self._browse_music)
        mrow.addWidget(mb)
        mrow.addWidget(self.music_lbl, 1)
        form.addRow("Music:", mrow)
        lay.addWidget(s)

        api = QGroupBox("4. API Settings (optional)")
        aform = QFormLayout(api)
        self.pexels_key = QLineEdit()
        self.pexels_key.setEchoMode(QLineEdit.Password)
        self.pexels_key.setPlaceholderText("Paste Pexels API key (free at pexels.com/api)")
        aform.addRow("Pexels API key:", self.pexels_key)
        self.pixabay_key = QLineEdit()
        self.pixabay_key.setEchoMode(QLineEdit.Password)
        self.pixabay_key.setPlaceholderText("Paste Pixabay API key (free at pixabay.com/api/docs)")
        aform.addRow("Pixabay API key:", self.pixabay_key)
        abtns = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_api_keys)
        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self._test_api_keys)
        abtns.addWidget(save_btn)
        abtns.addWidget(test_btn)
        abtns.addStretch(1)
        aform.addRow(abtns)
        self.pexels_status = QLabel("Not Connected")
        self.pixabay_status = QLabel("Not Connected")
        aform.addRow("Pexels:", self.pexels_status)
        aform.addRow("Pixabay:", self.pixabay_status)
        api_note = QLabel("Keys are stored securely on this PC and are never shown, "
                          "logged or saved into projects. Without keys, U One builds "
                          "every video with its built-in graphics engine.")
        api_note.setWordWrap(True)
        api_note.setStyleSheet("color:#888;font-size:11px;")
        aform.addRow(api_note)
        lay.addWidget(api)
        self._refresh_api_status(saved_only=True)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(w)
        scroll.setWidgetResizable(True)
        # CapCut-style: left panel = script/tools (scrollable).
        # Build actions live in the top bar; progress in the status bar.
        outer = QWidget()
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.addWidget(scroll, 1)
        log_head = QLabel("Log")
        log_head.setObjectName("sectionHead")
        outer_lay.addWidget(log_head)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumHeight(110)
        outer_lay.addWidget(self.log_edit)
        return outer

    def _center_panel(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        preview_head = QLabel("Preview")
        preview_head.setObjectName("sectionHead")
        lay.addWidget(preview_head)
        self.video_hint = QLabel(
            "Tip: video par click karo — caption edit ho jayega")
        self.video_hint.setObjectName("statusLabel")
        lay.addWidget(self.video_hint)
        # Lazy video widget: QVideoWidget() pulls in Qt Multimedia which can hang
        # on some systems during backend init. Use a placeholder until preview
        # is actually needed.
        self.video_widget = None
        self.video_placeholder = QLabel("Preview will appear here after rendering.")
        self.video_placeholder.setObjectName("statusLabel")
        self.video_placeholder.setAlignment(Qt.AlignCenter)
        self.video_placeholder.setMinimumHeight(200)
        lay.addWidget(self.video_placeholder, 3)
        # Lazy player: QMediaPlayer() can hang on some systems during Qt Multimedia
        # FFmpeg backend init, so create it only when preview is actually needed.
        self.player = None
        self.audio_out = None
        self._restart_on_play = False

        # Playback controls: play/pause + stop + seek bar + time label
        ctl = QHBoxLayout()
        ctl.setSpacing(8)
        self.play_btn = QPushButton("▶")
        self.play_btn.setMaximumWidth(48)
        self.play_btn.setToolTip("Play / Pause (jitni marzi bar dabao)")
        self.play_btn.clicked.connect(self._toggle_play)
        ctl.addWidget(self.play_btn)
        self.stop_btn = QPushButton("⏹")
        self.stop_btn.setMaximumWidth(48)
        self.stop_btn.setToolTip("Stop (shuru se)")
        self.stop_btn.clicked.connect(self._stop_play)
        ctl.addWidget(self.stop_btn)
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderMoved.connect(self._seek)
        ctl.addWidget(self.seek_slider, 1)
        self.time_lbl = QLabel("00:00 / 00:00")
        self.time_lbl.setObjectName("statusLabel")
        ctl.addWidget(self.time_lbl)
        lay.addLayout(ctl)
        # CapCut-style: center panel is ONLY the preview (large).
        # Build controls live in the left panel's fixed bottom section.
        lay.addStretch(1)
        return w

    def _ensure_player(self):
        """Create the QMediaPlayer (and video widget) on first use.

        Qt Multimedia's FFmpeg backend can hang during startup on some
        systems, so we defer creation until preview is actually needed.
        Returns True if the player is ready.
        """
        if self.player is not None:
            return True
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PySide6.QtMultimediaWidgets import QVideoWidget
            # Swap the placeholder for a real video widget on first use.
            if self.video_widget is None:
                self.video_widget = QVideoWidget()
                lay = self.video_placeholder.parent().layout()
                lay.replaceWidget(self.video_placeholder, self.video_widget)
                self.video_placeholder.deleteLater()
                self.video_placeholder = None
            self.player = QMediaPlayer()
            self.audio_out = QAudioOutput()
            self.player.setAudioOutput(self.audio_out)
            self.player.setVideoOutput(self.video_widget)
            self.player.positionChanged.connect(self._on_position)
            self.player.durationChanged.connect(self._on_duration)
            self.player.playbackStateChanged.connect(self._on_play_state)
            self.player.mediaStatusChanged.connect(self._on_media_status)
            self.player.errorOccurred.connect(self._on_player_error)
            # Click on the video -> open the caption editor for the
            # caption shown at that moment.
            self.video_widget.installEventFilter(self)
            # Instant caption preview overlay (CapCut-style live styling).
            self._ensure_caption_overlay()
            return True
        except Exception as e:
            self._log(f"Preview player unavailable: {e}")
            return False

    def _on_player_error(self, error, error_string):
        # Show why the preview failed so it can be diagnosed.
        self._log(f"Preview player error: {error_string}")
        self.time_lbl.setText(f"Preview error: {error_string[:60]}")

    # -- instant caption preview overlay (CapCut-style) --
    @staticmethod
    def _ass_to_css(ass_color: str) -> str:
        """Convert ASS color &HAABBGGRR to CSS #RRGGBB."""
        h = (ass_color or "").strip()
        if h.startswith("&H"):
            h = h[2:]
        if len(h) == 8:
            return f"#{h[6:8]}{h[4:6]}{h[2:4]}"
        return "#FFFFFF"

    def _caption_overlay_style(self) -> str:
        """Build QLabel QSS from the selected template + size (instant)."""
        from src.text_captions import CAPTION_TEMPLATES
        key = self.edit_template_cb.currentData()
        tmpl = CAPTION_TEMPLATES.get(key) or {}
        size = self.edit_size_slider.value()
        primary = self._ass_to_css(tmpl.get("primary", "&H00FFFFFF"))
        bg = ""
        if tmpl.get("box"):
            back = self._ass_to_css(tmpl.get("back_c", "&HC8000000"))
            bg = (f"background-color: {back}; padding: 6px 14px; "
                  f"border-radius: 8px;")
        bold = "bold" if tmpl.get("bold") == -1 else "normal"
        italic = "italic" if tmpl.get("italic") else "normal"
        font = tmpl.get("font", "Arial")
        px = max(12, int(size * float(tmpl.get("size_scale", 1.0))))
        return (f"QLabel {{ color: {primary}; font-family: '{font}'; "
                f"font-size: {px}px; font-weight: {bold}; "
                f"font-style: {italic}; {bg} }}")

    def _ensure_caption_overlay(self):
        """Create the caption overlay label on the video widget."""
        if getattr(self, "caption_overlay", None) is not None:
            return
        if self.video_widget is None:
            return
        from PySide6.QtWidgets import QLabel
        ov = QLabel(self.video_widget)
        ov.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
        ov.setWordWrap(True)
        ov.setAttribute(Qt.WA_TransparentForMouseEvents)  # clicks -> video
        ov.setContentsMargins(20, 0, 20, 24)
        ov.setStyleSheet(self._caption_overlay_style())
        ov.setGeometry(self.video_widget.rect())
        ov.show()
        self.caption_overlay = ov

    def _refresh_caption_style(self):
        """Instant style update when template/size changes (no re-encode)."""
        ov = getattr(self, "caption_overlay", None)
        if ov is not None:
            ov.setStyleSheet(self._caption_overlay_style())
            # Refresh text immediately so the user sees the change.
            if self.player is not None:
                self._update_caption_text(self.player.position())

    def _update_caption_text(self, pos_ms: int):
        """Show the caption visible at pos_ms on the overlay (karaoke)."""
        ov = getattr(self, "caption_overlay", None)
        if ov is None:
            return
        timings = getattr(self, "_cap_sentences", None) or []
        if not timings:
            ov.setText("")
            return
        pos_sec = pos_ms / 1000.0
        cur = None
        for s in timings:
            if s["start"] <= pos_sec <= s["end"]:
                cur = s
                break
        if cur is None:
            ov.setText("")
            return
        # Karaoke: highlight the word being spoken right now.
        html = cur["text"]
        words = getattr(self, "_cap_words", None) or []
        cur_word = None
        for w in words:
            if w["start"] <= pos_sec <= w["end"]:
                cur_word = w["word"]
                break
        if cur_word:
            from src.text_captions import CAPTION_TEMPLATES
            tmpl = CAPTION_TEMPLATES.get(
                self.edit_template_cb.currentData()) or {}
            hl = self._ass_to_css(tmpl.get("secondary", "&H0000D7FF"))
            # Highlight first occurrence of the current word.
            import re as _re
            html = _re.sub(
                _re.escape(cur_word),
                f'<span style="color:{hl};">{cur_word}</span>',
                cur["text"], count=1)
        ov.setText(f"<div style='text-align:center;'>{html}</div>")

    # -- preview playback controls --
    @staticmethod
    def _fmt_ms(ms: int) -> str:
        s = max(0, int(ms // 1000))
        return f"{s // 60:02d}:{s % 60:02d}"

    def _player_diag(self, tag: str):
        """Log full player state for diagnosing playback failures."""
        try:
            p = self.player
            if p is None:
                self._log(f"[play-diag:{tag}] player=None")
                return
            self._log(
                f"[play-diag:{tag}] state={int(p.playbackState())} "
                f"(0=Stopped 1=Playing 2=Paused) "
                f"mediaStatus={int(p.mediaStatus())} "
                f"pos={p.position()}ms dur={p.duration()}ms "
                f"src={p.source().toString()[:80]} "
                f"err={p.errorString()[:60] if hasattr(p, 'errorString') else ''}"
            )
        except Exception as e:  # noqa: BLE001
            self._log(f"[play-diag:{tag}] diag failed: {e}")

    def _toggle_play(self):
        if not self._ensure_player():
            return
        if self.player.playbackState() == self.player.PlayingState:
            self.player.pause()
            return
        # Not playing: (re)start. If the video ended (or stop was
        # pressed), seek to the start first so the button always works.
        # The seek is async on the Windows backend, so play() is issued
        # via a deferred call — never back-to-back with the seek.
        self._player_diag("play-pressed")
        try:
            dur = self.player.duration()
            pos = self.player.position()
            if (dur > 0 and pos >= dur - 300) or getattr(
                    self, "_restart_on_play", False):
                self._restart_on_play = False
                self.player.setPosition(0)
                from PySide6.QtCore import QTimer
                QTimer.singleShot(250, self._deferred_play)
                return
        except Exception as e:  # noqa: BLE001
            self._log(f"Play seek check failed: {e}")
        self.player.play()
        self._player_diag("play-issued")
        # Watchdog: if the backend swallowed play(), retry harder.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(800, self._play_watchdog)

    def _play_watchdog(self):
        """If play() didn't take effect, force a hard pipeline reset."""
        try:
            if self.player is None:
                return
            if self.player.playbackState() == self.player.PlayingState:
                return
            self._player_diag("watchdog-failed")
            self._log("Play didn't start — resetting media pipeline...")
            from PySide6.QtCore import QUrl, QTimer
            src = self.player.source()
            # Empty source first: forces a full pipeline teardown even
            # if the backend ignores setSource() with the same URL.
            self.player.setSource(QUrl())
            self.player.setSource(src)
            self.player.setPosition(0)
            self.player.play()
            self._player_diag("watchdog-retry-issued")
            QTimer.singleShot(2000, self._play_watchdog2)
        except Exception as e:  # noqa: BLE001
            self._log(f"Play restart failed: {e}")

    def _play_watchdog2(self):
        """Last resort: destroy and recreate the player entirely."""
        try:
            if self.player is None:
                return
            if self.player.playbackState() == self.player.PlayingState:
                return
            self._player_diag("watchdog2-failed")
            self._log("Play still stuck — recreating player...")
            self._recreate_player()
        except Exception as e:  # noqa: BLE001
            self._log(f"Player recreation failed: {e}")

    def _recreate_player(self):
        """Destroy the QMediaPlayer and build a fresh one on the same
        video widget. Nuclear fallback for a wedged backend pipeline."""
        from PySide6.QtCore import QTimer
        try:
            src = self.player.source() if self.player is not None else None
            old = self.player
            self.player = None
            self.audio_out = None
            if old is not None:
                try:
                    old.stop()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    old.deleteLater()
                except Exception:  # noqa: BLE001
                    pass
            # Re-run creation (video_widget already exists, so it is reused).
            if not self._ensure_player():
                self._log("Player recreation unavailable on this system.")
                return
            if src is not None and not src.isEmpty():
                self.player.setSource(src)
                self.player.setPosition(0)
            QTimer.singleShot(300, self._deferred_play)
            self._log("Player recreated — retrying playback...")
        except Exception as e:  # noqa: BLE001
            self._log(f"Player recreation failed: {e}")

    def _deferred_play(self):
        try:
            if (self.player is not None and
                    self.player.playbackState() != self.player.PlayingState):
                self.player.play()
                self._player_diag("deferred-play-issued")
        except Exception:  # noqa: BLE001
            pass

    def _stop_play(self):
        if not self._ensure_player():
            return
        # Pause only — no synchronous seek. The seek back to 0 happens
        # lazily on the next play press (see _toggle_play), because
        # pause()+seek() issued together can wedge the Windows backend
        # so that a later play() is silently swallowed.
        self._player_diag("stop-pressed")
        self.player.pause()
        self._restart_on_play = True
        self.play_btn.setText("▶")
        self._player_diag("stopped")

    def _on_media_status(self, status):
        # 7 = EndOfMedia. Just update the button — do NOT seek here.
        # Seeking from inside the status handler can fight with the
        # play logic's own seek and wedge the backend. _toggle_play
        # handles the rewind when the user presses play.
        if int(status) == 7:
            self.play_btn.setText("▶")
            self._player_diag("end-of-media")

    def eventFilter(self, obj, event):
        # Click on the video -> pause and open the caption editor
        # for the caption visible at that moment.
        if obj is self.video_widget:
            from PySide6.QtCore import QEvent
            if event.type() == QEvent.MouseButtonPress:
                self._on_video_clicked()
                return True
            if event.type() == QEvent.Resize:
                ov = getattr(self, "caption_overlay", None)
                if ov is not None:
                    ov.setGeometry(self.video_widget.rect())
        return super().eventFilter(obj, event)

    def _on_video_clicked(self):
        """Video clicked: pause and offer to edit the visible caption."""
        if not self._edit_session or not os.path.isfile(self._edit_session):
            return  # nothing to edit yet — render a video first
        if self.player is not None:
            self.player.pause()
        try:
            from src.caption_editor import load_edit_session
            data = load_edit_session(self._edit_session)
            pos_sec = (self.player.position() / 1000.0) if self.player else 0.0
            # Find the caption visible right now.
            current = None
            for i, s in enumerate(data.get("sentence_timings", [])):
                if s["start"] <= pos_sec <= s["end"]:
                    current = i
                    break
            self._open_caption_text_editor(data, select_index=current)
        except Exception as e:  # noqa: BLE001
            self._log(f"Could not open caption editor: {e}")

    def _open_caption_text_editor(self, data, select_index=None):
        """Dialog: edit each caption's text + change template/size."""
        from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                       QListWidget, QTextEdit, QLabel,
                                       QDialogButtonBox, QComboBox, QSlider)
        from PySide6.QtCore import Qt
        from src.text_captions import caption_template_labels

        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Captions")
        dlg.resize(560, 480)
        lay = QVBoxLayout(dlg)

        lay.addWidget(QLabel("Click a caption to edit its text:"))
        self._cap_list = QListWidget()
        timings = data.get("sentence_timings", [])
        for i, s in enumerate(timings):
            t0 = int(s["start"] // 60), int(s["start"] % 60)
            self._cap_list.addItem(
                f"{t0[0]:02d}:{t0[1]:02d}  {s['text'][:70]}")
        if select_index is not None and 0 <= select_index < len(timings):
            self._cap_list.setCurrentRow(select_index)
        lay.addWidget(self._cap_list, 1)

        lay.addWidget(QLabel("Caption text:"))
        self._cap_text_edit = QTextEdit()
        self._cap_text_edit.setMaximumHeight(80)
        lay.addWidget(self._cap_text_edit)

        def _fill_text(row):
            if 0 <= row < len(timings):
                self._cap_text_edit.setPlainText(timings[row]["text"])
        self._cap_list.currentRowChanged.connect(_fill_text)
        if self._cap_list.currentRow() >= 0:
            _fill_text(self._cap_list.currentRow())

        def _save_text():
            row = self._cap_list.currentRow()
            if 0 <= row < len(timings):
                new_text = self._cap_text_edit.toPlainText().strip()
                if new_text:
                    timings[row]["text"] = new_text
                    t0 = int(timings[row]["start"] // 60), int(
                        timings[row]["start"] % 60)
                    self._cap_list.item(row).setText(
                        f"{t0[0]:02d}:{t0[1]:02d}  {new_text[:70]}")
        self._cap_text_edit.textChanged.connect(_save_text)

        # Template + size in the same dialog.
        trow = QHBoxLayout()
        trow.addWidget(QLabel("Template:"))
        tmpl_cb = QComboBox()
        for key, label in caption_template_labels():
            tmpl_cb.addItem(label, key)
        idx = tmpl_cb.findData(data.get("template"))
        if idx >= 0:
            tmpl_cb.setCurrentIndex(idx)
        trow.addWidget(tmpl_cb, 1)
        lay.addLayout(trow)

        srow = QHBoxLayout()
        srow.addWidget(QLabel("Size:"))
        size_sl = QSlider(Qt.Horizontal)
        size_sl.setRange(24, 96)
        size_sl.setValue(int(data.get("font_size", 48)))
        size_lbl = QLabel(str(size_sl.value()))
        size_sl.valueChanged.connect(lambda v: size_lbl.setText(str(v)))
        srow.addWidget(size_sl, 1)
        srow.addWidget(size_lbl)
        lay.addLayout(srow)

        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() == QDialog.Accepted:
            # Save edited texts back to the session, then re-burn.
            import json
            data["sentence_timings"] = timings
            with open(self._edit_session, "w", encoding="utf-8") as f:
                json.dump(data, f)
            # Sync the main panel controls too.
            idx2 = self.edit_template_cb.findData(tmpl_cb.currentData())
            if idx2 >= 0:
                self.edit_template_cb.setCurrentIndex(idx2)
            self.edit_size_slider.setValue(size_sl.value())
            self._apply_caption_edits()

    def _seek(self, pos: int):
        if not self._ensure_player():
            return
        self.player.setPosition(pos)

    def _on_position(self, pos: int):
        self.seek_slider.blockSignals(True)
        self.seek_slider.setValue(pos)
        self.seek_slider.blockSignals(False)
        self.time_lbl.setText(
            f"{self._fmt_ms(pos)} / {self._fmt_ms(self.player.duration())}")
        # Live caption overlay follows playback (CapCut-style preview).
        self._update_caption_text(pos)

    def _on_duration(self, dur: int):
        self.seek_slider.setRange(0, dur)

    def _on_play_state(self, state):
        # Compare via int to avoid needing QMediaPlayer import at module level.
        self.play_btn.setText("⏸" if int(state) == 1 else "▶")  # 1 = PlayingState

    def _right_panel(self):
        tabs = QTabWidget()
        # scenes tab
        sw = QWidget()
        sl = QVBoxLayout(sw)
        self.scene_list = QListWidget()
        self.scene_list.itemClicked.connect(self._on_scene_clicked)
        sl.addWidget(self.scene_list, 2)
        self.scene_thumb = QLabel("Select a scene")
        self.scene_thumb.setAlignment(Qt.AlignCenter)
        self.scene_thumb.setMinimumHeight(160)
        self.scene_thumb.setStyleSheet("background:#222;color:#888;")
        sl.addWidget(self.scene_thumb)
        self.scene_text = QLabel("")
        self.scene_text.setWordWrap(True)
        sl.addWidget(self.scene_text)
        btns = QHBoxLayout()
        for label, fn in [("Regen Visual", self._regen_visual), ("Regen Text", self._regen_text)]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            btns.addWidget(b)
        sl.addLayout(btns)
        btns2 = QHBoxLayout()
        for label, fn in [("Regen SFX", self._regen_sfx), ("Regen Music", self._regen_music)]:
            b = QPushButton(label)
            b.clicked.connect(fn)
            btns2.addWidget(b)
        sl.addLayout(btns2)
        # Direct edit buttons
        btns3 = QHBoxLayout()
        edit_text_btn = QPushButton("✏️ Edit Text")
        edit_text_btn.setToolTip("Directly edit the on-screen text for this scene")
        edit_text_btn.clicked.connect(self._edit_scene_text)
        btns3.addWidget(edit_text_btn)
        mute_sfx_btn = QPushButton("🔇 Mute SFX")
        mute_sfx_btn.setToolTip("Remove sound effects from this scene")
        mute_sfx_btn.clicked.connect(self._mute_scene_sfx)
        btns3.addWidget(mute_sfx_btn)
        sl.addLayout(btns3)
        # CapCut-style structural editing: delete / reorder scenes.
        btns4 = QHBoxLayout()
        up_btn = QPushButton("⬆️ Up")
        up_btn.setToolTip("Move this scene earlier")
        up_btn.clicked.connect(lambda: self._move_scene(-1))
        btns4.addWidget(up_btn)
        down_btn = QPushButton("⬇️ Down")
        down_btn.setToolTip("Move this scene later")
        down_btn.clicked.connect(lambda: self._move_scene(1))
        btns4.addWidget(down_btn)
        del_btn = QPushButton("🗑️ Delete")
        del_btn.setToolTip("Delete this scene")
        del_btn.clicked.connect(self._delete_scene)
        btns4.addWidget(del_btn)
        sl.addLayout(btns4)
        self.lock_chk = QCheckBox("🔒 Lock scene (AI will not modify)")
        self.lock_chk.stateChanged.connect(self._toggle_lock)
        sl.addWidget(self.lock_chk)
        tabs.addTab(sw, "Scenes")
        # captions tab (CapCut-style: hamesha visible, right panel mein)
        tabs.addTab(self._captions_tab(), "Captions")
        # sources tab
        self.sources_table = QTableWidget(0, 3)
        self.sources_table.setHorizontalHeaderLabels(["Scene", "Source", "License"])
        tabs.addTab(self.sources_table, "Sources")
        # youtube tab
        yw = QWidget()
        yl = QVBoxLayout(yw)
        gen = QPushButton("Generate YouTube package")
        gen.clicked.connect(self._youtube)
        yl.addWidget(gen)
        self.yt_out = QTextEdit()
        self.yt_out.setReadOnly(True)
        yl.addWidget(self.yt_out, 1)
        tabs.addTab(yw, "YouTube")
        return tabs

    def _captions_tab(self):
        """CapCut-style caption editor: right panel mein, hamesha visible.

        Template/size select karte hi video preview par foran nazar aata
        hai (live overlay). 'Apply' dabane par video mein burn hota hai.
        """
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(10)
        info = QLabel(
            "Video render hone ke baad template/size badlo.\n"
            "Preview par foran nazar aayega — Apply se video mein save hoga.")
        info.setObjectName("statusLabel")
        info.setWordWrap(True)
        lay.addWidget(info)
        cap_form = QFormLayout()
        from src.text_captions import caption_template_labels
        self.edit_template_cb = QComboBox()
        for key, label in caption_template_labels():
            self.edit_template_cb.addItem(label, key)
        self.edit_template_cb.setEnabled(False)
        # CapCut-style: template select karte hi preview mein nazar aaye.
        self.edit_template_cb.currentIndexChanged.connect(
            lambda _i: self._refresh_caption_style())
        cap_form.addRow("Template:", self.edit_template_cb)
        size_row = QHBoxLayout()
        self.edit_size_slider = QSlider(Qt.Horizontal)
        self.edit_size_slider.setRange(24, 96)
        self.edit_size_slider.setValue(48)
        self.edit_size_slider.setEnabled(False)
        self.edit_size_lbl = QLabel("48")
        self.edit_size_slider.valueChanged.connect(
            lambda v: self.edit_size_lbl.setText(str(v)))
        # CapCut-style: size drag karte hi preview mein bara/chhota ho.
        self.edit_size_slider.valueChanged.connect(
            lambda _v: self._refresh_caption_style())
        size_row.addWidget(self.edit_size_slider, 1)
        size_row.addWidget(self.edit_size_lbl)
        cap_form.addRow("Size:", size_row)
        lay.addLayout(cap_form)
        self.edit_apply_btn = QPushButton("Apply Caption Changes")
        self.edit_apply_btn.setEnabled(False)
        self.edit_apply_btn.setMinimumHeight(44)
        self.edit_apply_btn.setToolTip(
            "Preview mein jo style nazar aa raha hai, usay video mein "
            "permanently burn karo.")
        self.edit_apply_btn.clicked.connect(self._apply_caption_edits)
        lay.addWidget(self.edit_apply_btn)
        lay.addStretch(1)
        self._edit_session = ""  # path to caption_edit.json
        self._cap_sentences = []
        self._cap_words = []
        # CapCut-style timeline editing state.
        self._current_video = ""  # latest preview video on disk
        self._tl_worker = None
        return w

    # ---------------- actions ----------------
    def _log(self, m):
        self.log_edit.append(m)

    # ---------------- API keys (secure storage, never logged/shown) ----------------
    def _save_api_keys(self):
        px = self.pexels_key.text().strip()
        pb = self.pixabay_key.text().strip()
        if px:
            secure_store.set_key("pexels", px)
        if pb:
            secure_store.set_key("pixabay", pb)
        self.pexels_key.clear()
        self.pixabay_key.clear()
        self._refresh_api_status(saved_only=True)
        self._log("API keys saved securely on this PC.")
        QMessageBox.information(self, "U One", "API keys saved.")

    def _test_api_keys(self):
        # test with currently stored keys (never display them) - off the UI
        # thread so a slow network cannot freeze the app.
        self._log("Testing API connections...")
        self.pexels_status.setText("Testing...")
        self.pixabay_status.setText("Testing...")

        class _ApiTestWorker(QThread):
            done = Signal(bool, str, bool, str)

            def run(self):  # noqa: D102
                try:
                    ok_px, msg_px = test_provider(
                        "pexels", secure_store.get_key("pexels"))
                except Exception as e:  # noqa: BLE001
                    ok_px, msg_px = False, str(e)[:120]
                try:
                    ok_pb, msg_pb = test_provider(
                        "pixabay", secure_store.get_key("pixabay"))
                except Exception as e:  # noqa: BLE001
                    ok_pb, msg_pb = False, str(e)[:120]
                self.done.emit(ok_px, msg_px, ok_pb, msg_pb)

        def _on_done(ok_px, msg_px, ok_pb, msg_pb):
            self.pexels_status.setText(
                "Connected" if ok_px else f"Not Connected - {msg_px}")
            self.pixabay_status.setText(
                "Connected" if ok_pb else f"Not Connected - {msg_pb}")
            self.pexels_status.setStyleSheet(
                "color:#2f9e44;" if ok_px else "color:#c0392b;")
            self.pixabay_status.setStyleSheet(
                "color:#2f9e44;" if ok_pb else "color:#c0392b;")
            self._log(f"Pexels: {'Connected' if ok_px else 'Not Connected'} | "
                      f"Pixabay: {'Connected' if ok_pb else 'Not Connected'}")
            self._api_worker = None

        self._api_worker = _ApiTestWorker(self)
        self._api_worker.done.connect(_on_done)
        self._api_worker.start()

    def _refresh_api_status(self, saved_only=False):
        has = secure_store.has_keys()
        self.pexels_status.setText("Saved" if has["pexels"] else "Not Connected")
        self.pixabay_status.setText("Saved" if has["pixabay"] else "Not Connected")
        self.pexels_status.setStyleSheet("color:#2f9e44;" if has["pexels"] else "color:#888;")
        self.pixabay_status.setStyleSheet("color:#2f9e44;" if has["pixabay"] else "color:#888;")

    def _settings(self):
        if self.rb_upload.isChecked() and self.voice_path_lbl.text().startswith("no file"):
            QMessageBox.warning(self, "Voiceover", "Choose an audio file or switch to AI voice.")
            return None
        return {
            "visual_mode": self.cb_visual.currentText(),
            "media_preference": self.cb_media.currentText(),
            "repetition_limit": self.cb_repeat.currentText(),
            "style_preset": self.cb_style.currentText(),
            "output_format": self.cb_fmt.currentText(),
            "output_resolution": self.cb_res.currentText(),
            "output_fps": int(self.cb_fps.currentText()),
            "output_codec": self.cb_codec.currentText(),
            "text_overlays": "auto" if self.chk_text_overlays.isChecked() else "off",
            "captions_enabled": self.chk_captions.isChecked(),
            "caption_highlight": True, "caption_font_size": 48,
            "caption_template": self.cb_caption_template.currentData(),
            "voice_upload": "" if self.rb_gen.isChecked() else self.voice_path_lbl.text(),
            "voice_generate_text": self.script_edit.toPlainText(),
            "voice_name": self.voice_combo.currentText(),
            "music_dir": self.music_lbl.text() if self.music_lbl.text() not in ("(optional)", "no file") else "",
        }

    def _create_video(self, preview=False):
        script = self.script_edit.toPlainText().strip()
        if not script:
            QMessageBox.warning(self, "Script", "Paste a script first.")
            return
        # Guard: never start a second render while one is running.
        # (Two concurrent renders delete each other's temp files.)
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.warning(
                self, "U One",
                "Video abhi ban rahi hai — pehle usay complete ya cancel karo.")
            return
        settings = self._settings()
        if not settings:
            return
        self.project.script = script
        self.project.snapshot()
        self.cancel_event.clear()
        pm = ProjectManager(self.project, str(app_paths.projects_dir()))
        self.pipeline = RenderEngine(pm, self.work_dir, Settings(settings),
                                     cancel_event=self.cancel_event)
        self.worker = PipelineWorker(self.pipeline, preview)
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self._log)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.create_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.final_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setValue(0)
        self.worker.start()

    def _render_buttons(self, enabled: bool):
        self.create_btn.setEnabled(enabled)
        self.preview_btn.setEnabled(enabled)
        self.final_btn.setEnabled(enabled)
        self.cancel_btn.setEnabled(not enabled)

    def _on_progress(self, i, stage, msg):
        self.progress.setValue(i + 1)
        self.stage_lbl.setText(f"{stage}... {msg}")

    def _on_finished(self, out):
        self._render_buttons(True)
        self.stage_lbl.setText("Done.")
        self._log(f"Saved: {out}")
        self._current_video = out  # timeline edits build on this file
        # Enable post-render caption editing if a session was saved.
        try:
            session = getattr(self.pipeline, "edit_session_path", "") or ""
            if session and os.path.isfile(session):
                from src.caption_editor import load_edit_session
                data = load_edit_session(session)
                self._edit_session = session
                # Sync the editor controls with the render's caption style.
                idx = self.edit_template_cb.findData(data.get("template"))
                if idx >= 0:
                    self.edit_template_cb.setCurrentIndex(idx)
                self.edit_size_slider.setValue(int(data.get("font_size", 48)))
                self.edit_template_cb.setEnabled(True)
                self.edit_size_slider.setEnabled(True)
                self.edit_apply_btn.setEnabled(True)
                # Cache timings for the live caption overlay.
                self._cap_sentences = data.get("sentence_timings", [])
                self._cap_words = data.get("word_timings", [])
                self._refresh_caption_style()
                self._log("Caption editor ready: template/size abhi preview "
                          "mein nazar aayega — Apply dabao to burn it in.")
            else:
                self._edit_session = ""
        except Exception as e:  # noqa: BLE001 - editor is optional
            self._log(f"Caption editor unavailable: {e}")
            self._edit_session = ""
        # Also place a copy in the user's Videos folder with a safe unique name.
        try:
            dest_dir = app_paths.default_output_dir()
            dest_dir.mkdir(parents=True, exist_ok=True)
            stem = re.sub(r"[^\w\- ]+", "", self.project.name or "video").strip() or "video"
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dest = dest_dir / f"U One {stem} {stamp}.mp4"
            n = 1
            while dest.exists():
                n += 1
                dest = dest_dir / f"U One {stem} {stamp} ({n}).mp4"
            shutil.copy2(out, dest)
            self._log(f"Video copied to: {dest}")
        except Exception as e:  # noqa: BLE001 - never fail a good render on the copy
            self._log(f"Note: could not copy to Videos folder ({e}).")
        self.timeline_widget.set_timeline(self.project.timeline)
        self._refresh_scenes()
        self._refresh_sources()
        # Verify the file exists before trying to play it.
        if os.path.isfile(out):
            if self._ensure_player():
                self.player.setSource(QUrl.fromLocalFile(out))
                self.player.play()
                self._log(f"Playing preview: {out}")
            else:
                self._log("Preview unavailable on this system.")
        else:
            self._log(f"WARNING: output file not found: {out}")
            self.time_lbl.setText("Output file not found")
        if self.project.warnings:
            QMessageBox.warning(self, "Quality check",
                                "\n".join(self.project.warnings[:10]))

    def _apply_caption_edits(self):
        """Re-burn captions with the chosen template/size (fast, no re-render)."""
        if not self._edit_session or not os.path.isfile(self._edit_session):
            QMessageBox.warning(self, "Edit Captions",
                                "Render a video first, then edit its captions.")
            return
        template = self.edit_template_cb.currentData()
        size = self.edit_size_slider.value()
        self.edit_apply_btn.setEnabled(False)
        self.stage_lbl.setText("Updating captions...")
        self._cap_worker = CaptionEditWorker(self._edit_session, template, size)
        self._cap_worker.log.connect(self._log)
        self._cap_worker.finished.connect(self._on_caption_edit_done)
        self._cap_worker.error.connect(self._on_caption_edit_error)
        self._cap_worker.start()

    def _on_caption_edit_done(self, out):
        self.edit_apply_btn.setEnabled(True)
        self.stage_lbl.setText("Done.")
        self._log(f"Captions updated: {out}")
        QMessageBox.information(self, "Edit Captions",
                                "Caption style updated!")
        # Refresh the preview player with the updated video.
        if os.path.isfile(out) and self._ensure_player():
            self.player.setSource(QUrl.fromLocalFile(out))
            self.player.play()
            self._current_video = out

    def _on_caption_edit_error(self, msg):
        self.edit_apply_btn.setEnabled(True)
        self.stage_lbl.setText("Done.")
        QMessageBox.warning(self, "Edit Captions",
                            f"Could not update captions:\n{msg}")

    def _on_error(self, msg):
        self._render_buttons(True)
        self.stage_lbl.setText("Error.")
        # U One never closes on a render failure: save the project, log the
        # technical detail, show the user a simple message.
        try:
            self.project.script = self.script_edit.toPlainText()
            snap = os.path.join(str(app_paths.projects_dir()), "last_error.uvp.json")
            self.project.save(snap)
            self._log(f"Project saved for recovery: {snap}")
        except Exception:  # noqa: BLE001
            pass
        # The render engine also wrote a full failure snapshot
        # (scene states, FFmpeg commands, log path) into the work dir.
        snap2 = os.path.join(self.work_dir, "last_error.uvp.json")
        if os.path.isfile(snap2):
            self._log(f"Failure snapshot: {snap2}")
        if "Cancelled" in msg or "cancelled" in msg:
            self._log("Cancelled by user.")
        else:
            self._log("ERROR: " + msg)
            simple = msg.split("\n")[0][:400]
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("U One")
            box.setText(f"{simple}\n\nYour project has been saved.")
            # Show the technical details (stage, FFmpeg output) in an
            # expandable section so the actual cause is visible.
            if "--- Technical details ---" in msg:
                tech = msg.split("--- Technical details ---", 1)[1].strip()
                box.setDetailedText(tech[:4000])
            box.setStandardButtons(QMessageBox.Ok)
            box.exec()

    def _cancel(self):
        self.cancel_event.set()
        self.cancel_btn.setEnabled(False)
        self.stage_lbl.setText("Cancelling...")
        self._log("Cancelling... (stopping FFmpeg)")

    # ---------------- scenes ----------------
    def _current_scene_id(self):
        it = self.scene_list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _refresh_scenes(self):
        self.scene_list.clear()
        for s in self.pipeline.scenes if self.pipeline else []:
            lock = "🔒 " if getattr(s, "locked", False) else ""
            it = QListWidgetItem(f"{lock}Scene {s.id+1}: {s.text[:70]}")
            it.setData(Qt.UserRole, s.id)
            self.scene_list.addItem(it)

    def _on_scene_clicked(self, item):
        self._show_scene(item.data(Qt.UserRole))

    def _on_scene_selected(self, scene_id):
        self._show_scene(scene_id)
        for i in range(self.scene_list.count()):
            if self.scene_list.item(i).data(Qt.UserRole) == scene_id:
                self.scene_list.setCurrentRow(i)

    # -- CapCut-style timeline editing (right-click a clip) --
    def _timeline_video(self) -> str:
        """Current preview video path (render output or last edit)."""
        if self._current_video and os.path.isfile(self._current_video):
            return self._current_video
        return ""

    def _on_timeline_clip_menu(self, clip_id):
        """Right-click menu on a timeline clip: delete / volume / captions."""
        tl = getattr(self.project, "timeline", None)
        if tl is None:
            return
        clip = tl.get(clip_id)
        if clip is None:
            return
        if not self._timeline_video():
            QMessageBox.information(
                self, "Timeline",
                "Pehle CREATE VIDEO se video banao, phir timeline edit hogi.")
            return
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QCursor
        menu = QMenu(self)
        track = clip.track
        if track == "video":
            act = menu.addAction(
                f"Delete Scene ({clip.label[:32]})")
            act.triggered.connect(
                lambda: self._timeline_delete_scene(clip))
        elif track in ("voice", "sfx", "music"):
            name = {"voice": "Voiceover", "sfx": "SFX",
                    "music": "Music"}[track]
            up = menu.addAction(f"{name}: Volume Up")
            up.triggered.connect(
                lambda: self._timeline_volume(clip, 1.5))
            down = menu.addAction(f"{name}: Volume Down")
            down.triggered.connect(
                lambda: self._timeline_volume(clip, 0.67))
            mute = menu.addAction(f"{name}: Mute")
            mute.triggered.connect(
                lambda: self._timeline_volume(clip, 0.0))
            if track in ("sfx", "music"):
                menu.addSeparator()
                dele = menu.addAction(f"Delete {name} clip")
                dele.triggered.connect(
                    lambda: self._timeline_delete_audio_clip(clip))
        elif track == "captions":
            act = menu.addAction("Remove Captions from video")
            act.triggered.connect(self._timeline_remove_captions)
        elif track == "text":
            act = menu.addAction("Delete Text")
            act.triggered.connect(
                lambda: self._timeline_delete_text(clip))
        if not menu.isEmpty():
            menu.exec(QCursor.pos())

    def _timeline_delete_scene(self, clip):
        """Cut a scene's time range out of the rendered video."""
        if clip.locked:
            QMessageBox.information(self, "Timeline",
                                    "Ye scene locked hai.")
            return
        ans = QMessageBox.question(
            self, "Delete Scene",
            f"Scene '{clip.label[:40]}' video se delete ho jayega "
            f"({clip.start:.1f}s – {clip.end:.1f}s). Continue?",
            QMessageBox.Yes | QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        self._run_timeline_edit(
            "delete_range",
            {"start": clip.start, "end": clip.end,
             "clip_id": clip.id,
             "scene_id": clip.payload.get("scene_id")})

    def _timeline_volume(self, clip, factor):
        """Change audio volume for a clip's time range."""
        dur = None
        tl = getattr(self.project, "timeline", None)
        if tl is not None:
            dur = tl.duration()
        self._run_timeline_edit(
            "volume_range",
            {"start": clip.start, "end": clip.end,
             "factor": factor, "duration": dur,
             "clip_id": clip.id, "mute": factor == 0.0})

    def _timeline_delete_audio_clip(self, clip):
        """Delete an SFX/music clip (mute its range + drop the clip)."""
        self._run_timeline_edit(
            "volume_range",
            {"start": clip.start, "end": clip.end,
             "factor": 0.0, "duration": None,
             "clip_id": clip.id, "mute": True,
             "drop_clip": True})

    def _timeline_delete_text(self, clip):
        """Delete a text overlay clip from the timeline model.

        Note: text is burned into the rendered pixels, so this only
        removes it from the timeline/project (applies to re-renders).
        """
        tl = getattr(self.project, "timeline", None)
        if tl is not None:
            tl.remove(clip.id)
            self.timeline_widget.set_timeline(tl)
            self._log(f"Text removed from timeline: {clip.label[:40]}")
            QMessageBox.information(
                self, "Timeline",
                "Text timeline se hata diya. Note: ye text video mein "
                "burned hai — poori tarah hatane ke liye dobara "
                "CREATE VIDEO karna hoga.")

    def _timeline_remove_captions(self):
        """Restore the caption-free clean copy as the current video."""
        if not self._edit_session or not os.path.isfile(
                self._edit_session):
            QMessageBox.warning(
                self, "Remove Captions",
                "Caption-free copy nahi mili. Pehle video render karo.")
            return
        try:
            from src.caption_editor import load_edit_session
            data = load_edit_session(self._edit_session)
            clean = data.get("clean_video", "")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Remove Captions",
                                f"Session parhne mein masla: {e}")
            return
        if not clean or not os.path.isfile(clean):
            QMessageBox.warning(
                self, "Remove Captions",
                "Caption-free copy nahi mili (scene delete ke baad "
                "sync mein honi chahiye thi).")
            return
        ans = QMessageBox.question(
            self, "Remove Captions",
            "Saari captions video se hata di jayengi. Continue?",
            QMessageBox.Yes | QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        # Make the clean copy the new current video.
        import shutil
        import tempfile
        fd, out = tempfile.mkstemp(suffix=".mp4", prefix="uone_nocap_")
        os.close(fd)
        shutil.copy2(clean, out)
        self._current_video = out
        # Drop the captions clip from the timeline.
        tl = getattr(self.project, "timeline", None)
        if tl is not None:
            for c in tl.by_track("captions"):
                tl.remove(c.id)
            self.timeline_widget.set_timeline(tl)
        if self._ensure_player():
            self.player.setSource(QUrl.fromLocalFile(out))
            self.player.play()
        self._log("Captions removed — clean video restored.")

    def _run_timeline_edit(self, operation, params):
        inp = self._timeline_video()
        if not inp:
            return
        self.stage_lbl.setText("Applying timeline edit...")
        self._tl_worker = TimelineEditWorker(inp, operation, params)
        self._tl_worker.log.connect(self._log)
        self._tl_worker.finished.connect(self._on_timeline_edit_done)
        self._tl_worker.error.connect(self._on_timeline_edit_error)
        self._tl_worker.start()

    def _on_timeline_edit_done(self, out, info):
        self.stage_lbl.setText("Done.")
        op = info.get("op")
        params = info.get("params", {})
        self._current_video = out
        tl = getattr(self.project, "timeline", None)
        if op == "delete_range":
            cut = params["end"] - params["start"]
            if tl is not None:
                # Remove the scene's clips.
                tl.remove(params["clip_id"])
                sid = params.get("scene_id")
                for c in list(tl.clips):
                    if (c.track in ("video", "text")
                            and c.payload.get("scene_id") == sid):
                        tl.remove(c.id)
                # Shift everything after the cut left; shrink clips that
                # span the cut (e.g. the full-length voiceover).
                for c in tl.clips:
                    if c.start >= params["end"] - 0.001:
                        c.start = round(c.start - cut, 3)
                        c.end = round(c.end - cut, 3)
                    elif (c.start < params["end"] - 0.001
                            and c.end > params["end"] + 0.001):
                        c.end = round(max(c.start + 0.1,
                                          c.end - cut), 3)
                self.timeline_widget.set_timeline(tl)
            # Keep the caption-free copy in sync (cut the same range).
            self._timeline_cut_clean_copy(params["start"],
                                          params["end"])
            # Shift cached caption timings for the live overlay.
            self._shift_caption_timings(params["start"],
                                        params["end"])
            self._log(f"Scene deleted ({cut:.1f}s removed).")
        elif op == "volume_range":
            if params.get("drop_clip") and tl is not None:
                tl.remove(params["clip_id"])
                self.timeline_widget.set_timeline(tl)
            f = params.get("factor", 1.0)
            what = "muted" if f == 0 else f"volume x{f:.2f}"
            self._log(f"Audio {what} "
                      f"({params['start']:.1f}s–{params['end']:.1f}s).")
        # Refresh preview with the edited video.
        if os.path.isfile(out) and self._ensure_player():
            self.player.setSource(QUrl.fromLocalFile(out))
            self.player.play()
        QMessageBox.information(self, "Timeline",
                                "Timeline edit apply ho gaya!")

    def _on_timeline_edit_error(self, msg):
        self.stage_lbl.setText("Done.")
        QMessageBox.warning(self, "Timeline",
                            f"Edit fail ho gaya:\n{msg}")

    def _timeline_cut_clean_copy(self, start, end):
        """Cut the same range from the caption-free copy (keeps it in
        sync so Remove Captions still works after scene deletions)."""
        if not self._edit_session or not os.path.isfile(
                self._edit_session):
            return
        try:
            import json
            from src.caption_editor import load_edit_session
            from src.timeline_editor import delete_range
            from src.ffmpeg_util import find_ffmpeg
            from src.ffmpeg_engine import FFmpegEngine
            data = load_edit_session(self._edit_session)
            clean = data.get("clean_video", "")
            if not clean or not os.path.isfile(clean):
                return
            hw_enc = None
            try:
                hw_enc = FFmpegEngine(
                    find_ffmpeg()).detect_hw_encoder("h264")
            except Exception:  # noqa: BLE001
                pass
            new_clean = clean + ".cut.mp4"
            self._log("Syncing caption-free copy...")
            delete_range(clean, start, end, new_clean,
                         ffmpeg_path=find_ffmpeg(), hw_enc=hw_enc,
                         log_cb=lambda m: self._log(m))
            data["clean_video"] = new_clean
            with open(self._edit_session, "w",
                      encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:  # noqa: BLE001
            self._log(f"Clean-copy sync failed (captions remove may "
                      f"not work): {e}")

    def _shift_caption_timings(self, start, end):
        """Shift cached caption timings after a scene deletion."""
        cut = end - start

        def _shift(items):
            out = []
            for s in items:
                ss, ee = s["start"], s["end"]
                if ee <= start + 0.001 or ss >= end - 0.001:
                    if ss >= end - 0.001:
                        s = dict(s)
                        s["start"] = round(ss - cut, 3)
                        s["end"] = round(ee - cut, 3)
                    out.append(s)
                # Overlapping the cut boundary: drop it (safe).
            return out

        self._cap_sentences = _shift(self._cap_sentences)
        self._cap_words = _shift(self._cap_words)

    def _show_scene(self, scene_id):
        if not self.pipeline:
            return
        sc = next((s for s in self.pipeline.scenes if s.id == scene_id), None)
        if not sc:
            return
        self.scene_text.setText(sc.text)
        self.lock_chk.blockSignals(True)
        self.lock_chk.setChecked(bool(getattr(sc, "locked", False)))
        self.lock_chk.blockSignals(False)
        asset = self.pipeline.assets.get(scene_id)
        if asset and asset.kind in ("image", "graphic") and os.path.exists(asset.local_path):
            pm = QPixmap(asset.local_path).scaled(320, 180, Qt.KeepAspectRatio)
            self.scene_thumb.setPixmap(pm)
        elif asset:
            self.scene_thumb.setText(f"[{asset.kind}] {asset.source}\n{os.path.basename(asset.local_path)}")
        self.timeline_widget.selected_scene = scene_id
        self.timeline_widget.update()

    def _toggle_lock(self, state):
        sid = self._current_scene_id()
        if sid is None or not self.pipeline:
            return
        sc = next((s for s in self.pipeline.scenes if s.id == sid), None)
        if sc:
            sc.locked = bool(state)
            for c in self.project.timeline.clips:
                if c.payload.get("scene_id") == sid:
                    c.locked = bool(state)
            self._refresh_scenes()

    def _regen_visual(self):
        sid = self._current_scene_id()
        if sid is None:
            return
        self.project.snapshot()
        asset = self.pipeline.regenerate_visual(sid, log_cb=self._log)
        if asset:
            self._log(f"Scene {sid+1} visual regenerated: {asset.source}")
            self._show_scene(sid)
            self.timeline_widget.update()
        else:
            self._log(f"Scene {sid+1} is locked - not modified.")

    def _regen_text(self):
        sid = self._current_scene_id()
        if sid is None:
            return
        self.project.snapshot()
        self.pipeline.regenerate_text(sid)
        self.timeline_widget.update()
        self._log(f"Scene {sid+1} text regenerated.")

    def _regen_sfx(self):
        sid = self._current_scene_id()
        if sid is None:
            return
        self.project.snapshot()
        self.pipeline.regenerate_sfx(sid)
        self.timeline_widget.update()
        self._log(f"Scene {sid+1} SFX regenerated.")

    def _regen_music(self):
        self.project.snapshot()
        self.pipeline.regenerate_music()
        self.timeline_widget.update()
        self._log("Music regenerated.")

    def _edit_scene_text(self):
        """Let the user directly edit a scene's on-screen text."""
        sid = self._current_scene_id()
        if sid is None or not self.pipeline:
            return
        sc = self.pipeline._scene(sid)
        if not sc:
            return
        from PySide6.QtWidgets import QInputDialog
        current = sc.text or ""
        text, ok = QInputDialog.getMultiLineText(
            self, f"Edit Scene {sid+1} Text",
            "On-screen text (shorter is better for readability):",
            current)
        if ok and text.strip() and text.strip() != current:
            self.project.snapshot()
            sc.text = text.strip()
            # Update the timeline overlay to match
            from src.text_overlays import make_overlay_text
            from src.script_analysis import analyze_sentence
            ov = make_overlay_text(analyze_sentence(sc.text))
            for c in self.pipeline.timeline.by_track("text"):
                if c.payload.get("scene_id") == sid and not c.locked:
                    c.label = ov
            self.timeline_widget.update()
            self._show_scene(sid)
            self._log(f"Scene {sid+1} text updated. Re-render to apply.")

    def _mute_scene_sfx(self):
        """Remove all SFX clips from the selected scene."""
        sid = self._current_scene_id()
        if sid is None or not self.pipeline or not self.pipeline.timeline:
            return
        self.project.snapshot()
        tl = self.pipeline.timeline
        removed = 0
        for c in list(tl.by_track("sfx")):
            if c.payload.get("scene_id") == sid and not c.locked:
                tl.clips.remove(c)
                removed += 1
        self.timeline_widget.update()
        self._log(f"Scene {sid+1}: removed {removed} SFX clip(s). Re-render to apply.")

    # -- CapCut-style structural scene editing (via script) --
    def _scene_sentence_range(self, sid):
        """Return (first, last) sentence ordinals for a scene, or None."""
        if not self.pipeline or not getattr(self.pipeline, "analysis", None):
            return None
        sc = next((s for s in self.pipeline.scenes if s.id == sid), None)
        if not sc:
            return None
        return (sc.first_sentence, sc.last_sentence)

    def _rebuild_script_from_sentences(self, sentences):
        """Update the script editor + project from a sentence list."""
        new_script = " ".join(s for s in sentences if s.strip())
        self.script_edit.setPlainText(new_script)
        self.project.script = new_script
        self.project.snapshot()
        # Re-detect scenes so the UI reflects the new structure.
        try:
            from src.scene_detector import detect_scenes
            from src.script_analysis import analyze_script
            self.pipeline.analysis = analyze_script(new_script)
            sent_texts = [s.text for s in self.pipeline.analysis.sentences]
            self.pipeline.scenes = detect_scenes(
                sent_texts, self.cb_visual.currentText())
            self._refresh_scenes()
            self.timeline_widget.update()
        except Exception as e:  # noqa: BLE001
            self._log(f"Could not refresh scenes: {e}")

    def _delete_scene(self):
        sid = self._current_scene_id()
        if sid is None or not self.pipeline:
            return
        if len(self.pipeline.scenes) <= 1:
            QMessageBox.warning(self, "Delete Scene",
                                "Cannot delete the only scene.")
            return
        rng = self._scene_sentence_range(sid)
        if not rng:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Delete Scene")
        box.setText(f"Delete scene {sid+1}? Click CREATE VIDEO to rebuild.")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return
        sents = [s.text for s in self.pipeline.analysis.sentences]
        f, l = rng
        new_sents = sents[:f] + sents[l + 1:]
        self._rebuild_script_from_sentences(new_sents)
        self._log(f"Scene {sid+1} deleted. Click CREATE VIDEO to rebuild "
                  f"(unchanged scenes reuse cache).")

    def _move_scene(self, delta):
        """Move the selected scene earlier (delta=-1) or later (delta=+1)."""
        sid = self._current_scene_id()
        if sid is None or not self.pipeline:
            return
        scenes = self.pipeline.scenes
        idx = next((i for i, s in enumerate(scenes) if s.id == sid), None)
        if idx is None:
            return
        j = idx + delta
        if j < 0 or j >= len(scenes):
            return
        sents = [s.text for s in self.pipeline.analysis.sentences]
        a, b = scenes[idx], scenes[j]
        # Swap the two scenes' sentence blocks.
        fa, la = a.first_sentence, a.last_sentence
        fb, lb = b.first_sentence, b.last_sentence
        if idx < j:
            # a before b: [..][a][b][..] -> [..][b][a][..]
            new_sents = (sents[:fa] + sents[fb:lb + 1] +
                         sents[fa:la + 1] + sents[lb + 1:])
        else:
            # b before a: [..][b][a][..] -> [..][a][b][..]
            new_sents = (sents[:fb] + sents[fa:la + 1] +
                         sents[fb:lb + 1] + sents[la + 1:])
        self._rebuild_script_from_sentences(new_sents)
        self._log(f"Scene {sid+1} moved. Click CREATE VIDEO to rebuild.")

    def _refresh_sources(self):
        if not self.pipeline:
            return
        rows = list(self.pipeline.assets.items())
        self.sources_table.setRowCount(len(rows))
        for i, (sid, a) in enumerate(rows):
            self.sources_table.setItem(i, 0, QTableWidgetItem(f"Scene {sid+1}"))
            self.sources_table.setItem(i, 1, QTableWidgetItem(f"{a.source}: {a.page_url or a.query}"))
            self.sources_table.setItem(i, 2, QTableWidgetItem(a.license))

    def _youtube(self):
        if not self.pipeline or not self.pipeline.analysis:
            QMessageBox.information(self, "YouTube", "Create a video first.")
            return
        s = youtube_suggest(self.pipeline.analysis, self.pipeline.scenes,
                            self.pipeline.scene_timings)
        txt = "TITLES\n" + "\n".join("- " + t for t in s["titles"])
        txt += "\n\nDESCRIPTION\n" + s["description"]
        txt += "\n\nCHAPTERS\n" + "\n".join(s["chapters"][:20])
        txt += "\n\nTAGS\n" + ", ".join(s["tags"])
        txt += "\n\nTHUMBNAIL TEXT\n" + s["thumbnail_text"]
        self.yt_out.setText(txt)

    # ---------------- project ----------------
    def _browse_voice(self):
        p, _ = QFileDialog.getOpenFileName(self, "Voiceover audio", "",
                                           "Audio (*.wav *.mp3 *.m4a *.ogg)")
        if p:
            self.voice_path_lbl.setText(p)
            self.rb_upload.setChecked(True)

    def _browse_music(self):
        d = QFileDialog.getExistingDirectory(self, "Royalty-free music folder")
        if d:
            self.music_lbl.setText(d)

    def _new_project(self):
        self.project = Project("Untitled")
        self.script_edit.clear()
        self.timeline_widget.set_timeline(None)
        self.scene_list.clear()

    def _save_project(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save project", "",
                                           "Video Project (*.uvp.json)")
        if p:
            self.project.script = self.script_edit.toPlainText()
            self.project.settings = self._settings() or {}
            self.project.save(p if p.endswith(".json") else p + ".uvp.json")
            self._log(f"Project saved: {self.project.path}")

    def _open_project(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open project", "",
                                           "Video Project (*.uvp.json *.json)")
        if p:
            self.project = Project.load(p)
            self.script_edit.setPlainText(self.project.script)
            self.timeline_widget.set_timeline(self.project.timeline)
            self._log(f"Project opened: {p} (re-run CREATE VIDEO to rebuild media)")

    def _undo(self):
        if self.project.undo():
            self.timeline_widget.update()
            self._log("Undo.")

    def _redo(self):
        if self.project.redo():
            self.timeline_widget.update()
            self._log("Redo.")

    def closeEvent(self, ev):
        self.project.stop_autosave()
        super().closeEvent(ev)
