"""On-screen text overlays + captions (SRT + styled ASS).

Overlays are rendered through libass (a top-positioned ASS style) instead of
FFmpeg drawtext, so long text, Unicode, commas, colons, quotes and
apostrophes can never break a filter graph.
"""
import os
import re

from .ffmpeg_util import sanitize_ass_text


# ---------------------------------------------------------------------------
# Caption templates (CapCut viral/trending styles).
# Each template defines an ASS "Cap" style. Colours are ASS &HAABBGGRR.
# size_scale multiplies the base font size. box=True draws an opaque
# background box behind the text (BorderStyle 3).
# ---------------------------------------------------------------------------
CAPTION_TEMPLATES = {
    "tiktok_classic": {
        "label": "TikTok Classic",
        "desc": "White bold text, black outline — the standard viral look",
        "font": "Arial", "size_scale": 1.0,
        "primary": "&H00FFFFFF", "secondary": "&H000019FF",
        "outline_c": "&H80000000", "back_c": "&H80000000",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "karaoke_pop": {
        "label": "Karaoke Pop",
        "desc": "Word-by-word yellow highlight — super viral on Reels/TikTok",
        "font": "Arial", "size_scale": 1.1,
        "primary": "&H00FFFFFF", "secondary": "&H0000D7FF",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": -1, "italic": 0, "outline": 3, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "hormozi_box": {
        "label": "Hormozi Box",
        "desc": "White text on solid black box — Alex Hormozi trending style",
        "font": "Arial", "size_scale": 0.95,
        "primary": "&H00FFFFFF", "secondary": "&H000019FF",
        "outline_c": "&H00000000", "back_c": "&HC8000000",
        "bold": -1, "italic": 0, "outline": 1, "shadow": 0,
        "alignment": 2, "box": True,
    },
    "neon_glow": {
        "label": "Neon Glow",
        "desc": "Cyan neon glow — eye-catching night/gaming vibe",
        "font": "Arial", "size_scale": 1.0,
        "primary": "&H00FFFF00", "secondary": "&H0000FF00",
        "outline_c": "&H80000000", "back_c": "&H80000000",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "bold_impact": {
        "label": "Bold Impact",
        "desc": "Extra large, thick outline — maximum readability",
        "font": "Arial Black", "size_scale": 1.35,
        "primary": "&H00FFFFFF", "secondary": "&H000019FF",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": -1, "italic": 0, "outline": 4, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "minimal_clean": {
        "label": "Minimal Clean",
        "desc": "Small, subtle — for cinematic/documentary feel",
        "font": "Arial", "size_scale": 0.7,
        "primary": "&H00FFFFFF", "secondary": "&H000019FF",
        "outline_c": "&H80000000", "back_c": "&H80000000",
        "bold": 0, "italic": 0, "outline": 1, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "fire": {
        "label": "Fire",
        "desc": "Yellow text, red glow — high energy style",
        "font": "Arial", "size_scale": 1.1,
        "primary": "&H0000D7FF", "secondary": "&H000000FF",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": -1, "italic": 0, "outline": 3, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "inverted": {
        "label": "Inverted",
        "desc": "Black text, white outline — trendy inverted look",
        "font": "Arial", "size_scale": 1.0,
        "primary": "&H00000000", "secondary": "&H00FFFFFF",
        "outline_c": "&H80FFFFFF", "back_c": "&H80FFFFFF",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "mrbeast": {
        "label": "MrBeast",
        "desc": "Huge bold blue-yellow energy — MrBeast thumbnail vibe",
        "font": "Arial Black", "size_scale": 1.3,
        "primary": "&H0000D7FF", "secondary": "&H0000FFFF",
        "outline_c": "&H901A4D8F", "back_c": "&H901A4D8F",
        "bold": -1, "italic": 0, "outline": 4, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "meme_impact": {
        "label": "Meme Impact",
        "desc": "Classic meme font — white Impact with heavy black outline",
        "font": "Impact", "size_scale": 1.15,
        "primary": "&H00FFFFFF", "secondary": "&H000019FF",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": 0, "italic": 0, "outline": 4, "shadow": 0,
        "alignment": 2, "box": False,
    },
    "glitch_rgb": {
        "label": "Glitch RGB",
        "desc": "Cyan/red chromatic vibe — edgy glitch aesthetic",
        "font": "Arial", "size_scale": 1.05,
        "primary": "&H00FF00FF", "secondary": "&H0000FFFF",
        "outline_c": "&H80000000", "back_c": "&H80000000",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 3,
        "alignment": 2, "box": False,
    },
    "gold_luxury": {
        "label": "Gold Luxury",
        "desc": "Gold text, dark outline — premium finance/luxury feel",
        "font": "Georgia", "size_scale": 1.0,
        "primary": "&H0000D7FF", "secondary": "&H0000FFFF",
        "outline_c": "&H901A1A1A", "back_c": "&H901A1A1A",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "news_ticker": {
        "label": "News Ticker",
        "desc": "White on red box — breaking-news urgency",
        "font": "Arial", "size_scale": 0.9,
        "primary": "&H00FFFFFF", "secondary": "&H000019FF",
        "outline_c": "&H000000FF", "back_c": "&HC80000FF",
        "bold": -1, "italic": 0, "outline": 1, "shadow": 0,
        "alignment": 2, "box": True,
    },
    "bubble_gum": {
        "label": "Bubble Gum",
        "desc": "Pink playful — fun lifestyle/beauty vibe",
        "font": "Comic Sans MS", "size_scale": 1.05,
        "primary": "&H00C080FF", "secondary": "&H000080FF",
        "outline_c": "&H905A1A5A", "back_c": "&H905A1A5A",
        "bold": -1, "italic": 0, "outline": 3, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "retro_wave": {
        "label": "Retro Wave",
        "desc": "Orange retro — 80s synthwave nostalgia",
        "font": "Verdana", "size_scale": 1.0,
        "primary": "&H000080FF", "secondary": "&H000000FF",
        "outline_c": "&H90400040", "back_c": "&H90400040",
        "bold": -1, "italic": 1, "outline": 2, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "outline_hollow": {
        "label": "Outline Hollow",
        "desc": "Hollow text, thick colored outline — modern minimal",
        "font": "Arial Black", "size_scale": 1.2,
        "primary": "&H00000000", "secondary": "&H000019FF",
        "outline_c": "&H0000FFFF", "back_c": "&H0000FFFF",
        "bold": -1, "italic": 0, "outline": 3, "shadow": 0,
        "alignment": 2, "box": False,
    },
    "typewriter": {
        "label": "Typewriter",
        "desc": "Monospace retro — documentary/storytime feel",
        "font": "Courier New", "size_scale": 0.95,
        "primary": "&H00E0E0E0", "secondary": "&H000019FF",
        "outline_c": "&H80000000", "back_c": "&H80000000",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "sticker_pop": {
        "label": "Sticker Pop",
        "desc": "Thick white sticker border — playful CapCut sticker look",
        "font": "Arial", "size_scale": 1.1,
        "primary": "&H00000000", "secondary": "&H000019FF",
        "outline_c": "&H00FFFFFF", "back_c": "&H00FFFFFF",
        "bold": -1, "italic": 0, "outline": 5, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "ocean_calm": {
        "label": "Ocean Calm",
        "desc": "Soft teal — calm travel/wellness vibe",
        "font": "Trebuchet MS", "size_scale": 0.95,
        "primary": "&H00D0E8E0", "secondary": "&H000019FF",
        "outline_c": "&H80304030", "back_c": "&H80304030",
        "bold": 0, "italic": 0, "outline": 2, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "purple_haze": {
        "label": "Purple Haze",
        "desc": "Purple glow — dreamy aesthetic edits",
        "font": "Arial", "size_scale": 1.05,
        "primary": "&H00E080FF", "secondary": "&H008000FF",
        "outline_c": "&H80400080", "back_c": "&H80400080",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 2,
        "alignment": 2, "box": False,
    },
    # --- Karaoke variations ---
    "karaoke_blue": {
        "label": "Karaoke Blue",
        "desc": "Word-by-word blue highlight karaoke",
        "font": "Arial", "size_scale": 1.1,
        "primary": "&H00FFFFFF", "secondary": "&H00FF0000",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": -1, "italic": 0, "outline": 3, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "karaoke_green": {
        "label": "Karaoke Green",
        "desc": "Word-by-word green highlight karaoke",
        "font": "Arial", "size_scale": 1.1,
        "primary": "&H00FFFFFF", "secondary": "&H0000FF00",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": -1, "italic": 0, "outline": 3, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "karaoke_pink": {
        "label": "Karaoke Pink",
        "desc": "Word-by-word pink highlight karaoke",
        "font": "Arial", "size_scale": 1.1,
        "primary": "&H00FFFFFF", "secondary": "&H00C080FF",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": -1, "italic": 0, "outline": 3, "shadow": 1,
        "alignment": 2, "box": False,
    },
    # --- Box variations ---
    "white_box": {
        "label": "White Box",
        "desc": "Black text on white box — inverted Hormozi",
        "font": "Arial", "size_scale": 0.95,
        "primary": "&H00000000", "secondary": "&H00FFFFFF",
        "outline_c": "&H00FFFFFF", "back_c": "&HC8FFFFFF",
        "bold": -1, "italic": 0, "outline": 1, "shadow": 0,
        "alignment": 2, "box": True,
    },
    "yellow_box": {
        "label": "Yellow Box",
        "desc": "Black text on yellow box — highlighter energy",
        "font": "Arial", "size_scale": 0.95,
        "primary": "&H00000000", "secondary": "&H00000000",
        "outline_c": "&H0000FFFF", "back_c": "&HC800FFFF",
        "bold": -1, "italic": 0, "outline": 1, "shadow": 0,
        "alignment": 2, "box": True,
    },
    "blue_box": {
        "label": "Blue Box",
        "desc": "White text on blue box — clean corporate",
        "font": "Arial", "size_scale": 0.95,
        "primary": "&H00FFFFFF", "secondary": "&H000019FF",
        "outline_c": "&H00FF0000", "back_c": "&HC8000000",
        "bold": -1, "italic": 0, "outline": 1, "shadow": 0,
        "alignment": 2, "box": True,
    },
    "green_box": {
        "label": "Green Box",
        "desc": "White text on green box — fresh/eco vibe",
        "font": "Arial", "size_scale": 0.95,
        "primary": "&H00FFFFFF", "secondary": "&H000019FF",
        "outline_c": "&H00008000", "back_c": "&HC8008000",
        "bold": -1, "italic": 0, "outline": 1, "shadow": 0,
        "alignment": 2, "box": True,
    },
    # --- Color themes ---
    "crimson_red": {
        "label": "Crimson Red",
        "desc": "Bold red — urgency, alerts, drama",
        "font": "Arial", "size_scale": 1.1,
        "primary": "&H000000FF", "secondary": "&H000080FF",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": -1, "italic": 0, "outline": 3, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "lime_punch": {
        "label": "Lime Punch",
        "desc": "Electric lime — fresh energetic pop",
        "font": "Arial Black", "size_scale": 1.1,
        "primary": "&H0000FF80", "secondary": "&H0000FF00",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": -1, "italic": 0, "outline": 3, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "sky_blue": {
        "label": "Sky Blue",
        "desc": "Bright sky blue — friendly, open feel",
        "font": "Arial", "size_scale": 1.0,
        "primary": "&H00FFC040", "secondary": "&H00FF0000",
        "outline_c": "&H80304010", "back_c": "&H80304010",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "sunset_orange": {
        "label": "Sunset Orange",
        "desc": "Warm sunset orange — travel/golden hour",
        "font": "Arial", "size_scale": 1.05,
        "primary": "&H000080FF", "secondary": "&H000000FF",
        "outline_c": "&H80402000", "back_c": "&H80402000",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "cotton_candy": {
        "label": "Cotton Candy",
        "desc": "Soft pink-white — sweet aesthetic",
        "font": "Comic Sans MS", "size_scale": 1.0,
        "primary": "&H00E0B0FF", "secondary": "&H00C080FF",
        "outline_c": "&H80604060", "back_c": "&H80604060",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "midnight": {
        "label": "Midnight",
        "desc": "Deep blue glow — night cinematic",
        "font": "Arial", "size_scale": 1.0,
        "primary": "&H00C0A060", "secondary": "&H00FF0000",
        "outline_c": "&H80102040", "back_c": "&H80102040",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "royal": {
        "label": "Royal",
        "desc": "Purple and gold — premium royal feel",
        "font": "Georgia", "size_scale": 1.0,
        "primary": "&H00D0A0FF", "secondary": "&H0000D7FF",
        "outline_c": "&H80400060", "back_c": "&H80400060",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 2,
        "alignment": 2, "box": False,
    },
    # --- Font styles ---
    "handwritten": {
        "label": "Handwritten",
        "desc": "Casual handwritten — personal vlog feel",
        "font": "Segoe Script", "size_scale": 1.0,
        "primary": "&H00FFFFFF", "secondary": "&H000019FF",
        "outline_c": "&H80000000", "back_c": "&H80000000",
        "bold": 0, "italic": 0, "outline": 2, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "elegant_serif": {
        "label": "Elegant Serif",
        "desc": "Classic serif italic — sophisticated docs",
        "font": "Times New Roman", "size_scale": 0.95,
        "primary": "&H00F0E8D0", "secondary": "&H000019FF",
        "outline_c": "&H80202020", "back_c": "&H80202020",
        "bold": 0, "italic": -1, "outline": 1, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "chunky": {
        "label": "Chunky",
        "desc": "Extra thick rounded — playful bold",
        "font": "Arial Black", "size_scale": 1.2,
        "primary": "&H00FFFFFF", "secondary": "&H000019FF",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": -1, "italic": 0, "outline": 5, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "slim_thin": {
        "label": "Slim Thin",
        "desc": "Thin elegant — fashion/luxury minimal",
        "font": "Segoe UI", "size_scale": 0.85,
        "primary": "&H00FFFFFF", "secondary": "&H000019FF",
        "outline_c": "&H80000000", "back_c": "&H80000000",
        "bold": 0, "italic": 0, "outline": 1, "shadow": 1,
        "alignment": 2, "box": False,
    },
    # --- Glow effects ---
    "glow_white": {
        "label": "Glow White",
        "desc": "Soft white glow — angelic clean",
        "font": "Arial", "size_scale": 1.0,
        "primary": "&H00FFFFFF", "secondary": "&H00FFFFFF",
        "outline_c": "&H80FFFFFF", "back_c": "&H80FFFFFF",
        "bold": -1, "italic": 0, "outline": 1, "shadow": 3,
        "alignment": 2, "box": False,
    },
    "glow_pink": {
        "label": "Glow Pink",
        "desc": "Pink neon glow — nightclub vibe",
        "font": "Arial", "size_scale": 1.05,
        "primary": "&H00C080FF", "secondary": "&H00FF00FF",
        "outline_c": "&H80500050", "back_c": "&H80500050",
        "bold": -1, "italic": 0, "outline": 1, "shadow": 3,
        "alignment": 2, "box": False,
    },
    "glow_green": {
        "label": "Glow Green",
        "desc": "Matrix green glow — hacker/tech",
        "font": "Courier New", "size_scale": 1.0,
        "primary": "&H0000FF00", "secondary": "&H00008000",
        "outline_c": "&H80004000", "back_c": "&H80004000",
        "bold": -1, "italic": 0, "outline": 1, "shadow": 3,
        "alignment": 2, "box": False,
    },
    "heavy_shadow": {
        "label": "Heavy Shadow",
        "desc": "Deep drop shadow — 3D pop effect",
        "font": "Arial Black", "size_scale": 1.15,
        "primary": "&H00FFFFFF", "secondary": "&H000019FF",
        "outline_c": "&H80000000", "back_c": "&H80000000",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 4,
        "alignment": 2, "box": False,
    },
    # --- Trending niche styles ---
    "podcast_clip": {
        "label": "Podcast Clip",
        "desc": "Bold white bottom captions — podcast viral clips",
        "font": "Arial", "size_scale": 1.15,
        "primary": "&H00FFFFFF", "secondary": "&H0000D7FF",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": -1, "italic": 0, "outline": 3, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "reddit_story": {
        "label": "Reddit Story",
        "desc": "Clean white on dark — story narration style",
        "font": "Verdana", "size_scale": 0.9,
        "primary": "&H00FFFFFF", "secondary": "&H000019FF",
        "outline_c": "&H80000000", "back_c": "&H80000000",
        "bold": 0, "italic": 0, "outline": 2, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "motivation": {
        "label": "Motivation",
        "desc": "Bold condensed — gym/mindset grindset",
        "font": "Impact", "size_scale": 1.25,
        "primary": "&H00FFFFFF", "secondary": "&H0000FFFF",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": 0, "italic": 0, "outline": 3, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "money_green": {
        "label": "Money Green",
        "desc": "Cash green — finance/crypto flex",
        "font": "Arial Black", "size_scale": 1.1,
        "primary": "&H0000C000", "secondary": "&H0000FF00",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": -1, "italic": 0, "outline": 3, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "anime_pop": {
        "label": "Anime Pop",
        "desc": "Playful bold — anime edit energy",
        "font": "Arial Black", "size_scale": 1.15,
        "primary": "&H00FFFFFF", "secondary": "&H00FF00FF",
        "outline_c": "&H90800000", "back_c": "&H90800000",
        "bold": -1, "italic": 1, "outline": 3, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "kpop_cute": {
        "label": "K-Pop Cute",
        "desc": "Soft pink rounded — k-pop fan edits",
        "font": "Comic Sans MS", "size_scale": 1.0,
        "primary": "&H00FFD0E8", "secondary": "&H00C080FF",
        "outline_c": "&H80605070", "back_c": "&H80605070",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "gaming_neon": {
        "label": "Gaming Neon",
        "desc": "Neon green on dark — esports/highlights",
        "font": "Arial Black", "size_scale": 1.1,
        "primary": "&H0000FF00", "secondary": "&H0000FFFF",
        "outline_c": "&H90002000", "back_c": "&H90002000",
        "bold": -1, "italic": 1, "outline": 3, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "horror_red": {
        "label": "Horror Red",
        "desc": "Dark blood red — scary stories",
        "font": "Georgia", "size_scale": 1.05,
        "primary": "&H00000080", "secondary": "&H000000FF",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 3,
        "alignment": 2, "box": False,
    },
    "luxury_black": {
        "label": "Luxury Black",
        "desc": "Gold on black box — ultra premium",
        "font": "Georgia", "size_scale": 0.95,
        "primary": "&H0000D7FF", "secondary": "&H0000FFFF",
        "outline_c": "&H00000000", "back_c": "&HE0000000",
        "bold": -1, "italic": 0, "outline": 1, "shadow": 0,
        "alignment": 2, "box": True,
    },
    "streetwear": {
        "label": "Streetwear",
        "desc": "Bold urban — street culture edits",
        "font": "Impact", "size_scale": 1.2,
        "primary": "&H00FFFFFF", "secondary": "&H000000FF",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": 0, "italic": 1, "outline": 4, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "vintage_film": {
        "label": "Vintage Film",
        "desc": "Cream sepia — old film nostalgia",
        "font": "Courier New", "size_scale": 0.9,
        "primary": "&H00D8C8A8", "secondary": "&H000019FF",
        "outline_c": "&H80403020", "back_c": "&H80403020",
        "bold": 0, "italic": 0, "outline": 1, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "cyberpunk": {
        "label": "Cyberpunk",
        "desc": "Magenta/cyan clash — futuristic dystopia",
        "font": "Arial Black", "size_scale": 1.1,
        "primary": "&H00FF00FF", "secondary": "&H00FFFF00",
        "outline_c": "&H90200040", "back_c": "&H90200040",
        "bold": -1, "italic": 1, "outline": 3, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "ice": {
        "label": "Ice",
        "desc": "Frosty blue-white — winter/clean",
        "font": "Arial", "size_scale": 1.0,
        "primary": "&H00FFF8E8", "secondary": "&H00FFC040",
        "outline_c": "&H80608080", "back_c": "&H80608080",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "lava": {
        "label": "Lava",
        "desc": "Molten red-orange — intense energy",
        "font": "Arial Black", "size_scale": 1.15,
        "primary": "&H000040FF", "secondary": "&H000000FF",
        "outline_c": "&H90400000", "back_c": "&H90400000",
        "bold": -1, "italic": 0, "outline": 3, "shadow": 3,
        "alignment": 2, "box": False,
    },
    "toxic": {
        "label": "Toxic",
        "desc": "Acid green glow — edgy underground",
        "font": "Impact", "size_scale": 1.1,
        "primary": "&H0000FF40", "secondary": "&H0000FF00",
        "outline_c": "&H90004000", "back_c": "&H90004000",
        "bold": 0, "italic": 0, "outline": 2, "shadow": 3,
        "alignment": 2, "box": False,
    },
    "chrome": {
        "label": "Chrome",
        "desc": "Silver metallic — sleek tech",
        "font": "Arial Black", "size_scale": 1.05,
        "primary": "&H00D8D8D8", "secondary": "&H00FFFFFF",
        "outline_c": "&H80606060", "back_c": "&H80606060",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "pastel": {
        "label": "Pastel",
        "desc": "Soft pastel tones — gentle aesthetic",
        "font": "Segoe UI", "size_scale": 0.95,
        "primary": "&H00F0D8E8", "secondary": "&H000019FF",
        "outline_c": "&H80708080", "back_c": "&H80708080",
        "bold": 0, "italic": 0, "outline": 1, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "highlighter": {
        "label": "Highlighter",
        "desc": "Marker highlight — study/notes vibe",
        "font": "Segoe Script", "size_scale": 1.0,
        "primary": "&H00000000", "secondary": "&H00000000",
        "outline_c": "&H0000FFFF", "back_c": "&H9600FFFF",
        "bold": 0, "italic": 0, "outline": 1, "shadow": 0,
        "alignment": 2, "box": True,
    },
    "chalk": {
        "label": "Chalk",
        "desc": "Chalkboard rough — classroom explainers",
        "font": "Comic Sans MS", "size_scale": 1.0,
        "primary": "&H00F0F0F0", "secondary": "&H000019FF",
        "outline_c": "&H80404040", "back_c": "&H80404040",
        "bold": 0, "italic": 0, "outline": 1, "shadow": 0,
        "alignment": 2, "box": False,
    },
    "gold_rush": {
        "label": "Gold Rush",
        "desc": "Shiny gold pop — winner/celebration",
        "font": "Arial Black", "size_scale": 1.2,
        "primary": "&H0000D7FF", "secondary": "&H0000FFFF",
        "outline_c": "&H90602000", "back_c": "&H90602000",
        "bold": -1, "italic": 0, "outline": 3, "shadow": 3,
        "alignment": 2, "box": False,
    },
    "deep_ocean": {
        "label": "Deep Ocean",
        "desc": "Dark teal glow — mysterious deep",
        "font": "Verdana", "size_scale": 1.0,
        "primary": "&H00808040", "secondary": "&H00FFFF00",
        "outline_c": "&H80402010", "back_c": "&H80402010",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "candy_pop": {
        "label": "Candy Pop",
        "desc": "Bright multicolor pop — fun kids content",
        "font": "Comic Sans MS", "size_scale": 1.1,
        "primary": "&H0000FFFF", "secondary": "&H00FF00FF",
        "outline_c": "&H90600060", "back_c": "&H90600060",
        "bold": -1, "italic": 0, "outline": 3, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "noir": {
        "label": "Noir",
        "desc": "Black and white film — dramatic cinema",
        "font": "Times New Roman", "size_scale": 1.0,
        "primary": "&H00E8E8E8", "secondary": "&H000019FF",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "electric": {
        "label": "Electric",
        "desc": "Crackling electric blue — high voltage",
        "font": "Arial Black", "size_scale": 1.15,
        "primary": "&H00FF8000", "secondary": "&H00FFFF00",
        "outline_c": "&H90400000", "back_c": "&H90400000",
        "bold": -1, "italic": 1, "outline": 3, "shadow": 3,
        "alignment": 2, "box": False,
    },
    "soft_peach": {
        "label": "Soft Peach",
        "desc": "Warm peach — cozy lifestyle",
        "font": "Segoe UI", "size_scale": 0.95,
        "primary": "&H00C8A8FF", "secondary": "&H000080FF",
        "outline_c": "&H80705050", "back_c": "&H80705050",
        "bold": 0, "italic": 0, "outline": 1, "shadow": 1,
        "alignment": 2, "box": False,
    },
    "matrix_code": {
        "label": "Matrix Code",
        "desc": "Digital rain green — cyber hacker",
        "font": "Courier New", "size_scale": 1.0,
        "primary": "&H0000FF00", "secondary": "&H0000C000",
        "outline_c": "&H90000000", "back_c": "&H90000000",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 2,
        "alignment": 2, "box": False,
    },
    "sunset_gradient": {
        "label": "Sunset Glow",
        "desc": "Warm gradient glow — golden content",
        "font": "Arial", "size_scale": 1.1,
        "primary": "&H0000C8FF", "secondary": "&H000080FF",
        "outline_c": "&H80603000", "back_c": "&H80603000",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 3,
        "alignment": 2, "box": False,
    },
    "bold_minimal": {
        "label": "Bold Minimal",
        "desc": "Simple bold white — clean modern default",
        "font": "Segoe UI", "size_scale": 1.0,
        "primary": "&H00FFFFFF", "secondary": "&H000019FF",
        "outline_c": "&H80000000", "back_c": "&H80000000",
        "bold": -1, "italic": 0, "outline": 2, "shadow": 1,
        "alignment": 2, "box": False,
    },
}

DEFAULT_CAPTION_TEMPLATE = "tiktok_classic"


def caption_template_labels():
    """Ordered (key, label) pairs for UI dropdowns."""
    return [(k, v["label"]) for k, v in CAPTION_TEMPLATES.items()]


def make_overlay_text(analysis, max_words=5):
    """Short punchy overlay, never the whole sentence."""
    parts = []
    for p in analysis.phrases or []:
        for w in p.split():
            if w.lower() not in [x.lower() for x in parts]:
                parts.append(w)
    for k in analysis.keywords or []:
        if k.lower() not in [x.lower() for x in parts]:
            parts.append(k)
    return " ".join(parts[:max_words]).upper()


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


def build_srt(sentence_timings, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, s in enumerate(sentence_timings, 1):
            f.write(f"{i}\n{_ts_srt(s['start'])} --> {_ts_srt(s['end'])}\n{s['text']}\n\n")
    return path


def build_ass(sentence_timings, word_timings, path, font_size=48, highlight=True,
              overlays=None, play_res=(1920, 1080), template=None):
    """Build the ASS file: bottom captions + optional top overlays.

    overlays: list of dicts {text, start, end}. They use the "Top" style
    (alignment 8 = top-center) so no drawtext filter is ever needed.
    template: key from CAPTION_TEMPLATES (CapCut-style caption look).
    """
    tmpl = CAPTION_TEMPLATES.get(template or DEFAULT_CAPTION_TEMPLATE,
                                 CAPTION_TEMPLATES[DEFAULT_CAPTION_TEMPLATE])
    pw, ph = play_res
    cap_size = max(24, int(ph * font_size / 1080 * tmpl["size_scale"]))
    top_size = max(30, int(ph / 20))
    # BorderStyle 3 = opaque box behind text (Hormozi style), else 1 = outline.
    border_style = 3 if tmpl["box"] else 1
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: %d
PlayResY: %d
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,%s,%d,%s,%s,%s,%s,%d,%d,0,0,100,100,0,0,%d,%d,%d,%d,40,40,60,1
Style: Top,Arial,%d,&H00FFFFFF,&H000019FF,&H90000000,&H90000000,-1,0,0,0,100,100,0,0,1,3,1,8,60,60,60,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" % (pw, ph, tmpl["font"], cap_size, tmpl["primary"], tmpl["secondary"],
       tmpl["outline_c"], tmpl["back_c"], tmpl["bold"], tmpl["italic"],
       border_style, tmpl["outline"], tmpl["shadow"], tmpl["alignment"],
       top_size)
    lines = []
    if highlight and word_timings:
        # karaoke-style per-word highlight
        words = list(word_timings)
        wi = 0
        for s in sentence_timings:
            seg = []
            while wi < len(words) and words[wi]["end"] <= s["end"] + 0.05:
                seg.append(words[wi])
                wi += 1
            if not seg:
                seg = [{"word": s["text"], "start": s["start"], "end": s["end"]}]
            txt = "".join("{\\k%d}%s " % (max(1, int((w["end"] - w["start"]) * 100)),
                                          sanitize_ass_text(w["word"]))
                          for w in seg)
            lines.append(f"Dialogue: 0,{_ts_ass(s['start'])},{_ts_ass(s['end'])},Cap,,0,0,0,,{txt.strip()}")
    else:
        for s in sentence_timings:
            safe = sanitize_ass_text(s["text"])
            lines.append(f"Dialogue: 0,{_ts_ass(s['start'])},{_ts_ass(s['end'])},Cap,,0,0,0,,{safe}")
    for ov in overlays or []:
        txt = sanitize_ass_text(ov["text"])
        if not txt.strip():
            continue
        lines.append(f"Dialogue: 1,{_ts_ass(ov['start'])},{_ts_ass(ov['end'])},Top,,0,0,0,,{txt}")
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
