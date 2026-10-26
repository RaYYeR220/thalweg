"""River Dart station: build the replay manifest and backfill the station history through the cloud pipeline.

    python scripts/dart_backfill.py manifest          # writes data/dart/replay-manifest.json in S3
    python scripts/dart_backfill.py backfill --group seed --brain autopilot
    python scripts/dart_backfill.py backfill --group storm --brain llm --model claude-haiku-5-5

Each clip goes through the same path as a live station upload (ingest bucket -> Step Functions ->
Graviton worker), one at a time, so the worker never has a queue that could time out.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime

import boto3

ART = "thalweg-artifacts-510735888173"
ING = "thalweg-ingest-510735888173"
API = "https://n5osg898lg.execute-api.us-east-1.amazonaws.com"


def _s3(profile):
    return boto3.Session(profile_name=profile, region_name="us-east-1").client("s3")


def raw_manifest(s3) -> list[dict]:
    rows = json.loads(s3.get_object(Bucket=ART, Key="data/dart/manifest_raw.json")["Body"].read())
    return sorted(rows, key=lambda r: r["t"])


def cmd_manifest(a):
    s3 = _s3(a.profile)
    rows = [r for r in raw_manifest(s3) if r["group"] == "storm"]
    doc = {"station_id": "dart", "event": "Storm Callum, River Dart at Austins Bridge, 11-12 Oct 2018",
           "clips": [{"video_key": f"data/dart/videos/{r['name']}", "t": r["t"], "level": r["level"]} for r in rows]}
    s3.put_object(Bucket=ART, Key="data/dart/replay-manifest.json", Body=json.dumps(doc, indent=1).encode(),
                  ContentType="application/json")
    print(f"replay manifest: {len(doc['clips'])} clips")


def _latest_run(clip_key: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{API}/runs?station=dart&limit=20", timeout=30) as r:
            runs = json.loads(r.read()).get("runs", [])
    except (OSError, ValueError):
        return None  # transient network error: poll again
    for run in runs:
        if run.get("clip_key") == clip_key:
            return run
    return None


def cmd_backfill(a):
    s3 = _s3(a.profile)
    rows = [r for r in raw_manifest(s3) if a.group in ("all", r["group"])]
    for i, r in enumerate(rows[a.start:a.start + a.limit], start=a.start):
        t = datetime.fromisoformat(r["t"])
        stamp = t.strftime("%Y%m%dT%H%M%S")
        clip_key = f"clips/dart/{stamp}.mp4"
        s3.copy_object(Bucket=ING, Key=clip_key, CopySource={"Bucket": ART, "Key": f"data/dart/videos/{r['name']}"},
                       ContentType="video/mp4", MetadataDirective="REPLACE")
        side = {"station_id": "dart", "t": t.isoformat() + "+00:00", "level": r["level"],
                "source": f"backfill:{r['name']}", "brain": a.brain, "requested_by": "api"}
        if a.model:
            side["model"] = a.model
        s3.put_object(Bucket=ING, Key=f"clips/dart/{stamp}.json", Body=json.dumps(side).encode(),
                      ContentType="application/json")
        t0 = time.time()
        run = None
        while time.time() - t0 < 900:
            time.sleep(8)
            run = _latest_run(clip_key)
            if run and run.get("status") not in (None, "RUNNING"):
                break
        d = (run or {}).get("decision") or {}
        print(json.dumps({"i": i, "t": r["t"], "level": r["level"], "group": r["group"], "status": (run or {}).get("status"),
                          "q": d.get("q"), "method": d.get("method"), "reasons": d.get("reasons", [])[:1],
                          "s": round(time.time() - t0)}), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["manifest", "backfill"])
    ap.add_argument("--group", default="all", choices=["all", "seed", "storm"])
    ap.add_argument("--brain", default="autopilot")
    ap.add_argument("--model", default=None)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--limit", type=int, default=10**6)
    ap.add_argument("--profile", default="countercharge")
    a = ap.parse_args()
    {"manifest": cmd_manifest, "backfill": cmd_backfill}[a.cmd](a)


if __name__ == "__main__":
    main()
