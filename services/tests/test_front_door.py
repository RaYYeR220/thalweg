import base64
import json

import pytest
from _http import http_event
from conftest import ARTIFACTS, WEB

from api import router, static


def _get(route: str, proxy: str | None = None, headers=None) -> dict:
    ev = http_event(route, path={"proxy": proxy} if proxy is not None else None, headers=headers or {})
    return static.handler(ev)


@pytest.mark.parametrize(
    "path,key",
    [("", "index.html"), ("/", "index.html"), ("stations/dart/", "stations/dart/index.html"), ("stations/dart", "stations/dart/index.html"), ("_next/static/a.js", "_next/static/a.js")],
)
def test_web_key_matches_the_cdn_function(path, key):
    assert static.web_key(path) == key


def test_serves_the_static_export(env):
    env.s3.put_object(Bucket=WEB, Key="index.html", Body=b"<!doctype html><h1>Thalweg</h1>", ContentType="text/html")
    env.s3.put_object(Bucket=WEB, Key="stations/dart/index.html", Body=b"<h1>Dart</h1>", ContentType="text/html")
    env.s3.put_object(Bucket=WEB, Key="_next/static/app.js", Body=b"console.log(1)")
    env.s3.put_object(Bucket=WEB, Key="hero.png", Body=b"\x89PNG\r\n\x1a\n\x00\x01", ContentType="image/png")

    root = _get("GET /")
    assert root["statusCode"] == 200 and "Thalweg" in root["body"] and root["headers"]["cache-control"] == "no-cache"
    page = _get("GET /{proxy+}", "stations/dart")
    assert page["statusCode"] == 200 and "Dart" in page["body"]
    js = _get("GET /{proxy+}", "_next/static/app.js")
    assert js["headers"]["content-type"] == "application/javascript" and "immutable" in js["headers"]["cache-control"]
    png = _get("GET /{proxy+}", "hero.png")
    assert png["isBase64Encoded"] is True and base64.b64decode(png["body"]).startswith(b"\x89PNG")
    again = _get("GET /{proxy+}", "hero.png", headers={"if-none-match": png["headers"]["etag"]})
    assert again["statusCode"] == 304


def test_missing_pages_are_404(env):
    assert _get("GET /{proxy+}", "nowhere/")["statusCode"] == 404
    env.s3.put_object(Bucket=WEB, Key="404.html", Body=b"<p>lost</p>", ContentType="text/html")
    lost = _get("GET /{proxy+}", "nowhere/")
    assert lost["statusCode"] == 404 and "lost" in lost["body"]


def test_artifacts_only_public_prefixes(env):
    env.put_json(ARTIFACTS, "runs/r-1/trace.json", {"steps": []})
    env.put_json(ARTIFACTS, "data/dart/replay-manifest.json", {"clips": []})
    ok = _get("GET /artifacts/{proxy+}", "runs/r-1/trace.json")
    assert ok["statusCode"] == 200 and json.loads(ok["body"]) == {"steps": []}
    assert _get("GET /artifacts/{proxy+}", "data/dart/replay-manifest.json")["statusCode"] == 404
    assert _get("GET /artifacts/{proxy+}", "runs/../data/x")["statusCode"] == 404
    assert _get("GET /artifacts/{proxy+}", "runs/")["statusCode"] == 404
    assert _get("GET /artifacts/{proxy+}", "runs/r-1/missing.jpg")["statusCode"] == 404


def test_large_objects_redirect(env, monkeypatch):
    monkeypatch.setattr(static, "INLINE_MAX_BYTES", 8)
    env.s3.put_object(Bucket=ARTIFACTS, Key="runs/r-1/field.jpg", Body=b"0123456789abcdef", ContentType="image/jpeg")
    resp = _get("GET /artifacts/{proxy+}", "runs/r-1/field.jpg")
    assert resp["statusCode"] == 302 and "X-Amz-Signature=" in resp["headers"]["location"]


def test_api_prefix_maps_onto_the_bare_routes(env):
    env.add_station("dart")

    def call(method: str, proxy: str, query=None):
        ev = http_event("ANY /api/{proxy+}", path={"proxy": proxy}, query=query)
        ev["requestContext"]["http"]["method"] = method
        resp = router.handler(ev)
        return resp["statusCode"], json.loads(resp["body"])

    assert call("GET", "health")[1]["ok"] is True
    assert call("GET", "stations")[1]["stations"][0]["id"] == "dart"
    assert call("GET", "stations/dart") == (200, {"id": "dart", "name": "Dart"})
    status, body = call("GET", "stations/dart/gaugings", {"limit": "5"})
    assert status == 200 and body["station_id"] == "dart"
    assert call("DELETE", "stations/dart")[0] == 404
    assert call("GET", "stations/dart/nope")[0] == 404
