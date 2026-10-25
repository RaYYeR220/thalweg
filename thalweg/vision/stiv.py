"""Space-time image velocimetry (STIV).

Intensity is sampled along short lines parallel to the flow. Surface features advect
along each line and draw slanted streaks in the space-time image; their slope is the
along-line velocity. The slope is found by shift-and-add: for a trial velocity v the
image is sheared so that features moving at v line up, and the variance of the
time-averaged profile peaks at the true v. Static texture (a riverbed seen through
clear water) is removed by a temporal high-pass first, so it cannot pull the answer
to zero.
"""

from __future__ import annotations

import cv2 as cv
import numpy as np

from thalweg.vision.field import VectorField
from thalweg.vision.geometry import OrthoGrid
from thalweg.vision.lspiv import remove_background


def space_time_image(frames: list[np.ndarray], grid: OrthoGrid, p0: tuple[float, float], p1: tuple[float, float]) -> np.ndarray:
    """Sample frames along the station-coordinate segment p0 -> p1 at the grid resolution. Returns (T, S)."""
    length = float(np.hypot(p1[0] - p0[0], p1[1] - p0[1]))
    n = max(int(length / grid.res_m), 8)
    t = np.linspace(0.0, 1.0, n)
    X = p0[0] + t * (p1[0] - p0[0])
    Y = p0[1] + t * (p1[1] - p0[1])
    col, row = grid.colrow(X, Y)
    mx = col.astype(np.float32).reshape(1, -1)
    my = row.astype(np.float32).reshape(1, -1)
    return np.vstack([cv.remap(f, mx, my, cv.INTER_LINEAR) for f in frames]).astype(np.float32)


def _shear_score(sti: np.ndarray, v: float, window: int) -> float:
    """Mean variance of the time-averaged, sheared profile over short windows.

    Surface features live for a fraction of a second, so alignment is scored over
    `window` frames at a time and the windows are averaged.
    """
    T, S = sti.shape
    W = min(window, T)
    tt = np.arange(W, dtype=np.float32)[:, None]
    x = np.arange(S, dtype=np.float32)[None, :] + v * tt  # sample position in each row
    i0 = np.floor(x).astype(np.int64)
    frac = (x - i0).astype(np.float32)
    inside = (i0 >= 0) & (i0 + 1 < S)
    keep = inside.all(axis=0)
    if keep.sum() < 8:
        return 0.0
    i0 = np.clip(i0, 0, S - 2)[:, keep]
    frac = frac[:, keep]
    rows = np.arange(W)[:, None]
    total, n = 0.0, 0
    for t0 in range(0, T - W + 1, max(W // 2, 1)):
        blk = sti[t0:t0 + W]
        w = blk[rows, i0] * (1.0 - frac) + blk[rows, i0 + 1] * frac
        w = w - w.mean(axis=1, keepdims=True)
        # Normalise by the mean per-row variance: linear interpolation lowers the variance of
        # fractionally shifted rows, which would otherwise lock the answer to whole pixels.
        den = float((w**2).mean())
        if den > 0:
            total += float(w.mean(axis=0).var()) / den
            n += 1
    return total / n if n else 0.0


def sti_velocity(sti: np.ndarray, vmax_px: float, vmin_px: float = 0.15, window: int = 16
                 ) -> tuple[float, float, np.ndarray, np.ndarray]:
    """Best along-line velocity in px/frame, a prominence score in [0, 1], and the score curve."""
    sti = sti - sti.mean(axis=0, keepdims=True)
    sd = sti.std()
    if sd < 1e-6:
        return float("nan"), 0.0, np.array([]), np.array([])
    sti = (sti / sd).astype(np.float32)
    S = sti.shape[1]
    vmax_px = min(vmax_px, 0.6 * S / max(window - 1, 1))
    cand = np.arange(-vmax_px, vmax_px + 1e-9, 0.1)
    scores = np.array([_shear_score(sti, v, window) for v in cand])
    band = np.abs(cand) >= vmin_px
    if not band.any() or scores.max() <= 0:
        return float("nan"), 0.0, cand, scores
    i = int(np.flatnonzero(band)[np.argmax(scores[band])])
    fine = np.arange(cand[i] - 0.1, cand[i] + 0.1001, 0.01)
    fs = np.array([_shear_score(sti, v, window) for v in fine])
    v = float(fine[np.argmax(fs)])
    peak = float(fs.max())
    # Prominence of the aligned peak over the misaligned floor (1/W of the variance for pure noise).
    floor = float(np.percentile(scores, 20))
    prom = (peak - floor) / peak if peak > 0 else 0.0
    return v, float(np.clip(prom, 0.0, 1.0)), cand, scores


def stiv(ortho: list[np.ndarray], dt_s: float, grid: OrthoGrid, lines: list[tuple[tuple[float, float], tuple[float, float]]],
         vmax_ms: float = 4.0, background: str | None = "highpass:3", window: int = 16) -> VectorField:
    """Along-line velocities for each line; returned as vectors at the line centres in station coordinates."""
    frames = remove_background(ortho, background) if background else [f.astype(np.float32) for f in ortho]
    vmax_px = vmax_ms * dt_s / grid.res_m
    xs, ys, us, vs, scores, valid = [], [], [], [], [], []
    for p0, p1 in lines:
        sti = space_time_image(frames, grid, p0, p1)
        v_px, score, _, _ = sti_velocity(sti, vmax_px, window=window)
        d = np.array([p1[0] - p0[0], p1[1] - p0[1]], dtype=np.float64)
        d /= np.linalg.norm(d)
        speed = v_px * grid.res_m / dt_s if np.isfinite(v_px) else np.nan
        xs.append(0.5 * (p0[0] + p1[0])); ys.append(0.5 * (p0[1] + p1[1]))
        us.append(speed * d[0]); vs.append(speed * d[1])
        scores.append(score); valid.append(bool(np.isfinite(speed)))
    return VectorField(x=np.asarray(xs), y=np.asarray(ys), u=np.asarray(us), v=np.asarray(vs),
                       score=np.asarray(scores), valid=np.asarray(valid, dtype=bool), method="stiv",
                       params=dict(lines=len(lines), dt_s=dt_s, vmax_ms=vmax_ms, background=background,
                                   res_m=grid.res_m))


def lines_across_transect(transect: np.ndarray, level: float, flow_deg: float, n: int = 20,
                          length_m: float = 3.0, per_station: int = 3) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Search lines parallel to the flow, centred on the wetted stations of the transect.

    `per_station` parallel lines are spread across each station's panel so the discharge step
    can take their median, which suppresses single-line outliers.
    """
    P = np.asarray(transect, dtype=np.float64)
    z = P[:, 2]
    seg = np.linalg.norm(np.diff(P[:, :2], axis=0), axis=1)
    ch = np.r_[0.0, np.cumsum(seg)]
    wet = np.flatnonzero(z < level)
    if wet.size == 0:
        return []
    s0, s1 = ch[max(wet[0] - 1, 0)], ch[min(wet[-1] + 1, len(ch) - 1)]
    edges = np.linspace(s0, s1, n + 1)
    s_mid = 0.5 * (edges[:-1] + edges[1:])
    panel = (s1 - s0) / n
    offsets = np.linspace(-panel / 3, panel / 3, per_station) if per_station > 1 else np.zeros(1)
    d = np.array([np.cos(np.radians(flow_deg)), np.sin(np.radians(flow_deg))])
    half = 0.5 * length_m
    out = []
    for sm in s_mid:
        for o in offsets:
            x = float(np.interp(sm + o, ch, P[:, 0]))
            y = float(np.interp(sm + o, ch, P[:, 1]))
            out.append(((x - half * d[0], y - half * d[1]), (x + half * d[0], y + half * d[1])))
    return out
