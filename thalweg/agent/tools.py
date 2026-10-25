"""The tools the agent can call. Perception tools wrap OpenCV 5 pipeline steps; three tools end the run.

There is deliberately no tool that issues a flood warning: warnings are drafted by the
station logic after a published gauging and only a person can sign them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from thalweg import pipeline as P

DECISION_TOOLS = ("publish_gauging", "hold_and_request_recapture", "escalate_to_hydrologist")
PUBLISH_BASES = ("methods_agree", "bed_visible_prefers_stiv", "seeded_surface_prefers_lspiv")
METHODS = ("lspiv", "stiv", "dense_flow")
BACKGROUNDS = ("mean", "median", "highpass:2", "highpass:3", "highpass:5")


@dataclass
class ToolSpec:
    name: str
    description: str
    params: dict  # JSON schema "properties"
    required: list[str]
    fn: Callable | None  # (ctx, args) -> dict; None for decision tools (handled by the loop)
    needs: tuple[str, ...] = ()  # context attributes that must exist before the call

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params, "required": self.required,
                           "additionalProperties": False}}}


def _int(lo, hi, desc):
    return {"type": "integer", "minimum": lo, "maximum": hi, "description": desc}


def _num(lo, hi, desc):
    return {"type": "number", "minimum": lo, "maximum": hi, "description": desc}


REASON = {"type": "string", "description": "One sentence: what you saw that makes this the right next step."}

TOOLS: dict[str, ToolSpec] = {}


def _reg(spec: ToolSpec):
    TOOLS[spec.name] = spec


_reg(ToolSpec("run_lspiv", "Large-scale PIV: ensemble cross-correlation of interrogation areas between frames on the "
              "orthorectified surface. Strong when the surface carries plenty of moving tracers (foam, ripples, debris). "
              "Biased toward zero when static texture (riverbed seen through clear water) dominates.",
              {"ia_px": _int(15, 81, "interrogation area side, ortho px"),
               "search_px": _int(4, 40, "search radius around the expected shift, ortho px"),
               "pair_step": _int(1, 8, "frames between the correlated pair"),
               "background": {"type": "string", "enum": list(BACKGROUNDS)}, "reason": REASON},
              ["reason"], lambda ctx, a: P.step_lspiv(ctx, a.get("ia_px"), a.get("search_px"), a.get("pair_step"),
                                                      a.get("background", "highpass:3")), needs=("ortho", "params")))
_reg(ToolSpec("run_stiv", "Space-time image velocimetry: streak slopes in space-time images sampled along lines parallel "
              "to the flow, one line per transect station. Robust to static riverbed texture after a temporal "
              "high-pass; can lock onto waves that travel at a different speed than the water.",
              {"line_m": _num(0.5, 20.0, "search line length, metres"),
               "window": _int(8, 48, "frames per alignment window (feature lifetime)"),
               "background": {"type": "string", "enum": list(BACKGROUNDS)}, "reason": REASON},
              ["reason"], lambda ctx, a: P.step_stiv(ctx, a.get("line_m"), a.get("window", 16),
                                                     a.get("background", "highpass:3")), needs=("ortho", "params")))
_reg(ToolSpec("run_dense_flow", "Dense DIS optical flow on the orthorectified surface, temporal median over frame pairs. "
              "A third, independent opinion; noisy on low-texture water.",
              {"pair_step": _int(1, 6, "frames between pairs"),
               "background": {"type": "string", "enum": list(BACKGROUNDS)}, "reason": REASON},
              ["reason"], lambda ctx, a: P.step_dense_flow(ctx, a.get("pair_step", 1), a.get("background", "highpass:3")),
              needs=("ortho", "params")))
_reg(ToolSpec("compute_discharge", "Velocity-area (midsection) discharge over the surveyed cross-section from one "
              "method's velocity field, with an uncertainty budget and coverage.",
              {"method": {"type": "string", "enum": list(METHODS)},
               "alpha": _num(0.6, 1.1, "surface-to-mean velocity coefficient; omit to use the station's"),
               "reason": REASON},
              ["method", "reason"], lambda ctx, a: P.step_discharge(ctx, a["method"], a.get("alpha")), needs=("ortho",)))
_reg(ToolSpec("compare_methods", "Discharge from every method computed so far, with pairwise relative differences.",
              {"reason": REASON}, ["reason"], lambda ctx, a: P.step_compare(ctx)))
_reg(ToolSpec("publish_gauging", "Publish the gauging. The policy checks the evidence: geometry, stabilization, "
              "coverage, image quality, and the stated basis must actually hold.",
              {"method": {"type": "string", "enum": list(METHODS) + ["mean_of_agreeing"]},
               "basis": {"type": "string", "enum": list(PUBLISH_BASES),
                         "description": "methods_agree: two methods within tolerance; bed_visible_prefers_stiv: "
                                        "diagnostics flag a visible riverbed so correlation is biased low; "
                                        "seeded_surface_prefers_lspiv: abundant moving tracers and little static texture"},
               "reason": REASON}, ["method", "basis", "reason"], None))
_reg(ToolSpec("hold_and_request_recapture", "Do not publish; ask the station for a better clip (capture problem).",
              {"duration_s": _int(5, 120, "requested clip length"), "fps": _int(10, 120, "requested frame rate"),
               "when": {"type": "string", "description": "e.g. 'daylight', 'after rain stops', 'now'"},
               "reason": REASON}, ["reason"], None))
_reg(ToolSpec("escalate_to_hydrologist", "Do not publish; hand the case to a hydrologist with the evidence "
              "(survey/geometry problem, camera moved, or methods disagree without a justified basis).",
              {"reason": REASON}, ["reason"], None))

PREFIX = [("probe_clip", lambda ctx: P.step_probe(ctx)),
          ("load_frames", lambda ctx: P.step_load(ctx)),
          ("check_camera_pose", lambda ctx: P.step_check_pose(ctx)),
          ("stabilize", lambda ctx: P.step_stabilize(ctx)),
          ("orthorectify", lambda ctx: P.step_orthorectify(ctx)),
          ("diagnose", lambda ctx: P.step_diagnose(ctx)),
          ("coarse_motion", lambda ctx: P.step_coarse_motion(ctx))]


def tool_schemas() -> list[dict]:
    return [t.schema() for t in TOOLS.values()]
