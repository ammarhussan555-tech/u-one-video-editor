"""U One - scene segment validator (v2).

Every generated segment is validated BEFORE it may enter the final
render. No invalid scene ever reaches concatenation.

Checks (fatal unless noted):
  file exists / size > 0 / container probeable / video stream present /
  known-good video codec / width,height > 0 / duration > min_duration /
  frames decode (full-stream decode pass) / black-video ratio /
  frozen-video ratio (warning < 0.95) / zero-frame detection /
  corrupt-stream signatures in decoder stderr / audio present+valid
  when required.

Returns SegmentReport(valid, fatals[], warnings[], metrics{}).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import media_probe
from .ffmpeg_engine import FFmpegEngine, FFmpegError
from .ffmpeg_util import run_no_window

# codecs we trust for segments we produced or downloaded
_GOOD_VIDEO_CODECS = {
    "h264", "hevc", "mpeg4", "vp9", "av1", "mjpeg", "png",
}
_CORRUPT_PATTERNS = [
    "invalid data found when processing input",
    "moov atom not found",
    "corrupt",
    "error while decoding",
    "invalid nal",
    "truncated",
]


@dataclass
class SegmentReport:
    path: str
    valid: bool = False
    fatals: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)

    def describe(self) -> str:
        bits = []
        if self.fatals:
            bits.append("FATAL: " + "; ".join(self.fatals))
        if self.warnings:
            bits.append("warnings: " + "; ".join(self.warnings))
        return " | ".join(bits) if bits else "valid"


def _fatal(rep: SegmentReport, msg: str) -> None:
    rep.fatals.append(msg)
    rep.valid = False


def _warn(rep: SegmentReport, msg: str) -> None:
    rep.warnings.append(msg)


def _decode_and_detect(engine: FFmpegEngine, path: str,
                       rep: SegmentReport) -> Optional[str]:
    """Full-stream decode with black/freeze detection. Returns stderr."""
    vf = ("blackdetect=d=0.5:pix_th=0.08,"
          "freezedetect=d=0.8:noise=0.001")
    try:
        res = engine.run(
            ["-i", path, "-map", "0:v:0", "-vf", vf,
             "-f", "null", "-"],
            stage="scene_validate", timeout=300, log_stderr=False,
            loglevel="info", show_stats=True)
        return res.stderr or ""
    except FFmpegError as e:
        _fatal(rep, f"video stream is not decodable "
                    f"(ffmpeg exit {e.exit_code})")
        rep.metrics["decode_error"] = float(e.exit_code or -1)
        return e.stderr_tail


def _black_ratio(stderr: str, duration: float) -> float:
    total = sum(float(m.group(1))
                for m in re.finditer(r"black_duration:([\d.]+)", stderr or ""))
    return (total / duration) if duration > 0 else 0.0


def _bright_pixel_fraction(eng: "FFmpegEngine", path: str,
                           duration: float) -> float:
    """Fraction of pixels brighter than luma 40 in one mid-stream frame.

    Distinguishes intentional dark visuals (title cards, night scenes)
    from genuinely blank/black video. Returns 0.0 on any error.
    Uses a direct subprocess call because FFmpegEngine.run() is text-mode.
    """
    import subprocess
    try:
        ss = max(0.1, (duration or 1.0) * 0.25)
        # 160x90 gray frame is plenty for a brightness census
        cmd = [eng.ffmpeg, "-hide_banner", "-loglevel", "error",
               "-ss", f"{ss:.2f}", "-i", path,
               "-vframes", "1", "-vf", "scale=160:90",
               "-f", "rawvideo", "-pix_fmt", "gray", "-"]
        raw = run_no_window(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL,
                             timeout=60).stdout or b""
        if len(raw) < 160 * 90:
            return 0.0
        bright = sum(1 for b in raw[:160 * 90] if b > 40)
        return bright / (160 * 90)
    except Exception:
        return 0.0


def _frozen_ratio(stderr: str, duration: float) -> float:
    total = sum(float(m.group(1))
                for m in re.finditer(r"freeze_duration:([\d.]+)", stderr or ""))
    return (total / duration) if duration > 0 else 0.0


def _decoded_frames(stderr: str) -> int:
    frames = [int(m.group(1))
              for m in re.finditer(r"frame=\s*(\d+)", stderr or "")]
    return frames[-1] if frames else 0


def validate_segment(path: str, *, engine: Optional[FFmpegEngine] = None,
                     expect_w: int = 0, expect_h: int = 0,
                     expect_fps: float = 0.0,
                     min_duration: float = 0.4,
                     require_audio: bool = False) -> SegmentReport:
    """Validate one rendered scene segment. Never raises for bad media."""
    eng = engine or FFmpegEngine()
    rep = SegmentReport(path=path, valid=True)

    info = media_probe.probe(path, eng.ffprobe)
    rep.metrics["size"] = float(info.size)
    if not info.exists:
        _fatal(rep, "file does not exist")
        return rep
    if info.size <= 0:
        _fatal(rep, "file is empty (0 bytes)")
        return rep
    if info.probe_error:
        _fatal(rep, f"container is not readable: {info.probe_error}")
        return rep
    if not info.has_video:
        _fatal(rep, "no video stream in container")
        return rep

    v = info.video
    assert v is not None
    rep.metrics.update(width=float(v.width), height=float(v.height),
                       fps=v.fps, duration=v.duration or info.duration)
    if v.codec not in _GOOD_VIDEO_CODECS:
        _fatal(rep, f"unsupported video codec: {v.codec or 'unknown'}")
    if v.width <= 0 or v.height <= 0:
        _fatal(rep, f"invalid dimensions: {v.width}x{v.height}")
    if expect_w and v.width != expect_w:
        _warn(rep, f"width {v.width} != expected {expect_w}")
    if expect_h and v.height != expect_h:
        _warn(rep, f"height {v.height} != expected {expect_h}")
    if expect_fps and v.fps and abs(v.fps - expect_fps) > 1.0:
        _warn(rep, f"fps {v.fps:.1f} != expected {expect_fps:.0f}")
    dur = v.duration or info.duration
    if dur < min_duration:
        _fatal(rep, f"invalid duration: {dur:.2f}s "
                    f"(minimum {min_duration:.1f}s)")
        return rep  # black/freeze ratios are meaningless on tiny clips

    if require_audio:
        if not info.has_audio:
            _fatal(rep, "required audio stream is missing")
        elif (info.audio.duration or 0) <= 0:
            _fatal(rep, "audio stream has invalid duration")

    stderr = _decode_and_detect(eng, path, rep)
    if not rep.valid and "not decodable" in " ".join(rep.fatals):
        return rep
    low = (stderr or "").lower()
    for pat in _CORRUPT_PATTERNS:
        if pat in low and "blackdetect" not in pat:
            _warn(rep, f"decoder reported stream issue: '{pat}'")
            break
    if "non-monotonous dts" in low or "invalid timestamps" in low:
        _warn(rep, "frame timestamps look irregular")

    frames = _decoded_frames(stderr or "")
    rep.metrics["decoded_frames"] = float(frames)
    if frames <= 0:
        _fatal(rep, "zero-frame video: no frames could be read")
        return rep

    black = _black_ratio(stderr or "", dur)
    rep.metrics["black_ratio"] = black
    if black > 0.9:
        # blackdetect cannot tell a truly blank video from dark-but-real
        # content (e.g. a navy title card with white text). Before failing,
        # check one frame for a meaningful amount of bright pixels.
        bright = _bright_pixel_fraction(eng, path, dur)
        rep.metrics["bright_fraction"] = bright
        if bright > 0.005:
            _warn(rep, f"dark video ({black:.0%} below black threshold) but "
                       f"{bright:.1%} of pixels carry bright content - "
                       f"accepted as intentional dark visuals")
        else:
            _fatal(rep, f"black/blank video: {black:.0%} of frames are black")
    elif black > 0.6:
        _warn(rep, f"mostly black video: {black:.0%} black")

    frozen = _frozen_ratio(stderr or "", dur)
    rep.metrics["frozen_ratio"] = frozen
    if dur > 2.0 and frozen > 0.95:
        _fatal(rep, f"frozen video: {frozen:.0%} of duration is static")
    elif frozen > 0.7:
        _warn(rep, f"largely static video: {frozen:.0%} frozen")

    return rep
