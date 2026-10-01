"""CapCut-style caption engine tests (2026-10-01 rewrite).

The old 8+5+5 spec engine (compose_template / CAPTION_PRESETS /
CAPTION_MOTIONS / spec_* templates / CaptionComposerDialog) was removed.
These tests pin the new 14-style engine:
  * every style builds valid ASS,
  * each style's animation signature is present and distinct,
  * legacy template ids migrate to the default style,
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
_SIGNATURES = {
    "karaoke_pop": [r"\c&H0000E6FF", r"\t(0,80,0.5,\fscx125\fscy125)"],
    "karaoke": [r"\t(0,60,\fscx112\fscy112)"],
    "bounce": [r"\move(", r"\fscx118\fscy118"],
    "spring_up": [r"\move(", r"\fscx120\fscy120"],
    "typewriter": ["▌"],
    "wave": [r"\move(", "BREAKING NEWS FROM THE CAPITAL"],
    "zoom": [r"\t(0,120,0.5,\fscx105\fscy105)"],
    "neon_glow": [r"\blur10", r"\3c&H00FFFF00"],
    "highlighter": [r"\bord6", r"\3c&H0000E6FF"],
    "beast_box": [r"\c&H0000E6FF", "&HA6000000"],
    "underline_sweep": [r"\u1"],
    "outline_fill": [r"\1a&HFF&"],
    "solo_pop": ["Montserrat,91,", r"\t(0,80,0.5,\fscx125\fscy125)"],
    "minimal_fade": [r"\fad(120,120)"],
}


def test_fourteen_styles_present():
    assert len(CAPTION_TEMPLATES) == 14
    assert set(_SIGNATURES) == set(CAPTION_TEMPLATES)


def test_default_is_karaoke_pop():
    assert DEFAULT_CAPTION_TEMPLATE == "karaoke_pop"


def test_labels_ordered():
    labels = caption_template_labels()
    assert len(labels) == 14
    assert labels[0] == ("karaoke_pop", "Karaoke Pop")


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


def test_legacy_id_renders_as_default(tmp_path):
    a = _ass_text("spec_highlight", tmp_path)
    b = _ass_text("karaoke_pop", tmp_path)
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


def test_settings_default():
    from src.settings import DEFAULTS
    assert DEFAULTS["caption_template"] == "karaoke_pop"
