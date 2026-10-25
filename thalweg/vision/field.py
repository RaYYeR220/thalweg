"""Surface velocity fields in station coordinates (metres, m/s) and their filters."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
from scipy.spatial import cKDTree


@dataclass
class VectorField:
    x: np.ndarray  # station X (m)
    y: np.ndarray  # station Y (m)
    u: np.ndarray  # dX/dt (m/s)
    v: np.ndarray  # dY/dt (m/s)
    score: np.ndarray  # method confidence in [0, 1]
    valid: np.ndarray  # bool
    method: str
    params: dict = field(default_factory=dict)
    rejected: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return int(self.x.size)

    @property
    def speed(self) -> np.ndarray:
        return np.hypot(self.u, self.v)

    @property
    def n_valid(self) -> int:
        return int(np.count_nonzero(self.valid))

    def summary(self) -> dict:
        s = self.speed[self.valid]
        sc = self.score[self.valid]
        return {
            "method": self.method,
            "vectors": len(self),
            "valid": self.n_valid,
            "valid_frac": round(self.n_valid / max(len(self), 1), 3),
            "speed_p50": round(float(np.median(s)), 3) if s.size else None,
            "speed_p90": round(float(np.percentile(s, 90)), 3) if s.size else None,
            "score_mean": round(float(sc.mean()), 3) if sc.size else None,
            "direction_deg": round(float(np.degrees(np.arctan2(np.median(self.v[self.valid]),
                                                                np.median(self.u[self.valid])))), 1) if s.size else None,
            "rejected": dict(self.rejected),
        }

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "params": self.params,
            "x": self.x.round(3).tolist(), "y": self.y.round(3).tolist(),
            "u": self.u.round(4).tolist(), "v": self.v.round(4).tolist(),
            "score": self.score.round(3).tolist(), "valid": self.valid.astype(int).tolist(),
        }


def filter_field(f: VectorField, score_min: float = 0.4, vmax: float | None = None,
                 nmt_eps: float = 0.1, nmt_thresh: float = 2.5, k: int = 8,
                 direction_deg: float | None = None, direction_tol_deg: float = 60.0) -> VectorField:
    """Score threshold, speed bound, direction gate and the normalised median test (Westerweel & Scarano 2005)."""
    valid = f.valid.copy()
    rejected = dict(f.rejected)

    def drop(mask, reason):
        n = int(np.count_nonzero(valid & mask))
        if n:
            rejected[reason] = rejected.get(reason, 0) + n
        valid[mask] = False

    drop(~np.isfinite(f.u) | ~np.isfinite(f.v), "nan")
    drop(f.score < score_min, "low_score")
    if vmax is not None:
        drop(f.speed > vmax, "too_fast")
    if direction_deg is not None:
        ang = np.degrees(np.arctan2(f.v, f.u))
        diff = np.abs((ang - direction_deg + 180.0) % 360.0 - 180.0)
        drop((diff > direction_tol_deg) & (f.speed > 0.05), "direction")

    idx = np.flatnonzero(valid)
    if idx.size > k + 1:
        pts = np.c_[f.x[idx], f.y[idx]]
        tree = cKDTree(pts)
        _, nn = tree.query(pts, k=k + 1)
        nn = nn[:, 1:]
        bad = np.zeros(idx.size, dtype=bool)
        for comp in (f.u[idx], f.v[idx]):
            neigh = comp[nn]
            med = np.median(neigh, axis=1)
            res = np.median(np.abs(neigh - med[:, None]), axis=1)
            bad |= np.abs(comp - med) / (res + nmt_eps) > nmt_thresh
        m = np.zeros_like(valid)
        m[idx[bad]] = True
        drop(m, "median_test")
    return replace(f, valid=valid, rejected=rejected)
