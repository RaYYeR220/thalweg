"""Graviton worker: takes gauging tasks from the Step Functions activity, runs the agent, reports back.

Runs as a systemd service on the COOL AMI (OpenCV 5 tuned for Graviton). Configuration comes
from /etc/thalweg.env, written by the instance user data.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import tempfile
import threading
import time
import traceback
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Key

from thalweg.agent.budget import Budget
from thalweg.agent.loop import run_agent
from thalweg.agent.policy import PolicyLimits
from thalweg.clip import Clip
from thalweg.hydro.forecast import StageKalman
from thalweg.hydro.rating import RatingCurve
from thalweg.hydro.warning import assess_warning
from thalweg.pipeline import ArtifactSink
from thalweg.station import load_station

log = logging.getLogger("thalweg.worker")
ENV = os.environ
REPO = Path(ENV.get("THALWEG_REPO", "/opt/thalweg/app"))
REGION = ENV.get("AWS_REGION", "us-east-1")

sfn = boto3.client("stepfunctions", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
ddb = boto3.resource("dynamodb", region_name=REGION)


class DynamoBudgetStore:
    def __init__(self, table):
        self.t = ddb.Table(table)

    def get(self, day: str) -> float:
        item = self.t.get_item(Key={"day": day}).get("Item") or {}
        return float(item.get("llm_usd", 0))

    def add(self, day: str, usd: float) -> None:
        self.t.update_item(Key={"day": day}, UpdateExpression="ADD llm_usd :u",
                           ExpressionAttributeValues={":u": Decimal(str(round(usd, 6)))})


def _station_path(station_id: str) -> Path:
    p = REPO / "stations" / f"{station_id}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"unknown station {station_id}")
    return p


def _download(bucket: str, key: str, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(bucket, key, str(dst))
    return dst


def _upload_dir(local: Path, bucket: str, prefix: str) -> None:
    for f in local.iterdir():
        ctype = "image/jpeg" if f.suffix == ".jpg" else "application/json"
        s3.upload_file(str(f), bucket, f"{prefix}{f.name}", ExtraArgs={"ContentType": ctype,
                                                                         "CacheControl": "max-age=86400"})


def _to_ddb(obj):
    return json.loads(json.dumps(obj, default=str), parse_float=Decimal)


def station_physics(station, clip, decision) -> tuple[dict | None, dict]:
    """Update the stage series, refit the rating from published gaugings and maybe draft a warning."""
    st_tbl = ddb.Table(ENV["THALWEG_TABLE_STATIONS"])
    g_tbl = ddb.Table(ENV["THALWEG_TABLE_GAUGINGS"])
    item = st_tbl.get_item(Key={"id": station.id}).get("Item") or {}
    levels = [(datetime.fromisoformat(t), float(h)) for t, h in item.get("levels", [])]
    levels.append((clip.t, clip.level))
    levels = sorted({t: h for t, h in levels}.items())[-300:]
    st_tbl.update_item(Key={"id": station.id}, UpdateExpression="SET levels = :l",
                       ExpressionAttributeValues={":l": _to_ddb([[t.isoformat(), h] for t, h in levels])})
    gaugings = g_tbl.query(KeyConditionExpression=Key("station_id").eq(station.id),
                           ScanIndexForward=False, Limit=200).get("Items", [])
    pts = [(float(g["level"]), float(g["q"])) for g in gaugings]
    if decision.kind == "PUBLISH":
        pts.append((clip.level, float(decision.q["q"])))
    info: dict = {"n_gaugings": len(pts)}
    rating = None
    if len(pts) >= 5 and len({round(h, 2) for h, _ in pts}) >= 3:
        try:
            rating = RatingCurve.fit([h for h, _ in pts], [q for _, q in pts])
            info["rating"] = rating.to_dict()
            if decision.kind == "PUBLISH":
                info["rating_residual_z"] = round(rating.residual_z(clip.level, float(decision.q["q"])), 2)
        except Exception as e:  # noqa: BLE001
            info["rating_error"] = str(e)[:120]
    kf = StageKalman()
    for t, h in levels:
        kf.update(t, h)
    info["stage_rate_m_per_h"] = round(kf.rate_m_per_h, 4)
    draft = None
    if decision.kind == "PUBLISH" and not item.get("open_warning"):
        w = assess_warning(station, kf, rating, clip.t, float(decision.q["q"]))
        if w is not None:
            draft = w.to_dict()
    return draft, info


def handle(task_input: dict, heartbeat) -> dict:
    run_id = task_input["run_id"]
    station = load_station(_station_path(task_input["station_id"]))
    if station.reference_frame and not Path(station.reference_frame).is_absolute():
        ref = Path(tempfile.gettempdir()) / "thalweg-ref" / Path(station.reference_frame).name
        if not ref.exists():
            _download(ENV["THALWEG_ARTIFACTS_BUCKET"], f"data/{station.reference_frame}", ref)
        station.reference_frame = str(ref)
    work = Path(tempfile.mkdtemp(prefix=f"{run_id}-"))
    try:
        sidecar = json.loads(s3.get_object(Bucket=ENV["THALWEG_INGEST_BUCKET"], Key=task_input["sidecar_key"])
                             ["Body"].read())
        video = _download(ENV["THALWEG_INGEST_BUCKET"], task_input["clip_key"], work / Path(task_input["clip_key"]).name)
        clip = Clip(station_id=station.id, video=video, t=datetime.fromisoformat(sidecar["t"].replace("Z", "+00:00")),
                    level=float(sidecar["level"]), source=sidecar.get("source", ""))
        heartbeat()
        brain_name = task_input.get("brain", "autopilot")
        if brain_name == "llm":
            from thalweg.agent.llm import LLMBrain
            budget = Budget(float(ENV.get("THALWEG_LLM_DAILY_USD", "3.0")), DynamoBudgetStore(ENV["THALWEG_TABLE_BUDGET"]))
            brain = LLMBrain(model=task_input.get("model") or ENV.get("THALWEG_MODEL", "claude-sonnet-5-5"), budget=budget)
        else:
            from thalweg.agent.autopilot import AutopilotBrain
            brain = AutopilotBrain()
        sink = ArtifactSink(work / "out", prefix=f"runs/{run_id}/")
        stop = threading.Event()

        def beat():
            while not stop.wait(30):
                heartbeat()
        threading.Thread(target=beat, daemon=True).start()
        out_dir = work / "out"
        uploaded: set[str] = set()

        def progress(step) -> None:
            # Stream the trace as it grows so the run page can show the agent working.
            for f in out_dir.glob("*.jpg"):
                if f.name not in uploaded:
                    s3.upload_file(str(f), ENV["THALWEG_ARTIFACTS_BUCKET"], f"runs/{run_id}/{f.name}",
                                   ExtraArgs={"ContentType": "image/jpeg", "CacheControl": "max-age=86400"})
                    uploaded.add(f.name)
            body = {"run_id": run_id, "station_id": station.id, "brain": brain_name, "model": getattr(brain, "model", None),
                    "clip": {"t": clip.t.isoformat(), "level": clip.level}, "done": False,
                    "steps": [st.to_dict() for st in steps_ref]}
            s3.put_object(Bucket=ENV["THALWEG_ARTIFACTS_BUCKET"], Key=f"runs/{run_id}/progress.json",
                          Body=json.dumps(body, default=str).encode(), ContentType="application/json",
                          CacheControl="no-store")

        steps_ref: list = []

        def on_step(step) -> None:
            steps_ref.append(step)
            progress(step)
        try:
            res = run_agent(station, clip, brain, sink, limits=PolicyLimits(), run_id=run_id, on_step=on_step)
        finally:
            stop.set()
        draft, physics = station_physics(station, clip, res.decision)
        trace = res.to_dict() | {"physics": physics, "warning_draft": draft, "brain_name": brain_name,
                                 "requested_by": task_input.get("requested_by")}
        sink.put_json("trace", trace)
        sink.put_json("progress", {**trace, "done": True})
        _upload_dir(work / "out", ENV["THALWEG_ARTIFACTS_BUCKET"], f"runs/{run_id}/")
        d = res.decision
        out = {"run_id": run_id, "decision": {"kind": d.kind, "reasons": d.reasons, "method": d.method,
                                               "basis": d.basis},
               "trace_key": f"runs/{run_id}/trace.json", "cost_usd": round(res.cost_usd, 5),
               "timings": res.timings, "policy_denials": sum(1 for s in res.steps if not s.verdict.allowed),
               "evidence": [f"runs/{run_id}/{k}.jpg" for k in ("frame_overlay", "ortho", "field_lspiv", "field_stiv",
                                                                  "sti_mid") if (work / "out" / f"{k}.jpg").exists()]}
        if d.q:
            out["decision"].update(q=d.q.get("q"), q_lo=d.q.get("q_lo"), q_hi=d.q.get("q_hi"))
        if d.kind == "PUBLISH":
            out["gauging"] = {"station_id": station.id, "t": clip.t.isoformat(), "level": clip.level,
                              "q": d.q["q"], "q_lo": d.q["q_lo"], "q_hi": d.q["q_hi"], "method": d.q.get("method"),
                              "run_id": run_id}
        if draft:
            out["warning_draft"] = draft
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    arn = ENV["THALWEG_ACTIVITY_ARN"]
    name = f"{socket.gethostname()}-{os.getpid()}"
    log.info("worker %s polling %s", name, arn)
    while True:
        try:
            task = sfn.get_activity_task(activityArn=arn, workerName=name)
        except Exception:  # noqa: BLE001
            log.exception("poll failed")
            time.sleep(5)
            continue
        token = task.get("taskToken")
        if not token:
            continue
        inp = json.loads(task["input"])
        log.info("task %s station=%s brain=%s", inp.get("run_id"), inp.get("station_id"), inp.get("brain"))
        t0 = time.time()
        try:
            out = handle(inp, lambda: sfn.send_task_heartbeat(taskToken=token))
            sfn.send_task_success(taskToken=token, output=json.dumps(out, default=str))
            log.info("done %s %s in %.1fs", inp.get("run_id"), out["decision"]["kind"], time.time() - t0)
        except Exception as e:  # noqa: BLE001 - the state machine turns this into a fail-closed HOLD
            log.error("task failed: %s", traceback.format_exc())
            sfn.send_task_failure(taskToken=token, error="WorkerError", cause=f"{type(e).__name__}: {e}"[:256])


if __name__ == "__main__":
    main()
