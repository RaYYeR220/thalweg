"""Read endpoints: stations, gaugings, runs."""

from __future__ import annotations

import re

from boto3.dynamodb.conditions import Attr, Key

from common import aws, config, jsonx, timefmt

from .http import HttpError, int_param, path_param, query

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")
INTERNAL_RUN_FIELDS = ("execution_arn",)


def _id(event: dict, name: str = "id") -> str:
    value = path_param(event, name)
    if not ID_RE.match(value):
        raise HttpError(400, "bad_path", f"{name} has invalid characters")
    return value


def _scan_all(table, **kwargs) -> list[dict]:
    items, start = [], None
    while True:
        if start:
            kwargs["ExclusiveStartKey"] = start
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        start = resp.get("LastEvaluatedKey")
        if not start:
            return items


def list_stations(event: dict) -> tuple[int, dict]:
    items = _scan_all(aws.table(config.table_stations()))
    items.sort(key=lambda it: str(it.get("id")))
    return 200, {"stations": jsonx.plain(items)}


def get_station(event: dict) -> tuple[int, dict]:
    sid = _id(event)
    item = aws.table(config.table_stations()).get_item(Key={"id": sid}).get("Item")
    if not item:
        raise HttpError(404, "station_not_found")
    return 200, jsonx.plain(item)


def list_gaugings(event: dict) -> tuple[int, dict]:
    sid = _id(event)
    q = query(event)
    limit = int_param(q, "limit", 500, 1, 5000)
    table = aws.table(config.table_gaugings())
    since = q.get("since")
    if since:
        try:
            since = timefmt.normalize(since)
        except ValueError:
            raise HttpError(400, "bad_query", "since must be an ISO 8601 timestamp") from None
        cond = Key("station_id").eq(sid) & Key("t").gte(since)
        forward = True
    else:
        # Without `since`, the most recent `limit` gaugings (returned oldest first).
        cond = Key("station_id").eq(sid)
        forward = False
    items, start = [], None
    while len(items) < limit:
        kwargs = {"KeyConditionExpression": cond, "ScanIndexForward": forward, "Limit": limit - len(items)}
        if start:
            kwargs["ExclusiveStartKey"] = start
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        start = resp.get("LastEvaluatedKey")
        if not start:
            break
    if not forward:
        items.reverse()
    return 200, {"station_id": sid, "count": len(items), "gaugings": jsonx.plain(items)}


def _public_run(item: dict) -> dict:
    return {k: v for k, v in jsonx.plain(item).items() if k not in INTERNAL_RUN_FIELDS}


def list_runs(event: dict) -> tuple[int, dict]:
    q = query(event)
    limit = int_param(q, "limit", 50, 1, 200)
    table = aws.table(config.table_runs())
    station = q.get("station")
    if station:
        if not ID_RE.match(station):
            raise HttpError(400, "bad_query", "station has invalid characters")
        resp = table.query(
            IndexName="by_station",
            KeyConditionExpression=Key("station_id").eq(station),
            ScanIndexForward=False,
            Limit=limit,
        )
        items = resp.get("Items", [])
    else:
        items = _scan_all(table, FilterExpression=Attr("run_id").begins_with("r-"))
        items.sort(key=lambda it: str(it.get("started", "")), reverse=True)
        items = items[:limit]
    return 200, {"runs": [_public_run(it) for it in items]}


def get_run(event: dict) -> tuple[int, dict]:
    rid = _id(event)
    if not rid.startswith("r-"):
        raise HttpError(404, "run_not_found")
    item = aws.table(config.table_runs()).get_item(Key={"run_id": rid}).get("Item")
    if not item:
        raise HttpError(404, "run_not_found")
    run = _public_run(item)
    # While the run is in flight the worker streams its trace to the conventional key.
    trace_key = run.get("trace_key") or f"runs/{rid}/trace.json"
    run["trace_url"] = "/artifacts/" + trace_key if trace_key.startswith("runs/") else None
    run["trace_presigned_url"] = aws.client("s3").generate_presigned_url(
        "get_object",
        Params={"Bucket": config.artifacts_bucket(), "Key": trace_key},
        ExpiresIn=3600,
    )
    return 200, run
