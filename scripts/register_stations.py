"""Register the station configs in the stations table (the API and the preflight check read them)."""

import argparse
import json
from decimal import Decimal
from pathlib import Path

import boto3

from thalweg.station import load_station

ROOT = Path(__file__).resolve().parents[1]


def item(st) -> dict:
    d = {
        "id": st.id, "name": st.name, "alpha": st.alpha, "alpha_source": st.alpha_source,
        "image_size": list(st.image_size), "grp_count": len(st.grp),
        "camera": "scaled" if st.scale_m_per_px else ("3d" if len({g.Z for g in st.grp}) > 1 else "planar"),
        "transect": [[round(float(x), 3) for x in p] for p in st.transect],
        "thresholds": {k: v for k, v in vars(st.thresholds).items() if v is not None},
        "notes": st.notes, "sources": st.sources,
    }
    return json.loads(json.dumps(d), parse_float=Decimal)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=None)
    ap.add_argument("--table", default="thalweg-stations")
    a = ap.parse_args()
    t = boto3.Session(profile_name=a.profile, region_name="us-east-1").resource("dynamodb").Table(a.table)
    for p in sorted((ROOT / "stations").glob("*.yaml")):
        st = load_station(p)
        # Merge, so live fields written by the worker (levels, last_*) survive a re-registration.
        it = item(st)
        names = {f"#{k}": k for k in it if k != "id"}
        values = {f":{k}": v for k, v in it.items() if k != "id"}
        expr = "SET " + ", ".join(f"#{k} = :{k}" for k in it if k != "id")
        t.update_item(Key={"id": st.id}, UpdateExpression=expr, ExpressionAttributeNames=names,
                      ExpressionAttributeValues=values)
        print("registered", st.id)


if __name__ == "__main__":
    main()
