"""U One - cache manager (v2).

Content-addressed caches for downloaded assets AND rendered segments.
Segment entries now store validation metadata (hash, duration,
resolution, codec, validation result JSON) so a re-render or resume
skips scenes that are already valid — and never trusts a cache entry
that failed validation.

Backed by the proven asset_cache implementation; this module adds the
validation-aware segment layer.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Dict, Optional

from . import asset_cache
from .app_paths import cache_dir

_SEG_INDEX = "segments.json"


def segment_cache_key(asset_id: str, asset_path: str, duration: float,
                      w: int, h: int, fps: int, seed: int) -> str:
    hsh = hashlib.sha1()
    try:
        mtime = str(os.path.getmtime(asset_path))
    except OSError:
        mtime = "missing"
    hsh.update(f"{asset_id}|{mtime}|{duration:.2f}|{w}x{h}|{fps}|{seed}"
               .encode("utf-8"))
    return hsh.hexdigest()


def _seg_index_path() -> Path:
    return cache_dir() / _SEG_INDEX


def _load_seg_index() -> Dict:
    try:
        return json.loads(_seg_index_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_seg_index(d: Dict) -> None:
    try:
        _seg_index_path().write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        pass


def store_segment(key: str, src_path: str, *,
                  validation: Optional[dict] = None) -> str:
    """Copy a validated segment into the cache; record its metadata."""
    dest = cache_dir() / "segments" / f"{key}.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dest)
    idx = _load_seg_index()
    idx[key] = {
        "path": str(dest),
        "validation": validation or {},
        "stored_at": time.time(),
    }
    _save_seg_index(idx)
    return str(dest)


def lookup_segment(key: str, *, require_valid: bool = True
                   ) -> Optional[str]:
    """Return the cached segment path, or None.

    With require_valid (default), only entries whose stored validation
    result was valid are returned — a cached broken segment is never
    trusted.
    """
    idx = _load_seg_index()
    ent = idx.get(key)
    if not ent:
        return None
    p = ent.get("path", "")
    if not (p and os.path.isfile(p) and os.path.getsize(p) > 1024):
        return None
    if require_valid and not (ent.get("validation") or {}).get("valid"):
        return None
    return p


def invalidate_segment(key: str) -> None:
    idx = _load_seg_index()
    ent = idx.pop(key, None)
    if ent and ent.get("path"):
        try:
            os.remove(ent["path"])
        except OSError:
            pass
    _save_seg_index(idx)


# -- asset (download) cache: re-export proven implementation --
url_cache_key = asset_cache.url_cache_key
cached_path_for = asset_cache.cached_path_for
lookup = asset_cache.lookup
