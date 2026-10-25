"""Command line: `thalweg gauge --station stations/vgc1.yaml --clip clips/vgc1.json --brain autopilot`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from thalweg.agent.autopilot import AutopilotBrain
from thalweg.agent.loop import new_run_id, run_agent
from thalweg.clip import Clip
from thalweg.pipeline import ArtifactSink
from thalweg.station import load_station


def make_brain(name: str, model: str | None = None):
    if name == "autopilot":
        return AutopilotBrain()
    if name == "llm":
        from thalweg.agent.llm import LLMBrain
        return LLMBrain(model=model)
    raise SystemExit(f"unknown brain {name}")


def cmd_gauge(a) -> int:
    station = load_station(a.station)
    clip = Clip.load(a.clip)
    run_id = a.run_id or new_run_id()
    out = Path(a.out or f"runs/{run_id}")
    sink = ArtifactSink(out)
    res = run_agent(station, clip, make_brain(a.brain, a.model), sink, run_id=run_id)
    trace = res.to_dict()
    sink.put_json("trace", trace)
    d = res.decision
    print(json.dumps({"run_id": res.run_id, "decision": d.kind, "method": d.method, "basis": d.basis,
                      "q": (d.q or {}).get("q"), "reasons": d.reasons, "estimates": res.estimates,
                      "cost_usd": round(res.cost_usd, 4), "timings": res.timings}, indent=1))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="thalweg")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gauge", help="run the gauging agent on one clip")
    g.add_argument("--station", required=True)
    g.add_argument("--clip", required=True)
    g.add_argument("--brain", default="autopilot", choices=["autopilot", "llm"])
    g.add_argument("--model", default=None)
    g.add_argument("--out", default=None)
    g.add_argument("--run-id", default=None)
    g.set_defaults(fn=cmd_gauge)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
