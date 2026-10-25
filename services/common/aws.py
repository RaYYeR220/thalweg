"""Lazily created, process-wide boto3 clients.

Clients are cached per Lambda container. Tests swap them through `override()`
and drop the cache with `reset()` so every test gets a fresh moto backend.
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from botocore.config import Config

_CONFIG = Config(retries={"max_attempts": 4, "mode": "standard"}, connect_timeout=3, read_timeout=10)
_clients: dict[str, Any] = {}
_resources: dict[str, Any] = {}


def _region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


def client(name: str) -> Any:
    if name not in _clients:
        if name == "s3":
            # Regional virtual-host addressing so presigned URLs work in us-east-1 and elsewhere.
            cfg = _CONFIG.merge(Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
            _clients[name] = boto3.client(name, region_name=_region(), config=cfg)
        else:
            _clients[name] = boto3.client(name, region_name=_region(), config=_CONFIG)
    return _clients[name]


def table(name: str) -> Any:
    if "dynamodb" not in _resources:
        _resources["dynamodb"] = boto3.resource("dynamodb", region_name=_region(), config=_CONFIG)
    return _resources["dynamodb"].Table(name)


def override(name: str, obj: Any) -> None:
    """Replace a client (tests use this to stub Step Functions)."""
    _clients[name] = obj


def reset() -> None:
    _clients.clear()
    _resources.clear()
