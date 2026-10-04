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
from concurrent.futures import TimeoutError as FuturesTimeoutError
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


def _ytdlp_download(webpage_url: str, kind: str,
                   timeout_s: int = 240) -> Optional[Path]:
    """Download a YouTube video via the bundled yt-dlp (<=720p single file).

    Used for candidates whose url is "ytdlp:<watch-url>". Returns the cached
    local path or None. yt-dlp is imported lazily so media_search still
    imports on systems without it (then every such candidate just fails).

    Hard wall-clock bound: the progress-hook deadline only fires while bytes
    flow; extraction stalls (e.g. YouTube bot-check pages) fire no progress
    events and would hang the scene worker forever (Uzair 2026-10-04: render
    froze at 59% on the last scene). The download runs in a thread and is
    abandoned after timeout_s; the scene then falls back to the next
    candidate / generated graphic instead of freezing the UI.
    """
    try:
        import yt_dlp
    except ImportError:  # noqa: BLE001
        return None
    cache_url = "ytdlp:" + (webpage_url or "")
    cached = asset_cache.lookup(cache_url, kind)
    if cached:
        return cached
    dest = asset_cache.cached_path_for(cache_url, kind, ".mp4")
    stem = dest.stem + "_dl"
    try:
        from .ffmpeg_util import find_ffmpeg
        ff = find_ffmpeg()
    except Exception:  # noqa: BLE001
        ff = None
    deadline = time.time() + timeout_s  # max 4 min per YouTube download

    def _hook(d):
        if time.time() > deadline:
            raise RuntimeError("yt-dlp download timed out")

    def _run():
        opts = {
            "quiet": True, "no_warnings": True, "noprogress": True,
            # DASH video+audio merged (news uploads are rarely progressive);
            # prefer H.264 for cheap decoding, merge to mp4
            "format": ("bv*[height<=720][vcodec^=avc]+ba/"
                       "bv*[height<=720]+ba/b[height<=720]/b"),
            "merge_output_format": "mp4",
            "outtmpl": str(dest.parent / (stem + ".%(ext)s")),
            "socket_timeout": 20, "retries": 2, "extractor_retries": 2,
            "progress_hooks": [_hook],
        }
        if ff:
            opts["ffmpeg_location"] = ff
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([webpage_url])

    try:
        # Manual daemon thread (not ThreadPoolExecutor): an abandoned hung
        # worker must not block process exit, and executor __exit__ would
        # wait for it (wait=True), reintroducing the hang.
        import threading as _th
        _box = {}

        def _target():
            try:
                _run()
            except Exception as e:  # noqa: BLE001
                _box["error"] = e

        _t = _th.Thread(target=_target, daemon=True,
                        name="ytdlp-download")
        _t.start()
        _t.join(timeout=timeout_s)
        if _t.is_alive():
            return None  # hung in extraction: abandon, fall back
        if "error" in _box:
            return None
    except Exception:  # noqa: BLE001 - timeout or download error -> None
        return None
    try:
        got = None
        for cand in dest.parent.glob(stem + ".*"):
            if cand.suffix.lower() in (".mp4", ".webm", ".mov", ".mkv") \
                    and cand.stat().st_size >= _MIN_BYTES:
                got = cand
                break
        if got is None:
            return None
        if not media_has_streams(str(got), want_video=True):
            got.unlink(missing_ok=True)
            return None
        got.replace(dest)
        asset_cache.store(cache_url, kind, dest)
        return dest
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            for leftover in dest.parent.glob(stem + ".*"):
                if leftover != dest:
                    leftover.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _safe_download(url: str, kind: str) -> Optional[Path]:
    """Download with full safety checks. Returns cached/final path or None."""
    if (url or "").startswith("ytdlp:"):
        return _ytdlp_download(url[len("ytdlp:"):], kind)
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

    def __init__(self, pd_only: bool = False):
        # pd_only: Safe-mode filter - keep only public-domain / CC0 files.
        self.pd_only = pd_only
        if pd_only:
            self.license = "Wikimedia Commons (public domain)"

    @staticmethod
    def _is_public_domain(ii: dict) -> bool:
        try:
            lic = (ii.get("extmetadata", {}).get("LicenseShortName", {})
                     .get("value", "") or "")
        except Exception:  # noqa: BLE001
            return False
        lv = lic.strip().lower()
        return "public domain" in lv or lv == "cc0"

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
                "iiprop": "url|size" + ("|extmetadata" if self.pd_only else ""),
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
                if self.pd_only and not self._is_public_domain(ii):
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


# Telegram public channel previews: t.me/s/<channel> is server-rendered
# (no login, no API key) and embeds direct telesco.pe mp4 video URLs and
# photo URLs with short-lived tokens. Pages are fetched fresh on every
# search and the URLs are used immediately, so expiry is not an issue.
# Channels below were verified live (HTTP 200 + message blocks) on
# 2026-09-30: 3 military/eyewitness + 3 news.
_TELEGRAM_CHANNELS = ("Osinttechnical", "rybar", "clashreport",
                      "disclosetv", "BNONews", "spectatorindex")
_TELEGRAM_STOPWORDS = frozenset({
    "news", "video", "videos", "photo", "photos", "latest", "today",
    "breaking", "watch", "live", "update", "updates", "new", "the",
    "and", "for", "with", "from", "this", "that",
})
_TG_MSG_SPLIT_RE = re.compile(r"tgme_widget_message_wrap")
_TG_MSG_TEXT_RE = re.compile(
    r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
_TG_MSG_VIDEO_RE = re.compile(r'<video[^>]+src="([^"]+)"')
_TG_MSG_PHOTO_RE = re.compile(r"background-image:url\('([^']+)'\)")
_WORD_RE = re.compile(r"[a-z0-9]{3,}")
# Reddit public JSON API (no key). secure_media.reddit_video.fallback_url
# is a direct mp4; image posts expose i.redd.it / preview.redd.it URLs.
# Note: Reddit blocks many datacenter IPs; residential connections
# (like the user's PC) normally work. Failures are silent (no results).
_REDDIT_SUBS = ("CombatFootage", "Military", "news", "worldnews")
# Reddit's API rules require a unique, descriptive User-Agent for OAuth.
_REDDIT_UA = "U-One/1.0 (AI Automatic Video Editor; contact: u-one.app)"
_REDDIT_IMG_RE = re.compile(r"\.(jpg|jpeg|png|webp)(\?|$)", re.IGNORECASE)


class TelegramProvider:
    """Eyewitness/news clips + photos from public Telegram channels.

    Keyless and quota-free: fetches the public t.me/s/<channel> preview
    pages, extracts per-post text + direct media URLs, and keyword-matches
    the post text against the query. first_query_only keeps it to one
    query variant per scene (6 channel pages x 2 kinds).
    """
    name = "Telegram"
    license = "Telegram public channels (check per-post license)"
    first_query_only = True

    def __init__(self, channels=()):
        self.channels = tuple(channels) or _TELEGRAM_CHANNELS

    @staticmethod
    def _query_words(query: str):
        return {w for w in _WORD_RE.findall((query or "").lower())
                if w not in _TELEGRAM_STOPWORDS}

    def _fetch_channel(self, channel: str) -> List[Dict]:
        """Fetch one channel preview page; return parsed message dicts."""
        try:
            r = _SESSION.get(f"https://t.me/s/{channel}",
                             timeout=_CONNECT_TIMEOUT)
            if r.status_code != 200 or "tgme_widget_message" not in r.text:
                return []
            return self._parse_messages(r.text, channel)
        except Exception:  # noqa: BLE001
            return []

    def _parse_messages(self, page_html: str, channel: str) -> List[Dict]:
        """Split the preview page into per-message (text, videos, photos)."""
        msgs: List[Dict] = []
        chunks = _TG_MSG_SPLIT_RE.split(page_html)
        for chunk in chunks[1:]:  # chunk 0 is the page header
            try:
                m = _TG_MSG_TEXT_RE.search(chunk)
                text = ""
                if m:
                    text = _html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))
                    text = re.sub(r"\s+", " ", text).strip()
                vids = []
                for v in _TG_MSG_VIDEO_RE.findall(chunk):
                    v = _html.unescape(v)
                    if "telesco.pe/file/" in v and ".mp4" in v and \
                            v not in vids:
                        vids.append(v)
                photos = []
                for p in _TG_MSG_PHOTO_RE.findall(chunk):
                    p = _html.unescape(p)
                    if "telesco.pe/file/" in p and p not in photos:
                        photos.append(p)
                if text or vids or photos:
                    msgs.append({"text": text, "videos": vids,
                                 "photos": photos, "channel": channel})
            except Exception:  # noqa: BLE001
                continue
        return msgs

    def search(self, query: str, kind: str = "video",
               per_page: int = 4) -> List[Dict]:
        words = self._query_words(query)
        if not words:
            return []
        try:
            msgs: List[Dict] = []
            with ThreadPoolExecutor(max_workers=6) as ex:
                futs = {ex.submit(self._fetch_channel, ch): ch
                        for ch in self.channels}
                for fut in as_completed(futs):
                    try:
                        msgs.extend(fut.result() or [])
                    except Exception:  # noqa: BLE001
                        continue
            scored = []
            for m in msgs:
                text_words = set(_WORD_RE.findall(m["text"].lower()))
                score = len(words & text_words)
                if score == 0:
                    continue
                urls = m["videos"] if kind == "video" else m["photos"]
                for u in urls:
                    scored.append((score, u, m))
            scored.sort(key=lambda t: t[0], reverse=True)
            out, seen = [], set()
            for score, url, m in scored:
                if url in seen:
                    continue
                seen.add(url)
                out.append({
                    "url": url,
                    "page": f"https://t.me/s/{m['channel']}",
                    "w": 0, "h": 0, "dur": 0,
                    "tags": m["text"][:200],
                })
                if len(out) >= per_page:
                    break
        except Exception:  # noqa: BLE001
            return []
        return [{**o, "source": "Telegram", "license": self.license,
                 "query": query} for o in out if o.get("url")]

    def test(self) -> tuple[bool, str]:
        try:
            r = _SESSION.get("https://t.me/s/disclosetv",
                             timeout=_CONNECT_TIMEOUT)
            if r.status_code == 200 and "tgme_widget_message" in r.text:
                return True, "Connected"
            return False, f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:80]


class RedditProvider:
    """Military/eyewitness clips + photos from Reddit (official OAuth API).

    Reddit shut down anonymous JSON access (www.reddit.com now returns
    HTTP 403 for bots) AND closed self-service app creation on
    2025-11-11 (Responsible Builder Policy). New API access requires
    Reddit's approval via a support ticket; once approved, Reddit issues
    a client ID + secret. Paste them into U One Settings and the
    provider uses the app-only (client_credentials) flow against
    oauth.reddit.com; without them it tries the legacy anonymous
    endpoint as a best-effort fallback.

    Searches the newest posts in each subreddit; native video posts carry
    a direct v.redd.it mp4 fallback URL, image posts carry direct
    i.redd.it / preview.redd.it URLs. NSFW posts are skipped.
    first_query_only keeps it to one query variant per scene.
    """
    name = "Reddit"
    license = "Reddit (check per-post license)"
    first_query_only = True

    def __init__(self, client_id: str = "", client_secret: str = "",
                 subs=()):
        self.subs = tuple(subs) or _REDDIT_SUBS
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self._token = ""
        self._token_exp = 0.0

    def _get_token(self) -> str:
        """App-only OAuth token (client_credentials grant). Cached."""
        import time as _time
        if self._token and _time.time() < self._token_exp - 60:
            return self._token
        if not (self.client_id and self.client_secret):
            return ""
        try:
            r = _SESSION.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": _REDDIT_UA},
                timeout=_CONNECT_TIMEOUT)
            if r.status_code != 200:
                return ""
            data = r.json()
            tok = data.get("access_token", "") or ""
            if tok:
                self._token = tok
                try:
                    ttl = int(data.get("expires_in", 3600))
                except Exception:  # noqa: BLE001
                    ttl = 3600
                self._token_exp = _time.time() + max(ttl, 60)
            return self._token
        except Exception:  # noqa: BLE001
            return ""

    def _search_sub_oauth(self, sub: str, query: str) -> List[Dict]:
        tok = self._get_token()
        if not tok:
            return []
        try:
            r = _SESSION.get(
                f"https://oauth.reddit.com/r/{sub}/search",
                params={"q": query, "sort": "new", "restrict_sr": "1",
                        "limit": 10, "raw_json": 1},
                headers={"Authorization": f"Bearer {tok}",
                         "User-Agent": _REDDIT_UA},
                timeout=_CONNECT_TIMEOUT)
            if r.status_code == 401:
                # Token rejected; drop cache and retry once.
                self._token, self._token_exp = "", 0.0
                tok = self._get_token()
                if not tok:
                    return []
                r = _SESSION.get(
                    f"https://oauth.reddit.com/r/{sub}/search",
                    params={"q": query, "sort": "new",
                            "restrict_sr": "1", "limit": 10,
                            "raw_json": 1},
                    headers={"Authorization": f"Bearer {tok}",
                             "User-Agent": _REDDIT_UA},
                    timeout=_CONNECT_TIMEOUT)
            if r.status_code != 200:
                return []
            return self._parse_posts(r.json())
        except Exception:  # noqa: BLE001
            return []

    def _search_sub_anon(self, sub: str, query: str) -> List[Dict]:
        try:
            r = _SESSION.get(
                f"https://www.reddit.com/r/{sub}/search.json",
                params={"q": query, "sort": "new", "restrict_sr": "1",
                        "limit": 10},
                timeout=_CONNECT_TIMEOUT)
            if r.status_code != 200:
                return []
            return self._parse_posts(r.json())
        except Exception:  # noqa: BLE001
            return []

    def _search_sub(self, sub: str, query: str) -> List[Dict]:
        if self.client_id and self.client_secret:
            return self._search_sub_oauth(sub, query)
        return self._search_sub_anon(sub, query)

    def _parse_posts(self, data) -> List[Dict]:
        """Parse Reddit search.json into (title, video, image, permalink)."""
        posts: List[Dict] = []
        try:
            children = ((data or {}).get("data", {}) or {}).get(
                "children", []) or []
        except Exception:  # noqa: BLE001
            return []
        for ch in children:
            try:
                pd = ((ch or {}).get("data", {}) or {})
                if pd.get("over_18") or pd.get("stickied"):
                    continue
                title = pd.get("title", "") or ""
                url = pd.get("url", "") or ""
                video_url = None
                rv = ((pd.get("secure_media") or {}).get("reddit_video")
                      or {})
                fb = rv.get("fallback_url", "") or ""
                if fb.startswith("https://v.redd.it/"):
                    video_url = fb.split("?")[0]
                elif url.startswith("https://v.redd.it/"):
                    video_url = url.rstrip("/").split("?")[0] + \
                        "/DASH_720.mp4"
                img_url = None
                if _REDDIT_IMG_RE.search(url) and \
                        ("i.redd.it" in url or "preview.redd.it" in url):
                    img_url = url
                if not img_url:
                    try:
                        src = pd["preview"]["images"][0]["source"]["url"]
                        src = _html.unescape(src or "")
                        if _REDDIT_IMG_RE.search(src):
                            img_url = src.split("?")[0]
                    except Exception:  # noqa: BLE001
                        pass
                if video_url or img_url:
                    posts.append({
                        "title": title, "video": video_url,
                        "image": img_url,
                        "permalink": "https://www.reddit.com" +
                                     (pd.get("permalink") or ""),
                    })
            except Exception:  # noqa: BLE001
                continue
        return posts

    def search(self, query: str, kind: str = "video",
               per_page: int = 4) -> List[Dict]:
        try:
            posts: List[Dict] = []
            with ThreadPoolExecutor(max_workers=4) as ex:
                futs = {ex.submit(self._search_sub, s, query): s
                        for s in self.subs}
                for fut in as_completed(futs):
                    try:
                        posts.extend(fut.result() or [])
                    except Exception:  # noqa: BLE001
                        continue
            out, seen = [], set()
            for p in posts:
                url = p["video"] if kind == "video" else p["image"]
                if not url or url in seen:
                    continue
                seen.add(url)
                out.append({
                    "url": url,
                    "page": p["permalink"],
                    "w": 0, "h": 0, "dur": 0,
                    "tags": p["title"],
                })
                if len(out) >= per_page:
                    break
        except Exception:  # noqa: BLE001
            return []
        return [{**o, "source": "Reddit", "license": self.license,
                 "query": query} for o in out if o.get("url")]

    def test(self) -> tuple[bool, str]:
        if self.client_id and self.client_secret:
            tok = self._get_token()
            if not tok:
                return False, ("OAuth failed - check Client ID/Secret "
                               "(from your Reddit-approved app)")
            try:
                r = _SESSION.get(
                    "https://oauth.reddit.com/r/news/new",
                    params={"limit": 1, "raw_json": 1},
                    headers={"Authorization": f"Bearer {tok}",
                             "User-Agent": _REDDIT_UA},
                    timeout=_CONNECT_TIMEOUT)
                if r.status_code == 200 and '"children"' in r.text:
                    return True, "Connected (OAuth)"
                return False, f"HTTP {r.status_code}"
            except Exception as e:  # noqa: BLE001
                return False, str(e)[:80]
        # No credentials: legacy anonymous check (best effort).
        try:
            r = _SESSION.get("https://www.reddit.com/r/news/new.json",
                             params={"limit": 1},
                             timeout=_CONNECT_TIMEOUT)
            if r.status_code == 200 and '"children"' in r.text:
                return True, "Connected"
            return False, (f"HTTP {r.status_code} - Reddit now needs free "
                           "OAuth keys (see Settings)")
        except Exception:  # noqa: BLE001
            return False, ("Reddit needs API approval - file a ticket "
                           "at support.reddithelp.com, then add the issued "
                           "keys in Settings")


class NASAProvider:
    """NASA Images & Video Library (images-api.nasa.gov). Keyless.

    video: search media_type=video -> per-item collection.json ->
           direct ~medium.mp4 (fallback ~mobile.mp4).
    image: search media_type=image -> links[0].href (direct jpg).
    US public-domain media - space, weather, earth science.
    first_query_only keeps it to one query variant per scene.
    """
    name = "NASA"
    license = "NASA (US public domain)"
    first_query_only = True

    def _api(self, query: str, media_type: str, num: int = 6) -> Dict:
        r = _SESSION.get(
            "https://images-api.nasa.gov/search",
            params={"q": query, "media_type": media_type,
                    "page_size": num},
            timeout=_CONNECT_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _video_mp4(self, collection_url: str) -> Optional[str]:
        try:
            r = _SESSION.get(collection_url, timeout=_CONNECT_TIMEOUT)
            if r.status_code != 200:
                return None
            files = r.json() or []
            mp4s = [f for f in files
                    if isinstance(f, str) and f.endswith(".mp4")]
            for f in mp4s:
                if "~medium.mp4" in f:
                    return f
            for f in mp4s:
                if "~mobile.mp4" in f:
                    return f
            return mp4s[0] if mp4s else None
        except Exception:  # noqa: BLE001
            return None

    def search(self, query: str, kind: str = "video",
               per_page: int = 4) -> List[Dict]:
        try:
            out: List[Dict] = []
            if kind == "video":
                data = self._api(query, "video")
                items = ((data.get("collection") or {}).get("items")
                         or [])[:6]
                jobs = []
                for it in items:
                    d0 = ((it.get("data") or [{}])[0]) or {}
                    href = it.get("href", "") or ""
                    if href:
                        jobs.append((href, d0.get("title", "") or ""))
                with ThreadPoolExecutor(max_workers=4) as ex:
                    futs = {ex.submit(self._video_mp4, h): (h, t)
                            for h, t in jobs}
                    for fut in as_completed(futs):
                        href, title = futs[fut]
                        try:
                            mp4 = fut.result()
                        except Exception:  # noqa: BLE001
                            continue
                        if mp4:
                            out.append({
                                "url": mp4,
                                "page": href.replace("/collection.json", ""),
                                "w": 0, "h": 0, "dur": 0,
                                "tags": title,
                            })
                        if len(out) >= per_page:
                            break
            else:
                data = self._api(query, "image")
                items = ((data.get("collection") or {}).get("items") or [])
                for it in items:
                    links = it.get("links") or []
                    href = ((links[0].get("href", "") if links else "")
                            or "")
                    d0 = ((it.get("data") or [{}])[0]) or {}
                    if href and "images-assets.nasa.gov" in href:
                        out.append({
                            "url": href,
                            "page": "",
                            "w": 0, "h": 0, "dur": 0,
                            "tags": d0.get("title", "") or "",
                        })
                    if len(out) >= per_page:
                        break
        except Exception:  # noqa: BLE001
            return []
        return [{**o, "source": "NASA", "license": self.license,
                 "query": query} for o in out if o.get("url")]

    def test(self) -> tuple[bool, str]:
        try:
            data = self._api("moon", "image", 1)
            items = ((data.get("collection") or {}).get("items") or [])
            if items:
                return True, "Connected"
            return False, "No items returned"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:80]


class YouTubeProvider:
    """YouTube clips via the bundled yt-dlp. Keyless (no API key).

    video: yt-dlp flat search -> watch URLs; the downloader hook in
           _safe_download fetches "ytdlp:<watch-url>" via yt-dlp
           (<=720p single file, cached like every other asset).
    image: direct i.ytimg.com hqdefault.jpg thumbnails (plain HTTP).
    This covers official news/sports channels (AP Archive, AFP, BBC,
    Al Jazeera, FIFA, NBA, NFL, WHO, ...) as well as eyewitness uploads.
    Note: YouTube sometimes bot-checks datacenter IPs; residential
    connections normally pass. Failures are silent (no results).
    first_query_only keeps it to one query variant per scene.
    """
    name = "YouTube"
    license = "YouTube (check per-video license/copyright)"
    first_query_only = True

    def _search_ids(self, query: str, num: int = 6,
                    timeout_s: int = 60) -> List[tuple]:
        try:
            import yt_dlp
        except ImportError:  # noqa: BLE001
            return []
        # Hard wall-clock bound: extract_info can stall forever on
        # YouTube bot-check pages (no progress events, socket_timeout
        # does not cover it). A hung search must return [] instead of
        # freezing the scene worker (Uzair 2026-10-04: stuck at 59%).
        import threading as _th
        box: Dict[str, object] = {}

        def _target():
            try:
                opts = {"quiet": True, "no_warnings": True,
                        "skip_download": True, "extract_flat": True,
                        "socket_timeout": 15, "extractor_retries": 1}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    box["info"] = ydl.extract_info(
                        f"ytsearch{num}:{query}", download=False)
            except Exception as e:  # noqa: BLE001
                box["error"] = e

        try:
            t = _th.Thread(target=_target, daemon=True,
                           name="ytdlp-ytsearch")
            t.start()
            t.join(timeout=timeout_s)
            if t.is_alive() or "error" in box:
                return []
            info = box.get("info")
        except Exception:  # noqa: BLE001
            return []
        try:
            out = []
            for e in ((info or {}).get("entries") or []):
                vid = e.get("id") or ""
                if isinstance(vid, str) and len(vid) == 11:
                    out.append((vid, e.get("title") or ""))
            return out
        except Exception:  # noqa: BLE001
            return []

    def search(self, query: str, kind: str = "video",
               per_page: int = 4) -> List[Dict]:
        try:
            out: List[Dict] = []
            for vid, title in self._search_ids(query):
                page = f"https://www.youtube.com/watch?v={vid}"
                url = ("ytdlp:" + page if kind == "video"
                       else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
                out.append({"url": url, "page": page,
                            "w": 0, "h": 0, "dur": 0, "tags": title})
                if len(out) >= per_page:
                    break
        except Exception:  # noqa: BLE001
            return []
        return [{**o, "source": "YouTube", "license": self.license,
                 "query": query} for o in out if o.get("url")]

    def test(self) -> tuple[bool, str]:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:  # noqa: BLE001
            return False, "yt-dlp not installed"
        try:
            if self._search_ids("news", num=1):
                return True, "Connected"
            return False, "Search empty (YouTube may block this network)"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:80]


class MediaFinder:
    """Concurrent provider search, ranked selection, dedupe, safe cached
    downloads, generated-graphics fallback. Never raises."""

    def __init__(self, assets_dir: str, pexels_key: str = "",
                 pixabay_key: str = "", google_key: str = "",
                 google_cx: str = "", serper_key: str = "",
                 reddit_id: str = "", reddit_secret: str = "",
                 preference: str = "ai_auto",
                 repetition: str = "never", project_id: str = "app",
                 safe_mode: bool = False,
                 auto_clean_logos: bool = True):
        self.assets_dir = assets_dir
        os.makedirs(assets_dir, exist_ok=True)
        self.safe_mode = safe_mode
        self.auto_clean_logos = auto_clean_logos
        if safe_mode:
            # Copyright-safe providers only: Pexels, Pixabay, NASA,
            # Wikimedia (public-domain files only).
            self.providers = [PexelsProvider(pexels_key),
                              PixabayProvider(pixabay_key),
                              NASAProvider(),
                              WikimediaProvider(pd_only=True)]
        else:
            self.providers = [PexelsProvider(pexels_key), PixabayProvider(pixabay_key),
                              SerperProvider(serper_key),
                              XTwitterProvider(serper_key),
                              TelegramProvider(),
                              RedditProvider(reddit_id, reddit_secret),
                              YouTubeProvider(), NASAProvider(),
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
        # Manual executor lifecycle (not a `with` block): __exit__ would
        # wait for hung provider workers (wait=True). A provider that
        # stalls past the as_completed deadline must never freeze the
        # scene (Uzair 2026-10-04: stuck at 59%).
        ex = ThreadPoolExecutor(max_workers=8)
        try:
            futs = {ex.submit(p.search, query, kind): p.name
                    for p in provs}
            try:
                for fut in as_completed(futs, timeout=45):
                    try:
                        results.extend(fut.result(timeout=5) or [])
                    except Exception:  # noqa: BLE001
                        pass
            except FuturesTimeoutError:
                pass  # stalled providers: fall through, shut down hard
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
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
                    if self.auto_clean_logos and kind == "video":
                        # Crop/blur static corner logos on risky clips.
                        # Never raises; returns the original on any failure.
                        try:
                            from .logo_clean import maybe_clean_clip
                            local = maybe_clean_clip(
                                str(local), self.assets_dir,
                                cand.get("source", ""))
                        except Exception:  # noqa: BLE001
                            pass
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
    if which == "telegram":
        return TelegramProvider().test()
    if which == "reddit":
        # key is "client_id|client_secret" (issued after Reddit API
        # approval); empty = legacy anonymous attempt.
        parts = (key or "").split("|", 1)
        return RedditProvider(parts[0] if len(parts) > 0 else "",
                              parts[1] if len(parts) > 1 else "").test()
    if which == "youtube":
        return YouTubeProvider().test()
    if which == "nasa":
        return NASAProvider().test()
    prov = PexelsProvider(key) if which == "pexels" else PixabayProvider(key)
    return prov.test()
