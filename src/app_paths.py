"""U One - platform-aware application directories.

Writable data always lives in proper per-user OS directories, never in the
application install folder or the current working directory:

  Windows: %LOCALAPPDATA%\\U One\\...
  macOS:   ~/Library/Application Support/U One/...
  Linux:   ~/.local/share/u-one/...

The final rendered MP4 defaults to the user's Videos folder.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "U One"
APP_DIR_NAME = "U One" if os.name == "nt" else "u-one"


def _base() -> Path:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(root) / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(root) / APP_DIR_NAME


BASE = _base()


def app_dir(*parts: str) -> Path:
    p = BASE.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def work_root() -> Path:
    return app_dir("work")


def cache_dir() -> Path:
    return app_dir("cache", "media")


def log_dir() -> Path:
    return app_dir("logs")


def projects_dir() -> Path:
    return app_dir("projects")


def default_output_dir() -> Path:
    if os.name == "nt":
        vids = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Videos"
    else:
        vids = Path.home() / "Videos"
    vids.mkdir(parents=True, exist_ok=True)
    return vids


def settings_path() -> Path:
    return app_dir() / "settings.json"


def key_file_path() -> Path:
    """Fallback key file when no OS credential store is available."""
    return app_dir() / "keys.dat"
