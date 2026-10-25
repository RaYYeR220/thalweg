"""POST /runs: run the agent on a clip in the ingest bucket.

Idempotent per (clip_key, brain): while a run for the pair is RUNNING the existing run_id
comes back with 200 and nothing new starts. LLM runs started by judges are capped per UTC
day through a counter in `thalweg-budget`.
"""

from __future__ import annotations

import hmac
import json
import os
import time

from botocore.exceptions import ClientError

from common import aws, config, ddb, params, runs, timefmt

from .http import HttpError, header, json_body

DEFAULT_JUDGE_LLM_CAP = 40


def _judge_model() -> str | None:
    return os.environ.get("JUDGE_MODEL") or None


def _allowed_models() -> set[str]:
    """Models a caller may ask for (deployment config); the judge default is always allowed."""
    allowed = {m.strip() for m in os.environ.get("ALLOWED_MODELS", "").split(",") if m.strip()}
    return allowed | ({_judge_model()} if _judge_model() else set())


def _requested_by(event: dict) -> str:
    """`api` for callers presenting the service token, `judge` for everyone else."""
    auth = header(event, "authorization") or ""
    if not auth.lower().startswith("bearer "):
        return "judge"
    name = os.environ.get("SERVICE_TOKEN_PARAM")
    if not name:
        return "judge"
    try:
        expected = params.get(name)
    except ClientError:
        return "judge"
    return "api" if hmac.compare_digest(auth[7:].strip().encode(), expected.encode()) else "judge"


def _charge_judge_llm_run() -> None:
    cap = int(os.environ.get("JUDGE_LLM_DAILY_CAP", DEFAULT_JUDGE_LLM_CAP))
    if cap <= 0:
        raise HttpError(429, "daily_llm_run_cap", "agent runs with the LLM brain are paused", cap=cap)
    day = timefmt.now().strftime("%Y-%m-%d")
    try:
        aws.table(config.table_budget()).update_item(
            Key={"day": day},
            UpdateExpression="ADD judge_llm_runs :one SET #ttl = if_not_exists(#ttl, :ttl)",
            ConditionExpression="attribute_not_exists(judge_llm_runs) OR judge_llm_runs < :cap",
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={":one": 1, ":cap": cap, ":ttl": int(time.time()) + 40 * 86400},
        )
    except ClientError as err:
        if ddb.is_conditional(err):
            raise HttpError(429, "daily_llm_run_cap", f"{cap} agent runs with the LLM brain per day", cap=cap) from None
        raise


def _exists(bucket: str, key: str) -> bool:
    try:
        aws.client("s3").head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def _sidecar(bucket: str, key: str) -> dict | None:
    try:
        obj = aws.client("s3").get_object(Bucket=bucket, Key=key)
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    if obj.get("ContentLength", 0) > 64 * 1024:
        raise HttpError(422, "bad_sidecar", "sidecar larger than 64 KiB")
    try:
        doc = json.loads(obj["Body"].read())
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HttpError(422, "bad_sidecar", "sidecar is not JSON") from None
    if not isinstance(doc, dict):
        raise HttpError(422, "bad_sidecar", "sidecar must be a JSON object")
    return doc


def start_run(event: dict) -> tuple[int, dict]:
    body = json_body(event)
    station_id = body.get("station_id")
    clip_key = body.get("clip_key")
    brain = body.get("brain", "autopilot")
    model = body.get("model")

    if not isinstance(station_id, str) or not station_id:
        raise HttpError(400, "bad_request", "station_id is required")
    if not isinstance(clip_key, str) or not runs.CLIP_KEY_RE.match(clip_key) or ".." in clip_key:
        raise HttpError(400, "bad_request", "clip_key must be clips/<station_id>/<file>")
    if runs.station_from_key(clip_key) != station_id:
        raise HttpError(400, "bad_request", "clip_key is not under this station's prefix")
    if clip_key.endswith(".json"):
        raise HttpError(400, "bad_request", "clip_key names the video, not the sidecar")
    if brain not in runs.BRAINS:
        raise HttpError(400, "bad_request", f"brain must be one of {list(runs.BRAINS)}")
    if brain == "llm":
        model = model or _judge_model()
        if not isinstance(model, str) or model not in _allowed_models():
            raise HttpError(400, "bad_request", f"model must be one of {sorted(_allowed_models())}")
    else:
        model = None

    station = aws.table(config.table_stations()).get_item(Key={"id": station_id}).get("Item")
    if not station:
        raise HttpError(404, "station_not_found")
    bucket = config.ingest_bucket()
    if not _exists(bucket, clip_key):
        raise HttpError(404, "clip_not_found")
    sidecar_key = runs.sidecar_key_for(clip_key)
    sidecar = _sidecar(bucket, sidecar_key)
    if sidecar is None:
        raise HttpError(422, "sidecar_missing", f"expected {sidecar_key}")
    if sidecar.get("station_id") not in (None, station_id):
        raise HttpError(400, "station_mismatch", "the sidecar names a different station")

    requested_by = _requested_by(event)
    run_id = runs.new_run_id()
    lock = runs.acquire_lock(clip_key, brain, run_id)
    if not lock.acquired:
        return 200, {"run_id": lock.holder, "status": lock.holder_status or "RUNNING", "deduplicated": True}

    try:
        if brain == "llm" and requested_by == "judge":
            _charge_judge_llm_run()
        payload = runs.build_input(
            run_id=run_id,
            station_id=station_id,
            clip_key=clip_key,
            sidecar_key=sidecar_key,
            brain=brain,
            requested_by=requested_by,
            model=model,
        )
        runs.start_execution(payload)
    except HttpError:
        runs.release_lock(clip_key, brain, run_id)
        raise
    except ClientError as err:
        runs.release_lock(clip_key, brain, run_id)
        raise HttpError(502, "start_failed", err.response.get("Error", {}).get("Code", "error")) from None
    return 202, {"run_id": run_id, "status": "RUNNING", "deduplicated": False, "brain": brain, "model": model}
