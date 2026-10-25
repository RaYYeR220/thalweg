"""The human-signature leg of the workflow.

`await_handler`  (AwaitSignature, .waitForTaskToken): stores the draft with state DRAFT and the
                 task token. The execution then waits up to 6 h for the review API.
`issue_handler`  (IssueWarning): after a reviewer signed, marks SIGNED and republishes
                 s3://<artifacts>/public/warnings.json.
`close_handler`  (MarkRejected / MarkExpired): REJECTED on the reviewer's `Rejected` failure,
                 EXPIRED on the 6 h timeout.

Nothing here can sign: SIGNED is only reachable through IssueWarning, which only runs after
the review API called SendTaskSuccess with the reviewer passcode checked.
"""

from __future__ import annotations

import json

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common import aws, config, ddb, jsonx, timefmt

PUBLIC_KEY = "public/warnings.json"
PUBLIC_FIELDS = (
    "id", "station_id", "run_id", "created", "level_now", "q_now", "eta_s", "p_cross",
    "text", "evidence", "expires", "state", "signed_by", "signed_at",
)
STATE_NAMES = {"#state": "state"}


class WarningConflict(Exception):
    """A warning with this id exists and is no longer a draft."""


def await_handler(event: dict, _context=None) -> dict:
    draft = event["warning_draft"]
    token = event["task_token"]
    item = {**draft, "run_id": event.get("run_id", draft.get("run_id")), "state": "DRAFT", "task_token": token}
    try:
        aws.table(config.table_warnings()).put_item(
            Item=jsonx.to_ddb(item),
            ConditionExpression="attribute_not_exists(id) OR #state = :draft",
            ExpressionAttributeNames=STATE_NAMES,
            ExpressionAttributeValues={":draft": "DRAFT"},
        )
    except ClientError as err:
        if ddb.is_conditional(err):
            raise WarningConflict(draft["id"]) from None
        raise
    return {"id": draft["id"], "state": "DRAFT"}


def issue_handler(event: dict, _context=None) -> dict:
    draft = event["warning_draft"]
    sig = event.get("signature") or {}
    note = str(sig.get("signed_by_note") or "reviewer")[:280]
    signed_at = sig.get("signed_at") or timefmt.iso()
    try:
        ddb.update_fields(
            config.table_warnings(),
            {"id": draft["id"]},
            {"state": "SIGNED", "signed_by": note, "signed_at": signed_at},
            remove=("task_token", "sign_lock", "sign_lock_at"),
            condition="#cs = :draft",
            names={"#cs": "state"},
            values={":draft": "DRAFT"},
        )
    except ClientError as err:
        if not ddb.is_conditional(err):
            raise
        current = aws.table(config.table_warnings()).get_item(Key={"id": draft["id"]}).get("Item") or {}
        if current.get("state") != "SIGNED":  # a retry after success is fine; anything else is not
            raise WarningConflict(f"{draft['id']} is {current.get('state')}") from None
    count = publish_signed()
    return {"id": draft["id"], "state": "SIGNED", "public_count": count}


def publish_signed(limit: int = 200) -> int:
    """Rebuild public/warnings.json from the SIGNED index (idempotent, newest first)."""
    resp = aws.table(config.table_warnings()).query(
        IndexName="by_state",
        KeyConditionExpression=Key("state").eq("SIGNED"),
        ScanIndexForward=False,
        Limit=limit,
    )
    items = [{k: it[k] for k in PUBLIC_FIELDS if k in it} for it in resp.get("Items", [])]
    doc = {"generated": timefmt.iso(), "warnings": jsonx.plain(items)}
    aws.client("s3").put_object(
        Bucket=config.artifacts_bucket(),
        Key=PUBLIC_KEY,
        Body=json.dumps(doc, indent=1).encode(),
        ContentType="application/json",
        CacheControl="public, max-age=60",
    )
    return len(items)


def _note_from_cause(cause) -> str | None:
    if not cause:
        return None
    try:
        parsed = json.loads(cause)
        if isinstance(parsed, dict):
            return str(parsed.get("note") or parsed.get("errorMessage") or "")[:280] or None
    except (json.JSONDecodeError, TypeError):
        pass
    return str(cause)[:280]


def close_handler(event: dict, _context=None) -> dict:
    outcome = event["outcome"]
    if outcome not in ("REJECTED", "EXPIRED"):
        raise ValueError("outcome must be REJECTED or EXPIRED")
    draft = event["warning_draft"]
    err = event.get("error") or {}
    fields = {"state": outcome, "closed_at": timefmt.iso(), "close_reason": err.get("Error")}
    if outcome == "REJECTED":
        fields["rejected_note"] = _note_from_cause(err.get("Cause"))
    try:
        ddb.update_fields(
            config.table_warnings(),
            {"id": draft["id"]},
            fields,
            remove=("task_token", "sign_lock", "sign_lock_at"),
            condition="#cs = :draft",
            names={"#cs": "state"},
            values={":draft": "DRAFT"},
        )
    except ClientError as e:
        if not ddb.is_conditional(e):
            raise
        return {"id": draft["id"], "state": "unchanged"}
    return {"id": draft["id"], "state": outcome}
