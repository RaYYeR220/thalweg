import pytest
from conftest import ARTIFACTS, INGEST

from workflow import preflight
from workflow.errors import BadInput, BadSidecar, ClipNotReady, StationNotFound


def _input(**over):
    base = {
        "run_id": "r-20261026090000-abc123",
        "station_id": "dart",
        "clip_key": "clips/dart/20181012T120000.mp4",
        "sidecar_key": "clips/dart/20181012T120000.json",
        "brain": "autopilot",
        "requested_by": "schedule",
    }
    base.update(over)
    return {"input": base, "execution_arn": "arn:aws:states:us-east-1:123456789012:execution:thalweg-gauging:x"}


def test_preflight_opens_the_run(env):
    env.add_station("dart", config_key="stations/dart/station.yaml")
    env.add_clip("dart", "20181012T120000", level=1.42, t="2018-10-12T12:00:00+00:00")

    out = preflight.handler(_input(model="claude-haiku-5-5", brain="llm"))

    assert out["t"] == "2018-10-12T12:00:00Z"
    assert out["level"] == pytest.approx(1.42)
    assert out["ingest_bucket"] == INGEST and out["artifacts_bucket"] == ARTIFACTS
    assert out["station_config_key"] == "stations/dart/station.yaml"
    assert out["clip_bytes"] > 0
    run = env.table("thalweg-runs").get_item(Key={"run_id": "r-20261026090000-abc123"})["Item"]
    assert run["status"] == "RUNNING"
    assert run["brain"] == "llm" and run["model"] == "claude-haiku-5-5"
    assert run["started"].endswith("Z")
    assert run["execution_arn"].endswith(":x")


def test_unknown_station_is_rejected(env):
    env.add_clip("dart")
    with pytest.raises(StationNotFound):
        preflight.handler(_input())


def test_missing_clip_is_retryable(env):
    env.add_station("dart")
    with pytest.raises(ClipNotReady):
        preflight.handler(_input())
    assert "Item" not in env.table("thalweg-runs").get_item(Key={"run_id": "r-20261026090000-abc123"})


def test_sidecar_without_level_is_rejected(env):
    env.add_station("dart")
    env.add_clip("dart", level=None)
    with pytest.raises(BadSidecar):
        preflight.handler(_input())


def test_sidecar_for_another_station_is_rejected(env):
    env.add_station("dart")
    env.add_clip("dart", station_id="vgc1")
    with pytest.raises(BadSidecar):
        preflight.handler(_input())


@pytest.mark.parametrize(
    "over",
    [{"brain": "gpt"}, {"requested_by": "someone"}, {"run_id": "nope"}, {"clip_key": "../etc/passwd"}, {"station_id": ""}],
)
def test_bad_input_is_rejected(env, over):
    env.add_station("dart")
    env.add_clip("dart")
    with pytest.raises(BadInput):
        preflight.handler(_input(**over))
