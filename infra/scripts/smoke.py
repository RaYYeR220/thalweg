"""Post-deploy smoke test for the Thalweg stacks.

Plays the Graviton worker's part by hand (GetActivityTask / SendTask*), so it can run before
the worker exists. Uses a throwaway station `smoke-test` and removes everything it wrote.

  1. GET /api/health, /api/stations through the front door (CloudFront, or the site API)
  2. a sidecar lands in ingest -> EventBridge -> StartRun -> execution reaches the Agent activity
  3. POST /api/runs on the same clip + brain returns the in-flight run (200, deduplicated)
  4. the agent task fails -> FailClosed -> run HOLD `fail_closed:<error>`, execution FAILED
  5. a second clip: the agent PUBLISHes with a warning draft -> gauging written, DRAFT waits
  6. POST /api/reviews/{id}: wrong passcode 403, sign 200, sign again 409 -> SIGNED,
     public/warnings.json served at /artifacts/public/warnings.json
  7. /artifacts/data/... stays private (404)

  python infra/scripts/smoke.py --profile <p> [--keep]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

STATION = "smoke-test"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}", flush=True)
    return ok


def http(method: str, url: str, body=None) -> tuple[int, dict | str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw, status = resp.read().decode(), resp.status
    except urllib.error.HTTPError as err:
        raw, status = err.read().decode(), err.code
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def outputs(cfn, stack: str) -> dict:
    out = cfn.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in out}


def wait_for(fn, timeout_s: float, every_s: float = 2.0):
    end = time.time() + timeout_s
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(every_s)
    return None


def take_agent_task(sfn, activity_arn: str, run_id: str, timeout_s: float = 120) -> dict | None:
    end = time.time() + timeout_s
    while time.time() < end:
        task = sfn.get_activity_task(activityArn=activity_arn, workerName="smoke")
        if task.get("taskToken"):
            inp = json.loads(task["input"])
            if inp.get("run_id") == run_id:
                return {"token": task["taskToken"], "input": inp}
            # Not ours (should not happen with the replay schedule off): hand it back as failed.
            sfn.send_task_failure(taskToken=task["taskToken"], error="SmokeForeignTask", cause="smoke test picked up a foreign task")
    return None


def execution_status(sfn, sm_arn: str, run_id: str) -> str:
    arn = sm_arn.replace(":stateMachine:", ":execution:") + ":" + run_id
    return sfn.describe_execution(executionArn=arn)["status"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--keep", action="store_true", help="leave the smoke-test records in place")
    args = ap.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    cfn, s3, sfn, ssm = (session.client(n) for n in ("cloudformation", "s3", "stepfunctions", "ssm"))
    ddb = session.resource("dynamodb")
    data, flow = outputs(cfn, "ThalwegData"), outputs(cfn, "ThalwegFlow")
    try:
        site = f"https://{outputs(cfn, 'ThalwegWeb')['DistributionDomain']}"
    except cfn.exceptions.ClientError:
        site = outputs(cfn, "ThalwegApi")["SiteUrl"]  # no CDN yet: the site API front door
    print(f"front door: {site}", flush=True)
    ingest, artifacts = data["IngestBucket"], data["ArtifactsBucket"]
    activity_arn, sm_arn = flow["ActivityArn"], flow["StateMachineArn"]
    stations, runs, gaugings, warnings = (ddb.Table(data[k]) for k in ("StationsTable", "RunsTable", "GaugingsTable", "WarningsTable"))

    status, body = http("GET", f"{site}/api/health")
    check("GET /api/health", status == 200 and isinstance(body, dict) and body.get("ok") is True, str(status))
    status, body = http("GET", f"{site}/api/stations")
    check("GET /api/stations", status == 200 and isinstance(body, dict) and isinstance(body.get("stations"), list), f"{status}, {len(body.get('stations', [])) if isinstance(body, dict) else '?'} stations")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    stamps = [stamp, stamp[:-2] + f"{(int(stamp[-2:]) + 1) % 60:02d}"]
    keys = [f"clips/{STATION}/{s}" for s in stamps]
    stations.put_item(Item={"id": STATION, "name": "Smoke test (temporary)"})
    run_ids: list[str] = []
    warning_id = f"w-{STATION}-{stamps[1]}"
    try:
        # --- 2/3: ingest trigger, dedup across the API -------------------------------
        for k, s in zip(keys, stamps):
            s3.put_object(Bucket=ingest, Key=k + ".mp4", Body=b"\x00\x00\x00\x18ftypmp42smoke")
        s3.put_object(
            Bucket=ingest,
            Key=keys[0] + ".json",
            Body=json.dumps({"station_id": STATION, "t": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "level": 0.42, "source": "smoke"}).encode(),
        )

        def started_run():
            items = runs.query(IndexName="by_station", KeyConditionExpression=Key("station_id").eq(STATION)).get("Items", [])
            return items[0] if items else None

        run = wait_for(started_run, 90)
        if not check("sidecar -> EventBridge -> StartRun -> Preflight opened the run", bool(run and run.get("status") == "RUNNING"), run["run_id"] if run else "no run"):
            return 1
        rid_a = run["run_id"]
        run_ids.append(rid_a)

        status, body = http("POST", f"{site}/api/runs", {"station_id": STATION, "clip_key": keys[0] + ".mp4", "brain": "autopilot"})
        check("POST /api/runs while in flight returns the same run (200)", status == 200 and isinstance(body, dict) and body.get("run_id") == rid_a and body.get("deduplicated") is True, f"{status} {body}")

        task = take_agent_task(sfn, activity_arn, rid_a)
        if not check("execution reached the Agent activity", task is not None):
            return 1
        inp = task["input"]
        check("activity input carries sidecar + buckets", inp.get("level") == 0.42 and inp.get("ingest_bucket") == ingest and inp.get("artifacts_bucket") == artifacts, json.dumps({k: inp.get(k) for k in ("brain", "level", "t")}))

        # --- 4: fail closed ------------------------------------------------------------
        sfn.send_task_failure(taskToken=task["token"], error="WorkerTimeout", cause="smoke: simulated agent timeout")
        final = wait_for(lambda: (lambda st: st if st != "RUNNING" else None)(execution_status(sfn, sm_arn, rid_a)), 60)
        item = runs.get_item(Key={"run_id": rid_a}).get("Item", {})
        check(
            "agent failure -> FailClosed: run HOLD fail_closed:WorkerTimeout, execution FAILED",
            final == "FAILED" and item.get("status") == "HOLD" and item.get("decision", {}).get("reasons") == ["fail_closed:WorkerTimeout"],
            f"execution {final}, run {item.get('status')} {item.get('decision', {}).get('reasons')}",
        )

        # --- 5: publish with a warning draft (started through the API) ------------------
        t_clip = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        status, body = http("POST", f"{site}/api/runs", {"station_id": STATION, "clip_key": keys[1] + ".mp4", "brain": "autopilot"})
        check("POST /api/runs without a sidecar is 422", status == 422, f"{status} {body}")
        s3.put_object(Bucket=ingest, Key=keys[1] + ".json", Body=json.dumps({"station_id": STATION, "t": t_clip, "level": 2.31, "source": "smoke"}).encode())
        # The trigger and the API race for the same (clip, brain); exactly one run must start.
        status, body = http("POST", f"{site}/api/runs", {"station_id": STATION, "clip_key": keys[1] + ".mp4", "brain": "autopilot"})
        check("POST /api/runs accepted (202 new or 200 deduplicated)", status in (200, 202) and isinstance(body, dict) and body.get("run_id"), f"{status}")
        rid_b = body["run_id"] if isinstance(body, dict) else None
        run_ids.append(rid_b)
        time.sleep(5)
        others = [it["run_id"] for it in runs.query(IndexName="by_station", KeyConditionExpression=Key("station_id").eq(STATION)).get("Items", []) if it["run_id"] not in (rid_a, rid_b)]
        run_ids.extend(others)
        check("one run per (clip, brain) across trigger + API", not others, f"extra runs: {others}" if others else rid_b)

        task = take_agent_task(sfn, activity_arn, rid_b)
        if not check("second execution reached the Agent activity", task is not None):
            return 1
        result = {
            "run_id": rid_b,
            "decision": {"kind": "PUBLISH", "reasons": ["smoke"], "q": 12.5, "q_lo": 11.0, "q_hi": 14.0, "method": "lspiv"},
            "gauging": {"station_id": STATION, "t": t_clip, "level": 2.31, "q": 12.5, "q_lo": 11.0, "q_hi": 14.0, "method": "lspiv", "run_id": rid_b},
            "warning_draft": {"id": warning_id, "station_id": STATION, "level_now": 2.31, "q_now": 12.5, "eta_s": 3600, "p_cross": 0.7, "text": "Smoke test draft: not a real warning.", "evidence": []},
            "trace_key": f"runs/{rid_b}/trace.json",
            "cost_usd": 0,
            "timings": {"total_s": 1.0},
        }
        sfn.send_task_success(taskToken=task["token"], output=json.dumps(result))

        draft = wait_for(lambda: (lambda it: it if it and it.get("state") == "DRAFT" else None)(warnings.get_item(Key={"id": warning_id}).get("Item")), 60)
        check("PUBLISH + draft -> gauging written, warning DRAFT awaiting signature", bool(draft) and "Item" in gaugings.get_item(Key={"station_id": STATION, "t": t_clip}), warning_id)

        status, body = http("GET", f"{site}/api/reviews?state=DRAFT")
        listed = isinstance(body, dict) and any(r.get("id") == warning_id and "task_token" not in r for r in body.get("reviews", []))
        check("GET /api/reviews lists the draft without its task token", status == 200 and listed, str(status))

        # --- 6: the review API ---------------------------------------------------------
        passcode = ssm.get_parameter(Name="/thalweg/reviewer_passcode", WithDecryption=True)["Parameter"]["Value"]
        status, _ = http("POST", f"{site}/api/reviews/{warning_id}", {"action": "sign", "passcode": "wrong-pass", "note": "smoke"})
        check("sign with a wrong passcode is 403", status == 403, str(status))
        status, body = http("POST", f"{site}/api/reviews/{warning_id}", {"action": "sign", "passcode": passcode, "note": "smoke reviewer"})
        check("sign with the passcode is 200", status == 200 and isinstance(body, dict) and body.get("next_state") == "SIGNED", f"{status} {body}")
        status, _ = http("POST", f"{site}/api/reviews/{warning_id}", {"action": "sign", "passcode": passcode, "note": "again"})
        check("signing again is 409", status == 409, str(status))

        signed = wait_for(lambda: (lambda it: it if it and it.get("state") == "SIGNED" else None)(warnings.get_item(Key={"id": warning_id}).get("Item")), 60)
        check("IssueWarning marked it SIGNED", bool(signed), (signed or {}).get("signed_by", ""))
        final = wait_for(lambda: (lambda st: st if st != "RUNNING" else None)(execution_status(sfn, sm_arn, rid_b)), 60)
        check("second execution SUCCEEDED", final == "SUCCEEDED", str(final))
        status, body = http("GET", f"{site}/artifacts/public/warnings.json")
        check("/artifacts/public/warnings.json carries the signed warning", status == 200 and isinstance(body, dict) and any(w.get("id") == warning_id for w in body.get("warnings", [])), str(status))

        status, body = http("GET", f"{site}/api/runs/{rid_b}")
        check("GET /api/runs/{id} shows PUBLISH with a trace URL", status == 200 and isinstance(body, dict) and body.get("status") == "PUBLISH" and body.get("trace_url") == f"/artifacts/runs/{rid_b}/trace.json", str(status))
        status, body = http("GET", f"{site}/api/stations/{STATION}/gaugings")
        check("GET /api/stations/{id}/gaugings returns the gauging", status == 200 and isinstance(body, dict) and body.get("count") == 1, str(status))

        # --- 7: private prefixes ---------------------------------------------------------
        status, _ = http("GET", f"{site}/artifacts/data/dart/replay-manifest.json")
        check("/artifacts/data/* is not served", status == 404, str(status))
    finally:
        if not args.keep:
            cleanup(s3, ingest, artifacts, keys, stations, runs, gaugings, warnings, run_ids, warning_id)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


def cleanup(s3, ingest, artifacts, keys, stations, runs, gaugings, warnings, run_ids, warning_id) -> None:
    for k in keys:
        for ext in (".mp4", ".json"):
            s3.delete_object(Bucket=ingest, Key=k + ext)
    for g in gaugings.query(KeyConditionExpression=Key("station_id").eq(STATION)).get("Items", []):
        gaugings.delete_item(Key={"station_id": STATION, "t": g["t"]})
    for it in runs.query(IndexName="by_station", KeyConditionExpression=Key("station_id").eq(STATION)).get("Items", []):
        runs.delete_item(Key={"run_id": it["run_id"]})
    for rid in run_ids:
        if rid:
            runs.delete_item(Key={"run_id": rid})
    for k in keys:  # in-flight locks would expire on their own after 15 minutes
        for brain in ("autopilot", "llm"):
            runs.delete_item(Key={"run_id": "lock#" + hashlib.sha1(f"{k}.mp4|{brain}".encode()).hexdigest()})
    warnings.delete_item(Key={"id": warning_id})
    stations.delete_item(Key={"id": STATION})
    # Republish the public feed without the smoke warning.
    signed = warnings.query(IndexName="by_state", KeyConditionExpression=Key("state").eq("SIGNED"), ScanIndexForward=False).get("Items", [])
    if signed:
        from decimal import Decimal

        def plain(o):
            if isinstance(o, Decimal):
                return int(o) if o == o.to_integral_value() else float(o)
            raise TypeError

        fields = ("id", "station_id", "run_id", "created", "level_now", "q_now", "eta_s", "p_cross", "text", "evidence", "expires", "state", "signed_by", "signed_at")
        public = [{k: w[k] for k in fields if k in w} for w in signed]
        doc = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "warnings": public}
        s3.put_object(Bucket=artifacts, Key="public/warnings.json", Body=json.dumps(doc, default=plain).encode(), ContentType="application/json", CacheControl="public, max-age=60")
    else:
        s3.delete_object(Bucket=artifacts, Key="public/warnings.json")
    print("cleaned up smoke-test records", flush=True)


if __name__ == "__main__":
    sys.exit(main())
