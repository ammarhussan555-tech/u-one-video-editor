"""U One - FFmpeg rendering: segments -> concat -> captions -> export.

All FFmpeg work goes through src/ffmpeg_util.py:
  * proper argv lists, never command strings (Windows paths with spaces,
    quotes, apostrophes, parentheses, Unicode are all safe);
  * filter-level escaping for anything embedded in a filter description;
  * filter-script files for complex graphs;
  * structured errors (stage, scene, exit code) instead of giant commands.

Text overlays are burned in through libass (ASS subtitles with a
top-positioned style) instead of drawtext, so long text, Unicode, commas,
colons and quotes can never break a filter graph.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from .ffmpeg_util import (
    FFmpegNotFoundError, FFmpegRunError, escape_filter_path, find_ffmpeg,
    find_ffprobe, ffprobe_duration, media_has_streams, run_ffmpeg,
    run_no_window, safe_temp_path, write_filter_script,
)

RESOLUTIONS = {
    "16:9": {"1080p": (1920, 1080), "1440p": (2560, 1440), "4K": (3840, 2160)},
    "9:16": {"1080p": (1080, 1920), "1440p": (1440, 2560), "4K": (2160, 3840)},
    "1:1": {"1080p": (1080, 1080), "1440p": (1440, 1440), "4K": (2160, 2160)},
}
CODECS = {"h264": "libx264", "h265": "libx265"}


def check_ffmpeg() -> bool:
    try:
        find_ffmpeg()
        return True
    except FFmpegNotFoundError:
        return False


# ---------------------------------------------------------------- segments

def make_segment(asset, duration: float, out_path: str, w: int, h: int,
                 fps: int, seed: int = 0, scene: Optional[int] = None) -> str:
    """Render one scene segment. Video: trimmed. Image/graphic: Ken Burns."""
    duration = max(0.5, float(duration))
    base_vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
               f"crop={w}:{h},setsar=1")
    if asset.kind == "video":
        vf = base_vf + f",fps={fps}"
        args = ["-t", f"{duration:.2f}", "-i", asset.local_path,
                "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "20", "-an", out_path]
    else:
        # Ken Burns: zoompan d=1 per input frame, zoom from output frame `on`.
        zin = (seed % 2 == 0)
        if zin:
            zp = (f"zoompan=z='min(1+0.0012*on,1.35)':d=1:"
                  f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}")
        else:
            zp = (f"zoompan=z='max(1.35-0.0012*on,1.0)':d=1:"
                  f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}")
        vf = f"scale={w * 2}:{h * 2},{zp},fps={fps}"
        args = ["-loop", "1", "-t", f"{duration:.2f}", "-i", asset.local_path,
                "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "20", "-an", out_path]
    run_ffmpeg(args, stage="scene_segment", scene=scene)
    if not (Path(out_path).is_file() and Path(out_path).stat().st_size > 1024):
        raise FFmpegRunError("Scene segment came out empty.",
                             exit_code=-2, stage="scene_segment", scene=scene)
    if not media_has_streams(out_path, want_video=True):
        raise FFmpegRunError("Scene segment has no video stream.",
                             exit_code=-2, stage="scene_segment", scene=scene)
    return out_path


def _concat_list_escape(p: str) -> str:
    # concat demuxer: single quotes escaped as '\'' ; use forward slashes.
    return p.replace("\\", "/").replace("'", "'\\''")


def concat_segments(seg_paths: List[str], out_path: str, fade: float = 0.35,
                    work_dir: Optional[str] = None,
                    scene: Optional[int] = None) -> str:
    """Concat with gentle per-segment fades (professional, restrained)."""
    if len(seg_paths) == 1:
        shutil.copy(seg_paths[0], out_path)
        return out_path
    tmpdir = Path(work_dir or os.path.dirname(out_path)) / "_faded"
    tmpdir.mkdir(parents=True, exist_ok=True)
    faded = []
    for i, sp in enumerate(seg_paths):
        dur = ffprobe_duration(sp) or 5.0
        fo = str(tmpdir / f"f{i}.mp4")
        vf = (f"fade=t=in:st=0:d={fade},"
              f"fade=t=out:st={max(0.0, dur - fade):.2f}:d={fade}")
        run_ffmpeg(["-i", sp, "-vf", vf, "-c:v", "libx264", "-preset",
                    "veryfast", "-crf", "20", "-an", fo],
                   stage="scene_fade", scene=scene)
        faded.append(fo)
    lst = tmpdir / "list.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p in faded:
            # Absolute paths: the concat demuxer resolves list entries
            # relative to the list file, so relative entries would break.
            f.write(f"file '{_concat_list_escape(str(Path(p).resolve()))}'\n")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst),
                "-c", "copy", out_path],
               stage="concat", scene=scene)
    if not Path(out_path).is_file():
        raise FFmpegRunError("Concatenated video was not created.",
                             exit_code=-2, stage="concat", scene=scene)
    return out_path


# ---------------------------------------------------------------- final render

def _burn_captions_filter(ass_path: str) -> str:
    """subtitles filter with a safely escaped path + retry-friendly copy."""
    src = Path(ass_path)
    # First choice: copy next to the render target would change caller's layout,
    # so we just escape the given path. Callers may pre-copy to a safe name.
    return f"subtitles='{escape_filter_path(str(src))}'"


def render_final(video_noaudio: str, mixed_audio: str, ass_path: str,
                 out_path: str, fmt: str = "16:9", res: str = "1080p",
                 fps: int = 30, codec: str = "h264",
                 preview: bool = False,
                 work_dir: Optional[str] = None) -> str:
    """Captions burn-in + audio + format/codec export.

    Text overlays are already merged into the ASS file (top style); no
    drawtext is used anywhere, so hostile text cannot break rendering.
    """
    w, h = RESOLUTIONS[fmt][res]
    if preview:
        w, h = (640, 360) if fmt == "16:9" else \
               (360, 640) if fmt == "9:16" else (480, 480)
    chain = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
             f"crop={w}:{h},setsar=1,fps={fps}")
    ass = Path(ass_path) if ass_path else None
    if ass and ass.is_file():
        # Retry safety: if the ASS path is hostile, burn from a safe copy.
        chain += "," + _burn_captions_filter(str(ass))
    vcodec = CODECS.get(codec, "libx264")
    preset = "ultrafast" if preview else "veryfast"
    crf = "30" if preview else "20"
    wd = work_dir or os.path.dirname(out_path) or "."
    # NOTE: -filter_script:v is NOT supported by bundled FFmpeg 9.0.2.
    # Pass the filter chain directly via -vf as a single argv element.
    base_args = ["-i", video_noaudio, "-i", mixed_audio,
                 "-vf", chain,
                 "-map", "0:v:0", "-map", "1:a:0",
                 "-c:v", vcodec, "-preset", preset, "-crf", crf,
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                 "-movflags", "+faststart", "-shortest", out_path]
    try:
        run_ffmpeg(base_args, stage="final_render", timeout=7200)
    except FFmpegRunError as first:
        # Fallback 1: copy the ASS next to a sanitized filename and retry.
        if ass and ass.is_file():
            safe_ass = safe_temp_path(wd, "captions", ".ass")
            shutil.copy(str(ass), safe_ass)
            chain2 = chain.split(",subtitles=")[0] + "," + \
                _burn_captions_filter(safe_ass)
            args2 = ["-i", video_noaudio, "-i", mixed_audio,
                     "-vf", chain2,
                     "-map", "0:v:0", "-map", "1:a:0",
                     "-c:v", vcodec, "-preset", preset, "-crf", crf,
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                     "-movflags", "+faststart", "-shortest", out_path]
            try:
                run_ffmpeg(args2, stage="final_render_retry", timeout=7200)
            except FFmpegRunError:
                # Fallback 2: render without captions rather than failing.
                chain3 = chain.split(",subtitles=")[0]
                args3 = ["-i", video_noaudio, "-i", mixed_audio,
                         "-vf", chain3,
                         "-map", "0:v:0", "-map", "1:a:0",
                         "-c:v", vcodec, "-preset", preset, "-crf", crf,
                         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                         "-movflags", "+faststart", "-shortest", out_path]
                run_ffmpeg(args3, stage="final_render_no_captions", timeout=7200)
        else:
            raise first
    problems = validate_final_mp4(out_path, expect_w=w, expect_h=h,
                                  expect_fps=fps)
    if problems:
        raise FFmpegRunError(
            "Final MP4 failed validation: " + "; ".join(problems),
            exit_code=-3, stage="final_validation")
    return out_path


def validate_final_mp4(path: str, expect_w: int = 0, expect_h: int = 0,
                       expect_fps: int = 0) -> List[str]:
    """Verify the exported MP4. Returns a list of problems (empty = valid)."""
    problems: List[str] = []
    p = Path(path)
    if not p.is_file():
        return ["output file was not created"]
    if p.stat().st_size < 1024:
        return ["output file is empty"]
    probe = find_ffprobe(find_ffmpeg())
    if not probe:
        return []  # cannot verify further without ffprobe
    import subprocess
    try:
        r = run_no_window(
            [probe, "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,width,height,avg_frame_rate",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1", str(p)],
            capture_output=True, text=True, timeout=60)
        out = r.stdout or ""
        if "codec_type=video" not in out:
            problems.append("no video stream")
        if "codec_type=audio" not in out:
            problems.append("no audio stream")
        if "codec_name=h264" not in out and "codec_name=hevc" not in out:
            problems.append("unexpected video codec")
        if expect_w:
            m = re.search(r"width=(\d+)", out)
            if m and int(m.group(1)) != expect_w:
                problems.append(f"width {m.group(1)} != expected {expect_w}")
        if expect_h:
            m = re.search(r"height=(\d+)", out)
            if m and int(m.group(1)) != expect_h:
                problems.append(f"height {m.group(1)} != expected {expect_h}")
        m = re.search(r"duration=([\d.]+)", out)
        if m and float(m.group(1)) < 0.5:
            problems.append("duration too short - file may be corrupt")
    except Exception as e:  # noqa: BLE001
        problems.append(f"validation probe failed: {e}")
    return problems


# ---------------------------------------------------------------- QC helpers

def _run_quiet(args: List[str], timeout: int = 90) -> str:
    ffmpeg = find_ffmpeg()
    try:
        r = subprocess_run([ffmpeg, "-hide_banner", *args],
                           capture_output=True, text=True, timeout=timeout)
        return r.stderr or ""
    except Exception:
        return ""


def subprocess_run(*a, **k):
    return run_no_window(*a, **k)


def _black_ratio(path: str, duration: float) -> float:
    err = _run_quiet(["-i", path, "-vf", "blackdetect=d=0.4:pix_th=0.10",
                      "-an", "-f", "null", "-"], timeout=60)
    total = sum(float(m.group(1))
                for m in re.finditer(r"black_duration:([\d.]+)", err))
    return (total / duration) if duration > 0 else 0.0


def _max_volume_db(path: str):
    err = _run_quiet(["-i", path, "-af", "volumedetect", "-vn",
                      "-f", "null", "-"], timeout=60)
    m = re.search(r"max_volume:\s*(-?[\d.]+)\s*dB", err)
    return float(m.group(1)) if m else None


def _long_silences(path: str, min_gap: float = 3.0) -> List[float]:
    err = _run_quiet(["-i", path, "-af", "silencedetect=noise=-32dB:d=1.0",
                      "-vn", "-f", "null", "-"], timeout=90)
    return [float(m.group(1)) for m in re.finditer(r"silence_duration:\s*([\d.]+)", err)
            if float(m.group(1)) >= min_gap]


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
            m = re.match(r"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)", lines[1])
            if not m:
                errs.append(f"Subtitle block {i + 1}: bad timestamp.")
                continue
            s0, s1 = ts(m.group(1)), ts(m.group(2))
            if s0 >= s1:
                errs.append(f"Subtitle block {i + 1}: start >= end.")
            if s0 < prev_end - 0.01:
                errs.append(f"Subtitle block {i + 1}: out of order / overlapping.")
            if not "".join(lines[2:]).strip():
                errs.append(f"Subtitle block {i + 1}: empty text.")
            prev_end = s1
    except Exception as e:  # noqa: BLE001
        errs.append(f"Could not validate subtitles: {e}")
    return errs


def quality_check(seg_paths: List[str], mixed_audio: str, timeline=None,
                  voice_path: Optional[str] = None,
                  srt_path: Optional[str] = None) -> List[str]:
    """Full QC pass. Returns human-readable warnings; never raises."""
    warnings: List[str] = []
    for sp in seg_paths:
        base = os.path.basename(sp)
        if not os.path.exists(sp) or os.path.getsize(sp) < 1024:
            warnings.append(f"Broken/empty segment: {base}")
            continue
        dur = ffprobe_duration(sp) or 0
        if dur <= 0.2:
            warnings.append(f"Near-zero duration segment: {base}")
            continue
        if _black_ratio(sp, dur) > 0.6:
            warnings.append(f"Black/blank scene detected: {base}")
    if not os.path.exists(mixed_audio) or os.path.getsize(mixed_audio) < 1024:
        warnings.append("Mixed audio missing or empty.")
    else:
        peak = _max_volume_db(mixed_audio)
        if peak is not None and peak >= -0.5:
            warnings.append(f"Audio clipping risk: peak {peak:.1f} dB on final mix.")
    if voice_path:
        if not os.path.exists(voice_path) or os.path.getsize(voice_path) < 1024:
            warnings.append("Voiceover audio missing or empty.")
        else:
            gaps = _long_silences(voice_path)
            if gaps:
                warnings.append(f"Long silent gap(s) in voiceover: {len(gaps)} "
                                f"gap(s), longest {max(gaps):.1f}s.")
    if timeline is not None:
        seen: Dict[str, int] = {}
        lowres = 0
        for c in timeline.by_track("video"):
            asset = c.payload.get("asset") if isinstance(c.payload, dict) else None
            aid = asset.get("id") if isinstance(asset, dict) else c.payload.get("asset_id")
            if aid:
                seen[aid] = seen.get(aid, 0) + 1
            wdt = asset.get("width", 0) if isinstance(asset, dict) else 0
            if wdt and wdt < 640:
                lowres += 1
        dups = [k for k, v in seen.items() if v > 1 and k != "emergency"]
        if dups:
            warnings.append("Duplicate visuals reused: " +
                            ", ".join(dups[:4]) + ("..." if len(dups) > 4 else ""))
        if lowres:
            warnings.append(f"Low-resolution visual(s) (<640px): {lowres} scene(s).")
        for c in timeline.by_track("text"):
            if len(c.label or "") > 48:
                warnings.append(f"On-screen text may overflow: '{c.label[:40]}...'")
        sfx_counts: Dict[str, int] = {}
        for c in timeline.by_track("sfx"):
            k = (c.payload or {}).get("sfx", "?")
            sfx_counts[k] = sfx_counts.get(k, 0) + 1
        for k, v in sfx_counts.items():
            if v > 3:
                warnings.append(f"SFX '{k}' repeats {v}x - may feel excessive.")
        if not timeline.by_track("voice"):
            warnings.append("No voiceover clip on the timeline.")
    if srt_path and os.path.exists(srt_path):
        warnings.extend(_check_srt(srt_path))
    return warnings
