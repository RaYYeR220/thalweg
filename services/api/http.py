"""Request/response plumbing for API Gateway HTTP API (payload format 2.0)."""

from __future__ import annotations

import base64
import json
from typing import Any

from common import jsonx

MAX_BODY_BYTES = 16 * 1024


class HttpError(Exception):
    def __init__(self, status: int, code: str, message: str | None = None, **extra: Any):
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra


def respond(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": jsonx.dumps(body),
    }


def error(err: HttpError) -> dict:
    body = {"error": err.code}
    if err.message:
        body["message"] = err.message
    body.update(err.extra)
    return respond(err.status, body)


def json_body(event: dict) -> dict:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise HttpError(400, "bad_body", "body is not valid base64 UTF-8") from None
    if len(raw.encode("utf-8")) > MAX_BODY_BYTES:
        raise HttpError(413, "body_too_large")
    if not raw.strip():
        raise HttpError(400, "bad_body", "JSON object body required")
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        raise HttpError(400, "bad_body", "body is not JSON") from None
    if not isinstance(doc, dict):
        raise HttpError(400, "bad_body", "JSON object body required")
    return doc


def query(event: dict) -> dict:
    return event.get("queryStringParameters") or {}


def path_param(event: dict, name: str) -> str:
    value = (event.get("pathParameters") or {}).get(name)
    if not value:
        raise HttpError(400, "bad_path", f"missing {name}")
    return value


def int_param(q: dict, name: str, default: int, lo: int, hi: int) -> int:
    raw = q.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise HttpError(400, "bad_query", f"{name} must be an integer") from None
    if not lo <= value <= hi:
        raise HttpError(400, "bad_query", f"{name} must be in [{lo}, {hi}]")
    return value


def header(event: dict, name: str) -> str | None:
    headers = event.get("headers") or {}
    return headers.get(name.lower()) or headers.get(name)
