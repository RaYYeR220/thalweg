"""Gauging station description: survey (GRPs, cross-section), coefficients, thresholds."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import yaml


@dataclass(frozen=True)
class GRP:
    X: float
    Y: float
    Z: float
    i: float
    j: float


@dataclass
class Thresholds:
    alert_stage: float | None = None
    warning_stage: float | None = None
    warning_q: float | None = None
    source: str = ""


@dataclass
class Station:
    id: str
    name: str
    grp: list[GRP]
    transect: np.ndarray  # (N, 3) X, Y, Z of the surveyed cross-section, bank to bank
    image_size: tuple[int, int]  # (width, height) of the camera frames
    alpha: float = 0.85
    alpha_source: str = "default"
    water_mask: list[tuple[float, float]] | None = None  # polygon in image pixels
    static_mask: list[list[tuple[float, float]]] | None = None  # polygons on banks/structures, image pixels
    reference_frame: str | None = None
    flow_hint_deg: float | None = None  # expected surface-flow azimuth in station coordinates
    ortho_extent: tuple[float, float, float, float] | None = None  # xmin, ymin, xmax, ymax (m)
    ortho_res_m: float | None = None
    scale_m_per_px: float | None = None  # nadir drone footage: pixel scale instead of GRPs
    camera: dict | None = None
    thresholds: Thresholds = field(default_factory=Thresholds)
    notes: str = ""
    sources: dict = field(default_factory=dict)

    def to_yaml(self) -> str:
        d = asdict(self)
        d["grp"] = [asdict(g) for g in self.grp]
        d["transect"] = np.asarray(self.transect).round(4).tolist()
        d["image_size"] = list(self.image_size)
        return yaml.safe_dump(d, sort_keys=False, allow_unicode=True)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_yaml(), encoding="utf-8")


def load_station(path: str | Path) -> Station:
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    d["grp"] = [GRP(**g) for g in d.get("grp") or []]
    d["transect"] = np.asarray(d["transect"], dtype=np.float64)
    d["image_size"] = tuple(d["image_size"])
    d["thresholds"] = Thresholds(**(d.get("thresholds") or {}))
    for k in ("ortho_extent",):
        if d.get(k) is not None:
            d[k] = tuple(d[k])
    if d.get("water_mask"):
        d["water_mask"] = [tuple(p) for p in d["water_mask"]]
    if d.get("static_mask"):
        d["static_mask"] = [[tuple(p) for p in poly] for poly in d["static_mask"]]
    return Station(**d)
