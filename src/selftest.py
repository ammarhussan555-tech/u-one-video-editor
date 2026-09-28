"""U One built-in end-to-end self test.

Run on the target machine (e.g. installed Windows build):

    U One.exe --selftest

It exercises the whole product path with no network and no API keys:
script -> scene detection -> media search (generated fallback) ->
timeline -> captions/overlays -> audio mix -> final MP4, in 16:9 and 9:16,
using a work directory that contains spaces. The MP4s are validated with
ffprobe (bundled). A report is written to the per-user U One data dir and
also printed to stdout. Exit code 0 = all checks passed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import traceback
import wave

RESULTS: list[tuple[str, bool, str]] = []


def _rec(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


def _make_voice_wav(path: str, seconds: float) -> str:
    import numpy as np
    sr = 22050
    t = np.arange(int(sr * seconds)) / sr
    tone = np.sin(2 * np.pi * (110 + 40 * np.sin(2 * np.pi * 0.7 * t)) * t)
    am = 0.5 + 0.5 * np.sin(2 * np.pi * 3.0 * t) ** 2
    pcm = (tone * am * 20000).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def _ffprobe_streams(path: str) -> str:
    from src.ffmpeg_util import find_ffmpeg, find_ffprobe
    probe = find_ffprobe(find_ffmpeg())
    r = subprocess.run(
        [probe, "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1", path],
        capture_output=True, text=True, timeout=60)
    return r.stdout


def _valid_mp4(path: str, w: int, h: int) -> tuple[bool, str]:
    if not os.path.isfile(path):
        return False, "file missing"
    if os.path.getsize(path) < 1024:
        return False, "file empty"
    out = _ffprobe_streams(path)
    if "codec_type=video" not in out:
        return False, "no video stream"
    if "codec_type=audio" not in out:
        return False, "no audio stream"
    if f"width={w}" not in out:
        return False, "width mismatch"
    if f"height={h}" not in out:
        return False, "height mismatch"
    return True, "ok"


def _run_one(script: str, wav: str, workdir: str, fmt: str, w: int, h: int,
             tag: str) -> None:
    from src.project import Project
    from src.project_manager import ProjectManager
    from src.render_engine import RenderEngine
    from src.settings import Settings
    from src.error_recovery import UOneError
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)
    proj = Project(name=f"selftest-{tag}")
    proj.script = script
    settings = Settings({
        "visual_mode": "every_sentence", "media_preference": "ai_auto",
        "repetition_limit": "never", "text_overlays": "auto",
        "captions_enabled": True, "caption_highlight": True,
        "caption_font_size": 48, "voice_name": "en-US-AriaNeural",
        "output_format": fmt, "output_resolution": "1080p",
        "output_fps": 30, "output_codec": "h264", "music_dir": "",
        "api_keys": {}, "voice_upload": wav,
    })
    t0 = time.time()
    try:
        pm = ProjectManager(proj, os.path.dirname(workdir))
        out = RenderEngine(pm, workdir, settings,
                           progress_cb=lambda *a: None,
                           log_cb=lambda m: None).run(preview=False)
    except UOneError as e:
        _rec(f"selftest {tag}: render completes", False,
             f"{type(e).__name__}: {str(e)[:160]}")
        traceback.print_exc()
        return
    except Exception as e:  # noqa: BLE001
        _rec(f"selftest {tag}: render completes", False,
             f"{type(e).__name__}: {str(e)[:160]}")
        traceback.print_exc()
        return
    dt = time.time() - t0
    ok, why = _valid_mp4(out, w, h)
    _rec(f"selftest {tag}: MP4 valid ({fmt})", ok, f"{why} in {dt:.0f}s -> {out}")


def _data_dir() -> str:
    from src import app_paths
    return str(app_paths.app_dir())


def run_selftest() -> bool:
    t_start = time.time()
    try:
        from src.renderer import check_ffmpeg
        _rec("selftest: bundled FFmpeg available", check_ffmpeg())
    except Exception as e:  # noqa: BLE001
        _rec("selftest: bundled FFmpeg available", False, str(e)[:120])

    base = os.path.join(_data_dir(), "selftest")
    # hostile work path: spaces + parentheses (Windows-style torture path)
    work = os.path.join(base, "work dir (1)")
    os.makedirs(work, exist_ok=True)
    wav = _make_voice_wav(os.path.join(work, "my voice.wav"), 22.0)
    _rec("selftest: synthetic voiceover created", os.path.isfile(wav))

    script = ("Mount Etna erupted again last night, sending ash ten "
              "kilometers into the sky. Scientists (\"on site\") say: the "
              "crater's pressure is rising, and nearby villages - including "
              "Zafferana - were told to stay indoors. Caf\u00e9 owners watched "
              "the plume for hours; it's the biggest event in years, they said.")
    _run_one(script, wav, os.path.join(work, "w169"), "16:9", 1920, 1080, "16:9")
    _run_one(script, wav, os.path.join(work, "w916"), "9:16", 1080, 1920, "9:16")

    # escaping layer (same code path the subtitles filter uses)
    from src.ffmpeg_util import escape_filter_path
    esc = escape_filter_path(
        "C:/Users/Gillani Computers/Downloads/my vid (1)/cap's,.ass")
    _rec("selftest: hostile path escaping",
         "\\'" in esc and "\\," in esc, esc[:80])

    failed = [n for n, ok, _ in RESULTS if not ok]
    total = time.time() - t_start
    lines = ["# U One self-test report", "",
             f"Date: {time.strftime('%Y-%m-%d %H:%M')}",
             f"Duration: {total:.0f}s", "",
             "| Check | Result | Detail |", "|---|---|---|"]
    for name, ok, detail in RESULTS:
        lines.append(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")
    lines += ["", f"**{len(RESULTS) - len(failed)}/{len(RESULTS)} passed.**"]
    if failed:
        lines.append("Failed: " + ", ".join(failed))
    report = "\n".join(lines) + "\n"
    report_path = os.path.join(base, "report.txt")
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
    except OSError:
        report_path = os.path.join(work, "report.txt")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
    print(f"\nReport written to: {report_path}", flush=True)
    print(f"SELFTEST {'PASSED' if not failed else 'FAILED'} "
          f"({len(RESULTS) - len(failed)}/{len(RESULTS)})", flush=True)
    return not failed
