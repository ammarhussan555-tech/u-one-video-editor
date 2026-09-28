"""U One - project manager (v2).

Owns the project lifecycle:
  * save / load / autosave (delegated to the proven Project class)
  * failure snapshots: on any render failure, persist the project plus
    scene metadata, asset paths, the render log path, FFmpeg commands
    used, and the structured failure info -> last_error.uvp.json
  * render-state persistence: completed stages + produced artifacts, so
    "Retry render" resumes instead of regenerating everything.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .project import Project

_STATE_FILE = "render_state.json"
_FAILURE_FILE = "last_error.uvp.json"


class ProjectManager:
    def __init__(self, project: Optional[Project] = None,
                 projects_dir: str = ""):
        self.project = project or Project()
        self.projects_dir = projects_dir
        if projects_dir:
            os.makedirs(projects_dir, exist_ok=True)

    # -- basic persistence --
    def save(self, path: Optional[str] = None) -> str:
        self.project.save(path)
        return self.project.path or ""

    def save_copy(self, path: str) -> str:
        data = self.project.to_dict()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        return path

    @staticmethod
    def load(path: str) -> "ProjectManager":
        return ProjectManager(Project.load(path),
                              os.path.dirname(path))

    def start_autosave(self, interval: int = 60) -> None:
        self.project.start_autosave(interval=interval,
                                    directory=self.projects_dir)

    def stop_autosave(self) -> None:
        self.project.stop_autosave()

    # -- render state (resume support) --
    @staticmethod
    def state_path(work_dir: str) -> str:
        return os.path.join(work_dir, _STATE_FILE)

    @staticmethod
    def load_render_state(work_dir: str) -> Dict[str, Any]:
        p = ProjectManager.state_path(work_dir)
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def save_render_state(work_dir: str, state: Dict[str, Any]) -> None:
        try:
            with open(ProjectManager.state_path(work_dir), "w",
                      encoding="utf-8") as f:
                json.dump(state, f, indent=1)
        except Exception:
            pass

    @staticmethod
    def clear_render_state(work_dir: str) -> None:
        try:
            os.remove(ProjectManager.state_path(work_dir))
        except OSError:
            pass

    # -- failure snapshot (§17) --
    def save_failure_snapshot(self, work_dir: str, error,
                              render_log_path: str = "",
                              ffmpeg_commands: list = None,
                              scene_states: list = None) -> str:
        """Persist everything needed to diagnose and resume.

        Returns the snapshot path (last_error.uvp.json in work_dir).
        Never raises: a failing snapshot must not mask the real error.
        """
        try:
            err = error.describe() if hasattr(error, "describe") else str(error)
            snapshot = {
                "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "error": {
                    "type": type(error).__name__,
                    "stage": getattr(error, "stage", ""),
                    "scene": getattr(error, "scene", None),
                    "exit_code": getattr(error, "exit_code", None),
                    "reason": getattr(error, "reason", str(error)),
                    "recovery_hint": getattr(error, "recovery_hint", ""),
                    "detail": err,
                },
                "project": self.project.to_dict(),
                "scene_states": scene_states or [],
                "render_log": render_log_path,
                "ffmpeg_commands": ffmpeg_commands or [],
            }
            path = os.path.join(work_dir, _FAILURE_FILE)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=1)
            # also keep a copy next to the project file for the GUI's
            # "recover last failed render" entry point
            if self.projects_dir:
                try:
                    shutil.copy2(path, os.path.join(self.projects_dir,
                                                    _FAILURE_FILE))
                except OSError:
                    pass
            return path
        except Exception:
            return ""
