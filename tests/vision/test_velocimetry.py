import numpy as np
import pytest

from tests.fixtures.synth import advected_frames, grid_for
from thalweg.vision.diagnose import BED_VISIBLE_RATIO, STILL_WATER_ENERGY, diagnose
from thalweg.vision.field import filter_field
from thalweg.vision.lspiv import lspiv
from thalweg.vision.stiv import stiv

DT, RES = 0.04, 0.01


@pytest.fixture(scope="module")
def flow():
    return advected_frames(u_px=-3.0, static_amp=25, lifetime=8, n=120, seed=2)


def test_lspiv_recovers_speed(flow):
    g = grid_for(flow[0].shape, RES)
    f = filter_field(lspiv(flow, DT, g, ia_px=31, search_px=8, step_px=16, shift_px=(-3, 0)), score_min=0.2)
    assert abs(np.median(-f.u[f.valid]) - 0.75) / 0.75 < 0.03


def test_stiv_recovers_speed_subpixel():
    fr = advected_frames(u_px=-2.3, static_amp=25, lifetime=8, n=150, seed=3)
    g = grid_for(fr[0].shape, RES)
    lines = [((3.4, y), (0.6, y)) for y in np.linspace(0.3, 1.7, 6)]
    s = stiv(fr, DT, g, lines, vmax_ms=3.0)
    assert abs(np.median(s.speed) - 0.575) / 0.575 < 0.05


def test_filter_removes_outliers(flow):
    g = grid_for(flow[0].shape, RES)
    f = lspiv(flow, DT, g, ia_px=31, search_px=8, step_px=16, shift_px=(-3, 0))
    rng = np.random.default_rng(0)
    bad = rng.choice(len(f), size=max(len(f) // 10, 1), replace=False)
    f.u[bad] = 5.0
    ff = filter_field(f, score_min=0.0)
    assert not ff.valid[bad].any()


def test_diagnose_flags_bed_and_still_water():
    still = advected_frames(u_px=0.0, static_amp=40, lifetime=10_000, n=40, density=0.0, noise=0.5)
    m = np.ones(still[0].shape, np.uint8)
    d = diagnose(still, m, RES)
    assert d.moving_energy < STILL_WATER_ENERGY
    assert "no_moving_tracers" in d.flags()
    bed = advected_frames(u_px=-3.0, static_amp=60, lifetime=8, n=40, density=0.004)
    assert diagnose(bed, m, RES).static_ratio > BED_VISIBLE_RATIO
