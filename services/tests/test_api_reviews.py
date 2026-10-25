import json

import pytest
from _http import call
from conftest import PASSCODE


def _draft(env, wid="w-dart-1", state="DRAFT", token="tok-1", created="2026-10-26T09:01:00Z"):
    item = {"id": wid, "station_id": "dart", "run_id": "r-20261026090000-abc123", "state": state, "created": created, "text": "Rising fast."}
    if token:
        item["task_token"] = token
    env.table("thalweg-warnings").put_item(Item=item)


def _act(wid, **body):
    return call("POST /reviews/{id}", path={"id": wid}, body=body)


def test_wrong_passcode_is_403(env):
    _draft(env)
    for passcode in ("nope", "", None):
        status, body = _act("w-dart-1", action="sign", passcode=passcode, note="x")
        assert status == 403 and body["error"] == "bad_passcode"
    assert env.sfn.calls == []


@pytest.mark.parametrize("state", ["SIGNED", "REJECTED", "EXPIRED"])
def test_acting_on_a_closed_warning_is_409(env, state):
    _draft(env, state=state, token=None)
    status, body = _act("w-dart-1", action="sign", passcode=PASSCODE, note="me")
    assert status == 409 and body == {"error": "not_draft", "message": f"warning is {state}", "state": state}
    assert env.sfn.calls == []


def test_sign_sends_task_success_once(env):
    _draft(env)
    status, body = _act("w-dart-1", action="sign", passcode=PASSCODE, note="  Duty hydrologist, EA SW  ")

    assert status == 200 and body == {"id": "w-dart-1", "action": "sign", "accepted": True, "next_state": "SIGNED"}
    (sent,) = env.sfn.named("send_task_success")
    assert sent["taskToken"] == "tok-1"
    assert json.loads(sent["output"])["signed_by_note"] == "Duty hydrologist, EA SW"

    # Double click while the workflow is still issuing: 409, no second callback.
    status, body = _act("w-dart-1", action="sign", passcode=PASSCODE, note="again")
    assert status == 409 and body["error"] == "not_draft"
    assert len(env.sfn.named("send_task_success")) == 1


def test_a_stale_lock_does_not_block_forever(env):
    _draft(env)
    env.table("thalweg-warnings").update_item(
        Key={"id": "w-dart-1"},
        UpdateExpression="SET sign_lock = :l, sign_lock_at = :t",
        ExpressionAttributeValues={":l": "sign|2026-10-26T09:00:00Z", ":t": 1},
    )
    status, _ = _act("w-dart-1", action="sign", passcode=PASSCODE, note="retry after a crash")
    assert status == 200 and len(env.sfn.named("send_task_success")) == 1


def test_reject_sends_task_failure(env):
    _draft(env)
    status, body = _act("w-dart-1", action="reject", passcode=PASSCODE, note="sensor spike")
    assert status == 200 and body["next_state"] == "REJECTED"
    (sent,) = env.sfn.named("send_task_failure")
    assert sent["error"] == "Rejected" and json.loads(sent["cause"]) == {"note": "sensor spike"}


def test_a_closed_signature_window_is_409_and_unlocks(env):
    _draft(env)
    env.sfn.fail_with = "TaskTimedOut"
    status, body = _act("w-dart-1", action="sign", passcode=PASSCODE)
    assert status == 409 and body["error"] == "not_draft"
    assert "sign_lock" not in env.table("thalweg-warnings").get_item(Key={"id": "w-dart-1"})["Item"]


def test_unknown_warning_and_bad_action(env):
    assert _act("w-missing", action="sign", passcode=PASSCODE)[0] == 404
    _draft(env)
    assert _act("w-dart-1", action="approve", passcode=PASSCODE)[0] == 400


def test_list_reviews_hides_task_tokens(env):
    _draft(env, "w-a", created="2026-10-26T09:01:00Z")
    _draft(env, "w-b", created="2026-10-26T09:05:00Z")
    _draft(env, "w-c", state="SIGNED", token=None)

    status, body = call("GET /reviews", query={"state": "DRAFT"})
    assert status == 200 and body["state"] == "DRAFT"
    assert [r["id"] for r in body["reviews"]] == ["w-b", "w-a"]
    assert all("task_token" not in r for r in body["reviews"])

    assert [r["id"] for r in call("GET /reviews", query={"state": "SIGNED"})[1]["reviews"]] == ["w-c"]
    assert call("GET /reviews")[1]["state"] == "DRAFT"
    assert call("GET /reviews", query={"state": "OPEN"})[0] == 400
