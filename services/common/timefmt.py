"""One timestamp format everywhere: ISO 8601 UTC, second precision, `Z` suffix.

Sort keys (`thalweg-gaugings.t`, `thalweg-runs.started`, `thalweg-warnings.created`)
compare lexicographically, so every writer goes through `iso()` / `normalize()`.
"""

from __future__ import annotations

from datetime import datetime, timezone

FMT = "%Y-%m-%dT%H:%M:%SZ"


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    dt = dt or now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(FMT)


def parse(value: str) -> datetime:
    """Parse ISO 8601; naive values are taken as UTC. Raises ValueError."""
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a non-empty string")
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize(value: str) -> str:
    return iso(parse(value))


def compact(dt: datetime) -> str:
    """`YYYYMMDDTHHMMSS`, the clip-key timestamp."""
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")
