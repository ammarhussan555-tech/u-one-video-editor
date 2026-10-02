"""CapCut-clone caption engine tests (2026-10-02 rewrite).

The 2026-10-01 14-style set was replaced by 23 styles at the user's
request (capcut_clone_research_2026-10-02.md). beast_box retired (its
successor is beast); hormozi/beast/box_highlight/pill/caption_bar/glide/
gradient_sweep/sticker_pop/glitch/pulse added. These tests pin:
  * all 23 styles build valid ASS,
  * each style's animation signature is present and distinct,
  * legacy ids migrate (beast_box -> beast, others -> default),
  * legacy template dicts (no "engine" key) still render safely.
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
    "karaoke_pop": [r"\c&H0000E6FF", r"\t(0,80,0.5,\fscx125\fscy125)"],
    "karaoke": [r"\t(0,60,\fscx112\fscy112)"],
    "hormozi": [r"\fscx70\fscy70", r"\t(0,60,0.5,\fscx120\fscy120)"],
    "beast": [r"\fscx70\fscy70", r"\t(0,90,0.5,\fscx125\fscy125)"],
    "bounce": [r"\move(", r"\fscx118\fscy118"],
    "spring_up": [r"\move(", r"\fscx120\fscy120"],
    "typewriter": ["▌"],
    "wave": [r"\move(", "BREAKING NEWS FROM THE CAPITAL"],
    "zoom": [r"\t(0,120,0.5,\fscx105\fscy105)"],
    "neon_glow": [r"\blur10", r"\3c&H00FFFF00"],
    "highlighter": [r"\bord6", r"\3c&H0000E6FF", r"\fscx105\fscy105"],
    "box_highlight": [r"\3c&H001409E5\bord7", r"\fscx40\fscy40"],
    "pill": [r"\3c&H0000E6FF", r"\bord8", r"\fscx115\fscy115"],
    "caption_bar": [r"\fad(150,0)", r"\move(", "&H66000000"],
    "outline_fill": [r"\1a&HFF&"],
    "underline_sweep": [r"\u1"],
    "glide": [r"\move(", r"\fad(150,0)"],
    "solo_pop": ["Montserrat,91,", r"\t(0,80,0.5,\fscx125\fscy125)"],
    "minimal_fade": [r"\fad(120,120)"],
    "gradient_sweep": [r"\1c&H00F6823B", r"\fscx"],
    "sticker_pop": [r"\frz6", r"\bord8"],
    "glitch": [r"\move(", r"\3c&H000000FF&"],
    "pulse": [r"\fscx108\fscy108"],
}


def test_twenty_three_styles_present():
    assert len(CAPTION_TEMPLATES) == 23
    assert set(_SIGNATURES) == set(CAPTION_TEMPLATES)


def test_default_is_karaoke_pop():
    assert DEFAULT_CAPTION_TEMPLATE == "karaoke_pop"


def test_labels_ordered():
    labels = caption_template_labels()
    assert len(labels) == 23
    assert labels[0] == ("karaoke_pop", "Karaoke Pop")
    assert labels[-1] == ("pulse", "Pulse")


def test_categories_single_capcut():
    assert CAPTION_TEMPLATE_CATEGORIES == ["CapCut"]


def test_visual_recreation_meta():
    for tid, tmpl in CAPTION_TEMPLATES.items():
        assert tmpl["meta"]["source"] == "visual_recreation", tid
        assert tmpl.get("engine"), tid


@pytest.mark.parametrize("tid", sorted(_SIGNATURES))
def test_style_builds_valid_ass(tid, tmp_path):
    txt = _ass_text(tid, tmp_path)
    assert "ScaledBorderAndShadow: yes" in txt
    assert "[Events]" in txt
    assert txt.count("Dialogue:") >= 2


@pytest.mark.parametrize("tid,tokens", sorted(_SIGNATURES.items()))
def test_style_animation_signature(tid, tokens, tmp_path):
    txt = _ass_text(tid, tmp_path)
    for tok in tokens:
        assert tok in txt, (tid, tok)


def test_typewriter_has_no_scale_transforms(tmp_path):
    txt = _ass_text("typewriter", tmp_path)
    assert r"\fscx" not in txt
    assert r"\move(" not in txt


def test_minimal_fade_has_no_scale_or_move(tmp_path):
    txt = _ass_text("minimal_fade", tmp_path)
    assert r"\fscx" not in txt
    assert r"\move(" not in txt
    assert r"\fad(120,120)" in txt


def test_zoom_has_no_move(tmp_path):
    txt = _ass_text("zoom", tmp_path)
    assert r"\move(" not in txt


def test_caption_bar_has_no_scale_or_pop(tmp_path):
    # the bar style's motion is fade+rise only — no color pop, no scale
    txt = _ass_text("caption_bar", tmp_path)
    assert r"\fscx" not in txt


def test_styles_are_distinct():
    """No two styles may share the same animation signature."""
    sigs = {}
    for tid, tokens in _SIGNATURES.items():
        sigs[tid] = tuple(sorted(tokens))
    assert len(set(sigs.values())) == len(sigs)


def test_terminal_punctuation_stripped(tmp_path):
    words = [{"word": "hello,", "start": 0.5, "end": 0.9},
             {"word": "world!", "start": 1.0, "end": 1.4}]
    sents = [{"text": "hello, world!", "start": 0.5, "end": 1.4}]
    p = str(tmp_path / "p.ass")
    build_ass(sents, words, p, template="karaoke_pop")
    txt = open(p, encoding="utf-8").read()
    assert "hello," not in txt.split("[Events]")[1]
    assert "world!" not in txt.split("[Events]")[1]


def test_migrate_legacy_ids():
    assert migrate_caption_template_id("spec_highlight") == "karaoke_pop"
    assert migrate_caption_template_id("spec_word_sync") == "karaoke_pop"
    assert migrate_caption_template_id("tiktok_classic") == "karaoke_pop"
    assert migrate_caption_template_id("karaoke_pop") == "karaoke_pop"
    assert migrate_caption_template_id(None) == "karaoke_pop"


def test_beast_box_migrates_to_beast():
    # retired 2026-10-02: beast_box maps to its successor beast.
    assert migrate_caption_template_id("beast_box") == "beast"


def test_legacy_id_renders_as_default(tmp_path):
    a = _ass_text("spec_highlight", tmp_path)
    b = _ass_text("karaoke_pop", tmp_path)
    assert a == b


def test_beast_box_renders_as_beast(tmp_path):
    a = _ass_text("beast_box", tmp_path)
    b = _ass_text("beast", tmp_path)
    assert a == b


def test_legacy_dict_without_engine_renders(tmp_path):
    p = str(tmp_path / "legacy.ass")
    build_ass(SENTS, WORDS, p, template={"template_key": "spec_impact",
                                         "dx_frac": 0.05})
    txt = open(p, encoding="utf-8").read()
    assert txt.count("Dialogue:") >= 2


def test_plain_fallback_no_words(tmp_path):
    p = str(tmp_path / "plain.ass")
    build_ass(SENTS, [], p, template="bounce")
    txt = open(p, encoding="utf-8").read()
    assert txt.count("Dialogue:") >= 1


def test_old_engine_names_gone():
    from src import text_captions as tc
    for name in ("compose_template", "CAPTION_PRESETS", "CAPTION_MOTIONS",
                 "_word_anim_open", "_entrance_tags"):
        assert not hasattr(tc, name), name


def test_composer_dialog_gone():
    src = open(os.path.join(os.path.dirname(__file__), "..", "src", "ui",
                            "caption_gallery.py"),
               encoding="utf-8").read()
    assert "class CaptionComposerDialog" not in src
    assert "compose_template" not in src


def test_engine_sfx_map():
    from src.audio_design import _caption_chunk_sfx
    assert _caption_chunk_sfx({"engine": "pop"}) == ("pop", -18)
    assert _caption_chunk_sfx({"engine": "typewriter"}) == ("tick", -20)
    assert _caption_chunk_sfx({"engine": "fade"}) is None
    assert _caption_chunk_sfx({"engine": "nope"}) is None
    assert _caption_chunk_sfx("karaoke_pop") is None
    # new engines (2026-10-02) keep their family's sound
    assert _caption_chunk_sfx({"engine": "box_snap"}) == ("pop", -17)
    assert _caption_chunk_sfx({"engine": "pill"}) == ("pop", -17)
    assert _caption_chunk_sfx({"engine": "bar"}) == ("swoosh", -21)
    assert _caption_chunk_sfx({"engine": "glide"}) == ("swoosh", -20)
    assert _caption_chunk_sfx({"engine": "gradient"}) == ("swoosh", -19)
    assert _caption_chunk_sfx({"engine": "sticker"}) == ("punch", -16)
    assert _caption_chunk_sfx({"engine": "glitch"}) == ("tick", -19)
    assert _caption_chunk_sfx({"engine": "pulse"}) == ("tick", -21)


def test_settings_default():
    from src.settings import DEFAULTS
    assert DEFAULTS["caption_template"] == "karaoke_pop"
