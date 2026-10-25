"""Dense optical flow (DIS) on orthorectified frames: a third velocimetry method and a quick motion probe."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2 as cv
import numpy as np

from thalweg.vision.field import VectorField
from thalweg.vision.geometry import OrthoGrid
from thalweg.vision.lspiv import remove_background


def _u8(img: np.ndarray) -> np.ndarray:
    if img.dtype == np.uint8:
        return img
    return np.clip(img * 3.0 + 128.0, 0, 255).astype(np.uint8)


@dataclass
class CoarseMotion:
    speed_p50_px: float  # px per frame
    speed_p90_px: float
    direction_deg: float  # station coordinates (atan2(dY, dX))
    coherence: float  # |mean vector| / mean |vector|, 1 = uniform direction
    speed_p50_ms: float
    speed_p90_ms: float

    def to_dict(self) -> dict:
        return {k: round(v, 4) for k, v in asdict(self).items()}


def coarse_motion(ortho: list[np.ndarray], dt_s: float, grid: OrthoGrid, mask: np.ndarray,
                  background: str = "highpass:3", pairs: int = 8) -> CoarseMotion:
    frames = [_u8(f) for f in remove_background(ortho, background)]
    dis = cv.DISOpticalFlow_create(cv.DISOPTICAL_FLOW_PRESET_MEDIUM)
    m = mask.astype(bool)
    idx = np.linspace(0, len(frames) - 2, min(pairs, len(frames) - 1)).astype(int)
    us, vs = [], []
    for i in idx:
        fl = dis.calc(frames[i], frames[i + 1], None)
        us.append(fl[..., 0][m])
        vs.append(fl[..., 1][m])
    u = np.concatenate(us)
    v = np.concatenate(vs)
    # Ortho rows run toward -Y.
    dX, dY = u, -v
    sp = np.hypot(dX, dY)
    p50, p90 = float(np.median(sp)), float(np.percentile(sp, 90))
    mx, my = float(np.median(dX)), float(np.median(dY))
    coh = float(np.hypot(dX.mean(), dY.mean()) / max(sp.mean(), 1e-6))
    scale = grid.res_m / dt_s
    return CoarseMotion(p50, p90, float(np.degrees(np.arctan2(my, mx))), coh, p50 * scale, p90 * scale)


def suggest_params(cm: CoarseMotion, res_m: float, dt_s: float) -> dict:
    """Window sizes, pair step and search radius from the measured motion (the parameter assistant)."""
    p90 = max(cm.speed_p90_px, 0.3)
    pair_step = int(np.clip(round(4.0 / p90), 1, 6))  # aim for ~4 px displacement
    disp = p90 * pair_step
    ia = int(np.clip(4 * disp, 21, 64)) | 1
    search = int(np.clip(np.ceil(1.6 * disp) + 3, 5, 30))
    shift = (disp * 0.7 * np.cos(np.radians(cm.direction_deg)), -disp * 0.7 * np.sin(np.radians(cm.direction_deg)))
    vmax = float(np.clip(3.0 * cm.speed_p90_ms, 0.5, 8.0))
    return {"pair_step": pair_step, "ia_px": ia, "search_px": search, "shift_px": [round(shift[0], 1), round(shift[1], 1)],
            "step_px": max(ia // 2, 8), "vmax_ms": round(vmax, 2), "flow_deg": round(cm.direction_deg, 1),
            "stiv_window": 16, "stiv_line_m": None}


def dense_flow(ortho: list[np.ndarray], dt_s: float, grid: OrthoGrid, mask: np.ndarray, step_px: int = 12,
               pair_step: int = 1, background: str = "highpass:3", max_pairs: int = 60) -> VectorField:
    frames = [_u8(f) for f in remove_background(ortho, background)]
    dis = cv.DISOpticalFlow_create(cv.DISOPTICAL_FLOW_PRESET_MEDIUM)
    idx = np.linspace(0, len(frames) - 1 - pair_step, min(max_pairs, len(frames) - pair_step)).astype(int)
    stack_u, stack_v = [], []
    for i in idx:
        fl = dis.calc(frames[i], frames[i + pair_step], None)
        stack_u.append(fl[..., 0])
        stack_v.append(fl[..., 1])
    U = np.median(np.stack(stack_u), axis=0)
    V = np.median(np.stack(stack_v), axis=0)
    spread = np.std(np.stack(stack_u), axis=0) + np.std(np.stack(stack_v), axis=0)
    h, w = mask.shape
    rows, cols = np.mgrid[step_px // 2: h: step_px, step_px // 2: w: step_px]
    keep = mask[rows, cols].astype(bool)
    rows, cols = rows[keep], cols[keep]
    X, Y = grid.xy(cols, rows)
    t = dt_s * pair_step
    u = U[rows, cols] * grid.res_m / t
    v = -V[rows, cols] * grid.res_m / t
    mag = np.hypot(U[rows, cols], V[rows, cols])
    score = np.clip(1.0 - spread[rows, cols] / (mag + 1.0) / 2.0, 0.0, 1.0)
    return VectorField(x=X, y=Y, u=u, v=v, score=score, valid=np.ones(u.size, bool), method="dense_flow",
                       params=dict(step_px=step_px, pair_step=pair_step, background=background, pairs=len(idx),
                                   dt_s=dt_s, res_m=grid.res_m))
