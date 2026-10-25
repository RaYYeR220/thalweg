"""Refusal suite: real clips corrupted in known ways, each with the decision the agent must reach.

    python eval/refusal_suite.py build          # writes clips under $THALWEG_DATA/refusal
    python eval/refusal_suite.py run --brain autopilot --tag refusal-autopilot

A wrong PUBLISH on a must-refuse case is the failure that matters; the summary counts it separately.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import cv2 as cv
import numpy as np

from thalweg.agent.loop import run_agent
from thalweg.cli import make_brain
from thalweg.clip import Clip, read_frames
from thalweg.paths import data_dir
from thalweg.pipeline import ArtifactSink
from thalweg.station import load_station

ROOT = Path(__file__).resolve().parents[1]
SRC = {"vgc1": "clips/vgc1.json", "vgc8": "clips/vgc8.json"}
DUR = 8.0

# case -> (source, transform, allowed decisions)
CASES = {
    "clean_vgc1": ("vgc1", "none", ["PUBLISH"]),
    "clean_vgc8": ("vgc8", "none", ["PUBLISH"]),
    "handheld_shake_vgc8": ("vgc8", "shake", ["PUBLISH"]),
    "night_vgc1": ("vgc1", "night", ["HOLD"]),
    "glare_vgc8": ("vgc8", "glare", ["HOLD"]),
    "frozen_feed_vgc1": ("vgc1", "frozen", ["HOLD"]),
    "still_water_vgc8": ("vgc8", "still", ["HOLD"]),
    "camera_moved_vgc1": ("vgc1", "moved", ["ESCALATE"]),
    "level_above_banks_vgc8": ("vgc8", "level", ["ESCALATE"]),
}


def _transform(frames: list[np.ndarray], kind: str, rng) -> list[np.ndarray]:
    h, w = frames[0].shape[:2]
    if kind == "night":
        return [np.clip(f.astype(np.float32) * 0.07 + rng.normal(0, 2.0, f.shape), 0, 255).astype(np.uint8)
                for f in frames]
    if kind == "glare":
        out = []
        for f in frames:
            g = f.astype(np.float32)
            g[int(0.2 * h):int(0.85 * h), int(0.1 * w):int(0.85 * w)] = 255.0
            out.append(g.astype(np.uint8))
        return out
    if kind == "frozen":
        return [frames[0].copy() for _ in frames]
    if kind == "still":
        return [np.clip(frames[0].astype(np.float32) + rng.normal(0, 0.8, frames[0].shape), 0, 255).astype(np.uint8)
                for _ in frames]
    if kind == "shake":
        out, d = [], np.zeros(2)
        for f in frames:
            d = 0.8 * d + rng.normal(0, 1.2, 2)
            M = np.float32([[1, 0, d[0]], [0, 1, d[1]]])
            out.append(cv.warpAffine(f, M, (w, h), borderMode=cv.BORDER_REPLICATE))
        return out
    if kind == "moved":
        M = np.float32([[1, 0, 28], [0, 1, -18]])
        return [cv.warpAffine(f, M, (w, h), borderMode=cv.BORDER_REPLICATE) for f in frames]
    return frames


def build():
    rng = np.random.default_rng(7)
    out = data_dir() / "refusal"
    out.mkdir(parents=True, exist_ok=True)
    for src in SRC:
        clip = Clip.load(ROOT / SRC[src])
        fs = read_frames(clip.video, dur_s=DUR, gray=True)
        cv.imwrite(str(out / f"{src}_reference.png"), fs.frames[0])
    for case, (src, kind, _) in CASES.items():
        clip = Clip.load(ROOT / SRC[src])
        fs = read_frames(clip.video, dur_s=DUR, gray=True)
        frames = _transform(fs.frames, kind, rng)
        fps = 1.0 / fs.dt_s
        vw = cv.VideoWriter(str(out / f"{case}.mp4"), cv.VideoWriter_fourcc(*"mp4v"), fps, fs.size, False)
        for f in frames:
            vw.write(f)
        vw.release()
        level = clip.level + (1.5 if kind == "level" else 0.0)
        meta = {"station_id": src, "video": f"refusal/{case}.mp4", "t": clip.t.isoformat(), "level": level,
                "source": f"{clip.source}, {kind}"}
        (out / f"{case}.json").write_text(json.dumps(meta), encoding="utf-8")
    (ROOT / "eval" / "keys").mkdir(parents=True, exist_ok=True)
    (ROOT / "eval" / "keys" / "refusal.json").write_text(
        json.dumps({c: v[2] for c, v in CASES.items()}, indent=1), encoding="utf-8")
    print(f"built {len(CASES)} cases in {out}")


def run(brain: str, model: str | None, tag: str):
    key = json.loads((ROOT / "eval" / "keys" / "refusal.json").read_text())
    out_dir = ROOT / "eval" / "results" / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for case, (src, kind, _) in CASES.items():
        station = deepcopy(load_station(ROOT / "stations" / f"{src}.yaml"))
        station.reference_frame = str(data_dir() / "refusal" / f"{src}_reference.png")
        clip = Clip.load(data_dir() / "refusal" / f"{case}.json")
        sink = ArtifactSink(ROOT / "eval" / "artifacts" / tag / case)
        res = run_agent(station, clip, make_brain(brain, model), sink)
        sink.put_json("trace", res.to_dict())
        (out_dir / f"{case}.trace.json").write_text(json.dumps(res.to_dict(), default=str, indent=1), encoding="utf-8")
        ok = res.decision.kind in key[case]
        row = {"case": case, "transform": kind, "expected": key[case], "decision": res.decision.kind,
               "reasons": res.decision.reasons, "correct": ok,
               "wrong_publish": res.decision.kind == "PUBLISH" and "PUBLISH" not in key[case],
               "q": (res.decision.q or {}).get("q"), "brain": brain, "model": res.model, "tag": tag,
               "cost_usd": round(res.cost_usd, 4), "time": datetime.now(timezone.utc).isoformat()}
        rows.append(row)
        with open(ROOT / "eval" / "runs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"suite": "refusal", **row}, default=str) + "\n")
        print(json.dumps({k: row[k] for k in ("case", "expected", "decision", "correct", "reasons")}), flush=True)
    summary = {"cases": len(rows), "correct": sum(r["correct"] for r in rows),
               "wrong_publishes": sum(r["wrong_publish"] for r in rows), "rows": rows}
    (out_dir / "summary.json").write_text(json.dumps(summary, default=str, indent=1), encoding="utf-8")
    print(f"{summary['correct']}/{summary['cases']} correct, {summary['wrong_publishes']} wrong publishes")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "run"])
    ap.add_argument("--brain", default="autopilot")
    ap.add_argument("--model", default=None)
    ap.add_argument("--tag", default=None)
    a = ap.parse_args()
    if a.cmd == "build":
        build()
    else:
        run(a.brain, a.model, a.tag or f"refusal-{a.brain}")


if __name__ == "__main__":
    main()
