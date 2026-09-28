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
