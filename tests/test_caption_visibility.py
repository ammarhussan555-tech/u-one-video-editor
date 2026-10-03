"""Regression tests for Uzair's 2026-10-02 16:11 PKT report:
"video bana ka bad caption change kay wo nazar nhi aye"
(after rendering, the caption change is not visible).

Three root causes, all fixed:

1. _caption_overlay_style() only understood the OLD template schema
   (bold == -1, box/back_c keys). The 120 schema-2 presets use
   bold=True/False and background="box"/"none" + bg_opacity, so the
   live preview showed every preset as plain non-bold text with no box
   -- picking a new style looked like (almost) nothing changed.

2. A big caption drag baked \\pos() coordinates OFF the frame into the
   burn (Uzair's log: dy_frac=0.2485 -> y=1288 on 1080p). The preview
   overlay clamps via margins and stays visible, but the burn did not --
   after Apply the captions silently vanished.

3. Clicking the video opened the caption text editor, which crashed
   with "unhashable type: 'dict'" because the session's template can be
   a composed dict and was used directly as a dict key.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SRC_UI = ROOT / "src" / "ui" / "main_window.py"

from src.text_captions import _clamp_burn_xy, CAPTION_TEMPLATES  # noqa: E402


def _bind(name):
    """Bind an unbound MainWindow method to a fake self (no Qt needed)."""
    tree = ast.parse(SRC_UI.read_text(encoding="utf-8"))
    fns = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    fns[name] = item
    assert name in fns, f"MainWindow.{name} not found"
    ns = {}
    exec(compile(ast.Module(body=[fns[name]], type_ignores=[]),
                 "<test>", "exec"), ns)
    return ns[name]


class _Slider:
    def value(self):
        return 48


def _overlay_style(template_key):
    overlay_fn = _bind("_caption_overlay_style")
    tmpl_fn = _bind("_active_tmpl_dict")
    css_fn = _bind("_ass_to_css")
    size_fn = _bind("_current_caption_size")

    class Fake:  # noqa: D106
        pass
    s = Fake()
    s.edit_size_slider = _Slider()
    s._cap_pos = {"dx": 0.0, "dy": 0.0, "scale": 1.0}
    s._caption_template_key = template_key
    s._active_tmpl_dict = tmpl_fn.__get__(s)
    s._ass_to_css = staticmethod(css_fn).__get__(s)
    s._current_caption_size = size_fn.__get__(s)
    return overlay_fn(s)


def test_overlay_renders_schema2_bold_and_box():
    style = _overlay_style("bold_05")
    assert "font-weight: bold" in style, f"bold lost: {style}"
    assert "background-color" in style, f"box bg lost: {style}"
    assert "Anton" in style


def test_overlay_renders_schema2_no_box():
    style = _overlay_style("wordbyword_04")
    assert "background-color" not in style, f"unexpected box: {style}"
    assert "Montserrat" in style


def test_burn_clamp_keeps_uzair_drag_on_screen():
    # His log: dx_frac=0.1287, dy_frac=0.2485, bottom anchor (960,1020).
    cx, cy = _clamp_burn_xy(960 + round(0.1287 * 1920),
                            1020 + round(0.2485 * 1080), 1920, 1080, 48)
    assert 0 <= cx <= 1920 and 0 <= cy <= 1080, (cx, cy)


def test_burn_clamp_never_moves_default_anchors():
    for ax, ay in [(960, 1020), (960, 60), (960, 540), (960, 907)]:
        assert _clamp_burn_xy(ax, ay, 1920, 1080, 48) == (ax, ay)


def test_burn_clamp_extreme_drags_stay_on_screen():
    for x, y in [(960 + 864, 1020 + 486), (960 - 864, 60 - 486),
                 (-500, -500), (3000, 2000)]:
        cx, cy = _clamp_burn_xy(x, y, 1920, 1080, 48)
        assert 0 <= cx <= 1920 and 0 <= cy <= 1080, (x, y)


def test_text_editor_never_uses_dict_as_key():
    # The "unhashable type: 'dict'" crash: data["template"] may be a dict.
    src = SRC_UI.read_text(encoding="utf-8")
    assert 'CAPTION_TEMPLATES.get(data.get("template")' not in src, \
        "dict used directly as dict key again"
    # And the chosen-key plumbing must normalize dicts before use.
    assert 'isinstance(_tkey, dict)' in src



def _update_fn():
    return _bind("_update_caption_text")


class _Ov:
    """Overlay stub: records setText; raises if karaoke path is reached."""

    def __init__(self):
        self.calls = []

    def text(self):
        return "OLD"

    def setText(self, t):
        self.calls.append(t)
        if t != "":
            raise AssertionError("should not reach karaoke rendering")


def _suppressed(burned, current):
    """True iff _update_caption_text suppresses the overlay (returns
    early after clearing), without needing the karaoke fakes."""
    import os as _os
    fn = _update_fn()
    fn.__globals__["os"] = _os

    class Fake:  # noqa: D106
        pass
    s = Fake()
    s.caption_overlay = _Ov()
    s._burned_caps_video = burned
    s._current_video = current
    s._cap_sentences = [{"text": "hello", "start": 0, "end": 5}]
    try:
        fn(s, 1000)
    except (AssertionError, AttributeError):
        return False  # reached karaoke -> not suppressed
    return s.caption_overlay.calls == [""]


def test_overlay_hidden_on_burned_video():
    # Uzair 2026-10-03 video: the karaoke overlay ghosted on top of the
    # burned-in captions ("pehli line ka pehla word"). While previewing
    # the burned video, the overlay must stay empty.
    assert _suppressed("/v/final.mp4", "/v/final.mp4")


def test_overlay_shown_when_no_burned_video():
    assert not _suppressed("", "/v/other.mp4")


def test_overlay_shown_when_paths_differ():
    assert not _suppressed("/v/final.mp4", "/v/other.mp4")


def _visual_fn(name):
    """Bind a _CaptionEditVisual method to a fake (no Qt)."""
    tree = ast.parse(SRC_UI.read_text(encoding="utf-8"))
    fns = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "_CaptionEditVisual":
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    fns[item.name] = item
    import types as _t
    fn = fns[name]
    mod = ast.Module(body=[fn], type_ignores=[])

    class _QP:
        def __init__(self, x, y):
            self._x, self._y = x, y

        def x(self): return self._x

        def y(self): return self._y

    ns = {}
    exec(compile(mod, "<test>", "exec"), ns)
    return ns[name]


class _QP:
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self): return self._x

    def y(self): return self._y


class _QR:
    def __init__(self, x, y, w, h):
        self._x, self._y, self._w, self._h = x, y, w, h

    def x(self): return self._x

    def y(self): return self._y

    def width(self): return self._w

    def height(self): return self._h

    def contains(self, pt):
        return (self._x <= pt.x() <= self._x + self._w
                and self._y <= pt.y() <= self._y + self._h)


class _VisFake:
    PAD = 26
    HANDLE_R = 9
    X_R = 11

    def __init__(self, w, h):
        self._w, self._h = w, h

    def width(self): return self._w

    def height(self): return self._h

    def _box_rect(self):
        pad = self.PAD
        return _QR(pad, pad, self._w - pad * 2, self._h - pad * 2)

    def _handle_points(self):
        r = self._box_rect()
        x0, y0 = r.x(), r.y()
        x1, y1 = x0 + r.width(), y0 + r.height()
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        return {
            "nw": _QP(x0, y0), "n": _QP(cx, y0),
            "ne": _QP(x1, y0), "e": _QP(x1, cy),
            "se": _QP(x1, y1), "s": _QP(cx, y1),
            "sw": _QP(x0, y1), "w": _QP(x0, cy),
        }

    def _x_center(self):
        r = self._box_rect()
        return _QP(r.x() - 4, r.y() - 18)


def _pt(x, y):
    import types as _t
    return _t.SimpleNamespace(x=lambda: x, y=lambda: y)


def test_handle_hit_corners():
    fn = _visual_fn("handle_at")
    v = _VisFake(252, 152)
    # PAD=26: box (26,26)-(226,126); corners at those points
    assert fn(v, _pt(26, 26)) == "nw"
    assert fn(v, _pt(226, 26)) == "ne"
    assert fn(v, _pt(26, 126)) == "sw"
    assert fn(v, _pt(226, 126)) == "se"


def test_handle_hit_midpoints():
    fn = _visual_fn("handle_at")
    v = _VisFake(252, 152)
    assert fn(v, _pt(126, 26)) == "n"
    assert fn(v, _pt(226, 76)) == "e"
    assert fn(v, _pt(126, 126)) == "s"
    assert fn(v, _pt(26, 76)) == "w"


def test_handle_hit_x_button():
    fn = _visual_fn("handle_at")
    v = _VisFake(252, 152)
    # X at (26-4, 26-18) = (22, 8)
    assert fn(v, _pt(22, 8)) == "x"


def test_handle_hit_body_and_outside():
    fn = _visual_fn("handle_at")
    v = _VisFake(252, 152)
    assert fn(v, _pt(126, 76)) == "body"
    assert fn(v, _pt(2, 140)) is None
