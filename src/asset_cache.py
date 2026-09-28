"""U One - content-addressed media asset cache.

* Every downloaded asset is stored once, keyed by the SHA-1 of its source
  URL (plus the requested kind). The same media is never downloaded twice.
* A small JSON index records per-URL metadata (provider, kind, verified
  resolution) so re-searches can reuse known-good assets.
* Scene render segments are cached by a hash of (asset id + file mtime +
  duration + geometry + seed) so re-renders skip scenes that already
  succeeded ("render resume").
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, Optional

from .app_paths import cache_dir

_INDEX = "index.json"


def _index_path() -> Path:
    return cache_dir() / _INDEX


def _load_index() -> Dict:
    p = _index_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_index(d: Dict):
    try:
        _index_path().write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        pass


def url_cache_key(url: str, kind: str = "") -> str:
    h = hashlib.sha1()
    h.update(kind.encode("utf-8"))
    h.update(b"\x00")
    h.update(url.encode("utf-8"))
    return h.hexdigest()


def cached_path_for(url: str, kind: str = "", ext: str = ".bin") -> Path:
    key = url_cache_key(url, kind)
    return cache_dir() / f"{key}{ext}"


def lookup(url: str, kind: str = "") -> Optional[Path]:
    """Return the cached file for a URL if it exists and is non-empty."""
    key = url_cache_key(url, kind)
    idx = _load_index()
    meta = idx.get(key)
    if not meta:
        return None
    p = Path(meta.get("path", ""))
    if p.is_file() and p.stat().st_size > 1024:
        return p
    # stale entry
    idx.pop(key, None)
    _save_index(idx)
    return None


def store(url: str, kind: str, path: Path, meta: Optional[Dict] = None):
    idx = _load_index()
    key = url_cache_key(url, kind)
    entry = {"path": str(path), "url": url, "kind": kind,
             "stored_at": time.time()}
    if meta:
        entry.update(meta)
    idx[key] = entry
    _save_index(idx)
    # prune very old entries (keep cache under reasonable size)
    try:
        files = sorted(cache_dir().glob("*"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files if p.is_file() and p.name != _INDEX)
        while total > 2_000_000_000 and files:  # 2 GB cap
            oldest = files.pop(0)
            if oldest.name == _INDEX or not oldest.is_file():
                continue
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)
    except Exception:
        pass


def _file_fingerprint(path: str) -> str:
    """Stable identity for cache keys: content hash for small files,
    mtime+size for large ones (avoids re-hashing video)."""
    try:
        st = os.stat(path)
    except OSError:
        return "missing"
    if st.st_size <= 8_000_000:
        hh = hashlib.sha1()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    hh.update(chunk)
        except OSError:
            return "missing"
        return "sha1:" + hh.hexdigest()
    return f"mtime:{st.st_mtime_ns}:{st.st_size}"


def segment_cache_key(asset_id: str, local_path: str, duration: float,
                      w: int, h: int, fps: int, seed: str = "") -> str:
    hh = hashlib.sha1()
    ident = _file_fingerprint(local_path)
    hh.update("|".join([asset_id, ident,
                        f"{duration:.3f}", str(w), str(h), str(fps),
                        seed]).encode("utf-8"))
    return hh.hexdigest()
