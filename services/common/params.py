"""SSM SecureString parameters, cached per container for a few minutes."""

from __future__ import annotations

import time

from . import aws

_TTL_S = 300
_cache: dict[str, tuple[float, str]] = {}


def get(name: str) -> str:
    hit = _cache.get(name)
    if hit and time.monotonic() - hit[0] < _TTL_S:
        return hit[1]
    value = aws.client("ssm").get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    _cache[name] = (time.monotonic(), value)
    return value


def reset() -> None:
    _cache.clear()
