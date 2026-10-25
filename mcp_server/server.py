"""MCP server: the gauging tools for any MCP-capable agent.

    python mcp_server/server.py                      # stdio
    python mcp_server/server.py --http --port 8000   # streamable HTTP (set THALWEG_MCP_TOKEN)

`gauge_clip` runs the whole policy-gated agent with the deterministic brain and returns its
decision; the perception tools expose the individual OpenCV 5 steps on a session.
"""

from __future__ import annotations

import argparse
import os
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from thalweg import pipeline as P
from thalweg.agent.autopilot import AutopilotBrain
from thalweg.agent.loop import run_agent
from thalweg.clip import Clip
from thalweg.pipeline import ArtifactSink, Context
from thalweg.station import load_station

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("THALWEG_MCP_OUT", ROOT / "runs" / "mcp"))
mcp = FastMCP("thalweg", stateless_http=True)
SESSIONS: dict[str, Context] = {}


def _station(station_id: str):
    return load_station(ROOT / "stations" / f"{station_id}.yaml")


@mcp.tool()
def list_stations() -> list[dict]:
    """Stations this server can gauge, with their survey summary."""
    out = []
    for p in sorted((ROOT / "stations").glob("*.yaml")):
        st = load_station(p)
        out.append({"id": st.id, "name": st.name, "grp": len(st.grp), "transect_points": len(st.transect),
                    "alpha": st.alpha, "camera": "scaled" if st.scale_m_per_px else ("3d" if len({g.Z for g in st.grp}) > 1 else "planar")})
    return out


@mcp.tool()
def gauge_clip(station_id: str, video_path: str, level_m: float, time_iso: str = "2026-01-01T00:00:00") -> dict:
    """Run the full gauging agent (deterministic brain, policy gate) on a local video. Returns the decision,
    the discharge if published, every method's estimate and the evidence image paths."""
    from datetime import datetime
    st = _station(station_id)
    clip = Clip(station_id=st.id, video=Path(video_path), t=datetime.fromisoformat(time_iso), level=level_m)
    rid = "mcp-" + uuid.uuid4().hex[:8]
    sink = ArtifactSink(OUT / rid)
    res = run_agent(st, clip, AutopilotBrain(), sink, run_id=rid)
    sink.put_json("trace", res.to_dict())
    d = res.decision
    return {"run_id": rid, "decision": d.kind, "reasons": d.reasons, "method": d.method, "basis": d.basis,
            "discharge": d.q if d.kind == "PUBLISH" else None, "estimates": res.estimates,
            "evidence_dir": str(OUT / rid)}


@mcp.tool()
def open_session(station_id: str, video_path: str, level_m: float) -> dict:
    """Load a clip and run the mandatory perception steps (stabilize, orthorectify, diagnose, coarse motion)."""
    from datetime import datetime
    st = _station(station_id)
    sid = uuid.uuid4().hex[:8]
    ctx = Context(station=st, clip=Clip(st.id, Path(video_path), datetime.now(), level_m),
                  sink=ArtifactSink(OUT / f"session-{sid}"))
    out = {}
    for name, fn in (("load", P.step_load), ("stabilize", P.step_stabilize), ("orthorectify", P.step_orthorectify),
                     ("diagnose", P.step_diagnose), ("coarse_motion", P.step_coarse_motion)):
        out[name] = fn(ctx)
    SESSIONS[sid] = ctx
    return {"session": sid, **out}


@mcp.tool()
def run_velocimetry(session: str, method: str, params: dict | None = None) -> dict:
    """Run 'lspiv', 'stiv' or 'dense_flow' on an open session, then compute discharge for it."""
    ctx = SESSIONS[session]
    params = params or {}
    fn = {"lspiv": P.step_lspiv, "stiv": P.step_stiv, "dense_flow": P.step_dense_flow}[method]
    field = fn(ctx, **params)
    return {"field": field, "discharge": P.step_discharge(ctx, method)}


@mcp.tool()
def compare_methods(session: str) -> dict:
    """Discharge from each method run so far on the session, with pairwise differences."""
    return P.step_compare(SESSIONS[session])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1", help="bind address; the HTTP mode has no authentication of its own")
    a = ap.parse_args()
    if a.http:
        mcp.settings.host = a.host
        mcp.settings.port = a.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
