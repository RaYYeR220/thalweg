"""Persist: record the agent's decision.

Input: the Preflight output plus `agent` (the worker's activity result).
- closes the run (status = decision kind, decision, trace_key, ended, cost, timings)
- writes the gauging when the decision is PUBLISH
- updates the station's latest fields
- emits the decision metrics

The worker's result is validated in full before anything is written. A result
that breaks the contract raises AgentOutputInvalid and the run fails closed.
A warning draft is only carried forward after a PUBLISH; on HOLD/ESCALATE it is
dropped and the run records why.
"""

from __future__ import annotations

import math
import re
from datetime import timedelta

from botocore.exceptions import ClientError

from common import aws, config, ddb, emf, jsonx, timefmt

from .errors import AgentOutputInvalid

KINDS = ("PUBLISH", "HOLD", "ESCALATE")
WARNING_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")
WARNING_TTL = timedelta(hours=6)


def _num(value, name: str, *, required: bool = False, minimum: float | None = None) -> float | None:
    if value is None:
        if required:
            raise AgentOutputInvalid(f"{name} is required")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AgentOutputInvalid(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise AgentOutputInvalid(f"{name} must be >= {minimum}")
    return float(value)


def _key(value, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or ".." in value or value.startswith("/"):
        raise AgentOutputInvalid(f"{name} must be a plain object key")
    return value


def _strs(value, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise AgentOutputInvalid(f"{name} must be a list of strings")
    return value


def _time(value, name: str, default: str) -> str:
    if value is None:
        return default
    try:
        return timefmt.normalize(value)
    except (ValueError, TypeError):
        raise AgentOutputInvalid(f"{name} must be an ISO 8601 timestamp") from None


def validate(run: dict, agent) -> dict:
    """Return a normalized copy of the worker result or raise AgentOutputInvalid."""
    if not isinstance(agent, dict):
        raise AgentOutputInvalid("agent result must be an object")
    if agent.get("run_id") not in (None, run["run_id"]):
        raise AgentOutputInvalid("agent result is for a different run")

    decision = agent.get("decision")
    if not isinstance(decision, dict) or decision.get("kind") not in KINDS:
        raise AgentOutputInvalid(f"decision.kind must be one of {KINDS}")
    kind = decision["kind"]
    norm_decision = {
        "kind": kind,
        "reasons": _strs(decision.get("reasons"), "decision.reasons"),
        "q": _num(decision.get("q"), "decision.q", minimum=0),
        "q_lo": _num(decision.get("q_lo"), "decision.q_lo", minimum=0),
        "q_hi": _num(decision.get("q_hi"), "decision.q_hi", minimum=0),
        "method": decision.get("method") if isinstance(decision.get("method"), str) else None,
    }

    gauging = None
    if kind == "PUBLISH":
        g = agent.get("gauging")
        if not isinstance(g, dict):
            raise AgentOutputInvalid("PUBLISH requires a gauging")
        if g.get("station_id") not in (None, run["station_id"]):
            raise AgentOutputInvalid("gauging is for a different station")
        q = _num(g.get("q"), "gauging.q", required=True, minimum=0)
        q_lo = _num(g.get("q_lo"), "gauging.q_lo", minimum=0)
        q_hi = _num(g.get("q_hi"), "gauging.q_hi", minimum=0)
        if (q_lo is not None and q_lo > q) or (q_hi is not None and q_hi < q):
            raise AgentOutputInvalid("gauging must satisfy q_lo <= q <= q_hi")
        method = g.get("method")
        if not isinstance(method, str) or not method:
            raise AgentOutputInvalid("gauging.method is required")
        gauging = {
            "station_id": run["station_id"],
            "t": _time(g.get("t"), "gauging.t", run["t"]),
            "level": _num(g.get("level"), "gauging.level") if g.get("level") is not None else run["level"],
            "q": q,
            "q_lo": q_lo,
            "q_hi": q_hi,
            "method": method,
            "run_id": run["run_id"],
            "brain": run["brain"],
        }
    elif agent.get("gauging") is not None:
        raise AgentOutputInvalid(f"{kind} must not carry a gauging")

    draft = agent.get("warning_draft")
    dropped = None
    if draft is not None:
        if kind != "PUBLISH":
            dropped, draft = "not_published", None
        else:
            draft = _validate_draft(run, draft)

    timings = agent.get("timings")
    if timings is not None and not isinstance(timings, dict):
        raise AgentOutputInvalid("timings must be an object")
    denials = agent.get("policy_denials")
    if denials is not None and (isinstance(denials, bool) or not isinstance(denials, int) or denials < 0):
        raise AgentOutputInvalid("policy_denials must be a non-negative integer")

    return {
        "decision": norm_decision,
        "gauging": gauging,
        "warning_draft": draft,
        "warning_dropped": dropped,
        "trace_key": _key(agent.get("trace_key"), "trace_key"),
        "cost_usd": _num(agent.get("cost_usd"), "cost_usd", minimum=0) or 0.0,
        "timings": timings,
        "policy_denials": denials,
    }


def _validate_draft(run: dict, d) -> dict:
    if not isinstance(d, dict):
        raise AgentOutputInvalid("warning_draft must be an object")
    wid = d.get("id")
    if not isinstance(wid, str) or not WARNING_ID_RE.match(wid):
        raise AgentOutputInvalid("warning_draft.id is required (letters, digits, _.:-)")
    if d.get("station_id") not in (None, run["station_id"]):
        raise AgentOutputInvalid("warning_draft is for a different station")
    text = d.get("text")
    if not isinstance(text, str) or not text.strip():
        raise AgentOutputInvalid("warning_draft.text is required")
    created = _time(d.get("created"), "warning_draft.created", timefmt.iso())
    expires = _time(d.get("expires"), "warning_draft.expires", timefmt.iso(timefmt.parse(created) + WARNING_TTL))
    p_cross = _num(d.get("p_cross"), "warning_draft.p_cross", minimum=0)
    if p_cross is not None and p_cross > 1:
        raise AgentOutputInvalid("warning_draft.p_cross must be in [0, 1]")
    return {
        "id": wid,
        "station_id": run["station_id"],
        "run_id": run["run_id"],
        "created": created,
        "level_now": _num(d.get("level_now"), "warning_draft.level_now"),
        "q_now": _num(d.get("q_now"), "warning_draft.q_now", minimum=0),
        "eta_s": _num(d.get("eta_s"), "warning_draft.eta_s", minimum=0),
        "p_cross": p_cross,
        "text": text.strip()[:4000],
        "evidence": [_key(k, "warning_draft.evidence[]") for k in _strs(d.get("evidence"), "warning_draft.evidence")],
        "expires": expires,
    }


def handler(event: dict, _context=None) -> dict:
    run = {k: event.get(k) for k in ("run_id", "station_id", "t", "level", "brain", "started", "clip_key")}
    if not run["run_id"] or not run["station_id"]:
        raise AgentOutputInvalid("persist input lacks run_id/station_id")
    result = validate(run, event.get("agent"))
    kind = result["decision"]["kind"]
    ended = timefmt.iso()
    try:
        latency_s = (timefmt.parse(ended) - timefmt.parse(run["started"])).total_seconds()
    except (ValueError, TypeError):
        latency_s = None

    if result["gauging"]:
        aws.table(config.table_gaugings()).put_item(Item=jsonx.to_ddb(result["gauging"]))

    run_fields = {
        "status": kind,
        "decision": result["decision"],
        "trace_key": result["trace_key"],
        "ended": ended,
        "cost_usd": result["cost_usd"],
        "timings": result["timings"],
        "latency_s": latency_s,
        "policy_denials": result["policy_denials"],
        "warning_id": result["warning_draft"]["id"] if result["warning_draft"] else None,
        "warning_dropped": result["warning_dropped"],
    }
    if result["gauging"]:
        run_fields["gauging"] = {k: result["gauging"][k] for k in ("t", "level", "q", "q_lo", "q_hi", "method")}
    ddb.update_fields(config.table_runs(), {"run_id": run["run_id"]}, run_fields)

    station_fields = {
        "last_t": run["t"],
        "last_level": run["level"],
        "last_decision": kind,
        "last_run_id": run["run_id"],
        "last_run_ended": ended,
    }
    if result["gauging"]:
        station_fields.update(
            last_q=result["gauging"]["q"],
            last_q_lo=result["gauging"]["q_lo"],
            last_q_hi=result["gauging"]["q_hi"],
            last_q_t=result["gauging"]["t"],
            last_method=result["gauging"]["method"],
        )
    try:
        ddb.update_fields(config.table_stations(), {"id": run["station_id"]}, station_fields, condition="attribute_exists(id)")
    except ClientError as err:
        if not ddb.is_conditional(err):
            raise

    props = {"run_id": run["run_id"], "station_id": run["station_id"], "brain": run["brain"]}
    emf.decision(kind, **props)
    emf.run_metrics(latency_s, result["cost_usd"], result["policy_denials"], **props)

    out = {"run_id": run["run_id"], "station_id": run["station_id"], "kind": kind}
    if result["warning_draft"]:
        out["warning_draft"] = result["warning_draft"]
    return out

