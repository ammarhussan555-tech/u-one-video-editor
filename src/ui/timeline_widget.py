"""6-track timeline widget: paint tracks, click a clip to select its scene."""
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QColor, QBrush, QPen

TRACKS = ["voice", "video", "text", "captions", "sfx", "music"]
COLORS = {
    "voice": QColor("#7c5cff"), "video": QColor("#2f9e44"), "text": QColor("#e67700"),
    "captions": QColor("#1971c2"), "sfx": QColor("#d6336c"), "music": QColor("#0c8599"),
}


class TimelineWidget(QWidget):
    sceneSelected = Signal(int)  # scene_id
    clipRightClicked = Signal(str)  # clip_id (for CapCut-style edit menu)
    clipSelected = Signal(str)  # clip_id (any left-click selection)
    clipMoved = Signal(str)  # clip_id (after a drag-move finishes)

    def __init__(self):
        super().__init__()
        self.timeline = None
        self.selected_scene = None
        self.selected_clip_id = None
        self._drag_clip = None
        self._drag_start_x = 0.0
        self._drag_orig = (0.0, 0.0)
        self.setMinimumHeight(250)
        self.setMouseTracking(True)

    def set_timeline(self, tl):
        self.timeline = tl
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#14161c"))
        if not self.timeline or not self.timeline.clips:
            p.setPen(QColor("#888"))
            p.drawText(self.rect(), Qt.AlignCenter, "Timeline appears after CREATE VIDEO")
            p.end()
            return
        dur = max(0.01, self.timeline.duration())
        W = self.width()
        row_h = (self.height() - 8) / len(TRACKS)
        for row, tr in enumerate(TRACKS):
            y = 4 + row * row_h
            p.setPen(QColor("#555"))
            p.drawText(6, int(y + 14), tr.upper())
            for c in self.timeline.by_track(tr):
                x = 70 + (c.start / dur) * (W - 80)
                ww = max(3, (c.end - c.start) / dur * (W - 80))
                r = QRectF(x, y + 2, ww, row_h - 6)
                col = COLORS[tr]
                if c.payload.get("scene_id") == self.selected_scene:
                    col = QColor("#ffd43b")
                p.setBrush(QBrush(col))
                p.setPen(QPen(QColor("#000"), 1))
                p.drawRoundedRect(r, 4, 4)
                if ww > 60:
                    p.setPen(QColor("#fff"))
                    p.drawText(r.adjusted(4, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft,
                               c.label[:28])
                if c.locked:
                    p.setPen(QColor("#fff"))
                    p.drawText(r, Qt.AlignRight | Qt.AlignVCenter, "🔒 ")
        p.end()

    def _clip_at(self, pos):
        """Return the Clip under the given widget position, or None."""
        if not self.timeline:
            return None
        dur = max(0.01, self.timeline.duration())
        W = self.width()
        row_h = (self.height() - 8) / len(TRACKS)
        for row, tr in enumerate(TRACKS):
            y = 4 + row * row_h
            for c in self.timeline.by_track(tr):
                x = 70 + (c.start / dur) * (W - 80)
                ww = max(3, (c.end - c.start) / dur * (W - 80))
                if QRectF(x, y + 2, ww, row_h - 6).contains(pos):
                    return c
        return None

    def mousePressEvent(self, ev):
        c = self._clip_at(ev.position())
        if c is None:
            return
        # Track selected clip for the toolbar buttons (CapCut-style: select then act).
        self.selected_clip_id = c.id
        self.clipSelected.emit(c.id)
        sid = c.payload.get("scene_id")
        if sid is not None:
            self.selected_scene = sid
            self.sceneSelected.emit(sid)
        self.update()
        # Begin a CapCut-style drag-move (left button, unlocked clip).
        if ev.button() == Qt.LeftButton and not c.locked:
            self._drag_clip = c
            self._drag_start_x = ev.position().x()
            self._drag_orig = (c.start, c.end)
            self.setCursor(Qt.ClosedHandCursor)
        # NOTE: right-click menu is handled ONLY by contextMenuEvent below
        # (handling it here too would open the menu twice per click).

    def mouseMoveEvent(self, ev):
        c = self._drag_clip
        if c is None or not self.timeline:
            return
        dur = max(0.01, self.timeline.duration())
        dx_px = ev.position().x() - self._drag_start_x
        dt = dx_px / max(1, self.width() - 80) * dur
        new_start = max(0.0, self._drag_orig[0] + dt)
        span = self._drag_orig[1] - self._drag_orig[0]
        c.start = new_start
        c.end = new_start + span
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag_clip is not None:
            moved = self._drag_clip
            self._drag_clip = None
            self.setCursor(Qt.ArrowCursor)
            self.clipMoved.emit(moved.id)
            self.update()

    def contextMenuEvent(self, ev):
        # Right-click (or menu key) -> CapCut-style edit menu for this clip.
        c = self._clip_at(ev.pos())
        if c is not None:
            self.selected_clip_id = c.id
            self.clipSelected.emit(c.id)
            self.clipRightClicked.emit(c.id)
