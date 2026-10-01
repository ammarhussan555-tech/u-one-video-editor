"""U One - on-screen text / captions facade (v2)."""
from __future__ import annotations

from .text_captions import (
    make_overlay_text, build_srt, build_ass,
    CAPTION_TEMPLATES, DEFAULT_CAPTION_TEMPLATE, caption_template_labels,
    HEADLINE_PRESETS, DEFAULT_HEADLINE_PRESET, headline_preset_for,
    headline_accent_for, detect_niche,
    headline_preset_labels,
)

__all__ = ["make_overlay_text", "build_srt", "build_ass",
           "CAPTION_TEMPLATES", "DEFAULT_CAPTION_TEMPLATE",
           "caption_template_labels", "HEADLINE_PRESETS",
           "DEFAULT_HEADLINE_PRESET", "headline_preset_for",
           "headline_accent_for", "detect_niche",
           "headline_preset_labels"]
