"""Starting gauging runs: ids, the in-flight lock, and the execution input.

One run per (clip_key, brain) may be in flight. The lock is an item in
`thalweg-runs` keyed `lock#<sha1(clip_key|brain)>`; it names the run holding it
and carries a 15-minute TTL. A lock whose run already reached a decision (or
whose TTL passed) is taken over by the next caller.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime

from botocore.exceptions import ClientError

from . import aws, config, timefmt

BRAINS = ("autopilot", "llm")
REQUESTERS = ("schedule", "judge", "api")
LOCK_TTL_S = 15 * 60
RUN_ID_RE = re.compile(r"^r-\d{14}-[0-9a-f]{6}$")
CLIP_KEY_RE = re.compile(r"^clips/[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}/[A-Za-z0-9_.\-]{1,128}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}$")


def new_run_id(at: datetime | None = None, seed: str | None = None) -> str:
    """`r-<YYYYMMDDHHMMSS>-<6 hex>`. A seed makes the suffix deterministic (event dedup)."""
    at = at or timefmt.now()
    suffix = hashlib.sha1(seed.encode()).hexdigest()[:6] if seed else secrets.token_hex(3)
    return f"r-{at.strftime('%Y%m%d%H%M%S')}-{suffix}"


def sidecar_key_for(clip_key: str) -> str:
    stem = clip_key.rsplit(".", 1)[0] if "." in clip_key.rsplit("/", 1)[-1] else clip_key
    return stem + ".json"


def clip_key_for(sidecar_key: str, sidecar: dict) -> str:
    explicit = sidecar.get("clip_key")
    if isinstance(explicit, str) and explicit:
        return explicit
    return sidecar_key[: -len(".json")] + ".mp4" if sidecar_key.endswith(".json") else sidecar_key + ".mp4"


def station_from_key(key: str) -> str | None:
    parts = key.split("/")
    return parts[1] if len(parts) >= 3 and parts[0] == "clips" else None


def lock_key(clip_key: str, brain: str) -> str:
    return "lock#" + hashlib.sha1(f"{clip_key}|{brain}".encode()).hexdigest()


@dataclass
class LockResult:
    acquired: bool
    holder: str  # run_id that holds the lock (ours when acquired)
    holder_status: str | None = None


def _runs():
    return aws.table(config.table_runs())


def _is_conditional(err: ClientError) -> bool:
    return err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def acquire_lock(clip_key: str, brain: str, run_id: str, now_s: int | None = None) -> LockResult:
    now_s = int(now_s if now_s is not None else time.time())
    pk = lock_key(clip_key, brain)
    item = {
        "run_id": pk,
        "holder": run_id,
        "clip_key": clip_key,
        "brain": brain,
        "created": timefmt.iso(),
        "ttl": now_s + LOCK_TTL_S,
    }
    try:
        _runs().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(run_id) OR #ttl < :now",
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={":now": now_s},
        )
        return LockResult(True, run_id)
    except ClientError as err:
        if not _is_conditional(err):
            raise

    current = _runs().get_item(Key={"run_id": pk}, ConsistentRead=True).get("Item")
    if not current:  # released between our put and get: try once more
        return acquire_lock(clip_key, brain, run_id, now_s)
    holder = str(current["holder"])
    run = _runs().get_item(Key={"run_id": holder}, ConsistentRead=True).get("Item")
    status = str(run["status"]) if run and "status" in run else None
    # No run item yet means the execution is between StartExecution and Preflight: still in flight.
    if status is None or status == "RUNNING":
        return LockResult(False, holder, status or "RUNNING")

    try:
        _runs().put_item(
            Item=item,
            ConditionExpression="holder = :old",
            ExpressionAttributeValues={":old": holder},
        )
        return LockResult(True, run_id)
    except ClientError as err:
        if not _is_conditional(err):
            raise
        latest = _runs().get_item(Key={"run_id": pk}, ConsistentRead=True).get("Item") or {}
        return LockResult(False, str(latest.get("holder", holder)), "RUNNING")


def release_lock(clip_key: str, brain: str, run_id: str) -> None:
    try:
        _runs().delete_item(
            Key={"run_id": lock_key(clip_key, brain)},
            ConditionExpression="holder = :me",
            ExpressionAttributeValues={":me": run_id},
        )
    except ClientError as err:
        if not _is_conditional(err):
            raise


def build_input(
    *,
    run_id: str,
    station_id: str,
    clip_key: str,
    sidecar_key: str,
    brain: str,
    requested_by: str,
    model: str | None = None,
) -> dict:
    if brain not in BRAINS:
        raise ValueError(f"brain must be one of {BRAINS}")
    if requested_by not in REQUESTERS:
        raise ValueError(f"requested_by must be one of {REQUESTERS}")
    payload = {
        "run_id": run_id,
        "station_id": station_id,
        "clip_key": clip_key,
        "sidecar_key": sidecar_key,
        "brain": brain,
        "requested_by": requested_by,
    }
    if model:
        payload["model"] = model
    return payload


def start_execution(payload: dict) -> str:
    """Start `thalweg-gauging` with the run id as the execution name. Returns the execution ARN."""
    sfn = aws.client("stepfunctions")
    try:
        resp = sfn.start_execution(
            stateMachineArn=config.state_machine_arn(),
            name=payload["run_id"],
            input=json.dumps(payload),
        )
        return resp["executionArn"]
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ExecutionAlreadyExists":
            # Same name and same input is a duplicate delivery of the same request.
            arn = config.state_machine_arn().replace(":stateMachine:", ":execution:")
            return f"{arn}:{payload['run_id']}"
        raise
