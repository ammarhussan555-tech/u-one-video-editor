"""Export engine tests: preflight rejects bad inputs, full render
path produces a valid MP4 with real audio, post-render validation
checks the whole checklist."""
import os
import subprocess

import pytest

from src.error_recovery import ValidationError
from src.export_engine import ExportEngine


def _make_concat(workdir, engine, n=2):
    """Concatenated video-only file (like the real concat stage output)."""
    segs = []
    for i in range(n):
        seg = os.path.join(workdir, f"seg_{i}.mp4")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i",
                        "testsrc=s=640x360:r=30:d=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", seg],
                       check=True, timeout=60)
        segs.append(seg)
    lst = os.path.join(workdir, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for s in segs:
            f.write(f"file '{s}'\n")
    concat = os.path.join(workdir, "concat.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", lst,
                    "-c", "copy", concat], check=True, timeout=60)
    return concat


def _make_audio(workdir, dur=4.0):
    a = os.path.join(workdir, "mixed.m4a")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi",
                    "-i", f"sine=frequency=440:duration={dur}",
                    "-c:a", "aac", "-b:a", "128k", a],
                   check=True, timeout=60)
    return a


def _exporter(workdir, engine):
    return ExportEngine(engine=engine)


def test_preflight_rejects_missing_concat(workdir, engine, media_dir):
    ex = _exporter(workdir, engine)
    audio = _make_audio(workdir)
    with pytest.raises(ValidationError) as ei:
        ex.preflight(os.path.join(workdir, "nope.mp4"), audio)
    assert ei.value.stage == "preflight"
    assert "nope.mp4" in ei.value.reason, "must name the offending input"


def test_preflight_rejects_black_concat(workdir, engine, media_dir):
    ex = _exporter(workdir, engine)
    audio = _make_audio(workdir, 2.0)
    with pytest.raises(ValidationError) as ei:
        ex.preflight(os.path.join(media_dir, "black.mp4"), audio)
    assert "black" in ei.value.reason.lower()


def test_preflight_rejects_zero_byte(workdir, engine, media_dir):
    ex = _exporter(workdir, engine)
    audio = _make_audio(workdir)
    with pytest.raises(ValidationError) as ei:
        ex.preflight(os.path.join(media_dir, "empty.mp4"), audio)
    assert ei.value.stage == "preflight"


def test_full_render_final_produces_valid_mp4(workdir, engine):
    from src.media_probe import probe as _probe
    concat = _make_concat(workdir, engine)
    audio = _make_audio(workdir)
    ex = _exporter(workdir, engine)
    out = os.path.join(workdir, "final.mp4")
    result = ex.render_final(concat, audio, "", out, fmt="16:9", res="360p",
                             fps=30, codec="h264", work_dir=workdir)
    assert result.path == out
    assert result.width == 640 and result.height == 360
    assert abs(result.duration - 4.0) < 0.6
    assert not result.captions_burned
    info = _probe(out, engine.ffprobe)
    assert info.has_audio
    assert info.audio.codec in ("aac", "mp4a")
    assert info.audio.sample_rate >= 44100


def test_validate_final_mp4_checklist(workdir, engine):
    concat = _make_concat(workdir, engine)
    audio = _make_audio(workdir)
    ex = _exporter(workdir, engine)
    out = os.path.join(workdir, "final.mp4")
    ex.render_final(concat, audio, "", out, fmt="16:9", res="360p",
                    fps=30, codec="h264", work_dir=workdir)
    problems = ex.validate_final_mp4(out, expect_w=640, expect_h=360,
                                     expect_fps=30, expect_audio=True)
    assert problems == [], f"validation problems: {problems}"
    # and a deliberately broken file fails the checklist
    bad = os.path.join(workdir, "bad.mp4")
    open(bad, "wb").write(os.urandom(100))
    assert ex.validate_final_mp4(bad) != []
