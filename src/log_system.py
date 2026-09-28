"""U One - per-render log system (v2).

Each render gets its own timestamped log file:

    %LOCALAPPDATA%\\U One\\logs\\render_2026-09-27_18-30-22.log

containing: project info, per-stage status, per-scene status, every
FFmpeg command with its FULL stderr, retries, and the final result.
Important lines are also mirrored to the main app log (which redacts
API keys). Nothing about a failure is ever only in memory.
"""
from __future__ import annotations

import datetime
import logging
import threading
from pathlib import Path
from typing import Optional

from . import app_log
from .app_paths import log_dir


def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


class RenderLogger:
    """Collects everything about one render into a dedicated log file."""

    def __init__(self, project_id: str, title: str = ""):
        self.project_id = project_id
        self.title = title
        self.path = Path(log_dir()) / f"render_{_stamp()}.log"
        self._lock = threading.Lock()
        self._fh = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.path, "w", encoding="utf-8")
        except OSError:
            self._fh = None
        self.write(f"=== U One render log | project={project_id} "
                   f"| title={title!r} | started={_stamp()} ===")
        app_log.log(project_id, "render", f"Render log: {self.path}")

    # -- low-level --
    def write(self, line: str) -> None:
        line = app_log.redact(line)
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        with self._lock:
            if self._fh:
                try:
                    self._fh.write(f"[{ts}] {line}\n")
                    self._fh.flush()
                except OSError:
                    pass

    def __call__(self, line: str) -> None:
        """Lets the logger act as the FFmpegEngine log callable."""
        self.write(line)

    # -- structured entries --
    def stage(self, num: int, name: str, msg: str = "") -> None:
        line = f"STAGE {num}: {name}" + (f" - {msg}" if msg else "")
        self.write(line)
        app_log.log(self.project_id, name, msg or name)

    def scene(self, idx: int, msg: str,
              level: int = logging.INFO) -> None:
        self.write(f"[scene {idx}] {msg}")
        app_log.log(self.project_id, "scene", msg, scene=idx, level=level)

    def ffmpeg_result(self, result) -> None:
        """Persist a full FFmpegResult: cmd + stdout + stderr + timing."""
        self.write(f"$ {' '.join(result.cmd)}")
        self.write(f"  exit={result.exit_code} "
                   f"duration={result.duration_s:.1f}s "
                   f"cancelled={result.cancelled} timed_out={result.timed_out}")
        if result.inputs:
            self.write("  inputs: " + "; ".join(result.inputs))
        if result.output:
            self.write(f"  output: {result.output}")
        if (result.stderr or "").strip():
            self.write("  --- stderr begin ---")
            for ln in result.stderr.strip().splitlines():
                self.write("  " + ln)
            self.write("  --- stderr end ---")

    def ffmpeg_error(self, err) -> None:
        self.write("FFMPEG ERROR:")
        for ln in err.describe().splitlines():
            self.write("  " + ln)
        app_log.log(self.project_id, getattr(err, "stage", "ffmpeg"),
                    f"{err}", scene=getattr(err, "scene", None),
                    level=logging.ERROR,
                    exit_code=getattr(err, "exit_code", None))

    def retry(self, what: str, attempt: int, max_attempts: int,
              reason: str = "") -> None:
        self.write(f"RETRY {what}: attempt {attempt}/{max_attempts} "
                   f"{('(' + reason + ')') if reason else ''}")

    def warning(self, msg: str) -> None:
        self.write(f"WARNING: {msg}")
        app_log.log(self.project_id, "qc", msg, level=logging.WARNING)

    def finish(self, ok: bool, detail: str = "") -> None:
        self.write(f"=== render {'SUCCEEDED' if ok else 'FAILED'} "
                   f"{detail} | ended={_stamp()} ===")
        app_log.log(self.project_id, "render",
                    f"Render {'succeeded' if ok else 'failed'}: {detail}",
                    level=logging.INFO if ok else logging.ERROR)
        with self._lock:
            if self._fh:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None
