"""On-screen text overlays + captions (SRT + styled ASS).

Overlays are rendered through libass (a top-positioned ASS style) instead of
FFmpeg drawtext, so long text, Unicode, commas, colons, quotes and
apostrophes can never break a filter graph.
"""
import os
import re

from .ffmpeg_util import sanitize_ass_text


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Caption styles grounded in REAL popular auto-caption tools (2026-10-02).
# See the CAPTION_TEMPLATES block below for per-tool sourcing.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# CAPTION PRESET LIBRARY — 120 data-driven presets (2026-10-02 rebuild).
#
# 24 categories x 5 presets. Every preset is PURE DATA with these fields:
#   FONT:            font, size_scale, bold, italic, text_case, spacing
#   COLOR:           primary            (normal words)
#   STROKE:          outline, outline_c  (px + color)
#   SHADOW:          shadow             (px, 0 = none)
#   POSITION:        position           (bottom|lower|center|top)
#   WORD HIGHLIGHT:  highlight          (color|box|pill|underline|wash|glow|
#                                       dim|outline|none),
#                    active_color, on_highlight_text
#   ACTIVE WORD ANIMATION:
#                    active_anim        (pop|spring|stomp|fade|rise|fall|wipe|
#                                       jitter|rotate|glowpulse|throb|
#                                       typewriter|slide|sweep|wave|none),
#                    anim_peak, anim_frm (0=auto), anim_up_ms, anim_total_ms,
#                    anim_y, anim_amp
#   WORD TIMING:     lead_ms, floor_ms, hold_ms
#   LINE BREAK:      max_words
#   BACKGROUND:      background         (none|box|bar|pill_line), bg_opacity
#   EMOJI:           emoji              ("" = none; else decorates active word)
#   ENTRY:           entry              (pop|fade|slide_up|none), entry_ms
#   EXIT:            exit               (fade|none), exit_ms
#   LAYOUT:          layout             (standard|two_tone|headline)
#
# Concepts are inspired by popular tools (CapCut's large preset library,
# Submagic's dynamic word highlighting, Captions.ai's creator styles, Opus
# Clip's automatic emphasis, VEED's caption style presets, VN's
# word-by-word animation, InShot/ZapCap/Clipchamp/Canva/Adobe Express
# preset approaches) — but every preset is an ORIGINAL recreation. No
# proprietary templates, keyframes, or assets are copied. Every preset
# carries meta {"source": "visual_recreation"}.
#
# RENDERER LAW (user-mandated): the renderer in this module is a PURE
# function of these fields. It must never invent animations, effects,
# emojis, SFX, colors, or movements not present in the selected preset.
# Only the currently spoken word (from word-level audio timestamps)
# receives the preset's active-word animation; previous words revert to
# normal automatically because each active-word overlay event is
# independent of the others.
# ---------------------------------------------------------------------------

_VR_META = {"source": "visual_recreation"}

# ASS colors, &HAABBGGRR.
_C_WHITE = "&H00FFFFFF"
_C_YELLOW = "&H0000E6FF"   # #FFE600
_C_GOLD = "&H0000D7FF"     # #FFD700
_C_CYAN = "&H00FFFF00"     # #00FFFF
_C_GREEN = "&H0066FF00"    # #00FF66
_C_LIME = "&H0035E6A3"     # #A3E635
_C_PINK = "&H00882EFF"     # #FF2E88
_C_ORANGE = "&H000066FF"   # #FF6600
_C_RED = "&H00303BFF"      # #FF3B30
_C_PURPLE = "&H00F755A8"   # #A855F7
_C_TEAL = "&H00FFD400"     # #00D4FF
_C_BLUE = "&H00F8BD38"     # #38BDF8
_C_CREAM = "&H00EBFBFF"    # #FFFBEB
_C_GREY = "&H00808080"
_C_BLACK = "&H00000000"
_C_DIM_UPCOMING = "&H00A0A0A0"

#: Defaults for every preset field; category bases and variants override.
_PRESET_DEFAULTS = {
    "font": "Montserrat", "size_scale": 1.0, "bold": True, "italic": False,
    "text_case": "upper", "spacing": 0,
    "primary": _C_WHITE,
    "outline": 3, "outline_c": _C_BLACK,
    "shadow": 1,
    "position": "bottom",
    "highlight": "color", "active_color": _C_YELLOW,
    "on_highlight_text": _C_BLACK,
    "active_anim": "pop", "anim_peak": 120, "anim_frm": 0,
    "anim_up_ms": 80, "anim_total_ms": 160, "anim_y": 0, "anim_amp": 4,
    "lead_ms": -20, "floor_ms": 140, "hold_ms": 200,
    "max_words": 4,
    "background": "none", "bg_opacity": 0.85,
    "emoji": "",
    "entry": "pop", "entry_ms": 180,
    "exit": "fade", "exit_ms": 120,
    "layout": "standard",
    "preset_schema": 2,
}

# (slug, label, concept, base overrides, [5 variant override dicts]).
# Every variant differs from its base in at least 3 fields.
_CATEGORY_DEFS = [
    ("classic", "Classic", "CapCut/Clipchamp clean caption presets",
     {"text_case": "title", "outline": 2, "shadow": 1, "highlight": "color",
      "active_anim": "none", "entry": "fade", "entry_ms": 200},
     [
         {"active_color": _C_YELLOW},
         {"active_color": _C_CYAN, "position": "lower"},
         {"active_color": _C_GREEN, "text_case": "upper", "entry": "pop",
          "entry_ms": 150},
         {"active_color": _C_ORANGE, "font": "Poppins", "size_scale": 0.95},
         {"active_color": _C_PINK, "outline": 3, "position": "lower",
          "size_scale": 1.05},
     ]),
    ("bold", "Bold", "heavy broadcast weight",
     {"outline": 5, "size_scale": 1.15, "active_anim": "pop",
      "anim_peak": 130, "shadow": 2},
     [
         {"font": "Anton", "active_color": _C_YELLOW},
         {"font": "Montserrat", "active_color": _C_ORANGE,
          "active_anim": "stomp", "anim_peak": 135},
         {"font": "Bebas Neue", "active_color": _C_CYAN,
          "active_anim": "spring", "anim_y": 20},
         {"font": "Bangers", "active_color": _C_LIME, "position": "lower"},
         {"font": "Anton", "active_color": _C_PINK, "background": "box",
          "bg_opacity": 0.9},
     ]),
    ("dynamic", "Dynamic", "Submagic-style dynamic word highlighting",
     {"active_anim": "pop", "anim_peak": 125, "max_words": 4,
      "highlight": "color"},
     [
         {"active_color": _C_YELLOW, "font": "Montserrat"},
         {"active_color": _C_GREEN, "active_anim": "spring", "anim_y": 16},
         {"active_color": _C_CYAN, "anim_peak": 130, "position": "lower"},
         {"active_color": _C_ORANGE, "active_anim": "stomp",
          "anim_peak": 135},
         {"active_color": _C_YELLOW, "active_anim": "throb"},
     ]),
    ("pop", "Pop", "scale-overshoot punch-ins",
     {"active_anim": "pop"},
     [
         {"active_anim": "pop", "anim_frm": 0, "anim_peak": 115,
          "anim_up_ms": 100, "active_color": _C_YELLOW},
         {"font": "Bangers", "anim_frm": 70, "anim_peak": 135,
          "active_color": _C_ORANGE},
         {"font": "Anton", "active_color": _C_PINK, "background": "box"},
         {"anim_frm": 55, "anim_peak": 105, "anim_up_ms": 150,
          "active_color": _C_CYAN},
         {"active_anim": "stomp", "anim_peak": 135,
          "active_color": _C_YELLOW, "font": "Montserrat"},
     ]),
    ("bounce", "Bounce", "springy scale + vertical bounce",
     {"active_anim": "spring", "anim_y": 18, "anim_peak": 118},
     [
         {"active_color": _C_YELLOW},
         {"active_color": _C_GREEN, "anim_peak": 125, "anim_y": 30},
         {"active_color": _C_CYAN, "anim_peak": 110, "anim_y": 10},
         {"active_color": _C_YELLOW, "highlight": "box",
          "on_highlight_text": _C_BLACK},
         {"active_color": _C_PINK, "position": "lower", "font": "Poppins"},
     ]),
    ("karaoke", "Karaoke", "pure word-by-word color sync",
     {"active_anim": "none", "highlight": "color", "entry": "fade"},
     [
         {"active_color": _C_YELLOW},
         {"highlight": "pill", "active_color": _C_LIME,
          "on_highlight_text": _C_BLACK},
         {"highlight": "dim", "active_color": _C_CYAN},
         {"highlight": "dim", "active_color": _C_TEAL, "font": "Poppins"},
         {"highlight": "underline", "active_color": _C_YELLOW},
     ]),
    ("hormozi", "Hormozi-style", "heavy caps business-creator look",
     {"font": "Montserrat", "text_case": "upper", "outline": 4,
      "shadow": 2, "active_anim": "pop", "anim_peak": 120, "max_words": 4},
     [
         {"active_color": _C_GREEN},
         {"active_color": _C_YELLOW, "outline": 5, "anim_peak": 125},
         {"active_color": _C_CYAN, "active_anim": "spring", "anim_y": 14},
         {"active_color": _C_ORANGE, "background": "box"},
         {"active_color": _C_GREEN, "size_scale": 1.1, "position": "lower"},
     ]),
    ("highlight", "Highlight", "marker / box / pill / underline treatments",
     {"highlight": "box"},
     [
         {"highlight": "box", "active_color": _C_YELLOW,
          "on_highlight_text": _C_BLACK, "active_anim": "pop",
          "anim_peak": 110},
         {"highlight": "pill", "active_color": _C_YELLOW,
          "on_highlight_text": _C_BLACK, "active_anim": "pop"},
         {"highlight": "wash", "active_color": _C_PURPLE,
          "active_anim": "fade"},
         {"highlight": "underline", "active_color": _C_YELLOW,
          "active_anim": "none"},
         {"highlight": "box", "active_color": _C_ORANGE,
          "on_highlight_text": _C_WHITE, "active_anim": "stomp"},
     ]),
    ("wordbyword", "Word-by-word", "VN-style compact word focus",
     {"max_words": 3, "active_anim": "pop", "anim_peak": 125},
     [
         {"active_color": _C_YELLOW},
         {"max_words": 2, "size_scale": 1.2, "active_color": _C_GREEN},
         {"active_color": _C_CYAN, "active_anim": "spring", "anim_y": 16},
         {"max_words": 2, "active_color": _C_PINK, "active_anim": "stomp"},
         {"active_color": _C_ORANGE, "active_anim": "fade",
          "font": "Poppins"},
     ]),
    ("minimal", "Minimal", "small quiet lower-third",
     {"size_scale": 0.85, "outline": 1, "shadow": 0, "active_anim": "fade",
      "entry": "fade", "exit": "fade", "text_case": "title"},
     [
         {"active_color": _C_BLUE, "font": "Poppins"},
         {"active_color": _C_CREAM, "position": "lower"},
         {"active_color": _C_TEAL, "italic": True},
         {"highlight": "none", "active_anim": "fade"},
         {"size_scale": 0.75, "active_color": _C_YELLOW, "outline": 1},
     ]),
    ("cinematic", "Cinematic", "slow elegant title cards",
     {"spacing": 2, "entry": "fade", "entry_ms": 400, "exit": "fade",
      "exit_ms": 200, "active_anim": "fade", "position": "lower",
      "text_case": "title"},
     [
         {"active_color": _C_WHITE, "highlight": "none"},
         {"active_color": _C_GOLD, "active_anim": "rise", "anim_y": 20},
         {"position": "center", "active_color": _C_CREAM},
         {"active_color": _C_GOLD, "spacing": 4, "size_scale": 1.1},
         {"active_color": _C_BLUE, "font": "Poppins", "italic": True},
     ]),
    ("news", "News", "broadcast lower-third bars",
     {"background": "bar", "bg_opacity": 0.75, "position": "bottom",
      "text_case": "upper", "font": "Montserrat"},
     [
         {"active_color": _C_WHITE, "highlight": "none",
          "active_anim": "fade"},
         {"active_color": _C_YELLOW, "active_anim": "pop", "anim_peak": 112},
         {"background": "box", "active_color": _C_WHITE,
          "highlight": "none"},
         {"position": "lower", "active_color": _C_CYAN,
          "active_anim": "none"},
         {"background": "box", "bg_opacity": 0.9, "active_color": _C_WHITE,
          "highlight": "none", "active_anim": "stomp", "font": "Anton"},
     ]),
    ("documentary", "Documentary", "calm explanatory captions",
     {"font": "Poppins", "text_case": "title", "active_anim": "fade",
      "entry": "fade", "entry_ms": 300},
     [
         {"active_color": _C_WHITE, "highlight": "none"},
         {"active_color": _C_CREAM, "position": "lower"},
         {"active_color": _C_BLUE, "highlight": "color"},
         {"italic": True, "active_color": _C_WHITE, "highlight": "none"},
         {"background": "bar", "bg_opacity": 0.5, "active_color": _C_CREAM},
     ]),
    ("podcast", "Podcast", "casual conversational badges",
     {"font": "Fredoka", "text_case": "title"},
     [
         {"highlight": "pill", "active_color": _C_YELLOW,
          "on_highlight_text": _C_BLACK, "active_anim": "pop"},
         {"active_color": _C_CYAN, "active_anim": "spring", "anim_y": 14},
         {"active_color": _C_GREEN, "position": "lower"},
         {"background": "pill_line", "active_color": _C_YELLOW},
         {"font": "Poppins", "active_color": _C_PINK,
          "active_anim": "fade"},
     ]),
    ("social", "Social", "bright playful creator captions",
     {"font": "Poppins", "active_anim": "pop"},
     [
         {"active_anim": "rotate", "active_color": _C_YELLOW},
         {"emoji": "\U0001F525", "active_color": _C_YELLOW,
          "anim_peak": 125},
         {"active_color": _C_PINK, "active_anim": "spring", "anim_y": 18},
         {"active_color": _C_CYAN, "active_anim": "wipe"},
         {"active_color": _C_LIME, "active_anim": "jitter"},
     ]),
    ("gaming", "Gaming", "aggressive glitch energy",
     {"font": "Anton", "active_anim": "jitter", "text_case": "upper"},
     [
         {"active_color": _C_RED, "highlight": "color"},
         {"active_anim": "jitter", "anim_amp": 8, "active_color": _C_YELLOW,
          "font": "Bangers"},
         {"active_anim": "stomp", "anim_peak": 140,
          "active_color": _C_ORANGE},
         {"active_anim": "rotate", "active_color": _C_RED},
         {"active_anim": "glowpulse", "highlight": "glow",
          "active_color": _C_RED, "font": "Bangers"},
     ]),
    ("meme", "Meme", "impact-style top captions",
     {"font": "Anton", "position": "top", "outline": 5,
      "text_case": "upper"},
     [
         {"active_color": _C_WHITE, "highlight": "none",
          "active_anim": "pop"},
         {"active_color": _C_YELLOW},
         {"active_color": _C_CYAN, "active_anim": "stomp"},
         {"font": "Bebas Neue", "active_color": _C_WHITE,
          "highlight": "none"},
         {"position": "bottom", "active_color": _C_YELLOW,
          "active_anim": "pop"},
     ]),
    ("neon", "Neon", "neon tube glow",
     {"highlight": "glow", "active_anim": "glowpulse", "shadow": 0,
      "outline": 2},
     [
         {"active_color": _C_CYAN},
         {"active_color": _C_PINK},
         {"active_color": _C_GREEN, "text_case": "upper"},
         {"active_color": _C_PURPLE, "font": "Poppins"},
         {"active_color": _C_YELLOW, "active_anim": "pop",
          "highlight": "glow"},
     ]),
    ("threed", "3D", "deep extruded shadow",
     {"shadow": 4, "outline": 4, "active_anim": "pop"},
     [
         {"active_color": _C_WHITE, "highlight": "none"},
         {"active_color": _C_YELLOW, "shadow": 3, "active_anim": "spring",
          "anim_y": 16},
         {"active_color": _C_CYAN, "shadow": 4, "active_anim": "stomp"},
         {"active_color": _C_ORANGE, "shadow": 3, "font": "Anton"},
         {"active_color": _C_LIME, "shadow": 4, "position": "lower"},
     ]),
    ("glowing", "Glowing", "soft luminous wash",
     {"highlight": "wash", "active_anim": "fade", "shadow": 0},
     [
         {"active_color": _C_YELLOW},
         {"active_color": _C_CYAN},
         {"active_color": _C_PINK, "active_anim": "rise", "anim_y": 14},
         {"active_color": _C_GREEN, "font": "Poppins"},
         {"active_color": _C_PURPLE, "entry": "fade", "entry_ms": 300},
     ]),
    ("typewriter", "Typewriter", "monospace character reveal",
     {"font": "Courier Prime", "text_case": "none",
      "active_anim": "typewriter", "highlight": "none", "shadow": 0,
      "outline": 1},
     [
         {"active_color": _C_WHITE},
         {"active_color": _C_GREEN},
         {"active_color": _C_YELLOW, "background": "bar",
          "bg_opacity": 0.8},
         {"active_color": _C_CYAN},
         {"active_color": _C_WHITE, "background": "box"},
     ]),
    ("animated", "Animated", "playful mixed motion",
     {},
     [
         {"active_anim": "wipe", "active_color": _C_YELLOW},
         {"active_anim": "rise", "anim_y": 30, "active_color": _C_CYAN},
         {"active_anim": "fall", "anim_y": 40, "active_color": _C_PINK},
         {"active_anim": "rotate", "active_color": _C_GREEN},
         {"active_anim": "slide", "active_color": _C_ORANGE},
     ]),
    ("emoji", "Emoji", "emoji-decorated active words",
     {"active_anim": "pop", "font": "Poppins"},
     [
         {"emoji": "\U0001F525", "active_color": _C_YELLOW},
         {"emoji": "\u2728", "active_color": _C_CYAN,
          "active_anim": "spring", "anim_y": 14},
         {"emoji": "\u26A1", "active_color": _C_WHITE,
          "active_anim": "stomp"},
         {"emoji": "\U0001F4A1", "active_color": _C_PINK,
          "font": "Fredoka"},
         {"emoji": "\U0001F3AF", "active_color": _C_GREEN,
          "active_anim": "rise", "anim_y": 16},
     ]),
    ("advanced", "Advanced", "experimental composites",
     {},
     [
         {"active_anim": "sweep", "active_color": _C_YELLOW},
         {"highlight": "outline", "active_color": _C_YELLOW,
          "active_anim": "pop", "anim_peak": 110},
         {"layout": "two_tone", "active_color": _C_LIME,
          "active_anim": "pop"},
         {"layout": "headline", "active_color": _C_PINK,
          "active_anim": "pop"},
         {"active_anim": "wave", "active_color": _C_YELLOW},
     ]),
]


def _build_caption_presets():
    """Materialize the 120 presets from _CATEGORY_DEFS. Pure data."""
    out = {}
    for slug, _label, _concept, base, variants in _CATEGORY_DEFS:
        for i, over in enumerate(variants, 1):
            pid = "%s_%02d" % (slug, i)
            p = dict(_PRESET_DEFAULTS)
            p.update(base)
            p.update(over)
            p["id"] = pid
            p["label"] = "%s %02d" % (_label, i)
            p["category"] = _label
            p["meta"] = _VR_META
            out[pid] = p
    return out


CAPTION_TEMPLATES = _build_caption_presets()

CAPTION_TEMPLATE_CATEGORIES = [label for _s, label, _c, _b, _v
                               in _CATEGORY_DEFS]

DEFAULT_CAPTION_TEMPLATE = "dynamic_01"

#: Every retired caption-style id migrates to its closest successor.
_MIGRATION_MAP = {
    # 28-style real-tools set (2026-10-02)
    "submagic_hormozi": "hormozi_01", "submagic_beast": "hormozi_02",
    "submagic_karaoke": "karaoke_01", "submagic_neon": "neon_01",
    "submagic_gradient": "advanced_01", "submagic_minimal": "minimal_01",
    "captions_box": "highlight_01", "captions_ali": "minimal_02",
    "captions_pulse": "dynamic_05",
    "opus_karaoke": "karaoke_02", "opus_beasty": "minimal_03",
    "opus_deepdiver": "karaoke_03", "opus_podp": "pop_03",
    "opus_mozi": "advanced_03", "opus_popline": "advanced_04",
    "opus_youshaei": "karaoke_04", "opus_glitch": "gaming_01",
    "opus_bounce": "bounce_01", "opus_quake": "gaming_02",
    "veed_impact": "pop_01", "veed_stomp": "pop_05",
    "veed_reveal": "animated_01", "veed_float": "animated_02",
    "veed_scale": "pop_04", "veed_drop": "animated_03",
    "veed_rotate": "animated_04", "veed_colour": "highlight_03",
    "typewriter": "typewriter_01",
    # 23-style CapCut-clone set (2026-10-02)
    "karaoke_pop": "dynamic_01", "karaoke": "karaoke_01",
    "hormozi": "hormozi_01", "beast": "hormozi_02",
    "bounce": "bounce_01", "spring_up": "bounce_02",
    "wave": "advanced_05", "zoom": "pop_04",
    "neon_glow": "neon_01", "highlighter": "highlight_04",
    "box_highlight": "highlight_01", "pill": "karaoke_02",
    "caption_bar": "news_02", "outline_fill": "advanced_02",
    "underline_sweep": "karaoke_05", "glide": "animated_05",
    "solo_pop": "wordbyword_02", "minimal_fade": "minimal_01",
    "gradient_sweep": "advanced_01", "sticker_pop": "social_01",
    "glitch": "gaming_01", "pulse": "dynamic_05",
    "beast_box": "hormozi_02",
    # 14-style set (2026-10-01) — same ids as the 23-set where they overlap
}

#: Older retired ids (8 spec_* structural styles, 101 legacy templates,
#: preset/motion ids) fall back to the default.
_LEGACY_STYLE_IDS = frozenset({
    "spec_word_sync", "spec_highlight", "spec_impact", "spec_aesthetic",
    "spec_minimal", "spec_dynamic", "spec_standard", "spec_dual",
    "tiktok_classic",
    "preset_classic", "preset_glow", "preset_monoline", "preset_textbox",
    "preset_trending", "motion_default", "motion_bounce", "motion_negrow",
    "motion_flip3d", "motion_typewriter", "motion_karaoke",
})


def migrate_caption_template_id(old_id):
    """Map any saved/legacy caption-style id to a current preset id.

    Retired style ids map to their closest successor (_MIGRATION_MAP).
    Unknown ids fall back to DEFAULT_CAPTION_TEMPLATE.
    """
    if isinstance(old_id, str):
        if old_id in _MIGRATION_MAP:
            return _MIGRATION_MAP[old_id]
        if old_id in CAPTION_TEMPLATES:
            return old_id
    return DEFAULT_CAPTION_TEMPLATE


def caption_template_labels():
    """[(preset_id, label)] for the caption-style pickers."""
    return [(k, CAPTION_TEMPLATES[k]["label"]) for k in CAPTION_TEMPLATES]


def caption_category_labels():
    """[(category_label, preset_count)] for the gallery category browser."""
    counts = {}
    for p in CAPTION_TEMPLATES.values():
        counts[p["category"]] = counts.get(p["category"], 0) + 1
    return [(c, counts.get(c, 0)) for c in CAPTION_TEMPLATE_CATEGORIES]


# ---------------------------------------------------------------------------
# MAIN-TEXT ENGINE — Genspark-validated (2026-10-01).
#
# Spec: ~/workspace/research/main_text_genspark_spec.md (authoritative).
# Seven types (STAT/NAME/DATE/KEYWORD/QUOTE/CHAPTER/OUTCOME), tiered
# selection with a scene-worthiness gate, broadcast styling (Montserrat
# Bold, white default, black box 50-85%), and narration-synced animation
# (appear 100-300ms BEFORE the spoken word, hold = 0.3*wc+0.5 clamped to
# [1.5, 4.0]s, exit fade 150-200ms always shorter than the entrance).
#
# Public API (stable for text_engine.py and the UI):
#   select_main_text(...) -> list of card dicts      (primary entry point)
#   classify_main_text(scene_text, ...) -> (type, text) or (None, "")
#   make_overlay(analysis, max_words=12) -> (text, accent_words)
#   make_overlay_text(analysis, max_words=12) -> text
#   headline_preset_for(scene_text, override="auto") -> preset id
#   headline_accent_for(scene_text, override="auto") -> ASS color
#   headline_preset_labels() -> [(id, label), ...]
#   detect_niche(text) -> niche id
#   story_box_color(scene_text) -> ASS BackColour for the story type
# ---------------------------------------------------------------------------

#: The seven main-text types (§1).
MAIN_TEXT_TYPES = ("STAT", "NAME", "DATE", "KEYWORD", "QUOTE", "CHAPTER", "OUTCOME")

# ASS colours, &HAABBGGRR.
_MT_WHITE = "&H00FFFFFF"   # #FFFFFF — default fill
_MT_YELLOW = "&H0000CCFF"  # #FFCC00 — fallback when white won't separate
_MT_RED = "&H000000FF"     # #FF0000 — crisis/casualty/negative ONLY
_MT_DRED = "&H000000DD"    # #DD0000 — crisis alt
_MT_CYAN = "&H00FFD400"    # #00D4FF — highly technical topics
_MT_GOLD = "&H0000D7FF"    # #FFD700 — chapter alt / positive
_MT_LGREEN = "&H0090EE90"  # light green — positive/record


def _box_back(opacity):
    """ASS BackColour for a black box at the given opacity (0..1)."""
    return "&H%02X000000" % int(round(255 * (1 - max(0.0, min(1.0, opacity)))))


# Per-type style at 1080p (Genspark §3 table). entrances ≤250ms, exits
# strictly shorter than entrances (§4). y_frac is the anchor for \an8/\an9.
MAIN_TEXT_STYLE = {
    "STAT":    dict(zone="upper-center", size=64, sub=44, box_op=0.60, pad_lr=25, pad_tb=15,
                    align=8, y_frac=0.30, enter_ms=180, exit_ms=120,
                    entrance="fade", case="upper"),
    "NAME":    dict(zone="upper-center-left", size=54, sub=36, box_op=0.65, pad_lr=22, pad_tb=12,
                    align=8, y_frac=0.32, enter_ms=200, exit_ms=150,
                    entrance="slide", case="title"),
    "DATE":    dict(zone="top-right", size=50, sub=36, box_op=0.55, pad_lr=16, pad_tb=10,
                    align=9, y_frac=0.20, enter_ms=180, exit_ms=120,
                    entrance="fade", case="upper"),
    "KEYWORD": dict(zone="upper-center", size=50, sub=36, box_op=0.65, pad_lr=20, pad_tb=12,
                    align=5, y_frac=0.40, enter_ms=200, exit_ms=150,
                    entrance="fade", case="upper"),
    "QUOTE":   dict(zone="center", size=56, sub=32, box_op=0.70, pad_lr=28, pad_tb=18,
                    align=5, y_frac=0.45, enter_ms=250, exit_ms=200,
                    entrance="fade", case="verbatim"),
    "CHAPTER": dict(zone="upper-center", size=68, sub=38, box_op=0.78, pad_lr=34, pad_tb=22,
                    align=5, y_frac=0.32, enter_ms=250, exit_ms=200,
                    entrance="slide", case="upper"),
    "OUTCOME": dict(zone="center", size=52, sub=36, box_op=0.65, pad_lr=22, pad_tb=14,
                    align=5, y_frac=0.42, enter_ms=200, exit_ms=150,
                    entrance="fade", case="upper"),
}

# Narration lead per type (Uzair 2026-10-04: "na aaga na peeche" -- the
# main text must appear EXACTLY when its hero word is spoken, neither
# ahead nor behind, so every lead is 0.0).
_MT_LEAD = {"STAT": 0.0, "NAME": 0.0, "DATE": 0.0, "KEYWORD": 0.0,
            "QUOTE": 0.0, "CHAPTER": 0.0, "OUTCOME": 0.0}

# ---------------------------------------------------------------------------
# Negative filters (§5).
# ---------------------------------------------------------------------------

# §5a filler / discourse markers — never heroes, never overlays alone.
_MAIN_FILLERS = frozenset((
    "instead rather however moreover furthermore nevertheless therefore thus hence "
    "also just even still yet very really quite somewhat essentially basically often "
    "usually sometimes always never ever much many more most such like well now today "
    "here there every any some each other another these those this that its their our "
    "your his her my own same new old big small large great good bad high low long "
    "short meanwhile already though although because since while when where which that "
    "both few several various certain in other words"
).split())

# Back-compat alias: the old HEADLINE_FILLERS name still resolves.
HEADLINE_FILLERS = _MAIN_FILLERS

# §5b generic verbs — never heroes.
_GENERIC_VERBS = frozenset((
    "is are was were be been being has have had do does did will would can could "
    "should may might must make made take took get got go went come came say said "
    "says tell told know knew think thought see saw look seemed become feel try "
    "used use want need help"
).split())

# §5c superlatives that need data — never shown isolated.
_SUPERLATIVES = frozenset(
    "amazing shocking historic unprecedented incredible unbelievable insane".split())

# §5c vague timeframes — replaced by concrete ones, never shown.
_VAGUE_TIME = frozenset("soon later eventually someday".split())

# §5c attribution phrases — never shown alone (only paired with a statement).
_ATTRIBUTION_RES = (
    re.compile(r"\b(?:officials?|sources?|reports?|experts?|analysts?)\s+say\b", re.I),
    re.compile(r"\baccording to (?:reports|sources|officials)\b", re.I),
)

# §5c standalone pronouns — never shown alone.
_PRONOUNS = frozenset(
    "it he she they him her them we you i me us".split())

# ---------------------------------------------------------------------------
# Importance scoring (2026-10-02 rebuild): the AI selects ONLY what is
# important from the narration script — never random words or filler lines.
# Priority: named entities > numbers-with-magnitude > outcome/decision words
# > attributed quotes > dates. Candidates below _IMPORTANCE_FLOOR are
# dropped: a weak pick emits NOTHING rather than a random-looking overlay.
# ---------------------------------------------------------------------------

#: Minimum importance for a candidate to become a card.
_IMPORTANCE_FLOOR = 45.0

# Common capitalised words that are NOT entities (news filler / labels).
_NAME_STOPLIST = frozenset((
    "breaking live today tonight exclusive watch update updates developing "
    "report reports source sources official officials video photo photos "
    "image images market economy government police company study studies "
    "footage clip alert just in new".split()))

# Quote fallback: "X said/announced/admitted that <clause>" — the clause is
# a memorable statement even without quote marks in the script.
_QUOTE_SAID_RE = re.compile(
    r"\b(said|stated|announced|admitted|declared|warned|promised|"
    r"confirmed|revealed)\b\s+(?:that\s+)?([^.,;!?]{12,90}?)"
    r"(?=[.,;!?]|$)", re.I)
_QUOTE_STRONG_VERBS = frozenset(
    ("admitted", "announced", "declared", "warned"))

# Words that license the RED accent: crisis / casualty / negative (§3).
_CRISIS_WORDS = frozenset((
    "dead death deaths died kill killed killing casualty casualties collapse collapsed "
    "collapsing crash crashed crashing decline declined loss losses lost disaster "
    "emergency attack attacked war recession bankrupt bankruptcy layoffs layoff fired "
    "outbreak victims victim fatal destroyed destruction crisis threat danger warning "
    "plunge plunged plunges soar nosedive tumble".split()))

# Strong emotional verbs that may join a STAT card as the red hero (§7 Ex 2).
_EMOTION_WORDS = _CRISIS_WORDS | frozenset(
    "surge surges soaring skyrocket skyrockets warns".split())

# Technical-topic hints: KEYWORD cards get the cyan accent on these.
_TECH_HINTS = frozenset((
    "ai quantum neural fiscal deficit gdp gdpr crypto blockchain semiconductor chip "
    "algorithm genome vaccine mrna inflation recession tariff quantum entanglement "
    "nuclear fusion reactor cpr".split()))

# Words stripped from overlay edges during compression (never content).
_STOP_EDGE = frozenset(
    "a an the to for of on at by with from as in into over under and or but".split())

# Weak generic words — dropped from the END so the punch lands last.
_WEAK_ENDINGS = frozenset(
    "region area zone people thing things stuff part side way kind sort".split())


def _is_filler(w):
    """True for filler/discourse words that must never anchor main text."""
    return (w or "").strip(".,!?\"'").lower() in _MAIN_FILLERS


def _content_words(text):
    """Words that carry meaning: not filler, verbs, pronouns or stopwords."""
    out = []
    for w in re.findall(r"[A-Za-z']+", text or ""):
        wl = w.lower()
        if (wl not in _MAIN_FILLERS and wl not in _GENERIC_VERBS
                and wl not in _PRONOUNS and wl not in _STOP_EDGE
                and len(w) > 2):
            out.append(w)
    return out


def _norm_text(t):
    return re.sub(r"\s+", " ", (t or "").strip(" .,!?\"'").lower())


# ---------------------------------------------------------------------------
# Candidate extraction — one candidate per Tier-1 entity (§2 Step 1).
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(
    r"\$?\d[\d,]*\.?\d*"
    r"(?:\s*(?:trillion|billion|million|thousand|percent|%|tons?|tonnes?|feet|ft|"
    r"miles?|km|°F|°C|degrees?))?"
    r"(?=\s|$|[.,;:!?\"'])", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_MONTHS = ("January|February|March|April|May|June|July|August|September|October|"
           "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec")
_DATE_RES = (
    re.compile(r"\b(?:%s)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?\b" % _MONTHS, re.I),
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
    re.compile(r"\bfor the past \d+\s+(?:months?|years?|days?|weeks?)\b", re.I),
    re.compile(r"\bin \d+\s+(?:hours?|days?|weeks?|months?|years?)\b", re.I),
    re.compile(r"\bsince the \d{4}s\b", re.I),
)
_DEADLINE_RE = re.compile(
    r"\b(?:deadline|scheduled|set for|due|expires?|launch(?:es|ed)?)\b", re.I)
_NAME_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2})\b")
_ACRONYM_RE = re.compile(r"\b([A-Z]{2,6})\b")
_ROLE_RE = re.compile(
    r"\b(?:the|a|an)\s+(CEO|witness|doctor|officer|official|expert|analyst|"
    r"spokesperson|founder|president|minister|chairman|governor|mayor)\b", re.I)
_QUOTE_RES = (
    re.compile(r"[\"“”]([^\"“”]{10,90})[\"“”]"),
    re.compile(r"\bquote,?\s+(.+?)\s*,?\s+end quote\b", re.I),
)
_CHAPTER_RE = re.compile(
    r"\b(?:number|chapter|step|part)\s+(\d+|one|two|three|four|five|six|seven|"
    r"eight|nine|ten)\b", re.I)
_OUTCOME_RES = (
    re.compile(r"\b(?:could|would|may|might|will)\s+"
               r"(?:affect|trigger|cause|lead to|mean|change|reshape|threaten|put)"
               r"[^.,;!?]{0,70}", re.I),
    re.compile(r"\b(?:first|biggest|largest|worst|deadliest|highest|lowest)\s+"
               r"(?:such\s+)?[^.,;!?]{1,50}?\s+in\s+\d+\s+years\b", re.I),
)
_INCIDENTAL_RE = re.compile(
    r"\b(?:in|for|within|just|about|around)\s+\$?\d[\d,]*\.?\d*\s*"
    r"(?:seconds?|minutes?|hours?|days?|weeks?)\b", re.I)
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                 "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
                 "fifteen": 15, "sixteen": 16, "seventeen": 17,
                 "eighteen": 18, "nineteen": 19, "twenty": 20}


def _magnitude(num_text):
    """Rough magnitude for the magnitude tie-break (bigger number wins)."""
    t = (num_text or "").lower().replace(",", "").replace("$", "")
    m = re.search(r"\d+\.?\d*", t)
    v = float(m.group(0)) if m else 0.0
    for unit, mult in (("trillion", 1e12), ("billion", 1e9), ("million", 1e6),
                       ("thousand", 1e3)):
        if unit in t:
            v *= mult
            break
    return v


def _extract_candidates(text, keywords=(), phrases=()):
    """Extract Tier-1 entity candidates from scene text.

    Returns a list of dicts: raw, type, tier, sub (in-tier rank), start,
    hero, magnitude, incidental, deadline. Overlapping spans are claimed
    by the earliest extractor (dates/stats/quotes first).
    """
    cands = []
    used = []  # (start, end) spans already claimed

    def _claim(s, e):
        for a, b in used:
            if s < b and e > a:
                return False
        used.append((s, e))
        return True

    # -- QUOTE (explicit markers = auto-qualify, Tier 1) --
    for rx in _QUOTE_RES:
        for m in rx.finditer(text):
            q = " ".join(m.group(1).split())
            words = q.split()
            if 2 <= len(words) <= 15 and _claim(m.start(), m.end()):
                cands.append({"raw": q, "type": "QUOTE", "tier": 1, "sub": 1,
                              "start": m.start(), "hero": "",
                              "magnitude": 0.0, "incidental": False,
                              "deadline": False})

    # -- QUOTE fallback: "X said/announced/admitted that <clause>" --
    # Narration scripts rarely carry quote marks; the clause after a
    # speech verb is still a memorable verbatim statement.
    for m in _QUOTE_SAID_RE.finditer(text):
        clause = " ".join(m.group(2).split()).strip(" .,!?\"'")
        words = clause.split()
        if 3 <= len(words) <= 12 and _claim(m.start(2), m.end(2)):
            strong = m.group(1).lower() in _QUOTE_STRONG_VERBS
            cands.append({"raw": clause, "type": "QUOTE", "tier": 1,
                          "sub": 0 if strong else 1,
                          "start": m.start(2), "hero": "",
                          "magnitude": 0.0, "incidental": False,
                          "deadline": False})

    # -- DATE (Tier 2; deadline/event dates get the news-priority override) --
    for rx in _DATE_RES:
        for m in rx.finditer(text):
            d = " ".join(m.group(0).split())
            if _claim(m.start(), m.end()):
                window = text[max(0, m.start() - 40):m.end() + 20]
                is_deadline = bool(_DEADLINE_RE.search(window)) \
                    or d.lower().startswith("by ")
                cands.append({"raw": d, "type": "DATE",
                              "tier": 1 if is_deadline else 2, "sub": 0,
                              "start": m.start(), "hero": "",
                              "magnitude": 0.0, "incidental": False,
                              "deadline": is_deadline})

    # -- STAT (number + unit, Tier 1) --
    for m in _NUM_RE.finditer(text):
        raw = " ".join(m.group(0).split())
        if not re.search(r"\d", raw):
            continue
        if _YEAR_RE.fullmatch(raw):
            # Bare 4-digit years are DATE-flavoured stats ("since 2019").
            if _claim(m.start(), m.end()):
                cands.append({"raw": raw, "type": "STAT", "tier": 1,
                              "sub": 3, "start": m.start(), "hero": raw,
                              "magnitude": _magnitude(raw),
                              "incidental": False, "deadline": False})
            continue
        has_unit = bool(re.search(
            r"billion|million|thousand|percent|%|tons?|tonnes?|feet|\bft\b|"
            r"miles?|\bkm\b|°F|°C|degrees?|\$", raw, re.I))
        if _claim(m.start(), m.end()):
            window = text[max(0, m.start() - 12):m.end() + 4]
            incidental = bool(_INCIDENTAL_RE.search(window))
            cands.append({"raw": raw, "type": "STAT",
                          "tier": 3 if incidental else 1,
                          "sub": 0 if has_unit else 3,
                          "start": m.start(), "hero": raw,
                          "magnitude": _magnitude(raw),
                          "incidental": incidental, "deadline": False})
    # number-words ("three miles" -> "3 MILES")
    for w, v in _NUMBER_WORDS.items():
        for m in re.finditer(r"\b%s\s+(miles?|km|feet|\bft\b|tons?|percent|%%)\b"
                             % w, text, re.I):
            unit = m.group(1).upper()
            raw = "%d %s" % (v, unit)
            if _claim(m.start(), m.end()):
                cands.append({"raw": raw, "type": "STAT", "tier": 1,
                              "sub": 0, "start": m.start(), "hero": raw,
                              "magnitude": float(v),
                              "incidental": False, "deadline": False})

    # -- NAME (proper nouns, Tier 1; role IDs, Tier 3) --
    for m in _NAME_RE.finditer(text):
        name = " ".join(m.group(1).split())
        if (name.split()[0].lower() in _MAIN_FILLERS
                or name.lower() in _STOP_EDGE
                or any(w in _NAME_STOPLIST for w in name.lower().split())
                or re.fullmatch(_MONTHS, name.split()[0], re.I)):
            continue
        if m.start() == 0 and " " not in name:
            continue  # sentence-initial single capitalised word: not an entity
        if _claim(m.start(), m.end()):
            parts = name.split()
            hero = parts[-1]  # surname / last token
            cands.append({"raw": name, "type": "NAME", "tier": 1, "sub": 2,
                          "start": m.start(), "hero": hero,
                          "magnitude": 0.0, "incidental": False,
                          "deadline": False})
    for m in _ROLE_RE.finditer(text):
        role = m.group(1).capitalize()
        if _claim(m.start(), m.end()):
            cands.append({"raw": role, "type": "NAME", "tier": 3, "sub": 1,
                          "start": m.start(), "hero": role,
                          "magnitude": 0.0, "incidental": False,
                          "deadline": False})

    # -- KEYWORD (technical/unfamiliar terms ONLY if in the voiceover) --
    techy = set()
    low = text.lower()
    for kw in list(keywords) + list(phrases):
        kl = (kw or "").lower().strip()
        if not kl or _is_filler(kl) or kl in _GENERIC_VERBS:
            continue
        if (re.fullmatch(r"[A-Z]{2,6}", kw or "")
                or any(h in kl for h in _TECH_HINTS)
                or kl in _TECH_HINTS):
            techy.add(kw)
    for m in _ACRONYM_RE.finditer(text):
        ac = m.group(1)
        if ac.lower() not in _MAIN_FILLERS and _claim(m.start(), m.end()):
            techy.add(ac)
    for kw in sorted(techy, key=lambda k: text.lower().find(k.lower())):
        idx = text.lower().find(kw.lower())
        cands.append({"raw": kw, "type": "KEYWORD", "tier": 2, "sub": 1,
                      "start": idx if idx >= 0 else 0, "hero": kw,
                      "magnitude": 0.0, "incidental": False,
                      "deadline": False})

    # -- CHAPTER (list-item openers, Tier 3) --
    for m in _CHAPTER_RE.finditer(text):
        n = m.group(1)
        num = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
               "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}.get(
                   n.lower(), n)
        # Label from the words AFTER the marker ("number three: strengthen
        # the border" -> "STRENGTHEN THE BORDER"), not from the start of
        # the scene — the old first-two-words heuristic was arbitrary.
        tail = text[m.end():m.end() + 80]
        if ":" in tail[:30]:
            tail = tail.split(":", 1)[1]
        label_words = [w for w in _content_words(tail)[:4]]
        label = " ".join(label_words).upper() or "#%s" % num
        if _claim(m.start(), m.end()):
            cands.append({"raw": label.strip(), "type": "CHAPTER", "tier": 3,
                          "sub": 0, "start": m.start(), "hero": "#%s" % num,
                          "magnitude": 0.0, "incidental": False,
                          "deadline": False})

    # -- OUTCOME (the "so what", Tier 2) --
    for rx in _OUTCOME_RES:
        for m in rx.finditer(text):
            oc = " ".join(m.group(0).split())
            if len(oc.split()) >= 3 and _claim(m.start(), m.end()):
                cands.append({"raw": oc, "type": "OUTCOME", "tier": 2,
                              "sub": 2, "start": m.start(), "hero": "",
                              "magnitude": 0.0, "incidental": False,
                              "deadline": False})

    # Secondary stats drop to Tier 3 (only the defining stat stays Tier 1).
    seen_stat = False
    for c in sorted(cands, key=lambda c: (c["tier"], c["sub"],
                                         -c["magnitude"], c["start"])):
        if c["type"] == "STAT" and c["tier"] == 1:
            if seen_stat:
                c["tier"] = 3
                c["sub"] = 2
            seen_stat = True
    return cands


# ---------------------------------------------------------------------------
# Compression (§2 Step 2) and case rules (§2 Step 3).
# ---------------------------------------------------------------------------

def _compress_words(words, max_words):
    """Strip edge stopwords/fillers/weak verbs, drop weak endings, hard-cap."""
    i, j = 0, len(words)
    while i < j and (words[i].lower() in _STOP_EDGE
                     or _is_filler(words[i])
                     or words[i].lower() in _GENERIC_VERBS):
        i += 1
    while j > i and (words[j - 1].lower() in _STOP_EDGE
                     or _is_filler(words[j - 1])
                     or words[j - 1].lower() in _WEAK_ENDINGS):
        j -= 1
    words = words[i:j]
    while len(words) > max_words and words[-1].lower() in (
            _WEAK_ENDINGS | _STOP_EDGE):
        words.pop()
    return words[:max_words]


def _apply_case(text, case):
    if case == "upper":
        return text.upper()
    if case == "title":
        out = []
        for w in text.split():
            if re.fullmatch(r"[A-Z]{2,6}", w):
                out.append(w)  # keep acronyms as-is
            else:
                out.append(w[:1].upper() + w[1:].lower())
        return " ".join(out)
    return text  # verbatim


def _crisis_in(text):
    toks = set(re.findall(r"[a-z]+", (text or "").lower()))
    return bool(toks & _CRISIS_WORDS)


def _adopt_emotion_word(scene_text, start, words, max_words, case):
    """For a STAT card, adopt the first emotional word spoken near the stat
    (§7 Ex 2: "$68 BILLION COLLAPSED"). Returns the cased word or None."""
    disp = {w.strip(".,!?\"'").lower() for w in words}
    window = scene_text[start:start + 90]
    for w in re.findall(r"[A-Za-z']+", window):
        wl = w.lower()
        if wl in disp or _is_filler(w) or wl in _STOP_EDGE:
            continue
        if wl in _EMOTION_WORDS and len(words) < max_words:
            return _apply_case(w, case)
    return None


def _card_accent(card_text, ctype, scene_text):
    """Accent colour + hero words for a finished card.

    Back-compat wrapper — the rules live in MAIN_TEXT_COLORS /
    main_text_color_for() now (explicit per-type map, 2026-10-02).
    """
    return main_text_color_for(ctype, card_text, scene_text)


def _importance(c, text):
    """News-importance score for one candidate (2026-10-02 rebuild).

    named entities > numbers-with-magnitude > outcome/decision words >
    attributed quotes > dates. Higher = more newsworthy. Candidates below
    _IMPORTANCE_FLOOR are dropped entirely.
    """
    import math
    tier_base = {1: 100.0, 2: 60.0, 3: 30.0}
    s = tier_base.get(c.get("tier", 3), 30.0)
    s += (3 - c.get("sub", 3)) * 5.0          # in-tier rank
    words = c["raw"].split()
    if len(words) > 1:
        s += min(20.0, 10.0 * (len(words) - 1))  # multi-word = specific
    elif c["type"] == "NAME":
        s -= 25.0                             # single-token names are weak
    mag = c.get("magnitude", 0.0) or 0.0
    if mag > 0:
        s += min(25.0, 5.0 * math.log10(1 + mag / 1e6))  # magnitude
    if c.get("boost"):
        s += 25.0                             # spoken >= 3x: auto-wins tier
    if c.get("deadline"):
        s += 20.0                             # news-priority override
    low = c["raw"].lower()
    if any(w in low for w in ("record", "first", "biggest", "largest",
                              "worst", "historic")):
        s += 10.0                             # newsworthy superlative + data
    start = c.get("start", 0) or 0
    s += max(0.0, 10.0 - start / max(1, len(text)) * 10.0)  # said early wins
    # Bare 4-digit years are DATE-flavoured and carry little news value
    # on their own ("In 2024, ..." should not beat a real name/stat).
    # Strip trailing punctuation: the extractor keeps "2024,".
    if (c["type"] == "STAT"
            and re.fullmatch(r"(?:19|20)\d{2}",
                             c["raw"].strip(".,!?\"'"))):
        s -= 60.0
    return s


# ---------------------------------------------------------------------------
# Explicit COLOR map per main-text type (2026-10-02).
# White default; RED only for crisis/casualty/negative; CYAN for highly
# technical KEYWORD cards. Accent lands on 1-3 hero words max — never
# every word (the all-red bug).
# ---------------------------------------------------------------------------
MAIN_TEXT_COLORS = {
    "STAT":    {"fill": _MT_WHITE, "accent_alt": _MT_YELLOW,
                "crisis": _MT_RED,
                "rule": "white; yellow only when white won't separate; "
                        "RED only crisis/casualty/negative"},
    "NAME":    {"fill": _MT_WHITE, "accent_alt": _MT_YELLOW,
                "crisis": _MT_WHITE, "rule": "white always"},
    "DATE":    {"fill": _MT_WHITE, "accent_alt": _MT_YELLOW,
                "crisis": _MT_WHITE, "rule": "white always"},
    "KEYWORD": {"fill": _MT_WHITE, "accent_alt": _MT_CYAN,
                "crisis": _MT_WHITE,
                "rule": "white; cyan for highly technical topics"},
    "QUOTE":   {"fill": _MT_WHITE, "accent_alt": _MT_YELLOW,
                "crisis": _MT_WHITE, "rule": "white, verbatim"},
    "CHAPTER": {"fill": _MT_WHITE, "accent_alt": _MT_GOLD,
                "crisis": _MT_RED,
                "rule": "white; gold alt; red only crisis chapters"},
    "OUTCOME": {"fill": _MT_WHITE, "accent_alt": _MT_YELLOW,
                "crisis": _MT_RED,
                "rule": "white; RED for negative outcomes"},
}


def main_text_color_for(ctype, card_text, scene_text):
    """(accent_color, accent_words) for a finished card — the explicit
    per-type color map. Returns (_MT_WHITE, []) for neutral cards."""
    spec = MAIN_TEXT_COLORS.get(ctype, MAIN_TEXT_COLORS["STAT"])
    crisis = _crisis_in(card_text) or _crisis_in(scene_text)
    if crisis and spec["crisis"] != _MT_WHITE:
        heroes = [w for w in card_text.split()
                  if w.strip(".,!?\"'").lower() in _EMOTION_WORDS][:3]
        if not heroes:
            heroes = card_text.split()[:1]
        return spec["crisis"], heroes
    if ctype == "KEYWORD":
        return _MT_CYAN, card_text.split()[:2]
    return _MT_WHITE, []


# ---------------------------------------------------------------------------
# Explicit POSITION map per main-text type (2026-10-02).
# Zones are named; the numeric anchors stay in MAIN_TEXT_STYLE.
# Safe zones (spec §3): title-safe inner 80% (X 192-1728, Y 108-972 at
# 1080p); the bottom 250px are OFF-LIMITS (YouTube UI + captions).
# ---------------------------------------------------------------------------
MAIN_TEXT_POSITION = {
    "STAT":    {"zone": "upper-center", "x": "center 960", "y": "200-500"},
    "NAME":    {"zone": "upper-center-left", "x": "400-960", "y": "250-450",
                "note": "avoid the face"},
    "DATE":    {"zone": "top-right", "x": "1100-1700", "y": "150-300",
                "note": "center if chapter marker"},
    "KEYWORD": {"zone": "upper-center", "x": "600-960", "y": "300-500"},
    "QUOTE":   {"zone": "center", "x": "400-1100", "y": "380-600"},
    "CHAPTER": {"zone": "upper-center", "x": "400-1100", "y": "250-400"},
    "OUTCOME": {"zone": "center", "x": "400-1100", "y": "380-600"},
}

#: Safe zones at 1080p (spec §3).
MAIN_TEXT_SAFE_ZONE = {"x_min": 192, "x_max": 1728, "y_min": 108,
                       "y_max": 972, "bottom_reserved_px": 250}


def main_text_position_for(ctype):
    """Named screen zone for a main-text type."""
    return MAIN_TEXT_POSITION.get(ctype, MAIN_TEXT_POSITION["STAT"])


# ---------------------------------------------------------------------------
# Selection pipeline (§2): gate -> tiers -> density -> compression -> cards.
# ---------------------------------------------------------------------------

def _max_cards(duration):
    if duration <= 6:
        return 1
    if duration <= 12:
        return 2
    return 3


# Narrative display order for multi-card scenes: identity -> impact -> meaning.
_NARRATIVE_ORDER = {"NAME": 0, "DATE": 1, "STAT": 2, "KEYWORD": 3,
                    "OUTCOME": 4, "QUOTE": 5, "CHAPTER": 6}


def _repetition_count(candidate_text, scene_text):
    pat = r"\b" + re.escape(_norm_text(candidate_text)) + r"\b"
    return len(re.findall(pat, _norm_text(scene_text)))


def _hero_word_time(hero, word_timings):
    """Start time of the hero word in scene-relative word timings."""
    if not word_timings or not hero:
        return None
    hl = hero.strip(".,!?\"'").lower()
    for w in word_timings:
        if (w.get("word", "").strip(".,!?\"'").lower() == hl
                and w.get("start") is not None):
            return float(w["start"])
    # fallback: first content word of the card
    return None


def select_main_text(scene_text, duration=6.0, word_timings=None,
                     recent_texts=(), keywords=(), phrases=(),
                     context=None, max_words=12):
    """Genspark-validated main-text selection for one scene.

    scene_text: 1-3 sentences of narration.
    duration: scene length in seconds (drives card density).
    word_timings: [{word, start, end}] with start/end RELATIVE TO THE
        SCENE START (or None) — used to appear 100-300ms before the hero.
    recent_texts: overlay texts shown in the last ~3s (dedupe).
    keywords/phrases: from analyze_sentence (KEYWORD extraction).
    context: caller-kept dict across scenes for pop-in budget and the
        60-80 words/minute ceiling. Keys used: pops, pop_t, words_used,
        seconds_used.
    Returns a list of card dicts: text, accent_words, accent (ASS color),
    type, tier, appear (s, scene-relative), hold (s), enter_ms, exit_ms,
    entrance, hero_word.
    """
    text = (scene_text or "").strip()
    if not text:
        return []
    if context is None:
        context = {}
    duration = max(1.0, float(duration or 6.0))

    cands = _extract_candidates(text, keywords=keywords, phrases=phrases)
    # Step 0 — scene-worthiness gate: no Tier-1 entity -> NOTHING.
    if not cands:
        return []

    # Repetition boost: a Tier-1 entity spoken >=3x auto-wins its tier.
    for c in cands:
        c["boost"] = (c["tier"] == 1
                      and _repetition_count(c["raw"], text) >= 3)
        c["importance"] = _importance(c, text)

    # Importance order (2026-10-02 rebuild): named entities >
    # numbers-with-magnitude > outcome/decision words > attributed quotes >
    # dates. Candidates below the floor are dropped — a weak pick emits
    # NOTHING rather than a random-looking overlay.
    cands = [c for c in cands if c["importance"] >= _IMPORTANCE_FLOOR]
    cands.sort(key=lambda c: (-c["importance"], c["start"]))

    # Density: how many cards fit this scene.
    want = _max_cards(duration)
    recent = {_norm_text(r) for r in recent_texts if r}
    picked = []
    for c in cands:
        if len(picked) >= want:
            break
        if _norm_text(c["raw"]) in recent:
            continue  # dedupe: never repeat a recent overlay
        picked.append(c)
    if not picked:
        return []

    # 60-80 overlay words/minute ceiling.
    words_used = float(context.get("words_used", 0.0))
    seconds_used = float(context.get("seconds_used", 0.0))
    if seconds_used >= 10:
        projected = (words_used + sum(len(p["raw"].split())
                                      for p in picked)) / (seconds_used / 60.0)
        if projected > 80:
            return []

    # Narrative display order for multi-card scenes.
    picked.sort(key=lambda c: (_NARRATIVE_ORDER.get(c["type"], 9),
                               c["start"]))

    cards = []
    prev_end = 0.0
    for idx, c in enumerate(picked):
        ts = MAIN_TEXT_STYLE[c["type"]]
        if c["type"] == "QUOTE":
            # Quotes are verbatim — compress only by the hard cap.
            words = c["raw"].split()[:max_words]
        else:
            words = _compress_words(c["raw"].split(), max_words)
        if not words:
            continue
        # Structural ban: overlay must not be ONLY filler/stopwords.
        if all(_is_filler(w) or w.lower() in _STOP_EDGE for w in words):
            continue
        display = _apply_case(" ".join(words), ts["case"])

        # A STAT card adopts the emotional hero word spoken near it
        # ("$68 BILLION" + red "COLLAPSED" — §7 Ex 2).
        accent_color, accent_words = _card_accent(display, c["type"], text)
        if c["type"] == "STAT":
            adopted = _adopt_emotion_word(text, c["start"], words,
                                          max_words, ts["case"])
            if adopted and all(adopted.lower() != w.lower() for w in words):
                words.append(adopted)
                display = _apply_case(" ".join(words), ts["case"])
                accent_color, accent_words = _MT_RED, [adopted]

        wc = len(display.split())
        hold = min(4.0, max(1.5, 0.3 * wc + 0.5))

        # Appearance: EXACTLY at the spoken hero word (Uzair 2026-10-04:
        # "na aaga na peeche"). On scene cuts (no timings) align with the
        # cut plus a small offset.
        hero = c["hero"] or (words[0] if words else "")
        hero_t = _hero_word_time(hero, word_timings)
        if hero_t is None and words:
            hero_t = _hero_word_time(words[0], word_timings)
        lead = _MT_LEAD.get(c["type"], 0.0)
        appear = max(0.0, hero_t - lead) if hero_t is not None else 0.15

        # Sequencing: 2nd/3rd cards start 0.5-1s after the previous card
        # began, with a 100-200ms text-to-text overlap.
        if idx > 0:
            appear = max(appear, prev_end - 0.15, cards[0]["appear"] + 0.5)
        # Never overflow the scene; shrink the hold before dropping.
        if appear + hold > duration - 0.1:
            hold = max(1.0, duration - 0.1 - appear)
            if appear + 1.0 > duration - 0.1 and idx > 0:
                continue  # doesn't fit: drop the extra card
        prev_end = appear + hold

        enter_ms = ts["enter_ms"]
        exit_ms = ts["exit_ms"]
        entrance = ts["entrance"]
        # Pop-in is reserved for high-impact stats / breaking news, max
        # 1-2 per 30s of video (spec HARD RULE).
        if c["type"] == "STAT" and (_crisis_in(display) or _crisis_in(text)):
            pops = int(context.get("pops", 0))
            video_t = float(context.get("video_t", 0.0))
            last_pop = float(context.get("last_pop_t", -100.0))
            if pops < 2 or video_t - last_pop >= 30:
                entrance, enter_ms, exit_ms = "pop", 150, 100
                context["pops"] = pops + 1
                context["last_pop_t"] = video_t

        # Accent words must exist in the display text (case-insensitive).
        disp_low = {w.strip(".,!?\"'").lower() for w in display.split()}
        accent_words = [w for w in accent_words
                        if w.strip(".,!?\"'").lower() in disp_low][:3]

        cards.append({
            "text": display,
            "accent_words": accent_words,
            "accent": accent_color,
            "type": c["type"],
            "tier": c["tier"],
            "appear": round(appear, 3),
            "hold": round(hold, 3),
            "enter_ms": enter_ms,
            "exit_ms": exit_ms,
            "entrance": entrance,
            "hero_word": hero,
        })
        recent.add(_norm_text(display))

    # Book-keeping for the words/minute ceiling and pop budget.
    context["words_used"] = words_used + sum(len(cd["text"].split())
                                             for cd in cards)
    context["seconds_used"] = seconds_used + duration
    context["video_t"] = float(context.get("video_t", 0.0)) + duration
    return cards


def classify_main_text(scene_text, keywords=(), phrases=()):
    """Type + text of the single winning candidate (or (None, ""))."""
    cards = select_main_text(scene_text, duration=6.0, keywords=keywords,
                             phrases=phrases)
    if not cards:
        return None, ""
    return cards[0]["type"], cards[0]["text"]


def make_overlay(analysis, max_words=12):
    """Select on-screen headline text AND the hero words for accent color.

    Genspark-validated: seven types, tiered priority, filler/negative
    filters, white default with red-only-for-crisis accents.
    Returns (display_text, accent_words). ("", []) when the scene is not
    text-worthy — silence is correct.
    """
    text = (getattr(analysis, "text", "") or "").strip()
    if not text:
        return "", []
    cards = select_main_text(
        text, duration=6.0,
        keywords=getattr(analysis, "keywords", None) or (),
        phrases=getattr(analysis, "phrases", None) or (),
        max_words=max_words)
    if not cards:
        return "", []
    return cards[0]["text"], cards[0]["accent_words"]


def make_overlay_text(analysis, max_words=12):
    """Short punchy overlay, never the whole sentence.

    Kept for backward compatibility; new code should use select_main_text()
    (or make_overlay() for the accent words).
    """
    return make_overlay(analysis, max_words)[0]


# ---------------------------------------------------------------------------
# House templates + story-type colour coding (§3, §6: one template per video).
# ---------------------------------------------------------------------------

HEADLINE_PRESETS = {
    "broadcast": {
        "label": "Broadcast News",
        "desc": "Genspark standard: Montserrat Bold, white, black box, fast fade",
        "font": "Montserrat", "primary": _MT_WHITE, "accent": _MT_WHITE,
        "box": True, "pill": False, "outline": 1, "shadow": 1,
        "entrance": "fade", "size_mult": 1.0,
    },
    "viral": {
        "label": "Viral Creator",
        "desc": "Research style: huge Anton caps, thick black outline, no box, pop-in",
        "font": "Anton", "primary": _MT_WHITE, "accent": "&H0000EAFF",
        "box": False, "pill": False, "outline": 4, "shadow": 2,
        "entrance": "pop", "size_mult": 2.0,
    },
    "breaking": {
        "label": "Breaking Bar",
        "desc": "Red breaking-news box, bold white, fast fade",
        "font": "Montserrat", "primary": _MT_WHITE, "accent": _MT_WHITE,
        "box": True, "pill": False, "outline": 1, "shadow": 1,
        "entrance": "fade", "size_mult": 1.0,
    },
    # Legacy aliases for settings saved by older builds.
    "creator": {"alias_of": "broadcast"},
    "archivo": {"alias_of": "viral"},
    "oswald": {"alias_of": "viral"},
    "pill": {"alias_of": "broadcast"},
    "minimal": {"alias_of": "broadcast"},
}

DEFAULT_HEADLINE_PRESET = "broadcast"


def _resolve_preset(pid):
    """Follow legacy aliases; unknown ids fall back to the default."""
    seen = set()
    while isinstance(pid, str) and pid not in seen:
        seen.add(pid)
        hp = HEADLINE_PRESETS.get(pid)
        if not hp:
            return DEFAULT_HEADLINE_PRESET
        nxt = hp.get("alias_of")
        if not nxt:
            return pid
        pid = nxt
    return DEFAULT_HEADLINE_PRESET


# Scene text that forces the breaking style (auto mode).
BREAKING_KEYWORDS = ("breaking", "urgent", "alert", "emergency",
                     "just in", "developing", "explosion", "attack")


def headline_preset_for(scene_text, override="auto"):
    """Pick a headline preset id: explicit override, else breaking when the
    scene text carries urgency, else the Genspark broadcast house template
    (one house template per video — §6)."""
    if override and override != "auto":
        return _resolve_preset(override)
    low = (scene_text or "").lower()
    if any(k in low for k in BREAKING_KEYWORDS):
        return "breaking"
    return DEFAULT_HEADLINE_PRESET


def headline_accent_for(scene_text, override="auto"):
    """House accent colour (ASS &HAABBGGRR). Per-card accents come from
    select_main_text(); this is the template-level default."""
    pid = headline_preset_for(scene_text, override)
    return HEADLINE_PRESETS[pid].get("accent", _MT_WHITE)




def headline_preset_labels():
    """Ordered (key, label) pairs for the main-text style dropdown."""
    return [("auto", "Auto (Genspark broadcast standard)"),
            ("broadcast", HEADLINE_PRESETS["broadcast"]["label"]),
            ("viral", HEADLINE_PRESETS["viral"]["label"]),
            ("breaking", HEADLINE_PRESETS["breaking"]["label"])]


# ---------------------------------------------------------------------------
# Story-type colour coding (§3): box colour follows the story, not the type.
# ---------------------------------------------------------------------------

_STORY_KEYWORDS = {
    "breaking": ("breaking", "urgent", "alert", "emergency", "just in",
                 "developing", "explosion"),
    "negative": ("crash", "crashed", "recession", "layoff", "bankrupt",
                 "market crash", "job losses", "plunge", "tumble", "collapse",
                 "killed", "dead", "deaths"),
    "technical": ("ai", "quantum", "neural", "algorithm", "semiconductor",
                  "genome", "fusion", "breakthrough", "software", "robot"),
    "weather": ("hurricane", "storm", "flood", "tornado", "earthquake",
                "heatwave", "blizzard", "wildfire", "drought"),
    "positive": ("record", "breakthrough", "milestone", "win", "won",
                 "success", "recovered", "first"),
}


def story_box_color(scene_text):
    """ASS BackColour for the headline box, by story type (§3).

    Breaking -> red #8B0000 @70%; economic/negative -> dark red #CC0000 @70%;
    technical -> black @70%; weather -> dark gray #444444 @65%;
    positive/record -> black @60%; standard news -> black @60% (default).
    """
    low = (scene_text or "").lower()
    for kw in _STORY_KEYWORDS["breaking"]:
        if kw in low:
            return "&H4C00008B"
    for kw in _STORY_KEYWORDS["negative"]:
        if kw in low:
            return "&H4C0000CC"
    for kw in _STORY_KEYWORDS["technical"]:
        if re.search(r"\b" + re.escape(kw) + r"\b", low):
            return "&H4C000000"
    for kw in _STORY_KEYWORDS["weather"]:
        if kw in low:
            return "&H59444444"
    for kw in _STORY_KEYWORDS["positive"]:
        if re.search(r"\b" + re.escape(kw) + r"\b", low):
            return "&H66000000"
    return "&H66000000"  # standard news: black @60%


# ---------------------------------------------------------------------------
# Niche detection (kept for API compatibility; story-type colour coding in
# story_box_color() drives the broadcast look now).
# ---------------------------------------------------------------------------

NICHE_KEYWORDS = {
    "finance": ("money", "dollar", "stock", "market", "fed", "bank", "invest",
                "crypto", "bitcoin", "billion", "million", "economy", "debt",
                "inflation", "savings", "trading", "wall street"),
    "military": ("war", "drone", "missile", "army", "military", "strike",
                 "convoy", "weapon", "troops", "ukraine", "russia", "attack",
                 "battle", "tank", "navy", "airforce"),
    "health": ("health", "doctor", "disease", "supplement", "muscle", "diet",
               "cancer", "vitamin", "protein", "iron", "shampoo", "spine",
               "exercise", "medical"),
    "tech": ("ai", "tech", "phone", "speaker", "software", "robot", "app",
             "breakthrough", "invention", "digital", "chip", "jbl"),
    "sports": ("game", "match", "player", "goal", "team", "season", "coach",
               "league", "championship", "score"),
    "crime": ("murder", "police", "crime", "suspect", "arrest", "killer",
              "victim", "court", "prison", "investigation"),
    "history": ("ancient", "empire", "century", "war", "king", "civilization",
                "medieval", "battle", "dynasty", "historical"),
}


def detect_niche(text):
    """Keyword-based niche detection for headline styling.

    Word-boundary matching throughout: without it "king" fires inside
    "breaking" and "ai" fires inside "said" (the same bug class fixed in
    audio_design._kw_hit).
    """
    low = (text or "").lower()
    best, best_hits = "general", 0
    for niche, kws in NICHE_KEYWORDS.items():
        hits = sum(1 for k in kws
                   if re.search(r"\b" + re.escape(k) + r"\b", low))
        if hits > best_hits:
            best, best_hits = niche, hits
    return best


# ---------------------------------------------------------------------------
# SPEC DATA MODEL — CapCut auto-caption engine (Step 1 of user spec).
#
# Words come from Whisper/faster-whisper with word_timestamps=True:
#   { word: str, start: float (sec, 3 decimals), end: float,
#     confidence: float }
# chunk_words() groups them per the spec's chunking rule. word_state_at()
# implements the 3-state machine (active/past/upcoming) that drives fill
# color, scale and glow per style config.
# ---------------------------------------------------------------------------
_SENTENCE_END = frozenset(".!?")

def chunk_words(words, max_words=4, max_dur=2.5, gap_break=0.4):
    """Group word-timestamp dicts into caption chunks.

    Break a chunk at: sentence punctuation (. ! ?), gap between words >
    gap_break seconds, or chunk duration > max_dur seconds. Caps chunk at
    max_words words (3-4 for 9:16 shorts, 7 for 16:9 landscape).
    Returns [{ words: [...], start: float, end: float }, ...].
    """
    chunks = []
    cur = []
    for w in words:
        if cur:
            prev = cur[-1]
            gap = w["start"] - prev["end"]
            dur = w["end"] - cur[0]["start"]
            if (len(cur) >= max_words or gap > gap_break
                    or dur > max_dur
                    or prev["word"][-1:] in _SENTENCE_END):
                chunks.append({"words": list(cur),
                               "start": cur[0]["start"],
                               "end": cur[-1]["end"]})
                cur = []
        cur.append(w)
    if cur:
        chunks.append({"words": list(cur),
                       "start": cur[0]["start"],
                       "end": cur[-1]["end"]})
    return chunks


def word_state_at(word, t):
    """3-state machine for one word at frame time t.

    Returns "active" (t within [start, end]), "past" (t > end) or
    "upcoming" (t < start).
    """
    if t < word["start"]:
        return "upcoming"
    if t > word["end"]:
        return "past"
    return "active"


def _ts_srt(sec):
    sec = max(0.0, sec)
    h, rem = int(sec // 3600), sec % 3600
    m, rem = int(rem // 60), rem % 60
    return f"{h:02d}:{m:02d}:{int(rem):02d},{int(rem % 1 * 1000):03d}"


def _ts_ass(sec):
    sec = max(0.0, sec)
    h, rem = int(sec // 3600), sec % 3600
    m, rem = int(rem // 60), rem % 60
    return f"{h}:{m:02d}:{int(rem):02d}.{int(rem % 1 * 100):02d}"


# ---------------------------------------------------------------------------
# CapCut-clone caption animation engines (2026-10-02: 23 styles).
#
# The 2026-10-01 14-style set was replaced at the user's request
# (capcut_clone_research_2026-10-02.md): beast_box retired (its successor
# is beast); hormozi/beast/box_highlight/pill/caption_bar/glide/
# gradient_sweep/sticker_pop/glitch/pulse added. All 23 are visual
# recreations of CapCut's trending caption looks (see each template's
# meta["source"]). They are NOT CapCut's internal parameters — CapCut
# does not publish those — but the animation behavior categories they
# recreate (word pop, bounce, spring entrances, karaoke highlighting,
# typewriter, wave, glow, box/pill highlights, glide, gradient sweep,
# sticker rotation, glitch, pulse) ARE documented CapCut text effects.
#
# Rendering model (robust by design):
#  * Layer 0: the full phrase card, static, per chunk.
#  * Layer 1: one overlay event per active word holding the FULL phrase;
#    every word except the active one is fully transparent
#    ({\alpha&HFF&}). libass lays out both layers identically, so the
#    active word always sits exactly over its base word — no measuring,
#    no font-mismatch ghosting, on any libass build.
#  * Tag mechanics verified against libass (2026-09-30):
#    \t(t1,t2[,accel],tags): t1/t2 = ms from EVENT start; sequential \t
#    tags chain correctly; nested \t is NOT supported.
#    Transformable in \t: \fscx \fscy \fs \frz \bord \shad \1c-\4c \alpha...
#    NOT transformable: \pos \move \fad \fade \k* \an.
#    Y-motion (bounce / spring / wave) therefore uses \move at EVENT level
#    (the transparent words move too, invisibly — only the active word
#    shows). \u1 underlines just the tagged word run.
# ---------------------------------------------------------------------------

_CAP_TERMINAL_PUNCT = ".,!?;:"


def _norm_cap_words(word_timings, text_case):
    """Normalize word timings: strip terminal punctuation, fix bad spans."""
    out = []
    for w in word_timings or []:
        t = str(w.get("word", "") or "").strip()
        t = t.rstrip(_CAP_TERMINAL_PUNCT).strip()
        if not t:
            continue
        if text_case == "upper":
            t = t.upper()
        elif text_case == "lower":
            t = t.lower()
        s = float(w.get("start", 0.0) or 0.0)
        e = float(w.get("end", 0.0) or 0.0)
        if e <= s:
            e = s + 0.2
        out.append({"word": t, "start": s, "end": e})
    return out


# ---------------------------------------------------------------------------
# DATA-DRIVEN PRESET RENDERER (2026-10-02 rebuild).
#
# RENDERER LAW: every function below is a PURE function of the preset
# fields + word timestamps. No branch invents animations, effects, emojis,
# colors, or movements that are not in the selected preset.
#
# Architecture (proven, kept): layer 0 = full phrase card; layer 1 =
# per-active-word overlay holding the FULL phrase with every other word
# fully transparent ({\alpha&HFF&}) so libass lays both layers out
# identically — no fragile word-width measurement. Words center-anchored
# (\an5). Y-motion uses event-level \move (NOT \t on \pos — libass can't
# transform \pos). \t() = milliseconds, \k = centiseconds, colors BGR.
#
# Spoken-word tracking: each overlay event covers exactly one word's
# active window (word start + lead .. max(word end, start + floor)).
# Previous words automatically return to normal because every event is
# independent — only the currently spoken word carries the preset's
# active-word animation.
# ---------------------------------------------------------------------------

#: Upcoming-word color for highlight="dim" (karaoke-dim concept).
_DIM_UPCOMING = "&H00A0A0A0"

#: Gradient sweep stops for active_anim="sweep" (teal -> blue -> violet).
_SWEEP_MID = "&H00F6823B"
_SWEEP_END = "&H00F65C8B"


def _preset_anchor(p, pw, ph):
    """(alignment, ax, ay) from the preset POSITION field. Deterministic."""
    pos = p.get("position", "bottom")
    if pos == "top":
        return 8, pw / 2.0, 60.0
    if pos == "center":
        return 5, pw / 2.0, ph / 2.0
    if pos == "lower":
        return 2, pw / 2.0, float(ph - int(ph * 0.16))
    return 2, pw / 2.0, float(ph - 60)


def _clamp_burn_xy(x, y, pw, ph, cap_size):
    """Keep burned caption coordinates inside the frame.

    The preview overlay clamps the drag via margins so captions stay
    visible; the burn must give the same guarantee. A big downward drag
    used to bake \\pos() coordinates below the frame (e.g. y=1288 on a
    1080p video) — captions silently vanished after Apply. Margins are
    chosen so default (un-dragged) anchors are never moved.
    """
    mx, my = 60, 60
    cx = min(max(int(round(x)), mx), pw - mx)
    cy = min(max(int(round(y)), my), ph - my)
    return cx, cy


def _layout_fs(p, layout, j, n, cap_size):
    """Metric font-size tag for word j (must be identical across layers)."""
    if layout == "two_tone" and n > 1 and j == n - 1:
        return "\\fs%d" % int(cap_size * 1.25)
    if layout == "headline" and n > 1:
        return "\\fs%d" % (int(cap_size * 1.6) if j == 0
                           else int(cap_size * 0.7))
    return ""


def _em_pair(p, visible):
    """Emoji decoration pair for a word. Invisible variant keeps metrics
    identical via transparent placeholders (never invents layout)."""
    em = p.get("emoji", "")
    if not em:
        return "", ""
    if visible:
        return em + " ", " " + em
    return ("{\\alpha&HFF&}%s{\\alpha&H00&} " % em,
            " {\\alpha&HFF&}%s{\\alpha&H00&}" % em)


def _join_chunks(parts):
    """Join word chunks with spaces, keeping backslash-N line breaks tight."""
    text = ""
    for c in parts:
        if c == "\\N":
            text = text.rstrip() + "\\N"
        elif text.endswith("\\N"):
            text += c
        else:
            text += (" " if text else "") + c
    return text


def _hl_tags(p):
    """Highlight treatment open-tags for the active word (no braces)."""
    hl = p.get("highlight", "color")
    ac = p["active_color"]
    on = p.get("on_highlight_text", "&H00000000")
    if hl == "box":
        return "\\c%s\\3c%s\\bord7" % (on, ac)
    if hl == "pill":
        return "\\c%s\\3c%s\\bord8" % (on, ac)
    if hl == "underline":
        return "\\c%s\\u1" % ac
    if hl == "wash":
        # translucent color wash behind the glyphs. NOTE: no \t here —
        # libass drops an entire event when two \t transforms share one
        # override block, and the active word's group already carries the
        # ACTIVE WORD ANIMATION's \t (see _anim_phases).
        return ("\\c%s\\3c%s\\bord10\\3a&H40&"
                % (ac, ac))
    if hl == "glow":
        return "\\c%s\\3c%s\\bord4\\blur6" % (ac, ac)
    if hl in ("color", "dim", "outline"):
        return "\\c%s" % ac
    return "\\c%s" % p["primary"]  # highlight == "none"


def _anim_phases(p, xi, yi, dur_ms):
    """ACTIVE WORD ANIMATION as sequential phases: [(delay_ms, tags)].

    RENDERER LAW (libass safety): ffmpeg's libass — the renderer U One
    burns captions with — silently drops an ENTIRE event when two ``\\t``
    transforms share one override block (verified empirically). So a
    multi-stage animation is emitted as sequential Dialogue events, each
    phase carrying at most ONE ``\\t`` with t1=0. Each phase is still a
    pure function of the preset's ACTIVE WORD ANIMATION field.
    """
    a = p.get("active_anim", "pop")
    peak = int(p.get("anim_peak", 120))
    up = int(p.get("anim_up_ms", 80))
    total = int(p.get("anim_total_ms", 160))
    frm = int(p.get("anim_frm") or max(50, 200 - peak))
    y = int(p.get("anim_y", 0))
    ac = p["active_color"]
    settle = max(60, total - up)
    if a == "pop":
        return [(0, "\\fscx%d\\fscy%d\\t(0,%d,0.5,\\fscx%d\\fscy%d)"
                     % (frm, frm, up, peak, peak)),
                (up, "\\fscx%d\\fscy%d\\t(0,%d,1.8,\\fscx100\\fscy100)"
                     % (peak, peak, settle))]
    if a == "spring":
        return [(0, "\\move(%d,%d,%d,%d,0,%d)"
                     "\\fscx%d\\fscy%d\\t(0,%d,0.5,\\fscx%d\\fscy%d)"
                     % (xi, yi + y, xi, yi, up,
                        frm or 85, frm or 85, up, peak, peak)),
                (up, "\\fscx%d\\fscy%d\\t(0,%d,1.5,\\fscx100\\fscy100)"
                     % (peak, peak, settle))]
    if a == "stomp":
        return [(0, "\\fscx%d\\fscy%d\\t(0,%d,1.8,\\fscx100\\fscy100)"
                     % (peak, peak, up))]
    if a == "fade":
        return [(0, "\\alpha&HFF&\\t(0,%d,\\alpha&H00&)" % up)]
    if a == "rise":
        return [(0, "\\move(%d,%d,%d,%d,0,%d)"
                     "\\alpha&HFF&\\t(0,%d,\\alpha&H00&)"
                     % (xi, yi + y, xi, yi, up, up))]
    if a == "fall":
        return [(0, "\\move(%d,%d,%d,%d,0,%d)"
                     "\\alpha&HFF&\\t(0,%d,\\alpha&H00&)"
                     % (xi, yi - y, xi, yi, up, min(up, 80)))]
    if a == "wipe":
        return [(0, "\\fscx30\\fscy100\\t(0,%d,0.5,\\fscx100\\fscy100)"
                     % up)]
    if a == "rotate":
        return [(0, "\\frz-10\\fscx80\\fscy80"
                     "\\t(0,%d,0.5,\\frz0\\fscx%d\\fscy%d)"
                     % (up, peak, peak)),
                (up, "\\fscx%d\\fscy%d\\t(0,%d,1.8,\\fscx100\\fscy100)"
                     % (peak, peak, settle))]
    if a == "glowpulse":
        return [(0, "\\3c%s\\bord2\\blur1"
                     "\\t(0,%d,\\bord6\\blur10\\fscx105\\fscy105)"
                     % (ac, up)),
                (up, "\\3c%s\\bord6\\blur10\\fscx105\\fscy105"
                     "\\t(0,120,\\bord2\\blur1\\fscx100\\fscy100)"
                     % ac)]
    if a == "throb":
        n = min(4, max(1, dur_ms // 300))
        phases = []
        for pi in range(n):
            s0 = pi * 300
            phases.append((s0, "\\t(0,150,\\fscx108\\fscy108)"))
            phases.append((s0 + 150,
                           "\\fscx108\\fscy108"
                           "\\t(0,150,\\fscx100\\fscy100)"))
        return phases
    if a == "slide":
        return [(0, "\\move(%d,%d,%d,%d,0,%d)" % (xi + 60, yi, xi, yi, up))]
    if a == "sweep":
        return [(0, "\\fscx%d\\fscy%d\\1c%s"
                     "\\t(0,%d,0.5,\\fscx%d\\fscy%d)"
                     % (frm, frm, _SWEEP_MID, up, peak, peak)),
                (up, "\\fscx%d\\fscy%d\\1c%s"
                     "\\t(0,%d,1.8,\\fscx100\\fscy100)"
                     % (peak, peak, _SWEEP_MID, settle)),
                (total, "\\1c%s\\t(0,60,\\1c%s)"
                        % (_SWEEP_MID, _SWEEP_END)),
                (total + 60, "\\1c%s\\t(0,60,\\1c%s)"
                             % (_SWEEP_END, ac))]
    return [(0, "")]  # "none" (and any unknown value degrades to no motion)


def _anim_sub_events(p, xi, yi, ws, we):
    """Active-word overlay sub-events: (start, end, pos_tags, tags, fade_in).

    Most primitives are a single event; jitter/wave need segmented moves.
    """
    a = p.get("active_anim", "pop")
    dur_ms = max(1, int((we - ws) * 1000))
    pos = "\\an5\\pos(%d,%d)" % (xi, yi)
    if a == "jitter":
        amp = int(p.get("anim_amp", 4))
        out = []
        for si in range(4):
            es = ws + si * 0.06
            ee = ws + (si + 1) * 0.06
            x0 = xi + (amp if si % 2 == 0 else -amp)
            out.append({"s": es, "e": ee,
                        "pos": "\\an5\\move(%d,%d,%d,%d,0,60)"
                               % (x0, yi, xi, yi),
                        "tags": "", "fade_in": 0})
        out.append({"s": ws + 0.24, "e": we, "pos": pos,
                    "tags": "", "fade_in": 0})
        return out
    if a == "wave":
        total = int(p.get("anim_total_ms", 400))
        seg = max(60, total // 4)
        ys = [10, -10, 10, -10]
        out = []
        y_prev = 0
        for si, y_off in enumerate(ys):
            es = ws + si * seg / 1000.0
            ee = ws + (si + 1) * seg / 1000.0
            out.append({"s": es, "e": ee,
                        "pos": "\\an5\\move(%d,%d,%d,%d,0,%d)"
                               % (xi, yi + y_prev, xi, yi + y_off, seg),
                        "tags": "", "fade_in": 60 if si == 0 else 0})
            y_prev = y_off
        out.append({"s": ws + 4 * seg / 1000.0, "e": we, "pos": pos,
                    "tags": "", "fade_in": 0})
        return out
    fade_in = 150 if a == "slide" else (0 if a in ("fade",) else 100)
    # Multi-phase animations become sequential sub-events so that no
    # single event ever carries two \t transforms (libass would drop
    # the whole event — see _anim_phases). Phases hand off cleanly:
    # phase i ends exactly when phase i+1 starts.
    phases = _anim_phases(p, xi, yi, dur_ms)
    out = []
    for pi, (delay, tags) in enumerate(phases):
        if delay >= dur_ms:
            continue
        es = ws + delay / 1000.0
        if pi + 1 < len(phases) and phases[pi + 1][0] < dur_ms:
            ee = ws + phases[pi + 1][0] / 1000.0
        else:
            ee = we
        out.append({"s": es, "e": ee, "pos": pos, "tags": tags,
                    "fade_in": fade_in if pi == 0 else 0})
    return out or [{"s": ws, "e": we, "pos": pos, "tags": "",
                    "fade_in": fade_in}]


def _entry_open(p, xi, yi):
    """Base-layer open tags: ENTRY animation + EXIT fade. Pure preset data."""
    e = p.get("entry", "pop")
    ms = int(p.get("entry_ms", 180))
    eo = int(p.get("exit_ms", 120)) if p.get("exit") == "fade" else 0
    if e == "pop":
        return ("\\an5\\pos(%d,%d)\\fscx120\\fscy120"
                "\\t(0,%d,\\fscx100\\fscy100)\\fad(0,%d)"
                % (xi, yi, ms, eo))
    if e == "slide_up":
        return ("\\an5\\move(%d,%d,%d,%d,0,%d)\\fad(0,%d)"
                % (xi, yi + 60, xi, yi, ms, eo))
    fi = ms if e == "fade" else 0
    return "\\an5\\pos(%d,%d)\\fad(%d,%d)" % (xi, yi, fi, eo)


def _entry_tags_no_exit(p, xi, yi):
    """ENTRY animation tags WITHOUT the exit fade.

    Used by the mirrored per-word base (see _METRIC_ANIMS): the exit
    \\fad belongs on the last word's last phase, not on the entry.
    """
    e = p.get("entry", "pop")
    ms = int(p.get("entry_ms", 180))
    if e == "pop":
        return ("\\an5\\pos(%d,%d)\\fscx120\\fscy120"
                "\\t(0,%d,\\fscx100\\fscy100)" % (xi, yi, ms))
    if e == "slide_up":
        return ("\\an5\\move(%d,%d,%d,%d,0,%d)"
                % (xi, yi + 60, xi, yi, ms))
    fi = ms if e == "fade" else 0
    return "\\an5\\pos(%d,%d)\\fad(%d,0)" % (xi, yi, fi)


# Active-word animations whose override tags change glyph metrics
# (\\fscx/\\fscy scale). The Layer-1 overlay renders the active word
# with these tags while the other words are transparent placeholders;
# the scaled word changes the total line width, so \\an5 re-centers the
# overlay line and the highlight no longer sits over its base word --
# the "ABOVEONE" collision / ghost-"S" overlap Uzair photographed on
# 2026-10-02. Presets using these animations get a per-word MIRRORED
# base (same words, same tags, primary color) so both layers are
# metric-identical at every instant. Animations using only \\move /
# \\alpha (fade, rise, fall, slide, jitter, wave) never change line
# metrics and keep the classic chunk-level base card.
_METRIC_ANIMS = frozenset({
    "pop", "spring", "stomp", "wipe", "rotate", "glowpulse", "throb",
    "sweep",
})


def _base_phrase_preset(p, disp, cap_size):
    """Visible base-layer phrase. Metric-identical to the overlay words."""
    n = len(disp)
    layout = p.get("layout", "standard")
    ac = p["active_color"]
    parts = []
    for j, w in enumerate(disp):
        fs = _layout_fs(p, layout, j, n, cap_size)
        if layout == "two_tone" and n > 1 and j == n - 1:
            col = ac
        elif layout == "headline" and n > 1 and j > 0:
            col = ac
        else:
            col = p["primary"]
        pre, post = _em_pair(p, False)
        parts.append("%s{\\c%s%s}%s%s" % (pre, col, fs, w, post))
        if layout == "headline" and j == 0 and n > 1:
            parts.append("\\N")
    return _join_chunks(parts)


def _overlay_text(p, disp, idx, cap_size, anim_tags, dim_mode):
    """Layer-1 overlay: full phrase, only word idx visible (or dim-mode
    states). Inactive words are fully transparent layout placeholders,
    so libass positions the active word exactly over the base layer."""
    n = len(disp)
    layout = p.get("layout", "standard")
    em = p.get("emoji", "")
    ac = p["active_color"]
    hl = p.get("highlight", "color")
    # ASS tags are STATEFUL across an event: an earlier {\alpha&HFF&}
    # (transparent placeholder) would otherwise keep the active word
    # invisible, because \c changes color but NOT alpha. Every group
    # that shows text therefore resets alpha explicitly — except the
    # fade animation, which drives alpha itself via \t.
    areset = "" if "\\alpha" in anim_tags else "\\alpha&H00&"
    chunks = []
    for j, w in enumerate(disp):
        fs = _layout_fs(p, layout, j, n, cap_size)
        if dim_mode:
            # karaoke-dim: past = primary, active = highlight, upcoming = grey
            pre_v, post_v = ((em + " ", " " + em) if em else ("", ""))
            if j < idx:
                paint = "{\\alpha&H00&\\c%s%s}" % (p["primary"], fs)
            elif j == idx:
                paint = "{%s%s%s%s}" % (areset, _hl_tags(p), fs, anim_tags)
            else:
                paint = "{\\alpha&H00&\\c%s%s}" % (_DIM_UPCOMING, fs)
            chunks.append(paint + pre_v + w + post_v)
        elif j == idx:
            pre_v, post_v = ((em + " ", " " + em) if em else ("", ""))
            if hl == "none":
                open_t = "{%s\\c%s%s%s}" % (areset, p["primary"], fs,
                                            anim_tags)
            elif hl == "outline":
                open_t = "{%s\\c%s\\1a&H00&%s%s}" % (areset, ac, fs,
                                                     anim_tags)
            else:
                open_t = "{%s%s%s%s}" % (areset, _hl_tags(p), fs,
                                         anim_tags)
            chunks.append(open_t + pre_v + w + post_v)
        else:
            t = "{\\alpha&HFF&%s}" % fs
            if em:
                t += em + " "
            t += w
            if em:
                t += " " + em
            chunks.append(t)
        if layout == "headline" and j == 0 and n > 1:
            chunks.append("\\N")
    return _join_chunks(chunks)


def _base_word_text(p, disp, idx, cap_size, anim_tags):
    """Layer-0 mirror of one overlay word event.

    Full phrase, every word visible in the base colors, word ``idx``
    carrying the SAME animation tags as the overlay's active word
    (\\fscx/\\fscy scale phases included). The line is therefore
    metric-identical to the overlay line at every instant, so \\an5
    centers both identically and the highlight sits pixel-exact over
    its base word -- even mid-pop. Only used for _METRIC_ANIMS.
    """
    n = len(disp)
    layout = p.get("layout", "standard")
    em = p.get("emoji", "")
    ac = p["active_color"]
    areset = "" if "\\alpha" in anim_tags else "\\alpha&H00&"
    chunks = []
    for j, w in enumerate(disp):
        fs = _layout_fs(p, layout, j, n, cap_size)
        if layout == "two_tone" and n > 1 and j == n - 1:
            col = ac
        elif layout == "headline" and n > 1 and j > 0:
            col = ac
        else:
            col = p["primary"]
        pre_v, post_v = ((em + " ", " " + em) if em else ("", ""))
        if j == idx:
            chunks.append("{%s\\c%s%s%s}%s%s%s"
                          % (areset, col, fs, anim_tags, pre_v, w, post_v))
        else:
            chunks.append("{\\c%s%s}%s%s%s"
                          % (col, fs, pre_v, w, post_v))
        if layout == "headline" and j == 0 and n > 1:
            chunks.append("\\N")
    return _join_chunks(chunks)


def _render_typewriter_preset(p, chunks, ax, ay, cap_size, lead, floor_s,
                              hold_s):
    """Character-by-character reveal synced to word timestamps.

    Each character of each word gets its own event inside the word's
    active window, plus a block cursor at the frontier. The completed
    word holds on screen until the next word starts.
    """
    lines = []
    cursor = "▌"
    cur_c = p["active_color"]
    xi, yi = int(round(ax)), int(round(ay))

    def _dlg(s, e, txt):
        return ("Dialogue: 1,%s,%s,Cap,,0,0,0,,"
                "{\\an5\\pos(%d,%d)}%s" % (_ts_ass(max(0.0, s)),
                                          _ts_ass(max(0.001, e)), xi, yi,
                                          txt))

    for ci, ch in enumerate(chunks):
        seg = ch["words"]
        if ci + 1 < len(chunks):
            # Next chunk's first visible instant: nothing from this chunk
            # may linger past it (same overlap rule as the phrase cards).
            next_start = max(0.0,
                             chunks[ci + 1]["words"][0]["start"] + lead)
        else:
            next_start = None
        acc = []
        for wi, w in enumerate(seg):
            word = w["word"]
            nch = max(1, len(word))
            ws = max(0.0, w["start"] + lead)
            dur = max(w["end"], w["start"] + floor_s) - ws
            step = min(dur / nch, 0.055)  # reveal speed cap ~55ms/char
            if wi + 1 < len(seg):
                # Handoff: this word's reveal ends exactly when the next
                # word's reveal starts (its 20ms lead). The completed word
                # stays visible as the next reveal's prefix text.
                hold_until = max(0.0, seg[wi + 1]["start"] + lead)
            else:
                hold_until = w["end"] + hold_s
                if next_start is not None:
                    hold_until = min(hold_until, next_start)
            # Degenerate timings still get a blink of visibility.
            hold_until = max(hold_until, ws + 0.02)
            prefix = (" ".join(acc) + " ") if acc else ""
            for k in range(1, nch + 1):
                cs = ws + (k - 1) * step
                ce = ws + k * step
                if k == nch:
                    ce = hold_until  # completed word holds until handoff
                else:
                    ce = min(ce, hold_until)  # reveal never bleeds past it
                if ce <= cs:
                    continue
                txt = sanitize_ass_text(prefix + word[:k])
                lines.append(_dlg(cs, ce, "%s{\\c%s}%s" % (txt, cur_c,
                                                           cursor)))
            acc.append(word)
        end = seg[-1]["end"] + hold_s
        full = sanitize_ass_text(" ".join(w["word"] for w in seg))
        # Tail flashes must also yield to the next chunk: trim them so
        # nothing from this chunk is visible once the next chunk starts.
        tail_end = end + 0.50
        if next_start is not None:
            tail_end = min(tail_end, next_start)
        if end < tail_end:
            lines.append(_dlg(end, min(end + 0.25, tail_end), full))
        if end + 0.25 < tail_end:
            lines.append(_dlg(end + 0.25, tail_end,
                              "%s{\\c%s}%s" % (full, cur_c, cursor)))
    return lines


def _render_preset_captions(p, sentence_timings, word_timings, pw, ph,
                            cap_size, dx_px, dy_px):
    """Dialogue lines for one caption preset — the data-driven renderer.

    Layer 0: the full phrase card (preset ENTRY/EXIT, BACKGROUND, POSITION).
    Layer 1: per active word, the full phrase with only that word visible,
    carrying the preset's WORD HIGHLIGHT + ACTIVE WORD ANIMATION, timed
    from the word-level audio timestamps (WORD TIMING field). Previous
    words automatically return to normal: every overlay event is
    independent, so when a word's window ends its styling ends with it.
    """
    lines = []
    if p.get("active_anim") == "typewriter":
        words_tw = _norm_cap_words(word_timings, p.get("text_case"))
        if not words_tw:
            return lines
        _al, ax, ay = _preset_anchor(p, pw, ph)
        ax, ay = _clamp_burn_xy(ax + dx_px, ay + dy_px, pw, ph, cap_size)
        lead = p.get("lead_ms", -20) / 1000.0
        floor_s = p.get("floor_ms", 140) / 1000.0
        hold_s = p.get("hold_ms", 200) / 1000.0
        chunks = chunk_words(words_tw, max_words=p.get("max_words", 4),
                             max_dur=2.5, gap_break=0.4)
        return _render_typewriter_preset(p, chunks, ax, ay, cap_size,
                                         lead, floor_s, hold_s)
    words = _norm_cap_words(word_timings, p.get("text_case"))
    if not words:
        return lines
    _al, ax, ay = _preset_anchor(p, pw, ph)
    ax, ay = _clamp_burn_xy(ax + dx_px, ay + dy_px, pw, ph, cap_size)
    xi, yi = int(round(ax)), int(round(ay))
    lead = p.get("lead_ms", -20) / 1000.0
    floor_s = p.get("floor_ms", 140) / 1000.0
    hold_s = p.get("hold_ms", 200) / 1000.0
    chunks = chunk_words(words, max_words=p.get("max_words", 4),
                         max_dur=2.5, gap_break=0.4)
    dim_mode = (p.get("highlight") == "dim")
    # Metric-changing active anims (pop/scale family): the overlay's
    # scaled word would otherwise re-center the overlay line under
    # \\an5 and collide with its base word ("ABOVEONE", ghost letters --
    # Uzair's 2026-10-02 photos). These get a per-word mirrored base
    # instead of the chunk-level card.
    metric_anim = (not dim_mode
                   and p.get("active_anim", "pop") in _METRIC_ANIMS)
    base_open = ""
    if p.get("highlight") == "outline":
        # hollow base: transparent fill, colored outline
        base_open = "\\1a&HFF&\\3c%s\\bord%d" % (p["active_color"],
                                                p.get("outline", 3))
    for ci, ch in enumerate(chunks):
        seg = ch["words"]
        disp = [w["word"] for w in seg]
        card_s = max(0.0, seg[0]["start"] + lead)
        card_e = seg[-1]["end"] + hold_s
        if ci + 1 < len(chunks):
            # ROOT CAUSE FIX (2026-10-02, Uzair's photos): the 200ms hold
            # made the previous phrase linger ~220ms into the next phrase
            # ("pale text abi ha aur uepr sa agala text a jata ha").
            # Trim the hold so this card ends exactly when the next card
            # starts. A genuine pause (gap > lead+hold) keeps its full hold.
            next_s = max(0.0, chunks[ci + 1]["words"][0]["start"] + lead)
            card_e = min(card_e, max(card_s, next_s))
        if not dim_mode and not metric_anim:
            base_text = _base_phrase_preset(p, disp, cap_size)
            lines.append(
                "Dialogue: 0,%s,%s,Cap,,0,0,0,,"
                "{%s%s}%s"
                % (_ts_ass(card_s), _ts_ass(card_e),
                   _entry_open(p, xi, yi), base_open, base_text))
        for i, w in enumerate(seg):
            ws = max(0.0, w["start"] + lead)
            we = max(w["end"], w["start"] + floor_s)
            # Clean highlight handoff: this word's highlight ends exactly
            # when the next word's (20ms early) highlight starts, so two
            # words are never highlighted at once -- including across the
            # chunk boundary.
            if i + 1 < len(seg):
                _nw_start = seg[i + 1]["start"]
            elif ci + 1 < len(chunks):
                _nw_start = chunks[ci + 1]["words"][0]["start"]
            else:
                _nw_start = None
            if _nw_start is not None:
                we = min(we, max(ws, max(0.0, _nw_start + lead)))
            sub = _anim_sub_events(p, xi, yi, ws, we)
            for pi, ev in enumerate(sub):
                pos_tags = ev["pos"]
                if ev["fade_in"]:
                    pos_tags += "\\fad(%d,0)" % ev["fade_in"]
                txt = _overlay_text(p, disp, i, cap_size, ev["tags"],
                                    dim_mode)
                lines.append(
                    "Dialogue: 1,%s,%s,Cap,,0,0,0,,{%s}%s"
                    % (_ts_ass(ev["s"]), _ts_ass(ev["e"]),
                       pos_tags, txt))
                if metric_anim:
                    # Mirrored Layer-0: same words, same phase tags,
                    # primary color -- metric-identical line, so the
                    # highlight lands pixel-exact on its base word.
                    if i == 0 and pi == 0:
                        bpos = _entry_tags_no_exit(p, xi, yi)
                    else:
                        bpos = "\\an5\\pos(%d,%d)" % (xi, yi)
                    last_phase = (i == len(seg) - 1
                                  and pi == len(sub) - 1)
                    # The base keeps the card's 200ms hold AND stays
                    # gapless across the chunk (the old card was one
                    # continuous event): the last phase bridges any
                    # pause to the next word's start / card_e.
                    if last_phase:
                        be = card_e
                    elif pi == len(sub) - 1:
                        _nws = max(0.0, seg[i + 1]["start"] + lead)
                        be = max(ev["e"], _nws)
                    else:
                        be = ev["e"]
                    btail = ""
                    if last_phase:
                        _eo = (int(p.get("exit_ms", 120))
                               if p.get("exit") == "fade" else 0)
                        if _eo:
                            btail = "\\fad(0,%d)" % _eo
                    btxt = _base_word_text(p, disp, i, cap_size,
                                           ev["tags"])
                    lines.append(
                        "Dialogue: 0,%s,%s,Cap,,0,0,0,,"
                        "{%s%s}%s%s"
                        % (_ts_ass(ev["s"]), _ts_ass(be),
                           bpos, btail, base_open, btxt))
    return lines


def _headline_entrance(entrance, pw, ph, margin_v, alignment=8,
                      enter_ms=200, exit_ms=150):
    """ASS entrance + exit tags for a main-text card (Genspark §4).

    Entrances run 150-250ms; the exit fade is ALWAYS shorter than the
    entrance. `entrance` is one of: fade (default), slide, pop.
    """
    enter_ms = int(enter_ms)
    # Hard rule: exit fade strictly shorter than the entrance.
    exit_ms = min(int(exit_ms), max(80, enter_ms - 30))
    an = alignment
    xc = pw // 2
    y = ph // 2 if an == 5 else (ph - margin_v if an == 2 else margin_v)
    if entrance == "slide":
        if an in (5, 2):
            mv = "\\move(%d,%d,%d,%d,0,%d)" % (xc, y + 60, xc, y, enter_ms)
        else:
            mv = "\\move(%d,%d,%d,%d,0,%d)" % (-pw // 4, y, xc, y, enter_ms)
        return "{\\an%d\\fad(0,%d)%s}" % (an, exit_ms, mv)
    if entrance == "pop":
        return ("{\\an%d\\fad(%d,%d)\\fscx120\\fscy120"
                "\\t(0,%d,\\fscx100\\fscy100)}"
                % (an, enter_ms, exit_ms, enter_ms))
    return "{\\an%d\\fad(%d,%d)}" % (an, enter_ms, exit_ms)


def _headline_style_line(pid, hp, ph, hl_type="STAT", box_c=None, name=None):
    """One V4+ Style line for a headline preset + main-text type.

    Style name: `name`, or HL_<pid>_<type>. Size follows the per-type
    MAIN_TEXT_STYLE table at 1080p, scaled by the play resolution and the
    preset's size_mult. Box colour comes from story_box_color() (story
    type), not from the text type (§3).
    """
    ts = MAIN_TEXT_STYLE.get(hl_type, MAIN_TEXT_STYLE["STAT"])
    size = max(28, int(ts["size"] * (ph / 1080.0) * hp.get("size_mult", 1.0)))
    an = ts["align"]
    mv = int(ph * ts["y_frac"]) if an in (8, 9) else 0
    box = hp.get("box", True)
    border = 3 if box else max(1, hp.get("outline", 1))
    outline = hp.get("outline", 1)
    shadow = hp.get("shadow", 1)
    back_c = box_c or _box_back(ts["box_op"])
    sname = name or "HL_%s_%s" % (pid, hl_type)
    return (f"Style: {sname},{hp['font']},{size},{hp['primary']},{hp['primary']},"
            f"&H00000000,{back_c},-1,0,0,0,100,100,0,0,"
            f"{border},{outline},{shadow},{an},40,40,{mv},1")


def build_srt(sentence_timings, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, s in enumerate(sentence_timings, 1):
            f.write(f"{i}\n{_ts_srt(s['start'])} --> {_ts_srt(s['end'])}\n{s['text']}\n\n")
    return path


def build_ass(sentence_timings, word_timings, path, font_size=48, highlight=True,
              overlays=None, play_res=(1920, 1080), template=None,
              secondary_track=None):
    """Build the ASS file: bottom captions + optional top overlays.

    overlays: list of dicts {text, start, end, style?, keywords?}.
    style: a HEADLINE_PRESETS id (creator / archivo / oswald / breaking /
    viral / pill / broadcast / minimal) — redesigned 2026-10-01 from the
    250-video main-text research: huge heavy caps, white fill, red/yellow
    keyword accents, no box by default, snappy pop entrances. Falls back
    to the default creator style.
    template: key from CAPTION_TEMPLATES (caption preset look).
    secondary_track: for dual-language templates, list of dicts
    {text, start, end} with GENUINE second-language text (never a copy of
    the primary). Rendered with the "Cap2" style at its own y position.
    """
    # template may be a key into CAPTION_TEMPLATES or a preset dict
    # (preset_schema == 2). Legacy ids (removed engines, old template
    # packs) migrate to the default preset.
    if isinstance(template, dict):
        if template.get("preset_schema") == 2:
            tmpl = dict(template)
        else:
            tmpl = dict(CAPTION_TEMPLATES.get(
                migrate_caption_template_id(template.get("template_key")),
                CAPTION_TEMPLATES[DEFAULT_CAPTION_TEMPLATE]))
            for _k in ("dx_frac", "dy_frac", "user_scale", "template_key"):
                if _k in template:
                    tmpl[_k] = template[_k]
    else:
        tmpl = dict(CAPTION_TEMPLATES.get(
            migrate_caption_template_id(template or DEFAULT_CAPTION_TEMPLATE),
            CAPTION_TEMPLATES[DEFAULT_CAPTION_TEMPLATE]))
    for _k, _v in (("spacing", 0), ("text_case", None)):
        tmpl.setdefault(_k, _v)
    pw, ph = play_res
    # user_scale: click-drag resize factor from the preview (1.0 = unchanged)
    cap_size = max(24, int(ph * font_size / 1080 * tmpl["size_scale"]
                           * tmpl.get("user_scale", 1.0)))
    top_size = max(30, int(ph / 20))
    h_primary = tmpl["primary"]
    h_outline_c, h_outline = tmpl["outline_c"], tmpl["outline"]
    h_bold = -1 if tmpl.get("bold", True) else 0
    h_italic = -1 if tmpl.get("italic", False) else 0
    # BorderStyle from the preset BACKGROUND field: 3 = opaque box/bar
    # (drawn from BackColour), 4 = rounded pill line, else 1 = outline.
    _bg = tmpl.get("background", "none")
    if _bg == "pill_line":
        border_style = 4
    elif _bg in ("box", "bar"):
        border_style = 3
    else:
        border_style = 1
    bg_op = tmpl.get("bg_opacity", 0.85)
    back_c = ("&H%02X000000" % int(round(255 * (1 - bg_op)))
              if border_style in (3, 4) else "&H90000000")
    tmpl["_cap_size"] = cap_size  # for wordbig static fallback sizing
    mv = 60
    # POSITION field -> alignment + anchor (mirrors _preset_anchor, so the
    # burned captions sit exactly where the preset says).
    _pos = tmpl.get("position", "bottom")
    _al = {"top": 8, "center": 5}.get(_pos, 2)
    if _al == 8:
        _ax, _ay = pw // 2, mv
    elif _al == 5:
        _ax, _ay = pw // 2, ph // 2
    elif _pos == "lower":
        _ax, _ay = pw // 2, ph - int(ph * 0.16)
    else:
        _ax, _ay = pw // 2, ph - mv
    # dx_frac/dy_frac are fractions of the play resolution; positive dy
    # moves captions DOWN on screen. Applied via \pos so every style
    # (including \move entrances, which get the offset baked in) shifts
    # exactly like the preview.
    dx_px = int(round(tmpl.get("dx_frac", 0.0) * pw))
    dy_px = int(round(tmpl.get("dy_frac", 0.0) * ph))
    _al = tmpl.get("alignment", 2)
    if _al == 8:
        _ax, _ay = pw // 2, mv
    elif _al == 5:
        _ax, _ay = pw // 2, ph // 2
    else:
        _ax, _ay = pw // 2, ph - mv

    def _pos_shift(ent_text):
        """{pos} prefix reproducing the default anchor + user drag offset.

        Skipped when the event already positions itself (\\move entrance
        has the offset baked in; \\pos word-boxes get it directly), and
        skipped entirely when nothing was dragged (zero behavior change).
        """
        if dx_px == 0 and dy_px == 0:
            return ""
        if "\\move" in ent_text or "\\pos" in ent_text:
            return ""
        # Clamp: never bake an off-frame position into the burn.
        _cx, _cy = _clamp_burn_xy(_ax + dx_px, _ay + dy_px, pw, ph, cap_size)
        return "{\\pos(%d,%d)}" % (_cx, _cy)
    # BACKGROUND field: the box/bar/pill is drawn from BackColour.
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: %d
PlayResY: %d
ScaledBorderAndShadow: yes
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,%s,%d,%s,%s,%s,%s,%d,%d,0,0,100,100,%d,0,%d,%d,%d,%d,40,40,%d,1
Style: Top,Arial,%d,&H00FFFFFF,&H000019FF,&H90000000,&H90000000,-1,0,0,0,100,100,0,0,1,3,1,8,60,60,60,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" % (pw, ph, tmpl["font"], cap_size, h_primary, tmpl["active_color"],
       h_outline_c, back_c, h_bold, h_italic,
       tmpl.get("spacing", 0),
       border_style, h_outline, tmpl["shadow"], _al, mv,
       top_size)
    # Headline preset styles: one V4+ Style per unique
    # (preset, main-text type, story box colour) combo actually used by the
    # overlays. The legacy plain "Top" style stays for compatibility.
    _used_hl = []
    for _ov in overlays or []:
        _pid = _ov.get("style") or DEFAULT_HEADLINE_PRESET
        if _pid not in HEADLINE_PRESETS:
            _pid = DEFAULT_HEADLINE_PRESET
        _key = (_pid, _ov.get("hl_type") or "STAT", _ov.get("box_c"))
        if _key not in _used_hl:
            _used_hl.append(_key)
    _hl_lines = "".join(
        _headline_style_line(_pid, HEADLINE_PRESETS[_pid], ph,
                             hl_type=_htype, box_c=_boxc,
                             name="HL_%s_%s_%d" % (_pid, _htype, i)) + "\n"
        for i, (_pid, _htype, _boxc) in enumerate(_used_hl))
    header = header.replace("[Events]", _hl_lines + "[Events]")
    _hl_style_names = {"%s|%s|%s" % (pid, ht, bc): "HL_%s_%s_%d" % (pid, ht, i)
                       for i, (pid, ht, bc) in enumerate(_used_hl)}
    # spec style 8 dual-language: second independent track ("Cap2" style)
    # with its own font/size/y. Genuine translation input required —
    # never duplicate the primary line.
    if tmpl.get("dual"):
        sc = tmpl.get("secondary_cfg", {})
        sec_size = int(ph * font_size / 1080 * sc.get("size_scale", 0.92))
        sec_mv = int(ph * sc.get("margin_v_frac", 0.12))
        cap2 = ("Style: Cap2,%s,%d,%s,%s,&H00000000,&H00000000,0,0,0,0,"
                "100,100,0,0,1,%d,0,2,40,40,%d,1\n" % (
                    sc.get("font", "Montserrat"), sec_size,
                    sc.get("fill", "&H00D0D0D0"), sc.get("fill", "&H00D0D0D0"),
                    sc.get("outline", 3), sec_mv))
        header = header.replace("[Events]",
                                cap2 + "[Events]")
    lines = []
    if highlight and word_timings and tmpl.get("preset_schema") == 2:
        lines.extend(_render_preset_captions(
            tmpl, sentence_timings, word_timings, pw, ph, cap_size,
            dx_px, dy_px))
    else:
        for s in sentence_timings:
            safe = sanitize_ass_text(s["text"])
            lines.append(f"Dialogue: 0,{_ts_ass(s['start'])},{_ts_ass(s['end'])},Cap,,0,0,0,,{_pos_shift('')}{safe}")
    # spec style 8: secondary track events (independent text + timings,
    # Cap2 style). Static per spec (no word animation on secondary).
    if tmpl.get("dual") and secondary_track:
        # secondary anchor: an2 with its own MarginV
        _sec_mv = int(ph * tmpl.get("secondary_cfg", {}).get("margin_v_frac", 0.12))
        if dx_px == 0 and dy_px == 0:
            _sec_pos = ""
        else:
            _sx, _sy = _clamp_burn_xy(pw // 2 + dx_px, ph - _sec_mv + dy_px,
                                      pw, ph, cap_size)
            _sec_pos = "{\\pos(%d,%d)}" % (_sx, _sy)
        for t2 in secondary_track:
            safe2 = sanitize_ass_text(t2["text"])
            lines.append(f"Dialogue: 0,{_ts_ass(t2['start'])},{_ts_ass(t2['end'])},"
                         f"Cap2,,0,0,0,,{_sec_pos}{safe2}")
    for ov in overlays or []:
        txt = ov.get("text", "") or ""
        if not txt.strip():
            continue
        pid = ov.get("style") or DEFAULT_HEADLINE_PRESET
        if pid not in HEADLINE_PRESETS:
            pid = DEFAULT_HEADLINE_PRESET
        hp = HEADLINE_PRESETS[pid]
        # Case follows the main-text type (§2 Step 3): stats upper, names
        # title, quotes verbatim. select_main_text() already applies it;
        # raw callers should pass cased text.
        # Accent color: ONLY the hero words (accent_words, <=3) get it —
        # everything else renders in the primary (white). Accenting every
        # keyword turned whole headlines ALL RED (ugly user screenshots).
        accset = {str(k).lower() for k in (ov.get("accent_words") or [])}
        accent = ov.get("accent") or hp["accent"]
        parts = []
        for w_ in txt.split():
            core = w_.strip(".,!?\"'").lower()
            safe = sanitize_ass_text(w_)
            if core and core in accset and accent != hp["primary"]:
                parts.append("{\\c%s}%s{\\c%s}"
                             % (accent, safe, hp["primary"]))
            else:
                parts.append(safe)
        _htype = ov.get("hl_type") or "STAT"
        _boxc = ov.get("box_c")
        _sname = _hl_style_names.get("%s|%s|%s" % (pid, _htype, _boxc),
                                    "HL_%s_%s" % (pid, _htype))
        _ts = MAIN_TEXT_STYLE.get(_htype, MAIN_TEXT_STYLE["STAT"])
        _mv = int(ph * _ts["y_frac"])
        # Middle-center cards keep their y fraction via \pos (MarginV only
        # anchors top/bottom alignments).
        _pos = ""
        if _ts["align"] == 5 and abs(_ts["y_frac"] - 0.5) > 0.02:
            _pos = "{\\pos(%d,%d)}" % (pw // 2, int(ph * _ts["y_frac"]))
        ent = _headline_entrance(ov.get("entrance", _ts["entrance"]), pw, ph,
                                 _mv, _ts["align"],
                                 enter_ms=ov.get("enter_ms", _ts["enter_ms"]),
                                 exit_ms=ov.get("exit_ms", _ts["exit_ms"]))
        lines.append(f"Dialogue: 1,{_ts_ass(ov['start'])},{_ts_ass(ov['end'])},"
                     f"{_sname},,0,0,0,,{_pos}{ent}{' '.join(parts)}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")
    return path


def youtube_suggest(analysis, scenes, timings):
    """Titles, description, chapters, tags, thumbnail text from the real script data."""
    kws = []
    for s in analysis.sentences:
        kws += s.keywords
    from collections import Counter
    top = [w for w, _ in Counter(kws).most_common(6)]
    topic = analysis.topic.replace("_", " ").title()
    base = " ".join(w.capitalize() for w in top[:4])
    titles = [
        f"{base} Explained: What You Need to Know",
        f"The Truth About {topic} ({base})",
        f"{base} | {topic} Documentary",
    ]
    desc = (f"{base}.\n\nIn this video we break down {topic.lower()} step by step.\n\n"
            f"Topics covered: {', '.join(top)}.\n")
    chapters = []
    for sc, t in zip(scenes, timings):
        m, sec = int(t["start"] // 60), int(t["start"] % 60)
        chapters.append(f"{m:02d}:{sec:02d} - {sc.text[:60]}")
    tags = list(dict.fromkeys(top + [analysis.topic, "documentary", "explainer"]))
    thumb = make_overlay_text(analysis.sentences[0], max_words=4) if analysis.sentences else topic.upper()
    return {"titles": titles, "description": desc, "chapters": chapters,
            "tags": tags, "thumbnail_text": thumb}
