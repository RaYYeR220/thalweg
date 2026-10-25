import cv2 as cv
import numpy as np
import pytest

from thalweg.station import GRP
from thalweg.vision.geometry import CameraModel, GeometryError, OrthoGrid, ortho_maps, orthorectify


def _camera():
    K = np.array([[1400.0, 0, 960], [0, 1400.0, 540], [0, 0, 1]])
    rvec = np.array([[1.9], [0.15], [-0.1]])
    tvec = np.array([[-5.0], [3.0], [14.0]])
    return K, rvec, tvec


def test_dlt_recovers_3d_camera():
    K, rvec, tvec = _camera()
    rng = np.random.default_rng(1)
    xyz = np.c_[rng.uniform(0, 10, 10), rng.uniform(0, 8, 10), rng.uniform(-1, 2, 10)]
    ij, _ = cv.projectPoints(xyz, rvec, tvec, K, np.zeros(5))
    ij = ij.reshape(-1, 2) + rng.normal(0, 0.5, (10, 2))
    grp = [GRP(*p, *q) for p, q in zip(xyz, ij)]
    cam = CameraModel.fit(grp, (1920, 1080))
    assert cam.kind == "3d"
    assert cam.reproj_rmse_px < 1.0
    plane = np.c_[rng.uniform(1, 9, 20), rng.uniform(1, 7, 20), np.full(20, 0.3)]
    true_ij, _ = cv.projectPoints(plane, rvec, tvec, K, np.zeros(5))
    assert np.abs(cam.project(plane) - true_ij.reshape(-1, 2)).max() < 3.0


def test_planar_vgc1_grps():
    grp = [GRP(0.0, 6.7, 0.0, 201, 648), GRP(0.0, 0.0, 0.0, 1452, 743), GRP(7.6, 0.0, 0.0, 1518, 117),
           GRP(7.6, 6.7, 0.0, 768, 155)]
    cam = CameraModel.fit(grp, (1920, 1080))
    assert cam.kind == "planar" and cam.reproj_rmse_px < 3.0


def test_planar_model_refuses_other_level():
    grp = [GRP(0, 0, 0, 100, 900), GRP(10, 0, 0, 1800, 900), GRP(10, 8, 0, 1500, 200), GRP(0, 8, 0, 300, 200)]
    cam = CameraModel.fit(grp, (1920, 1080))
    with pytest.raises(GeometryError) as e:
        ortho_maps(cam, OrthoGrid(0, 0, 10, 8, 0.05, 0.25))
    assert e.value.code == "planar_grp_level_mismatch"


def test_scaled_camera_keeps_squares_square():
    cam = CameraModel.scaled(0.02, (400, 300))
    img = np.zeros((300, 400), np.uint8)
    img[::20, :] = 255
    img[:, ::20] = 255
    grid = OrthoGrid(0, 0, 8.0, 6.0, 0.02, 1.7)  # any level is valid for nadir footage
    out = orthorectify([img], ortho_maps(cam, grid))[0]
    rows = np.flatnonzero(out[:, 50] > 100)
    cols = np.flatnonzero(out[50, :] > 100)
    assert abs(np.median(np.diff(rows)) - np.median(np.diff(cols))) <= 1
