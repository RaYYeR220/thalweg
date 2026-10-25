"""The gauging context and the perception/measurement steps the agent (or the autopilot) calls.

Every step reads and writes the shared Context, returns a small JSON-able summary, and
may store evidence images through the ArtifactSink so a reviewer can see what was seen.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2 as cv
import numpy as np

from thalweg.clip import Clip, FrameStack, probe, read_frames
from thalweg.evidence import render_field, render_overlay, render_sti
from thalweg.hydro.discharge import DischargeResult, HydroError, compute_discharge
from thalweg.station import Station
from thalweg.vision.diagnose import Diagnostics, diagnose, polygon_mask, water_mask_from_transect
from thalweg.vision.features import FeatureMatcher
from thalweg.vision.field import VectorField, filter_field
from thalweg.vision.flow import CoarseMotion, coarse_motion, dense_flow, suggest_params
from thalweg.vision.geometry import CameraModel, GeometryError, OrthoGrid, ortho_maps, orthorectify, valid_ortho_mask
from thalweg.vision.lspiv import lspiv
from thalweg.vision.stabilize import StabResult, check_camera_pose, stabilize
from thalweg.vision.stiv import space_time_image, stiv, lines_across_transect


class StepError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class ArtifactSink:
    """Stores evidence locally; the worker swaps in an S3-backed sink."""

    def __init__(self, root: str | Path, prefix: str = ""):
        self.root = Path(root)
        self.prefix = prefix
        self.root.mkdir(parents=True, exist_ok=True)

    def put_image(self, name: str, img: np.ndarray, quality: int = 85) -> str:
        key = f"{self.prefix}{name}.jpg"
        cv.imwrite(str(self.root / f"{name}.jpg"), img, [cv.IMWRITE_JPEG_QUALITY, quality])
        return key

    def put_json(self, name: str, obj) -> str:
        key = f"{self.prefix}{name}.json"
        (self.root / f"{name}.json").write_text(json.dumps(obj, default=_json_default, indent=1), encoding="utf-8")
        return key

    def path(self, key: str) -> Path:
        return self.root / Path(key).name


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


@dataclass
class Context:
    station: Station
    clip: Clip
    sink: ArtifactSink
    matcher: FeatureMatcher | None = None
    raw: FrameStack | None = None
    frames: list[np.ndarray] | None = None
    stab: StabResult | None = None
    cam: CameraModel | None = None
    grid: OrthoGrid | None = None
    maps: tuple | None = None
    valid_mask: np.ndarray | None = None
    water_mask: np.ndarray | None = None
    ortho: list[np.ndarray] | None = None
    diag: Diagnostics | None = None
    coarse: CoarseMotion | None = None
    params: dict = field(default_factory=dict)
    px_scale: float = 1.0  # working frames / station survey pixels
    frozen_frac: float = 0.0  # share of repeated frames in the clip
    pose_verified: bool = True  # False when the reference-view check could not be made
    fields: dict[str, VectorField] = field(default_factory=dict)
    results: dict[str, DischargeResult] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def level(self) -> float:
        return float(self.clip.level)

    def flow_deg(self) -> float:
        if self.coarse is not None:
            return self.coarse.direction_deg
        if self.station.flow_hint_deg is not None:
            return float(self.station.flow_hint_deg)
        return 0.0


def _timed(ctx: Context, name: str, t0: float):
    ctx.timings[name] = round(ctx.timings.get(name, 0.0) + time.time() - t0, 3)


# ---- perception ------------------------------------------------------------------------------------------

def step_probe(ctx: Context) -> dict:
    info = probe(ctx.clip.video)
    if not info.get("ok"):
        raise StepError("cannot_open_video", str(ctx.clip.video))
    w, h = ctx.station.image_size
    info["matches_station_size"] = (info["width"], info["height"]) == (w, h)
    return info


MAX_WORK_WIDTH = 1920


def step_load(ctx: Context, dur_s: float = 10.0, start_s: float = 0.0) -> dict:
    t0 = time.time()
    ctx.raw = read_frames(ctx.clip.video, start_s=start_s, dur_s=dur_s, max_width=MAX_WORK_WIDTH)
    w, h = ctx.station.image_size
    # Survey pixels refer to the station's native frame size; work at most MAX_WORK_WIDTH wide
    # and carry the scale into every pixel-space quantity (camera, masks, reference frame).
    ctx.px_scale = min(1.0, MAX_WORK_WIDTH / w)
    tw, th = int(round(w * ctx.px_scale)), int(round(h * ctx.px_scale))
    if ctx.raw.size != (tw, th):
        ctx.raw.frames = [cv.resize(f, (tw, th), interpolation=cv.INTER_AREA) for f in ctx.raw.frames]
        ctx.notes.append(f"resized {ctx.raw.size} -> {(tw, th)}")
        ctx.raw.size = (tw, th)
    if ctx.px_scale < 1.0:
        ctx.notes.append(f"working scale {ctx.px_scale:.3f} of native {w}x{h}")
    ctx.frames = ctx.raw.frames
    _timed(ctx, "load", t0)
    frozen = _frozen_fraction(ctx.frames)
    return {"frames": len(ctx.frames), "dt_s": round(ctx.raw.dt_s, 5), "duration_s": round(float(ctx.raw.t_s[-1]), 2),
            "warnings": ctx.raw.warnings, "frozen_frac": round(frozen, 3)}


def _frozen_fraction(frames: list[np.ndarray]) -> float:
    if len(frames) < 3:
        return 1.0
    same = 0
    for a, b in zip(frames[:-1], frames[1:]):
        if float(cv.absdiff(a, b).mean()) < 0.05:
            same += 1
    return same / (len(frames) - 1)


def _static_mask(ctx: Context) -> np.ndarray | None:
    polys = ctx.station.static_mask
    if not polys:
        return None
    s = ctx.px_scale
    w, h = ctx.station.image_size
    w, h = int(round(w * s)), int(round(h * s))
    m = np.zeros((h, w), np.uint8)
    for p in polys:
        m |= polygon_mask((h, w), [(x * s, y * s) for x, y in p])
    return m


def _matcher(ctx: Context) -> FeatureMatcher:
    if ctx.matcher is None:
        ctx.matcher = FeatureMatcher()
    return ctx.matcher


def step_check_pose(ctx: Context) -> dict:
    ref_path = ctx.station.reference_frame
    if not ref_path:
        return {"checked": False, "reason": "no_reference_frame"}
    ref = cv.imread(str(ref_path), cv.IMREAD_GRAYSCALE)
    if ref is None:
        return {"checked": False, "reason": "reference_unreadable"}
    if ref.shape[::-1] != ctx.raw.size:
        ref = cv.resize(ref, ctx.raw.size, interpolation=cv.INTER_AREA)
    t0 = time.time()
    pc = check_camera_pose(ref, ctx.frames[0], _static_mask(ctx), _matcher(ctx))
    _timed(ctx, "pose", t0)
    out = {"checked": True, **pc.summary()}
    if pc.H is not None:
        out["H"] = np.round(pc.H, 5).tolist()
    return out


def step_stabilize(ctx: Context, keyframe_every: int = 25) -> dict:
    t0 = time.time()
    mask = _static_mask(ctx)
    if mask is None:
        # No surveyed static scenery: the station is declared a fixed mount and frames are used as recorded.
        ctx.stab = StabResult(ctx.frames, [np.eye(3)] * len(ctx.frames), float("nan"), 0.0, 1.0, "none", 0,
                              ["no_static_scenery_defined", "static_camera"])
        return ctx.stab.summary()
    ctx.stab = stabilize(ctx.frames, mask, _matcher(ctx), keyframe_every=keyframe_every)
    if "stabilization_rejected" not in ctx.stab.notes:
        ctx.frames = ctx.stab.frames
    _timed(ctx, "stabilize", t0)
    return ctx.stab.summary()


def _default_extent(st: Station) -> tuple[float, float, float, float]:
    if st.ortho_extent:
        return st.ortho_extent
    pts = np.array([[g.X, g.Y] for g in st.grp] + [list(p[:2]) for p in st.transect])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    pad = 0.05 * (hi - lo).max()
    return float(lo[0] - pad), float(lo[1] - pad), float(hi[0] + pad), float(hi[1] + pad)


def build_camera(st: Station) -> CameraModel:
    if st.scale_m_per_px:
        origin = (st.camera or {}).get("origin_px", (0.0, float(st.image_size[1])))
        return CameraModel.scaled(st.scale_m_per_px, st.image_size, tuple(origin))
    return CameraModel.fit(st.grp, st.image_size, st.camera)


def step_orthorectify(ctx: Context, res_m: float | None = None) -> dict:
    t0 = time.time()
    st = ctx.station
    tz = np.asarray(st.transect)[:, 2]
    if ctx.level >= min(tz[0], tz[-1]) or ctx.level <= tz.min():
        # The river is outside the surveyed section (over a bank top, or below the bed): no discharge
        # can be computed from this survey, whatever the velocities are.
        raise StepError("level_out_of_range",
                        f"level {ctx.level:.3f} m vs bank tops {tz[0]:.3f}/{tz[-1]:.3f} m, bed {tz.min():.3f} m")
    try:
        ctx.cam = build_camera(st).rescaled(ctx.px_scale)
        x0, y0, x1, y1 = _default_extent(st)
        res = res_m or st.ortho_res_m or max((x1 - x0), (y1 - y0)) / 600.0
        ctx.grid = OrthoGrid(x0, y0, x1, y1, float(res), ctx.level)
        ctx.maps = ortho_maps(ctx.cam, ctx.grid)
    except GeometryError as e:
        raise StepError(e.code, e.detail) from e
    ctx.ortho = orthorectify(ctx.frames, ctx.maps)
    # The full-resolution frames are no longer needed; keep the first one for evidence overlays.
    first = ctx.raw.frames[0]
    ctx.raw.frames = [first]
    ctx.frames = None
    if ctx.stab is not None:
        ctx.stab.frames = []
    ctx.valid_mask = valid_ortho_mask(ctx.cam, ctx.maps)
    if ctx.stab is not None and ctx.stab.valid is not None:
        # Only water seen in every stabilized frame takes part in the measurement.
        seen = cv.remap(ctx.stab.valid * 255, ctx.maps[0], ctx.maps[1], cv.INTER_NEAREST, borderValue=0)
        ctx.valid_mask = ctx.valid_mask & (seen > 0).astype(np.uint8)
    flow = ctx.station.flow_hint_deg if ctx.station.flow_hint_deg is not None else 0.0
    ctx.water_mask = water_mask_from_transect(ctx.grid, st.transect, ctx.level, _band_half_length(ctx), flow,
                                              ctx.valid_mask)
    _timed(ctx, "orthorectify", t0)
    ev = [ctx.sink.put_image("ortho", _ortho_preview(ctx)),
          ctx.sink.put_image("frame_overlay", render_overlay(ctx.raw.frames[0], ctx.cam, st, ctx.level,
                                                             px_scale=ctx.px_scale))]
    return {"kind": ctx.cam.kind, "reproj_rmse_px": round(ctx.cam.reproj_rmse_px / ctx.px_scale, 3),
            "reproj_rmse_rel_diag": round(ctx.cam.reproj_rmse_px / float(np.hypot(*ctx.cam.image_size)), 5),
            "reproj_max_px": round(ctx.cam.reproj_max_px / ctx.px_scale, 3), "res_m": round(ctx.grid.res_m, 4),
            "ortho_shape": list(ctx.grid.shape), "water_px": int(ctx.water_mask.sum()), "evidence": ev}


def _band_half_length(ctx: Context) -> float:
    x0, y0, x1, y1 = _default_extent(ctx.station)
    return 0.3 * max(x1 - x0, y1 - y0)


def _ortho_preview(ctx: Context) -> np.ndarray:
    img = cv.cvtColor(ctx.ortho[0], cv.COLOR_GRAY2BGR)
    contours, _ = cv.findContours(ctx.water_mask.astype(np.uint8), cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
    cv.drawContours(img, contours, -1, (255, 200, 0), 1)
    return img


def step_diagnose(ctx: Context) -> dict:
    t0 = time.time()
    ctx.diag = diagnose(ctx.ortho, ctx.water_mask, ctx.grid.res_m)
    _timed(ctx, "diagnose", t0)
    return ctx.diag.to_dict()


AXIS_TOLERANCE_DEG = 45.0


def _axis_guard(ctx: Context, cm: CoarseMotion) -> str | None:
    """Keep the measured flow direction on the channel axis surveyed for the station.

    On glassy, poorly seeded water the dense flow can be dominated by ripples running toward a
    bank. Lines and search windows laid across the channel would then measure almost nothing
    normal to the section. If the measured axis is more than 45 degrees off the surveyed one,
    the surveyed axis is used, in the sense closest to the measurement.
    """
    hint = ctx.station.flow_hint_deg
    if hint is None:
        return None
    d = (cm.direction_deg - hint + 180.0) % 360.0 - 180.0
    axis_dev = min(abs(d), 180.0 - abs(d))
    if axis_dev <= AXIS_TOLERANCE_DEG:
        return None
    sense = hint if abs(d) <= 90.0 else (hint + 180.0) % 360.0
    sense = (sense + 180.0) % 360.0 - 180.0
    note = f"flow_axis_from_survey: measured {cm.direction_deg:.0f} deg is {axis_dev:.0f} deg off the channel axis"
    cm.direction_deg = float(sense)
    return note


def step_coarse_motion(ctx: Context) -> dict:
    t0 = time.time()
    ctx.coarse = coarse_motion(ctx.ortho, ctx.raw.dt_s, ctx.grid, ctx.water_mask)
    guard = _axis_guard(ctx, ctx.coarse)
    if guard:
        ctx.notes.append(guard)
    ctx.params = suggest_params(ctx.coarse, ctx.grid.res_m, ctx.raw.dt_s)
    # Re-centre the water band on the measured flow direction.
    ctx.water_mask = water_mask_from_transect(ctx.grid, ctx.station.transect, ctx.level, _band_half_length(ctx),
                                              ctx.coarse.direction_deg, ctx.valid_mask)
    _timed(ctx, "coarse_motion", t0)
    return {**ctx.coarse.to_dict(), "suggested": ctx.params, **({"axis_guard": guard} if guard else {})}


# ---- velocimetry ----------------------------------------------------------------------------------------

def step_lspiv(ctx: Context, ia_px: int | None = None, search_px: int | None = None, pair_step: int | None = None,
               background: str = "highpass:3", step_px: int | None = None) -> dict:
    p = ctx.params
    t0 = time.time()
    f = lspiv(ctx.ortho, ctx.raw.dt_s, ctx.grid, ia_px=ia_px or p["ia_px"], search_px=search_px or p["search_px"],
              step_px=step_px or p["step_px"], mask=ctx.water_mask, pair_step=pair_step or p["pair_step"],
              shift_px=tuple(p["shift_px"]) if pair_step in (None, p["pair_step"]) else
              tuple(np.array(p["shift_px"]) * (pair_step / p["pair_step"])),
              background=background, max_pairs=150)
    f = filter_field(f, score_min=0.25, vmax=p.get("vmax_ms"), direction_deg=ctx.flow_deg(), direction_tol_deg=70)
    ctx.fields["lspiv"] = f
    _timed(ctx, "lspiv", t0)
    ev = ctx.sink.put_image("field_lspiv", render_field(ctx.ortho[0], f, ctx.grid, ctx.station.transect))
    return {**f.summary(), "params": f.params, "evidence": [ev]}


def step_stiv(ctx: Context, line_m: float | None = None, window: int = 16, background: str = "highpass:3",
              n_lines: int = 20) -> dict:
    t0 = time.time()
    x0, y0, x1, y1 = _default_extent(ctx.station)
    L = line_m or float(np.clip(0.35 * max(x1 - x0, y1 - y0), 1.0, 8.0))
    lines = lines_across_transect(ctx.station.transect, ctx.level, ctx.flow_deg(), n=n_lines, length_m=L)
    f = stiv(ctx.ortho, ctx.raw.dt_s, ctx.grid, lines, vmax_ms=ctx.params.get("vmax_ms", 4.0), background=background,
             window=window)
    f = filter_field(f, score_min=0.3, vmax=ctx.params.get("vmax_ms"), nmt_thresh=1e9)
    ctx.fields["stiv"] = f
    _timed(ctx, "stiv", t0)
    from thalweg.vision.lspiv import remove_background
    mid_i = len(lines) // 2
    mid = lines[mid_i]
    sti = space_time_image(remove_background(ctx.ortho, background), ctx.grid, *mid)
    ev = [ctx.sink.put_image("field_stiv", render_field(ctx.ortho[0], f, ctx.grid, ctx.station.transect, lines=lines)),
          ctx.sink.put_image("sti_mid", render_sti(sti, f.speed[mid_i] if len(f) else None,
                                                   ctx.raw.dt_s, ctx.grid.res_m))]
    return {**f.summary(), "params": {**f.params, "line_m": round(L, 2), "window": window}, "evidence": ev}


def step_dense_flow(ctx: Context, pair_step: int = 1, background: str = "highpass:3") -> dict:
    t0 = time.time()
    f = dense_flow(ctx.ortho, ctx.raw.dt_s, ctx.grid, ctx.water_mask, step_px=ctx.params.get("step_px", 12),
                   pair_step=pair_step, background=background)
    f = filter_field(f, score_min=0.3, vmax=ctx.params.get("vmax_ms"), direction_deg=ctx.flow_deg(),
                     direction_tol_deg=70)
    ctx.fields["dense_flow"] = f
    _timed(ctx, "dense_flow", t0)
    ev = ctx.sink.put_image("field_dense_flow", render_field(ctx.ortho[0], f, ctx.grid, ctx.station.transect))
    return {**f.summary(), "params": f.params, "evidence": [ev]}


def step_discharge(ctx: Context, method: str, alpha: float | None = None) -> dict:
    if method not in ctx.fields:
        raise StepError("no_field", method)
    f = ctx.fields[method]
    sparse = method == "stiv"
    spacing = _station_spacing(ctx)
    try:
        radius = 0.6 * spacing if sparse else max(spacing, 2.5 * ctx.grid.res_m * ctx.params.get("step_px", 12))
        r = compute_discharge(f, ctx.station.transect, ctx.level, alpha or ctx.station.alpha, search_radius_m=radius,
                              min_vectors=2, method=method)
    except HydroError as e:
        raise StepError(e.code, str(e)) from e
    ctx.results[method] = r
    return r.summary()


def _station_spacing(ctx: Context) -> float:
    P = ctx.station.transect
    wet = P[P[:, 2] < ctx.level]
    if len(wet) < 2:
        return 0.5
    return float(np.linalg.norm(wet[-1, :2] - wet[0, :2]) / 20.0)


def step_compare(ctx: Context) -> dict:
    qs = {k: r.q for k, r in ctx.results.items()}
    if len(qs) < 2:
        return {"methods": qs, "agree": None}
    vals = np.array(list(qs.values()))
    spread = float((vals.max() - vals.min()) / max(abs(np.median(vals)), 1e-9))
    pairs = {}
    keys = list(qs)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = qs[keys[i]], qs[keys[j]]
            pairs[f"{keys[i]}~{keys[j]}"] = round(abs(a - b) / max(abs(0.5 * (a + b)), 1e-9), 3)
    return {"methods": {k: round(v, 4) for k, v in qs.items()}, "spread": round(spread, 3), "pairwise": pairs}
