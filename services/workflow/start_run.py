"""StartRun: an `Object Created` event for `clips/<station_id>/<stamp>.json` starts a gauging.

The sidecar may choose the brain (`autopilot` by default), the model and who asked for the
run. The run id's suffix is derived from the object's identity, so a duplicate delivery of
the same S3 event maps onto the same execution name instead of a second run.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import unquote_plus

from botocore.exceptions import ClientError

from common import aws, config, runs, timefmt

log = logging.getLogger()
log.setLevel(logging.INFO)

MAX_SIDECAR_BYTES = 64 * 1024


def _read_json(bucket: str, key: str) -> dict:
    s3 = aws.client("s3")
    obj = s3.get_object(Bucket=bucket, Key=key)
    if obj.get("ContentLength", 0) > MAX_SIDECAR_BYTES:
        raise ValueError("sidecar larger than 64 KiB")
    doc = json.loads(obj["Body"].read())
    if not isinstance(doc, dict):
        raise ValueError("sidecar must be a JSON object")
    return doc


def handler(event: dict, _context=None) -> dict:
    detail = event.get("detail") or {}
    bucket = (detail.get("bucket") or {}).get("name")
    obj = detail.get("object") or {}
    key = obj.get("key") or ""
    if bucket != config.ingest_bucket():
        return {"skipped": "other_bucket"}
    if not runs.CLIP_KEY_RE.match(key) or not key.endswith(".json"):
        key = unquote_plus(key)
        if not runs.CLIP_KEY_RE.match(key) or not key.endswith(".json"):
            log.info("ignored key %s", key)
            return {"skipped": "not_a_sidecar", "key": key}

    try:
        sidecar = _read_json(bucket, key)
    except (ClientError, ValueError, json.JSONDecodeError) as err:
        log.warning("unreadable sidecar %s: %s", key, err)
        return {"skipped": "bad_sidecar", "key": key}

    station_id = sidecar.get("station_id") or runs.station_from_key(key)
    if station_id != runs.station_from_key(key):
        log.warning("sidecar %s names station %r outside its prefix", key, station_id)
        return {"skipped": "station_mismatch", "key": key}

    brain = sidecar.get("brain") if sidecar.get("brain") in runs.BRAINS else "autopilot"
    requested_by = sidecar.get("requested_by") if sidecar.get("requested_by") in runs.REQUESTERS else "schedule"
    model = sidecar.get("model")
    if not (isinstance(model, str) and runs.MODEL_RE.match(model)):
        model = None
    clip_key = runs.clip_key_for(key, sidecar)

    try:
        at = timefmt.parse(event.get("time") or "")
    except ValueError:
        at = timefmt.now()
    seed = f"{bucket}/{key}|{obj.get('etag', '')}|{obj.get('sequencer', '')}"
    run_id = runs.new_run_id(at, seed=seed)

    lock = runs.acquire_lock(clip_key, brain, run_id)
    if not lock.acquired:
        log.info("clip %s (%s) already in flight as %s", clip_key, brain, lock.holder)
        return {"skipped": "in_flight", "run_id": lock.holder}

    payload = runs.build_input(
        run_id=run_id,
        station_id=station_id,
        clip_key=clip_key,
        sidecar_key=key,
        brain=brain,
        requested_by=requested_by,
        model=model,
    )
    try:
        arn = runs.start_execution(payload)
    except Exception:
        runs.release_lock(clip_key, brain, run_id)
        raise
    log.info("started %s for %s", run_id, clip_key)
    return {"run_id": run_id, "execution_arn": arn}
