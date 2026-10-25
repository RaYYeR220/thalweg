"""A station clip and frame reading with real timestamps."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2 as cv
import numpy as np

FPS_MISMATCH_TOL = 0.02


@dataclass
class Clip:
    station_id: str
    video: Path
    t: datetime
    level: float
    source: str = ""
    meta: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "Clip":
        p = Path(path)
        d = json.loads(p.read_text(encoding="utf-8"))
        video = Path(d["video"])
        if not video.is_absolute():
            # Clip files name videos relative to the data directory (THALWEG_DATA).
            from thalweg.paths import data_dir
            cand = data_dir() / video
            video = cand if cand.exists() else (p.parent / video).resolve()
        return cls(station_id=d["station_id"], video=video, t=datetime.fromisoformat(d["t"]),
                   level=float(d["level"]), source=d.get("source", ""), meta=d.get("meta", {}))


@dataclass
class FrameStack:
    frames: list[np.ndarray]
    t_s: np.ndarray  # seconds from the first returned frame
    fps_nominal: float
    size: tuple[int, int]  # (width, height)
    warnings: list[str] = field(default_factory=list)

    @property
    def dt_s(self) -> float:
        return float(np.median(np.diff(self.t_s))) if len(self.t_s) > 1 else float("nan")


def probe(video: str | Path) -> dict:
    cap = cv.VideoCapture(str(video))
    if not cap.isOpened():
        return {"ok": False, "error": "cannot_open"}
    info = {
        "ok": True,
        "width": int(cap.get(cv.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv.CAP_PROP_FRAME_HEIGHT)),
        "fps": float(cap.get(cv.CAP_PROP_FPS)),
        "frames": int(cap.get(cv.CAP_PROP_FRAME_COUNT)),
    }
    info["duration_s"] = info["frames"] / info["fps"] if info["fps"] > 0 else None
    cap.release()
    return info


def read_frames(video: str | Path, start_s: float = 0.0, dur_s: float | None = None, step: int = 1,
                gray: bool = True, max_frames: int = 1500, max_width: int | None = None) -> FrameStack:
    """Read frames with container timestamps (CAP_PROP_POS_MSEC), not nominal fps.

    Phones and some drone files are variable-frame-rate; using 1/fps as dt would bias every velocity.
    """
    cap = cv.VideoCapture(str(video))
    if not cap.isOpened():
        raise IOError(f"cannot open {video}")
    fps = float(cap.get(cv.CAP_PROP_FPS)) or 0.0
    if start_s > 0:
        cap.set(cv.CAP_PROP_POS_MSEC, start_s * 1000.0)
    frames, ts = [], []
    k = 0
    scale = None
    while len(frames) < max_frames:
        ok, f = cap.read()
        if not ok:
            break
        t = cap.get(cv.CAP_PROP_POS_MSEC) / 1000.0
        if dur_s is not None and ts and t - ts[0] > dur_s:
            break
        if k % step == 0:
            if gray and f.ndim == 3:
                f = cv.cvtColor(f, cv.COLOR_BGR2GRAY)
            if max_width and f.shape[1] > max_width:
                scale = max_width / f.shape[1]
                f = cv.resize(f, None, fx=scale, fy=scale, interpolation=cv.INTER_AREA)
            frames.append(f)
            ts.append(t)
        k += 1
    cap.release()
    if not frames:
        raise IOError(f"no frames read from {video}")
    t_s = np.asarray(ts) - ts[0]
    warnings = []
    if len(t_s) > 2 and fps > 0:
        dt = np.diff(t_s)
        if np.any(dt <= 0):
            # Some containers report no per-frame time; fall back to nominal fps and say so.
            t_s = np.arange(len(frames)) * step / fps
            warnings.append("timestamps_unavailable_using_nominal_fps")
        else:
            nominal = step / fps
            if abs(np.median(dt) - nominal) / nominal > FPS_MISMATCH_TOL:
                warnings.append(f"fps_mismatch: container {fps:.3f} fps, timestamps {step / np.median(dt):.3f} fps")
            if np.std(dt) / np.mean(dt) > 0.05:
                warnings.append("variable_frame_rate")
    h, w = frames[0].shape[:2]
    if scale:
        warnings.append(f"downscaled_x{scale:.3f}")
    return FrameStack(frames=frames, t_s=t_s, fps_nominal=fps, size=(w, h), warnings=warnings)
