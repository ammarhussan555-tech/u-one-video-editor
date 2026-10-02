"""120-preset caption library smoke tests (2026-10-02 rebuild).

The 28-style real-tools set was replaced by the 120-preset data-driven
library (24 categories x 5 presets). These tests pin the library shape,
the default, and per-category animation signatures.
"""
import pytest

from src.text_captions import (
    CAPTION_TEMPLATES,
    CAPTION_TEMPLATE_CATEGORIES,
    DEFAULT_CAPTION_TEMPLATE,
    build_ass,
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


def test_library_shape():
    assert len(CAPTION_TEMPLATES) == 120
    assert len(CAPTION_TEMPLATE_CATEGORIES) == 24
    assert DEFAULT_CAPTION_TEMPLATE == "dynamic_01"
    assert DEFAULT_CAPTION_TEMPLATE in CAPTION_TEMPLATES


def test_every_preset_builds_ass(tmp_path):
    for pid in CAPTION_TEMPLATES:
        txt = _ass_text(pid, tmp_path)
        assert "Dialogue: 1," in txt, pid


# preset id -> ASS tokens that must appear (pinned to preset field data)
_SIGNATURES = {
    "dynamic_01": [r"\t(0,80,0.5,\fscx125\fscy125)", r"\c&H0000E6FF"],
    "hormozi_01": [r"\c&H0066FF00", "Montserrat,"],
    "neon_01": [r"\blur", r"\c&H00FFFF00"],
    "typewriter_01": ["\u258c", "Courier Prime,"],
    "gaming_01": [r"\move(", r"\c&H00303BFF"],
    "meme_01": ["Anton,", ",8,"],
    "news_01": ["3,", r"\c&H00FFFFFF"],
    "karaoke_02": [r"\bord8", r"\c&H00000000\3c"],
    "advanced_01": [r"\1c&H00F6823B"],
    "advanced_02": [r"\1a&HFF&"],
    "emoji_01": ["\U0001F525"],
    "bounce_01": [r"\move(", r"\fscx"],
    "pop_05": [r"\fscx135\fscy135"],
    "animated_05": [r"\move("],
}


def test_preset_signatures(tmp_path):
    for pid, tokens in _SIGNATURES.items():
        txt = _ass_text(pid, tmp_path)
        for tok in tokens:
            assert tok in txt, (pid, tok)


def test_migration_from_retired_sets():
    assert migrate_caption_template_id("submagic_hormozi") == "hormozi_01"
    assert migrate_caption_template_id("opus_mozi") == "advanced_03"
    assert migrate_caption_template_id("veed_stomp") == "pop_05"
    assert migrate_caption_template_id("karaoke_pop") == "dynamic_01"
    assert migrate_caption_template_id("glitch") == "gaming_01"
    assert migrate_caption_template_id("unknown_xyz") == \
        DEFAULT_CAPTION_TEMPLATE


def test_all_presets_are_visual_recreations():
    for pid, p in CAPTION_TEMPLATES.items():
        assert p["meta"].get("source") == "visual_recreation", pid
