"""Filesystem locations for models and datasets."""

import os
from pathlib import Path


def models_dir() -> Path:
    p = Path(os.environ.get("THALWEG_MODELS", Path.home() / ".cache" / "thalweg" / "models"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    p = Path(os.environ.get("THALWEG_DATA", Path.home() / ".cache" / "thalweg" / "data"))
    p.mkdir(parents=True, exist_ok=True)
    return p
