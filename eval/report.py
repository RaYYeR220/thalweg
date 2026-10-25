"""Aggregate evaluation results into eval/results/benchmark.json (published with the site) and a Markdown summary.

    python eval/report.py
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "eval" / "results"
KEYS = ROOT / "eval" / "keys" / "vgc.json"

RUNS = {
    "vgc-autopilot": ("autopilot", "frozen (blind)"),
    "vgc-llm-sonnet": ("llm", "frozen (blind)"),
    "vgc-autopilot-v1.1": ("autopilot", "v1.1 (after refusal-suite fixes, not blind)"),
    "vgc-llm-sonnet-v1.1": ("llm", "v1.1 (after refusal-suite fixes, not blind)"),
    "vgc-autopilot-v1.2": ("autopilot", "v1.2 (channel-axis guard, not blind)"),
}
REFUSAL = ["refusal-autopilot", "refusal-llm-sonnet", "refusal-autopilot-v1.1", "refusal-llm-sonnet-v1.1",
           "refusal-autopilot-v1.2"]


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def expert_stats(keys: dict) -> dict:
    out = {}
    for vid, k in keys.items():
        errs = sorted(abs((e - k["q_ref"]) / k["q_ref"]) for e in k["experts"])
        out[vid] = {"n": len(errs), "median_abs_err_pct": round(100 * statistics.median(errs), 2),
                    "best_abs_err_pct": round(100 * errs[0], 2), "q_ref": k["q_ref"], "u_ref_pct": round(100 * (k.get("u_ref_raw") or 0), 1),
                    "site": k["site"], "experts_q": k["experts"]}
    return out


def summarise(rows: list[dict]) -> dict:
    test = [r for r in rows if r["split"] == "test"]
    pub = [r for r in test if r["decision"] == "PUBLISH"]
    est = [r for r in test if r["estimate"]["err_pct"] is not None]

    def mae(rs, key):
        v = [abs(r[key]["err_pct"]) for r in rs if r[key]["err_pct"] is not None]
        return round(statistics.mean(v), 2) if v else None

    return {
        "test_videos": len(test), "published": len(pub), "escalated": sum(r["decision"] == "ESCALATE" for r in test),
        "held": sum(r["decision"] == "HOLD" for r in test),
        "mae_published_pct": mae(pub, "published"), "mae_all_estimates_pct": mae(est, "estimate"),
        "within_ref_uncertainty": sum(bool(r["published"]["within_ref_u"]) for r in pub),
        "beats_median_expert": sum(1 for r in est if abs(r["estimate"]["err_pct"]) < r["expert_median_err_pct"]),
        "mean_beats_share": round(statistics.mean(r["estimate"]["beats_share"] for r in est), 3) if est else None,
        "cost_usd": round(sum(r.get("cost_usd", 0) for r in rows), 3),
    }


def main():
    keys = json.loads(KEYS.read_text(encoding="utf-8"))
    out = {"benchmark": "Video Globe Challenge 2020 (Bodart, Le Coz, Jodeau & Hauet; doi:10.57745/KVNIHX)",
           "protocol": "Videos 1 and 8 were used for development. The agent, policy and parameters were frozen "
                       "(git tag eval-freeze) before videos 2-7 were scored once per brain. Every run is in eval/runs.jsonl.",
           "experts": expert_stats(keys), "runs": {}, "refusal": {}}
    md = ["| Run | Videos published | MAE published (%) | Beats median expert | Mean share of experts beaten |", "|---|---|---|---|---|"]
    for tag, (brain, label) in RUNS.items():
        rows = _load(RES / tag / "summary.json")
        if not rows:
            continue
        s = summarise(rows)
        out["runs"][tag] = {"brain": brain, "label": label, "summary": s, "rows": [
            {k: r[k] for k in ("video", "split", "site", "decision", "method", "basis", "reasons", "q_published",
                               "q_estimate", "q_ref", "u_ref", "published", "estimate", "expert_median_err_pct",
                               "estimates", "cost_usd", "seconds", "run_id")} for r in rows]}
        md.append(f"| {tag} ({label}) | {s['published']}/{s['test_videos']} | {s['mae_published_pct']} | "
                  f"{s['beats_median_expert']}/{s['test_videos']} | {s['mean_beats_share']} |")
    for tag in REFUSAL:
        s = _load(RES / tag / "summary.json")
        if s:
            out["refusal"][tag] = {"cases": s["cases"], "correct": s["correct"], "wrong_publishes": s["wrong_publishes"],
                                   "rows": [{k: r[k] for k in ("case", "transform", "expected", "decision", "reasons",
                                                               "correct", "q")} for r in s["rows"]]}
    (RES / "benchmark.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    (RES / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
