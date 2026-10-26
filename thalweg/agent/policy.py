"""The policy gate. Every tool call passes through `Policy.check` before it runs.

The rules are code, not prompt: a model can ask for anything, but a gauging is only
published when the evidence in the context supports the basis it claims.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from thalweg.agent.tools import DECISION_TOOLS, TOOLS
from thalweg.agent.types import Step, ToolCall, Verdict
from thalweg.vision.diagnose import BED_VISIBLE_RATIO


@dataclass
class PolicyLimits:
    max_steps: int = 24
    max_compute_s: float = 420.0
    grp_rmse_rel_max: float = 0.006  # GRP reprojection RMSE as a share of the image diagonal
    stab_residual_px_max: float = 2.0
    coverage_min: float = 0.6
    agree_tol: float = 0.15
    single_method_score_min: float = 0.5
    valid_frac_min: float = 0.5
    seeded_static_ratio_max: float = 0.5
    glare_max: float = 0.25
    dark_max: float = 0.5
    frozen_max: float = 0.2
    uncertainty_max: float = 0.5  # total 95 % relative uncertainty of a published discharge

    def to_dict(self) -> dict:
        return asdict(self)


QUALITY_BLOCKERS = ("no_moving_tracers", "too_dark", "glare")


class Policy:
    def __init__(self, limits: PolicyLimits | None = None):
        self.limits = limits or PolicyLimits()

    # -- generic checks -----------------------------------------------------------------------------------
    def check(self, call: ToolCall, ctx, history: list[Step], decided: bool = False) -> Verdict:
        spec = TOOLS.get(call.name)
        if spec is None:
            return Verdict(False, [f"unknown_tool:{call.name}"])
        if decided:
            return Verdict(False, ["already_decided"])
        bad = _validate_args(call.args, spec.params, spec.required)
        if bad:
            return Verdict(False, bad)
        for attr in spec.needs:
            if getattr(ctx, attr, None) in (None, {}, []):
                return Verdict(False, [f"prerequisite_missing:{attr}"])
        if call.name == "compute_discharge" and call.args.get("method") not in ctx.fields:
            return Verdict(False, [f"no_field:{call.args.get('method')}"])
        if call.name == "publish_gauging":
            return self.check_publish(call, ctx)
        return Verdict(True, [])

    # -- the publication gate -----------------------------------------------------------------------------
    def check_publish(self, call: ToolCall, ctx) -> Verdict:
        L = self.limits
        why: list[str] = []
        method, basis = call.args.get("method"), call.args.get("basis")
        diag = float(np.hypot(*ctx.cam.image_size)) if ctx.cam is not None else 0.0
        if ctx.cam is None or not np.isfinite(ctx.cam.reproj_rmse_px) or ctx.cam.reproj_rmse_px > L.grp_rmse_rel_max * diag:
            why.append("geometry_unverified")
        if ctx.stab is not None and "stabilization_rejected" in ctx.stab.notes and ctx.stab.motion_px_p95 > 5.0:
            why.append("camera_motion_uncorrected")
        elif ctx.stab is not None and np.isfinite(ctx.stab.residual_px_p95) and ctx.stab.residual_px_p95 > L.stab_residual_px_max \
                and "static_camera" not in ctx.stab.notes:
            why.append("stabilization_residual_high")
        if getattr(ctx, "pose_verified", True) is False:
            why.append("camera_pose_unverified")
        if ctx.diag is None:
            why.append("not_diagnosed")
        else:
            flags = ctx.diag.flags()
            why += [f"image_quality:{f}" for f in flags if f in QUALITY_BLOCKERS]
        frozen = getattr(ctx, "frozen_frac", 0.0)
        if frozen > L.frozen_max:
            why.append("frozen_frames")
        if not ctx.results:
            why.append("no_discharge")
            return Verdict(False, why)

        if basis == "methods_agree":
            agreeing = _agreeing(ctx, L.agree_tol)
            if not agreeing:
                why.append("methods_disagree")
            elif method not in agreeing and method != "mean_of_agreeing":
                why.append("method_not_in_agreeing_pair")
            chosen = agreeing if method == "mean_of_agreeing" else [method]
        elif basis == "bed_visible_prefers_stiv":
            if method != "stiv":
                why.append("basis_method_mismatch")
            if ctx.diag is None or ctx.diag.static_ratio <= BED_VISIBLE_RATIO:
                why.append("basis_false:bed_not_visible")
            chosen = ["stiv"]
        elif basis == "seeded_surface_prefers_lspiv":
            if method != "lspiv":
                why.append("basis_method_mismatch")
            if ctx.diag is None or ctx.diag.static_ratio > L.seeded_static_ratio_max:
                why.append("basis_false:surface_not_seeded")
            chosen = ["lspiv"]
        else:
            why.append("unknown_basis")
            chosen = []

        for m in chosen:
            r = ctx.results.get(m)
            f = ctx.fields.get(m)
            if r is None:
                why.append(f"no_discharge:{m}")
                continue
            if not np.isfinite(r.q) or r.q <= 0:
                why.append(f"discharge_not_positive:{m}:{r.q:.3f}")
            unc = (r.uncertainty or {}).get("total_95", 1.0)
            if unc > L.uncertainty_max:
                why.append(f"uncertainty_high:{m}:{unc:.2f}")
            if r.coverage < L.coverage_min:
                why.append(f"coverage_low:{m}:{r.coverage:.2f}")
            if f is not None and len(f):
                vf = f.n_valid / len(f)
                if vf < L.valid_frac_min:
                    why.append(f"valid_vectors_low:{m}:{vf:.2f}")
                sc = float(np.mean(f.score[f.valid])) if f.n_valid else 0.0
                if basis != "methods_agree" and sc < L.single_method_score_min:
                    why.append(f"confidence_low:{m}:{sc:.2f}")
        return Verdict(not why, why)


def _agreeing(ctx, tol: float) -> list[str]:
    qs = {k: r.q for k, r in ctx.results.items() if np.isfinite(r.q) and r.q > 0}
    best, best_d = [], None
    keys = list(qs)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = qs[keys[i]], qs[keys[j]]
            d = abs(a - b) / (0.5 * (a + b))
            if d <= tol and (best_d is None or d < best_d):
                best, best_d = [keys[i], keys[j]], d
    return best


def _validate_args(args: dict, props: dict, required: list[str]) -> list[str]:
    errs = []
    if not isinstance(args, dict):
        return ["bad_args:not_an_object"]
    for r in required:
        if r not in args or args[r] in (None, ""):
            errs.append(f"bad_args:missing:{r}")
    for k, v in args.items():
        if k not in props:
            errs.append(f"bad_args:unexpected:{k}")
            continue
        p = props[k]
        t = p.get("type")
        if t == "integer":
            if not isinstance(v, int) or isinstance(v, bool):
                if isinstance(v, float) and v.is_integer():
                    args[k] = int(v)
                    v = args[k]
                else:
                    errs.append(f"bad_args:type:{k}")
                    continue
        if t == "number" and (not isinstance(v, (int, float)) or isinstance(v, bool)):
            errs.append(f"bad_args:type:{k}")
            continue
        if t == "string" and not isinstance(v, str):
            errs.append(f"bad_args:type:{k}")
            continue
        if "enum" in p and v not in p["enum"]:
            errs.append(f"param_bounds:{k}")
        if "minimum" in p and v < p["minimum"]:
            errs.append(f"param_bounds:{k}")
        if "maximum" in p and v > p["maximum"]:
            errs.append(f"param_bounds:{k}")
    return errs


def decision_tool(name: str) -> bool:
    return name in DECISION_TOOLS
