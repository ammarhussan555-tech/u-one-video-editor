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

    def __init__(self):
        super().__init__()
        self.timeline = None
        self.selected_scene = None
        self.setMinimumHeight(250)

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

    def mousePressEvent(self, ev):
        if not self.timeline:
            return
        dur = max(0.01, self.timeline.duration())
        W = self.width()
        row_h = (self.height() - 8) / len(TRACKS)
        for row, tr in enumerate(TRACKS):
            y = 4 + row * row_h
            for c in self.timeline.by_track(tr):
                x = 70 + (c.start / dur) * (W - 80)
                ww = max(3, (c.end - c.start) / dur * (W - 80))
                if QRectF(x, y + 2, ww, row_h - 6).contains(ev.position()):
                    sid = c.payload.get("scene_id")
                    if sid is not None:
                        self.selected_scene = sid
                        self.sceneSelected.emit(sid)
                        self.update()
                    return
