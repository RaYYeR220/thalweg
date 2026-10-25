"""Flood-warning drafts. The station drafts; only a person can sign (see the review API)."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

import numpy as np

from thalweg.hydro.forecast import StageKalman, time_to_threshold
from thalweg.hydro.rating import RatingCurve
from thalweg.station import Station

P_DRAFT = 0.5
HORIZON_S = 3 * 3600


@dataclass
class WarningDraft:
    id: str
    station_id: str
    created: str
    expires: str
    level_now: float
    q_now: float | None
    threshold_q: float
    threshold_stage: float
    eta_s: float | None
    p_cross: float
    rate_m_per_h: float
    text: str
    basis: dict = field(default_factory=dict)
    state: str = "DRAFT"

    def to_dict(self) -> dict:
        return asdict(self)


def threshold_stage(station: Station, rating: RatingCurve | None) -> tuple[float | None, float | None]:
    """Warning threshold as (stage, discharge). A discharge threshold is mapped through the rating curve."""
    th = station.thresholds
    if th.warning_stage is not None:
        q = float(rating.q(th.warning_stage)) if rating else None
        return th.warning_stage, q
    if th.warning_q is not None and rating is not None:
        return float(rating.h(th.warning_q)), th.warning_q
    return None, th.warning_q


def assess_warning(station: Station, kf: StageKalman, rating: RatingCurve | None, now: datetime,
                   q_now: float | None, p_draft: float = P_DRAFT, horizon_s: float = HORIZON_S) -> WarningDraft | None:
    h_thr, q_thr = threshold_stage(station, rating)
    if h_thr is None or kf.x is None:
        return None
    eta = time_to_threshold(kf, h_thr, horizon_s)
    if eta.p_cross < p_draft or (eta.rate_m_per_h <= 0 and eta.h_now < h_thr):
        return None
    when = "now" if eta.eta_s == 0 else (f"in about {eta.eta_s / 3600:.1f} h" if eta.eta_s else
                                          f"within {horizon_s / 3600:.0f} h (p={eta.p_cross:.2f})")
    text = (f"{station.name}: river level {eta.h_now:.2f} m and rising {eta.rate_m_per_h * 100:.0f} cm/h. "
            f"Flow is forecast to reach the warning threshold of {q_thr:.0f} m³/s (stage {h_thr:.2f} m) {when}. "
            f"Threshold source: {station.thresholds.source}.")
    return WarningDraft(
        id="w-" + uuid.uuid4().hex[:10], station_id=station.id, created=now.isoformat(),
        expires=(now + timedelta(hours=6)).isoformat(), level_now=round(eta.h_now, 3),
        q_now=None if q_now is None else round(q_now, 2), threshold_q=round(float(q_thr), 2) if q_thr else None,
        threshold_stage=round(h_thr, 3), eta_s=eta.eta_s, p_cross=round(eta.p_cross, 3),
        rate_m_per_h=round(eta.rate_m_per_h, 4), text=text,
        basis={"rating": rating.to_dict() if rating else None, "horizon_s": horizon_s, "p_draft": p_draft,
               "kalman_state": np.round(kf.x, 8).tolist()})
