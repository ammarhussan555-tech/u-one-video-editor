"""U One - export engine (v2).

Final render pipeline:
    1. PRE-FLIGHT: probe render inputs (video must have a valid video
       stream + duration; audio must have a valid audio stream). A bad
       input is reported exactly instead of failing blindly inside ffmpeg.
    2. RENDER: captions burn-in (libass) + audio mux + format export.
       Hardware encoder when available, automatic CPU fallback.
       Caption burn-in retries: hostile-path copy, then no-captions.
    3. VALIDATE: full final-MP4 checklist (§12). Any failure -> exact reason.
    4. DELIVER: copy to the user's Videos folder with a timestamped name.

Raises UOneError with stage/scene/exit_code/reason/recovery_hint.
"""
from __future__ import annotations

import datetime
import os
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from . import media_probe
from .error_recovery import RenderCancelled, UOneError, ValidationError
from .ffmpeg_engine import (FFmpegEngine, FFmpegError, escape_filter_path,
                            safe_temp_path, write_filter_script)

RESOLUTIONS = {
    "16:9": {"360p": (640, 360), "480p": (854, 480), "720p": (1280, 720),
             "1080p": (1920, 1080), "1440p": (2560, 1440),
             "4K": (3840, 2160)},
    "9:16": {"360p": (360, 640), "480p": (480, 854), "720p": (720, 1280),
             "1080p": (1080, 1920), "1440p": (1440, 2560),
             "4K": (2160, 3840)},
    "1:1": {"360p": (360, 360), "480p": (480, 480), "720p": (720, 720),
            "1080p": (1080, 1080), "1440p": (1440, 1440),
            "4K": (2160, 2160)},
}
_CPU_CODECS = {"h264": "libx264", "h265": "libx265"}
_HW_BITRATE = {"1080p": "10M", "1440p": "18M", "4K": "35M"}


@dataclass
class ExportResult:
    path: str
    duration: float
    width: int
    height: int
    used_hwaccel: bool
    captions_burned: bool
    clean_video_path: str = ""  # video+audio WITHOUT burned captions (for post-render caption editing)


def _burn_captions_filter(ass_path: str) -> str:
    return f"subtitles='{escape_filter_path(str(Path(ass_path)))}'"


class ExportEngine:
    def __init__(self, engine: Optional[FFmpegEngine] = None,
                 render_log=None,
                 cancel_event: Optional[threading.Event] = None):
        self.engine = engine or FFmpegEngine(cancel_event=cancel_event,
                                             log=render_log)
        self.log = render_log

    def _msg(self, text: str) -> None:
        if self.log:
            try:
                self.log.write(text)
            except Exception:
                pass

    # -- stage 1: pre-flight --
    def _probe_video(self, path: str) -> "scene_validator.SegmentReport":
        from . import scene_validator
        return scene_validator.validate_segment(
            path, engine=self.engine, expect_w=None, expect_h=None,
            min_duration=0.3, require_audio=False)

    def _probe_audio(self, path: str) -> None:
        info = media_probe.probe(path, self.engine.ffprobe)
        if not info.exists or info.size <= 0:
            raise ValidationError(f"Render input audio is missing: {path}",
                                  stage="preflight",
                                  recovery_hint="re-run audio mixing")
        if info.probe_error or not info.has_audio:
            raise ValidationError(
                f"Render input audio is not valid: "
                f"{os.path.basename(path)} "
                f"({info.probe_error or 'no audio stream'})",
                stage="preflight",
                recovery_hint="re-run audio mixing")
        # decode smoke test: the container may lie, the decoder may not
        self.engine.run(["-i", path, "-t", "2", "-f", "null", "-"],
                        stage="preflight_audio", timeout=120)

    def preflight(self, video_path: str, audio_path: str,
                  expect_audio: bool = True):
        """Validate render inputs. Raises ValidationError naming the input."""
        rep = self._probe_video(video_path)
        if not rep.valid:
            first = rep.fatals[0] if rep.fatals else "unknown"
            raise ValidationError(
                f"Render input video is not valid: "
                f"{os.path.basename(video_path)} - {first}",
                stage="preflight",
                recovery_hint="re-run scene generation; see render log")
        if expect_audio:
            self._probe_audio(audio_path)
        self._msg(f"Pre-flight OK: {rep.describe()}")
        return rep

    # -- stage 2: render --
    def _video_args(self, codec: str, res: str, preview: bool,
                    use_hw: bool, hw_enc: Optional[str]) -> List[str]:
        if use_hw and hw_enc:
            return ["-c:v", hw_enc, "-b:v",
                    _HW_BITRATE.get(res, "10M")]
        vcodec = _CPU_CODECS.get(codec, "libx264")
        # "veryfast" is ~2-3x faster than "medium" with negligible quality
        # loss at CRF 20. "medium" made renders painfully slow on Windows.
        preset = "ultrafast" if preview else "veryfast"
        crf = "30" if preview else "20"
        return ["-c:v", vcodec, "-preset", preset, "-crf", crf]

    def render_final(self, video_noaudio: str, mixed_audio: str,
                     ass_path: str, out_path: str,
                     fmt: str = "16:9", res: str = "1080p", fps: int = 30,
                     codec: str = "h264", preview: bool = False,
                     work_dir: str = ".",
                     prefer_hw: bool = True) -> ExportResult:
        self.preflight(video_noaudio, mixed_audio, expect_audio=True)

        w, h = RESOLUTIONS[fmt][res]
        if preview:
            w, h = (640, 360) if fmt == "16:9" else \
                   (360, 640) if fmt == "9:16" else (480, 480)
        base_chain = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                      f"crop={w}:{h},setsar=1,fps={fps}")
        ass = Path(ass_path) if ass_path else None
        captions = bool(ass and ass.is_file())

        hw_enc = self.engine.detect_hw_encoder(
            "hevc" if codec == "h265" else "h264") if prefer_hw else None
        use_hw = bool(hw_enc)
        if use_hw:
            self._msg(f"Hardware encoder available: {hw_enc}; "
                      f"CPU fallback ready.")

        def attempt_clean(tag: str, hw: bool,
                          enc: Optional[str], dest: str) -> None:
            # NOTE: -filter_script:v is NOT supported by the bundled FFmpeg
            # 9.0.2 (essentials build). Pass the filter chain directly via
            # -vf as a single argv element.
            args = ["-i", video_noaudio, "-i", mixed_audio,
                    "-vf", base_chain,
                    "-map", "0:v:0", "-map", "1:a:0",
                    *self._video_args(codec, res, preview, hw, enc),
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", "-shortest", dest]
            self.engine.run(args, stage=f"final_render_{tag}", timeout=7200,
                            inputs=[video_noaudio, mixed_audio], output=dest)

        def attempt_burn(vf_chain: str, tag: str, src: str,
                         dest: str) -> None:
            # Caption burn-in from the clean video: re-encode video only,
            # copy audio (fast).
            args = ["-i", src,
                    "-vf", vf_chain,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "copy",
                    "-movflags", "+faststart", "-shortest", dest]
            self.engine.run(args, stage=f"final_render_{tag}", timeout=7200,
                            inputs=[src], output=dest)

        def try_call(fn, *a, **k) -> bool:
            """One render attempt. Returns True on success; logs failures."""
            try:
                fn(*a, **k)
                return True
            except RenderCancelled:
                raise
            except FFmpegError as e:
                self._msg(f"Render attempt failed (exit {e.exit_code}); "
                          f"see log.")
                if self.log:
                    self.log.ffmpeg_error(e)
                self._last_error = e
                return False

        self._last_error: Optional[FFmpegError] = None
        captions_burned = captions
        used_hw = use_hw
        clean_path = os.path.join(work_dir, "clean_nocap.mp4")

        # ---- Pass 1: clean render (video + audio, NO burned captions). ----
        # The clean copy is kept so captions can be re-styled after the
        # render without re-rendering the whole video.
        self._msg("Final render pass 1/2: clean video (no captions)...")
        ok = try_call(attempt_clean, "clean", use_hw, hw_enc, clean_path)
        if not ok and use_hw:
            self._msg(f"Hardware encoder {hw_enc} failed; "
                      f"falling back to CPU.")
            ok = try_call(attempt_clean, "clean_cpu", False, None, clean_path)
            used_hw = False
        if not ok:
            last = self._last_error
            raise UOneError(
                "U One could not finish the final video file. "
                "Your project has been saved - the render log has "
                "the technical details.",
                stage="final_render",
                exit_code=last.exit_code if last else -1,
                reason=last.describe() if last else "unknown render failure",
                recovery_hint="check the render log; re-run scene "
                              "generation if an input was bad",
                stderr_tail=last.stderr_tail if last else "")

        # ---- Pass 2: burn captions onto the clean video. ----
        if captions:
            self._msg("Final render pass 2/2: burning captions...")
            burn_chain = _burn_captions_filter(str(ass))
            ok = try_call(attempt_burn, burn_chain, "burn", clean_path,
                          out_path)
            # Fallback: ASS path hostile -> sanitized copy, retry burn-in.
            if not ok:
                try:
                    safe_ass = safe_temp_path(work_dir, "captions", ".ass")
                    shutil.copy(str(ass), safe_ass)
                    ok = try_call(attempt_burn,
                                  _burn_captions_filter(safe_ass),
                                  "burn_safe", clean_path, out_path)
                except OSError as e:
                    self._msg(f"Could not stage sanitized captions: {e}")
            # Fallback: deliver the clean video rather than failing.
            if not ok:
                shutil.copy2(clean_path, out_path)
                captions_burned = False
                self._msg("Rendered without burned-in captions "
                          "(caption burn-in failed).")
        else:
            shutil.copy2(clean_path, out_path)
            captions_burned = False

        problems = self.validate_final_mp4(out_path, expect_w=w, expect_h=h,
                                           expect_fps=fps, expect_audio=True)
        if problems:
            raise ValidationError(
                "Final MP4 failed validation: " + "; ".join(problems),
                stage="final_validation",
                recovery_hint="see render log; retry the render")
        info = media_probe.probe(out_path, self.engine.ffprobe)
        v = info.video
        return ExportResult(path=out_path,
                            duration=info.duration or 0.0,
                            width=v.width if v else w,
                            height=v.height if v else h,
                            used_hwaccel=used_hw,
                            captions_burned=captions_burned,
                            clean_video_path=clean_path)

    # -- stage 3: final MP4 validation (§12 checklist) --
    def validate_final_mp4(self, path: str, expect_w: int = 0,
                           expect_h: int = 0, expect_fps: int = 0,
                           expect_audio: bool = True) -> List[str]:
        problems: List[str] = []
        p = Path(path)
        if not p.is_file():
            return ["output file was not created"]
        if p.stat().st_size < 1024:
            return ["output file is empty"]
        info = media_probe.probe(str(p), self.engine.ffprobe)
        if info.probe_error:
            return [f"output container unreadable: {info.probe_error}"]
        if not info.has_video:
            problems.append("no video stream")
        else:
            v = info.video
            assert v is not None
            if v.width <= 0 or v.height <= 0:
                problems.append("video stream has invalid dimensions")
            if expect_w and v.width != expect_w:
                problems.append(f"width {v.width} != expected {expect_w}")
            if expect_h and v.height != expect_h:
                problems.append(f"height {v.height} != expected {expect_h}")
            if expect_fps and v.fps and abs(v.fps - expect_fps) > 1.0:
                problems.append(f"fps {v.fps:.1f} != expected {expect_fps}")
            if v.codec not in ("h264", "hevc"):
                problems.append(f"unexpected video codec: {v.codec}")
        if expect_audio:
            if not info.has_audio:
                problems.append("no audio stream (audio required)")
            elif (info.audio.duration or 0) <= 0:
                problems.append("audio stream has invalid duration")
        if (info.duration or 0) < 0.5:
            problems.append("duration too short - file may be corrupt")
        if info.has_video and info.has_audio:
            vd = info.video.duration or info.duration
            ad = info.audio.duration or info.duration
            if vd > 0 and abs(vd - ad) > max(2.0, vd * 0.1):
                problems.append(
                    f"audio/video duration mismatch: video {vd:.1f}s vs "
                    f"audio {ad:.1f}s")
        # readable frames: decode a sample
        try:
            self.engine.run(
                ["-i", str(p), "-map", "0:v:0", "-frames:v", "30",
                 "-f", "null", "-"],
                stage="final_frame_check", timeout=300, log_stderr=False)
        except FFmpegError as e:
            problems.append(f"video frames not decodable "
                            f"(ffmpeg exit {e.exit_code})")
        except RenderCancelled:
            raise
        return problems

    # -- stage 4: deliver --
    def deliver_to_videos(self, path: str, title: str = "U One video") -> str:
        """Copy the finished MP4 to the user's Videos folder."""
        videos = Path.home() / "Videos"
        try:
            videos.mkdir(parents=True, exist_ok=True)
        except OSError:
            return path
        safe = re.sub(r"[^\w\- ]+", "", title).strip() or "U One video"
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = videos / f"{safe} {stamp}.mp4"
        try:
            shutil.copy2(path, dest)
            self._msg(f"Saved a copy to {dest}")
            return str(dest)
        except OSError:
            return path
