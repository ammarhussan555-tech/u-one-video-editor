"""Merged single-pass burn: captions + documentary overlays in ONE encode.

Uzair 2026-10-04: "1 he video bane" -- the documentary overlays must ride
inside the caption burn's filter_complex instead of a separate full-video
pass. This test renders a tiny video end-to-end through
ExportEngine.render_final with documentary mode inputs and verifies the
output is valid (correct size/fps/duration, overlays burned).
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.export_engine import ExportEngine


def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def test_merged_burn_produces_valid_video(tmp_path):
    work = tmp_path
    vid = str(work / "clean.mp4")
    aud = str(work / "mix.m4a")
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
          "-i", "color=c=0x274b73:s=640x360:d=6:r=30",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", vid])
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
          "-i", "anullsrc=r=48000:cl=stereo:d=6", "-c:a", "aac", aud])
    ass = work / "cap.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\n"
        "\n[V4+ Styles]\n"
        "Format: Name, Fontname, PrimaryColour, Bold, Alignment, Encoding\n"
        "Style: Default,Arial,&H00FFFFFF,-1,5,1\n"
        "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Hello world\n")
    # Location-free script: chapters + quotes + lower thirds, no geocoding.
    script = (
        "THE RISE\n\nThe team lifted the trophy after a historic victory "
        "that made headlines across the nation.\n\n\"We will never give "
        "up,\" the captain declared on 12 March 2024.\n\nTHE FALL\n\n"
        "Critics questioned every decision in the months that followed.")
    eng = ExportEngine()
    out = str(work / "final.mp4")
    res = eng.render_final(vid, aud, str(ass), out, fmt="16:9", res="360p",
                           fps=30, codec="h264", preview=True,
                           work_dir=str(work), prefer_hw=False,
                           doc_script=script, doc_timings=[],
                           doc_cache_dir=str(work / "doc"))
    assert os.path.isfile(out)
    assert res.captions_burned
    assert (res.width, res.height) == (640, 360)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,avg_frame_rate,duration",
         "-of", "csv=p=0", out],
        capture_output=True, text=True, check=True)
    w, h, fps_s, dur = probe.stdout.strip().split(",")
    assert (w, h) == ("640", "360")
    num, den = fps_s.split("/")
    assert abs(float(num) / float(den) - 30) < 1.0
    assert abs(float(dur) - 6.0) < 0.6
    # overlay PNGs were actually generated and fed to the burn
    pngs = list((work / "doc").glob("*.png"))
    assert len(pngs) >= 2, f"expected overlay PNGs, got {pngs}"


def test_burn_without_documentary_unchanged(tmp_path):
    """OFF path: no doc inputs -> plain caption burn, same as before."""
    work = tmp_path
    vid = str(work / "clean.mp4")
    aud = str(work / "mix.m4a")
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
          "-i", "color=c=0x274b73:s=640x360:d=4:r=30",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", vid])
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
          "-i", "anullsrc=r=48000:cl=stereo:d=4", "-c:a", "aac", aud])
    ass = work / "cap.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\n"
        "\n[V4+ Styles]\n"
        "Format: Name, Fontname, PrimaryColour, Bold, Alignment, Encoding\n"
        "Style: Default,Arial,&H00FFFFFF,-1,5,1\n"
        "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hi\n")
    eng = ExportEngine()
    out = str(work / "final.mp4")
    res = eng.render_final(vid, aud, str(ass), out, fmt="16:9", res="360p",
                           fps=30, codec="h264", preview=True,
                           work_dir=str(work), prefer_hw=False)
    assert os.path.isfile(out)
    assert res.captions_burned
    assert (res.width, res.height) == (640, 360)
