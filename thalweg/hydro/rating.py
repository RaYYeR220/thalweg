"""Stage-discharge rating curve Q = a (h - h0)^b, fitted from accepted gaugings."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares


def _power(h, a, b, h0):
    return a * np.clip(h - h0, 1e-6, None) ** b


@dataclass
class RatingCurve:
    a: float
    b: float
    h0: float
    cov: np.ndarray
    n: int
    rmse_rel: float

    @classmethod
    def fit(cls, h, q, q_sigma=None, h0_guess: float | None = None) -> "RatingCurve":
        h = np.asarray(h, float)
        q = np.asarray(q, float)
        if h.size < 3:
            raise ValueError("a rating curve needs at least three gaugings")
        h0g = h0_guess if h0_guess is not None else float(h.min() - 0.2 * np.ptp(h) - 0.05)
        p0 = (float(q.max() / max(h.max() - h0g, 1e-3) ** 1.6), 1.6, h0g)
        lo, hi = [1e-6, 0.8, -np.inf], [np.inf, 3.5, float(h.min()) - 1e-3]
        p0 = tuple(np.clip(p0, [l + 1e-9 if np.isfinite(l) else -1e9 for l in lo], [u - 1e-9 if np.isfinite(u) else 1e9 for u in hi]))
        w = np.ones_like(q) if q_sigma is None else 1.0 / np.maximum(np.asarray(q_sigma, float) / np.maximum(q, 1e-9), 0.02)
        keep = q > 0
        # Relative (log) residuals with a soft-L1 loss: one bad low-flow gauging must not bend the curve.
        res = least_squares(lambda p: w[keep] * (np.log(_power(h[keep], *p)) - np.log(q[keep])), p0, bounds=(lo, hi),
                            loss="soft_l1", f_scale=0.1, max_nfev=20000)
        popt = res.x
        J = res.jac
        try:
            pcov = np.linalg.pinv(J.T @ J) * float(np.mean(res.fun ** 2))
        except np.linalg.LinAlgError:
            pcov = np.full((3, 3), np.nan)
        pred = _power(h, *popt)
        rmse = float(np.sqrt(np.mean(((q - pred) / np.maximum(pred, 1e-9)) ** 2)))
        return cls(float(popt[0]), float(popt[1]), float(popt[2]), pcov, int(h.size), rmse)

    def q(self, h):
        return _power(np.asarray(h, float), self.a, self.b, self.h0)

    def h(self, q):
        return self.h0 + (np.asarray(q, float) / self.a) ** (1.0 / self.b)

    def residual_z(self, h: float, q: float) -> float:
        """How many relative-RMSE units a new gauging sits from the curve."""
        pred = float(self.q(h))
        return abs(q - pred) / max(pred * max(self.rmse_rel, 0.03), 1e-9)

    def to_dict(self) -> dict:
        return {"a": self.a, "b": self.b, "h0": self.h0, "n": self.n, "rmse_rel": round(self.rmse_rel, 4)}
