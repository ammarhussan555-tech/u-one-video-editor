"""U One - media normalizer (v2).

Before concatenation, every segment is normalized to one common spec so
the concat demuxer (stream copy) can never produce a franken-file from
heterogeneous inputs.

Fast path: if the probed file already matches the target spec, it is
returned untouched (no re-encode). Otherwise only the mismatching
aspects are re-encoded.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from . import media_probe
from .ffmpeg_engine import FFmpegEngine


@dataclass
class TargetSpec:
    width: int
    height: int
    fps: float
    vcodec: str = "libx264"
    pix_fmt: str = "yuv420p"
    preset: str = "veryfast"
    crf: int = 20
    acodec: str = "aac"
    sample_rate: int = 48000
    channels: int = 2


def mismatch_reasons(info: "media_probe.MediaInfo",
                     spec: TargetSpec,
                     expect_audio: bool = False) -> List[str]:
    """List every way a file differs from the target spec."""
    reasons: List[str] = []
    if not info.has_video:
        return ["no video stream"]
    v = info.video
    assert v is not None
    if v.width != spec.width or v.height != spec.height:
        reasons.append(f"resolution {v.width}x{v.height}")
    if v.fps and abs(v.fps - spec.fps) > 1.0:
        reasons.append(f"fps {v.fps:.1f}")
    if v.pix_fmt != spec.pix_fmt:
        reasons.append(f"pix_fmt {v.pix_fmt or '?'}")
    # codec family check: accept h264/avc, hevc/h265 as-is only when the
    # target vcodec matches; otherwise re-encode.
    fam = {"libx264": "h264", "h264_nvenc": "h264", "h264_qsv": "h264",
           "h264_amf": "h264", "libx265": "hevc", "hevc_nvenc": "hevc"}
    want = fam.get(spec.vcodec, spec.vcodec)
    if v.codec != want:
        reasons.append(f"vcodec {v.codec or '?'}")
    if expect_audio:
        if not info.has_audio:
            reasons.append("missing audio")
        else:
            a = info.audio
            assert a is not None
            if a.sample_rate != spec.sample_rate:
                reasons.append(f"sample_rate {a.sample_rate}")
            if a.channels != spec.channels:
                reasons.append(f"channels {a.channels}")
    return reasons


def normalize_segment(path: str, spec: TargetSpec, out_path: str,
                      engine: Optional[FFmpegEngine] = None,
                      expect_audio: bool = False,
                      scene: Optional[int] = None) -> str:
    """Normalize one segment to the target spec. Returns out_path.

    Returns `path` unchanged when already compliant (fast path).
    Raises FFmpegError on encode failure.
    """
    eng = engine or FFmpegEngine()
    info = media_probe.probe(path, eng.ffprobe)
    reasons = mismatch_reasons(info, spec, expect_audio)
    if not reasons:
        return path  # fast path: stream-copy safe already

    vf = (f"scale={spec.width}:{spec.height}:force_original_aspect_ratio=increase,"
          f"crop={spec.width}:{spec.height},setsar=1,fps={spec.fps}")
    args = ["-i", path, "-map", "0:v:0", "-vf", vf,
            "-c:v", spec.vcodec, "-preset", spec.preset, "-crf", str(spec.crf),
            "-pix_fmt", spec.pix_fmt]
    if expect_audio and info.has_audio:
        args += ["-map", "0:a:0?", "-c:a", spec.acodec,
                 "-ar", str(spec.sample_rate), "-ac", str(spec.channels),
                 "-b:a", "128k"]
    else:
        args += ["-an"]
    args.append(out_path)
    eng.run(args, stage="normalize", scene=scene, inputs=[path],
            output=out_path, timeout=600)
    return out_path


def normalize_all(paths: List[str], spec: TargetSpec, work_dir: str,
                  engine: Optional[FFmpegEngine] = None,
                  scene_offset: int = 0) -> List[str]:
    """Normalize every segment; returns the list of render-ready paths."""
    import os
    eng = engine or FFmpegEngine()
    norm_dir = os.path.join(work_dir, "_normalized")
    os.makedirs(norm_dir, exist_ok=True)
    out = []
    for i, p in enumerate(paths):
        dest = os.path.join(norm_dir, f"n{i:03d}.mp4")
        out.append(normalize_segment(p, spec, dest, eng,
                                    scene=scene_offset + i + 1))
    return out
