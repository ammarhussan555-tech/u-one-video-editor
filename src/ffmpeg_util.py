"""U One - safe FFmpeg execution layer.

Root-cause fix for Windows rendering failures ("ffmpeg failed", "Error opening
output file ... Invalid argument").

Problems fixed here:
  - No more unsafe command *strings*. Every invocation uses a proper argv list
    executed without a shell, so spaces, quotes, apostrophes, parentheses and
    Unicode in paths are handled by the OS, not by string parsing.
  - FFmpeg *filter-level* escaping is applied wherever a file path or free
    text is embedded inside a filter description (subtitles filename,
    filter_complex_script contents, etc.).
  - Complex filter graphs are written to generated filter-script files
    (-filter_complex_script) instead of being crammed onto the command line.
  - Text overlays are rendered through libass (ASS subtitles with a top-
    positioned style) instead of drawtext, so long text, Unicode, commas,
    colons, quotes and apostrophes can never break a filter graph.
  - Subtitle file paths are escaped per the subtitles-filter rules and the
    burn-in step retries with a copied temp file when the path is hostile.
  - All failures return structured errors (exit code, failing stage, scene)
    instead of dumping a giant unreadable command at the user.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence


def _no_window_kwargs() -> dict:
    """Subprocess kwargs that suppress the flashing console window on Windows.

    Root cause of "blank windows keep opening": every FFmpeg/probe invocation
    via Popen/run spawns a visible console window on Windows unless
    CREATE_NO_WINDOW is passed. During a render dozens of FFmpeg processes
    are spawned, so the user sees black windows flashing repeatedly.
    """
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def popen_no_window(*args, **kwargs):
    """subprocess.Popen that never shows a console window on Windows."""
    kwargs.update(_no_window_kwargs())
    return subprocess.Popen(*args, **kwargs)


def run_no_window(*args, **kwargs):
    """subprocess.run that never shows a console window on Windows."""
    kwargs.update(_no_window_kwargs())
    return subprocess.run(*args, **kwargs)


class FFmpegNotFoundError(RuntimeError):
    pass


class FFmpegRunError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int, stage: str,
                 stderr_tail: str = "", scene: Optional[int] = None):
        super().__init__(message)
        self.exit_code = exit_code
        self.stage = stage
        self.stderr_tail = stderr_tail
        self.scene = scene


@dataclass
class RunResult:
    ok: bool
    exit_code: int
    stderr: str
    stdout: str


def find_ffmpeg(explicit: Optional[str] = None) -> str:
    """Locate a working ffmpeg executable.

    Priority: explicit path -> PATH lookup -> common Windows install spots ->
    bundled binary next to the frozen app.
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
    # bundled next to a PyInstaller-frozen app (one-folder) or inside the
    # one-file bundle extraction dir (sys._MEIPASS)
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
        "place ffmpeg on your PATH."
    )


def find_ffprobe(ffmpeg_path: str) -> Optional[str]:
    probe = shutil.which("ffprobe")
    if probe:
        return probe
    sibling = Path(ffmpeg_path).parent / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
    return str(sibling) if sibling.is_file() else None


def escape_filter_value(text: str) -> str:
    """Escape free text for use inside a filter option value."""
    out = []
    for ch in text:
        if ch in "\\'[],;:":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def escape_filter_path(path: str) -> str:
    """Escape a file path for the subtitles (or similar) filter argument.

    Handles Windows drive letters, backslashes, spaces, colons, commas,
    quotes and Unicode. The value is embedded in a filter description that
    is passed as a single argv element (no shell), so only ffmpeg-level
    escaping is required.
    """
    p = os.path.normpath(path)
    # Normalize to forward slashes first; ffmpeg understands them on Windows.
    p = p.replace("\\", "/")
    # Escape the drive-letter colon and any other special chars.
    p = p.replace(":", "\\:")
    p = p.replace("'", "\\'")
    p = p.replace(",", "\\,")
    p = p.replace("[", "\\[").replace("]", "\\]")
    return p


def sanitize_ass_text(text: str) -> str:
    """Make user text safe for an ASS Dialogue line."""
    # Strip ASS override blocks the user may have typed.
    import re
    text = re.sub(r"\{[^}]*\}", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Normalize unicode so combining marks don't break shaping.
    text = unicodedata.normalize("NFC", text)
    return text.replace("\n", "\\N")


def run_ffmpeg(args: Sequence[str], *, stage: str, timeout: int = 1800,
               scene: Optional[int] = None, cwd: Optional[str] = None) -> RunResult:
    """Run ffmpeg with a proper argv list (no shell).

    Raises FFmpegRunError with a structured, user-safe message on failure.
    """
    ffmpeg = find_ffmpeg()
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error",
           "-y", *list(args)]
    try:
        proc = run_no_window(cmd, capture_output=True, text=True,
                              timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired as e:
        raise FFmpegRunError(
            f"FFmpeg timed out during {stage}.",
            exit_code=-1, stage=stage, scene=scene,
            stderr_tail="timeout") from e
    except OSError as e:
        raise FFmpegRunError(
            f"Could not start FFmpeg during {stage}: {e}",
            exit_code=-1, stage=stage, scene=scene) from e
    stderr = proc.stderr or ""
    if proc.returncode != 0:
        tail = "\n".join(stderr.strip().splitlines()[-15:])
        scene_txt = f" (scene {scene})" if scene is not None else ""
        raise FFmpegRunError(
            f"Video processing failed during {stage}{scene_txt} "
            f"(error code {proc.returncode}). U One will try a fallback.",
            exit_code=proc.returncode, stage=stage,
            stderr_tail=tail, scene=scene)
    return RunResult(ok=True, exit_code=0, stderr=stderr, stdout=proc.stdout or "")


def write_filter_script(filter_text: str, work_dir: str, name: str = "graph") -> str:
    """Write a complex filter graph to a file for -filter_complex_script."""
    path = Path(work_dir) / f"{name}.fffilter"
    path.write_text(filter_text, encoding="utf-8")
    return str(path)


def ffprobe_duration(path: str, ffmpeg_path: Optional[str] = None) -> float:
    """Return media duration in seconds via ffprobe; 0.0 on failure."""
    probe = find_ffprobe(ffmpeg_path or find_ffmpeg())
    if not probe:
        return 0.0
    try:
        r = run_no_window(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30)
        return float((r.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def media_has_streams(path: str, want_video: bool = True,
                      want_audio: bool = False,
                      ffmpeg_path: Optional[str] = None) -> bool:
    probe = find_ffprobe(ffmpeg_path or find_ffmpeg())
    if not probe:
        return Path(path).is_file()
    try:
        r = run_no_window(
            [probe, "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30)
        types = (r.stdout or "").split()
        ok = (not want_video or "video" in types) and \
             (not want_audio or "audio" in types)
        return ok and r.returncode == 0
    except Exception:
        return False


def safe_temp_path(work_dir: str, prefix: str, suffix: str) -> str:
    """Create a reserved temp path with a filesystem-safe name."""
    fd, p = tempfile.mkstemp(prefix=prefix + "_", suffix=suffix, dir=work_dir)
    os.close(fd)
    return p
