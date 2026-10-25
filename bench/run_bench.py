"""COOL vs stock OpenCV benchmark for the gauging workload.

    python bench/run_bench.py --label c8g-cool --usd-per-hour 0.0898 --station stations/vgc1.yaml --clip clips/vgc1.json

Run it once per build (COOL's cv2, pip opencv-python-headless) on the same instance and once on an
x86 instance. It records which cv2 actually ran, the HAL it was built with and the instance type,
times the OpenCV operations the pipeline leans on, and times the full deterministic pipeline.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
import urllib.request
from pathlib import Path

import cv2 as cv
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def imds(path: str) -> str | None:
    try:
        tok = urllib.request.urlopen(urllib.request.Request(
            "http://169.254.169.254/latest/api/token", method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}), timeout=1).read().decode()
        return urllib.request.urlopen(urllib.request.Request(
            f"http://169.254.169.254/latest/meta-data/{path}", headers={"X-aws-ec2-metadata-token": tok}),
            timeout=1).read().decode()
    except Exception:  # noqa: BLE001 - not on EC2
        return None


def environment() -> dict:
    info = cv.getBuildInformation()
    keep = [ln.strip() for ln in info.splitlines()
            if any(k in ln for k in ("Version control", "Baseline", "Dispatched", "Custom HAL", "Parallel framework",
                                      "CPU/HW features", "KleidiCV", "Compiler"))]
    return {"cv2_version": cv.__version__, "cv2_file": cv.__file__, "build": keep, "threads": cv.getNumThreads(),
            "python": platform.python_version(), "machine": platform.machine(), "processor": platform.processor(),
            "instance_type": imds("instance-type"), "ami_id": imds("ami-id"),
            "product_codes": imds("product-codes")}


def timeit(fn, repeat=30, warmup=3) -> float:
    for _ in range(warmup):
        fn()
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return round(statistics.median(ts), 3)


def ops() -> dict:
    rng = np.random.default_rng(0)
    bgr = rng.integers(0, 255, (1080, 1920, 3), np.uint8)
    bgr4k = rng.integers(0, 255, (2160, 4096, 3), np.uint8)
    gray = cv.cvtColor(bgr, cv.COLOR_BGR2GRAY)
    small = cv.resize(gray, (600, 600))
    small2 = np.roll(small, 3, axis=1)
    mx, my = np.meshgrid(np.linspace(100, 1800, 600, dtype=np.float32), np.linspace(50, 1000, 600, dtype=np.float32))
    H = np.array([[1.001, 0.002, 1.5], [-0.001, 0.999, -0.8], [0, 0, 1]])
    tpl = small[100:131, 100:131].astype(np.float32)
    srch = small[90:145, 90:145].astype(np.float32)
    pts = rng.uniform(0, 1000, (1000, 2)).astype(np.float32)
    pts2 = cv.perspectiveTransform(pts.reshape(-1, 1, 2), H).reshape(-1, 2) + rng.normal(0, 0.5, (1000, 2)).astype(np.float32)
    dis = cv.DISOpticalFlow_create(cv.DISOPTICAL_FLOW_PRESET_MEDIUM)
    win = cv.createHanningWindow((600, 600), cv.CV_32F)
    s32, s32b = small.astype(np.float32), small2.astype(np.float32)
    return {
        "cvtColor_bgr2gray_1080p": timeit(lambda: cv.cvtColor(bgr, cv.COLOR_BGR2GRAY)),
        "resize_area_4k_to_1080p": timeit(lambda: cv.resize(bgr4k, (1920, 1013), interpolation=cv.INTER_AREA), 15),
        "remap_ortho_600x600": timeit(lambda: cv.remap(gray, mx, my, cv.INTER_LINEAR)),
        "warpPerspective_1080p": timeit(lambda: cv.warpPerspective(gray, H, (1920, 1080))),
        "gaussianBlur_s6_600": timeit(lambda: cv.GaussianBlur(s32, (0, 0), 6.0)),
        "sobel_600": timeit(lambda: cv.Sobel(s32, cv.CV_32F, 1, 0)),
        "matchTemplate_31_in_55_x100": timeit(lambda: [cv.matchTemplate(srch, tpl, cv.TM_CCOEFF_NORMED) for _ in range(100)], 15),
        "dis_flow_600": timeit(lambda: dis.calc(small, small2, None), 15),
        "phaseCorrelate_600": timeit(lambda: cv.phaseCorrelate(s32, s32b, win), 15),
        "findHomography_usac_1000": timeit(lambda: cv.findHomography(pts, pts2, cv.USAC_MAGSAC, 1.5), 15),
        "laplacian_1080p": timeit(lambda: cv.Laplacian(gray, cv.CV_32F)),
    }


def pipeline(station: str, clip: str, repeat: int) -> dict:
    from thalweg.agent.autopilot import AutopilotBrain
    from thalweg.agent.loop import run_agent
    from thalweg.clip import Clip
    from thalweg.pipeline import ArtifactSink
    from thalweg.station import load_station
    runs = []
    for k in range(repeat):
        st = load_station(ROOT / station)
        c = Clip.load(ROOT / clip)
        t0 = time.perf_counter()
        res = run_agent(st, c, AutopilotBrain(), ArtifactSink(ROOT / "runs" / "bench" / f"r{k}"))
        runs.append({"total_s": round(time.perf_counter() - t0, 2), "stages": res.timings,
                     "decision": res.decision.kind, "q": (res.decision.q or {}).get("q")})
    tot = statistics.median(r["total_s"] for r in runs)
    stages = {k: round(statistics.median(r["stages"].get(k, 0.0) for r in runs), 3) for k in runs[0]["stages"]}
    return {"station": station, "clip": clip, "repeat": repeat, "median_total_s": tot, "median_stages_s": stages,
            "runs": runs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--usd-per-hour", type=float, required=True, help="instance price plus any software fee")
    ap.add_argument("--station", default="stations/vgc1.yaml")
    ap.add_argument("--clip", default="clips/vgc1.json")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--no-pipeline", action="store_true")
    a = ap.parse_args()
    out = {"label": a.label, "env": environment(), "ops_ms": ops()}
    if not a.no_pipeline:
        p = pipeline(a.station, a.clip, a.repeat)
        out["pipeline"] = p
        per_clip = p["median_total_s"]
        out["clips_per_hour"] = round(3600.0 / per_clip, 1)
        out["usd_per_1000_gaugings"] = round(a.usd_per_hour * per_clip / 3.6, 3)
    out["usd_per_hour"] = a.usd_per_hour
    dst = ROOT / "bench" / "results" / f"{a.label}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out.get(k) for k in ("label", "clips_per_hour", "usd_per_1000_gaugings")} |
                     {"cv2": out["env"]["cv2_file"], "instance": out["env"]["instance_type"]}, indent=1))


if __name__ == "__main__":
    main()
