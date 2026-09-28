"""FFmpeg engine tests: success, structured failure with stderr,
missing input, cancellation actually killing the child process."""
import threading
import time

import pytest

from src.error_recovery import RenderCancelled
from src.ffmpeg_engine import FFmpegEngine, FFmpegError


def test_successful_run_captures_everything(engine, tmp_path):
    out = str(tmp_path / "o.mp4")
    res = engine.run(
        ["-f", "lavfi", "-i", "testsrc=s=320x180:r=15:d=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", out],
        stage="test_convert", inputs=["lavfi:testsrc"], output=out,
        timeout=60)
    assert res.ok and res.exit_code == 0
    assert res.duration_s > 0
    assert res.output == out
    assert "ffmpeg" in res.cmd[0]
    assert "-i" in res.cmd


def test_failed_run_raises_structured_error(engine, tmp_path):
    with pytest.raises(FFmpegError) as ei:
        engine.run(["-i", str(tmp_path / "missing.mp4"),
                    str(tmp_path / "o.mp4")],
                   stage="test_missing_input", scene=7, timeout=60)
    e = ei.value
    assert e.stage == "test_missing_input"
    assert e.scene == 7
    assert e.exit_code != 0
    assert e.stderr_tail, "stderr must never be hidden"
    assert "no such file" in e.stderr_tail.lower()
    desc = e.describe()
    assert "Stage: test_missing_input" in desc
    assert "Scene: 7" in desc


def test_invalid_filter_error_is_diagnosed(engine, tmp_path):
    with pytest.raises(FFmpegError) as ei:
        engine.run(["-f", "lavfi", "-i", "testsrc=s=64x64:d=1",
                    "-vf", "nosuchfilter_xyz", "-f", "null", "-"],
                   stage="test_bad_filter", timeout=60)
    assert "no such filter" in ei.value.stderr_tail.lower()


def test_cancel_terminates_child_process(tmp_path):
    cancel = threading.Event()
    eng = FFmpegEngine(cancel_event=cancel)

    def canceller():
        time.sleep(1.0)
        cancel.set()

    t = threading.Thread(target=canceller, daemon=True)
    t.start()
    started = time.time()
    with pytest.raises(RenderCancelled):
        eng.run(["-f", "lavfi", "-i", "testsrc=s=640x360:r=30:d=60",
                 "-c:v", "libx264", "-f", "null", "-"],
                stage="test_cancel", timeout=120)
    elapsed = time.time() - started
    # must stop promptly after cancel, not run the full 60s input
    assert elapsed < 20, f"cancel took too long: {elapsed:.1f}s"


def test_hw_encoder_detection_returns_str_or_none(engine):
    enc = engine.detect_hw_encoder("h264")
    assert enc is None or isinstance(enc, str)
    # second call must come from cache without re-running ffmpeg
    assert engine.detect_hw_encoder("h264") == enc
