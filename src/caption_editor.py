"""Post-render caption editor.

After a video is rendered, the user can change the caption template and
caption size WITHOUT re-rendering the whole video. This works because the
render pipeline saves a "clean" copy (video + audio, no burned captions)
plus the sentence/word timings. Re-styling captions is then a single fast
ffmpeg pass with the subtitles filter.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile

from .ffmpeg_util import escape_filter_path, popen_no_window, safe_temp_path
from .text_captions import build_ass


def save_edit_session(session_path: str, clean_video: str,
                      sentence_timings: list, word_timings,
                      fmt: str, res: str, fps: int,
                      template: str, font_size: int,
                      output_video: str) -> str:
    """Persist everything needed to re-style captions later."""
    data = {
        "version": 1,
        "clean_video": clean_video,
        "sentence_timings": sentence_timings,
        "word_timings": word_timings or [],
        "fmt": fmt,
        "res": res,
        "fps": fps,
        "template": template,
        "font_size": font_size,
        "output_video": output_video,
    }
    with open(session_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return session_path


def load_edit_session(session_path: str) -> dict:
    with open(session_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # The clean video must still exist for re-burn to work.
    if not os.path.isfile(data.get("clean_video", "")):
        raise FileNotFoundError(
            "Clean video not found — it may have been cleaned up. "
            "Re-render the video to edit captions.")
    return data


def _play_res(fmt: str, res: str):
    from .export_engine import RESOLUTIONS
    return RESOLUTIONS.get(fmt, {}).get(res, (1920, 1080))


def reburn_captions(clean_video: str, sentence_timings: list, word_timings,
                    template: str, font_size: int,
                    output_path: str, fmt: str = "16:9", res: str = "1080p",
                    ffmpeg_path: str = "ffmpeg",
                    fast_preview: bool = False,
                    hw_enc: str = None,
                    log_cb=None) -> str:
    """Re-burn captions with a new template/size onto the clean video.

    This is a single ffmpeg pass (video re-encode + audio copy) — much
    faster than a full re-render. Uses the hardware encoder when hw_enc
    is given (3-5x faster, same visual quality).
    """
    w, h = _play_res(fmt, res)
    work_dir = os.path.dirname(os.path.abspath(output_path))
    ass_path = safe_temp_path(work_dir, "recaption", ".ass")
    build_ass(sentence_timings, word_timings, ass_path,
              font_size=font_size, highlight=True,
              overlays=None, play_res=(w, h), template=template)

    vf = f"subtitles='{escape_filter_path(ass_path)}'"
    if hw_enc:
        v_args = ["-c:v", hw_enc, "-b:v", "10M"]
    else:
        # Fast preset for interactive previewing; quality preset otherwise.
        preset = "ultrafast" if fast_preview else "veryfast"
        v_args = ["-c:v", "libx264", "-preset", preset, "-crf", "20"]
    tmp_out = safe_temp_path(work_dir, "recaption_out", ".mp4")
    args = [ffmpeg_path, "-y", "-i", clean_video,
            "-vf", vf,
            *v_args,
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart", "-shortest", tmp_out]
    if log_cb:
        log_cb(f"Re-burning captions: template={template}, size={font_size}")
    proc = popen_no_window(args)
    _, err = proc.communicate()
    if proc.returncode != 0:
        tail = (err or b"").decode("utf-8", errors="replace")[-800:]
        raise RuntimeError(f"Caption re-burn failed: {tail}")
    shutil.move(tmp_out, output_path)
    try:
        os.unlink(ass_path)
    except OSError:
        pass
    if log_cb:
        log_cb(f"Captions updated: {output_path}")
    return output_path
