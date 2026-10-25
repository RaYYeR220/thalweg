"""The review queue: list warning drafts and sign or reject them.

Signing is the only path to a public flood warning. The reviewer passcode is checked
first (403), then the warning must still be a DRAFT (409). A short-lived `sign_lock`
attribute makes a double click land as 409 instead of a second callback; the state
itself only changes inside the workflow (IssueWarning / MarkRejected).
"""

from __future__ import annotations

import hmac
import json
import os
import time

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common import aws, config, ddb, jsonx, params, timefmt

from .http import HttpError, int_param, json_body, path_param, query
from .reads import ID_RE

STATES = ("DRAFT", "SIGNED", "REJECTED", "EXPIRED")
HIDDEN = ("task_token", "sign_lock", "sign_lock_at")
LOCK_STALE_S = 60  # a lock left by a request that died mid-way stops blocking after this
GONE_TOKEN_ERRORS = ("TaskTimedOut", "InvalidToken", "TaskDoesNotExist")


def _public(item: dict) -> dict:
    out = {k: v for k, v in jsonx.plain(item).items() if k not in HIDDEN}
    if item.get("sign_lock"):
        out["pending_action"] = str(item["sign_lock"]).split("|", 1)[0]
    return out


def list_reviews(event: dict) -> tuple[int, dict]:
    q = query(event)
    state = (q.get("state") or "DRAFT").upper()
    if state not in STATES:
        raise HttpError(400, "bad_query", f"state must be one of {list(STATES)}")
    limit = int_param(q, "limit", 100, 1, 500)
    resp = aws.table(config.table_warnings()).query(
        IndexName="by_state",
        KeyConditionExpression=Key("state").eq(state),
        ScanIndexForward=False,
        Limit=limit,
    )
    return 200, {"state": state, "reviews": [_public(it) for it in resp.get("Items", [])]}


def _check_passcode(given) -> None:
    if not isinstance(given, str) or not given:
        raise HttpError(403, "bad_passcode")
    expected = params.get(os.environ.get("PASSCODE_PARAM", "/thalweg/reviewer_passcode"))
    if not hmac.compare_digest(given.strip().encode(), expected.encode()):
        raise HttpError(403, "bad_passcode")


def _state_of(wid: str) -> str | None:
    item = aws.table(config.table_warnings()).get_item(Key={"id": wid}, ConsistentRead=True).get("Item")
    return item.get("state") if item else None


def _unlock(wid: str) -> None:
    ddb.update_fields(config.table_warnings(), {"id": wid}, {}, remove=("sign_lock", "sign_lock_at"))


def act(event: dict) -> tuple[int, dict]:
    wid = path_param(event, "id")
    if not ID_RE.match(wid):
        raise HttpError(400, "bad_path", "id has invalid characters")
    body = json_body(event)
    action = body.get("action")
    if action not in ("sign", "reject"):
        raise HttpError(400, "bad_request", "action must be sign or reject")
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise HttpError(400, "bad_request", "note must be a string")
    note = (note or "").strip()[:280]

    _check_passcode(body.get("passcode"))

    table = aws.table(config.table_warnings())
    item = table.get_item(Key={"id": wid}, ConsistentRead=True).get("Item")
    if not item:
        raise HttpError(404, "review_not_found")
    if item.get("state") != "DRAFT" or not item.get("task_token"):
        raise HttpError(409, "not_draft", f"warning is {item.get('state')}", state=item.get("state"))

    now = timefmt.iso()
    now_s = int(time.time())
    try:
        ddb.update_fields(
            config.table_warnings(),
            {"id": wid},
            {"sign_lock": f"{action}|{now}", "sign_lock_at": now_s},
            condition="#cs = :draft AND (attribute_not_exists(sign_lock) OR sign_lock_at < :stale)",
            names={"#cs": "state"},
            values={":draft": "DRAFT", ":stale": now_s - LOCK_STALE_S},
        )
    except ClientError as err:
        if ddb.is_conditional(err):
            state = _state_of(wid)
            raise HttpError(409, "not_draft", "this warning is already being acted on", state=state) from None
        raise

    sfn = aws.client("stepfunctions")
    token = str(item["task_token"])
    try:
        if action == "sign":
            sfn.send_task_success(
                taskToken=token,
                output=json.dumps({"signed_by_note": note or "reviewer", "signed_at": now}),
            )
        else:
            sfn.send_task_failure(taskToken=token, error="Rejected", cause=json.dumps({"note": note}))
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code")
        _unlock(wid)
        if code in GONE_TOKEN_ERRORS:
            # The 6 h window closed under us; the workflow marks it EXPIRED.
            raise HttpError(409, "not_draft", "the signature window has closed", state=_state_of(wid) or "EXPIRED") from None
        raise HttpError(502, "callback_failed", code or "error") from None

    return 200, {
        "id": wid,
        "action": action,
        "accepted": True,
        "next_state": "SIGNED" if action == "sign" else "REJECTED",
    }
