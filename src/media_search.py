"""U One - media search and download.

* Pexels + Pixabay + Wikimedia Commons providers with a shared, pooled
  HTTP session (connection reuse), request retries and timeouts.
  Wikimedia is keyless (no API key needed) and great for news niche:
  real event/place/people photos.
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
import os
import time
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
    else:
        score -= 8.0  # very low resolution
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


class MediaFinder:
    """Concurrent provider search, ranked selection, dedupe, safe cached
    downloads, generated-graphics fallback. Never raises."""

    def __init__(self, assets_dir: str, pexels_key: str = "",
                 pixabay_key: str = "", preference: str = "ai_auto",
                 repetition: str = "never", project_id: str = "app"):
        self.assets_dir = assets_dir
        os.makedirs(assets_dir, exist_ok=True)
        self.providers = [PexelsProvider(pexels_key), PixabayProvider(pixabay_key),
                          WikimediaProvider()]
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

    def _search_all(self, query: str, kind: str) -> List[Dict]:
        """Search all providers concurrently for one (query, kind)."""
        results: List[Dict] = []
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(p.search, query, kind): p.name
                    for p in self.providers}
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
                for q in queries:
                    try:
                        candidates.extend(self._search_all(q, kind))
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
    prov = PexelsProvider(key) if which == "pexels" else PixabayProvider(key)
    return prov.test()
