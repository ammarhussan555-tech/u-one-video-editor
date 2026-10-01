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

from ..text_captions import CAPTION_TEMPLATES, DEFAULT_CAPTION_TEMPLATE

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
    # bundled OFL fonts ship in assets/fonts — check there first
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
    """Render a PNG preview card for a caption template.

    Shows "The" as the active word (secondary color / active treatment)
    and "quick" as the inactive word (primary) - the CapCut karaoke look.
    Per-engine treatments (box, glow, underline, hollow, solo, typewriter)
    are drawn so each card is visually distinct.
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
    engine = tmpl.get("engine", "pop")
    size = 34
    font = _load_font(tmpl["font"], size,
                      bool(tmpl["bold"]), bool(tmpl["italic"]))
    outline_w = max(1, int(tmpl["outline"]))
    glow_c = _ass_to_rgb(tmpl.get("glow_c", tmpl["secondary"]))

    def _text(px, py, text, fnt, fill, oc=None, ow=None):
        if tmpl["shadow"]:
            d.text((px + 2, py + 2), text, font=fnt, fill=(0, 0, 0))
        d.text((px, py), text, font=fnt, fill=fill,
               stroke_width=ow if ow is not None else outline_w,
               stroke_fill=oc if oc is not None else outline_c)

    if engine == "solo":
        # one giant word at a time, centered
        bf = _load_font(tmpl["font"], 52,
                        bool(tmpl["bold"]), bool(tmpl["italic"]))
        bw = d.textlength(w_the, font=bf)
        _text((_CARD_W - bw) / 2, (_CARD_H - 52) / 2, w_the, bf, secondary)
    elif engine == "typewriter":
        # partial text + block cursor at the frontier
        frag = w_the + " qu"
        w1 = d.textlength(frag, font=font)
        x = (_CARD_W - w1 - 14) / 2
        y = (_CARD_H - size) / 2 - 4
        _text(x, y, frag, font, primary)
        d.rectangle([x + w1 + 2, y, x + w1 + 12, y + size], fill=secondary)
    else:
        w1 = d.textlength(w_the + " ", font=font)
        w2 = d.textlength(w_quick, font=font)
        total = w1 + w2
        x = (_CARD_W - total) / 2
        y = (_CARD_H - size) / 2 - 4
        if tmpl.get("box_mode") == "persistent":
            # black box behind the whole phrase
            d.rounded_rectangle(
                [x - 10, y - 8, x + total + 10, y + size + 10],
                radius=10, fill=(0, 0, 0))
        elif engine == "highlight_box":
            # marker stroke: black text with a thick yellow outline
            box_c = _ass_to_rgb(tmpl.get("box_color", "&H0000E6FF&"))
            d.text((x, y), w_the + " ", font=font, fill=(0, 0, 0),
                   stroke_width=5, stroke_fill=box_c)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        if engine == "glow":
            _text(x, y, w_the + " ", font, secondary,
                  oc=glow_c, ow=outline_w + 3)
        elif engine == "outline_fill":
            # hollow active word: colored outline, no fill
            d.text((x, y), w_the + " ", font=font, fill=(26, 26, 30),
                   stroke_width=outline_w, stroke_fill=secondary)
        elif engine == "wave":
            _text(x, y - 8, w_the + " ", font, secondary)
        else:
            _text(x, y, w_the + " ", font, secondary)
        if engine == "underline":
            bar_c = _ass_to_rgb(tmpl.get("bar_c", tmpl["secondary"]))
            d.rectangle([x, y + size + 4, x + w1 - 4, y + size + 10],
                        fill=bar_c)
        quick_fill = (125, 125, 125) if engine == "fade" else primary
        _text(x + w1, y, w_quick, font, quick_fill)
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
        # animation badge so templates don't all look static
        try:
            from ..text_captions import CAPTION_TEMPLATES
            tmpl = CAPTION_TEMPLATES.get(key, {})
            badge_txt = {
                "pop": "▶ word pop",
                "spring": "▶ spring",
                "karaoke": "🎤 karaoke",
                "wave": "🌊 wave",
                "highlight_box": "🖍 highlight",
                "underline": "➖ underline",
                "outline_fill": "▢ fill",
                "glow": "✨ glow",
                "fade": "◐ fade",
                "typewriter": "⌨ typewriter",
                "solo": "🔍 solo pop",
            }.get(tmpl.get("engine", ""), "")
            if badge_txt:
                badge = QLabel(badge_txt)
                badge.setAlignment(Qt.AlignCenter)
                badge.setStyleSheet(
                    "color:#7fe08a;font-size:10px;font-weight:bold;")
                lay.addWidget(badge)
        except Exception:  # noqa: BLE001
            pass
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
        self.setMinimumSize(760, 580)
        self.selected_key = current_key or DEFAULT_CAPTION_TEMPLATE
        self._cards = {}
        self._category = "All"

        lay = QVBoxLayout(self)
        # search row
        top = QHBoxLayout()
        top.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("type a style name...")
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search, 1)
        lay.addLayout(top)

        # CapCut-style category tabs
        try:
            from ..text_captions import CAPTION_TEMPLATE_CATEGORIES
        except Exception:
            CAPTION_TEMPLATE_CATEGORIES = []
        cats = ["All"] + [c for c in CAPTION_TEMPLATE_CATEGORIES
                if c != "All"]
        self._cat_btns = {}
        if len(set(cats)) > 1:
            tabrow = QHBoxLayout()
            tabrow.setSpacing(4)
            for cat in cats:
                b = QPushButton(cat)
                b.setCheckable(True)
                b.setChecked(cat == self._category)
                b.setStyleSheet(
                    "QPushButton{background:#232326;color:#ccc;border:1px solid #333;"
                    "border-radius:10px;padding:4px 10px;}"
                    "QPushButton:checked{background:#0aa;color:#fff;}")
                b.clicked.connect(lambda _c=False, c=cat: self._set_category(c))
                tabrow.addWidget(b)
                self._cat_btns[cat] = b
            tabrow.addStretch(1)
            lay.addLayout(tabrow)

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

    def _set_category(self, cat: str):
        self._category = cat
        for c, b in self._cat_btns.items():
            b.setChecked(c == cat)
        self._populate(self.search.text())

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
        items = list(CAPTION_TEMPLATES.items())
        for key, tmpl in items:
            label = tmpl["label"]
            if (self._category != "All"
                    and tmpl.get("category") != self._category):
                continue
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
