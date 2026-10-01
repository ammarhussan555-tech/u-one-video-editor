"""U One - on-screen text / captions facade (v2)."""
from __future__ import annotations

from .text_captions import (
    make_overlay_text, make_overlay, build_srt, build_ass,
    CAPTION_TEMPLATES, DEFAULT_CAPTION_TEMPLATE, caption_template_labels,
    HEADLINE_PRESETS, DEFAULT_HEADLINE_PRESET, headline_preset_for,
    headline_accent_for, detect_niche,
    headline_preset_labels,
    select_main_text, classify_main_text, MAIN_TEXT_TYPES,
    MAIN_TEXT_STYLE, story_box_color,
)

__all__ = ["make_overlay_text", "make_overlay", "build_srt", "build_ass",
           "CAPTION_TEMPLATES", "DEFAULT_CAPTION_TEMPLATE",
           "caption_template_labels", "HEADLINE_PRESETS",
           "DEFAULT_HEADLINE_PRESET", "headline_preset_for",
           "headline_accent_for", "detect_niche",
           "headline_preset_labels",
           "select_main_text", "classify_main_text", "MAIN_TEXT_TYPES",
           "MAIN_TEXT_STYLE", "story_box_color"]
