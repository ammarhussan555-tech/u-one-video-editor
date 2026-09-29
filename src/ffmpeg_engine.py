"""U One - robust FFmpeg engine (v2).

Every FFmpeg process captures: full command, stdout, stderr, exit code,
execution time, input files and output file. Stderr is NEVER hidden: it is
returned in the result, written to the render log, and its tail is attached
to every structured error.

Cancellation is real: run() drives the child via Popen and terminates it
when the cancel event fires (terminate, then kill after a grace period).

Hardware encoding is opportunistic: detect_hw_encoder() probes for
h264_nvenc / h264_qsv / h264_amf and the export engine uses it only when
present; any hardware failure falls back to CPU (libx264) with a log line.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

from .ffmpeg_util import popen_no_window, run_no_window
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# ---------------------------------------------------------------- errors

class FFmpegNotFoundError(RuntimeError):
    pass


from .error_recovery import RenderCancelled


class FFmpegError(RuntimeError):
    """Structured FFmpeg failure. Never a bare 'video processing failed'."""

    def __init__(self, message: str, *, stage: str,
                 scene: Optional[int] = None,
                 exit_code: int = -1,
                 stderr_tail: str = "",
                 cmd: Sequence[str] = (),
                 recovery_hint: str = "",
                 timed_out: bool = False):
        super().__init__(message)
        self.stage = stage
        self.scene = scene
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail
        self.cmd = list(cmd)
        self.recovery_hint = recovery_hint
        self.timed_out = timed_out

    def describe(self) -> str:
        lines = [f"Stage: {self.stage}"]
        if self.scene is not None:
            lines.append(f"Scene: {self.scene}")
        lines.append(f"FFmpeg exit code: {self.exit_code}")
        if self.stderr_tail:
            lines.append("FFmpeg said:")
            lines.append(self.stderr_tail)
        if self.recovery_hint:
            lines.append(f"Recovery: {self.recovery_hint}")
        return "\n".join(lines)


@dataclass
class FFmpegResult:
    ok: bool
    cmd: List[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_s: float = 0.0
    timed_out: bool = False
    cancelled: bool = False
    inputs: List[str] = field(default_factory=list)
    output: str = ""

    def stderr_tail(self, n: int = 25) -> str:
        return "\n".join((self.stderr or "").strip().splitlines()[-n:])


# ---------------------------------------------------------------- discovery

def find_ffmpeg(explicit: Optional[str] = None) -> str:
    """Locate a working ffmpeg executable.

    Priority: explicit path -> PATH -> common Windows spots -> bundled
    binary next to the frozen app.
    """
    candidates: List[str] = []
    if explicit:
        candidates.append(explicit)
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(found)
    if os.name == "nt":
        candidates += [
            r"C:\ffmpeg\bin\ffmpeg.exe",
            os.path.expandvars(r"%ProgramFiles%\ffmpeg\bin\ffmpeg.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\ffmpeg\bin\ffmpeg.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\ffmpeg\bin\ffmpeg.exe"),
        ]
    import sys
    if getattr(sys, "frozen", False):
        exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(str(Path(meipass) / exe))
            candidates.append(str(Path(meipass) / "ffmpeg" / exe))
        base = Path(sys.executable).parent
        candidates.append(str(base / exe))
        candidates.append(str(base / "ffmpeg" / exe))
    for c in candidates:
        if c and Path(c).is_file():
            return c
    raise FFmpegNotFoundError(
        "FFmpeg was not found. U One needs FFmpeg to render video. "
        "Please reinstall U One (FFmpeg is bundled with the installer) or "
        "place ffmpeg on your PATH.")


def find_ffprobe(ffmpeg_path: Optional[str] = None) -> Optional[str]:
    probe = shutil.which("ffprobe")
    if probe:
        return probe
    if ffmpeg_path:
        sibling = Path(ffmpeg_path).parent / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if sibling.is_file():
            return str(sibling)
    return None


# ---------------------------------------------------------------- escaping helpers

def escape_filter_value(text: str) -> str:
    """Escape free text for use inside a filter option value."""
    return "".join("\\" + ch if ch in "\\'[],;:" else ch for ch in text)


def escape_filter_path(path: str) -> str:
    """Escape a file path for the subtitles (or similar) filter argument.

    Forward slashes (ffmpeg understands them on Windows), escaped drive
    colon, quotes, commas and brackets. The value travels inside a single
    argv element (no shell), so only ffmpeg-level escaping is required.
    """
    p = os.path.normpath(path).replace("\\", "/")
    p = p.replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")
    return p.replace("[", "\\[").replace("]", "\\]")


def sanitize_ass_text(text: str) -> str:
    """Make user text safe for an ASS Dialogue line."""
    text = re.sub(r"\{[^}]*\}", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text)
    return text.replace("\n", "\\N")


def write_filter_script(filter_text: str, work_dir: str, name: str = "graph") -> str:
    path = Path(work_dir) / f"{name}.fffilter"
    path.write_text(filter_text, encoding="utf-8")
    return str(path)


def safe_temp_path(work_dir: str, prefix: str, suffix: str) -> str:
    fd, p = tempfile.mkstemp(prefix=prefix + "_", suffix=suffix, dir=work_dir)
    os.close(fd)
    return p


# ---------------------------------------------------------------- engine

_POLL_INTERVAL = 0.2
_TERMINATE_GRACE = 5.0


class FFmpegEngine:
    """Owns all FFmpeg execution: capture, cancel, hwaccel, structured errors."""

    def __init__(self, ffmpeg_path: Optional[str] = None,
                 cancel_event: Optional[threading.Event] = None,
                 log=None):
        self._explicit = ffmpeg_path
        self._ffmpeg: Optional[str] = None
        self._ffprobe: Optional[str] = None
        self.cancel_event = cancel_event or threading.Event()
        self.log = log  # optional callable(str)
        self._hw_cache: Dict[str, Optional[str]] = {}

    # -- discovery --
    @property
    def ffmpeg(self) -> str:
        if not self._ffmpeg:
            self._ffmpeg = find_ffmpeg(self._explicit)
        return self._ffmpeg

    @property
    def ffprobe(self) -> Optional[str]:
        if self._ffprobe is None:
            try:
                self._ffprobe = find_ffprobe(self.ffmpeg)
            except FFmpegNotFoundError:
                self._ffprobe = None
        return self._ffprobe

    def check(self) -> bool:
        try:
            self.ffmpeg  # noqa: B018
            return True
        except FFmpegNotFoundError:
            return False

    # -- hardware acceleration (opportunistic, never mandatory) --
    def detect_hw_encoder(self, codec: str = "h264") -> Optional[str]:
        """Return a WORKING ffmpeg hw encoder name, or None for CPU.

        Just checking `-encoders` is not enough: the bundled FFmpeg lists
        h264_nvenc/h264_qsv/h264_amf, but they fail at runtime if the
        matching GPU/drivers are absent. So we test-encode 10 frames
        with each candidate and only accept one that produces output.
        """
        key = codec
        if key in self._hw_cache:
            return self._hw_cache[key]
        enc: Optional[str] = None
        try:
            r = run_no_window([self.ffmpeg, "-hide_banner", "-encoders"],
                              capture_output=True, text=True, timeout=30)
            out = r.stdout or ""
            prefs = {"h264": ["h264_nvenc", "h264_qsv", "h264_amf"],
                     "hevc": ["hevc_nvenc", "hevc_qsv", "hevc_amf"]}[codec]
            for name in prefs:
                if not re.search(rf"^\s*\S+\s+{re.escape(name)}\b", out, re.M):
                    continue
                if self._test_hw_encoder(name):
                    enc = name
                    break
        except Exception:
            enc = None
        self._hw_cache[key] = enc
        return enc

    def _test_hw_encoder(self, enc_name: str) -> bool:
        """Encode 10 tiny frames; True only if the encoder really works."""
        try:
            # 320x180 testsrc, 10 frames, to null output.
            cmd = [self.ffmpeg, "-hide_banner", "-nostats", "-loglevel",
                   "error", "-y",
                   "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=0.34",
                   "-frames:v", "10", "-c:v", enc_name,
                   "-f", "null", "-"]
            r = run_no_window(cmd, capture_output=True, text=True,
                              timeout=30)
            return r.returncode == 0
        except Exception:
            return False

    # -- execution --
    def run(self, args: Sequence[str], *, stage: str,
            scene: Optional[int] = None,
            timeout: int = 1800,
            inputs: Sequence[str] = (),
            output: str = "",
            cwd: Optional[str] = None,
            log_stderr: bool = True,
            loglevel: str = "error",
            show_stats: bool = False) -> FFmpegResult:
        """Run ffmpeg. Returns FFmpegResult; raises FFmpegError/RenderCancelled.

        Cancel: when cancel_event is set, the child is terminated (then
        killed after a grace period) and RenderCancelled is raised.
        loglevel: ffmpeg log level; use "info" when filter detection
        output (blackdetect/freezedetect) must be visible.
        show_stats: keep ffmpeg's progress stats (needed to count frames).
        """
        stats_args = [] if show_stats else ["-nostats"]
        cmd = [self.ffmpeg, "-hide_banner", *stats_args, "-loglevel", loglevel,
               "-y", *[str(a) for a in args]]
        started = time.time()
        self._emit(f"[{stage}]$ {' '.join(cmd)}")
        proc = popen_no_window(cmd, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, cwd=cwd,
                               encoding="utf-8", errors="replace")
        stdout, stderr = "", ""
        timed_out = False
        cancelled = False
        try:
            while True:
                try:
                    stdout, stderr = proc.communicate(timeout=_POLL_INTERVAL)
                    break
                except subprocess.TimeoutExpired:
                    if self.cancel_event.is_set():
                        cancelled = True
                        self._terminate(proc)
                        stdout, stderr = proc.communicate()
                        break
                    if time.time() - started > timeout:
                        timed_out = True
                        self._terminate(proc)
                        stdout, stderr = proc.communicate()
                        break
        except RenderCancelled:
            raise
        duration = time.time() - started
        result = FFmpegResult(
            ok=proc.returncode == 0 and not cancelled and not timed_out,
            cmd=cmd, stdout=stdout or "", stderr=stderr or "",
            exit_code=proc.returncode if proc.returncode is not None else -1,
            duration_s=duration, timed_out=timed_out, cancelled=cancelled,
            inputs=list(inputs), output=output)
        if log_stderr and result.stderr.strip():
            # Full stderr goes to the render log; never hidden.
            self._emit(f"[{stage}] stderr ({len(result.stderr)} chars):\n"
                       f"{result.stderr.strip()[:4000]}")
        if cancelled:
            raise RenderCancelled(f"Cancelled during {stage}")
        if timed_out:
            raise FFmpegError(
                f"FFmpeg timed out during {stage} after {timeout}s.",
                stage=stage, scene=scene, exit_code=-1,
                stderr_tail=result.stderr_tail(), cmd=cmd, timed_out=True,
                recovery_hint="retry with a longer timeout or fewer parallel jobs")
        if proc.returncode != 0:
            tail = result.stderr_tail()
            scene_txt = f" (scene {scene})" if scene is not None else ""
            raise FFmpegError(
                f"Video processing failed during {stage}{scene_txt} "
                f"(error code {proc.returncode}).",
                stage=stage, scene=scene, exit_code=proc.returncode,
                stderr_tail=tail, cmd=cmd,
                recovery_hint=self._hint_for(stage, tail))
        return result

    @staticmethod
    def _terminate(proc: "subprocess.Popen") -> None:
        try:
            proc.terminate()
            proc.wait(timeout=_TERMINATE_GRACE)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    @staticmethod
    def _hint_for(stage: str, stderr_tail: str) -> str:
        low = (stderr_tail or "").lower()
        if "invalid data found when processing input" in low:
            return "input file is corrupt; regenerate the scene"
        if "no such file or directory" in low:
            return "input file missing; check the project folder"
        if "matches no streams" in low:
            return "expected audio/video stream not found; re-probe inputs"
        if "disk quota exceeded" in low or "no space left" in low:
            return "disk full; free space and retry"
        if "permission denied" in low:
            return "permission error; check folder access"
        return "see FFmpeg output above"

    def _emit(self, msg: str) -> None:
        if self.log:
            try:
                self.log(msg)
            except Exception:
                pass


# ---------------------------------------------------------------- module-level convenience (compat)

_default_engine: Optional[FFmpegEngine] = None


def get_engine(cancel_event: Optional[threading.Event] = None) -> FFmpegEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = FFmpegEngine(cancel_event=cancel_event)
    elif cancel_event is not None:
        _default_engine.cancel_event = cancel_event
    return _default_engine
