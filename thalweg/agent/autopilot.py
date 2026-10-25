"""Deterministic brain: fixed procedure with the parameter assistant. The ablation baseline for the LLM agent."""

from __future__ import annotations

from thalweg.agent.policy import QUALITY_BLOCKERS
from thalweg.agent.types import ToolCall
from thalweg.vision.diagnose import BED_VISIBLE_RATIO


class AutopilotBrain:
    name = "autopilot"
    actor = "autopilot"
    model = None
    cost_usd = 0.0

    def start(self, state) -> None:
        self._denied: list[str] = []
        self._plan: list[ToolCall] | None = None

    def feedback(self, call: ToolCall, result: dict) -> None:
        if "denied" in result:
            self._denied.append(call.name)

    def next(self, state) -> ToolCall | None:
        ctx = state.ctx
        flags = ctx.diag.flags() if ctx.diag else []
        blockers = [f for f in flags if f in QUALITY_BLOCKERS]
        if blockers or getattr(ctx, "frozen_frac", 0.0) > state.limits.frozen_max:
            why = blockers or ["frozen_frames"]
            return ToolCall("hold_and_request_recapture",
                            {"reason": f"image quality blocks velocimetry: {', '.join(why)}", "when": "next good light"})
        if self._plan is None:
            self._plan = [
                ToolCall("run_lspiv", {"reason": "standard method with assistant-suggested windows"}),
                ToolCall("compute_discharge", {"method": "lspiv", "reason": "discharge from LSPIV"}),
                ToolCall("run_stiv", {"reason": "independent second method for a cross-check"}),
                ToolCall("compute_discharge", {"method": "stiv", "reason": "discharge from STIV"}),
                ToolCall("compare_methods", {"reason": "cross-check"}),
            ]
        if self._plan:
            return self._plan.pop(0)
        if "publish_gauging" in self._denied:
            return ToolCall("escalate_to_hydrologist",
                            {"reason": "the evidence did not satisfy the publication policy"})
        res = ctx.results
        if len(res) >= 2:
            q = {k: r.q for k, r in res.items()}
            a, b = q.get("lspiv"), q.get("stiv")
            if a and b and abs(a - b) / (0.5 * (a + b)) <= state.limits.agree_tol:
                return ToolCall("publish_gauging", {"method": "mean_of_agreeing", "basis": "methods_agree",
                                                    "reason": "LSPIV and STIV agree within tolerance"})
        sr = ctx.diag.static_ratio if ctx.diag else 0.0
        if sr > BED_VISIBLE_RATIO and "stiv" in res:
            return ToolCall("publish_gauging", {"method": "stiv", "basis": "bed_visible_prefers_stiv",
                                                "reason": "riverbed visible through clear water: correlation is pinned "
                                                          "low, space-time streaks are not"})
        if sr <= state.limits.seeded_static_ratio_max and "lspiv" in res:
            return ToolCall("publish_gauging", {"method": "lspiv", "basis": "seeded_surface_prefers_lspiv",
                                                "reason": "well-seeded surface with little static texture"})
        return ToolCall("escalate_to_hydrologist", {"reason": "methods disagree and the diagnostics give no basis to "
                                                              "prefer one"})
