"""Documentary 1-click mode (Uzair 2026-10-04).

Script-driven documentary overlays for ANY niche / ANY country -- nothing is
hard-coded to one topic. Derived from his benchmark: a fully AI-made
documentary with chapter markers ("01 | THE RISE"), an animated location map
with pin, red-accent quote captions, news-style lower thirds and AI voiceover.

Pipeline position: optional post stage AFTER the caption burn, before the
final MP4 validation. Default OFF -- the OFF path is untouched.

Graphics are generated with PIL (no paid assets); OSM/Nominatim are used
keyless with caching, and EVERY network failure degrades to a graceful skip.
Out of scope (honest limit): AI-generated 3D scenes and custom After-Effects
motion graphics -- those need a render farm, not this module.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .app_paths import fonts_dir
from .script_analyzer import analyze_sentence, split_sentences, STOPWORDS

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_OSM_TILE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
_UA = {"User-Agent": "U-One/1.0 (documentary mode; contact: local user)"}

RED = (225, 17, 17)
YELLOW = (245, 180, 0)
WHITE = (255, 255, 255)
DARK = (16, 16, 16)


# --------------------------------------------------------------------------
# Plan dataclasses
# --------------------------------------------------------------------------
@dataclass
class Chapter:
    index: int          # 1-based -> rendered as "01"
    title: str          # <= 4 words, ALL CAPS
    start: float        # seconds in final video
    dur: float = 3.0


@dataclass
class Quote:
    text: str
    hero_words: list = field(default_factory=list)
    start: float = 0.0
    end: float = 0.0


@dataclass
class Location:
    name: str
    lat: float
    lon: float
    start: float
    dur: float = 3.0


@dataclass
class LowerThird:
    kicker: str
    headline: str
    date: str
    start: float
    dur: float = 4.0


@dataclass
class DocumentaryPlan:
    chapters: list = field(default_factory=list)
    quotes: list = field(default_factory=list)
    locations: list = field(default_factory=list)
    lower_thirds: list = field(default_factory=list)

    def has_any(self):
        return bool(self.chapters or self.quotes or self.locations
                    or self.lower_thirds)


# --------------------------------------------------------------------------
# Script analysis -> fractions of the script
# --------------------------------------------------------------------------
def _meaningful_words(text, n):
    out = []
    for w in re.findall(r"[A-Za-z']+", text):
        wl = w.lower()
        if wl not in STOPWORDS and len(w) > 2 and wl not in out:
            out.append(w.upper())
        if len(out) >= n:
            break
    return out


def _split_sections(script_text):
    """Split the script into (start_char, end_char) sections.

    Headings (short lines that are ALL CAPS, numbered, or end with ':')
    start new chapters; otherwise blank-line paragraphs are sections.
    """
    text = (script_text or "").strip()
    if not text:
        return []
    lines = text.splitlines()
    bounds, cur_start, cur_len = [], 0, 0
    for i, ln in enumerate(lines):
        s = ln.strip()
        is_heading = bool(s) and (
            len(s) <= 60 and (s.isupper() or re.match(r"^\d{1,2}[.)]\s", s)
                              or s.endswith(":")))
        if is_heading and cur_len > 0:
            bounds.append((cur_start, i))
            cur_start, cur_len = i, 0
        if s:
            cur_len += 1
    bounds.append((cur_start, len(lines)))
    # Convert line ranges to char offsets.
    line_off = []
    off = 0
    for ln in lines:
        line_off.append(off)
        off += len(ln) + 1
    line_off.append(off)  # sentinel so b == len(lines) is valid
    paras = [(line_off[a], line_off[b] - 1) for a, b in bounds
             if "\n".join(lines[a:b]).strip()]
    # Too few sections -> time-proportional fallback (2..6 sections).
    if len(paras) < 2:
        words = text.split()
        n = max(2, min(6, max(1, len(words) // 150)))
        wi = [m.start() for m in re.finditer(r"\S+", text)]
        paras = []
        for k in range(n):
            a = wi[k * len(wi) // n] if wi else 0
            b = wi[min(len(wi) - 1, (k + 1) * len(wi) // n)] if wi else len(text)
            if b > a:
                paras.append((a, b))
        return paras
    return paras


def _section_title(section_text):
    lines = [ln.strip() for ln in section_text.splitlines() if ln.strip()]
    if lines:
        head = lines[0]
        if len(head) <= 60 and (head.isupper() or re.match(r"^\d{1,2}[.)]\s", head)):
            # An explicit heading is used verbatim (e.g. "01 THE RISE").
            words = re.findall(r"[A-Za-z']+", head)[:4]
            if words:
                return " ".join(w.upper() for w in words)
    sents = split_sentences(section_text)
    first = sents[0] if sents else section_text
    words = _meaningful_words(first, 4)
    if not words:
        words = _meaningful_words(section_text, 4)
    return " ".join(words[:4]) or "STORY"


def analyze_script(script_text):
    """Chapters as (start_fraction, title). Niche-agnostic."""
    text = (script_text or "").strip()
    if not text:
        return []
    sections = _split_sections(text)
    total = max(1, len(text))
    out = []
    for i, (a, b) in enumerate(sections):
        sec_text = text[a:b]
        out.append((a / total, _section_title(sec_text)))
    # Order + dedupe titles.
    out.sort(key=lambda t: t[0])
    seen, final = set(), []
    for frac, title in out:
        key = title
        n = 2
        while key in seen:
            key = f"{title} {n}"
            n += 1
        seen.add(key)
        final.append((frac, key))
    return final


_INTENSITY = set(
    "shocking|unbelievable|crisis|breaking|urgent|deadly|tragic|historic|"
    "never|always|everyone|nobody|impossible|miracle|disaster|victory|"
    "betrayal|secret|warning|finally".split("|"))


def _quote_score(sentence):
    t = sentence or ""
    score = 0
    if re.search(r'[\"“”]', t):
        score += 5
    if t.rstrip().endswith(("!", "?")):
        score += 2
    lows = set(re.findall(r"[a-z']+", t.lower()))
    score += sum(1 for w in lows if w in _INTENSITY)
    nwords = len(t.split())
    if 4 <= nwords <= 22:
        score += 1
    return score


def _hero_words_for(quote_text):
    """Hero words come from the existing main-text importance engine --
    never random words."""
    try:
        from .text_captions import select_main_text
        cards = select_main_text(quote_text, duration=4.0)
        if cards:
            c0 = cards[0]
            words = list(c0.get("accent_words") or [])
            if c0.get("hero_word"):
                words.append(c0["hero_word"])
            clean = " ".join(words)
            norm = {re.sub(r"[^\w]", "", w).lower() for w in words}
            kept = [w for w in re.findall(r"[A-Za-z']+", quote_text)
                    if re.sub(r"[^\w]", "", w).lower() in norm]
            if kept:
                return kept[:2]
    except Exception:  # noqa: BLE001 - fall back below
        pass
    # Fallback: the 1-2 longest meaningful words (deterministic, not random).
    cands = [w for w in re.findall(r"[A-Za-z']+", quote_text)
             if w.lower() not in STOPWORDS and len(w) > 3]
    cands.sort(key=lambda w: (-len(w), w.lower()))
    seen, out = set(), []
    for w in cands:
        wl = w.lower()
        if wl not in seen:
            seen.add(wl)
            out.append(w)
        if len(out) == 2:
            break
    return out


def extract_quotes(script_text, max_quotes=3):
    """Most impactful short sentences / quoted speech + hero words."""
    sents = split_sentences(script_text)
    scored = [(_quote_score(s), s) for s in sents]
    scored = [(sc, s) for sc, s in scored if sc > 0]
    scored.sort(key=lambda t: -t[0])
    out = []
    for _, s in scored[:max_quotes]:
        # Prefer the quoted span itself over the whole sentence
        # ("...," he declared -> just the ... part).
        m = re.search(r'[\"“]([^\"”]{10,200})[\"”]', s)
        clean = m.group(1).strip() if m else s.strip().strip("\"'“”").strip()
        if len(clean.split()) > 22:
            clean = " ".join(clean.split()[:22])
        out.append({"text": clean, "hero_words": _hero_words_for(clean)})
    return out


def _http_json(url, timeout=12):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _geocode(name, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "loc"
    cp = os.path.join(cache_dir, f"geo_{key}.json")
    if os.path.isfile(cp):
        try:
            with open(cp, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("lat") is not None:
                return d
        except Exception:  # noqa: BLE001 - re-query below
            pass
    try:
        q = urllib.parse.urlencode({"q": name, "format": "json", "limit": 1,
                                    "addressdetails": 1})
        data = _http_json(f"{_NOMINATIM}?{q}")
        time.sleep(1.1)  # Nominatim usage policy: max 1 req/sec
        if data:
            d = {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"]),
                 "display": data[0].get("display_name", name),
                 "class": data[0].get("class", ""),
                 "type": data[0].get("type", "")}
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(d, f)
            return d
    except Exception:  # noqa: BLE001 - offline or blocked: skip
        pass
    return None


def extract_locations(script_text, cache_dir, max_locations=3):
    """Place names -> (name, lat, lon). Keyless Nominatim, cached; skips on
    any failure -- never raises."""
    try:
        phrases = []
        for s in split_sentences(script_text):
            phrases.extend(analyze_sentence(s).phrases)
        seen, cands = set(), []
        for p in phrases:
            pl = p.lower()
            if pl in seen or len(p.split()) > 3:
                continue
            seen.add(pl)
            cands.append(p)
        out = []
        for name in cands[:8]:
            g = _geocode(name, cache_dir)
            # Only real places (city/country/region): a street or venue named
            # after a person ("Imran Khan" road) is NOT a location.
            if g and g.get("class") in ("place", "boundary"):
                out.append((name, g["lat"], g["lon"]))
            if len(out) >= max_locations:
                break
        return out
    except Exception:  # noqa: BLE001 - never break a render
        return []


def _compress(text, n):
    words = text.split()
    return " ".join(words[:n])


def extract_lowerthirds(script_text, max_items=4):
    """(kicker, headline, date) news banners from people/dates/events."""
    sents = split_sentences(script_text)
    items, seen = [], set()
    for s in sents:
        if len(items) >= max_items:
            break
        a = analyze_sentence(s)
        persons = [p for p in a.phrases
                   if 1 < len(p.split()) <= 3 and p not in seen]
        dates = a.dates
        if not (persons or dates):
            continue
        kicker = persons[0].upper() if persons else "NEWS"
        headline = _compress(s, 10).upper()
        date = dates[0].upper() if dates else ""
        key = (kicker, headline)
        if key in seen:
            continue
        seen.add(key)
        items.append({"kicker": kicker, "headline": headline, "date": date,
                      "char": 0})
    # Spread evenly across the script by sentence position.
    step = max(1, len(sents) // max(1, len(items)))
    for i, it in enumerate(items):
        it["frac"] = min(0.95, (i * step + 1) / max(1, len(sents)))
    return items


# --------------------------------------------------------------------------
# Fraction -> absolute time (via word-level alignment to sentence timings)
# --------------------------------------------------------------------------
def _build_time_map(script_text, sentence_timings, total_duration):
    """Map script char offset -> seconds, using voiceover sentence timings
    when available, else linear over total_duration."""
    total = max(1, len(script_text or ""))
    if not sentence_timings or total_duration <= 0:
        return lambda c: max(0.0, min(1.0, c / total)) * max(0.0, total_duration)
    words = re.findall(r"\S+", script_text or "")
    if not words:
        return lambda c: 0.0
    # char offset of each word
    wchars = [m.start() for m in re.finditer(r"\S+", script_text)]
    # cumulative word counts per timing entry
    cum, segs = [], []
    wsum = 0
    for t in sentence_timings:
        try:
            n = len(re.findall(r"\S+", str(t.get("text", ""))))
            s, e = float(t.get("start", 0)), float(t.get("end", 0))
        except Exception:  # noqa: BLE001
            continue
        if n <= 0 or e <= s:
            continue
        segs.append((wsum, wsum + n, s, e))
        wsum += n
        cum.append(wsum)
    if not segs or wsum <= 0:
        return lambda c: max(0.0, min(1.0, c / total)) * total_duration

    def at(char_idx):
        # word index at char offset
        import bisect
        wi = max(0, bisect.bisect_right(wchars, char_idx) - 1)
        for w0, w1, s, e in segs:
            if w0 <= wi < w1:
                f = (wi - w0) / max(1, w1 - w0)
                return s + f * (e - s)
        return min(1.0, wi / wsum) * total_duration
    return at


def plan_documentary(script_text, sentence_timings, total_duration,
                     cache_dir, log=None):
    """Full plan: chapters, quotes, locations, lower thirds with times."""
    plan = DocumentaryPlan()
    text = (script_text or "").strip()
    if not text or total_duration <= 0:
        return plan
    at = _build_time_map(text, sentence_timings, total_duration)
    total = len(text)

    analyzed = analyze_script(text)
    for i, (frac, title) in enumerate(analyzed):
        start = at(int(frac * total))
        nxt = (at(int(analyzed[i + 1][0] * total)) if i + 1 < len(analyzed)
               else total_duration)
        plan.chapters.append(Chapter(index=i + 1, title=title,
                                    start=max(0.0, start),
                                    dur=min(3.5, max(2.0, (nxt - start) / 3))))

    quotes = []
    for q in extract_quotes(text):
        idx = text.find(q["text"][:30])
        start = at(max(0, idx))
        dur = max(3.0, min(6.0, len(q["text"].split()) * 0.45))
        quotes.append(Quote(text=q["text"], hero_words=q["hero_words"],
                            start=start, end=start + dur))
    # Resolve overlaps: never show two quotes at once; a quote that can't
    # fit after shifting is dropped (the earlier one wins).
    quotes.sort(key=lambda q: q.start)
    kept = []
    for q in quotes:
        dur = q.end - q.start
        if kept and q.start < kept[-1].end + 0.3:
            q.start = kept[-1].end + 0.3
            q.end = q.start + dur
        if q.end - q.start >= 2.0 and q.end <= total_duration + 0.5:
            kept.append(q)
    plan.quotes = kept

    for name, lat, lon in extract_locations(text, cache_dir):
        idx = text.find(name)
        plan.locations.append(Location(name=name, lat=lat, lon=lon,
                                       start=at(max(0, idx)), dur=3.0))

    for lt in extract_lowerthirds(text):
        start = at(int(lt["frac"] * total))
        plan.lower_thirds.append(LowerThird(kicker=lt["kicker"],
                                            headline=lt["headline"],
                                            date=lt["date"], start=start))
    if log:
        log(f"Documentary plan: {len(plan.chapters)} chapters, "
            f"{len(plan.quotes)} quotes, {len(plan.locations)} maps, "
            f"{len(plan.lower_thirds)} lower thirds.")
    return plan


# --------------------------------------------------------------------------
# PIL graphics
# --------------------------------------------------------------------------
def _font(name, size):
    try:
        fd = fonts_dir()
        p = os.path.join(str(fd), name)
        if os.path.isfile(p):
            from PIL import ImageFont
            return ImageFont.truetype(p, size)
    except Exception:  # noqa: BLE001
        pass
    try:
        from PIL import ImageFont
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except Exception:  # noqa: BLE001
        from PIL import ImageFont
        return ImageFont.load_default()


def _canvas(w, h):
    from PIL import Image
    return Image.new("RGBA", (w, h), (0, 0, 0, 0))


def _text_size(draw, text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0], bb[3] - bb[1]


def chapter_png(path, index, title, w, h):
    from PIL import ImageDraw
    img = _canvas(w, h)
    d = ImageDraw.Draw(img)
    m = int(w * 0.05)
    f_num = _font("Anton-Regular.ttf", int(h * 0.075))
    f_sep = _font("Anton-Regular.ttf", int(h * 0.06))
    num = f"{index:02d}"
    d.text((m, int(h * 0.045)), num, font=f_num, fill=YELLOW + (255,))
    nw, _ = _text_size(d, num, f_num)
    d.text((m + nw + int(w * 0.015), int(h * 0.048)), "|", font=f_sep,
           fill=WHITE + (255,))
    sw, _ = _text_size(d, "|", f_sep)
    d.text((m + nw + int(w * 0.015) + sw + int(w * 0.015), int(h * 0.048)),
           title, font=f_sep, fill=WHITE + (255,))
    img.save(path)


def quote_png(path, text, hero_words, w, h):
    from PIL import ImageDraw
    img = _canvas(w, h)
    d = ImageDraw.Draw(img)
    size = int(h * 0.062)
    font = _font("Anton-Regular.ttf", size)
    hero = {re.sub(r"[^\w]", "", x).lower() for x in hero_words}
    words = text.split()
    # Word wrap.
    max_w = int(w * 0.86)
    lines, cur = [], []
    for wd in words:
        trial = cur + [wd]
        tw = sum(_text_size(d, x + " ", font)[0] for x in trial)
        if tw > max_w and cur:
            lines.append(cur)
            cur = [wd]
        else:
            cur = trial
    if cur:
        lines.append(cur)
    lh = int(size * 1.28)
    total_h = lh * len(lines)
    y = int(h * 0.72) - total_h // 2
    for line in lines:
        widths = [_text_size(d, x + " ", font)[0] for x in line]
        x = (w - sum(widths)) // 2
        for wd, wd_w in zip(line, widths):
            is_hero = re.sub(r"[^\w]", "", wd).lower() in hero
            col = RED + (255,) if is_hero else WHITE + (255,)
            # Subtle dark outline for readability.
            for ox, oy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                d.text((x + ox, y + oy), wd, font=font, fill=(0, 0, 0, 200))
            d.text((x, y), wd, font=font, fill=col)
            x += wd_w
        y += lh
    img.save(path)


def lowerthird_png(path, kicker, headline, date, w, h):
    from PIL import ImageDraw
    img = _canvas(w, h)
    d = ImageDraw.Draw(img)
    f_k = _font("Anton-Regular.ttf", int(h * 0.032))
    f_h = _font("Anton-Regular.ttf", int(h * 0.042))
    f_d = _font("Anton-Regular.ttf", int(h * 0.028))
    m = int(w * 0.04)
    yb = int(h * 0.88)
    # Headline white strip.
    hw, hh = _text_size(d, headline, f_h)
    strip_h = hh + int(h * 0.03)
    d.rectangle([m, yb - strip_h, m + hw + int(w * 0.04), yb], fill=WHITE)
    d.text((m + int(w * 0.02), yb - strip_h + int(h * 0.014)), headline,
           font=f_h, fill=(20, 20, 20, 255))
    # Red kicker above.
    kw, kh = _text_size(d, kicker, f_k)
    d.rectangle([m, yb - strip_h - kh - int(h * 0.024), m + kw + int(w * 0.03),
                 yb - strip_h - int(h * 0.008)], fill=(200, 16, 46, 255))
    d.text((m + int(w * 0.015), yb - strip_h - kh - int(h * 0.018)), kicker,
           font=f_k, fill=WHITE + (255,))
    # Date bar below.
    if date:
        dw, dh = _text_size(d, date, f_d)
        d.rectangle([m, yb + int(h * 0.006), m + dw + int(w * 0.03),
                     yb + int(h * 0.006) + dh + int(h * 0.014)],
                    fill=(20, 20, 20, 230))
        d.text((m + int(w * 0.015), yb + int(h * 0.01)), date, font=f_d,
               fill=WHITE + (255,))
    img.save(path)


def _tile_xy(lat, lon, z):
    n = 2.0 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return x, y


def map_png(path, name, lat, lon, cache_dir, size=768, zoom=11):
    """OSM-tile map with a red pin + label. Returns True, or False when
    offline/blocked (caller skips gracefully)."""
    from PIL import Image, ImageDraw
    try:
        os.makedirs(cache_dir, exist_ok=True)
        cx, cy = _tile_xy(lat, lon, zoom)
        tiles = Image.new("RGB", (size, size), (230, 230, 230))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                tp = os.path.join(cache_dir, f"tile_{zoom}_{cx+dx}_{cy+dy}.png")
                if not os.path.isfile(tp):
                    url = _OSM_TILE.format(z=zoom, x=cx + dx, y=cy + dy)
                    req = urllib.request.Request(url, headers=_UA)
                    with urllib.request.urlopen(req, timeout=15) as r:
                        data = r.read()
                    with open(tp, "wb") as f:
                        f.write(data)
                    time.sleep(0.5)  # tile usage policy: be gentle
                t = Image.open(tp).convert("RGB")
                tiles.paste(t, ((dx + 1) * 256, (dy + 1) * 256))
        # Crop to exact location center.
        n = 2.0 ** zoom
        fx = (lon + 180.0) / 360.0 * n * 256
        lat_r = math.radians(lat)
        fy = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n * 256
        ox = int(fx - (cx - 1) * 256 - size // 2)
        oy = int(fy - (cy - 1) * 256 - size // 2)
        img = tiles.crop((ox, oy, ox + size, oy + size)).convert("RGBA")
        d = ImageDraw.Draw(img)
        # Red pin.
        px, py = size // 2, size // 2
        pr = size // 22
        d.ellipse([px - pr, py - pr * 2, px + pr, py], fill=RED + (255,))
        d.polygon([(px - pr // 2, py - pr // 3), (px + pr // 2, py - pr // 3),
                   (px, py + pr)], fill=RED + (255,))
        d.ellipse([px - pr // 3, py - pr * 1.35, px + pr // 3, py - pr * 0.65],
                  fill=WHITE + (255,))
        # Label.
        font = _font("Anton-Regular.ttf", size // 16)
        tw, th = _text_size(d, name.upper(), font)
        lx, ly = px + pr + 12, py - pr * 2 - th - 24
        lx = max(8, min(lx, size - tw - 24))
        d.rounded_rectangle([lx - 12, ly - 10, lx + tw + 12, ly + th + 10],
                            radius=10, fill=(20, 20, 20, 225))
        d.text((lx, ly), name.upper(), font=font, fill=WHITE + (255,))
        img.save(path)
        return True
    except Exception:  # noqa: BLE001 - offline/blocked: skip the map
        return False


# --------------------------------------------------------------------------
# ffmpeg burn pass
# --------------------------------------------------------------------------
def apply_documentary_overlays(plan, video_in, video_out, w, h, fps,
                               cache_dir, engine, log=None, timeout=7200):
    """Burn all planned overlays in ONE extra encode. Audio stream-copied.
    Never raises: returns False on failure (caller keeps the clean video)."""
    try:
        os.makedirs(cache_dir, exist_ok=True)
        events = []  # (kind, png_path, start, end)
        for c in plan.chapters:
            p = os.path.join(cache_dir, f"chapter_{c.index}.png")
            chapter_png(p, c.index, c.title, w, h)
            events.append(("static", p, c.start, c.start + c.dur))
        for i, q in enumerate(plan.quotes):
            p = os.path.join(cache_dir, f"quote_{i}.png")
            quote_png(p, q.text, q.hero_words, w, h)
            events.append(("static", p, q.start, q.end))
        for i, lt in enumerate(plan.lower_thirds):
            p = os.path.join(cache_dir, f"lt_{i}.png")
            lowerthird_png(p, lt.kicker, lt.headline, lt.date, w, h)
            events.append(("static", p, lt.start, lt.start + lt.dur))
        maps = []
        for i, loc in enumerate(plan.locations):
            p = os.path.join(cache_dir,
                             f"map_{re.sub(r'[^a-z0-9]+', '_', loc.name.lower())}.png")
            if map_png(p, loc.name, loc.lat, loc.lon, cache_dir):
                maps.append((p, loc.start, loc.start + loc.dur))
        if not events and not maps:
            return False

        inputs = ["-i", video_in]
        for kind, p, s, e in events:
            # -loop 1: a still PNG emits one frame otherwise, so the
            # overlay would only appear at t=0.
            inputs += ["-loop", "1", "-i", p]
        for p, s, e in maps:
            inputs += ["-loop", "1", "-i", p]

        fc, out_lbl, idx = [], "[0:v]", 1
        for kind, p, s, e in events:
            ov, nl = f"[ov{idx}]", f"[v{idx}]"
            fc.append(
                f"[{idx}:v]format=rgba,"
                f"fade=in:st={s:.2f}:d=0.4:alpha=1,"
                f"fade=out:st={max(s, e - 0.4):.2f}:d=0.4:alpha=1{ov}")
            fc.append(f"{out_lbl}{ov}overlay=0:0:"
                      f"enable='between(t,{s:.2f},{e:.2f})'{nl}")
            out_lbl, idx = nl, idx + 1
        for p, s, e in maps:
            frames = max(30, int((e - s) * fps))
            zm, nl = f"[zm{idx}]", f"[v{idx}]"
            fc.append(
                f"[{idx}:v]scale={w}:{h},"
                f"zoompan=z='min(zoom+0.0012,1.18)':d={frames}:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"s={w}x{h}:fps={fps}{zm}")
            fc.append(f"{out_lbl}{zm}overlay=0:0:"
                      f"enable='between(t,{s:.2f},{e:.2f})'{nl}")
            out_lbl, idx = nl, idx + 1

        vf = ";".join(fc) + f";{out_lbl}fps={fps},format=yuv420p[docv]"
        # Hardware encoder when available (same policy as the burn pass).
        try:
            hw = engine.detect_hw_encoder("h264")
        except Exception:  # noqa: BLE001
            hw = None
        v_args = (["-c:v", hw, "-b:v", "10M"] if hw
                  else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])
        args = [*inputs, "-filter_complex", vf,
                "-map", "[docv]", "-map", "0:a:0?",
                *v_args, "-c:a", "copy",
                "-movflags", "+faststart", "-shortest", video_out]
        engine.run(args, stage="documentary_overlays", timeout=timeout,
                   inputs=[video_in], output=video_out)
        if log:
            log(f"Documentary overlays burned: {len(events)} static + "
                f"{len(maps)} maps.")
        return True
    except Exception as e:  # noqa: BLE001 - never fail a good render
        if log:
            log(f"Documentary overlays skipped ({type(e).__name__}: {e}).")
        return False
