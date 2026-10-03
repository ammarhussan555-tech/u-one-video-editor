"""U One - scene generator with validation and automatic recovery (v2).

Per scene:
    search asset -> render segment -> VALIDATE ->
    invalid? -> recover (max 3 attempts: alternate query, regenerate,
                generated fallback) -> validate again ->
    still invalid? -> guaranteed-valid bright fallback card.

A scene NEVER reaches the render queue unvalidated. The only way
generate_all() raises is if even the pre-validated fallback card fails,
which is treated as a fatal environment problem (disk/ffmpeg broken).
"""
from __future__ import annotations

import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .error_recovery import RenderCancelled, RetryPolicy, SceneFailedError
from .ffmpeg_engine import FFmpegEngine
from .scene_validator import SegmentReport, validate_segment

# Bright branded fallback: luma ~0.30, far above the black threshold,
# subtly animated (slow zoom) so freeze detection never fires.
_FALLBACK_COLOR = "0x274b73"


@dataclass
class SceneSpec:
    index: int          # 0-based position in the video
    scene_id: int
    text: str
    duration: float     # seconds this scene occupies
    keyword_insert: Optional[str] = None  # pop-up keyword (rule 10)


@dataclass
class SceneResult:
    spec: SceneSpec
    segment_path: str
    asset_id: str
    attempts: int = 1
    recovered: bool = False
    report: Optional[SegmentReport] = None
    warnings: List[str] = field(default_factory=list)
    asset: Any = None  # the Asset that produced this segment (None for fallback)


class SceneGenerator:
    """Renders + validates + recovers one segment per scene."""

    def __init__(self, *, seg_dir: str, assets_dir: str,
                 width: int, height: int, fps: int,
                 search_fn: "Callable[[SceneSpec, int], object]",
                 render_log=None,
                 cancel_event: Optional[threading.Event] = None,
                 max_workers: Optional[int] = None,
                 cache=None):
        self.seg_dir = seg_dir
        self.assets_dir = assets_dir
        self.w, self.h, self.fps = width, height, fps
        self.search_fn = search_fn          # (spec, attempt) -> asset-like
        self.log = render_log
        self.cancel_event = cancel_event or threading.Event()
        cpu = os.cpu_count() or 4
        self.max_workers = max_workers or max(1, min(8, cpu * 2))
        self.cache = cache
        self._fallback_card: Optional[str] = None
        self._lock = threading.Lock()

    # -- logging helper --
    def _msg(self, idx: int, text: str) -> None:
        if self.log:
            try:
                self.log.scene(idx + 1, text)
            except Exception:
                pass

    # -- segment rendering (engine-based, cancel-aware) --
    def render_segment(self, asset, duration: float, out_path: str,
                       seed: int, scene: int,
                       engine: FFmpegEngine) -> str:
        duration = max(0.5, float(duration))
        w, h, fps = self.w, self.h, self.fps
        kind = getattr(asset, "kind", "image")
        src = getattr(asset, "local_path", "")
        # -- SPEED: stream-copy fast path for already-compliant video. --
        # Re-encoding every scene with libx264 is the slowest CPU stage;
        # if the source already matches the target spec, a stream-copy
        # trim is ~100x faster and lossless. (Uzair 2026-10-03: 2-min
        # video must render in 2-3 min on a slow GPU.)
        if kind == "video" and src and os.path.isfile(src):
            try:
                from . import media_probe
                info = media_probe.probe(src)
                v = info.video
                if (v is not None and v.width == w and v.height == h
                        and abs((v.fps or 0) - fps) < 0.5
                        and (v.codec or "").lower() in ("h264", "avc")):
                    engine.run(
                        ["-ss", "0", "-i", src, "-t", f"{duration:.2f}",
                         "-c", "copy", "-an", out_path],
                        stage="scene_segment_fastcopy", scene=scene,
                        inputs=[src], output=out_path, timeout=120)
                    return out_path
            except Exception:  # noqa: BLE001 - fall through to re-encode
                pass
        if kind == "video":
            vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                  f"crop={w}:{h},setsar=1,fps={fps}")
            # SPEED: prefer the hardware encoder (3-5x faster than
            # libx264); fall back to CPU veryfast. (Uzair 2026-10-03.)
            hw = self._hw_encoder(engine)
            if hw:
                v_args = ["-c:v", hw, "-b:v", "10M"]
            else:
                v_args = ["-c:v", "libx264", "-preset", "veryfast",
                          "-crf", "20"]
            args = ["-t", f"{duration:.2f}", "-i", src,
                    "-vf", vf, *v_args,
                    "-pix_fmt", "yuv420p", "-an", out_path]
        else:
            zin = (seed % 2 == 0)
            if zin:
                zp = (f"zoompan=z='min(1+0.0012*on,1.35)':d=1:"
                      f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}")
            else:
                zp = (f"zoompan=z='max(1.35-0.0012*on,1.0)':d=1:"
                      f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}")
            # SPEED: ultrafast + 1x scale (was veryfast + 2x). The 2x
            # supersample is invisible after the final 1080p encode but
            # doubles the zoompan cost. (Uzair 2026-10-03 speed demand.)
            vf = f"scale={w}:{h},{zp},fps={fps}"
            hw = self._hw_encoder(engine)
            if hw:
                v_args = ["-c:v", hw, "-b:v", "10M"]
            else:
                v_args = ["-c:v", "libx264", "-preset", "ultrafast",
                          "-crf", "20"]
            args = ["-loop", "1", "-t", f"{duration:.2f}", "-i", src,
                    "-vf", vf, *v_args,
                    "-pix_fmt", "yuv420p", "-an", out_path]
        engine.run(args, stage="scene_segment", scene=scene, inputs=[src],
                   output=out_path, timeout=600)
        return out_path

    def _hw_encoder(self, engine: FFmpegEngine) -> Optional[str]:
        """Hardware h264 encoder name, or None (cached per instance)."""
        if not hasattr(self, "_hw_enc_cache"):
            try:
                self._hw_enc_cache = engine.detect_hw_encoder("h264")
            except Exception:  # noqa: BLE001
                self._hw_enc_cache = None
        return self._hw_enc_cache

    # -- guaranteed-valid bright fallback card --
    def fallback_card(self, engine: FFmpegEngine) -> str:
        """A bright, subtly-animated branded card. Validated once, reused."""
        with self._lock:
            if self._fallback_card and os.path.isfile(self._fallback_card):
                return self._fallback_card
            card = os.path.join(self.assets_dir, "_fallback_card.mp4")
            dur = 6.0
            vf = (f"scale={self.w * 2}:{self.h * 2},"
                  f"zoompan=z='min(1+0.0008*on,1.2)':d=1:"
                  f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                  f"s={self.w}x{self.h},fps={self.fps}")
            engine.run(
                ["-f", "lavfi", "-i",
                 f"color=c={_FALLBACK_COLOR}:s={self.w}x{self.h}:"
                 f"r={self.fps}:d={dur:.1f}",
                 "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "20", "-pix_fmt", "yuv420p", "-an", card],
                stage="fallback_card", timeout=300, output=card)
            rep = validate_segment(card, engine=engine,
                                   expect_w=self.w, expect_h=self.h,
                                   min_duration=1.0)
            if not rep.valid:
                raise SceneFailedError(
                    "The emergency fallback visual itself failed validation: "
                    + rep.describe(), stage="fallback_card",
                    recovery_hint="FFmpeg or disk may be broken; "
                                 "reinstall U One")
            self._fallback_card = card
            return card

    # -- per-scene pipeline --
    def _attempt(self, spec: SceneSpec, attempt: int,
                 engine: FFmpegEngine) -> SceneResult:
        seg = os.path.join(self.seg_dir, f"seg_{spec.index:03d}.mp4")
        asset = self.search_fn(spec, attempt)
        aid = str(getattr(asset, "id", "?"))
        # cache: reuse a previously validated segment for the same asset
        if self.cache is not None:
            try:
                from .cache_manager import (lookup_segment, segment_cache_key,
                                            store_segment)
                key = segment_cache_key(
                    aid, getattr(asset, "local_path", ""), spec.duration,
                    self.w, self.h, self.fps, spec.index)
                hit = lookup_segment(key, require_valid=True)
                if hit:
                    shutil.copy(hit, seg)
                    self._msg(spec.index,
                              f"Scene {spec.scene_id + 1}: reused cached "
                              f"segment.")
                    from .scene_validator import SegmentReport
                    return SceneResult(spec=spec, segment_path=seg,
                                       asset_id=aid, attempts=attempt + 1,
                                       report=SegmentReport(path=seg,
                                                            valid=True),
                                       asset=asset)
            except Exception:
                pass
        self.render_segment(asset, spec.duration, seg, seed=spec.index,
                            scene=spec.scene_id + 1, engine=engine)
        # Keyword insert pop-up (Uzair rule 10): burn a 0.8s styled
        # keyword overlay into the segment if one was detected.
        if getattr(spec, "keyword_insert", None):
            try:
                seg = _burn_keyword_popup(
                    seg, spec.keyword_insert, spec.duration,
                    self.w, self.h, engine)
            except Exception:  # noqa: BLE001 - pop-up is optional
                pass
        rep = validate_segment(seg, engine=engine,
                               expect_w=self.w, expect_h=self.h,
                               expect_fps=float(self.fps),
                               min_duration=0.4)
        if rep.valid and self.cache is not None:
            try:
                from .cache_manager import segment_cache_key, store_segment
                key = segment_cache_key(
                    aid, getattr(asset, "local_path", ""), spec.duration,
                    self.w, self.h, self.fps, spec.index)
                store_segment(key, seg, validation={
                    "valid": True,
                    "black_ratio": rep.metrics.get("black_ratio", 0),
                    "duration": rep.metrics.get("duration", 0)})
            except Exception:
                pass
        return SceneResult(spec=spec, segment_path=seg,
                           asset_id=aid,
                           attempts=attempt + 1, report=rep,
                           asset=asset)

    def generate_one(self, spec: SceneSpec) -> SceneResult:
        engine = FFmpegEngine(cancel_event=self.cancel_event,
                              log=self.log)
        policy = RetryPolicy(max_attempts=3, backoff_s=0.5)
        attempt_box = [0]

        def one_try() -> SceneResult:
            attempt = attempt_box[0]
            attempt_box[0] += 1
            res = self._attempt(spec, attempt, engine)
            if not res.report or not res.report.valid:
                raise SceneFailedError(
                    (res.report.describe() if res.report else "no report"),
                    stage="scene_validate", scene=spec.scene_id + 1)
            return res

        def on_retry(n, e):
            reason = e.describe() if isinstance(e, SceneFailedError) else str(e)
            self._msg(spec.index,
                      f"Scene {spec.scene_id + 1} invalid ({reason}); "
                      f"recovery attempt {n}/3.")

        try:
            res = policy.run(one_try, what=f"scene {spec.scene_id + 1}",
                             on_retry=on_retry)
            if res.attempts > 1:
                res.recovered = True
            return res
        except RenderCancelled:
            raise
        except Exception as e:  # noqa: BLE001 - fallback is the recovery
            reason = e.describe() if isinstance(e, SceneFailedError) else str(e)
            self._msg(spec.index,
                      f"Scene {spec.scene_id + 1}: recovery exhausted "
                      f"({reason}); using guaranteed fallback card.")
            card = self.fallback_card(engine)
            seg = os.path.join(self.seg_dir, f"seg_{spec.index:03d}.mp4")
            # trim (or loop) the card to this scene's exact duration so the
            # timeline stays in sync - a raw copy would skew every timing
            # downstream (concat duration, caption sync, voiceover sync).
            engine.run(
                ["-stream_loop", "2", "-i", card,
                 "-t", f"{spec.duration:.3f}",
                 "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                 "-pix_fmt", "yuv420p", "-an", seg],
                stage="scene_fallback_trim", scene=spec.scene_id + 1,
                inputs=[card], output=seg, timeout=300)
            rep = validate_segment(seg, engine=engine,
                                   expect_w=self.w, expect_h=self.h,
                                   min_duration=0.4)
            if not rep.valid:  # practically impossible; card is pre-validated
                raise SceneFailedError(
                    "Fallback card copy failed validation: " + rep.describe(),
                    stage="scene_recovery", scene=spec.scene_id + 1)
            return SceneResult(spec=spec, segment_path=seg, asset_id="fallback",
                               attempts=3, recovered=True, report=rep,
                               warnings=[f"scene used fallback card: {reason}"])

    def generate_all(self, specs: List[SceneSpec],
                     progress_cb=None) -> List[SceneResult]:
        """Generate+validate all scenes in parallel (bounded workers).

        Returns results in scene order. Every result is a VALID segment.
        """
        os.makedirs(self.seg_dir, exist_ok=True)
        results: Dict[int, SceneResult] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {ex.submit(self.generate_one, s): s for s in specs}
            done = 0
            for fut in as_completed(futs):
                if self.cancel_event.is_set():
                    raise RenderCancelled("cancelled during scene generation")
                spec = futs[fut]
                results[spec.index] = fut.result()  # raises on fatal
                done += 1
                if progress_cb:
                    progress_cb(done, len(specs), spec.index)
        return [results[i] for i in sorted(results)]

# -- Keyword insert pop-up (Uzair 2026-10-03 rule 10) --
# A 0.8s styled pop-up for one important keyword per scene, burned via
# libass (same mechanism as captions, no drawtext font issues).

def _keyword_ass(keyword: str, duration: float, w: int, h: int) -> str:
    """Build a minimal ASS with one pop-up event for the keyword."""
    # Show at 30% into the scene, for 0.8s (or less if scene is short).
    start = max(0.2, duration * 0.3)
    end = min(duration - 0.1, start + 0.8)
    if end <= start:
        start, end = 0.2, min(duration - 0.1, 1.0)
    def _ts(s):
        cs = int(round((s - int(s)) * 100))
        return f"0:00:{int(s):02d}.{cs:02d}"
    # Escape ASS special chars.
    kw = keyword.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Popup,Arial,{max(48, h // 12)},&H00FFFFFF,&H000000FF,&H80000000,&H80000000,-1,0,3,3,0,5,40,40,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,{_ts(start)},{_ts(end)},Popup,,0,0,0,,{kw}
"""


def _burn_keyword_popup(seg_path: str, keyword: str, duration: float,
                        w: int, h: int, engine: FFmpegEngine) -> str:
    """Burn a keyword pop-up into a segment. Returns the new path."""
    ass_text = _keyword_ass(keyword, duration, w, h)
    ass_path = seg_path + ".kw.ass"
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_text)
    out_path = seg_path + ".kw.mp4"
    # Escape for subtitles filter (Windows paths need special handling).
    ass_esc = ass_path.replace("\\", "/").replace(":", "\\:")
    vf = f"subtitles='{ass_esc}'"
    hw = None
    try:
        # Reuse the generator's hw detection via a temp instance check.
        pass
    except Exception:
        pass
    engine.run(
        ["-i", seg_path, "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "20", "-pix_fmt", "yuv420p", "-an", out_path],
        stage="keyword_popup", inputs=[seg_path], output=out_path,
        timeout=120)
    try:
        os.remove(ass_path)
    except Exception:
        pass
    return out_path
