import json

from conftest import INGEST

from common import runs
from workflow import start_run


def _event(key: str, etag: str = "e1") -> dict:
    return {
        "source": "aws.s3",
        "detail-type": "Object Created",
        "time": "2026-10-26T09:15:00Z",
        "detail": {"bucket": {"name": INGEST}, "object": {"key": key, "size": 120, "etag": etag, "sequencer": "0A"}},
    }


def test_a_sidecar_starts_an_autopilot_run(env):
    env.add_clip("dart", "20181012T120000")
    out = start_run.handler(_event("clips/dart/20181012T120000.json"))

    assert runs.RUN_ID_RE.match(out["run_id"]) and out["run_id"].startswith("r-20261026091500-")
    (call,) = env.sfn.named("start_execution")
    payload = json.loads(call["input"])
    assert call["name"] == out["run_id"]
    assert payload == {
        "run_id": out["run_id"],
        "station_id": "dart",
        "clip_key": "clips/dart/20181012T120000.mp4",
        "sidecar_key": "clips/dart/20181012T120000.json",
        "brain": "autopilot",
        "requested_by": "schedule",
    }


def test_the_sidecar_can_choose_the_llm_brain(env):
    env.add_clip("dart", "20181012T121500", brain="llm", model="claude-haiku-5-5", requested_by="schedule")
    start_run.handler(_event("clips/dart/20181012T121500.json"))
    payload = json.loads(env.sfn.named("start_execution")[0]["input"])
    assert payload["brain"] == "llm" and payload["model"] == "claude-haiku-5-5"


def test_a_duplicate_event_does_not_start_a_second_run(env):
    env.add_clip("dart", "20181012T120000")
    first = start_run.handler(_event("clips/dart/20181012T120000.json"))
    again = start_run.handler(_event("clips/dart/20181012T120000.json"))
    assert again == {"skipped": "in_flight", "run_id": first["run_id"]}
    assert len(env.sfn.named("start_execution")) == 1


def test_videos_and_foreign_keys_are_ignored(env):
    assert start_run.handler(_event("clips/dart/20181012T120000.mp4"))["skipped"] == "not_a_sidecar"
    assert start_run.handler(_event("uploads/x.json"))["skipped"] == "not_a_sidecar"
    env.put_json(INGEST, "clips/dart/odd.json", {"station_id": "vgc1", "t": "2018-10-12T12:00:00Z", "level": 1})
    assert start_run.handler(_event("clips/dart/odd.json"))["skipped"] == "station_mismatch"
    assert env.sfn.named("start_execution") == []
