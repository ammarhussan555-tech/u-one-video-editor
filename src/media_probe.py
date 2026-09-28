"""U One - structured media probing (v2).

One ffprobe call per file -> MediaInfo dataclass. Results are cached by
(path, mtime, size) so repeated validation/QC passes never re-probe.
This replaces ad-hoc ffprobe_duration()/media_has_streams() calls and the
repeated probing that slowed the old pipeline.
"""
from __future__ import annotations

import json
import os
import subprocess
from .ffmpeg_util import run_no_window
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class VideoStreamInfo:
    index: int = 0
    codec: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    pix_fmt: str = ""
    duration: float = 0.0
    nb_frames: int = 0


@dataclass
class AudioStreamInfo:
    index: int = 0
    codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    channel_layout: str = ""
    duration: float = 0.0


@dataclass
class MediaInfo:
    path: str = ""
    exists: bool = False
    size: int = 0
    container: str = ""
    duration: float = 0.0
    video: Optional[VideoStreamInfo] = None
    audio: Optional[AudioStreamInfo] = None
    probe_error: str = ""

    @property
    def has_video(self) -> bool:
        return self.video is not None

    @property
    def has_audio(self) -> bool:
        return self.audio is not None

    def problems(self) -> List[str]:
        out = []
        if not self.exists:
            out.append("file does not exist")
        elif self.size <= 0:
            out.append("file is empty (0 bytes)")
        if self.probe_error:
            out.append(f"unreadable container: {self.probe_error}")
        return out


def _parse_fps(value: str) -> float:
    try:
        if "/" in value:
            num, den = value.split("/")
            den = float(den)
            return float(num) / den if den else 0.0
        return float(value)
    except Exception:
        return 0.0


_cache: Dict[Tuple[str, float, int], MediaInfo] = {}
_cache_lock = threading.Lock()


def probe(path: str, ffprobe_path: Optional[str] = None,
          use_cache: bool = True) -> MediaInfo:
    """Probe a media file once; cache by (path, mtime, size)."""
    p = Path(path)
    info = MediaInfo(path=str(p), exists=p.is_file())
    if info.exists:
        try:
            st = p.stat()
            info.size = st.st_size
        except OSError:
            info.exists = False
    if not info.exists or info.size <= 0:
        return info

    key = (str(p.resolve()), os.path.getmtime(p), info.size)
    if use_cache:
        with _cache_lock:
            hit = _cache.get(key)
        if hit is not None:
            return hit

    if not ffprobe_path:
        from .ffmpeg_engine import find_ffprobe, find_ffmpeg
        try:
            ffprobe_path = find_ffprobe(find_ffmpeg())
        except Exception:
            ffprobe_path = None
    if not ffprobe_path:
        info.probe_error = "ffprobe not available"
        return info

    try:
        r = run_no_window(
            [ffprobe_path, "-v", "error",
             "-show_entries", "stream=index,codec_type,codec_name,width,height,"
                              "avg_frame_rate,pix_fmt,duration,sample_rate,"
                              "channels,channel_layout,nb_frames",
             "-show_entries", "format=format_name,duration,size",
             "-of", "json", str(p)],
            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            info.probe_error = (r.stderr or "ffprobe failed").strip()[:300]
            return info
        data = json.loads(r.stdout or "{}")
    except Exception as e:  # noqa: BLE001
        info.probe_error = f"probe exception: {e}"[:300]
        return info

    fmt = data.get("format", {}) or {}
    info.container = str(fmt.get("format_name", ""))
    try:
        info.duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        info.duration = 0.0

    for s in data.get("streams", []) or []:
        ctype = s.get("codec_type")
        if ctype == "video" and info.video is None:
            try:
                dur = float(s.get("duration") or info.duration or 0)
            except (TypeError, ValueError):
                dur = info.duration or 0.0
            try:
                nb = int(s.get("nb_frames") or 0)
            except (TypeError, ValueError):
                nb = 0
            info.video = VideoStreamInfo(
                index=int(s.get("index", 0)),
                codec=str(s.get("codec_name", "")),
                width=int(s.get("width", 0) or 0),
                height=int(s.get("height", 0) or 0),
                fps=_parse_fps(str(s.get("avg_frame_rate", "0"))),
                pix_fmt=str(s.get("pix_fmt", "")),
                duration=dur, nb_frames=nb)
        elif ctype == "audio" and info.audio is None:
            try:
                dur = float(s.get("duration") or info.duration or 0)
            except (TypeError, ValueError):
                dur = info.duration or 0.0
            try:
                sr = int(s.get("sample_rate", 0) or 0)
            except (TypeError, ValueError):
                sr = 0
            info.audio = AudioStreamInfo(
                index=int(s.get("index", 0)),
                codec=str(s.get("codec_name", "")),
                sample_rate=sr,
                channels=int(s.get("channels", 0) or 0),
                channel_layout=str(s.get("channel_layout", "")),
                duration=dur)

    with _cache_lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[key] = info
    return info


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def duration_of(path: str, ffprobe_path: Optional[str] = None) -> float:
    """Fast duration lookup (cached probe). 0.0 on failure."""
    return probe(path, ffprobe_path).duration
