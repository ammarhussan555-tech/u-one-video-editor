"""Shared fixtures for U One v2 tests.

Builds small synthetic media files once per session:
  valid.mp4    - 2s color bars, 640x360, 30fps, yuv420p, h264
  black.mp4    - 2s pure black (must be REJECTED by the validator)
  empty.mp4    - 0 bytes
  corrupt.mp4  - random bytes with .mp4 extension
  oneframe.mp4 - single frame (~0.03s, too short for a scene)
  tone.wav     - 2s sine tone (valid audio)
"""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ffmpeg_engine import FFmpegEngine  # noqa: E402


def _ff(*args):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        *args], capture_output=True, timeout=120)
    assert r.returncode == 0, r.stderr.decode(errors="replace")


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    d = str(d)
    _ff("-f", "lavfi", "-i", "testsrc=s=640x360:r=30:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        os.path.join(d, "valid.mp4"))
    _ff("-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        os.path.join(d, "black.mp4"))
    open(os.path.join(d, "empty.mp4"), "wb").close()
    with open(os.path.join(d, "corrupt.mp4"), "wb") as f:
        f.write(os.urandom(500))
    _ff("-f", "lavfi", "-i", "testsrc=s=640x360:r=30:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-frames:v", "1",
        os.path.join(d, "oneframe.mp4"))
    _ff("-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:a", "pcm_s16le", os.path.join(d, "tone.wav"))
    # a valid still image for image-asset tests
    _ff("-f", "lavfi", "-i", "testsrc=s=640x360:r=1:d=1",
        "-frames:v", "1", os.path.join(d, "still.png"))
    return d


@pytest.fixture(scope="session")
def engine():
    eng = FFmpegEngine()
    assert eng.check(), "ffmpeg not found"
    return eng


@pytest.fixture()
def workdir(tmp_path):
    d = str(tmp_path / "work")
    os.makedirs(d, exist_ok=True)
    return d
