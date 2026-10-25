"""Score the gauging agent on the Video Globe Challenge 2020 benchmark.

    python eval/score_vgc.py --brain autopilot --videos 2 3 4 5 6 7 --tag frozen-autopilot

Each run is appended to eval/runs.jsonl as it finishes, whatever the result, so no run can
be dropped quietly. The answer key is read only here, after the agent has decided.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from thalweg.agent.loop import run_agent
from thalweg.cli import make_brain
from thalweg.clip import Clip
from thalweg.pipeline import ArtifactSink
from thalweg.station import load_station

ROOT = Path(__file__).resolve().parents[1]
KEYS = ROOT / "eval" / "keys" / "vgc.json"
DEV = {"vgc1", "vgc8"}


def git_rev() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def score(vid: str, q: float | None, key: dict) -> dict:
    if q is None:
        return {"err_pct": None, "within_ref_u": None, "expert_rank": None}
    ref = key["q_ref"]
    err = (q - ref) / ref
    experts = [abs((e - ref) / ref) for e in key["experts"]]
    better = sum(1 for e in experts if e < abs(err))
    u = key.get("u_ref_raw")
    return {"err_pct": round(100 * err, 2), "within_ref_u": None if u is None else abs(err) <= u,
            "expert_rank": better + 1, "n_experts": len(experts),
            "beats_share": round(sum(1 for e in experts if e > abs(err)) / len(experts), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", default="autopilot")
    ap.add_argument("--model", default=None)
    ap.add_argument("--videos", nargs="+", type=int, default=[2, 3, 4, 5, 6, 7])
    ap.add_argument("--tag", required=True)
    a = ap.parse_args()
    keys = json.loads(KEYS.read_text(encoding="utf-8"))
    out_dir = ROOT / "eval" / "results" / a.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for n in a.videos:
        vid = f"vgc{n}"
        station = load_station(ROOT / "stations" / f"{vid}.yaml")
        clip = Clip.load(ROOT / "clips" / f"{vid}.json")
        sink = ArtifactSink(ROOT / "eval" / "artifacts" / a.tag / vid)
        res = run_agent(station, clip, make_brain(a.brain, a.model), sink)
        trace = res.to_dict()
        sink.put_json("trace", trace)
        (out_dir / f"{vid}.trace.json").write_text(json.dumps(trace, default=str, indent=1), encoding="utf-8")
        d = res.decision
        q_pub = d.q["q"] if d.kind == "PUBLISH" and d.q else None
        q_est = d.q["q"] if d.q else None
        key = keys[vid]
        row = {"video": vid, "split": "dev" if vid in DEV else "test", "site": key["site"], "brain": a.brain,
               "model": res.model, "decision": d.kind, "method": d.method, "basis": d.basis, "reasons": d.reasons,
               "q_published": q_pub, "q_estimate": q_est, "estimates": res.estimates, "q_ref": key["q_ref"],
               "u_ref": key.get("u_ref_raw"), "published": score(vid, q_pub, key),
               "estimate": score(vid, q_est, key), "expert_median_err_pct": None,
               "cost_usd": round(res.cost_usd, 4), "seconds": round(sum(res.timings.values()), 1),
               "run_id": res.run_id, "git": git_rev(), "tag": a.tag,
               "time": datetime.now(timezone.utc).isoformat()}
        ex = sorted(abs((e - key["q_ref"]) / key["q_ref"]) for e in key["experts"])
        row["expert_median_err_pct"] = round(100 * ex[len(ex) // 2], 2)
        rows.append(row)
        with open(ROOT / "eval" / "runs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
        print(json.dumps({k: row[k] for k in ("video", "decision", "method", "q_published", "q_estimate", "q_ref")}
                         | {"err": row["estimate"]["err_pct"], "rank": row["estimate"]["expert_rank"]}), flush=True)
    (out_dir / "summary.json").write_text(json.dumps(rows, default=str, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
