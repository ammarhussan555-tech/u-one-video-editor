"""U One - temporary file lifecycle manager (v2).

All render temporaries are registered here. On normal completion the
manager cleans them; on cancel or error it still cleans; nothing is left
behind in the work dir except files the caller explicitly keeps
(segments, final output, logs).

Usage:
    with TempManager(work_dir) as tmp:
        p = tmp.temp_path("concat", ".mp4")
        ... render ...
        tmp.keep(final_path)   # excluded from cleanup
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Set


class TempManager:
    def __init__(self, base_dir: str, subdir: str = "_tmp"):
        self.base_dir = Path(base_dir)
        self.root = self.base_dir / subdir
        self.root.mkdir(parents=True, exist_ok=True)
        self._tracked: List[Path] = []
        self._keep: Set[str] = set()
        self._lock = threading.Lock()
        self._counter = 0

    def temp_path(self, prefix: str, suffix: str) -> str:
        """Reserve and track a temp path."""
        with self._lock:
            self._counter += 1
            p = self.root / f"{prefix}_{self._counter:04d}{suffix}"
        self._tracked.append(p)
        return str(p)

    def track(self, path: str) -> str:
        """Track an existing path for cleanup."""
        self._tracked.append(Path(path))
        return path

    def keep(self, path: str) -> str:
        """Exclude a path from cleanup (final outputs)."""
        self._keep.add(str(Path(path).resolve()))
        return path

    def cleanup(self) -> None:
        """Remove all tracked temp files/dirs (best effort)."""
        with self._lock:
            tracked, self._tracked = self._tracked, []
        for p in tracked:
            try:
                rp = str(p.resolve())
            except OSError:
                rp = str(p)
            if rp in self._keep:
                continue
            try:
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p, ignore_errors=True)
                elif p.is_file() or p.is_symlink():
                    p.unlink(missing_ok=True)
            except OSError:
                pass
        # remove the tmp root if empty
        try:
            self.root.rmdir()
        except OSError:
            pass

    # -- context manager --
    def __enter__(self) -> "TempManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()

    # -- crash-safety: purge stale tmp dirs from previous runs --
    @staticmethod
    def purge_stale(base_dir: str, subdir: str = "_tmp",
                    max_age_hours: float = 2.0) -> None:
        """Remove per-run temp dirs older than max_age_hours.

        Each render run gets its own unique subdir (see RenderEngine), so
        this only cleans up leftovers from crashed/killed runs — never the
        temp files of a render that is still running.
        """
        root = Path(base_dir) / subdir
        if not root.is_dir():
            return
        cutoff = time.time() - max_age_hours * 3600
        for child in root.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                pass


def ensure_free_space(path: str, needed_mb: float) -> None:
    """Raise UOneError-friendly OSError if disk space is insufficient."""
    try:
        free_mb = shutil.disk_usage(path).free / (1024 * 1024)
    except OSError:
        return
    if free_mb < needed_mb:
        raise OSError(
            f"Not enough free disk space: {free_mb:.0f} MB available, "
            f"need ~{needed_mb:.0f} MB. Free some space and retry.")
