"""HTTP API (payload 2.0) event builder and a router caller for tests."""

from __future__ import annotations

import json


def http_event(route: str, *, path: dict | None = None, query: dict | None = None, body=None, headers=None) -> dict:
    method, raw = route.split(" ", 1)
    return {
        "version": "2.0",
        "routeKey": route,
        "rawPath": raw,
        "pathParameters": path,
        "queryStringParameters": query,
        "headers": headers or {"content-type": "application/json"},
        "body": json.dumps(body) if body is not None and not isinstance(body, str) else body,
        "isBase64Encoded": False,
        "requestContext": {"http": {"method": method, "path": raw}},
    }


def call(route: str, **kw) -> tuple[int, dict]:
    from api import router

    resp = router.handler(http_event(route, **kw))
    return resp["statusCode"], json.loads(resp["body"])
