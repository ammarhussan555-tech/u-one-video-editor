"""U One - caption preset gallery.

A visual picker showing every caption preset as a preview card, like
CapCut's Templates panel: a category browser on the left, preset cards
in a grid on the right. Clicking a card immediately applies it
("Applied ✓" badge); the choice flows into the render settings and the
post-render caption restyle path via the dialog's selected_key.
"""
from __future__ import annotations

import io

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel, QListWidget, QPushButton,
    QScrollArea, QSplitter, QVBoxLayout, QWidget, QLineEdit,
)

from ..text_captions import (
    CAPTION_TEMPLATES,
    DEFAULT_CAPTION_TEMPLATE,
    caption_category_labels,
)

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_OK = True
except Exception:  # noqa: BLE001
    _PIL_OK = False

_CARD_W, _CARD_H = 200, 140


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
    bundled = {
        "anton": ["Anton-Regular.ttf"],
        "bebas": ["BebasNeue-Regular.ttf"],
        "bangers": ["Bangers-Regular.ttf"],
        "montserrat": ["Montserrat-ExtraBold.ttf"],
        "fredoka": ["Fredoka-SemiBold.ttf"],
        "outfit": ["Outfit-ExtraBold.ttf"],
        "poppins": ["Poppins-Bold.ttf"],
        "courier prime": ["CourierPrime-Bold.ttf"],
        "caveat": ["Caveat-Bold.ttf"],
    }
    for _bk, _bf in bundled.items():
        if _bk in n:
            candidates = _bf
            break
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
    try:
        from ..app_paths import fonts_dir
        _fd = fonts_dir()
        if _fd:
            search.insert(0, str(_fd))
    except Exception:  # noqa: BLE001
        pass
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
    """Render a PNG preview card for a caption preset.

    Shows the first word as the active (spoken) word with the preset's
    WORD HIGHLIGHT treatment and the rest as normal words — the
    karaoke look the renderer produces.
    """
    p = CAPTION_TEMPLATES[key]
    img = Image.new("RGB", (_CARD_W, _CARD_H), (26, 26, 30))
    d = ImageDraw.Draw(img)
    primary = _ass_to_rgb(p["primary"])
    active = _ass_to_rgb(p["active_color"])
    outline_c = _ass_to_rgb(p["outline_c"])
    on_hl = _ass_to_rgb(p.get("on_highlight_text", "&H00000000"))
    tcase = p.get("text_case")
    w_active = "THE" if tcase == "upper" else "The"
    w_rest = "QUICK BROWN" if tcase == "upper" else "quick brown"
    hl = p.get("highlight", "color")
    size = 30
    font = _load_font(p["font"], size, bool(p["bold"]), bool(p["italic"]))
    outline_w = max(1, int(p["outline"]))
    em = p.get("emoji", "")

    def _text(px, py, text, fnt, fill, oc=None, ow=None):
        if p["shadow"]:
            d.text((px + 2, py + 2), text, font=fnt, fill=(0, 0, 0))
        d.text((px, py), text, font=fnt, fill=fill,
               stroke_width=ow if ow is not None else outline_w,
               stroke_fill=oc if oc is not None else outline_c)

    # BACKGROUND treatment behind the whole line
    bg = p.get("background", "none")
    words_w = d.textlength(w_active + " " + w_rest, font=font)
    x0 = (_CARD_W - words_w) / 2
    y0 = (_CARD_H - size) / 2 - 6
    if bg in ("box", "bar"):
        alpha = 230 if bg == "box" else 130
        bar = Image.new("RGBA", img.size, (0, 0, 0, 0))
        bd = ImageDraw.Draw(bar)
        bd.rounded_rectangle(
            [x0 - 10, y0 - 8, x0 + words_w + 10, y0 + size + 10],
            radius=6 if bg == "box" else 12, fill=(0, 0, 0, alpha))
        img = Image.alpha_composite(img.convert("RGBA"), bar).convert("RGB")
        d = ImageDraw.Draw(img)
    elif bg == "pill_line":
        d.rounded_rectangle(
            [x0 - 12, y0 - 10, x0 + words_w + 12, y0 + size + 12],
            radius=(size + 20) // 2, fill=(20, 20, 24))

    aw = d.textlength(w_active + " ", font=font)
    ax = x0
    # WORD HIGHLIGHT treatment on the active word
    label = (em + " " if em else "") + w_active + (" " + em if em else "")
    if hl == "box":
        d.rounded_rectangle([ax - 6, y0 - 5, ax + aw + 2, y0 + size + 7],
                            radius=6, fill=active)
        d.text((ax, y0), label, font=font, fill=on_hl, stroke_width=0)
    elif hl == "pill":
        d.rounded_rectangle([ax - 8, y0 - 6, ax + aw + 4, y0 + size + 8],
                            radius=(size + 14) // 2, fill=active)
        d.text((ax, y0), label, font=font, fill=on_hl, stroke_width=0)
    elif hl == "underline":
        _text(ax, y0, label, font, active)
        d.rectangle([ax, y0 + size + 4, ax + aw - 4, y0 + size + 9],
                    fill=active)
    elif hl == "wash":
        d.rounded_rectangle([ax - 5, y0 - 4, ax + aw + 1, y0 + size + 6],
                            radius=6, fill=active)
        d.text((ax, y0), label, font=font, fill=primary, stroke_width=0)
    elif hl == "glow":
        _text(ax, y0, label, font, active, oc=active, ow=outline_w + 3)
    elif hl == "outline":
        d.text((ax, y0), label, font=font, fill=(26, 26, 30),
               stroke_width=outline_w, stroke_fill=active)
    elif hl == "dim":
        _text(ax, y0, label, font, active)
    else:  # color / none
        fill = active if hl == "color" else primary
        _text(ax, y0, label, font, fill)
    # remaining words in the normal color
    rest_fill = (125, 125, 125) if hl == "dim" else primary
    _text(ax + aw, y0, w_rest, font, rest_fill)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


_ANIM_BADGE = {
    "pop": "⤴ pop", "spring": "⤴ spring", "stomp": "⬇ stomp",
    "fade": "◐ fade", "rise": "⬆ rise", "fall": "⬇ drop",
    "wipe": "➡ wipe", "jitter": "⚡ jitter", "rotate": "🔃 rotate",
    "glowpulse": "✨ glow", "throb": "💓 throb", "typewriter": "⌨ type",
    "slide": "➡ slide", "sweep": "🌈 sweep", "wave": "🌊 wave",
    "none": "",
}


class _PresetCard(QWidget):
    """One clickable preset card with an Applied ✓ badge."""
    clicked = Signal(str)

    def __init__(self, key: str, label: str, parent=None):
        super().__init__(parent)
        self.key = key
        self._selected = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        self.img_lbl = QLabel()
        self.img_lbl.setAlignment(Qt.AlignCenter)
        self.img_lbl.setFixedSize(_CARD_W, _CARD_H)
        if _PIL_OK:
            try:
                pm = QPixmap()
                pm.loadFromData(render_template_preview(key))
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
        lay.addWidget(name)
        # animation badge
        try:
            badge_txt = _ANIM_BADGE.get(
                CAPTION_TEMPLATES.get(key, {}).get("active_anim", ""), "")
            if badge_txt:
                badge = QLabel(badge_txt)
                badge.setAlignment(Qt.AlignCenter)
                badge.setStyleSheet(
                    "color:#7fe08a;font-size:10px;font-weight:bold;")
                lay.addWidget(badge)
        except Exception:  # noqa: BLE001
            pass
        # Applied ✓ badge (visible only on the selected card)
        self.applied = QLabel("✓ Applied")
        self.applied.setAlignment(Qt.AlignCenter)
        self.applied.setStyleSheet(
            "color:#0a0;font-size:12px;font-weight:bold;")
        self.applied.setVisible(False)
        lay.addWidget(self.applied)
        self.setCursor(Qt.PointingHandCursor)
        self._refresh_style()

    def _refresh_style(self):
        if self._selected:
            self.setStyleSheet(
                "background:#1d3a2a;border:2px solid #2ecc71;"
                "border-radius:10px;")
        else:
            self.setStyleSheet(
                "background:#232327;border:2px solid transparent;"
                "border-radius:10px;")

    def set_selected(self, sel: bool):
        self._selected = sel
        self.applied.setVisible(sel)
        self._refresh_style()

    def mousePressEvent(self, ev):  # noqa: D102
        self.clicked.emit(self.key)
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):  # noqa: D102
        self.clicked.emit(self.key)
        w = self
        while w is not None and not isinstance(w, CaptionGalleryDialog):
            w = w.parentWidget()
        if w is not None:
            w.accept_with(self.key)
        super().mouseDoubleClickEvent(ev)


class CaptionGalleryDialog(QDialog):
    """Preset library browser: categories on the left, preset cards right.

    Clicking a card immediately applies it (✓ Applied badge); Use Template
    confirms the dialog. selected_key flows into render settings and the
    post-render caption restyle path.
    """

    def __init__(self, current_key: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Caption Presets")
        self.setMinimumSize(900, 600)
        self.selected_key = current_key or DEFAULT_CAPTION_TEMPLATE
        self._cards = {}
        self._category = "All"

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("type a preset name...")
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search, 1)
        lay.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        # category browser
        self.cat_list = QListWidget()
        self.cat_list.setMaximumWidth(170)
        self.cat_list.setStyleSheet(
            "QListWidget{background:#1a1a1e;color:#ccc;border:none;}"
            "QListWidget::item{padding:6px;}"
            "QListWidget::item:selected{background:#0a6e4e;color:#fff;}")
        self.cat_list.addItem("All (120)")
        for label, count in caption_category_labels():
            self.cat_list.addItem("%s (%d)" % (label, count))
        self.cat_list.setCurrentRow(0)
        self.cat_list.currentRowChanged.connect(self._on_category_row)
        split.addWidget(self.cat_list)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(8)
        scroll.setWidget(self.grid_host)
        split.addWidget(scroll)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

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

    def _on_category_row(self, row: int):
        item = self.cat_list.item(row)
        if item is None:
            return
        text = item.text()
        self._category = "All" if text.startswith("All") else text.rsplit(
            " (", 1)[0]
        self._populate(self.search.text())

    def _populate(self, filt: str):
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._cards = {}
        filt = (filt or "").lower()
        cols = 3
        r = c = 0
        for key, p in CAPTION_TEMPLATES.items():
            label = p["label"]
            if (self._category != "All"
                    and p.get("category") != self._category):
                continue
            if filt and filt not in label.lower() and filt not in key.lower():
                continue
            card = _PresetCard(key, label)
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
        # click-to-apply: the preset takes effect immediately (✓ badge)
        self.selected_key = key
        for k, card in self._cards.items():
            card.set_selected(k == key)

    def accept_with(self, key: str):
        self.selected_key = key
        self.accept()
