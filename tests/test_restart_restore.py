"""Regression tests for Uzair's 2026-10-02 17:35 PKT log:

1. Autosave snowball: the recovered autosave path was
   `Untitled.uvp.json.autosave.autosave.autosave...` (20x). Recovery
   loads the autosave file itself, and the autosave thread appended
   ".autosave" to that path every cycle.

2. Empty preview after restart: "Caption editor ready" was restored at
   startup, but no video was loaded into the player, so Play hit
   NoMedia and the watchdog gave up ("Preview play didn't start").

Fixes: Project._autosave_target() strips stacked suffixes; recovery
resets project.path; _use_edit_session() records _current_video from
the session; startup loads the preview video (no autoplay).
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SRC_UI = ROOT / "src" / "ui" / "main_window.py"

from src.project import Project  # noqa: E402


def test_autosave_target_strips_stacked_suffixes():
    assert Project._autosave_target("/p/Untitled.uvp.json") == \
        "/p/Untitled.uvp.json.autosave"
    assert Project._autosave_target("/p/Untitled.uvp.json.autosave") == \
        "/p/Untitled.uvp.json.autosave"
    assert Project._autosave_target(
        "/p/Untitled.uvp.json" + ".autosave" * 20) == \
        "/p/Untitled.uvp.json.autosave"


def _main_window_source():
    return SRC_UI.read_text(encoding="utf-8")


def test_use_edit_session_records_current_video():
    src = _main_window_source()
    assert 'data.get("output_video")' in src, \
        "_use_edit_session must read the session's output_video"
    assert "self._current_video = _out_vid" in src


def test_preview_play_file_supports_no_autoplay():
    tree = ast.parse(_main_window_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for item in node.body:
                if (isinstance(item, ast.FunctionDef)
                        and item.name == "_preview_play_file"):
                    args = [a.arg for a in item.args.args]
                    assert "autoplay" in args, \
                        "_preview_play_file must accept autoplay kwarg"
                    return
    raise AssertionError("MainWindow._preview_play_file not found")


def test_startup_restores_preview_video():
    src = _main_window_source()
    # Startup block: after _use_edit_session succeeds, load the video.
    assert "_preview_play_file(_pv, autoplay=False)" in src, \
        "startup must reload the preview video without autoplay"


def test_recovery_resets_autosave_path():
    src = _main_window_source()
    assert 'while _pp.endswith(".autosave")' in src, \
        "recovery must strip .autosave from project.path"
