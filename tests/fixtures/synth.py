"""Synthetic river surfaces with exact velocity: advected short-lived features over static texture."""

from __future__ import annotations

import cv2 as cv
import numpy as np

from thalweg.vision.geometry import OrthoGrid


def advected_frames(shape=(200, 400), n=120, u_px=3.0, v_px=0.0, lifetime=12, density=0.02,
                    static_amp=0.0, noise=2.0, seed=0, shear=None) -> list[np.ndarray]:
    """Gaussian blobs advected at (u_px, v_px) per frame; each blob lives `lifetime` frames.

    `shear(row) -> factor` scales the speed by row to build a velocity profile.
    `static_amp` adds a fixed texture (a riverbed seen through clear water).
    """
    rng = np.random.default_rng(seed)
    h, w = shape
    n_blobs = int(density * h * w)
    pos = np.c_[rng.uniform(0, w, n_blobs), rng.uniform(0, h, n_blobs)]
    age = rng.integers(0, lifetime, n_blobs)
    amp = rng.uniform(40, 120, n_blobs)
    static = cv.GaussianBlur(rng.normal(0, 1, shape).astype(np.float32), (0, 0), 2.0)
    static = static / (static.std() + 1e-6) * static_amp
    frames = []
    for _ in range(n):
        img = np.full(shape, 100.0, np.float32) + static
        layer = np.zeros(shape, np.float32)
        for (x, y), a in zip(pos, amp):
            xi, yi = int(x), int(y)
            if 0 <= xi < w and 0 <= yi < h:
                layer[yi, xi] += a
        layer = cv.GaussianBlur(layer, (0, 0), 1.5)
        img += layer + rng.normal(0, noise, shape).astype(np.float32)
        frames.append(np.clip(img, 0, 255).astype(np.uint8))
        f = shear(pos[:, 1]) if shear else 1.0
        pos[:, 0] += u_px * f
        pos[:, 1] += v_px
        age += 1
        dead = (age >= lifetime) | (pos[:, 0] < 0) | (pos[:, 0] >= w) | (pos[:, 1] < 0) | (pos[:, 1] >= h)
        k = int(dead.sum())
        pos[dead] = np.c_[rng.uniform(0, w, k), rng.uniform(0, h, k)]
        age[dead] = 0
    return frames


def grid_for(shape, res_m=0.01, level=0.0) -> OrthoGrid:
    h, w = shape
    return OrthoGrid(0.0, 0.0, w * res_m, h * res_m, res_m, level)
