"""Preflight: check the station, the clip and its sidecar, then open the run record.

Input (state machine Payload): {"input": <execution input>, "execution_arn": "..."}
Output: the execution input enriched with the sidecar's `t`, `level`, `source`,
the run's `started` time and the bucket names. This is what the Graviton worker
receives as its activity input.
"""

from __future__ import annotations

import json
import math
import re

from botocore.exceptions import ClientError

from common import aws, config, jsonx, runs, timefmt

from .errors import BadInput, BadSidecar, ClipNotReady, StationNotFound

STATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
MAX_SIDECAR_BYTES = 64 * 1024


def _validate(inp: dict) -> dict:
    required = ("run_id", "station_id", "clip_key", "sidecar_key", "brain", "requested_by")
    missing = [k for k in required if not isinstance(inp.get(k), str) or not inp.get(k)]
    if missing:
        raise BadInput(f"missing or empty: {', '.join(missing)}")
    if not runs.RUN_ID_RE.match(inp["run_id"]):
        raise BadInput("run_id must look like r-YYYYMMDDHHMMSS-xxxxxx")
    if not STATION_RE.match(inp["station_id"]):
        raise BadInput("station_id has invalid characters")
    if inp["brain"] not in runs.BRAINS:
        raise BadInput(f"brain must be one of {runs.BRAINS}")
    if inp["requested_by"] not in runs.REQUESTERS:
        raise BadInput(f"requested_by must be one of {runs.REQUESTERS}")
    model = inp.get("model")
    if model is not None and (not isinstance(model, str) or not runs.MODEL_RE.match(model)):
        raise BadInput("model has invalid characters")
    for key in ("clip_key", "sidecar_key"):
        if ".." in inp[key] or inp[key].startswith("/"):
            raise BadInput(f"{key} is not a plain object key")
    return inp


def _head(bucket: str, key: str) -> dict:
    try:
        return aws.client("s3").head_object(Bucket=bucket, Key=key)
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            raise ClipNotReady(f"s3://{bucket}/{key} not found") from None
        raise


def _read_sidecar(bucket: str, key: str) -> dict:
    head = _head(bucket, key)
    if head.get("ContentLength", 0) > MAX_SIDECAR_BYTES:
        raise BadSidecar("sidecar larger than 64 KiB")
    body = aws.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    try:
        doc = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise BadSidecar(f"sidecar is not JSON: {err}") from None
    if not isinstance(doc, dict):
        raise BadSidecar("sidecar must be a JSON object")
    return doc


def handler(event: dict, _context=None) -> dict:
    inp = event.get("input", event)
    if not isinstance(inp, dict):
        raise BadInput("input must be an object")
    inp = _validate(dict(inp))

    station = aws.table(config.table_stations()).get_item(Key={"id": inp["station_id"]}).get("Item")
    if not station:
        raise StationNotFound(inp["station_id"])

    bucket = config.ingest_bucket()
    clip_head = _head(bucket, inp["clip_key"])
    sidecar = _read_sidecar(bucket, inp["sidecar_key"])

    if sidecar.get("station_id") not in (None, inp["station_id"]):
        raise BadSidecar(f"sidecar station_id {sidecar.get('station_id')!r} != {inp['station_id']!r}")
    try:
        t = timefmt.normalize(sidecar["t"])
    except (KeyError, ValueError, TypeError):
        raise BadSidecar("sidecar t must be an ISO 8601 timestamp") from None
    level = sidecar.get("level")
    if isinstance(level, bool) or not isinstance(level, (int, float)) or not math.isfinite(level):
        raise BadSidecar("sidecar level must be a finite number (metres, station datum)")

    started = timefmt.iso()
    run_item = {
        "run_id": inp["run_id"],
        "station_id": inp["station_id"],
        "clip_key": inp["clip_key"],
        "sidecar_key": inp["sidecar_key"],
        "brain": inp["brain"],
        "model": inp.get("model"),
        "requested_by": inp["requested_by"],
        "status": "RUNNING",
        "started": started,
        "t": t,
        "level": float(level),
        "source": sidecar.get("source"),
        "execution_arn": event.get("execution_arn"),
    }
    aws.table(config.table_runs()).put_item(Item=jsonx.to_ddb(run_item))

    out = {
        **inp,
        "t": t,
        "level": float(level),
        "source": sidecar.get("source"),
        "started": started,
        "clip_bytes": int(clip_head.get("ContentLength", 0)),
        "ingest_bucket": bucket,
        "artifacts_bucket": config.artifacts_bucket(),
    }
    if station.get("config_key"):
        out["station_config_key"] = str(station["config_key"])
    return out
