"""Image-quality diagnostics on the orthorectified water surface.

These numbers are what an experienced hydrologist checks by eye before trusting a
velocimetry result: are there enough moving tracers, is the riverbed showing
through clear water, is there glare, is it too dark, is the image sharp.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2 as cv
import numpy as np

STILL_WATER_ENERGY = 1.5  # temporal high-pass std (grey levels) below which nothing is moving
BED_VISIBLE_RATIO = 1.2  # static texture std / moving texture std above which correlation is pinned to zero


@dataclass
class Diagnostics:
    moving_energy: float  # std of the temporal high-pass signal on water (grey levels)
    static_texture: float  # spatial std of the high-pass-filtered temporal mean (grey levels)
    static_ratio: float  # static_texture / moving_energy
    tracer_density: float  # moving features per m^2
    glare_frac: float  # share of water pixels near saturation
    dark_frac: float  # share of water pixels below 25 grey levels
    blur_var: float  # variance of the Laplacian of one frame
    contrast: float  # p95 - p5 grey levels on water
    water_px: int
    frames: int

    def flags(self) -> list[str]:
        f = []
        if self.moving_energy < STILL_WATER_ENERGY:
            f.append("no_moving_tracers")
        if self.static_ratio > BED_VISIBLE_RATIO:
            f.append("bed_visible")
        if self.glare_frac > 0.25:
            f.append("glare")
        if self.dark_frac > 0.5:
            f.append("too_dark")
        if self.blur_var < 15:
            f.append("blurred")
        if self.contrast < 12:
            f.append("low_contrast")
        return f

    def to_dict(self) -> dict:
        d = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}
        d["flags"] = self.flags()
        return d


def _highpass_spatial(img: np.ndarray, sigma: float = 6.0) -> np.ndarray:
    return img - cv.GaussianBlur(img, (0, 0), sigma)


def diagnose(ortho: list[np.ndarray], water_mask: np.ndarray, res_m: float, k: int = 3) -> Diagnostics:
    stack = np.stack([f.astype(np.float32) for f in ortho])
    n = len(stack)
    m = water_mask.astype(bool)
    if m.sum() < 100 or n < 2 * k + 2:
        return Diagnostics(0, 0, 0, 0, 0, 0, 0, 0, int(m.sum()), n)
    csum = np.cumsum(np.concatenate([np.zeros_like(stack[:1]), stack]), axis=0)
    mids = range(k, n - k, max((n - 2 * k) // 24, 1))
    hp_std, peaks = [], []
    for i in mids:
        hp = stack[i] - (csum[i + k + 1] - csum[i - k]) / (2 * k + 1)
        hp_std.append(float(hp[m].std()))
        thr = max(3.0 * hp_std[-1], 6.0)
        blobs = (np.abs(hp) > thr).astype(np.uint8) * m.astype(np.uint8)
        peaks.append(cv.connectedComponents(blobs)[0] - 1)
    moving = float(np.median(hp_std))
    mean_img = stack.mean(axis=0)
    static = float(_highpass_spatial(mean_img)[m].std())
    area_m2 = m.sum() * res_m * res_m
    mid = stack[n // 2]
    water = mid[m]
    lap = cv.Laplacian(mid, cv.CV_32F)
    return Diagnostics(
        moving_energy=moving,
        static_texture=static,
        static_ratio=static / max(moving, 1e-3),
        tracer_density=float(np.median(peaks)) / max(area_m2, 1e-6),
        glare_frac=float((water >= 245).mean()),
        dark_frac=float((water < 25).mean()),
        blur_var=float(lap[m].var()),
        contrast=float(np.percentile(water, 95) - np.percentile(water, 5)),
        water_px=int(m.sum()),
        frames=n,
    )


def polygon_mask(shape: tuple[int, int], poly_px: list[tuple[float, float]]) -> np.ndarray:
    m = np.zeros(shape, np.uint8)
    cv.fillPoly(m, [np.asarray(poly_px, np.int32)], 1)
    return m


def water_mask_from_transect(grid, transect: np.ndarray, level: float, half_length_m: float,
                             flow_deg: float, valid: np.ndarray | None = None) -> np.ndarray:
    """Ortho-pixel mask of a band of water upstream and downstream of the wetted transect."""
    P = np.asarray(transect, dtype=np.float64)
    wet = P[P[:, 2] < level]
    if len(wet) < 2:
        return np.zeros(grid.shape, np.uint8)
    a, b = wet[0, :2], wet[-1, :2]
    d = np.array([np.cos(np.radians(flow_deg)), np.sin(np.radians(flow_deg))]) * half_length_m
    corners = [a - d, b - d, b + d, a + d]
    cols, rows = grid.colrow(np.array([c[0] for c in corners]), np.array([c[1] for c in corners]))
    m = polygon_mask(grid.shape, list(zip(cols, rows)))
    if valid is not None:
        m &= valid.astype(np.uint8)
    return m
