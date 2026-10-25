"""Daily LLM spend cap. Local runs keep the counter in a JSON file; the worker uses DynamoDB."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# USD per million tokens (input, output), as listed by the provider.
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.1, 0.5),
    "claude-opus-5-5": (4.0, 20.0),
}


def price_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = PRICES.get(model, (5.0, 25.0))  # unknown models are priced high on purpose
    return (prompt_tokens * pin + completion_tokens * pout) / 1e6


class Budget:
    def __init__(self, daily_usd: float | None = None, store=None):
        self.daily_usd = float(daily_usd if daily_usd is not None else os.environ.get("THALWEG_LLM_DAILY_USD", 3.0))
        self.store = store or FileStore(Path(os.environ.get("THALWEG_BUDGET_FILE",
                                                            Path.home() / ".cache" / "thalweg" / "budget.json")))

    @staticmethod
    def today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def spent(self) -> float:
        return self.store.get(self.today())

    def allow(self) -> bool:
        return self.spent() < self.daily_usd

    def charge(self, usd: float) -> None:
        if usd > 0:
            self.store.add(self.today(), usd)


class FileStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def get(self, day: str) -> float:
        return float(self._load().get(day, 0.0))

    def add(self, day: str, usd: float) -> None:
        d = self._load()
        d[day] = float(d.get(day, 0.0)) + usd
        self.path.write_text(json.dumps(d))


class MemoryStore:
    def __init__(self, spent: float = 0.0):
        self.v = {}
        self.initial = spent

    def get(self, day: str) -> float:
        return self.v.get(day, self.initial)

    def add(self, day: str, usd: float) -> None:
        self.v[day] = self.get(day) + usd
