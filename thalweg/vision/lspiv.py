"""Large-scale particle image velocimetry on orthorectified frames.

Ensemble correlation: for each interrogation area the normalised cross-correlation
maps of all frame pairs are averaged before the peak is located, which is far more
robust on river surfaces than per-pair peaks (Meinhart et al. 2000).
"""

from __future__ import annotations

import cv2 as cv
import numpy as np

from thalweg.vision.field import VectorField
from thalweg.vision.geometry import OrthoGrid


def remove_background(frames: list[np.ndarray], mode: str = "mean") -> list[np.ndarray]:
    """Suppress static or slowly changing texture (riverbed seen through clear water, banks, shadows).

    "mean" / "median": subtract one temporal background.
    "highpass:<k>": subtract a centred moving average over 2k+1 frames, which also removes slow
    illumination changes and keeps only features that move within a fraction of a second.
    Memory stays at one float32 copy of the clip plus a running sum.
    """
    n = len(frames)
    if mode.startswith("highpass"):
        k = int(mode.split(":")[1]) if ":" in mode else 5
        f32 = [f.astype(np.float32) for f in frames]
        acc = np.zeros_like(f32[0])
        lo, hi = 0, -1  # current window is frames[lo..hi]
        out = []
        for i in range(n):
            a, b = max(0, i - k), min(n - 1, i + k)
            while hi < b:
                hi += 1
                acc += f32[hi]
            while lo < a:
                acc -= f32[lo]
                lo += 1
            out.append(f32[i] - acc / (hi - lo + 1))
        return out
    if mode == "median":
        bg = np.median(np.stack(frames), axis=0).astype(np.float32)
    else:
        bg = np.zeros(frames[0].shape, np.float64)
        for f in frames:
            bg += f
        bg = (bg / n).astype(np.float32)
    return [f.astype(np.float32) - bg for f in frames]


def _subpixel(c: np.ndarray, r: int, k: int) -> tuple[float, float]:
    """Three-point Gaussian peak fit on a correlation map (row r, col k)."""
    def g(a, b, d):
        a, b, d = (max(x, 1e-6) for x in (a, b, d))
        la, lb, ld = np.log(a), np.log(b), np.log(d)
        den = 2 * (la - 2 * lb + ld)
        return 0.0 if den == 0 else (la - ld) / den

    dy = g(c[r - 1, k], c[r, k], c[r + 1, k]) if 0 < r < c.shape[0] - 1 else 0.0
    dx = g(c[r, k - 1], c[r, k], c[r, k + 1]) if 0 < k < c.shape[1] - 1 else 0.0
    return float(np.clip(dx, -1, 1)), float(np.clip(dy, -1, 1))


def lspiv(ortho: list[np.ndarray], dt_s: float, grid: OrthoGrid, ia_px: int = 32, search_px: int = 12,
          step_px: int | None = None, mask: np.ndarray | None = None, pair_step: int = 1,
          shift_px: tuple[float, float] = (0.0, 0.0), background: str | None = "mean",
          max_pairs: int = 200) -> VectorField:
    """Velocity field from ensemble-averaged NCC between frames `pair_step` apart.

    `search_px` is the search radius around the expected shift `shift_px` (col, row) in ortho pixels.
    Velocities come back in station coordinates (m/s); ortho rows run toward -Y.
    """
    if len(ortho) < pair_step + 1:
        raise ValueError("not enough frames for one pair")
    ia = int(ia_px) | 1  # odd so the window has a centre pixel
    half = ia // 2
    r = int(search_px)
    step = int(step_px or max(ia // 2, 4))
    frames = remove_background(ortho, background) if background else [f.astype(np.float32) for f in ortho]
    h, w = frames[0].shape[:2]
    sx, sy = int(round(shift_px[0])), int(round(shift_px[1]))
    pairs = [(k, k + pair_step) for k in range(0, len(frames) - pair_step)]
    if len(pairs) > max_pairs:
        pick = np.linspace(0, len(pairs) - 1, max_pairs).astype(int)
        pairs = [pairs[i] for i in pick]
    margin = half + r + max(abs(sx), abs(sy)) + 1
    rows = np.arange(margin, h - margin, step)
    cols = np.arange(margin, w - margin, step)
    xs, ys, us, vs, scores, valid = [], [], [], [], [], []
    texture_min = 1.0
    for row in rows:
        for col in cols:
            if mask is not None and not mask[row, col]:
                continue
            acc = None
            n = 0
            for a, b in pairs:
                tpl = frames[a][row - half: row + half + 1, col - half: col + half + 1]
                if float(tpl.std()) < texture_min:
                    continue
                r0, c0 = row + sy - half - r, col + sx - half - r
                srch = frames[b][r0: r0 + ia + 2 * r, c0: c0 + ia + 2 * r]
                cm = cv.matchTemplate(srch, tpl, cv.TM_CCOEFF_NORMED)
                acc = cm if acc is None else acc + cm
                n += 1
            if n == 0:
                continue
            cm = acc / n
            _, peak, _, loc = cv.minMaxLoc(cm)
            kx, ky = loc
            fx, fy = _subpixel(np.clip(cm, 1e-6, None), ky, kx)
            dcol = kx + fx - r + sx
            drow = ky + fy - r + sy
            on_edge = kx in (0, cm.shape[1] - 1) or ky in (0, cm.shape[0] - 1)
            X, Y = grid.xy(col, row)
            t = dt_s * pair_step
            xs.append(X); ys.append(Y)
            us.append(dcol * grid.res_m / t)
            vs.append(-drow * grid.res_m / t)
            scores.append(max(float(peak), 0.0))
            valid.append(not on_edge)
    f = VectorField(x=np.asarray(xs), y=np.asarray(ys), u=np.asarray(us), v=np.asarray(vs),
                    score=np.asarray(scores), valid=np.asarray(valid, dtype=bool), method="lspiv",
                    params=dict(ia_px=ia, search_px=r, step_px=step, pair_step=pair_step, dt_s=dt_s,
                                shift_px=[sx, sy], background=background, pairs=len(pairs), res_m=grid.res_m))
    if f.valid.size:
        f.rejected["search_edge"] = int(np.count_nonzero(~f.valid))
    return f
