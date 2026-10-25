import json
from decimal import Decimal

import pytest

from workflow import persist
from workflow.errors import AgentOutputInvalid

RUN_ID = "r-20261026090000-abc123"


def _state(agent: dict) -> dict:
    return {
        "run_id": RUN_ID,
        "station_id": "dart",
        "clip_key": "clips/dart/20181012T120000.mp4",
        "brain": "autopilot",
        "t": "2018-10-12T12:00:00Z",
        "level": 1.42,
        "started": "2026-10-26T09:00:00Z",
        "agent": agent,
    }


def _publish(**over) -> dict:
    agent = {
        "run_id": RUN_ID,
        "decision": {"kind": "PUBLISH", "reasons": ["methods_agree"], "q": 61.2, "q_lo": 55.0, "q_hi": 67.0, "method": "lspiv+stiv"},
        "gauging": {"station_id": "dart", "t": "2018-10-12T12:00:00Z", "level": 1.42, "q": 61.2, "q_lo": 55.0, "q_hi": 67.0, "method": "lspiv+stiv", "run_id": RUN_ID},
        "trace_key": f"runs/{RUN_ID}/trace.json",
        "cost_usd": 0.0031,
        "timings": {"total_s": 41.5},
        "policy_denials": 1,
    }
    agent.update(over)
    return agent


def _hold(**over) -> dict:
    agent = {
        "run_id": RUN_ID,
        "decision": {"kind": "HOLD", "reasons": ["glare_frac 0.58 > 0.35"]},
        "trace_key": f"runs/{RUN_ID}/trace.json",
        "cost_usd": 0,
        "timings": {"total_s": 12.0},
    }
    agent.update(over)
    return agent


def _metrics(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]


def test_publish_writes_the_gauging(env, capsys):
    env.add_station("dart")
    out = persist.handler(_state(_publish()))

    assert out == {"run_id": RUN_ID, "station_id": "dart", "kind": "PUBLISH"}
    g = env.table("thalweg-gaugings").get_item(Key={"station_id": "dart", "t": "2018-10-12T12:00:00Z"})["Item"]
    assert g["q"] == Decimal("61.2") and g["run_id"] == RUN_ID and g["method"] == "lspiv+stiv"

    run = env.table("thalweg-runs").get_item(Key={"run_id": RUN_ID})["Item"]
    assert run["status"] == "PUBLISH"
    assert run["decision"]["kind"] == "PUBLISH"
    assert run["trace_key"] == f"runs/{RUN_ID}/trace.json"
    assert run["ended"].endswith("Z")

    st = env.table("thalweg-stations").get_item(Key={"id": "dart"})["Item"]
    assert st["last_decision"] == "PUBLISH" and st["last_q"] == Decimal("61.2")
    assert st["last_t"] == "2018-10-12T12:00:00Z" and st["last_level"] == Decimal("1.42")

    docs = _metrics(capsys)
    decision = [d for d in docs if "Decision" in d]
    assert decision and decision[0]["kind"] == "PUBLISH"
    assert decision[0]["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [["kind"]]
    run_doc = [d for d in docs if "GaugingLatency" in d][0]
    assert run_doc["LLMCostUSD"] == pytest.approx(0.0031) and run_doc["PolicyDenials"] == 1
    names = {m["Name"] for d in docs for m in d["_aws"]["CloudWatchMetrics"][0]["Metrics"]}
    assert names == {"Decision", "GaugingLatency", "LLMCostUSD", "PolicyDenials"}


def test_hold_writes_no_gauging(env):
    env.add_station("dart", last_q=Decimal("40.0"))
    out = persist.handler(_state(_hold()))

    assert out["kind"] == "HOLD" and "warning_draft" not in out
    assert env.table("thalweg-gaugings").scan()["Items"] == []
    run = env.table("thalweg-runs").get_item(Key={"run_id": RUN_ID})["Item"]
    assert run["status"] == "HOLD" and run["decision"]["reasons"] == ["glare_frac 0.58 > 0.35"]
    st = env.table("thalweg-stations").get_item(Key={"id": "dart"})["Item"]
    assert st["last_decision"] == "HOLD" and st["last_q"] == Decimal("40.0")


def test_publish_with_a_warning_draft_goes_to_signature(env):
    env.add_station("dart")
    draft = {"id": "w-dart-20181012T120000", "station_id": "dart", "text": "River Dart at Austins Bridge forecast to cross 2.5 m.", "p_cross": 0.8, "eta_s": 5400, "evidence": [f"runs/{RUN_ID}/field.jpg"]}
    out = persist.handler(_state(_publish(warning_draft=draft)))

    wd = out["warning_draft"]
    assert wd["id"] == "w-dart-20181012T120000" and wd["run_id"] == RUN_ID
    assert wd["expires"] > wd["created"]
    run = env.table("thalweg-runs").get_item(Key={"run_id": RUN_ID})["Item"]
    assert run["warning_id"] == "w-dart-20181012T120000"


def test_a_draft_without_a_publish_is_dropped(env):
    env.add_station("dart")
    out = persist.handler(_state(_hold(warning_draft={"id": "w1", "text": "rising"})))
    assert "warning_draft" not in out
    run = env.table("thalweg-runs").get_item(Key={"run_id": RUN_ID})["Item"]
    assert run["warning_dropped"] == "not_published"


@pytest.mark.parametrize(
    "agent",
    [
        {"decision": {"kind": "MAYBE"}},
        {"decision": {"kind": "PUBLISH", "reasons": []}},  # no gauging
        _publish(gauging={"q": 10.0, "q_lo": 12.0, "method": "lspiv"}),  # q_lo > q
        _publish(gauging={"q": float("nan"), "method": "lspiv"}),
        _publish(run_id="r-20261026090000-ffffff"),
        _hold(gauging={"q": 1.0, "method": "lspiv"}),
        _publish(warning_draft={"id": "w1"}),  # no text
        "not an object",
    ],
)
def test_malformed_results_fail_closed(env, agent):
    env.add_station("dart")
    with pytest.raises(AgentOutputInvalid):
        persist.handler(_state(agent))
    assert env.table("thalweg-gaugings").scan()["Items"] == []
