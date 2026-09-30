"""U One - media search and download.

* Pexels + Pixabay + Wikimedia Commons + Archive.org providers with a
  shared, pooled HTTP session (connection reuse), request retries and
  timeouts. Wikimedia (news photos) and Archive.org (news video clips)
  are keyless (no API key needed) - great for news niche.
* All providers are searched CONCURRENTLY; results are merged and ranked
  by relevance score (preferred: relevant video > relevant image > graphic).
* Downloads are cached by URL hash: the same media is never downloaded
  twice, even across projects.
* Download safety: HTTP status verified, content-type checked, minimum
  file size enforced, stalled downloads time out, the file is decoded/
  probed before acceptance, broken files are rejected, and temp names are
  filesystem-safe (never raw API filenames).
* Every asset records its source URL + license. Nothing here ever raises
  into the caller: failures become "no result" so the pipeline can fall
  back to generated graphics.
"""
from __future__ import annotations

import hashlib
import html as _html
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import asset_cache
from .ffmpeg_util import ffprobe_duration, media_has_streams

_CONNECT_TIMEOUT = 12
_DOWNLOAD_TIMEOUT = 60

# Direct video files the downloader can fetch over plain HTTP.
_VIDEO_EXTS = (".mp4", ".webm", ".mov", ".m4v")
# Video pages that never expose a direct file in static HTML (JS players).
_VIDEO_PAGE_BLOCKLIST = (
    "youtube.com", "youtu.be", "vimeo.com", "facebook.com", "fb.watch",
    "twitter.com", "x.com", "instagram.com", "tiktok.com",
    "dailymotion.com", "rumble.com",
)
# Tweet URL -> (user, status_id). X itself is blocklisted above because its
# pages need login; tweets are resolved via the FxEmbed "d." hosts instead.
_TWEET_RE = re.compile(
    r"https?://(?:www\.)?(?:x\.com|twitter\.com)/(\w+)/status/(\d+)",
    re.IGNORECASE)
# FxEmbed hosts: d.<host>/<user>/status/<id> 302-redirects to the direct
# video.twimg.com mp4 (no login, no API key).
_FX_VIDEO_HOSTS = ("d.fxtwitter.com", "d.fixupx.com", "d.vxtwitter.com")
_MIN_BYTES = 8 * 1024


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=0.8,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET", "HEAD"))
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8,
                          pool_maxsize=8)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "U-One/1.0 (AI Automatic Video Editor)"})
    return s


_SESSION = _session()


@dataclass
class Asset:
    id: str
    kind: str          # video | image | graphic
    local_path: str
    source: str        # Pexels | Pixabay | Generated | Placeholder
    license: str
    page_url: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0.0
    tags: str = ""
    query: str = ""

    def to_dict(self):
        return self.__dict__

    @staticmethod
    def from_dict(d):
        return Asset(**d)


class PexelsProvider:
    name = "Pexels"
    license = "Pexels License (free to use)"

    def __init__(self, key: str = ""):
        self.key = key or ""

    def search(self, query: str, kind: str, per_page: int = 4) -> List[Dict]:
        if not self.key:
            return []
        try:
            h = {"Authorization": self.key}
            out: List[Dict] = []
            if kind == "video":
                r = _SESSION.get("https://api.pexels.com/videos/search", headers=h,
                                 params={"query": query, "per_page": per_page},
                                 timeout=_CONNECT_TIMEOUT)
                r.raise_for_status()
                for v in r.json().get("videos", []):
                    files = v.get("video_files", [])
                    if not files:
                        continue
                    # SPEED: pick the SMALLEST file that meets 720p, not the
                    # largest (4K). Smaller download = 3-5x faster, and we
                    # upscale to 1080p in normalization anyway.
                    big_enough = [f for f in files
                                  if f.get("width", 0) >= 1280]
                    pool = big_enough or files
                    f = min(pool, key=lambda x: x.get("width", 0))
                    out.append({"url": f.get("link"), "page": v.get("url", ""),
                                "w": f.get("width", 0), "h": f.get("height", 0),
                                "dur": v.get("duration", 0), "tags": ""})
            else:
                r = _SESSION.get("https://api.pexels.com/v1/search", headers=h,
                                 params={"query": query, "per_page": per_page},
                                 timeout=_CONNECT_TIMEOUT)
                r.raise_for_status()
                for p in r.json().get("photos", []):
                    src = p.get("src", {})
                    out.append({"url": src.get("large2x") or src.get("large"),
                                "page": p.get("url", ""), "w": p.get("width", 0),
                                "h": p.get("height", 0), "dur": 0,
                                "tags": p.get("alt", "")})
        except Exception:  # noqa: BLE001 - provider failure -> no results
            return []
        return [{**o, "source": "Pexels", "license": self.license, "query": query}
                for o in out if o.get("url")]

    def test(self) -> tuple[bool, str]:
        """Return (ok, message) without leaking the key."""
        if not self.key:
            return False, "No key saved."
        try:
            r = _SESSION.get("https://api.pexels.com/v1/search",
                             headers={"Authorization": self.key},
                             params={"query": "nature", "per_page": 1},
                             timeout=_CONNECT_TIMEOUT)
            if r.status_code == 200:
                return True, "Connected."
            if r.status_code in (401, 403):
                return False, "Key rejected (401/403). Check the key."
            return False, f"HTTP {r.status_code}."
        except Exception as e:  # noqa: BLE001
            return False, f"Network error: {type(e).__name__}."


class PixabayProvider:
    name = "Pixabay"
    license = "Pixabay Content License (free to use)"

    def __init__(self, key: str = ""):
        self.key = key or ""

    def search(self, query: str, kind: str, per_page: int = 4) -> List[Dict]:
        if not self.key:
            return []
        try:
            if kind == "video":
                r = _SESSION.get("https://pixabay.com/api/videos/",
                                 params={"key": self.key, "q": query,
                                         "per_page": per_page, "safesearch": "true"},
                                 timeout=_CONNECT_TIMEOUT)
                r.raise_for_status()
                hits = r.json().get("hits", [])
                out = []
                for it in hits:
                    vids = it.get("videos", {})
                    # SPEED: prefer medium (720p) over large; faster download,
                    # upscaled to 1080p in normalization.
                    f = vids.get("medium") or vids.get("large") or {}
                    out.append({"url": f.get("url"), "page": it.get("pageURL", ""),
                                "w": f.get("width", 0), "h": f.get("height", 0),
                                "dur": it.get("duration", 0),
                                "tags": it.get("tags", "")})
            else:
                r = _SESSION.get("https://pixabay.com/api/",
                                 params={"key": self.key, "q": query,
                                         "per_page": per_page, "image_type": "photo",
                                         "safesearch": "true"},
                                 timeout=_CONNECT_TIMEOUT)
                r.raise_for_status()
                out = [{"url": it.get("largeImageURL"), "page": it.get("pageURL", ""),
                        "w": it.get("imageWidth", 0), "h": it.get("imageHeight", 0),
                        "dur": 0, "tags": it.get("tags", "")}
                       for it in r.json().get("hits", [])]
        except Exception:  # noqa: BLE001
            return []
        return [{**o, "source": "Pixabay", "license": self.license, "query": query}
                for o in out if o.get("url")]

    def test(self) -> tuple[bool, str]:
        if not self.key:
            return False, "No key saved."
        try:
            r = _SESSION.get("https://pixabay.com/api/",
                             params={"key": self.key, "q": "nature",
                                     "per_page": 3},
                             timeout=_CONNECT_TIMEOUT)
            if r.status_code == 200:
                return True, "Connected."
            return False, f"HTTP {r.status_code}."
        except Exception as e:  # noqa: BLE001
            return False, f"Network error: {type(e).__name__}."


class GraphicGenerator:
    """Offline fallback: charts from numbers, stylized location cards, title cards."""

    def __init__(self, out_dir: str):
        os.makedirs(out_dir, exist_ok=True)
        self.out_dir = out_dir

    def _fig(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(12.8, 7.2))
        fig.patch.set_facecolor("#0b1020")
        ax.set_facecolor("#0b1020")
        ax.axis("off")
        return fig, ax, plt

    def _save(self, fig, plt, name: str) -> str:
        path = os.path.join(self.out_dir, name)
        # exact 1280x720 (figsize 12.8x7.2 @ dpi 100); never use
        # bbox_inches="tight" - it explodes dimensions and shrinks content
        fig.savefig(path, dpi=100, facecolor=fig.get_facecolor())
        plt.close(fig)
        return path

    def chart(self, numbers, title, scene_id) -> str:
        fig, ax, plt = self._fig()
        vals = []
        for x in numbers[:8]:
            try:
                vals.append(float(str(x).rstrip("%")))
            except Exception:  # noqa: BLE001
                pass
        if not vals:
            vals = [1, 2, 3]
        ax.axis("on")
        ax.bar(range(len(vals)), vals, color="#4da3ff")
        ax.set_title(title[:60], color="white", fontsize=20, pad=18)
        ax.tick_params(colors="white", labelsize=12)
        ax.grid(alpha=0.2)
        return self._save(fig, plt, f"chart_{scene_id}.png")

    def location_card(self, place, scene_id) -> str:
        fig, ax, plt = self._fig()
        ax.set_xlim(0, 10)
        ax.set_ylim(0, 10)
        for i in range(11):
            ax.plot([i, i], [0, 10], color="#1c2a4a", lw=1)
            ax.plot([0, 10], [i, i], color="#1c2a4a", lw=1)
        ax.plot(5, 5.4, "o", ms=70, color="#ff5252")
        ax.text(5, 5.4, "\u25c9", color="white", fontsize=28, ha="center", va="center")
        ax.text(5, 4.4, place[:42], color="white", fontsize=26, ha="center", weight="bold")
        ax.text(5, 9.3, "LOCATION", color="#4da3ff", fontsize=16, ha="center")
        return self._save(fig, plt, f"map_{scene_id}.png")

    def title_card(self, text, scene_id) -> str:
        import textwrap
        fig, ax, plt = self._fig()
        ax.set_xlim(0, 10)
        ax.set_ylim(0, 10)
        lines = textwrap.wrap(text[:120], width=28)
        ax.text(5, 5.6, "\n".join(lines) or "U One", color="white",
                fontsize=30, ha="center", va="center", weight="bold",
                linespacing=1.5)
        ax.text(5, 1.1, "U ONE", color="#4da3ff", fontsize=18, ha="center",
                weight="bold")
        return self._save(fig, plt, f"card_{scene_id}.png")


def _safe_download(url: str, kind: str) -> Optional[Path]:
    """Download with full safety checks. Returns cached/final path or None."""
    ext = ".mp4" if kind == "video" else ".jpg"
    cached = asset_cache.lookup(url, kind)
    if cached:
        return cached
    dest = asset_cache.cached_path_for(url, kind, ext)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with _SESSION.get(url, stream=True, timeout=_DOWNLOAD_TIMEOUT) as r:
            r.raise_for_status()
            ctype = (r.headers.get("Content-Type", "") or "").split(";")[0].strip()
            want = "video" if kind == "video" else "image"
            if ctype and want not in ctype and "octet-stream" not in ctype:
                return None  # wrong file type
            total = 0
            start = time.time()
            with open(tmp, "wb") as f:
                for ch in r.iter_content(1024 * 256):
                    if not ch:
                        continue
                    f.write(ch)
                    total += len(ch)
                    if time.time() - start > _DOWNLOAD_TIMEOUT * 3:
                        return None  # stalled
            if total < _MIN_BYTES:
                return None
        # verify the file actually decodes
        ok = media_has_streams(str(tmp), want_video=(kind == "video"))
        if not ok:
            # images: ffprobe can still probe stills; accept by extension probe
            if kind != "image" or not media_has_streams(str(tmp), want_video=False):
                return None
        tmp.replace(dest)
        asset_cache.store(url, kind, dest)
        return dest
    except Exception:  # noqa: BLE001 - any failure -> caller tries next
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return None
    finally:
        try:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _relevance_score(cand: Dict, analysis) -> float:
    """Heuristic relevance: keyword/tag overlap, resolution, duration fit."""
    score = 0.0
    text = f"{cand.get('tags', '')} {cand.get('query', '')}".lower()
    kws = {k.lower() for k in (analysis.keywords or [])}
    hits = sum(1 for k in kws if k and k in text)
    score += hits * 10.0
    w = cand.get("w", 0) or 0
    if w >= 1920:
        score += 6.0
    elif w >= 1280:
        score += 4.0
    elif w >= 640:
        score += 2.0
    elif w > 0:
        score -= 8.0  # known to be very low resolution
    # w == 0 means unknown resolution: no penalty
    if cand.get("dur"):
        score += 1.0
    return score


class WikimediaProvider:
    """Keyless provider: Wikimedia Commons news/event photos.

    No API key needed. Great for news niche: real photos of events,
    places, people, disasters (not just generic stock). Only images
    (no videos); the app applies Ken Burns motion to images.
    """
    name = "Wikimedia"
    license = "Wikimedia Commons (check per-image license; mostly free)"

    def search(self, query: str, kind: str, per_page: int = 4) -> List[Dict]:
        if kind != "image":
            return []
        try:
            params = {
                "action": "query", "format": "json",
                "generator": "search", "gsrsearch": query,
                "gsrnamespace": "6",  # File: namespace
                "gsrlimit": str(min(per_page * 2, 10)),
                "prop": "imageinfo",
                "iiprop": "url|size",
                "iiurlwidth": "1280",
            }
            r = _SESSION.get("https://commons.wikimedia.org/w/api.php",
                             params=params, timeout=_CONNECT_TIMEOUT)
            r.raise_for_status()
            pages = r.json().get("query", {}).get("pages", {})
            out = []
            for pid, p in pages.items():
                ii = (p.get("imageinfo") or [{}])[0]
                # Prefer thumbnail (1280px wide); fall back to original.
                url = ii.get("thumburl") or ii.get("url")
                if not url:
                    continue
                # Skip tiny images.
                if (ii.get("thumbwidth") or ii.get("width") or 0) < 640:
                    continue
                out.append({
                    "url": url,
                    "page": "https://commons.wikimedia.org/wiki/" +
                            p.get("title", "").replace(" ", "_"),
                    "w": ii.get("thumbwidth") or ii.get("width", 0),
                    "h": ii.get("thumbheight") or ii.get("height", 0),
                    "dur": 0,
                    "tags": p.get("title", ""),
                })
                if len(out) >= per_page:
                    break
        except Exception:  # noqa: BLE001
            return []
        return [{**o, "source": "Wikimedia", "license": self.license,
                 "query": query} for o in out if o.get("url")]

    def test(self) -> tuple[bool, str]:
        try:
            r = _SESSION.get(
                "https://commons.wikimedia.org/w/api.php",
                params={"action": "query", "format": "json",
                        "list": "search", "srsearch": "news",
                        "srlimit": "1"},
                timeout=_CONNECT_TIMEOUT)
            if r.status_code == 200:
                return True, "Connected (keyless)"
            return False, f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:80]


class ArchiveOrgProvider:
    """Keyless provider: Archive.org news/documentary VIDEO clips.

    No API key needed. Real video footage (not just photos): TV news
    archives, documentaries, event coverage. Perfect for news niche.
    Prefers the smaller .ia.mp4 derivative for fast downloads.
    """
    name = "Archive.org"
    license = "Archive.org (check per-item license; mostly public domain / CC)"

    _MAX_BYTES = 250 * 1024 * 1024  # skip files bigger than 250 MB

    def _metadata(self, identifier: str) -> Dict:
        try:
            r = _SESSION.get(f"https://archive.org/metadata/{identifier}",
                             timeout=_CONNECT_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception:  # noqa: BLE001
            return {}

    def _best_mp4(self, identifier: str, meta: Dict):
        """Pick the smallest decent mp4 (prefer .ia.mp4 derivative)."""
        cands = []
        for f in meta.get("files", []):
            name = f.get("name", "")
            if not name.endswith(".mp4"):
                continue
            try:
                size = int(f.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            if size and size > self._MAX_BYTES:
                continue
            # .ia.mp4 is the streamable derivative: smaller, faster.
            rank = 0 if name.endswith(".ia.mp4") else 1
            cands.append((rank, size, name))
        if not cands:
            return None
        cands.sort()
        _, size, name = cands[0]
        url = (f"https://archive.org/download/{identifier}/"
               + urllib.parse.quote(name))
        return url, size

    def _one(self, doc: Dict):
        ident = doc.get("identifier", "")
        if not ident:
            return None
        meta = self._metadata(ident)
        best = self._best_mp4(ident, meta)
        if not best:
            return None
        url, _size = best
        # duration if the item metadata has it
        dur = 0.0
        try:
            d = meta.get("metadata", {}).get("duration", "")
            dur = float(str(d).split(":")[-1]) if d else 0.0
        except Exception:  # noqa: BLE001
            dur = 0.0
        return {"url": url,
                "page": f"https://archive.org/details/{ident}",
                "w": 0, "h": 0, "dur": dur,
                "tags": doc.get("title", "")}

    def search(self, query: str, kind: str, per_page: int = 4) -> List[Dict]:
        if kind != "video":
            return []
        try:
            params = {
                "q": f"({query}) AND mediatype:movies",
                "fl[]": "identifier,title",
                "rows": str(min(per_page * 2, 8)),
                "output": "json",
            }
            r = _SESSION.get("https://archive.org/advancedsearch.php",
                             params=params, timeout=_CONNECT_TIMEOUT)
            r.raise_for_status()
            docs = r.json().get("response", {}).get("docs", [])
        except Exception:  # noqa: BLE001
            return []
        out: List[Dict] = []
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = [ex.submit(self._one, d) for d in docs]
            for fut in as_completed(futs, timeout=90):
                try:
                    res = fut.result(timeout=10)
                except Exception:  # noqa: BLE001
                    res = None
                if res:
                    out.append(res)
                if len(out) >= per_page:
                    break
        return [{**o, "source": "Archive.org", "license": self.license,
                 "query": query} for o in out]

    def test(self) -> tuple[bool, str]:
        try:
            r = _SESSION.get(
                "https://archive.org/advancedsearch.php",
                params={"q": "news", "rows": "1", "output": "json"},
                timeout=_CONNECT_TIMEOUT)
            if r.status_code == 200:
                return True, "Connected (keyless)"
            return False, f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:80]


class GoogleProvider:
    """Optional provider: Google Custom Search (web images + videos).

    Needs a free API key + Search Engine ID (cx):
    Google Cloud Console -> Custom Search JSON API (100 free/day).
    Searches the whole web: latest news photos, any-niche images.
    Video results are best-effort (direct mp4 links from pagemap).
    Without keys this provider is silently skipped.
    """
    name = "Google"
    license = "Web search (check per-site license)"

    def __init__(self, api_key: str = "", cx: str = ""):
        self.api_key = api_key or ""
        self.cx = cx or ""

    def _api(self, params: Dict) -> Dict:
        params = dict(params)
        params.update({"key": self.api_key, "cx": self.cx})
        r = _SESSION.get("https://www.googleapis.com/customsearch/v1",
                         params=params, timeout=_CONNECT_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def search(self, query: str, kind: str, per_page: int = 4) -> List[Dict]:
        if not (self.api_key and self.cx):
            return []
        try:
            if kind == "image":
                data = self._api({
                    "q": query, "searchType": "image",
                    "num": min(per_page * 2, 10), "safe": "active",
                    "imgSize": "large",
                })
                out = []
                for it in data.get("items", []):
                    url = it.get("link", "")
                    if not url:
                        continue
                    img = it.get("image", {})
                    out.append({
                        "url": url,
                        "page": it.get("image", {}).get("contextLink", ""),
                        "w": img.get("width", 0), "h": img.get("height", 0),
                        "dur": 0, "tags": it.get("title", ""),
                    })
                    if len(out) >= per_page:
                        break
            else:
                # video: best-effort direct mp4 links
                data = self._api({
                    "q": f"{query} filetype:mp4",
                    "num": min(per_page * 2, 10), "safe": "active",
                })
                out = []
                for it in data.get("items", []):
                    url = ""
                    # pagemap videoobject often has the direct file URL
                    pm = it.get("pagemap", {})
                    for vo in pm.get("videoobject", []):
                        cu = (vo.get("contenturl", "") or "").split("?")[0]
                        if cu.lower().endswith(".mp4"):
                            url = vo.get("contenturl", "")
                            break
                    if not url:
                        link = it.get("link", "")
                        if link.lower().split("?")[0].endswith(".mp4"):
                            url = link
                    if not url:
                        continue
                    out.append({
                        "url": url,
                        "page": it.get("link", ""),
                        "w": 0, "h": 0, "dur": 0,
                        "tags": it.get("title", ""),
                    })
                    if len(out) >= per_page:
                        break
        except Exception:  # noqa: BLE001
            return []
        return [{**o, "source": "Google", "license": self.license,
                 "query": query} for o in out if o.get("url")]

    def test(self) -> tuple[bool, str]:
        if not (self.api_key and self.cx):
            return False, "No key saved."
        try:
            data = self._api({"q": "news", "num": 1})
            if "items" in data or "searchInformation" in data:
                return True, "Connected"
            err = (data.get("error", {}) or {}).get("message", "")
            return False, err[:80] or "API error"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:80]


class SerperProvider:
    """Optional provider: Serper.dev - real Google results, whole web.

    Free signup at serper.dev (2,500 free searches, no card needed).
    Single API key. Searches the entire web: images via /images
    endpoint; videos via /videos endpoint PLUS page scraping - each
    video result page is fetched and its embedded direct mp4/webm
    (og:video, <video>/<source> tags, JSON-LD contentUrl) is extracted,
    so news sites yield real downloadable clips, not just page links.
    Without a key this provider is silently skipped.
    """
    name = "Serper"
    license = "Web search (check per-site license)"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or ""

    def _api(self, endpoint: str, query: str, num: int) -> Dict:
        r = _SESSION.post(
            f"https://google.serper.dev/{endpoint}",
            headers={"X-API-KEY": self.api_key,
                     "Content-Type": "application/json"},
            json={"q": query, "num": num},
            timeout=_CONNECT_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _page_direct_videos(self, page_url: str) -> List[str]:
        """Fetch a video page; extract direct video-file URLs from HTML."""
        try:
            host = (urllib.parse.urlparse(page_url).netloc or "").lower()
            if any(b in host for b in _VIDEO_PAGE_BLOCKLIST):
                return []
            r = _SESSION.get(page_url, timeout=10)
            if r.status_code != 200:
                return []
            if "html" not in (r.headers.get("Content-Type", "") or "").lower():
                return []
            markup = r.text[:600_000]
        except Exception:  # noqa: BLE001
            return []
        patterns = (
            r'<meta[^>]+property=["\']og:video(?::secure_url)?["\'][^>]+'
            r'content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+'
            r'property=["\']og:video(?::secure_url)?["\']',
            r'<meta[^>]+(?:name|property)=["\']twitter:player:stream["\']'
            r'[^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+'
            r'(?:name|property)=["\']twitter:player:stream["\']',
            r'<(?:video|source)[^>]+src=["\']([^"\']+)["\']',
            r'"contentUrl"\s*:\s*"([^"]+)"',
        )
        found: List[str] = []
        for pat in patterns:
            for m in re.finditer(pat, markup, re.IGNORECASE):
                u = _html.unescape(m.group(1)).strip()
                if not u or u.startswith("data:"):
                    continue
                u = urllib.parse.urljoin(page_url, u)
                if urllib.parse.urlparse(u).path.lower().endswith(_VIDEO_EXTS):
                    found.append(u)
        seen = set()
        out = []
        for u in found:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out[:5]

    def search(self, query: str, kind: str, per_page: int = 4) -> List[Dict]:
        if not self.api_key:
            return []
        try:
            if kind == "image":
                data = self._api("images", query, min(per_page * 2, 10))
                out = []
                for it in data.get("images", []):
                    url = it.get("imageUrl", "")
                    if not url:
                        continue
                    out.append({
                        "url": url,
                        "page": it.get("link", ""),
                        "w": it.get("imageWidth", 0),
                        "h": it.get("imageHeight", 0),
                        "dur": 0,
                        "tags": it.get("title", ""),
                    })
                    if len(out) >= per_page:
                        break
            else:
                data = self._api("videos", query, min(per_page * 2, 10))
                out = []
                page_jobs: List[tuple] = []
                for it in data.get("videos", []):
                    link = it.get("link", "") or ""
                    if not link:
                        continue
                    if urllib.parse.urlparse(link).path.lower() \
                            .endswith(_VIDEO_EXTS):
                        out.append({
                            "url": link,
                            "page": link,
                            "w": 0, "h": 0, "dur": 0,
                            "tags": it.get("title", ""),
                        })
                    elif len(page_jobs) < 4:
                        page_jobs.append((link, it.get("title", "") or ""))
                    if len(out) >= per_page:
                        break
                # Scrape top video pages for embedded direct files.
                if page_jobs and len(out) < per_page:
                    with ThreadPoolExecutor(max_workers=4) as ex:
                        futs = {ex.submit(self._page_direct_videos, link): (link, t)
                                for link, t in page_jobs}
                        for fut in as_completed(futs):
                            if len(out) >= per_page:
                                break
                            link, title = futs[fut]
                            try:
                                urls = fut.result()
                            except Exception:  # noqa: BLE001
                                continue
                            for u in urls:
                                out.append({
                                    "url": u,
                                    "page": link,
                                    "w": 0, "h": 0, "dur": 0,
                                    "tags": title,
                                })
                                if len(out) >= per_page:
                                    break
        except Exception:  # noqa: BLE001
            return []
        return [{**o, "source": "Serper", "license": self.license,
                 "query": query} for o in out if o.get("url")]

    def test(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "No key saved."
        try:
            data = self._api("search", "news", 1)
            if "organic" in data:
                return True, "Connected"
            return False, str(data.get("error", ""))[:80] or "API error"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:80]


class XTwitterProvider:
    """X/Twitter latest media via Serper discovery + FxEmbed resolution.

    X's own API is paid-only and X login-walls scrapers, so this provider:
      video: Serper /videos with 'site:x.com' -> tweet URLs ->
             d.fxtwitter.com (302 redirect) -> direct video.twimg.com mp4.
      image: Serper /images with 'site:x.com' -> direct pbs.twimg.com URLs.
    Reuses the Serper API key (no separate key); without it, silently
    skipped. first_query_only keeps Serper quota use to 1 call/scene/kind.
    """
    name = "X"
    license = "X/Twitter (check per-post license)"
    first_query_only = True

    def __init__(self, serper_key: str = ""):
        self.api_key = serper_key or ""

    def _api(self, endpoint: str, query: str, num: int) -> Dict:
        r = _SESSION.post(
            f"https://google.serper.dev/{endpoint}",
            headers={"X-API-KEY": self.api_key,
                     "Content-Type": "application/json"},
            json={"q": query, "num": num},
            timeout=_CONNECT_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _tweet_direct_mp4(self, tweet_url: str) -> Optional[str]:
        """Resolve a tweet URL to its direct video.twimg.com mp4 (or None)."""
        m = _TWEET_RE.match((tweet_url or "").strip())
        if not m:
            return None
        user, sid = m.group(1), m.group(2)
        for host in _FX_VIDEO_HOSTS:
            try:
                with _SESSION.get(f"https://{host}/{user}/status/{sid}",
                                  timeout=12, stream=True) as r:
                    final = r.url or ""
                    if "video.twimg.com" in final:
                        return final
            except Exception:  # noqa: BLE001
                continue
        return None

    def search(self, query: str, kind: str, per_page: int = 4) -> List[Dict]:
        if not self.api_key:
            return []
        try:
            out: List[Dict] = []
            if kind == "image":
                data = self._api("images", f"{query} site:x.com",
                                 min(per_page * 2, 10))
                for it in data.get("images", []):
                    url = it.get("imageUrl", "") or ""
                    if "pbs.twimg.com" not in url:
                        continue
                    out.append({
                        "url": url,
                        "page": it.get("link", ""),
                        "w": it.get("imageWidth", 0),
                        "h": it.get("imageHeight", 0),
                        "dur": 0,
                        "tags": it.get("title", ""),
                    })
                    if len(out) >= per_page:
                        break
            else:
                data = self._api("videos", f"{query} site:x.com",
                                 min(per_page * 2, 10))
                tweets: List[tuple] = []
                for it in data.get("videos", []):
                    link = it.get("link", "") or ""
                    if _TWEET_RE.match(link) and \
                            all(link != t[0] for t in tweets):
                        tweets.append((link, it.get("title", "") or ""))
                    if len(tweets) >= 4:
                        break
                if tweets:
                    with ThreadPoolExecutor(max_workers=3) as ex:
                        futs = {ex.submit(self._tweet_direct_mp4, link): (link, t)
                                for link, t in tweets}
                        for fut in as_completed(futs):
                            if len(out) >= per_page:
                                break
                            link, title = futs[fut]
                            try:
                                mp4 = fut.result()
                            except Exception:  # noqa: BLE001
                                continue
                            if mp4:
                                out.append({
                                    "url": mp4,
                                    "page": link,
                                    "w": 0, "h": 0, "dur": 0,
                                    "tags": title,
                                })
        except Exception:  # noqa: BLE001
            return []
        return [{**o, "source": "X", "license": self.license,
                 "query": query} for o in out if o.get("url")]

    def test(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "Needs Serper key."
        try:
            data = self._api("search", "news site:x.com", 1)
            if "organic" in data:
                return True, "Connected"
            return False, str(data.get("error", ""))[:80] or "API error"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:80]


class MediaFinder:
    """Concurrent provider search, ranked selection, dedupe, safe cached
    downloads, generated-graphics fallback. Never raises."""

    def __init__(self, assets_dir: str, pexels_key: str = "",
                 pixabay_key: str = "", google_key: str = "",
                 google_cx: str = "", serper_key: str = "",
                 preference: str = "ai_auto",
                 repetition: str = "never", project_id: str = "app"):
        self.assets_dir = assets_dir
        os.makedirs(assets_dir, exist_ok=True)
        self.providers = [PexelsProvider(pexels_key), PixabayProvider(pixabay_key),
                          SerperProvider(serper_key),
                          XTwitterProvider(serper_key),
                          GoogleProvider(google_key, google_cx),
                          WikimediaProvider(), ArchiveOrgProvider()]
        self.preference = preference
        self.repetition = repetition
        self.used_ids = set()
        self.graphics = GraphicGenerator(assets_dir)
        self.project_id = project_id

    def _kind_order(self) -> List[str]:
        if self.preference == "video_first":
            return ["video", "image"]
        if self.preference == "image_first":
            return ["image", "video"]
        return ["video", "image"]

    def _search_all(self, query: str, kind: str,
                    first_only: bool = False) -> List[Dict]:
        """Search all providers concurrently for one (query, kind).

        first_only=True skips providers flagged first_query_only (used for
        the 2nd/3rd query variants to save paid-search quota).
        """
        results: List[Dict] = []
        provs = [p for p in self.providers
                 if not (first_only and getattr(p, "first_query_only", False))]
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(p.search, query, kind): p.name
                    for p in provs}
            for fut in as_completed(futs, timeout=45):
                try:
                    results.extend(fut.result(timeout=5) or [])
                except Exception:  # noqa: BLE001
                    pass
        return results

    def find_for_scene(self, scene, queries: List[str], analysis,
                       accept: Callable, exclude_ids=()) -> Asset:
        """Ranked concurrent search; safe cached download; graphic fallback."""
        try:
            for kind in self._kind_order():
                candidates: List[Dict] = []
                for qi, q in enumerate(queries):
                    try:
                        candidates.extend(
                            self._search_all(q, kind, first_only=(qi > 0)))
                    except Exception:  # noqa: BLE001
                        continue
                # rank: relevance first, then provider preference
                ranked = sorted(
                    ((c, _relevance_score(c, analysis)) for c in candidates),
                    key=lambda t: t[1], reverse=True)
                for cand, score in ranked:
                    cid = hashlib.md5(cand["url"].encode()).hexdigest()
                    if cid in self.used_ids and self.repetition == "never":
                        continue
                    if cid in exclude_ids:
                        continue
                    if score < 0:
                        continue  # too low-res / irrelevant
                    if not accept(cand, analysis):
                        continue
                    local = _safe_download(cand["url"], kind)
                    if local is None:
                        continue  # broken download -> next candidate
                    self.used_ids.add(cid)
                    return Asset(id=cid, kind=kind, local_path=str(local),
                                 source=cand["source"], license=cand["license"],
                                 page_url=cand.get("page", ""),
                                 width=cand.get("w", 0), height=cand.get("h", 0),
                                 duration=cand.get("dur", 0.0),
                                 tags=cand.get("tags", ""), query=cand.get("query", ""))
        except Exception:  # noqa: BLE001 - never break the pipeline
            pass
        return self.generated_fallback(scene, analysis)

    def generated_fallback(self, scene, analysis) -> Asset:
        gid = f"g{scene.id}"
        try:
            if analysis.numbers:
                p = self.graphics.chart(analysis.numbers,
                                        " ".join(analysis.keywords[:5]) or "Data",
                                        scene.id)
            elif analysis.phrases:
                p = self.graphics.location_card(analysis.phrases[0], scene.id)
            else:
                p = self.graphics.title_card(scene.text, scene.id)
        except Exception:  # noqa: BLE001
            p = self._emergency_card(scene)
        return Asset(id=gid, kind="graphic", local_path=p, source="Generated",
                     license="Created by U One (no third-party rights)",
                     width=1280, height=720, query="(generated)")

    def _emergency_card(self, scene) -> str:
        from PIL import Image, ImageDraw
        p = os.path.join(self.assets_dir, f"emg_{scene.id}.png")
        img = Image.new("RGB", (1280, 720), (11, 16, 32))
        d = ImageDraw.Draw(img)
        d.text((80, 320), (scene.text[:80] or "U One"), fill=(255, 255, 255))
        img.save(p)
        return p


def test_provider(which: str, key: str) -> tuple[bool, str]:
    """Test a provider connection with the given key (key never logged)."""
    if which == "wikimedia":
        return WikimediaProvider().test()
    if which == "archive_org":
        return ArchiveOrgProvider().test()
    if which == "google":
        # key is "api_key|cx"
        parts = (key or "").split("|", 1)
        return GoogleProvider(parts[0] if len(parts) > 0 else "",
                              parts[1] if len(parts) > 1 else "").test()
    if which == "serper":
        return SerperProvider(key).test()
    if which == "x_twitter":
        return XTwitterProvider(key).test()
    prov = PexelsProvider(key) if which == "pexels" else PixabayProvider(key)
    return prov.test()
