"""Normalizer tests: fast path for compliant files, re-encode for
mismatched resolution/fps/pixel format. Output must always match spec."""
import os

from src.media_normalizer import TargetSpec, mismatch_reasons, normalize_segment
from src.media_probe import probe

SPEC = TargetSpec(width=640, height=360, fps=30.0)


def test_compliant_file_takes_fast_path(media_dir, engine, tmp_path):
    src = os.path.join(media_dir, "valid.mp4")
    assert mismatch_reasons(probe(src, engine.ffprobe), SPEC) == []
    out = str(tmp_path / "n.mp4")
    result = normalize_segment(src, SPEC, out, engine)
    assert result == src, "compliant file must be returned untouched"
    assert not os.path.exists(out)


def test_wrong_resolution_gets_reencoded(media_dir, engine, tmp_path):
    import subprocess
    big = str(tmp_path / "big.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=s=1280x720:r=30:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", big],
                   check=True, timeout=60)
    reasons = mismatch_reasons(probe(big, engine.ffprobe), SPEC)
    assert any("resolution" in r for r in reasons)
    out = str(tmp_path / "n.mp4")
    result = normalize_segment(big, SPEC, out, engine)
    assert result == out
    info = probe(out, engine.ffprobe)
    assert (info.video.width, info.video.height) == (640, 360)
    assert info.video.pix_fmt == "yuv420p"
    assert abs(info.video.fps - 30.0) < 1.0


def test_wrong_fps_gets_reencoded(media_dir, engine, tmp_path):
    import subprocess
    f60 = str(tmp_path / "f60.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=s=640x360:r=60:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", f60],
                   check=True, timeout=60)
    reasons = mismatch_reasons(probe(f60, engine.ffprobe), SPEC)
    assert any("fps" in r for r in reasons)
    out = str(tmp_path / "n.mp4")
    normalize_segment(f60, SPEC, out, engine)
    info = probe(out, engine.ffprobe)
    assert abs(info.video.fps - 30.0) < 1.0
