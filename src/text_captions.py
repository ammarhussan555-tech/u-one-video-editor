"""On-screen text overlays + captions (SRT + styled ASS).

Overlays are rendered through libass (a top-positioned ASS style) instead of
FFmpeg drawtext, so long text, Unicode, commas, colons, quotes and
apostrophes can never break a filter graph.
"""
import os
import re

from .ffmpeg_util import sanitize_ass_text


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
              overlays=None, play_res=(1920, 1080)):
    """Build the ASS file: bottom captions + optional top overlays.

    overlays: list of dicts {text, start, end}. They use the "Top" style
    (alignment 8 = top-center) so no drawtext filter is ever needed.
    """
    pw, ph = play_res
    cap_size = max(28, int(ph * font_size / 1080))
    top_size = max(30, int(ph / 20))
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: %d
PlayResY: %d
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial,%d,&H00FFFFFF,&H000019FF,&H80000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,40,40,60,1
Style: Top,Arial,%d,&H00FFFFFF,&H000019FF,&H90000000,&H90000000,-1,0,0,0,100,100,0,0,1,3,1,8,60,60,60,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" % (pw, ph, cap_size, top_size)
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
