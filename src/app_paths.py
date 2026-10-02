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


def fonts_dir() -> Path | None:
    """Directory with U One's bundled caption fonts (OFL), or None.

    Resolves to the PyInstaller bundle (sys._MEIPASS/assets/fonts) when
    frozen, else to <project>/assets/fonts in a dev checkout.
    """
    import sys
    cands = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(Path(meipass) / "assets" / "fonts")
        cands.append(Path(sys.executable).parent / "assets" / "fonts")
    # dev checkout: <project>/assets/fonts (this file is <project>/src/app_paths.py)
    cands.append(Path(__file__).resolve().parent.parent / "assets" / "fonts")
    for c in cands:
        if c.is_dir() and any(c.glob("*.ttf")):
            return c
    return None


def sfx_dir() -> Path | None:
    """Directory with U One's bundled real SFX (PD/CC0 WAVs), or None.

    Resolves to the PyInstaller bundle (sys._MEIPASS/assets/sfx) when
    frozen, else to <project>/assets/sfx in a dev checkout. See
    assets/sfx/SOURCES.md for per-file source + license.
    """
    import sys
    cands = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(Path(meipass) / "assets" / "sfx")
        cands.append(Path(sys.executable).parent / "assets" / "sfx")
    # dev checkout: <project>/assets/sfx (this file is <project>/src/app_paths.py)
    cands.append(Path(__file__).resolve().parent.parent / "assets" / "sfx")
    for c in cands:
        if c.is_dir() and any(c.glob("*.wav")):
            return c
    return None


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
