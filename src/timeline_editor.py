"""CapCut-style timeline edit operations on a rendered video.

All operations run a single ffmpeg pass on the finished MP4 — no full
re-render is needed:

- delete_range: cut a time range (e.g. a scene) out of the video.
- volume_range: raise/lower/mute the audio for a time range (or the
  whole video). Video stream is copied, so this is fast.
- The caption-free "clean" copy is handled by the caller (plain file
  copy) for the Remove Captions action.
"""
import os
import subprocess

from .ffmpeg_util import find_ffmpeg, popen_no_window


def _base_cmd(ffmpeg, inp, out):
    return [ffmpeg, "-y", "-i", inp]


def _video_codec_args(hw_enc):
    """[video codec args] with CPU fallback handled by the caller."""
    if hw_enc == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20"]
    if hw_enc == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "veryfast",
                "-global_quality", "20"]
    if hw_enc == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "balanced", "-qp", "20"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def _run(cmd, log_cb=None):
    if log_cb:
        log_cb("Running: " + " ".join(cmd[:6]) + " ...")
    p = popen_no_window(cmd, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True)
    tail = []
    for line in p.stdout:
        tail.append(line.rstrip())
    p.wait()
    if p.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed (exit %d):\n%s"
            % (p.returncode, "\n".join(tail[-15:])))
    return True


def delete_range(inp, start, end, out, ffmpeg_path=None, hw_enc=None,
                 log_cb=None):
    """Cut [start, end) seconds out of the video (re-encodes)."""
    ffmpeg = ffmpeg_path or find_ffmpeg()
    s, e = float(start), float(end)
    vf = (f"select='not(between(t,{s:.3f},{e:.3f}))',"
          f"setpts=N/FRAME_RATE/TB")
    af = (f"aselect='not(between(t,{s:.3f},{e:.3f}))',"
          f"asetpts=N/SR/TB")
    cmd = (_base_cmd(ffmpeg, inp, out)
           + ["-vf", vf, "-af", af]
           + _video_codec_args(hw_enc)
           + ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
              out])
    try:
        _run(cmd, log_cb)
    except RuntimeError:
        # Hardware encoder failed at runtime -> retry on CPU.
        if hw_enc:
            if log_cb:
                log_cb("Hardware encoder failed, retrying on CPU...")
            cmd = (_base_cmd(ffmpeg, inp, out)
                   + ["-vf", vf, "-af", af]
                   + _video_codec_args(None)
                   + ["-c:a", "aac", "-b:a", "160k",
                      "-movflags", "+faststart", out])
            _run(cmd, log_cb)
        else:
            raise
    if not os.path.isfile(out):
        raise RuntimeError("ffmpeg produced no output file.")
    return out


def volume_range(inp, start, end, factor, out, duration=None,
                 ffmpeg_path=None, log_cb=None):
    """Scale audio volume by factor for [start, end).

    factor > 1 louder, < 1 quieter, 0 = mute. Video stream is copied,
    so this is fast (audio-only re-encode).
    """
    ffmpeg = ffmpeg_path or find_ffmpeg()
    s, e = float(start), float(end)
    full = duration is not None and s <= 0.01 and e >= float(duration) - 0.01
    if full or (s <= 0 and e <= 0):
        af = f"volume={float(factor):.3f}"
    else:
        af = (f"volume={float(factor):.3f}:"
              f"enable='between(t,{s:.3f},{e:.3f})'")
    cmd = (_base_cmd(ffmpeg, inp, out)
           + ["-af", af, "-c:v", "copy",
              "-c:a", "aac", "-b:a", "160k",
              "-movflags", "+faststart", out])
    _run(cmd, log_cb)
    if not os.path.isfile(out):
        raise RuntimeError("ffmpeg produced no output file.")
    return out
