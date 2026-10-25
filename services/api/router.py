"""thalweg-api: one router Lambda for every HTTP API route.

CloudFront serves this API at /api/* and strips the prefix, so routes here are bare. Without
the CDN, the HTTP API itself also answers under /api/* (see `resolve`).
"""

from __future__ import annotations

import logging
import os
import re

from common import timefmt

from . import reads, reviews, start
from .http import HttpError, error, respond

log = logging.getLogger()
log.setLevel(logging.INFO)


def health(_event: dict) -> tuple[int, dict]:
    return 200, {"ok": True, "service": "thalweg-api", "time": timefmt.iso(), "version": os.environ.get("APP_VERSION", "dev")}


ROUTES = {
    "GET /health": health,
    "GET /stations": reads.list_stations,
    "GET /stations/{id}": reads.get_station,
    "GET /stations/{id}/gaugings": reads.list_gaugings,
    "GET /runs": reads.list_runs,
    "GET /runs/{id}": reads.get_run,
    "POST /runs": start.start_run,
    "GET /reviews": reviews.list_reviews,
    "POST /reviews/{id}": reviews.act,
}


PREFIXED_ROUTE = "ANY /api/{proxy+}"


def _compile(route: str) -> tuple[str, re.Pattern]:
    method, path = route.split(" ", 1)
    pattern = re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>[^/]+)", re.escape(path))
    return method, re.compile(f"^{pattern}$")


_PATTERNS = [(route, *_compile(route)) for route in ROUTES]


def resolve(event: dict) -> dict:
    """Requests reaching the API under /api/* (no CDN in front) are mapped onto the bare routes."""
    if event.get("routeKey") != PREFIXED_ROUTE:
        return event
    method = ((event.get("requestContext") or {}).get("http") or {}).get("method", "")
    path = "/" + ((event.get("pathParameters") or {}).get("proxy") or "").strip("/")
    for route, route_method, pattern in _PATTERNS:
        m = pattern.match(path)
        if m and route_method == method:
            return {**event, "routeKey": route, "pathParameters": m.groupdict() or None}
    return {**event, "routeKey": f"{method} {path}"}


def handler(event: dict, _context=None) -> dict:
    event = resolve(event)
    route = event.get("routeKey", "")
    fn = ROUTES.get(route)
    if fn is None:
        return error(HttpError(404, "not_found"))
    try:
        status, body = fn(event)
        return respond(status, body)
    except HttpError as err:
        if err.status >= 500:
            log.error("%s -> %s %s", route, err.status, err.code)
        return error(err)
    except Exception:
        log.exception("unhandled error on %s", route)
        return error(HttpError(500, "internal_error"))
