"""FailClosed: any error or timeout before a decision is persisted ends the run in HOLD.

Input: the state input at the failure point plus `error` = {"Error", "Cause"} from the Catch.
The run is upserted (Preflight may not have written it) with
status HOLD and reason `fail_closed:<error>`. Never a guessed number.
"""

from __future__ import annotations

import json
import re

from botocore.exceptions import ClientError

from common import aws, config, ddb, emf, timefmt

# Step Functions' own error names, spelled the way the agent's reasons are.
_STATES_ERRORS = {
    "States.Timeout": "timeout",
    "States.HeartbeatTimeout": "heartbeat_timeout",
    "States.TaskFailed": "task_failed",
    "States.Permissions": "permissions",
    "States.ALL": "error",
}
_SAFE = re.compile(r"[^A-Za-z0-9_.:\-]")


def reason_for(error: str | None) -> str:
    name = _STATES_ERRORS.get(error or "", error or "unknown")
    return "fail_closed:" + (_SAFE.sub("_", name)[:80] or "unknown")


def _cause_text(cause) -> str | None:
    if cause is None:
        return None
    text = cause if isinstance(cause, str) else json.dumps(cause)
    try:  # Lambda errors arrive as a JSON string {"errorMessage": ...}
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("errorMessage"):
            text = str(parsed["errorMessage"])
    except (json.JSONDecodeError, TypeError):
        pass
    return text[:1000]


def handler(event: dict, _context=None) -> dict:
    inp = event.get("input", event) if isinstance(event.get("input"), dict) else event
    err = event.get("error") or {}
    run_id = inp.get("run_id")
    station_id = inp.get("station_id")
    reason = reason_for(err.get("Error"))
    cause = _cause_text(err.get("Cause"))
    ended = timefmt.iso()

    if run_id:
        fields = {
            "status": "HOLD",
            "decision": {"kind": "HOLD", "reasons": [reason]},
            "ended": ended,
            "error": {"error": err.get("Error"), "cause": cause},
        }
        # Keep what Preflight would have recorded if it never ran.
        for k in ("station_id", "clip_key", "sidecar_key", "brain", "model", "requested_by"):
            if inp.get(k) is not None:
                fields[k] = inp[k]
        ddb.update_fields(config.table_runs(), {"run_id": run_id}, fields)
        _ensure_started(run_id, ended)
        _withdraw_gauging(inp, run_id)

    if station_id:
        try:
            ddb.update_fields(
                config.table_stations(),
                {"id": station_id},
                {"last_decision": "HOLD", "last_run_id": run_id, "last_run_ended": ended},
                condition="attribute_exists(id)",
            )
        except ClientError as e:
            if not ddb.is_conditional(e):
                raise

    emf.decision("HOLD", run_id=run_id, station_id=station_id, reason=reason)
    return {"run_id": run_id, "status": "HOLD", "reason": reason, "cause": cause}


def _withdraw_gauging(inp: dict, run_id: str) -> None:
    """If Persist wrote this run's gauging and then failed, take the number back down."""
    agent = inp.get("agent") if isinstance(inp.get("agent"), dict) else {}
    gauging = agent.get("gauging") if isinstance(agent.get("gauging"), dict) else {}
    station_id = inp.get("station_id")
    t = gauging.get("t") or inp.get("t")
    if not station_id or not t:
        return
    try:
        t = timefmt.normalize(t)
    except (ValueError, TypeError):
        return
    try:
        aws.table(config.table_gaugings()).delete_item(
            Key={"station_id": station_id, "t": t},
            ConditionExpression="run_id = :r",
            ExpressionAttributeValues={":r": run_id},
        )
    except ClientError as e:
        if not ddb.is_conditional(e):
            raise


def _ensure_started(run_id: str, ended: str) -> None:
    """Set `started` only when Preflight never ran (so the run still sorts in by_station)."""
    try:
        ddb.update_fields(
            config.table_runs(),
            {"run_id": run_id},
            {"started": ended},
            condition="attribute_not_exists(#st)",
            names={"#st": "started"},
        )
    except ClientError as e:
        if not ddb.is_conditional(e):
            raise
