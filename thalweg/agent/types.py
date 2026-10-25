"""Agent data types: tool calls, policy verdicts, trace steps and the final decision."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

DecisionKind = Literal["PUBLISH", "HOLD", "ESCALATE"]


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)
    reason: str = ""
    call_id: str | None = None


@dataclass
class Verdict:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class Step:
    i: int
    actor: str  # "system" | "autopilot" | "llm"
    tool: str
    args: dict
    reason: str
    verdict: Verdict
    ok: bool | None = None
    summary: dict | None = None
    evidence: list[str] = field(default_factory=list)
    error: str | None = None
    t_ms: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class Decision:
    kind: DecisionKind
    reasons: list[str]
    method: str | None = None
    basis: str | None = None
    q: dict | None = None  # DischargeResult.summary() of the published method (or the best estimate when held)
    recapture_spec: dict | None = None
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunResult:
    run_id: str
    station_id: str
    clip: str
    brain: str
    model: str | None
    decision: Decision
    steps: list[Step]
    timings: dict
    cost_usd: float
    started: str
    ended: str
    notes: list[str] = field(default_factory=list)
    estimates: dict = field(default_factory=dict)  # every method's Q, published or not

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id, "station_id": self.station_id, "clip": self.clip, "brain": self.brain,
            "model": self.model, "decision": self.decision.to_dict(), "steps": [s.to_dict() for s in self.steps],
            "timings": self.timings, "cost_usd": round(self.cost_usd, 5), "started": self.started,
            "ended": self.ended, "notes": self.notes, "estimates": self.estimates,
        }
