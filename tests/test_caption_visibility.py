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
