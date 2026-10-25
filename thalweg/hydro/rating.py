"""Stage-discharge rating curve Q = a (h - h0)^b, fitted from accepted gaugings."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import curve_fit


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
        bounds = ([1e-6, 0.8, -np.inf], [np.inf, 3.5, float(h.min()) - 1e-3])
        sigma = None if q_sigma is None else np.asarray(q_sigma, float)
        popt, pcov = curve_fit(_power, h, q, p0=p0, sigma=sigma, bounds=bounds, maxfev=20000)
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
