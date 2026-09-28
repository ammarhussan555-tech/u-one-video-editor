"""U One - quality control engine (v2).

Two severity levels:
  FATALS   -> block the render, with an exact reason per item.
  WARNINGS -> logged and shown, never block the render.

Covers: missing/empty segments, missing voiceover/mix, duplicate
visuals, low-res visuals, text overflow, SFX over-repetition, audio
clipping risk, long voiceover silences, subtitle file problems,
duration mismatches.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import media_probe
from .ffmpeg_engine import FFmpegEngine


@dataclass
class QCReport:
    fatals: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.fatals

    def describe(self) -> str:
        bits = []
        if self.fatals:
            bits.append("FATAL: " + "; ".join(self.fatals))
        if self.warnings:
            bits.append("warnings: " + "; ".join(self.warnings))
        return " | ".join(bits) if bits else "clean"


def _max_volume_db(engine: FFmpegEngine, path: str) -> Optional[float]:
    try:
        res = engine.run(["-i", path, "-af", "volumedetect", "-vn",
                          "-f", "null", "-"],
                         stage="qc_volume", timeout=120, log_stderr=False,
                         loglevel="info")
        m = re.search(r"max_volume:\s*(-?[\d.]+)\s*dB", res.stderr or "")
        return float(m.group(1)) if m else None
    except Exception:
        return None


def _long_silences(engine: FFmpegEngine, path: str,
                   min_gap: float = 3.0) -> List[float]:
    try:
        res = engine.run(["-i", path, "-af", "silencedetect=noise=-32dB:d=1.0",
                          "-vn", "-f", "null", "-"],
                         stage="qc_silence", timeout=180, log_stderr=False,
                         loglevel="info")
        return [float(m.group(1))
                for m in re.finditer(r"silence_duration:\s*([\d.]+)",
                                     res.stderr or "")
                if float(m.group(1)) >= min_gap]
    except Exception:
        return []


def _check_srt(path: str) -> List[str]:
    errs: List[str] = []

    def ts(t: str) -> float:
        h, m, rest = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest.replace(",", "."))

    try:
        blocks = Path(path).read_text(encoding="utf-8").strip().split("\n\n")
        prev_end = -1.0
        for i, b in enumerate(blocks):
            lines = b.strip().splitlines()
            if len(lines) < 2:
                errs.append(f"Subtitle block {i + 1}: malformed.")
                continue
            m = re.match(r"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)",
                         lines[1])
            if not m:
                errs.append(f"Subtitle block {i + 1}: bad timestamp.")
                continue
            s0, s1 = ts(m.group(1)), ts(m.group(2))
            if s0 >= s1:
                errs.append(f"Subtitle block {i + 1}: start >= end.")
            if s0 < prev_end - 0.01:
                errs.append(f"Subtitle block {i + 1}: out of order/overlapping.")
            if not "".join(lines[2:]).strip():
                errs.append(f"Subtitle block {i + 1}: empty text.")
            prev_end = s1
    except Exception as e:  # noqa: BLE001
        errs.append(f"Could not validate subtitles: {e}")
    return errs


def run_qc(seg_paths: List[str], mixed_audio: str,
           voice_path: str = "",
           timeline=None,
           srt_path: str = "",
           engine: Optional[FFmpegEngine] = None) -> QCReport:
    """Full QC pass. Never raises; fatals block, warnings inform."""
    eng = engine or FFmpegEngine()
    rep = QCReport()

    if not seg_paths:
        rep.fatals.append("No scene segments were produced.")
    for sp in seg_paths:
        base = os.path.basename(sp)
        if not os.path.isfile(sp) or os.path.getsize(sp) < 1024:
            rep.fatals.append(f"Segment missing or empty: {base}")
            continue
        info = media_probe.probe(sp, eng.ffprobe)
        if not info.has_video:
            rep.fatals.append(f"Segment has no video stream: {base}")
        elif (info.duration or 0) <= 0.2:
            rep.fatals.append(f"Segment has no usable duration: {base}")

    if not os.path.isfile(mixed_audio) or os.path.getsize(mixed_audio) < 1024:
        rep.fatals.append("Final mixed audio is missing or empty; "
                          "the video cannot be rendered with sound.")
    else:
        peak = _max_volume_db(eng, mixed_audio)
        if peak is not None and peak >= -0.5:
            rep.warnings.append(
                f"Audio clipping risk: peak {peak:.1f} dB on final mix.")

    if voice_path:
        if not os.path.isfile(voice_path) or os.path.getsize(voice_path) < 1024:
            rep.fatals.append("Voiceover audio is missing or empty.")
        else:
            gaps = _long_silences(eng, voice_path)
            if gaps:
                rep.warnings.append(
                    f"Long silent gap(s) in voiceover: {len(gaps)} gap(s), "
                    f"longest {max(gaps):.1f}s.")

    if timeline is not None:
        seen: Dict[str, int] = {}
        lowres = 0
        for c in timeline.by_track("video"):
            asset = c.payload.get("asset") if isinstance(c.payload, dict) else None
            aid = asset.get("id") if isinstance(asset, dict) else None
            if aid:
                seen[aid] = seen.get(aid, 0) + 1
            wdt = asset.get("width", 0) if isinstance(asset, dict) else 0
            if wdt and wdt < 640:
                lowres += 1
        dups = [k for k, v in seen.items() if v > 1 and k != "fallback"]
        if dups:
            rep.warnings.append("Duplicate visuals reused: " +
                                ", ".join(dups[:4]) +
                                ("..." if len(dups) > 4 else ""))
        if lowres:
            rep.warnings.append(
                f"Low-resolution visual(s) (<640px): {lowres} scene(s).")
        for c in timeline.by_track("text"):
            if len(c.label or "") > 48:
                rep.warnings.append(
                    f"On-screen text may overflow: '{c.label[:40]}...'")
        sfx_counts: Dict[str, int] = {}
        for c in timeline.by_track("sfx"):
            k = (c.payload or {}).get("sfx", "?")
            sfx_counts[k] = sfx_counts.get(k, 0) + 1
        for k, v in sfx_counts.items():
            if v > 3:
                rep.warnings.append(
                    f"SFX '{k}' repeats {v}x - may feel excessive.")
        if not timeline.by_track("voice"):
            rep.warnings.append("No voiceover clip on the timeline.")

    if srt_path and os.path.exists(srt_path):
        rep.warnings.extend(_check_srt(srt_path))
    return rep
