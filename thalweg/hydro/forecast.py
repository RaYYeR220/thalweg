"""Stage nowcast: a constant-velocity Kalman filter on the water level, and time to a threshold.

Constant velocity (not acceleration) on purpose: extrapolating a fitted acceleration for
hours makes the forecast variance explode, and every threshold becomes a coin flip.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import erf, sqrt

import numpy as np


class StageKalman:
    """State [h, dh/dt] in metres and seconds; irregular sampling (including night gaps) is handled exactly."""

    def __init__(self, q_accel: float = 4e-13, r_level: float = 0.01**2, max_gap_s: float = 4 * 3600):
        self.q = q_accel  # white-acceleration spectral density (m^2 / s^3)
        self.r = r_level
        self.max_gap_s = max_gap_s
        self.x: np.ndarray | None = None
        self.P: np.ndarray | None = None
        self.t: datetime | None = None

    def _F_Q(self, dt: float):
        F = np.array([[1.0, dt], [0.0, 1.0]])
        Q = self.q * np.array([[dt**3 / 3, dt**2 / 2], [dt**2 / 2, dt]])
        return F, Q

    def reset(self, t: datetime, h: float) -> None:
        self.x = np.array([h, 0.0])
        self.P = np.diag([self.r, (0.3 / 3600) ** 2])
        self.t = t

    def update(self, t: datetime, h: float) -> None:
        if self.x is None or (t - self.t).total_seconds() > self.max_gap_s:
            # After a long gap (e.g. night, no daylight footage) the old trend says nothing.
            self.reset(t, h)
            return
        dt = (t - self.t).total_seconds()
        if dt > 0:
            F, Q = self._F_Q(dt)
            self.x = F @ self.x
            self.P = F @ self.P @ F.T + Q
        Hm = np.array([[1.0, 0.0]])
        S = float((Hm @ self.P @ Hm.T)[0, 0]) + self.r
        K = (self.P @ Hm.T) / S
        self.x = self.x + K.ravel() * (h - self.x[0])
        self.P = (np.eye(2) - K @ Hm) @ self.P
        self.t = max(self.t, t)

    def forecast(self, horizon_s: float) -> tuple[float, float]:
        F, Q = self._F_Q(horizon_s)
        x = F @ self.x
        P = F @ self.P @ F.T + Q
        return float(x[0]), float(np.sqrt(max(P[0, 0], 0.0)))

    @property
    def rate_m_per_h(self) -> float:
        return float(self.x[1] * 3600) if self.x is not None else 0.0


@dataclass
class ThresholdETA:
    eta_s: float | None
    p_cross: float
    h_now: float
    rate_m_per_h: float


def time_to_threshold(kf: StageKalman, h_thr: float, horizon_s: float = 3 * 3600, step_s: float = 300) -> ThresholdETA:
    """First time the forecast mean reaches `h_thr`, and the probability that it is exceeded at the horizon."""
    eta = None
    h_now = float(kf.x[0])
    for t in np.arange(step_s, horizon_s + 1, step_s):
        m, _ = kf.forecast(float(t))
        if m >= h_thr:
            eta = float(t)
            break
    m, sd = kf.forecast(horizon_s)
    p = 0.5 * (1 + erf((m - h_thr) / (max(sd, 1e-6) * sqrt(2))))
    if h_now >= h_thr:
        eta, p = 0.0, 1.0
    return ThresholdETA(eta, float(p), h_now, kf.rate_m_per_h)
