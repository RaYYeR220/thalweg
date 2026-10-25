"""The perceive -> decide -> act loop with the policy gate in front of every action."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import numpy as np

from thalweg.agent.policy import Policy, PolicyLimits, decision_tool
from thalweg.agent.tools import PREFIX, TOOLS
from thalweg.agent.types import Decision, RunResult, Step, ToolCall, Verdict
from thalweg.clip import Clip
from thalweg.pipeline import ArtifactSink, Context, StepError
from thalweg.station import Station

# Failures of the mandatory perception steps and what they mean for the run.
ESCALATE_CODES = {"planar_grp_level_mismatch", "pnp_failed", "homography_failed", "too_few_grp", "level_out_of_range",
                  "ortho_too_large", "camera_moved"}


class AgentState:
    """What a brain sees: the context plus the trace so far."""

    def __init__(self, ctx: Context, steps: list[Step], limits: PolicyLimits):
        self.ctx = ctx
        self.steps = steps
        self.limits = limits


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("r-%Y%m%d%H%M%S-") + uuid.uuid4().hex[:6]


def run_agent(station: Station, clip: Clip, brain, sink: ArtifactSink, limits: PolicyLimits | None = None,
              run_id: str | None = None, ctx: Context | None = None) -> RunResult:
    limits = limits or PolicyLimits()
    policy = Policy(limits)
    run_id = run_id or new_run_id()
    started = datetime.now(timezone.utc).isoformat()
    t_start = time.time()
    ctx = ctx or Context(station=station, clip=clip, sink=sink)
    steps: list[Step] = []
    decision: Decision | None = None

    def record(actor, tool, args, reason, verdict, ok=None, summary=None, error=None, t0=None):
        ev = list((summary or {}).get("evidence", [])) if isinstance(summary, dict) else []
        s = Step(i=len(steps), actor=actor, tool=tool, args=args, reason=reason, verdict=verdict, ok=ok,
                 summary=summary, evidence=ev, error=error, t_ms=int(1000 * (time.time() - (t0 or time.time()))))
        steps.append(s)
        return s

    # 1. Mandatory perception. Any failure here ends the run without a number.
    for name, fn in PREFIX:
        t0 = time.time()
        try:
            out = fn(ctx)
            record("system", name, {}, "mandatory perception step", Verdict(True), True, out, t0=t0)
            if name == "load_frames":
                ctx.frozen_frac = out.get("frozen_frac", 0.0)
            if name == "check_camera_pose" and out.get("checked"):
                ctx.pose_verified = bool(out.get("verified", True))
            if name == "check_camera_pose" and out.get("checked") and out.get("moved"):
                decision = Decision("ESCALATE", ["camera_moved: the view no longer matches the surveyed reference frame; "
                                                 "GRP pixels must be re-confirmed before any gauging"],
                                    evidence=out.get("evidence", []))
                break
        except StepError as e:
            record("system", name, {}, "mandatory perception step", Verdict(True), False, None, error=e.code, t0=t0)
            kind = "ESCALATE" if e.code in ESCALATE_CODES else "HOLD"
            decision = Decision(kind, [f"perception_failed:{name}:{e.code}"])
            break
        except Exception as e:  # noqa: BLE001 - any unexpected failure fails closed
            record("system", name, {}, "mandatory perception step", Verdict(True), False, None,
                   error=f"{type(e).__name__}: {e}"[:300], t0=t0)
            decision = Decision("HOLD", [f"fail_closed:{name}:{type(e).__name__}"])
            break

    # 2. The brain chooses; the policy gates; the loop executes.
    if decision is None:
        brain.start(AgentState(ctx, steps, limits))
        while decision is None:
            if len([s for s in steps if s.actor != "system"]) >= limits.max_steps:
                decision = Decision("HOLD", ["fail_closed:step_budget"])
                break
            if time.time() - t_start > limits.max_compute_s:
                decision = Decision("HOLD", ["fail_closed:time_budget"])
                break
            try:
                call = brain.next(AgentState(ctx, steps, limits))
            except Exception as e:  # noqa: BLE001
                cause = getattr(e, "cause", None) or type(e).__name__
                record(brain.actor, "brain_error", {}, "", Verdict(False, [f"brain_error:{cause}"]), False,
                       error=str(e)[:300])
                decision = Decision("HOLD", [f"fail_closed:{cause}"])
                break
            if call is None:
                decision = Decision("HOLD", ["fail_closed:brain_stopped_without_decision"])
                break
            verdict = policy.check(call, ctx, steps)
            if not verdict.allowed:
                record(brain.actor, call.name, call.args, call.reason, verdict, None)
                brain.feedback(call, {"denied": verdict.reasons})
                continue
            if decision_tool(call.name):
                decision = _decide(call, ctx)
                record(brain.actor, call.name, call.args, call.reason, verdict, True, decision.to_dict())
                brain.feedback(call, {"accepted": decision.kind})
                break
            t0 = time.time()
            try:
                out = TOOLS[call.name].fn(ctx, call.args)
                record(brain.actor, call.name, call.args, call.reason, verdict, True, out, t0=t0)
                brain.feedback(call, out)
            except StepError as e:
                record(brain.actor, call.name, call.args, call.reason, verdict, False, error=e.code, t0=t0)
                brain.feedback(call, {"error": e.code})
            except Exception as e:  # noqa: BLE001
                record(brain.actor, call.name, call.args, call.reason, verdict, False,
                       error=f"{type(e).__name__}: {e}"[:300], t0=t0)
                brain.feedback(call, {"error": type(e).__name__})

    estimates = {k: {"q": round(r.q, 4), "q_lo": round(r.q_lo, 4), "q_hi": round(r.q_hi, 4),
                     "coverage": round(r.coverage, 3)} for k, r in ctx.results.items()}
    if decision.kind != "PUBLISH" and decision.q is None and ctx.results:
        # Keep the best unpublished estimate for the reviewer; it is never shown as a gauging.
        best = max(ctx.results.values(), key=lambda r: r.coverage)
        decision.q = {**best.summary(), "published": False}
    return RunResult(run_id=run_id, station_id=station.id, clip=str(clip.video.name), brain=brain.name,
                     model=getattr(brain, "model", None), decision=decision, steps=steps, timings=ctx.timings,
                     cost_usd=getattr(brain, "cost_usd", 0.0), started=started,
                     ended=datetime.now(timezone.utc).isoformat(), notes=ctx.notes, estimates=estimates)


def _decide(call: ToolCall, ctx: Context) -> Decision:
    a = call.args
    reason = a.get("reason") or call.reason
    if call.name == "publish_gauging":
        method = a["method"]
        if method == "mean_of_agreeing":
            from thalweg.agent.policy import _agreeing, PolicyLimits as _PL
            pair = _agreeing(ctx, _PL().agree_tol)
            rs = [ctx.results[m] for m in pair]
            q = float(np.mean([r.q for r in rs]))
            lo = float(np.mean([r.q_lo for r in rs]))
            hi = float(np.mean([r.q_hi for r in rs]))
            summary = {**rs[0].summary(), "q": round(q, 4), "q_lo": round(lo, 4), "q_hi": round(hi, 4),
                       "method": "+".join(pair)}
        else:
            summary = ctx.results[method].summary()
        return Decision("PUBLISH", [reason], method=method, basis=a.get("basis"), q={**summary, "published": True})
    if call.name == "hold_and_request_recapture":
        spec = {k: a[k] for k in ("duration_s", "fps", "when") if k in a}
        return Decision("HOLD", [reason], recapture_spec=spec or None)
    return Decision("ESCALATE", [reason])
