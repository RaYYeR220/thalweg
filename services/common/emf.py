"""CloudWatch Embedded Metric Format.

Lambda ships stdout to CloudWatch Logs, which extracts these documents into
metrics in the `Thalweg` namespace. The custom-metric set is deliberately small:
`Decision{kind}` (3 series), `GaugingLatency`, `LLMCostUSD`, `PolicyDenials`.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from . import config


def _emit(dimensions: list[list[str]], metrics: list[tuple[str, float, str]], props: dict[str, Any]) -> dict:
    doc: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": config.metrics_namespace(),
                    "Dimensions": dimensions,
                    "Metrics": [{"Name": name, "Unit": unit} for name, _, unit in metrics],
                }
            ],
        }
    }
    doc.update(props)
    for name, value, _ in metrics:
        doc[name] = value
    sys.stdout.write(json.dumps(doc, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return doc


def decision(kind: str, **props: Any) -> dict:
    return _emit([["kind"]], [("Decision", 1, "Count")], {"kind": kind, **props})


def run_metrics(
    latency_s: float | None,
    cost_usd: float | None,
    policy_denials: int | None,
    **props: Any,
) -> dict | None:
    metrics: list[tuple[str, float, str]] = []
    if latency_s is not None:
        metrics.append(("GaugingLatency", round(float(latency_s), 3), "Seconds"))
    if cost_usd is not None:
        metrics.append(("LLMCostUSD", round(float(cost_usd), 6), "None"))
    if policy_denials is not None:
        metrics.append(("PolicyDenials", int(policy_denials), "Count"))
    if not metrics:
        return None
    return _emit([[]], metrics, props)
