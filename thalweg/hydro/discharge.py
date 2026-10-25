"""Discharge by the velocity-area (midsection) method over the surveyed cross-section."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from thalweg.vision.field import VectorField


class HydroError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass
class StationRow:
    s: float  # distance along the transect from the first waterline (m)
    x: float
    y: float
    depth: float
    width: float
    v_surf: float | None
    v_mean: float
    q_i: float
    n_vectors: int
    interpolated: bool


@dataclass
class DischargeResult:
    q: float
    q_lo: float
    q_hi: float
    area: float
    wetted_width: float
    coverage: float  # share of discharge-weighted width measured directly
    alpha: float
    level: float
    method: str
    v_mean: float
    stations: list[StationRow] = field(default_factory=list)
    uncertainty: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items() if k != "stations"}


def _wetted_section(transect: np.ndarray, level: float):
    """Chainage, bed elevation and XY of the wetted part of the transect, with waterline points inserted."""
    P = np.asarray(transect, dtype=np.float64)
    seg = np.linalg.norm(np.diff(P[:, :2], axis=0), axis=1)
    ch = np.r_[0.0, np.cumsum(seg)]
    z = P[:, 2]
    if np.all(z >= level):
        raise HydroError("level_out_of_range", f"level {level:.3f} below the whole bed")
    if z[0] < level or z[-1] < level:
        raise HydroError("level_out_of_range", f"level {level:.3f} above a bank top ({z[0]:.3f}, {z[-1]:.3f})")
    wet = z < level
    first, last = np.flatnonzero(wet)[[0, -1]]

    def crossing(a, b):
        t = (level - z[a]) / (z[b] - z[a])
        return ch[a] + t * (ch[b] - ch[a]), P[a, :2] + t * (P[b, :2] - P[a, :2])

    s0, p0 = crossing(first - 1, first)
    s1, p1 = crossing(last, last + 1)
    return s0, s1, ch, z, P[:, :2], p0, p1


def compute_discharge(field_: VectorField, transect: np.ndarray, level: float, alpha: float,
                      search_radius_m: float = 0.5, n_stations: int = 20, method: str | None = None,
                      flow_sign: float | None = None, min_vectors: int = 2) -> DischargeResult:
    s0, s1, ch, z, xy, p0, p1 = _wetted_section(transect, level)
    width = s1 - s0
    if width <= 0:
        raise HydroError("level_out_of_range", "no wetted width")
    d = xy[-1] - xy[0]
    t_hat = d / np.linalg.norm(d)
    n_hat = np.array([t_hat[1], -t_hat[0]])

    valid = field_.valid
    pts = np.c_[field_.x[valid], field_.y[valid]]
    vel = np.c_[field_.u[valid], field_.v[valid]]
    vn_all = vel @ n_hat
    if flow_sign is None:
        flow_sign = 1.0 if (vn_all.size == 0 or np.median(vn_all) >= 0) else -1.0
    tree = cKDTree(pts) if len(pts) else None

    # Station positions: midpoints of equal panels across the wetted width.
    edges = np.linspace(s0, s1, n_stations + 1)
    s_mid = 0.5 * (edges[:-1] + edges[1:])
    bed = np.interp(s_mid, ch, z)
    depth = np.clip(level - bed, 0.0, None)
    sx = np.interp(s_mid, ch, xy[:, 0])
    sy = np.interp(s_mid, ch, xy[:, 1])
    widths = np.diff(edges)

    v_surf = np.full(n_stations, np.nan)
    v_sd = np.full(n_stations, np.nan)
    counts = np.zeros(n_stations, dtype=int)
    if tree is not None:
        for i in range(n_stations):
            idx = tree.query_ball_point([sx[i], sy[i]], search_radius_m)
            if len(idx) >= min_vectors:
                vals = flow_sign * vn_all[idx]
                v_surf[i] = float(np.median(vals))
                v_sd[i] = float(np.std(vals) / np.sqrt(len(idx))) if len(idx) > 1 else 0.1 * abs(v_surf[i])
                counts[i] = len(idx)

    measured = np.isfinite(v_surf)
    interp = ~measured & (depth > 0)
    v_fill = v_surf.copy()
    if measured.any():
        # Fill gaps with the nearest measured station scaled by depth^(2/3) (Manning-type profile).
        mi = np.flatnonzero(measured)
        for i in np.flatnonzero(interp):
            j = mi[np.argmin(np.abs(mi - i))]
            ratio = (depth[i] / depth[j]) ** (2.0 / 3.0) if depth[j] > 0 else 0.0
            v_fill[i] = v_surf[j] * ratio
    v_fill = np.nan_to_num(v_fill)
    v_mean = alpha * v_fill
    q_i = v_mean * depth * widths
    q = float(q_i.sum())
    area = float((depth * widths).sum())
    qa = np.abs(q_i)
    coverage = float(qa[measured].sum() / qa.sum()) if qa.sum() > 0 else 0.0

    # Uncertainty budget (relative, 95 %): alpha, velocity sampling, interpolated share, level.
    rel = {}
    rel["alpha"] = 0.05 / alpha
    if q != 0 and measured.any():
        rel["velocity"] = float(np.sqrt(np.nansum((alpha * v_sd * depth * widths) ** 2)) * 1.96 / abs(q))
    else:
        rel["velocity"] = 1.0
    rel["interpolation"] = 0.2 * (1.0 - coverage)
    rel["level"] = 0.01 * width / area if area > 0 else 1.0
    tot = float(np.sqrt(sum(v**2 for v in rel.values())))
    rows = [StationRow(s=float(s_mid[i] - s0), x=float(sx[i]), y=float(sy[i]), depth=float(depth[i]),
                       width=float(widths[i]), v_surf=None if not measured[i] else float(v_surf[i]),
                       v_mean=float(v_mean[i]), q_i=float(q_i[i]), n_vectors=int(counts[i]),
                       interpolated=bool(interp[i])) for i in range(n_stations)]
    return DischargeResult(q=q, q_lo=q * (1 - tot), q_hi=q * (1 + tot), area=area, wetted_width=float(width),
                           coverage=coverage, alpha=alpha, level=level, method=method or field_.method,
                           v_mean=q / area if area > 0 else 0.0, stations=rows,
                           uncertainty={k: round(v, 4) for k, v in rel.items()} | {"total_95": round(tot, 4)})
