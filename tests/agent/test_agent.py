"""End-to-end agent runs on a synthetic nadir channel with a known discharge."""

from datetime import datetime
from pathlib import Path

import cv2 as cv
import numpy as np
import pytest

from tests.fixtures.synth import advected_frames
from thalweg.agent.autopilot import AutopilotBrain
from thalweg.agent.budget import Budget, MemoryStore
from thalweg.agent.llm import BrainError, LLMBrain
from thalweg.agent.loop import run_agent
from thalweg.agent.policy import Policy
from thalweg.agent.types import ToolCall
from thalweg.clip import Clip
from thalweg.pipeline import ArtifactSink, Context
from thalweg.station import Station, Thresholds

RES, FPS, U_PX = 0.01, 25.0, 3.0  # 0.75 m/s surface velocity toward -X


def _write_video(path: Path, frames):
    h, w = frames[0].shape
    vw = cv.VideoWriter(str(path), cv.VideoWriter_fourcc(*"mp4v"), FPS, (w, h), False)
    for f in frames:
        vw.write(f)
    vw.release()


@pytest.fixture(scope="module")
def channel(tmp_path_factory):
    d = tmp_path_factory.mktemp("chan")
    frames = advected_frames(shape=(240, 400), n=100, u_px=-U_PX, static_amp=0.0, lifetime=10, seed=5)
    video = d / "clip.mp4"
    _write_video(video, frames)
    # Rectangular channel across Y at X = 2.0 m, 2.0 m wide (Y 0.2..2.2), 0.5 m deep.
    transect = np.array([[2.0, 0.2, 0.3], [2.0, 0.21, -0.5], [2.0, 2.19, -0.5], [2.0, 2.2, 0.3]])
    st = Station(id="synth", name="synthetic channel", grp=[], transect=transect, image_size=(400, 240),
                 alpha=0.85, scale_m_per_px=RES, flow_hint_deg=180.0, ortho_extent=(0.0, 0.0, 4.0, 2.4),
                 ortho_res_m=RES, thresholds=Thresholds(source="none"))
    clip = Clip(station_id="synth", video=video, t=datetime(2026, 1, 1), level=0.0)
    return st, clip, d


def expected_q():
    return 0.85 * (U_PX * RES * FPS) * 2.0 * 0.5  # alpha * v_surf * width * depth


def test_autopilot_publishes_correct_discharge(channel):
    st, clip, d = channel
    res = run_agent(st, clip, AutopilotBrain(), ArtifactSink(d / "auto"))
    assert res.decision.kind == "PUBLISH", res.decision.reasons
    assert res.decision.q["q"] == pytest.approx(expected_q(), rel=0.08)
    assert [s.tool for s in res.steps[:7]] == ["probe_clip", "load_frames", "check_camera_pose", "stabilize",
                                               "orthorectify", "diagnose", "coarse_motion"]


class ScriptedClient:
    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, tools, model, max_tokens=1200):
        item = self.script.pop(0) if self.script else None
        if isinstance(item, Exception):
            raise item
        calls = [{"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": __import__("json").dumps(a)}}
                 for i, (n, a) in enumerate(item or [])]
        return {"choices": [{"message": {"content": "", "tool_calls": calls}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}


def _llm(script, spent=0.0):
    return LLMBrain(model="claude-haiku-5-5", client=ScriptedClient(script), budget=Budget(3.0, MemoryStore(spent)))


GOOD = [
    [("run_lspiv", {"reason": "seeded surface"})],
    [("compute_discharge", {"method": "lspiv", "reason": "q"})],
    [("run_stiv", {"reason": "cross-check"})],
    [("compute_discharge", {"method": "stiv", "reason": "q"})],
    [("publish_gauging", {"method": "mean_of_agreeing", "basis": "methods_agree", "reason": "agree"})],
]


def test_llm_scripted_publish(channel):
    st, clip, d = channel
    res = run_agent(st, clip, _llm(GOOD), ArtifactSink(d / "llm"))
    assert res.decision.kind == "PUBLISH"
    assert res.cost_usd > 0


def test_llm_timeout_fails_closed(channel):
    st, clip, d = channel
    res = run_agent(st, clip, _llm([BrainError("timeout")]), ArtifactSink(d / "to"))
    assert res.decision.kind == "HOLD"
    assert res.decision.reasons == ["fail_closed:timeout"]


def test_budget_exhausted_makes_no_call(channel):
    st, clip, d = channel
    client = ScriptedClient(GOOD)
    brain = LLMBrain(model="claude-haiku-5-5", client=client, budget=Budget(3.0, MemoryStore(3.5)))
    res = run_agent(st, clip, brain, ArtifactSink(d / "budget"))
    assert res.decision.reasons == ["fail_closed:budget"]
    assert len(client.script) == len(GOOD)


def test_looping_brain_hits_step_budget(channel):
    st, clip, d = channel
    script = [[("compare_methods", {"reason": "again"})]] * 40
    res = run_agent(st, clip, _llm(script), ArtifactSink(d / "loop"))
    assert res.decision.kind == "HOLD" and res.decision.reasons == ["fail_closed:step_budget"]


def test_false_basis_is_denied_then_escalated(channel):
    st, clip, d = channel
    script = GOOD[:4] + [[("publish_gauging", {"method": "stiv", "basis": "bed_visible_prefers_stiv", "reason": "x"})],
                         [("escalate_to_hydrologist", {"reason": "policy refused my basis"})]]
    res = run_agent(st, clip, _llm(script), ArtifactSink(d / "basis"))
    denied = [s for s in res.steps if s.tool == "publish_gauging"][0]
    assert not denied.verdict.allowed and "basis_false:bed_not_visible" in denied.verdict.reasons
    assert res.decision.kind == "ESCALATE"


def test_policy_rejections(channel):
    st, clip, d = channel
    ctx = Context(station=st, clip=clip, sink=ArtifactSink(d / "pol"))
    pol = Policy()
    assert pol.check(ToolCall("publish_gauging", {"method": "lspiv", "basis": "methods_agree", "reason": "r"}), ctx, []
                     ).reasons[-1] == "no_discharge"
    v = pol.check(ToolCall("run_lspiv", {"ia_px": 4, "reason": "r"}), ctx, [])
    assert "param_bounds:ia_px" in v.reasons
    assert "bad_args:missing:reason" in pol.check(ToolCall("run_stiv", {}), ctx, []).reasons
    assert pol.check(ToolCall("issue_flood_warning", {}), ctx, []).reasons == ["unknown_tool:issue_flood_warning"]
    assert pol.check(ToolCall("run_stiv", {"reason": "r"}), ctx, [], decided=True).reasons == ["already_decided"]


def test_policy_refuses_negative_or_too_uncertain_discharge(channel):
    from thalweg.hydro.discharge import DischargeResult
    from thalweg.vision.field import VectorField
    import numpy as np
    st, clip, d = channel
    ctx = Context(station=st, clip=clip, sink=ArtifactSink(d / "neg"))
    from thalweg.vision.geometry import CameraModel
    ctx.cam = CameraModel.scaled(RES, st.image_size)
    from thalweg.vision.diagnose import Diagnostics
    ctx.diag = Diagnostics(5.0, 0.5, 0.1, 10.0, 0.0, 0.0, 100.0, 50.0, 1000, 100)
    f = VectorField(np.zeros(10), np.zeros(10), np.ones(10), np.zeros(10), np.ones(10), np.ones(10, bool), "lspiv")
    ctx.fields["lspiv"] = f
    ctx.results["lspiv"] = DischargeResult(q=-0.2, q_lo=-0.3, q_hi=-0.1, area=1, wetted_width=2, coverage=1.0,
                                           alpha=0.85, level=0, method="lspiv", v_mean=-0.2,
                                           uncertainty={"total_95": 1.4})
    v = Policy().check(ToolCall("publish_gauging", {"method": "lspiv", "basis": "seeded_surface_prefers_lspiv",
                                                    "reason": "r"}), ctx, [])
    assert not v.allowed
    assert any(r.startswith("discharge_not_positive") for r in v.reasons)
    assert any(r.startswith("uncertainty_high") for r in v.reasons)
