"""thalweg-web: serves the product from the HTTP API when no CDN sits in front of it.

Same URL layout as the CloudFront distribution, so the web app does not care which front
door it is behind:
    /             -> s3://thalweg-web-<acct>/  (static export; directory paths get index.html)
    /artifacts/*  -> s3://thalweg-artifacts-<acct>/  (runs/, stations/, public/ only)
    /api/*        -> thalweg-api (routed in api.router)

Objects up to 4 MiB are returned inline; larger ones redirect to a short-lived presigned URL.
"""

from __future__ import annotations

import base64
import mimetypes
import os

from botocore.exceptions import ClientError

from common import aws, config

INLINE_MAX_BYTES = 4 * 1024 * 1024
ARTIFACT_PREFIXES = ("runs/", "stations/", "public/")
TEXT_TYPES = ("text/", "application/json", "application/javascript", "image/svg+xml", "application/xml")

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/json", ".map")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("text/plain", ".txt")


def web_bucket() -> str:
    return os.environ["WEB_BUCKET"]


def _plain(status: int, text: str, cache: str = "no-store") -> dict:
    return {"statusCode": status, "headers": {"content-type": "text/plain; charset=utf-8", "cache-control": cache}, "body": text}


def web_key(path: str) -> str:
    """Mirror of infra/functions/web-index.js."""
    key = path.lstrip("/")
    if key == "" or key.endswith("/"):
        return key + "index.html"
    if "." not in key.rsplit("/", 1)[-1]:
        return key + "/index.html"
    return key


def artifact_key(path: str) -> str | None:
    key = path.lstrip("/")
    if ".." in key or not any(key.startswith(p) and len(key) > len(p) for p in ARTIFACT_PREFIXES):
        return None
    return key


def _default_cache(key: str, content_type: str) -> str:
    if content_type.startswith("text/html"):
        return "no-cache"
    if key.startswith("_next/static/"):
        return "public, max-age=31536000, immutable"
    return "public, max-age=300"


def serve_object(bucket: str, key: str, event: dict, artifact: bool) -> dict | None:
    """The object as an HTTP API response, or None if it does not exist."""
    s3 = aws.client("s3")
    headers_in = event.get("headers") or {}
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound", "403"):
            return None
        raise
    etag = head.get("ETag")
    content_type = head.get("ContentType") or ""
    if not content_type or content_type in ("binary/octet-stream", "application/octet-stream"):
        content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    cache = head.get("CacheControl") or ("public, max-age=86400" if artifact else _default_cache(key, content_type))
    if etag and headers_in.get("if-none-match") == etag:
        return {"statusCode": 304, "headers": {"etag": etag, "cache-control": cache}, "body": ""}
    size = int(head.get("ContentLength", 0))
    if size > INLINE_MAX_BYTES:
        url = s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=900)
        return {"statusCode": 302, "headers": {"location": url, "cache-control": "private, max-age=60"}, "body": ""}
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    headers = {"content-type": content_type, "cache-control": cache, "x-content-type-options": "nosniff"}
    if etag:
        headers["etag"] = etag
    if content_type.startswith(TEXT_TYPES):
        try:
            return {"statusCode": 200, "headers": headers, "body": body.decode("utf-8"), "isBase64Encoded": False}
        except UnicodeDecodeError:
            pass
    return {"statusCode": 200, "headers": headers, "body": base64.b64encode(body).decode(), "isBase64Encoded": True}


def handler(event: dict, _context=None) -> dict:
    route = event.get("routeKey", "")
    proxy = (event.get("pathParameters") or {}).get("proxy", "")
    if route == "GET /artifacts/{proxy+}":
        key = artifact_key(proxy)
        if key is None:
            return _plain(404, "not found")
        return serve_object(config.artifacts_bucket(), key, event, artifact=True) or _plain(404, "not found", "public, max-age=10")

    key = web_key(proxy if route == "GET /{proxy+}" else "")
    resp = serve_object(web_bucket(), key, event, artifact=False)
    if resp is not None:
        return resp
    page = serve_object(web_bucket(), "404.html", event, artifact=False)
    if page is not None and page["statusCode"] == 200:
        page["statusCode"] = 404
        page["headers"]["cache-control"] = "no-cache"
        return page
    return _plain(404, "not found", "no-cache")
