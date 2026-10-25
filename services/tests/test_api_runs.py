import json

import pytest
from _http import call
from conftest import SERVICE_TOKEN

from common import runs


def _post(body, headers=None):
    return call("POST /runs", body=body, headers=headers)


@pytest.fixture
def clip(env):
    env.add_station("dart")
    return env.add_clip("dart", "20181012T120000")


def test_post_runs_starts_one_execution(env, clip):
    status, body = _post({"station_id": "dart", "clip_key": clip, "brain": "autopilot"})

    assert status == 202
    assert body["deduplicated"] is False and runs.RUN_ID_RE.match(body["run_id"])
    (start,) = env.sfn.named("start_execution")
    payload = json.loads(start["input"])
    assert start["name"] == body["run_id"]
    assert payload["requested_by"] == "judge" and payload["brain"] == "autopilot" and "model" not in payload
    assert payload["sidecar_key"] == "clips/dart/20181012T120000.json"


def test_double_click_returns_the_same_run(env, clip):
    _, first = _post({"station_id": "dart", "clip_key": clip, "brain": "autopilot"})
    # Preflight has opened the run by the time the second click lands.
    env.table("thalweg-runs").put_item(Item={"run_id": first["run_id"], "station_id": "dart", "status": "RUNNING", "started": "2026-10-26T09:00:00Z"})

    status, again = _post({"station_id": "dart", "clip_key": clip, "brain": "autopilot"})

    assert status == 200
    assert again == {"run_id": first["run_id"], "status": "RUNNING", "deduplicated": True}
    assert len(env.sfn.named("start_execution")) == 1


def test_double_click_before_preflight_is_also_deduplicated(env, clip):
    _, first = _post({"station_id": "dart", "clip_key": clip, "brain": "autopilot"})
    status, again = _post({"station_id": "dart", "clip_key": clip, "brain": "autopilot"})
    assert status == 200 and again["run_id"] == first["run_id"]
    assert len(env.sfn.named("start_execution")) == 1


def test_other_brain_is_a_different_run(env, clip):
    _, a = _post({"station_id": "dart", "clip_key": clip, "brain": "autopilot"})
    status, b = _post({"station_id": "dart", "clip_key": clip, "brain": "llm"})
    assert status == 202 and b["run_id"] != a["run_id"] and b["model"] == "claude-sonnet-5-5"
    assert len(env.sfn.named("start_execution")) == 2


def test_a_finished_run_releases_the_clip(env, clip):
    _, first = _post({"station_id": "dart", "clip_key": clip, "brain": "autopilot"})
    env.table("thalweg-runs").put_item(Item={"run_id": first["run_id"], "station_id": "dart", "status": "PUBLISH", "started": "2026-10-26T09:00:00Z"})

    status, second = _post({"station_id": "dart", "clip_key": clip, "brain": "autopilot"})
    assert status == 202 and second["run_id"] != first["run_id"]
    assert len(env.sfn.named("start_execution")) == 2


def test_judge_llm_runs_are_capped_per_day(env, clip, monkeypatch):
    monkeypatch.setenv("JUDGE_LLM_DAILY_CAP", "2")
    stamps = ["20181012T121500", "20181012T123000", "20181012T124500"]
    keys = [env.add_clip("dart", s) for s in stamps]

    assert _post({"station_id": "dart", "clip_key": keys[0], "brain": "llm"})[0] == 202
    assert _post({"station_id": "dart", "clip_key": keys[0], "brain": "llm"})[0] == 200  # dedup costs nothing
    assert _post({"station_id": "dart", "clip_key": keys[1], "brain": "llm"})[0] == 202
    status, body = _post({"station_id": "dart", "clip_key": keys[2], "brain": "llm"})
    assert status == 429 and body["error"] == "daily_llm_run_cap"
    # Autopilot runs are not capped, and the refused clip is not left locked.
    assert _post({"station_id": "dart", "clip_key": keys[2], "brain": "autopilot"})[0] == 202
    assert "Item" not in env.table("thalweg-runs").get_item(Key={"run_id": runs.lock_key(keys[2], "llm")})


def test_service_token_marks_api_runs_and_skips_the_judge_cap(env, clip, monkeypatch):
    monkeypatch.setenv("JUDGE_LLM_DAILY_CAP", "0")
    status, _ = _post({"station_id": "dart", "clip_key": clip, "brain": "llm"}, headers={"authorization": f"Bearer {SERVICE_TOKEN}"})
    assert status == 202
    assert json.loads(env.sfn.named("start_execution")[0]["input"])["requested_by"] == "api"
    status, _ = _post({"station_id": "dart", "clip_key": env.add_clip("dart", "20181012T130000"), "brain": "llm"}, headers={"authorization": "Bearer wrong"})
    assert status == 429


def test_start_failure_releases_the_lock(env, clip):
    env.sfn.fail_with = "ThrottlingException"
    status, body = _post({"station_id": "dart", "clip_key": clip, "brain": "autopilot"})
    assert status == 502 and body["error"] == "start_failed"
    assert _post({"station_id": "dart", "clip_key": clip, "brain": "autopilot"})[0] == 202


@pytest.mark.parametrize(
    "body,status,code",
    [
        ({"station_id": "dart", "clip_key": "clips/dart/20181012T120000.mp4", "brain": "gpt"}, 400, "bad_request"),
        ({"station_id": "dart", "clip_key": "clips/dart/20181012T120000.mp4", "brain": "llm", "model": "o9"}, 400, "bad_request"),
        ({"station_id": "dart", "clip_key": "clips/vgc1/x.mp4", "brain": "autopilot"}, 400, "bad_request"),
        ({"station_id": "dart", "clip_key": "../x.mp4"}, 400, "bad_request"),
        ({"station_id": "dart", "clip_key": "clips/dart/missing.mp4"}, 404, "clip_not_found"),
        ({"station_id": "nowhere", "clip_key": "clips/nowhere/x.mp4"}, 404, "station_not_found"),
    ],
)
def test_post_runs_validation(env, clip, body, status, code):
    got_status, got = _post(body)
    assert (got_status, got["error"]) == (status, code)
    assert env.sfn.named("start_execution") == []


def test_post_runs_needs_a_sidecar(env, clip):
    env.s3.put_object(Bucket="thalweg-ingest-test", Key="clips/dart/lonely.mp4", Body=b"x")
    status, body = _post({"station_id": "dart", "clip_key": "clips/dart/lonely.mp4"})
    assert status == 422 and body["error"] == "sidecar_missing"


def test_post_runs_rejects_non_json(env):
    assert call("POST /runs", body="not json")[0] == 400
