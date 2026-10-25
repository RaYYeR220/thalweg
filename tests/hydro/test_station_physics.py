from datetime import datetime, timedelta

import numpy as np
import pytest

from thalweg.hydro.forecast import StageKalman, time_to_threshold
from thalweg.hydro.rating import RatingCurve
from thalweg.hydro.warning import assess_warning
from thalweg.station import Station, Thresholds


def test_rating_fit_recovers_power_law():
    h = np.linspace(0.4, 2.5, 25)
    q = 12 * (h - 0.2) ** 1.6 * (1 + np.random.default_rng(0).normal(0, 0.02, h.size))
    rc = RatingCurve.fit(h, q)
    assert rc.b == pytest.approx(1.6, rel=0.1)
    assert float(rc.q(2.0)) == pytest.approx(12 * 1.8**1.6, rel=0.05)
    assert float(rc.h(rc.q(1.3))) == pytest.approx(1.3, abs=1e-6)
    assert rc.residual_z(2.0, 12 * 1.8**1.6 * 1.3) > 3


def _rising(kf, hours=3.0, rate=0.3, start=1.0):
    t0 = datetime(2018, 10, 12, 6)
    for k in range(int(hours * 4) + 1):
        kf.update(t0 + timedelta(minutes=15 * k), start + rate * k / 4)
    return t0 + timedelta(hours=hours)


def test_eta_of_linear_rise():
    kf = StageKalman()
    _rising(kf)  # now at 1.9 m, rising 0.3 m/h
    eta = time_to_threshold(kf, 2.5)
    assert eta.eta_s == pytest.approx(2 * 3600, abs=15 * 60)
    assert eta.rate_m_per_h == pytest.approx(0.3, rel=0.1)


def test_falling_limb_drafts_nothing():
    kf = StageKalman()
    t0 = datetime(2018, 10, 12, 6)
    for k in range(13):
        kf.update(t0 + timedelta(minutes=15 * k), 2.4 - 0.2 * k / 4)
    st = Station(id="s", name="S", grp=[], transect=np.zeros((2, 3)), image_size=(1, 1),
                 thresholds=Thresholds(warning_stage=2.5, source="test"))
    assert assess_warning(st, kf, None, t0 + timedelta(hours=3), None) is None


def test_rising_limb_drafts_warning_through_rating():
    kf = StageKalman()
    now = _rising(kf)
    h = np.linspace(0.5, 3.0, 20)
    rc = RatingCurve.fit(h, 12 * (h - 0.2) ** 1.6)
    st = Station(id="s", name="Test reach", grp=[], transect=np.zeros((2, 3)), image_size=(1, 1),
                 thresholds=Thresholds(warning_q=float(rc.q(2.5)), source="test QMED"))
    w = assess_warning(st, kf, rc, now, q_now=float(rc.q(1.9)))
    assert w is not None and w.state == "DRAFT"
    assert w.threshold_stage == pytest.approx(2.5, abs=0.01)
    assert "test QMED" in w.text
