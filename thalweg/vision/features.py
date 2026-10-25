"""Feature matching for stabilization: ALIKED + LightGlue (OpenCV 5), with ORB as a fallback."""

from __future__ import annotations

from pathlib import Path

import cv2 as cv
import numpy as np

from thalweg.paths import models_dir

ALIKED_MODEL = "aliked-n16rot-top1k-640.onnx"
LIGHTGLUE_MODEL = "aliked_lightglue.onnx"


class FeatureMatcher:
    def __init__(self, kind: str = "aliked", score_threshold: float = 0.2, match_width: int = 960):
        self.kind = kind
        self.match_width = match_width
        self.fallback_reason: str | None = None
        self.score_threshold = score_threshold
        if kind == "aliked":
            a, lg = models_dir() / ALIKED_MODEL, models_dir() / LIGHTGLUE_MODEL
            if not (Path(a).exists() and Path(lg).exists()):
                self.kind, self.fallback_reason = "orb", "aliked_models_missing"
            else:
                try:
                    self._aliked = cv.ALIKED_create(str(a))
                    self._lg = cv.LightGlueMatcher_create(str(lg))
                except cv.error as e:  # pragma: no cover - depends on the runtime build
                    self.kind, self.fallback_reason = "orb", f"aliked_init_failed: {e.msg[:80]}"
        if self.kind == "orb":
            self._orb = cv.ORB_create(3000)
            self._bf = cv.BFMatcher(cv.NORM_HAMMING, crossCheck=True)

    @property
    def method(self) -> str:
        return "aliked+lightglue" if self.kind == "aliked" else "orb"

    def detect(self, img: np.ndarray, mask: np.ndarray | None = None):
        scale = min(1.0, self.match_width / img.shape[1])
        small = cv.resize(img, None, fx=scale, fy=scale, interpolation=cv.INTER_AREA) if scale < 1.0 else img
        if self.kind == "aliked":
            img3 = small if small.ndim == 3 else cv.cvtColor(small, cv.COLOR_GRAY2BGR)
            kps, desc = self._aliked.detectAndCompute(img3, None)
        else:
            kps, desc = self._orb.detectAndCompute(small, None)
        if scale < 1.0:
            kps = [cv.KeyPoint(k.pt[0] / scale, k.pt[1] / scale, k.size / scale, k.angle, k.response, k.octave, k.class_id)
                   for k in kps]
        if mask is not None and kps:
            keep = [i for i, k in enumerate(kps) if mask[int(min(k.pt[1], mask.shape[0] - 1)), int(min(k.pt[0], mask.shape[1] - 1))]]
            kps = [kps[i] for i in keep]
            desc = desc[keep] if desc is not None and len(keep) else None
        return kps, desc

    def match(self, img_a: np.ndarray, img_b: np.ndarray, mask_a: np.ndarray | None = None,
              mask_b: np.ndarray | None = None, cached_a=None) -> tuple[np.ndarray, np.ndarray]:
        ka, da = cached_a if cached_a is not None else self.detect(img_a, mask_a)
        kb, db = self.detect(img_b, mask_b)
        if da is None or db is None or len(ka) < 4 or len(kb) < 4:
            return np.empty((0, 2), np.float32), np.empty((0, 2), np.float32)
        if self.kind == "aliked":
            pa = np.float32([k.pt for k in ka])
            pb = np.float32([k.pt for k in kb])
            # LightGlue reasons about keypoint layout; give it the coordinates at the detection scale.
            sa = min(1.0, self.match_width / img_a.shape[1])
            sb = min(1.0, self.match_width / img_b.shape[1])
            self._lg.setPairInfo(pa * sa, pb * sb, (int(img_a.shape[1] * sa), int(img_a.shape[0] * sa)),
                                 (int(img_b.shape[1] * sb), int(img_b.shape[0] * sb)))
            ms = [m for m in self._lg.match(da, db) if m.distance < 1.0 - self.score_threshold]
        else:
            ms = self._bf.match(da, db)
        if not ms:
            return np.empty((0, 2), np.float32), np.empty((0, 2), np.float32)
        a = np.float32([ka[m.queryIdx].pt for m in ms])
        b = np.float32([kb[m.trainIdx].pt for m in ms])
        return a, b
