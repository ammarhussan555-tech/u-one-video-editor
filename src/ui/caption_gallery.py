"""U One - CapCut-style caption template gallery.

A visual picker showing every caption template as a preview card
("The quick" rendered in that template's style), like CapCut's
Templates panel. Click a card to select, double-click (or Use button)
to confirm.
"""
from __future__ import annotations

import io

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget, QLineEdit,
)

from ..text_captions import CAPTION_TEMPLATES

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_OK = True
except Exception:  # noqa: BLE001
    _PIL_OK = False

_CARD_W, _CARD_H = 200, 130


def _ass_to_rgb(color: str):
    """&HAABBGGRR -> (R, G, B)."""
    c = (color or "").strip().lstrip("&H")
    if len(c) < 8:
        c = "00" + c.zfill(6)
    try:
        bb, gg, rr = c[2:4], c[4:6], c[6:8]
        return int(rr, 16), int(gg, 16), int(bb, 16)
    except Exception:  # noqa: BLE001
        return 255, 255, 255


def _load_font(name: str, size: int, bold: bool, italic: bool):
    """Best-effort font load; falls back to PIL default."""
    candidates = []
    n = (name or "").lower()
    if "arial black" in n:
        candidates = ["ariblk.ttf", "Arial Black.ttf"]
    elif "arial" in n:
        candidates = ["arialbd.ttf" if bold else "arial.ttf",
                      "ariali.ttf" if italic else "arial.ttf"]
    elif "times" in n:
        candidates = ["timesbd.ttf" if bold else "times.ttf"]
    elif "courier" in n:
        candidates = ["courbd.ttf" if bold else "cour.ttf"]
    elif "segoe" in n:
        candidates = ["segoeuib.ttf" if bold else "segoeui.ttf"]
    candidates += ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"]
    import os
    search = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")]
    search += ["/usr/share/fonts", os.path.expanduser("~/.fonts")]
    for d in search:
        if not os.path.isdir(d):
            continue
        for root, _ds, files in os.walk(d):
            for cand in candidates:
                if cand.lower() in (f.lower() for f in files):
                    for f in files:
                        if f.lower() == cand.lower():
                            try:
                                return ImageFont.truetype(
                                    os.path.join(root, f), size)
                            except Exception:  # noqa: BLE001
                                pass
            break
    try:
        return ImageFont.truetype(candidates[-1], size)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def render_template_preview(key: str) -> bytes:
    """Render a PNG preview card for a caption template.

    Shows "The" in the highlight (secondary/karaoke) color and "quick"
    in the primary color - the CapCut active-word look. Respects
    text_case and the wordbox highlight mode.
    """
    tmpl = CAPTION_TEMPLATES[key]
    img = Image.new("RGB", (_CARD_W, _CARD_H), (26, 26, 30))
    d = ImageDraw.Draw(img)
    primary = _ass_to_rgb(tmpl["primary"])
    secondary = _ass_to_rgb(tmpl["secondary"])
    outline_c = _ass_to_rgb(tmpl["outline_c"])
    tcase = tmpl.get("text_case")
    w_the = "THE" if tcase == "upper" else "The"
    w_quick = "QUICK" if tcase == "upper" else "quick"
    hmode = tmpl.get("highlight_mode", "karaoke")
    size = 34
    # wordbig: highlight word is larger
    big_size = int(size * tmpl.get("big_scale", 1.8)) if hmode == "wordbig" else size
    font = _load_font(tmpl["font"], size,
                      bool(tmpl["bold"]), bool(tmpl["italic"]))
    big_font = _load_font(tmpl["font"], big_size,
                          bool(tmpl["bold"]), bool(tmpl["italic"]))
    w1 = d.textlength(w_the + " ", font=big_font if hmode == "wordbig" else font)
    w2 = d.textlength(w_quick, font=font)
    total = w1 + w2
    x = (_CARD_W - total) / 2
    y = (_CARD_H - size) / 2 - 4
    # pill: rounded black background behind the whole caption
    if tmpl.get("pill"):
        pill_c = _ass_to_rgb(tmpl.get("back_c", "&HC8000000"))
        d.rounded_rectangle([x - 14, y - 10, x + total + 14, y + size + 14],
                            radius=18, fill=pill_c)
    outline_w = max(1, int(tmpl["outline"]))
    glow_c = _ass_to_rgb(tmpl.get("glow_color", tmpl["secondary"]))

    def _draw_text(px, text, fill, hl_box=False, use_big=False, use_glow=False):
        fnt = big_font if use_big else font
        ty = y - (big_size - size) // 2 if use_big else y
        if tmpl["box"]:
            # opaque box behind text (Hormozi style)
            bx0 = px - 6
            bx1 = px + d.textlength(text, font=fnt) + 6
            d.rectangle([bx0, ty - 6, bx1, ty + size + 6],
                        fill=outline_c)
        if hl_box:
            # highlight box behind the active word
            box_c = _ass_to_rgb(tmpl.get("box_color", "&H00FF0000"))
            bx0 = px - 4
            bx1 = px + d.textlength(text, font=fnt) + 4
            d.rectangle([bx0, ty - 4, bx1, ty + size + 4], fill=box_c)
        if tmpl["shadow"]:
            d.text((px + 2, ty + 2), text, font=fnt, fill=(0, 0, 0))
        oc = glow_c if use_glow else outline_c
        ow = outline_w + 2 if use_glow else outline_w
        d.text((px, ty), text, font=fnt, fill=fill,
               stroke_width=ow, stroke_fill=oc)

    if hmode == "wordbox":
        _draw_text(x, w_the + " ", primary, hl_box=True)
        _draw_text(x + w1, w_quick, primary)
    elif hmode == "wordbig":
        _draw_text(x, w_the + " ", secondary, use_big=True)
        _draw_text(x + w1, w_quick, primary)
    elif hmode == "wordglow":
        _draw_text(x, w_the + " ", secondary, use_glow=True)
        _draw_text(x + w1, w_quick, primary)
    else:
        _draw_text(x, w_the + " ", secondary)
        _draw_text(x + w1, w_quick, primary)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _TemplateCard(QWidget):
    """One clickable template preview card."""
    clicked = Signal(str)

    def __init__(self, key: str, label: str, parent=None):
        super().__init__(parent)
        self.key = key
        self._selected = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        self.img_lbl = QLabel()
        self.img_lbl.setAlignment(Qt.AlignCenter)
        self.img_lbl.setFixedSize(_CARD_W, _CARD_H)
        if _PIL_OK:
            try:
                png = render_template_preview(key)
                pm = QPixmap()
                pm.loadFromData(png)
                self.img_lbl.setPixmap(pm)
            except Exception:  # noqa: BLE001
                self.img_lbl.setText(label)
        else:
            self.img_lbl.setText(label)
        self.img_lbl.setStyleSheet(
            "background:#1a1a1e;border-radius:8px;")
        lay.addWidget(self.img_lbl)
        name = QLabel(label)
        name.setAlignment(Qt.AlignCenter)
        name.setStyleSheet("color:#ccc;font-size:11px;")
        name.setWordWrap(True)
        lay.addWidget(name)
        self.setCursor(Qt.PointingHandCursor)
        self._refresh_style()

    def _refresh_style(self):
        if self._selected:
            self.setStyleSheet(
                "background:#2a4a6a;border:2px solid #4da3ff;border-radius:10px;")
        else:
            self.setStyleSheet(
                "background:#232327;border:2px solid transparent;border-radius:10px;")

    def set_selected(self, sel: bool):
        self._selected = sel
        self._refresh_style()

    def mousePressEvent(self, ev):  # noqa: D102
        self.clicked.emit(self.key)
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):  # noqa: D102
        self.clicked.emit(self.key)
        # find the dialog and accept
        w = self
        while w is not None and not isinstance(w, CaptionGalleryDialog):
            w = w.parentWidget()
        if w is not None:
            w.accept_with(self.key)
        super().mouseDoubleClickEvent(ev)


class CaptionGalleryDialog(QDialog):
    """CapCut-style template picker grid."""

    def __init__(self, current_key: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Caption Templates - CapCut Style")
        self.setMinimumSize(760, 540)
        self.selected_key = current_key or "tiktok_classic"
        self._cards = {}

        lay = QVBoxLayout(self)
        # search row
        top = QHBoxLayout()
        top.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("type a style name...")
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search, 1)
        lay.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(8)
        scroll.setWidget(self.grid_host)
        lay.addWidget(scroll, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.use_btn = QPushButton("Use Template")
        self.use_btn.setDefault(True)
        self.use_btn.clicked.connect(
            lambda: self.accept_with(self.selected_key))
        btns.addWidget(cancel)
        btns.addWidget(self.use_btn)
        lay.addLayout(btns)

        self._populate("")
        self.setStyleSheet("QDialog{background:#141416;}")

    def _populate(self, filt: str):
        # clear
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._cards = {}
        filt = (filt or "").lower()
        cols = 3
        r = c = 0
        for key, tmpl in CAPTION_TEMPLATES.items():
            label = tmpl["label"]
            if filt and filt not in label.lower() and filt not in key.lower():
                continue
            card = _TemplateCard(key, label)
            card.clicked.connect(self._on_card_clicked)
            card.set_selected(key == self.selected_key)
            self.grid.addWidget(card, r, c)
            self._cards[key] = card
            c += 1
            if c >= cols:
                c = 0
                r += 1

    def _filter(self, text: str):
        self._populate(text)

    def _on_card_clicked(self, key: str):
        self.selected_key = key
        for k, card in self._cards.items():
            card.set_selected(k == key)

    def accept_with(self, key: str):
        self.selected_key = key
        self.accept()
