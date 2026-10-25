"""Camera models from ground reference points and orthorectification at the water level.

Two cases, as in operational video gauging:

* planar GRPs (all at one elevation): a homography maps the plane to the image.
  It is only valid on that plane, so gauging at another water level needs the
  camera intrinsics.
* 3D GRPs: a full 3x4 projection is estimated (DLT) and refined with solvePnP,
  so any horizontal plane - the live water surface - can be projected.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2 as cv
import numpy as np

from thalweg.station import GRP


class GeometryError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


PLANE_TOL_M = 0.01


@dataclass
class CameraModel:
    kind: str  # "planar" | "3d"
    image_size: tuple[int, int]  # (width, height)
    H: np.ndarray | None = None  # plane (X, Y) -> pixel, planar case
    plane_z: float | None = None
    K: np.ndarray | None = None
    dist: np.ndarray | None = None
    rvec: np.ndarray | None = None
    tvec: np.ndarray | None = None
    reproj_rmse_px: float = float("nan")
    reproj_max_px: float = float("nan")
    any_level: bool = False  # nadir footage scaled at the water surface: valid at whatever level is gauged

    @classmethod
    def scaled(cls, m_per_px: float, image_size: tuple[int, int], origin_px: tuple[float, float] | None = None,
               y_up: bool = True) -> "CameraModel":
        """Nadir (drone) footage with a known pixel scale at the water surface.

        World X = (i - i0) * s, Y = (j0 - j) * s when `y_up` (image rows grow downward). The default
        origin is the bottom-left image corner for `y_up`, the top-left otherwise.
        """
        s = float(m_per_px)
        i0, j0 = origin_px if origin_px is not None else (0.0, float(image_size[1]) if y_up else 0.0)
        sy = -1.0 / s if y_up else 1.0 / s
        H = np.array([[1.0 / s, 0.0, i0], [0.0, sy, j0], [0.0, 0.0, 1.0]])
        return cls(kind="planar", image_size=tuple(image_size), H=H, plane_z=0.0, reproj_rmse_px=0.0,
                   reproj_max_px=0.0, any_level=True)

    @classmethod
    def fit(cls, grp: list[GRP], image_size: tuple[int, int], camera: dict | None = None) -> "CameraModel":
        xyz = np.array([[g.X, g.Y, g.Z] for g in grp], dtype=np.float64)
        ij = np.array([[g.i, g.j] for g in grp], dtype=np.float64)
        if len(grp) < 4:
            raise GeometryError("too_few_grp", f"{len(grp)} points")
        planar = float(np.ptp(xyz[:, 2])) <= PLANE_TOL_M
        if planar and not camera:
            return cls._fit_planar(xyz, ij, image_size)
        if camera:
            return cls._fit_pnp(xyz, ij, image_size, camera)
        if len(grp) < 6:
            raise GeometryError("too_few_grp", "3D GRPs need at least 6 points")
        return cls._fit_dlt(xyz, ij, image_size)

    @classmethod
    def _fit_planar(cls, xyz, ij, image_size):
        method = cv.USAC_MAGSAC if len(xyz) > 4 else 0
        H, _ = cv.findHomography(xyz[:, :2], ij, method, 3.0)
        if H is None:
            raise GeometryError("homography_failed")
        m = cls(kind="planar", image_size=tuple(image_size), H=H, plane_z=float(np.mean(xyz[:, 2])))
        m._set_reprojection(xyz, ij)
        return m

    @classmethod
    def _fit_dlt(cls, xyz, ij, image_size):
        # Normalised DLT for the 3x4 projection, then a decomposition to seed solvePnP.
        P = _dlt(xyz, ij)
        K, R, C = _decompose(P)
        dist = np.zeros(5)
        rvec, _ = cv.Rodrigues(R)
        tvec = -R @ C
        ok, rvec, tvec = cv.solvePnP(xyz, ij, K, dist, rvec, tvec.reshape(3, 1), True, cv.SOLVEPNP_ITERATIVE)
        if not ok:
            raise GeometryError("pnp_failed")
        m = cls(kind="3d", image_size=tuple(image_size), K=K, dist=dist, rvec=rvec, tvec=tvec)
        m._set_reprojection(xyz, ij)
        return m

    @classmethod
    def _fit_pnp(cls, xyz, ij, image_size, camera):
        K = np.array(camera["K"], dtype=np.float64)
        dist = np.array(camera.get("dist", [0, 0, 0, 0, 0]), dtype=np.float64)
        flag = cv.SOLVEPNP_IPPE if float(np.ptp(xyz[:, 2])) <= PLANE_TOL_M else cv.SOLVEPNP_ITERATIVE
        ok, rvec, tvec = cv.solvePnP(xyz, ij, K, dist, flags=flag)
        if not ok:
            raise GeometryError("pnp_failed")
        m = cls(kind="3d", image_size=tuple(image_size), K=K, dist=dist, rvec=rvec, tvec=tvec)
        m._set_reprojection(xyz, ij)
        return m

    def _set_reprojection(self, xyz, ij):
        err = np.linalg.norm(self.project(xyz) - ij, axis=1)
        self.reproj_rmse_px = float(np.sqrt(np.mean(err**2)))
        self.reproj_max_px = float(err.max())

    def project(self, xyz: np.ndarray) -> np.ndarray:
        xyz = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
        if self.kind == "planar":
            pts = cv.perspectiveTransform(xyz[:, :2].reshape(-1, 1, 2), self.H)
            return pts.reshape(-1, 2)
        pts, _ = cv.projectPoints(xyz, self.rvec, self.tvec, self.K, self.dist)
        return pts.reshape(-1, 2)

    def rescaled(self, s: float) -> "CameraModel":
        """The same camera seen in frames resized by factor `s` (pixel coordinates scale by s)."""
        if s == 1.0:
            return self
        S = np.diag([s, s, 1.0])
        size = (int(round(self.image_size[0] * s)), int(round(self.image_size[1] * s)))
        m = CameraModel(kind=self.kind, image_size=size, plane_z=self.plane_z, dist=self.dist, rvec=self.rvec,
                        tvec=self.tvec, reproj_rmse_px=self.reproj_rmse_px * s, reproj_max_px=self.reproj_max_px * s,
                        any_level=self.any_level)
        if self.kind == "planar":
            m.H = S @ self.H
        else:
            m.K = S @ self.K
        return m

    def check_level(self, level: float) -> None:
        if self.kind == "planar" and not self.any_level and abs(level - self.plane_z) > PLANE_TOL_M:
            raise GeometryError(
                "planar_grp_level_mismatch",
                f"GRP plane z={self.plane_z:.3f} m, water level {level:.3f} m; intrinsics required",
            )

    def camera_center(self) -> np.ndarray | None:
        if self.kind != "3d":
            return None
        R, _ = cv.Rodrigues(self.rvec)
        return (-R.T @ self.tvec).ravel()

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "image_size": list(self.image_size), "reproj_rmse_px": self.reproj_rmse_px,
             "reproj_max_px": self.reproj_max_px}
        if self.kind == "planar":
            d.update(H=self.H.tolist(), plane_z=self.plane_z)
        else:
            d.update(K=self.K.tolist(), rvec=self.rvec.ravel().tolist(), tvec=self.tvec.ravel().tolist())
        return d


def _normalise(pts):
    c = pts.mean(axis=0)
    s = np.sqrt(pts.shape[1]) / np.mean(np.linalg.norm(pts - c, axis=1))
    T = np.eye(pts.shape[1] + 1)
    T[:-1, :-1] *= s
    T[:-1, -1] = -s * c
    return T


def _dlt(xyz, ij):
    T3 = _normalise(xyz)
    T2 = _normalise(ij)
    Xh = (T3 @ np.c_[xyz, np.ones(len(xyz))].T).T
    xh = (T2 @ np.c_[ij, np.ones(len(ij))].T).T
    A = []
    for (X, Y, Z, W), (u, v, w) in zip(Xh, xh):
        A.append([0, 0, 0, 0, -w * X, -w * Y, -w * Z, -w * W, v * X, v * Y, v * Z, v * W])
        A.append([w * X, w * Y, w * Z, w * W, 0, 0, 0, 0, -u * X, -u * Y, -u * Z, -u * W])
    _, _, Vt = np.linalg.svd(np.asarray(A))
    Pn = Vt[-1].reshape(3, 4)
    P = np.linalg.inv(T2) @ Pn @ T3
    return P / np.linalg.norm(P[2, :3])


def _decompose(P):
    K, R, C, *_ = cv.decomposeProjectionMatrix(P)
    K = K / K[2, 2]
    C = (C[:3] / C[3]).ravel()
    # decomposeProjectionMatrix can return negative focal lengths; fold the sign into R.
    S = np.diag(np.sign(np.diag(K)))
    K, R = K @ S, S @ R
    if np.linalg.det(R) < 0:
        R = -R
    K[0, 1] = 0.0
    return K, R, C


@dataclass(frozen=True)
class OrthoGrid:
    x0: float
    y0: float
    x1: float
    y1: float
    res_m: float
    level: float

    @property
    def shape(self) -> tuple[int, int]:
        w = int(round((self.x1 - self.x0) / self.res_m))
        h = int(round((self.y1 - self.y0) / self.res_m))
        return h, w

    def xy(self, col: np.ndarray, row: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Pixel centres of the ortho image to world X, Y. Row 0 is the max-Y edge (north up)."""
        return self.x0 + (np.asarray(col) + 0.5) * self.res_m, self.y1 - (np.asarray(row) + 0.5) * self.res_m

    def colrow(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return (np.asarray(x) - self.x0) / self.res_m - 0.5, (self.y1 - np.asarray(y)) / self.res_m - 0.5

    def to_dict(self) -> dict:
        return dict(x0=self.x0, y0=self.y0, x1=self.x1, y1=self.y1, res_m=self.res_m, level=self.level)


def ortho_maps(cam: CameraModel, grid: OrthoGrid) -> tuple[np.ndarray, np.ndarray]:
    cam.check_level(grid.level)
    h, w = grid.shape
    if h * w > 25_000_000:
        raise GeometryError("ortho_too_large", f"{w}x{h}")
    cols, rows = np.meshgrid(np.arange(w), np.arange(h))
    X, Y = grid.xy(cols.ravel(), rows.ravel())
    xyz = np.c_[X, Y, np.full(X.size, grid.level)]
    ij = cam.project(xyz)
    map_x = ij[:, 0].reshape(h, w).astype(np.float32)
    map_y = ij[:, 1].reshape(h, w).astype(np.float32)
    return map_x, map_y


def orthorectify(frames: list[np.ndarray], maps: tuple[np.ndarray, np.ndarray]) -> list[np.ndarray]:
    mx, my = maps
    return [cv.remap(f, mx, my, cv.INTER_LINEAR, borderMode=cv.BORDER_CONSTANT, borderValue=0) for f in frames]


def valid_ortho_mask(cam: CameraModel, maps: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    """Ortho pixels that map inside the image."""
    mx, my = maps
    w, h = cam.image_size
    return ((mx >= 0) & (mx < w - 1) & (my >= 0) & (my < h - 1)).astype(np.uint8)


def grid_from_extent(xmin: float, ymin: float, xmax: float, ymax: float, res_m: float, level: float) -> OrthoGrid:
    return OrthoGrid(float(xmin), float(ymin), float(xmax), float(ymax), float(res_m), float(level))
