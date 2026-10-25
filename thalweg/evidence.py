"""Evidence images: what the measurement saw, drawn for a reviewer (and for the agent's vision model)."""

from __future__ import annotations

import cv2 as cv
import numpy as np

ACCENT = (255, 190, 40)  # BGR
OK = (120, 220, 120)
BAD = (60, 60, 230)


def _bgr(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        img = cv.normalize(img.astype(np.float32), None, 0, 255, cv.NORM_MINMAX).astype(np.uint8)
        return cv.cvtColor(img, cv.COLOR_GRAY2BGR)
    return img.copy()


def render_field(ortho0: np.ndarray, field, grid, transect=None, lines=None, max_px: int = 900,
                 arrow_s: float = 0.6) -> np.ndarray:
    img = _bgr(ortho0)
    if lines:
        for p0, p1 in lines:
            c, r = grid.colrow(np.array([p0[0], p1[0]]), np.array([p0[1], p1[1]]))
            cv.line(img, (int(c[0]), int(r[0])), (int(c[1]), int(r[1])), (90, 90, 90), 1, cv.LINE_AA)
    if transect is not None:
        c, r = grid.colrow(transect[:, 0], transect[:, 1])
        pts = np.c_[c, r].astype(np.int32)
        cv.polylines(img, [pts], False, (255, 255, 255), 1, cv.LINE_AA)
    if len(field):
        speeds = field.speed[field.valid]
        vref = float(np.percentile(speeds, 90)) if speeds.size else 1.0
        cols, rows = grid.colrow(field.x, field.y)
        L = 18.0 if field.method != "stiv" else 30.0
        for c, r, u, v, ok in zip(cols, rows, field.u, field.v, field.valid):
            if not np.isfinite(u):
                continue
            dx, dy = u / max(vref, 1e-6) * L, -v / max(vref, 1e-6) * L
            cv.arrowedLine(img, (int(c), int(r)), (int(c + dx), int(r + dy)), ACCENT if ok else BAD, 1, cv.LINE_AA,
                           tipLength=0.3)
    s = min(1.0, max_px / max(img.shape[:2]))
    return cv.resize(img, None, fx=s, fy=s, interpolation=cv.INTER_AREA) if s < 1 else img


def render_overlay(frame: np.ndarray, cam, station, level: float, max_px: int = 1100) -> np.ndarray:
    img = _bgr(frame)
    for g in station.grp:
        cv.circle(img, (int(g.i), int(g.j)), 9, (0, 0, 255), 2, cv.LINE_AA)
    if cam is not None and len(station.grp):
        proj = cam.project(np.array([[g.X, g.Y, g.Z] for g in station.grp]))
        for p in proj:
            cv.drawMarker(img, (int(p[0]), int(p[1])), (0, 255, 255), cv.MARKER_CROSS, 14, 2)
    if cam is not None:
        tr = station.transect.copy()
        wet = tr[:, 2] < level
        tr[:, 2] = np.where(wet, level, tr[:, 2])
        pts = cam.project(tr).astype(np.int32)
        cv.polylines(img, [pts], False, ACCENT, 2, cv.LINE_AA)
    s = min(1.0, max_px / max(img.shape[:2]))
    return cv.resize(img, None, fx=s, fy=s, interpolation=cv.INTER_AREA) if s < 1 else img


def render_sti(sti: np.ndarray, speed_ms: float | None, dt_s: float, res_m: float) -> np.ndarray:
    img = _bgr(sti[: min(len(sti), 160)])
    img = cv.resize(img, None, fx=2, fy=2, interpolation=cv.INTER_NEAREST)
    if speed_ms is not None and np.isfinite(speed_ms) and speed_ms > 0:
        v_px = speed_ms * dt_s / res_m * 2  # per (doubled) row
        h, w = img.shape[:2]
        for x0 in range(0, w, 60):
            x1 = x0 + v_px * h / 2
            cv.line(img, (int(x0), 0), (int(x1), h), ACCENT, 1, cv.LINE_AA)
    return img
