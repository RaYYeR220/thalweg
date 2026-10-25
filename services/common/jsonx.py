"""JSON <-> DynamoDB conversion (Decimal in the table, float/int on the wire)."""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any


class _Encoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            if o == o.to_integral_value():
                return int(o)
            return float(o)
        if isinstance(o, (set, frozenset)):
            return sorted(o)
        return super().default(o)


def dumps(obj: Any, **kw: Any) -> str:
    return json.dumps(obj, cls=_Encoder, separators=(",", ":"), **kw)


def plain(obj: Any) -> Any:
    """DynamoDB item -> JSON-native python (Decimal -> int/float)."""
    return json.loads(dumps(obj))


def _check_finite(obj: Any) -> None:
    if isinstance(obj, float) and not math.isfinite(obj):
        raise ValueError("non-finite number")
    if isinstance(obj, dict):
        for v in obj.values():
            _check_finite(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _check_finite(v)


def to_ddb(obj: Any) -> Any:
    """JSON-native python -> DynamoDB-safe python (float -> Decimal, None dropped from dicts)."""
    _check_finite(obj)
    return _strip_none(json.loads(json.dumps(obj), parse_float=Decimal))


def _strip_none(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none(v) for v in obj]
    return obj
