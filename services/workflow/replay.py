"""Replay: keep the demo station gauging by feeding it the next archived clip.

Reads s3://<artifacts>/data/dart/replay-manifest.json
    {"station_id": "dart", "clips": [{"video_key": "...", "t": "...", "level": 1.23}, ...]}
takes the clip at the cursor (`replay_cursor` on the station item), copies it to
clips/<station_id>/<YYYYMMDDTHHMMSS>.mp4 in the ingest bucket, then writes the sidecar,
which is what triggers the run. The cursor wraps around at the end of the manifest.
"""

from __future__ import annotations

import json
import logging
import os

from botocore.exceptions import ClientError

from common import aws, config, ddb, timefmt

log = logging.getLogger()
log.setLevel(logging.INFO)

MANIFEST_KEY = os.environ.get("REPLAY_MANIFEST_KEY", "data/dart/replay-manifest.json")
REPLAY_MODEL = os.environ.get("REPLAY_MODEL")  # deployment config (ThalwegFlow)


def _manifest() -> dict:
    body = aws.client("s3").get_object(Bucket=config.artifacts_bucket(), Key=MANIFEST_KEY)["Body"].read()
    doc = json.loads(body)
    if not isinstance(doc, dict) or not isinstance(doc.get("clips"), list) or not doc["clips"]:
        raise ValueError("replay manifest must have a non-empty clips list")
    return doc


def handler(event: dict | None = None, _context=None) -> dict:
    manifest = _manifest()
    station_id = str(manifest.get("station_id") or "dart")
    clips = manifest["clips"]
    stations = aws.table(config.table_stations())

    station = stations.get_item(Key={"id": station_id}, ConsistentRead=True).get("Item")
    if not station:
        log.warning("station %s is not registered; replay skipped", station_id)
        return {"skipped": "station_missing", "station_id": station_id}
    cursor = int(station.get("replay_cursor", 0)) % len(clips)
    nxt = (cursor + 1) % len(clips)

    # Advance first, conditionally, so two overlapping invocations never send the same clip twice.
    try:
        if "replay_cursor" in station:
            cond, vals = "replay_cursor = :old", {":old": int(station["replay_cursor"])}
        else:
            cond, vals = "attribute_not_exists(replay_cursor)", None
        ddb.update_fields(config.table_stations(), {"id": station_id}, {"replay_cursor": nxt}, condition=cond, values=vals)
    except ClientError as err:
        if ddb.is_conditional(err):
            return {"skipped": "cursor_moved", "station_id": station_id}
        raise

    clip = clips[cursor]
    t = timefmt.parse(clip["t"])
    stamp = timefmt.compact(t)
    clip_key = f"clips/{station_id}/{stamp}.mp4"
    sidecar_key = f"clips/{station_id}/{stamp}.json"

    s3 = aws.client("s3")
    s3.copy_object(
        Bucket=config.ingest_bucket(),
        Key=clip_key,
        CopySource={"Bucket": config.artifacts_bucket(), "Key": clip["video_key"]},
        ContentType="video/mp4",
        MetadataDirective="REPLACE",
    )
    sidecar = {
        "station_id": station_id,
        "t": timefmt.iso(t),
        "level": float(clip["level"]),
        "source": f"replay:{clip['video_key']}",
        "brain": "llm",
        "requested_by": "schedule",
        "replay_index": cursor,
        "replayed_at": timefmt.iso(),
    }
    if REPLAY_MODEL:
        sidecar["model"] = REPLAY_MODEL
    s3.put_object(
        Bucket=config.ingest_bucket(),
        Key=sidecar_key,
        Body=json.dumps(sidecar).encode(),
        ContentType="application/json",
    )
    log.info("replayed clip %d/%d -> %s", cursor + 1, len(clips), clip_key)
    return {"station_id": station_id, "index": cursor, "next": nxt, "clip_key": clip_key, "sidecar_key": sidecar_key}
