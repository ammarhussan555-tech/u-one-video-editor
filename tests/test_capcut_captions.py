"""Real-tools caption engine tests (2026-10-02).

The 23-style CapCut-clone set was replaced by 28 styles grounded in REAL
popular auto-caption tools (~/workspace/research/popular_tools_captions.md):
Submagic (Hormozi 1/Beast/Karaoke/Neon/Gradient/Minimal), Captions.ai
(Box/Ali/Pulse), Opus Clip (Karaoke-lime/Beasty/Deep Diver/Pod P/Mozi/
Popline/Youshaei/Glitch/Bounce/Quake), VEED (Impact Pop/Stomp/Reveal/
Float In/Scale In/Drop In/Rotate & Flip/Colour Highlight), Universal
(Typewriter). These tests pin:
  * all 28 styles build valid ASS,
  * each style's animation signature is present and distinct,
  * legacy ids migrate to their closest successor,
  * every style is labelled a visual recreation (never official),
  * every engine has a caption UI sound mapped.
"""
import os

import pytest

from src.text_captions import (  # noqa: E402
    CAPTION_TEMPLATES,
    CAPTION_TEMPLATE_CATEGORIES,
    DEFAULT_CAPTION_TEMPLATE,
    build_ass,
    caption_template_labels,
    migrate_caption_template_id,
)
from src.audio_design import _ENGINE_SFX  # noqa: E402

WORDS = [
    {"word": w, "start": 0.5 + i * 0.4, "end": 0.5 + i * 0.4 + 0.35}
    for i, w in enumerate("Breaking news from the capital".split())
]
SENTS = [{"text": "Breaking news from the capital", "start": 0.5, "end": 2.9}]


def _ass_text(tid, tmp_path, **kw):
    p = str(tmp_path / ("cap_%s.ass" % tid))
    build_ass(SENTS, WORDS, p, font_size=48, highlight=True,
              template=tid, **kw)
    return open(p, encoding="utf-8").read()


# style id -> animation tokens that MUST appear in its ASS
# (tokens pinned to the actual template configs in text_captions.py)
_SIGNATURES = {
    # Submagic
    "submagic_hormozi": [r"\t(0,80,0.5,\fscx125\fscy125)",
                         r"\c&H0000E6FF"],
    "submagic_beast": [r"\t(0,90,0.5,\fscx135\fscy135)", "Bangers,"],
    "submagic_karaoke": [r"\t(0,60,\fscx100\fscy100)",
                         r"\c&H0000D7FF"],
    "submagic_neon": [r"\blur10", r"\3c&H00FFFF00"],
    "submagic_gradient": [r"\1c&H00F6823B", r"\1c&H00F65C8B"],
    "submagic_minimal": [r"\fad(150,150)"],
    # Captions.ai
    "captions_box": [r"\3c&H0000E6FF\bord7", r"\fscx40\fscy40"],
    "captions_ali": [r"\fad(200,200)"],
    "captions_pulse": [r"\fscx108\fscy108"],
    # Opus
    "opus_karaoke": [r"\3c&H0035E6A3\bord8", r"\c&H00000000\alpha"],
    "opus_beasty": [r"\fad(250,250)", "Playfair Display,"],
    "opus_deepdiver": [r"\c&H00808080", r"\c&H00D6F7FF"],
    "opus_podp": ["Anton,", r"\t(0,70,0.5,\fscx122\fscy122)"],
    "opus_mozi": [r"\c&H0035E6A3", r"\fs60"],
    "opus_popline": [r"\N", r"\fs76", r"\c&H00882EFF"],
    "opus_youshaei": [r"\c&H00808080", r"\c&H00FFD400"],
    "opus_glitch": [r"\3c&H000000FF&", r"\move("],
    "opus_bounce": [r"\move(", r"\fscx118\fscy118"],
    "opus_quake": [r"\move(", "BREAKING"],
    # VEED
    "veed_impact": [r"\fscx0\fscy0", r"\t(0,100,0.5,\fscx115\fscy115)"],
    "veed_stomp": [r"\fscx135\fscy135\t(0,90,1.8,\fscx100\fscy100)"],
    "veed_reveal": [r"\fscx30\fscy100\t(0,120,0.5,\fscx100\fscy100)"],
    "veed_float": [r"\fad(350,0)", r"\move("],
    "veed_scale": [r"\fscx55\fscy55", r"\t(0,150,0.5,\fscx100\fscy100)"],
    "veed_drop": [r"\move(", "BREAKING"],
    "veed_rotate": [r"\frz-10", r"\fscx80\fscy80"],
    "veed_colour": [r"\3c&H00ED3A7C", r"\3a&HFF&"],
    # Universal
    "typewriter": ["▌"],
}


def test_twenty_eight_styles_present():
    assert len(CAPTION_TEMPLATES) == 28
    assert set(_SIGNATURES) == set(CAPTION_TEMPLATES)


def test_default_is_submagic_hormozi():
    assert DEFAULT_CAPTION_TEMPLATE == "submagic_hormozi"


def test_labels_ordered():
    labels = caption_template_labels()
    assert len(labels) == 28
    assert labels[0] == ("submagic_hormozi", "Submagic Hormozi")


def test_categories_are_real_tools():
    assert CAPTION_TEMPLATE_CATEGORIES == [
        "Submagic", "Captions.ai", "Opus", "VEED", "Universal"]
    cats = {t["category"] for t in CAPTION_TEMPLATES.values()}
    assert cats <= set(CAPTION_TEMPLATE_CATEGORIES)


def test_visual_recreation_meta():
    for tid, tmpl in CAPTION_TEMPLATES.items():
        assert tmpl.get("meta", {}).get("source") == "visual_recreation", tid
        assert "official" not in tmpl["desc"].lower(), tid


@pytest.mark.parametrize("tid", sorted(_SIGNATURES))
def test_style_builds_valid_ass(tid, tmp_path):
    txt = _ass_text(tid, tmp_path)
    assert "[V4+ Styles]" in txt
    assert txt.count("Dialogue:") > 1, tid


@pytest.mark.parametrize("tid,tokens", sorted(_SIGNATURES.items()))
def test_style_animation_signature(tid, tokens, tmp_path):
    txt = _ass_text(tid, tmp_path)
    for tok in tokens:
        assert tok in txt, f"{tid}: missing {tok!r}"


def test_pop_envelopes_differ():
    """Styles sharing the pop engine must differ in envelope/layout/font."""
    pops = {tid: t for tid, t in CAPTION_TEMPLATES.items()
            if t["engine"] == "pop"}
    seen = set()
    for tid, t in pops.items():
        key = (t["pop"]["frm"], t["pop"]["peak"], t["pop"]["up_ms"],
               t.get("layout"), t["font"], t["primary"], t["secondary"])
        assert key not in seen, f"{tid} duplicates another pop style"
        seen.add(key)


def test_styles_are_distinct():
    """No two styles may render byte-identical ASS."""
    import tempfile
    outs = {}
    for tid in CAPTION_TEMPLATES:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.ass")
            build_ass(SENTS, WORDS, p, font_size=48, template=tid)
            outs[tid] = open(p, encoding="utf-8").read()
    assert len(set(outs.values())) == len(outs)


def test_dim_upcoming_words_grey(tmp_path):
    txt = _ass_text("opus_deepdiver", tmp_path)
    assert txt.count(r"\c&H00808080") >= 2


def test_mozi_punchline_lime_and_big(tmp_path):
    txt = _ass_text("opus_mozi", tmp_path)
    assert r"\c&H0035E6A3" in txt and r"\fs60" in txt


def test_popline_two_lines(tmp_path):
    txt = _ass_text("opus_popline", tmp_path)
    assert r"\N" in txt


def test_opus_karaoke_dark_text_on_lime(tmp_path):
    txt = _ass_text("opus_karaoke", tmp_path)
    assert r"\c&H00000000\alpha&H00&" in txt


def test_veed_colour_has_no_motion(tmp_path):
    txt = _ass_text("veed_colour", tmp_path)
    ev = txt.split("[Events]")[1]
    assert r"\move(" not in ev
    assert ev.replace(r"\fscx100", "").count(r"\fscx") == 0


def test_submagic_karaoke_pure_color_no_scale(tmp_path):
    txt = _ass_text("submagic_karaoke", tmp_path)
    ev = txt.split("[Events]")[1]
    assert r"\fscx112" not in ev and r"\fscx125" not in ev


def test_migrate_legacy_ids():
    from src.text_captions import _MIGRATION_MAP
    for old, new in _MIGRATION_MAP.items():
        assert migrate_caption_template_id(old) == new, old
        assert new in CAPTION_TEMPLATES


def test_legacy_id_renders_as_successor(tmp_path):
    txt = _ass_text("karaoke_pop", tmp_path)  # retired -> hormozi
    txt2 = _ass_text("submagic_hormozi", tmp_path)
    assert txt == txt2


def test_unknown_id_falls_back_to_default(tmp_path):
    txt = _ass_text("does_not_exist_xyz", tmp_path)
    assert txt == _ass_text(DEFAULT_CAPTION_TEMPLATE, tmp_path)


def test_legacy_dict_without_engine_renders(tmp_path):
    from src.text_captions import build_ass as _ba
    p = str(tmp_path / "legacy.ass")
    _ba(SENTS, WORDS, p, font_size=48, template={"template_key": "karaoke_pop"})
    txt = open(p, encoding="utf-8").read()
    assert txt == _ass_text("submagic_hormozi", tmp_path)


def test_engine_sfx_map_covers_all_styles():
    engines = {t["engine"] for t in CAPTION_TEMPLATES.values()}
    # fade is intentionally silent: a sound on every faded line is annoying
    missing = engines - set(_ENGINE_SFX) - {"fade"}
    assert not missing, f"engines without UI sound: {missing}"


def test_gallery_previews_render():
    pytest.importorskip("PySide6.QtWidgets")
    from src.ui.caption_gallery import render_template_preview
    for tid in CAPTION_TEMPLATES:
        png = render_template_preview(tid)
        assert png[:8] == b"\x89PNG\r\n\x1a\n", tid
        assert len(png) > 1000, tid
