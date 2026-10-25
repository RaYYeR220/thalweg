"""Stabilization on static scenery (banks, structures) and the camera-moved check."""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2 as cv
import numpy as np

from thalweg.vision.features import FeatureMatcher


@dataclass
class StabResult:
    frames: list[np.ndarray]
    H: list[np.ndarray]
    residual_px_p95: float  # p95 reprojection residual of the inliers, worst frame
    motion_px_p95: float  # p95 corner displacement removed, i.e. how much the camera moved
    inlier_ratio_min: float
    method: str
    keyframes: int
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"method": self.method, "residual_px_p95": round(self.residual_px_p95, 3),
                "motion_px_p95": round(self.motion_px_p95, 3), "inlier_ratio_min": round(self.inlier_ratio_min, 3),
                "keyframes": self.keyframes, "notes": self.notes}


def _corner_shift(H: np.ndarray, w: int, h: int) -> float:
    c = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    return float(np.max(np.linalg.norm(cv.perspectiveTransform(c, H) - c, axis=2)))


def _homography(a: np.ndarray, b: np.ndarray):
    if len(a) < 8:
        return None, 0.0, float("inf")
    H, inl = cv.findHomography(b, a, cv.USAC_MAGSAC, 1.5)
    if H is None:
        return None, 0.0, float("inf")
    m = inl.ravel().astype(bool)
    res = np.linalg.norm(cv.perspectiveTransform(b[m].reshape(-1, 1, 2), H).reshape(-1, 2) - a[m], axis=1)
    return H, float(m.mean()), float(np.percentile(res, 95)) if res.size else float("inf")


def stabilize(frames: list[np.ndarray], static_mask: np.ndarray | None = None, matcher: FeatureMatcher | None = None,
              keyframe_every: int = 5, min_motion_px: float = 0.3, max_motion_px: float = 80.0,
              min_inlier_ratio: float = 0.3) -> StabResult:
    """Register every frame to the first one on static scenery.

    Homographies are estimated on keyframes (USAC MAGSAC) and interpolated in between;
    if the measured camera motion is below `min_motion_px` the frames are left untouched.
    """
    matcher = matcher or FeatureMatcher()
    ref = frames[0]
    h, w = ref.shape[:2]
    cache = matcher.detect(ref, static_mask)
    keys = list(range(0, len(frames), keyframe_every))
    if keys[-1] != len(frames) - 1:
        keys.append(len(frames) - 1)
    Hk, inl, res = {0: np.eye(3)}, [1.0], [0.0]
    notes = []
    if matcher.fallback_reason:
        notes.append(matcher.fallback_reason)
    for k in keys[1:]:
        a, b = matcher.match(ref, frames[k], static_mask, static_mask, cached_a=cache)
        H, r, rp = _homography(a, b)
        if H is None:
            notes.append(f"frame_{k}_unmatched")
            H, r, rp = Hk[max(x for x in Hk)], 0.0, float("inf")
        Hk[k] = H
        inl.append(r)
        res.append(rp)
    motion = max(_corner_shift(Hk[k], w, h) for k in Hk)
    if motion > max_motion_px or min(inl) < min_inlier_ratio:
        # Implausible camera motion or weak support: the matches were probably on moving water.
        # Never warp the clip with a wrong model. Measure the real camera motion on the static
        # scenery with phase correlation instead; a still camera needs no correction.
        pc = _phase_motion(frames, static_mask, keys)
        if pc < 1.5:
            return StabResult(frames, [np.eye(3)] * len(frames), pc, pc, float(min(inl)), matcher.method, len(keys),
                              notes + ["feature_model_rejected", "static_camera"])
        return StabResult(frames, [np.eye(3)] * len(frames), float("inf"), max(motion, pc), float(min(inl)),
                          matcher.method, len(keys), notes + ["stabilization_rejected"])
    if motion < min_motion_px and all(np.isfinite(res)):
        return StabResult(frames, [np.eye(3)] * len(frames), float(max(res)), motion, float(min(inl)),
                          matcher.method, len(keys), notes + ["static_camera"])
    out, Hs = [], []
    for i, f in enumerate(frames):
        k0 = max(k for k in keys if k <= i)
        k1 = min(k for k in keys if k >= i)
        t = 0.0 if k1 == k0 else (i - k0) / (k1 - k0)
        H = (1 - t) * Hk[k0] + t * Hk[k1]
        H = H / H[2, 2]
        Hs.append(H)
        out.append(cv.warpPerspective(f, H, (w, h), flags=cv.INTER_LINEAR, borderMode=cv.BORDER_REPLICATE))
    finite = [x for x in res if np.isfinite(x)]
    return StabResult(out, Hs, float(max(finite)) if finite else float("inf"), motion, float(min(inl)),
                      matcher.method, len(keys), notes)


def _phase_motion(frames: list[np.ndarray], static_mask: np.ndarray | None, keys: list[int]) -> float:
    """Largest translation of the static scenery relative to frame 0, by phase correlation."""
    if static_mask is None or not static_mask.any():
        return float("inf")
    ys, xs = np.nonzero(static_mask)
    best = 0.0
    win = None
    # Use the largest static band; phase correlation needs a rectangle.
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    ref = frames[0][y0:y1, x0:x1].astype(np.float32) * static_mask[y0:y1, x0:x1]
    win = cv.createHanningWindow(ref.shape[::-1], cv.CV_32F)
    for k in keys[1:]:
        cur = frames[k][y0:y1, x0:x1].astype(np.float32) * static_mask[y0:y1, x0:x1]
        (dx, dy), _ = cv.phaseCorrelate(ref, cur, win)
        best = max(best, float(np.hypot(dx, dy)))
    return best


@dataclass
class PoseCheck:
    moved: bool
    shift_px_p95: float
    inliers: int
    H: np.ndarray | None
    method: str

    def summary(self) -> dict:
        return {"moved": self.moved, "shift_px_p95": round(self.shift_px_p95, 2), "inliers": self.inliers,
                "method": self.method}


def check_camera_pose(ref_img: np.ndarray, img: np.ndarray, static_mask: np.ndarray | None = None,
                      matcher: FeatureMatcher | None = None, tol_px: float = 3.0) -> PoseCheck:
    """Compare a clip against the station's reference frame: has the camera been knocked or re-aimed?"""
    matcher = matcher or FeatureMatcher()
    a, b = matcher.match(ref_img, img, static_mask, static_mask)
    H, r, _ = _homography(a, b)
    if H is None:
        return PoseCheck(True, float("inf"), 0, None, matcher.method)
    h, w = ref_img.shape[:2]
    shift = _corner_shift(np.linalg.inv(H), w, h)
    return PoseCheck(shift > tol_px, shift, int(r * len(a)), H, matcher.method)
