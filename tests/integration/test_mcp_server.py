"""The MCP server starts over stdio, lists its tools and answers list_stations."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


async def _session_check():
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server" / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            res = await s.call_tool("list_stations", {})
            payload = [json.loads(c.text) for c in res.content if getattr(c, "text", None)]
            return tools, payload


def test_mcp_tools_and_stations():
    tools, payload = asyncio.run(_session_check())
    assert {"list_stations", "gauge_clip", "open_session", "run_velocimetry", "compare_methods"} <= tools
    flat = payload[0] if payload and isinstance(payload[0], list) else payload
    ids = {p["id"] for p in flat}
    assert {"dart", "vgc1", "vgc8"} <= ids
