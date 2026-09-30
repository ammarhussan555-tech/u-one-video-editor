"""U One - automatic logo/watermark cleaning for risky-source clips.

Detects overlays by measuring temporal behaviour across frames sampled
through the clip:

- STATIC overlays (news bugs, burned-in @tags, centered watermarks),
  opaque or semi-transparent: low temporal variance + persistent edges
  (the temporal minimum of per-frame edge maps - a static logo has an
  edge at its boundary in every frame, moving content does not).
  Checked in corners AND the center (stricter gates in the center).
- MOVING watermarks (bouncing logos, incl. centered): candidate graphics
  are extracted from the middle sample frame and located in every other
  sample frame with normalized cross-correlation. A graphic that
  reappears at clearly
  different positions is a moving watermark; its full motion range is
  blurred for the whole clip (time-segmented tracking can't reliably
  follow fast motion with sparse samples).

- TILED watermarks (stock-photo grids): the same graphic at 3+ positions
  in one frame is blurred at every tile for the whole clip.

Then:

- CROPS the edge band when a static logo sits in a top/bottom edge strip
  whose surroundings are plain enough that cropping won't hurt the clip,
- BLURS the logo region otherwise (static: logo box; moving: motion
  range; tiled: every tile).

Honest limits: full-frame translucent washes and watermarks whose
appearance constantly morphs are not reliably handled.
watermarks that constantly change appearance are NOT handled. Never
raises: returns the original path when nothing is found or anything
fails.
"""
from __future__ import annotations

import os
from collections import deque
from typing import Dict, List, Optional, Tuple

# Sources whose clips may carry burned-in logos / repost tags.
RISKY_SOURCES = {"YouTube", "Telegram", "Reddit", "X"}

_SAMPLE_W = 320          # frame samples are scaled to this width
_N_SAMPLES = 7
_CORNER_FRAC = 0.25      # corner region = 25% x 25% of the frame
_STATIC_STD = 7.0        # temporal std below this => static (0-255 scale)
_TRANS_STD = 26.0        # ... below this => maybe translucent static
_TRANS_REL = 0.6         # translucent pixel calmer than 60% of corner bg
_DETAIL_EDGE = 4.0       # mean edge magnitude => structured, not flat
_MIN_EDGE_PX = 2.5       # per-pixel temporal-min edge for logo boundary px
_STRONG_EDGE_PX = 10.0    # a real logo has SOME strong edge pixels (rejects
                          # smooth-bg blobs whose edges never get strong)
_BLOB_MIN_PX = 30        # min boundary pixels in a corner to look for a logo
_DENS_PEAK_MIN = 0.10    # 21x21 density peak that marks a logo cluster
_DENS_FLOOD_MIN = 0.04   # flood level around the peak for the logo bbox
_LINE_RATIO_MAX = 25.0   # PCA eigenvalue ratio above this => it's a line, not a logo
_MAX_CROP_FRAC = 0.12    # never crop more than 12% of an edge
_BLUR_FILTER = ("boxblur=luma_radius=12:luma_power=2:"
                "chroma_radius=6:chroma_power=1")
_EDGE_TOUCH = 0.03       # logo must be within 3% of the edge to be croppable
# moving-watermark tuning
_BAND_FRAC = 0.30        # watermarks live in the outer 30% border band
_BLOB_EDGE = 12.0        # edge magnitude that marks graphic pixels
_NCC_MIN = 0.72          # template match score to count as "same graphic"
_MIN_MATCH_FRAMES = 4    # ... in at least this many sample frames
_MOVE_STD_MIN = 12.0     # position std (sample px) that means "moving"


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
                   sw: int, sh: int) -> Tuple[List["object"], List[float]]:
    """Extract up to _N_SAMPLES rgb24 frames as numpy arrays + their times."""
    import numpy as np
    from .ffmpeg_util import run_no_window
    n = min(_N_SAMPLES, max(4, int(dur // 3) if dur else 4))
    frames, times = [], []
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
        times.append(t)
    return frames, times


def _edge_mean(gray) -> float:
    import numpy as np
    gx = np.abs(np.diff(gray.astype(np.int16), axis=1)).mean()
    gy = np.abs(np.diff(gray.astype(np.int16), axis=0)).mean()
    return float(gx + gy)


def _box_density(mask, k: int):
    """Mean of `mask` over kxk windows (float array, same size, pure numpy)."""
    import numpy as np
    h, w = mask.shape
    m = mask.astype(np.float64)
    ii = np.zeros((h + 1, w + 1))
    ii[1:, 1:] = np.cumsum(np.cumsum(m, axis=0), axis=1)
    kk = min(k, h, w)
    s = (ii[kk:, kk:] - ii[:-kk, kk:] - ii[kk:, :-kk] + ii[:-kk, :-kk])
    out = np.zeros((h, w))
    oh, ow = s.shape
    y0, x0 = (h - oh) // 2, (w - ow) // 2
    out[y0:y0 + oh, x0:x0 + ow] = s / (kk * kk)
    return out


def _flood_from(binary, sy: int, sx: int):
    """8-way flood fill from (sy,sx) over True pixels -> bool mask."""
    import numpy as np
    h, w = binary.shape
    seen = np.zeros((h, w), bool)
    if not binary[sy, sx]:
        return seen
    q = deque([(sy, sx)])
    seen[sy, sx] = True
    while q:
        cy, cx = q.popleft()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                ny, nx = cy + dy, cx + dx
                if (0 <= ny < h and 0 <= nx < w
                        and binary[ny, nx] and not seen[ny, nx]):
                    seen[ny, nx] = True
                    q.append((ny, nx))
    return seen


def _detect(frames: List["object"]) -> tuple:
    """Return (overlay dicts, mean_gray). An overlay is a LOCALIZED DENSE
    cluster of static boundary pixels inside a corner (a logo/bug).

    A logo - opaque or translucent - has an edge at its boundary in EVERY
    sampled frame, while moving backgrounds only have edges at a fixed
    pixel sometimes. So: temporal-minimum edge map + low temporal variance
    (+ calmer than the corner's surroundings for translucent logos) gives
    boundary pixels; a 21x21 density map then finds compact clusters.
    Density (not dilation) is used so a logo never merges with diffuse
    background clutter into one giant blob.
    """
    import numpy as np
    if len(frames) < 3:
        return [], None
    stack = np.stack([f.astype(np.float32) for f in frames], axis=0)
    sh, sw = stack.shape[1], stack.shape[2]
    gray_stack = stack.mean(axis=-1)                      # (n, h, w)
    tvar = gray_stack.std(axis=0)                         # (h, w)
    mean_gray = gray_stack.mean(axis=0)
    emin = None
    for g in gray_stack:
        gy, gx = np.gradient(g)
        e = np.abs(gx) + np.abs(gy)
        emin = e if emin is None else np.minimum(emin, e)

    cw, ch = int(sw * _CORNER_FRAC), int(sh * _CORNER_FRAC)
    corners = {
        "tl": (0, 0, cw, ch), "tr": (sw - cw, 0, sw, ch),
        "bl": (0, sh - ch, cw, sh), "br": (sw - cw, sh - ch, sw, sh),
    }
    # Center region for centered watermarks (stricter gates - the center
    # is busier than corners, so we demand stronger evidence).
    cx0, cy0 = sw // 4, sh // 4
    regions = list(corners.items()) + [("center", (cx0, cy0, sw - cx0, sh - cy0))]
    found = []
    for name, (x0, y0, x1, y1) in regions:
        is_center = (name == "center")
        # background variance level of this region (robust median)
        bg_var = float(np.median(tvar[y0:y1, x0:x1])) + 1e-6
        # static enough: hard-static (opaque logo) or calmer than the
        # region's surroundings (translucent logo over moving bg)
        calm = (tvar < _STATIC_STD) | (tvar < _TRANS_REL * bg_var)
        bound = (tvar < _TRANS_STD) & (emin > _MIN_EDGE_PX) & calm
        region = bound[y0:y1, x0:x1]
        min_px = _BLOB_MIN_PX * 2 if is_center else _BLOB_MIN_PX
        if int(region.sum()) < min_px:
            continue
        dens = _box_density(region, 21)
        peak_min = _DENS_PEAK_MIN * 1.5 if is_center else _DENS_PEAK_MIN
        for _ in range(2):  # up to two logos per region
            flat = int(np.argmax(dens))
            py, px = flat // dens.shape[1], flat % dens.shape[1]
            if dens[py, px] < peak_min:
                break
            blob = _flood_from(dens > _DENS_FLOOD_MIN, py, px)
            if blob.sum() > 0.70 * region.size:
                break  # the whole corner is static - not a logo
            ys, xs = np.nonzero(blob)
            bx0, bx1 = int(xs.min()), int(xs.max())
            by0, by1 = int(ys.min()), int(ys.max())
            bw_ = bx1 - bx0 + 1
            bh_ = by1 - by0 + 1
            if bw_ < 12 or bh_ < 12 or bw_ > 150 or bh_ > 150:
                dens[blob] = 0
                continue
            # a real logo/graphic has SOME strong edge pixels; smooth
            # background textures never do - this kills false positives
            core = region[by0:by1 + 1, bx0:bx1 + 1]
            if not core.any() or float(
                    emin[y0 + by0:y0 + by1 + 1,
                         x0 + bx0:x0 + bx1 + 1][core].max()) < _STRONG_EDGE_PX:
                dens[blob] = 0
                continue
            # reject 1-D structures (horizon lines, banner edges): a logo
            # is a compact 2-D blob, a line has one dominant PCA direction
            cys, cxs = np.nonzero(core)
            if len(cxs) >= 10:
                pts = np.stack([cxs.astype(np.float64),
                                cys.astype(np.float64)], axis=1)
                cov = np.cov(pts, rowvar=False) + 1e-9 * np.eye(2)
                ev = np.linalg.eigvalsh(cov)
                if ev[1] / max(ev[0], 1e-9) > _LINE_RATIO_MAX:
                    dens[blob] = 0
                    continue
            pad = 5
            found.append({
                "corner": name,
                # raw bbox (no pad) -> used for the crop-band decision
                "bbox_raw": (x0 + bx0, y0 + by0, x0 + bx1, y0 + by1),
                # padded bbox -> used for the blur region
                "bbox": (max(0, x0 + bx0 - pad), max(0, y0 + by0 - pad),
                         min(sw, x0 + bx1 + pad), min(sh, y0 + by1 + pad)),
            })
            dens[blob] = 0
    return found, mean_gray


def _ncc_best(img, tmpl, band) -> Optional[Tuple[Tuple[int, int], float]]:
    """Normalized cross-correlation of tmpl inside img (float32 2D).

    Returns ((x, y), score) of the best match whose center lies in `band`,
    or None when the template is flat.
    """
    import numpy as np
    H, W = img.shape
    h, w = tmpl.shape
    if h > H or w > W or h < 4 or w < 4:
        return None
    t = tmpl - tmpl.mean()
    t_ss = float((t * t).sum())
    if t_ss < 1e-9:
        return None
    N = h * w
    # cross-correlation via FFT: xcorr[k] = sum_n img[n]*t[n-k], so the
    # 'valid' map is the top-left (H-h+1, W-w+1) block. (t already has
    # zero mean, so the correlation value IS the centered numerator.)
    fh, fw = H + h, W + w
    F1 = np.fft.rfft2(img, s=(fh, fw))
    F2 = np.fft.rfft2(t, s=(fh, fw))
    xcorr = np.fft.irfft2(F1 * np.conj(F2), s=(fh, fw))
    C = xcorr[:H - h + 1, :W - w + 1]
    # local window sums via integral images
    ii = np.zeros((H + 1, W + 1), dtype=np.float64)
    ii[1:, 1:] = np.cumsum(np.cumsum(img, axis=0), axis=1)
    ii2 = np.zeros((H + 1, W + 1), dtype=np.float64)
    ii2[1:, 1:] = np.cumsum(np.cumsum(img * img, axis=0), axis=1)
    S = ii[h:, w:] - ii[:-h, w:] - ii[h:, :-w] + ii[:-h, :-w]
    Q = ii2[h:, w:] - ii2[:-h, w:] - ii2[h:, :-w] + ii2[:-h, :-w]
    mu = S / N
    var = np.maximum(Q / N - mu * mu, 0.0)
    denom = np.sqrt(var) * np.sqrt(t_ss) * np.sqrt(N)
    ncc = np.where(denom > 1e-9, C / np.maximum(denom, 1e-9), 0.0)
    # only positions whose template center falls inside the border band
    cy = np.arange(ncc.shape[0]) + h // 2
    cx = np.arange(ncc.shape[1]) + w // 2
    valid = band[cy[:, None], cx[None, :]]
    ncc = np.where(valid, ncc, -1.0)
    j = int(np.argmax(ncc))
    iy, ix = j // ncc.shape[1], j % ncc.shape[1]
    return (ix, iy), float(ncc[iy, ix])


def _ncc_all(img, tmpl, band, thresh: float) -> List[Tuple[int, int, float]]:
    """All NCC match positions >= thresh (with non-maximum suppression).

    Returns [(x, y, score)] in img pixels. Used for tiled watermarks where
    the same graphic repeats at several positions in one frame.
    """
    import numpy as np
    H, W = img.shape
    h, w = tmpl.shape
    if h > H or w > W or h < 4 or w < 4:
        return []
    t = tmpl - tmpl.mean()
    t_ss = float((t * t).sum())
    if t_ss < 1e-9:
        return []
    N = h * w
    fh, fw = H + h, W + w
    F1 = np.fft.rfft2(img, s=(fh, fw))
    F2 = np.fft.rfft2(t, s=(fh, fw))
    xcorr = np.fft.irfft2(F1 * np.conj(F2), s=(fh, fw))
    C = xcorr[:H - h + 1, :W - w + 1]
    ii = np.zeros((H + 1, W + 1), dtype=np.float64)
    ii[1:, 1:] = np.cumsum(np.cumsum(img, axis=0), axis=1)
    ii2 = np.zeros((H + 1, W + 1), dtype=np.float64)
    ii2[1:, 1:] = np.cumsum(np.cumsum(img * img, axis=0), axis=1)
    S = ii[h:, w:] - ii[:-h, w:] - ii[h:, :-w] + ii[:-h, :-w]
    Q = ii2[h:, w:] - ii2[:-h, w:] - ii2[h:, :-w] + ii2[:-h, :-w]
    mu = S / N
    var = np.maximum(Q / N - mu * mu, 0.0)
    denom = np.sqrt(var) * np.sqrt(t_ss) * np.sqrt(N)
    ncc = np.where(denom > 1e-9, C / np.maximum(denom, 1e-9), 0.0)
    cy = np.arange(ncc.shape[0]) + h // 2
    cx = np.arange(ncc.shape[1]) + w // 2
    valid = band[cy[:, None], cx[None, :]]
    ncc = np.where(valid, ncc, -1.0)
    # non-maximum suppression: greedily take peaks, suppress neighborhood
    out = []
    work = ncc.copy()
    rad = max(h, w) // 2
    for _ in range(12):  # at most 12 tiles
        j = int(np.argmax(work))
        iy, ix = j // work.shape[1], j % work.shape[1]
        s = float(work[iy, ix])
        if s < thresh:
            break
        out.append((ix, iy, s))
        y0, y1 = max(0, iy - rad), min(work.shape[0], iy + rad + 1)
        x0, x1 = max(0, ix - rad), min(work.shape[1], ix + rad + 1)
        work[y0:y1, x0:x1] = -1.0
    return out


def _detect_tiled(frames: List["object"],
                  static_overlays: List[Dict]) -> List[Dict]:
    """Find tiled/repeating watermarks (e.g. stock-photo grids).

    Takes candidate graphics from the middle sample frame and looks for
    the SAME graphic at 3+ positions in that frame. Returns
    [{"boxes": [(x0,y0,x1,y1), ...]}] in sample pixels (static positions,
    blurred for the whole clip). Never raises.
    """
    import numpy as np
    try:
        n = len(frames)
        if n < 3:
            return []
        grays = [f.astype(np.float32).mean(axis=-1) for f in frames]
        sh, sw = grays[0].shape
        mid = n // 2
        gy, gx = np.gradient(grays[mid])
        emid = np.abs(gx) + np.abs(gy)
        band = np.ones((sh, sw), bool)  # tiles can be anywhere

        # candidate blobs (same criteria as _detect_moving)
        active = emid > _BLOB_EDGE
        visited = np.zeros_like(active, bool)
        cands = []
        ys, xs = np.where(active)
        for sy, sx in zip(ys.tolist(), xs.tolist()):
            if visited[sy, sx]:
                continue
            q = deque([(sy, sx)])
            visited[sy, sx] = True
            pts = []
            while q:
                cy, cx = q.popleft()
                pts.append((cy, cx))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        ny, nx = cy + dy, cx + dx
                        if (0 <= ny < sh and 0 <= nx < sw
                                and active[ny, nx] and not visited[ny, nx]):
                            visited[ny, nx] = True
                            q.append((ny, nx))
            arr = np.array(pts)
            y0, y1 = int(arr[:, 0].min()), int(arr[:, 0].max())
            x0, x1 = int(arr[:, 1].min()), int(arr[:, 1].max())
            bw_ = x1 - x0 + 1
            bh_ = y1 - y0 + 1
            if not (8 <= bw_ <= 90 and 8 <= bh_ <= 90):
                continue
            if len(pts) < 40:
                continue
            if len(pts) / (bw_ * bh_) < 0.15:
                continue
            cands.append((x0, y0, x1, y1))
            if len(cands) >= 4:
                break

        def _iou(a, b):
            ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
            ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
            iw, ih = ix1 - ix0, iy1 - iy0
            if iw <= 0 or ih <= 0:
                return 0.0
            inter = iw * ih
            ua = ((a[2] - a[0]) * (a[3] - a[1])
                  + (b[2] - b[0]) * (b[3] - b[1]) - inter)
            return inter / ua if ua > 0 else 0.0

        stat_boxes = [o["bbox"] for o in static_overlays]
        found = []
        for (x0, y0, x1, y1) in cands:
            if any(_iou((x0, y0, x1, y1), s) > 0.25 for s in stat_boxes):
                continue
            tmpl = emid[max(0, y0 - 2):y1 + 3,
                        max(0, x0 - 2):x1 + 3].astype(np.float32)
            if tmpl.shape[0] < 4 or tmpl.shape[1] < 4:
                continue
            th, tw = tmpl.shape
            matches = _ncc_all(emid.astype(np.float32), tmpl, band,
                               _NCC_MIN)
            # need 3+ distinct positions (the original + 2 repeats)
            if len(matches) < 3:
                continue
            boxes = [(mx, my, mx + tw, my + th) for mx, my, _ in matches]
            found.append({"boxes": boxes, "tiled": True})
            if found:
                break  # one tiled watermark is enough
        return found
    except Exception:
        return []


def _detect_moving(frames: List["object"], times: List[float],
                   static_overlays: List[Dict],
                   tiled_overlays: List[Dict] = None) -> List[Dict]:
    """Find moving watermarks via template matching.

    Candidate graphics are taken from the middle sample frame; each is
    located in every sample frame with NCC on edge images. A graphic that
    matches in >= _MIN_MATCH_FRAMES frames at clearly different positions
    is a moving watermark. Candidates overlapping tiled watermarks are
    skipped (a static tile grid can look like movement when the best NCC
    peak jumps between identical tiles). Returns [{"boxes":
    [(x0,y0,x1,y1,t), ...]}] in sample pixels. Never raises.
    """
    import numpy as np
    try:
        n = len(frames)
        if n < 4 or len(times) != n:
            return []
        grays = [f.astype(np.float32).mean(axis=-1) for f in frames]
        sh, sw = grays[0].shape
        edges = []
        for g in grays:
            gy, gx = np.gradient(g)
            edges.append(np.abs(gx) + np.abs(gy))

        bh, bw = int(sh * _BAND_FRAC), int(sw * _BAND_FRAC)
        # Search band: edges + center (centered/bouncing watermarks live
        # in the middle too, not just the edge strips).
        band = np.zeros((sh, sw), bool)
        band[:bh, :] = True
        band[sh - bh:, :] = True
        band[:, :bw] = True
        band[:, sw - bw:] = True
        band[sh // 4:3 * sh // 4, sw // 4:3 * sw // 4] = True

        def blobs_in(ei):
            active = (ei > _BLOB_EDGE) & band
            visited = np.zeros_like(active, bool)
            out = []
            ys, xs = np.where(active)
            for sy, sx in zip(ys.tolist(), xs.tolist()):
                if visited[sy, sx]:
                    continue
                q = deque([(sy, sx)])
                visited[sy, sx] = True
                pts = []
                while q:
                    cy, cx = q.popleft()
                    pts.append((cy, cx))
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            if dy == 0 and dx == 0:
                                continue
                            ny, nx = cy + dy, cx + dx
                            if (0 <= ny < sh and 0 <= nx < sw
                                    and active[ny, nx]
                                    and not visited[ny, nx]):
                                visited[ny, nx] = True
                                q.append((ny, nx))
                arr = np.array(pts)
                y0, y1 = int(arr[:, 0].min()), int(arr[:, 0].max())
                x0, x1 = int(arr[:, 1].min()), int(arr[:, 1].max())
                bw_ = x1 - x0 + 1
                bh_ = y1 - y0 + 1
                if not (8 <= bw_ <= 90 and 8 <= bh_ <= 90):
                    continue
                if len(pts) < 40:
                    continue
                if len(pts) / (bw_ * bh_) < 0.15:
                    continue
                out.append((x0, y0, x1, y1))
            return out

        def _iou(a, b):
            ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
            ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
            iw, ih = ix1 - ix0, iy1 - iy0
            if iw <= 0 or ih <= 0:
                return 0.0
            inter = iw * ih
            ua = ((a[2] - a[0]) * (a[3] - a[1])
                  + (b[2] - b[0]) * (b[3] - b[1]) - inter)
            return inter / ua if ua > 0 else 0.0

        mid = n // 2
        cands = blobs_in(edges[mid])[:4]
        stat_boxes = [o["bbox"] for o in static_overlays]
        tile_boxes = []
        for t in (tiled_overlays or []):
            tile_boxes += t["boxes"]
        skip_boxes = stat_boxes + tile_boxes
        cands = [c for c in cands
                 if not any(_iou(c, s) > 0.25 for s in skip_boxes)]

        found = []
        for (x0, y0, x1, y1) in cands:
            tmpl = edges[mid][max(0, y0 - 2):y1 + 3,
                              max(0, x0 - 2):x1 + 3].astype(np.float32)
            if tmpl.shape[0] < 4 or tmpl.shape[1] < 4:
                continue
            th, tw = tmpl.shape
            matches = []
            for i, e in enumerate(edges):
                r = _ncc_best(e.astype(np.float32), tmpl, band)
                if r is None:
                    continue
                (mx, my), score = r
                if score < _NCC_MIN:
                    continue
                matches.append((i, mx, my, mx + tw, my + th))
            if len(matches) < _MIN_MATCH_FRAMES:
                continue
            cx = np.array([(m[1] + m[3]) / 2 for m in matches])
            cy = np.array([(m[2] + m[4]) / 2 for m in matches])
            if (float(cx.std()) < _MOVE_STD_MIN
                    and float(cy.std()) < _MOVE_STD_MIN):
                continue  # static - the static path owns it
            boxes = [(m[1], m[2], m[3], m[4], times[m[0]])
                     for m in matches]
            found.append({"boxes": boxes})
        return found
    except Exception:  # noqa: BLE001
        return []


def _plan(sh_real: int, sw_real: int, overlays: List[Dict],
          moving: List[Dict], tiled: List[Dict], mean_gray, sx: float,
          sy: float, dur: float) -> Tuple[int, int, List[Tuple]]:
    """Decide crop vs blur.

    Returns (crop_top, crop_bottom, blur_specs). A blur spec is
    (x0, y0, x1, y1, t0, t1) in post-crop real-pixel coords; t0/t1 are
    None for a whole-clip blur. Crop is chosen only when a static logo
    touches a top/bottom edge, the strip is thin (<=12%), AND the strip
    away from the logo is plain so removing it won't hurt the clip.
    """
    sw = mean_gray.shape[1]
    crop_top, crop_bottom = 0, 0
    blurs: List[Tuple] = []

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
                          int(px1 * sx), int(py1 * sy) - crop_top,
                          None, None))

    # moving watermarks: blur the bounding box of the watermark's full
    # motion range for the whole clip. Time-segmented boxes can't reliably
    # track fast/bouncing motion with sparse samples (aliasing), so we
    # trade a larger blur area for guaranteed coverage.
    pad = 6
    for wm in moving:
        boxes = wm["boxes"]
        if not boxes:
            continue
        bx0 = min(b[0] for b in boxes)
        by0 = min(b[1] for b in boxes)
        bx1 = max(b[2] for b in boxes)
        by1 = max(b[3] for b in boxes)
        # motion margin for inter-sample travel (modest - the core range
        # already covers the sampled positions)
        mx = (bx1 - bx0) * 0.10
        my = (by1 - by0) * 0.10
        x0 = max(0, int((bx0 - pad - mx) * sx))
        y0 = max(0, int((by0 - pad - my) * sy) - crop_top)
        x1 = min(sw_real, int((bx1 + pad + mx) * sx))
        y1 = min(sh_real - crop_top - crop_bottom,
                 int((by1 + pad + my) * sy) - crop_top)
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        blurs.append((x0, y0, x1, y1, None, None))

    # tiled watermarks: static positions, blur each tile for the whole clip
    pad = 6
    for tw in tiled:
        for (bx0, by0, bx1, by1) in tw["boxes"]:
            x0 = max(0, int((bx0 - pad) * sx))
            y0 = max(0, int((by0 - pad) * sy) - crop_top)
            x1 = min(sw_real, int((bx1 + pad) * sx))
            y1 = min(sh_real - crop_top - crop_bottom,
                     int((by1 + pad) * sy) - crop_top)
            if x1 - x0 < 8 or y1 - y0 < 8:
                continue
            blurs.append((x0, y0, x1, y1, None, None))
    return crop_top, crop_bottom, blurs


def _apply(path: str, out_path: str, crop_top: int, crop_bottom: int,
           blurs: List[Tuple], has_audio: bool) -> bool:
    """One ffmpeg pass: crop edge bands, then blur logo boxes.

    A blur spec (x0, y0, x1, y1, t0, t1) with t0/t1 None blurs the whole
    clip; otherwise the blur is enabled only between(t0, t1).
    """
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
    for (x0, y0, x1, y1, t0, t1) in blurs:
        w, h = x1 - x0, y1 - y0
        if w < 8 or h < 8:
            continue
        ovf = f"[s{n}b][bg{n}]overlay={x0}:{y0}"
        if t0 is not None and t1 is not None:
            ovf += f":enable='between(t,{t0:.2f},{t1:.2f})'"
        ovf += f"[c{n}]"
        vf_parts.append(
            f"{stream}split=2[s{n}a][s{n}b];"
            f"[s{n}a]crop={w}:{h}:{x0}:{y0},"
            f"{_BLUR_FILTER}[bg{n}];"
            f"{ovf}")
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
    """Crop/blur static corner logos and moving watermarks on risky clips.

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
        frames, times = _sample_frames(path, ff, dur, sw, sh)
        overlays, mean_gray = _detect(frames)
        # tiled before moving: a static tile grid can mimic movement when
        # the best NCC peak jumps between identical tiles
        tiled = _detect_tiled(frames, overlays)
        moving = _detect_moving(frames, times, overlays, tiled)
        if (not overlays and not moving and not tiled) or mean_gray is None:
            return path
        sx, sy = w / sw, h / sh
        crop_top, crop_bottom, blurs = _plan(h, w, overlays, moving, tiled,
                                             mean_gray, sx, sy, dur)
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
