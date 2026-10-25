import numpy as np
import pytest

from thalweg.hydro.discharge import HydroError, compute_discharge
from thalweg.vision.field import VectorField


def _uniform_field(u=1.0, xs=(0, 4), ys=(0, 10), step=0.25):
    X, Y = np.meshgrid(np.arange(*xs, step), np.arange(*ys, step))
    n = X.size
    return VectorField(X.ravel(), Y.ravel(), np.full(n, u), np.zeros(n), np.ones(n), np.ones(n, bool), "test")


def test_rectangular_channel():
    tr = np.array([[2, 0.0, 0.5], [2, 0.01, -1.0], [2, 9.99, -1.0], [2, 10.0, 0.5]])
    r = compute_discharge(_uniform_field(), tr, level=0.0, alpha=0.85, search_radius_m=0.4)
    assert r.q == pytest.approx(8.5, rel=0.01)
    assert r.coverage == pytest.approx(1.0)


def test_trapezoid():
    tr = np.array([[2, 0, 0.5], [2, 2.5, -1.0], [2, 7.5, -1.0], [2, 10, 0.5]])
    r = compute_discharge(_uniform_field(), tr, level=0.0, alpha=1.0, search_radius_m=0.4, n_stations=40)
    wet_edge = 2.5 * 0.5 / 1.5  # waterline offset from each bank point
    area = 5 * 1.0 + 2 * 0.5 * (2.5 - wet_edge) * 1.0
    assert r.q == pytest.approx(area, rel=0.03)


def test_level_out_of_range():
    tr = np.array([[2, 0, 0.5], [2, 1, -1.0], [2, 9, -1.0], [2, 10, 0.5]])
    with pytest.raises(HydroError) as e:
        compute_discharge(_uniform_field(), tr, level=0.8, alpha=0.85)
    assert e.value.code == "level_out_of_range"
    with pytest.raises(HydroError):
        compute_discharge(_uniform_field(), tr, level=-1.5, alpha=0.85)


def test_gaps_lower_coverage():
    tr = np.array([[2, 0.0, 0.5], [2, 0.01, -1.0], [2, 9.99, -1.0], [2, 10.0, 0.5]])
    f = _uniform_field(ys=(0, 4))
    r = compute_discharge(f, tr, level=0.0, alpha=0.85, search_radius_m=0.4)
    assert r.coverage < 0.5
