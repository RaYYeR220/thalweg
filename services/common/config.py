"""Resource names, read from the Lambda environment (set by the CDK app)."""

from __future__ import annotations

import os


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"missing environment variable {name}")
    return value


def table_stations() -> str:
    return env("TABLE_STATIONS", "thalweg-stations")


def table_gaugings() -> str:
    return env("TABLE_GAUGINGS", "thalweg-gaugings")


def table_runs() -> str:
    return env("TABLE_RUNS", "thalweg-runs")


def table_warnings() -> str:
    return env("TABLE_WARNINGS", "thalweg-warnings")


def table_budget() -> str:
    return env("TABLE_BUDGET", "thalweg-budget")


def ingest_bucket() -> str:
    return env("INGEST_BUCKET")


def artifacts_bucket() -> str:
    return env("ARTIFACTS_BUCKET")


def state_machine_arn() -> str:
    return env("STATE_MACHINE_ARN")


def metrics_namespace() -> str:
    return env("METRICS_NAMESPACE", "Thalweg")
