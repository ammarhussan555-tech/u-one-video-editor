"""U One - automatic logo/watermark cleaning for risky-source clips.

Detects STATIC corner overlays (news channel bugs, burned-in @channel tags)
by measuring temporal variance across frames sampled through the clip, then:

- CROPS the edge band when the logo sits in a top/bottom edge strip whose
  surroundings are plain enough that cropping won't hurt the clip, else
- BLURS the logo region (boxblur over the detected bounding box).

Honest limits: only static overlays near corners/edges are handled. Moving
watermarks (bouncing TikTok logos), center watermarks and translucent
full-frame watermarks are NOT detected. Never raises: returns the original
path when nothing is found or anything fails.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

# Sources whose clips may carry burned-in logos / repost tags.
RISKY_SOURCES = {"YouTube", "Telegram", "Reddit", "X"}

_SAMPLE_W = 320          # frame samples are scaled to this width
_N_SAMPLES = 7
_CORNER_FRAC = 0.20      # corner region = 20% x 20% of the frame
_STATIC_STD = 7.0        # temporal std below this => static (0-255 scale)
_DETAIL_EDGE = 4.0       # mean edge magnitude => structured, not flat
_DETAIL_EDGE_PX = 10.0    # per-pixel edge magnitude for logo pixels
_LOGO_FRAC_MIN = 0.03     # logo must cover >=3% of the corner region
_LOGO_FRAC_MAX = 0.70     # ... but <=70% (else the whole corner is static)
_MAX_CROP_FRAC = 0.12    # never crop more than 12% of an edge
_BLUR_R = 14             # boxblur radius for logo regions
_BLUR_FILTER = ("boxblur=luma_radius=12:luma_power=2:"
                "chroma_radius=6:chroma_power=1")
_EDGE_TOUCH = 0.03       # logo must be within 3% of the edge to be croppable


def _ffmpeg() -> Optional[str]:
    try:
        from .ffmpeg_util import find_ffmpeg
        return find_ffmpeg()
    except Exception:  # noqa: BLE001
        return None


def _probe(path: str):
    try:
        from . import media_probe
        from .ffmpeg_util import find_ffprobe
        ff = _ffmpeg()
        fp = find_ffprobe(ff) if ff else None
        return media_probe.probe(path, fp)
    except Exception:  # noqa: BLE001
        return None


def _sample_frames(path: str, ff: str, dur: float,
                   sw: int, sh: int) -> List["object"]:
    """Extract up to _N_SAMPLES rgb24 frames as numpy arrays."""
    import numpy as np
    from .ffmpeg_util import run_no_window
    n = min(_N_SAMPLES, max(3, int(dur // 3) if dur else 3))
    frames = []
    for i in range(n):
        t = dur * (i + 1) / (n + 1) if dur else 0.5 + i
        try:
            r = run_no_window(
                [ff, "-hide_banner", "-loglevel", "error",
                 "-ss", f"{t:.2f}", "-i", path,
                 "-frames:v", "1", "-vf", f"scale={sw}:{sh}",
                 "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                capture_output=True, timeout=60)
        except Exception:  # noqa: BLE001
            continue
        if r.returncode != 0 or not r.stdout:
            continue
        expect = sw * sh * 3
        if len(r.stdout) < expect:
            continue
        frames.append(
            np.frombuffer(r.stdout[:expect], dtype=np.uint8
                          ).reshape(sh, sw, 3))
    return frames


def _edge_mean(gray) -> float:
    import numpy as np
    gx = np.abs(np.diff(gray.astype(np.int16), axis=1)).mean()
    gy = np.abs(np.diff(gray.astype(np.int16), axis=0)).mean()
    return float(gx + gy)


def _detect(frames: List["object"]) -> tuple:
    """Return (overlay dicts, mean_gray). An overlay is a LOCALIZED cluster
    of static + detailed pixels inside a corner (a logo/bug) - not a corner
    that is entirely static (static scene/background)."""
    import numpy as np
    if len(frames) < 3:
        return [], None
    stack = np.stack([f.astype(np.float32) for f in frames], axis=0)
    sh, sw = stack.shape[1], stack.shape[2]
    gray_stack = stack.mean(axis=-1)                      # (n, h, w)
    tvar = gray_stack.std(axis=0)                         # (h, w)
    mean_gray = gray_stack.mean(axis=0)
    gy, gx = np.gradient(mean_gray)
    edge = np.abs(gx) + np.abs(gy)
    logo_mask = (tvar < _STATIC_STD) & (edge > _DETAIL_EDGE_PX)

    cw, ch = int(sw * _CORNER_FRAC), int(sh * _CORNER_FRAC)
    corners = {
        "tl": (0, 0, cw, ch), "tr": (sw - cw, 0, sw, ch),
        "bl": (0, sh - ch, cw, sh), "br": (sw - cw, sh - ch, sw, sh),
    }
    found = []
    for name, (x0, y0, x1, y1) in corners.items():
        region = logo_mask[y0:y1, x0:x1]
        frac = region.mean()
        if frac < _LOGO_FRAC_MIN or frac > _LOGO_FRAC_MAX:
            continue  # no logo, or the whole corner is static background
        ys, xs = np.where(region)
        bx0, bx1 = int(xs.min()) + x0, int(xs.max()) + x0
        by0, by1 = int(ys.min()) + y0, int(ys.max()) + y0
        # require a minimum size so noise specks don't count
        if (bx1 - bx0) < 6 or (by1 - by0) < 6:
            continue
        pad = 5
        found.append({
            "corner": name,
            # raw bbox (no pad) -> used for the crop-band decision
            "bbox_raw": (bx0, by0, bx1, by1),
            # padded bbox -> used for the blur region
            "bbox": (max(0, bx0 - pad), max(0, by0 - pad),
                     min(sw, bx1 + pad), min(sh, by1 + pad)),
        })
    return found, mean_gray


def _plan(sh_real: int, sw_real: int, overlays: List[Dict],
          mean_gray, sx: float, sy: float
          ) -> Tuple[int, int, List[Tuple[int, int, int, int]]]:
    """Decide crop vs blur. Returns (crop_top, crop_bottom, blur_boxes).

    Crop is chosen only when the logo touches a top/bottom edge, the strip
    is thin (<=12%), AND the strip away from the logo is plain (low detail)
    so removing it won't hurt the clip. Otherwise the logo region is
    blurred. blur_boxes are in post-crop real-pixel coords.
    """
    sw = mean_gray.shape[1]
    crop_top, crop_bottom = 0, 0
    blurs: List[Tuple[int, int, int, int]] = []

    def strip_plain(side: str, y0: int, y1: int, logo_left: bool) -> bool:
        # check the half of the strip AWAY from the corner logo
        half = (0, sw // 2) if logo_left else (sw // 2, sw)
        region = mean_gray[y0:y1, half[0]:half[1]]
        if region.size == 0:
            return False
        return _edge_mean(region) < _DETAIL_EDGE * 0.8

    for ov in overlays:
        # padded bbox (sample px) -> blur region; raw bbox -> crop decision
        (px0, py0, px1, py1) = ov["bbox"]
        (ux0, uy0, ux1, uy1) = ov.get("bbox_raw", ov["bbox"])
        corner = ov["corner"]
        sh_s = mean_gray.shape[0]
        touches_top = uy0 <= sh_s * _EDGE_TOUCH
        touches_bottom = uy1 >= sh_s * (1 - _EDGE_TOUCH)

        cropped = False
        if corner in ("tl", "tr") and touches_top:
            band = int(uy1 * sy)  # raw strip height that removes the logo
            if band <= int(sh_real * _MAX_CROP_FRAC) and strip_plain(
                    "top", 0, uy1, logo_left=(corner == "tr")):
                crop_top = max(crop_top, band)
                cropped = True
        elif corner in ("bl", "br") and touches_bottom:
            band = sh_real - int(uy0 * sy)
            if band <= int(sh_real * _MAX_CROP_FRAC) and strip_plain(
                    "bottom", uy0, sh_s, logo_left=(corner == "br")):
                crop_bottom = max(crop_bottom, band)
                cropped = True
        if not cropped:
            # blur the padded region (coords adjusted for any top crop)
            blurs.append((int(px0 * sx), int(py0 * sy) - crop_top,
                          int(px1 * sx), int(py1 * sy) - crop_top))
    return crop_top, crop_bottom, blurs


def _apply(path: str, out_path: str, crop_top: int, crop_bottom: int,
           blurs: List[Tuple[int, int, int, int]],
           has_audio: bool) -> bool:
    """One ffmpeg pass: crop edge bands, then blur logo boxes."""
    from .ffmpeg_util import run_no_window
    ff = _ffmpeg()
    if not ff:
        return False
    vf_parts: List[str] = []
    stream = "[0:v]"
    n = 0
    if crop_top or crop_bottom:
        h_expr = f"ih-{crop_top + crop_bottom}"
        vf_parts.append(f"{stream}crop=iw:{h_expr}:0:{crop_top}[c{n}]")
        stream = f"[c{n}]"
        n += 1
    for (x0, y0, x1, y1) in blurs:
        w, h = x1 - x0, y1 - y0
        if w < 8 or h < 8:
            continue
        vf_parts.append(
            f"{stream}split=2[s{n}a][s{n}b];"
            f"[s{n}a]crop={w}:{h}:{x0}:{y0},"
            f"{_BLUR_FILTER}[bg{n}];"
            f"[s{n}b][bg{n}]overlay={x0}:{y0}[c{n}]")
        stream = f"[c{n}]"
        n += 1
    if n == 0:
        return False
    vf_parts.append(f"{stream}format=yuv420p[vout]")
    vf = ";".join(vf_parts)
    args = ["-hide_banner", "-loglevel", "error", "-y",
            "-i", path, "-filter_complex", vf,
            "-map", "[vout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    if has_audio:
        args += ["-map", "0:a:0?", "-c:a", "copy"]
    else:
        args += ["-an"]
    args.append(out_path)
    try:
        r = run_no_window([ff, *args], capture_output=True, timeout=900)
    except Exception:  # noqa: BLE001
        return False
    return r.returncode == 0 and os.path.isfile(out_path) \
        and os.path.getsize(out_path) > 0


def maybe_clean_clip(path: str, work_dir: str,
                     source: str = "") -> str:
    """Crop/blur static corner logos on risky-source clips.

    Returns the cleaned file path, or `path` unchanged when there is
    nothing to clean or anything fails. Never raises.
    """
    try:
        if (source or "") not in RISKY_SOURCES:
            return path
        info = _probe(path)
        if info is None or not getattr(info, "exists", False):
            return path
        vs = getattr(info, "video", None)
        w = int(getattr(vs, "width", 0) or 0) if vs else 0
        h = int(getattr(vs, "height", 0) or 0) if vs else 0
        dur = float(getattr(info, "duration", 0) or 0)
        if w < 160 or h < 160:
            return path
        ff = _ffmpeg()
        if not ff:
            return path
        sw = _SAMPLE_W
        sh = max(2, int(round(h * sw / w)) & ~1)
        frames = _sample_frames(path, ff, dur, sw, sh)
        overlays, mean_gray = _detect(frames)
        if not overlays or mean_gray is None:
            return path
        sx, sy = w / sw, h / sh
        crop_top, crop_bottom, blurs = _plan(h, w, overlays, mean_gray,
                                             sx, sy)
        if crop_top == 0 and crop_bottom == 0 and not blurs:
            return path
        base = os.path.splitext(os.path.basename(path))[0]
        out_path = os.path.join(work_dir, base + "_clean.mp4")
        ok = _apply(path, out_path, crop_top, crop_bottom, blurs,
                    bool(getattr(info, "has_audio", False)))
        if ok:
            return out_path
        try:
            if os.path.isfile(out_path):
                os.remove(out_path)
        except OSError:  # noqa: BLE001
            pass
        return path
    except Exception:  # noqa: BLE001
        return path
