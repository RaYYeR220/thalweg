import json

import pytest
from conftest import ARTIFACTS

from workflow import signature

DRAFT = {
    "id": "w-dart-20181012T120000",
    "station_id": "dart",
    "run_id": "r-20261026090000-abc123",
    "created": "2026-10-26T09:01:00Z",
    "level_now": 2.31,
    "q_now": 180.0,
    "eta_s": 5400.0,
    "p_cross": 0.8,
    "text": "River Dart at Austins Bridge forecast to cross the warning stage in about 90 minutes.",
    "evidence": ["runs/r-20261026090000-abc123/field.jpg"],
    "expires": "2026-10-26T15:01:00Z",
}


def test_await_stores_a_draft_with_its_token(env):
    out = signature.await_handler({"task_token": "tok-1", "run_id": DRAFT["run_id"], "warning_draft": DRAFT})
    assert out == {"id": DRAFT["id"], "state": "DRAFT"}
    item = env.table("thalweg-warnings").get_item(Key={"id": DRAFT["id"]})["Item"]
    assert item["state"] == "DRAFT" and item["task_token"] == "tok-1"


def test_await_refuses_to_reopen_a_signed_warning(env):
    env.table("thalweg-warnings").put_item(Item={"id": DRAFT["id"], "state": "SIGNED", "created": DRAFT["created"]})
    with pytest.raises(signature.WarningConflict):
        signature.await_handler({"task_token": "tok-2", "warning_draft": DRAFT})


def test_issue_signs_and_publishes(env):
    signature.await_handler({"task_token": "tok-1", "warning_draft": DRAFT})
    out = signature.issue_handler({"warning_draft": DRAFT, "signature": {"signed_by_note": "duty hydrologist", "signed_at": "2026-10-26T09:10:00Z"}})

    assert out["state"] == "SIGNED" and out["public_count"] == 1
    item = env.table("thalweg-warnings").get_item(Key={"id": DRAFT["id"]})["Item"]
    assert item["state"] == "SIGNED" and item["signed_by"] == "duty hydrologist"
    assert "task_token" not in item
    doc = env.get_json(ARTIFACTS, "public/warnings.json")
    assert [w["id"] for w in doc["warnings"]] == [DRAFT["id"]]
    assert "task_token" not in doc["warnings"][0]
    head = env.s3.head_object(Bucket=ARTIFACTS, Key="public/warnings.json")
    assert head["CacheControl"] == "public, max-age=60"
    # A retry of the same step is harmless.
    assert signature.issue_handler({"warning_draft": DRAFT, "signature": {}})["state"] == "SIGNED"


def test_reject_and_expire(env):
    signature.await_handler({"task_token": "tok-1", "warning_draft": DRAFT})
    out = signature.close_handler(
        {"outcome": "REJECTED", "warning_draft": DRAFT, "error": {"Error": "Rejected", "Cause": json.dumps({"note": "gauge drift, not a real rise"})}}
    )
    assert out["state"] == "REJECTED"
    item = env.table("thalweg-warnings").get_item(Key={"id": DRAFT["id"]})["Item"]
    assert item["state"] == "REJECTED" and item["rejected_note"] == "gauge drift, not a real rise"
    assert "task_token" not in item

    # A late timeout does not overwrite the reviewer's outcome.
    out = signature.close_handler({"outcome": "EXPIRED", "warning_draft": DRAFT, "error": {"Error": "States.Timeout"}})
    assert out["state"] == "unchanged"
    assert env.table("thalweg-warnings").get_item(Key={"id": DRAFT["id"]})["Item"]["state"] == "REJECTED"


def test_expire_a_draft(env):
    signature.await_handler({"task_token": "tok-1", "warning_draft": DRAFT})
    assert signature.close_handler({"outcome": "EXPIRED", "warning_draft": DRAFT, "error": {"Error": "States.Timeout"}})["state"] == "EXPIRED"
    item = env.table("thalweg-warnings").get_item(Key={"id": DRAFT["id"]})["Item"]
    assert item["state"] == "EXPIRED" and item["close_reason"] == "States.Timeout"
