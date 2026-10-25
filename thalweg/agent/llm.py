"""The LLM brain: an OpenAI-compatible chat model with tool calling and image input (served by Venice)."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import cv2 as cv
import httpx

from thalweg.agent.budget import Budget, price_usd
from thalweg.agent.tools import tool_schemas
from thalweg.agent.types import ToolCall

PROMPT = (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8")
DEFAULT_MODEL = os.environ.get("THALWEG_MODEL", "claude-sonnet-5-5")
BASE_URL = os.environ.get("THALWEG_LLM_BASE_URL") or os.environ.get("VENICE_BASE_URL") or "https://api.venice.ai/api/v1"


class BrainError(Exception):
    def __init__(self, cause: str, detail: str = ""):
        super().__init__(f"{cause}: {detail}" if detail else cause)
        self.cause = cause


class VeniceClient:
    def __init__(self, api_key: str | None = None, base_url: str = BASE_URL, timeout_s: float = 90.0):
        self.api_key = api_key or os.environ.get("VENICE_API_KEY") or _ssm_key()
        if not self.api_key:
            raise BrainError("no_api_key")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def chat(self, messages: list[dict], tools: list[dict], model: str, max_tokens: int = 1200) -> dict:
        body = {"model": model, "messages": messages, "tools": tools, "tool_choice": "auto",
                "max_tokens": max_tokens, "venice_parameters": {"include_venice_system_prompt": False}}
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout_s,
                           headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.TimeoutException as e:
            raise BrainError("timeout", str(e)) from e
        except httpx.HTTPError as e:
            raise BrainError("transport", str(e)) from e
        if r.status_code != 200:
            raise BrainError(f"http_{r.status_code}", r.text[:200])
        return r.json()


def _ssm_key() -> str | None:
    name = os.environ.get("THALWEG_PARAM_VENICE_KEY") or os.environ.get("THALWEG_VENICE_PARAM")
    if not name:
        return None
    try:
        import boto3
        return boto3.client("ssm").get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    except Exception:  # noqa: BLE001 - no key means the brain fails closed
        return None


def _image_part(path: Path, max_px: int = 1024) -> dict | None:
    img = cv.imread(str(path))
    if img is None:
        return None
    s = min(1.0, max_px / max(img.shape[:2]))
    if s < 1:
        img = cv.resize(img, None, fx=s, fy=s, interpolation=cv.INTER_AREA)
    ok, buf = cv.imencode(".jpg", img, [cv.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        return None
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf).decode()}}


def _compact(obj, depth=0):
    """Trim long arrays from tool outputs before they go to the model."""
    if isinstance(obj, dict):
        return {k: _compact(v, depth + 1) for k, v in obj.items() if k not in ("H", "stations")}
    if isinstance(obj, list):
        return [_compact(v, depth + 1) for v in obj[:12]]
    if isinstance(obj, float):
        return round(obj, 4)
    return obj


class LLMBrain:
    name = "llm"
    actor = "llm"

    def __init__(self, model: str | None = None, client: VeniceClient | None = None, budget: Budget | None = None,
                 max_images: int = 3):
        self.model = model or DEFAULT_MODEL
        self._client = client
        self.budget = budget or Budget()
        self.max_images = max_images
        self.cost_usd = 0.0
        self.messages: list[dict] = []
        self._pending: list[dict] = []  # tool results waiting to be sent
        self._queue: list[ToolCall] = []

    @property
    def client(self) -> VeniceClient:
        if self._client is None:
            self._client = VeniceClient()
        return self._client

    def start(self, state) -> None:
        ctx = state.ctx
        prefix = {s.tool: _compact(s.summary) for s in state.steps if s.actor == "system"}
        st = ctx.station
        intro = {
            "station": {"id": st.id, "name": st.name, "alpha": st.alpha, "alpha_source": st.alpha_source,
                        "notes": st.notes},
            "clip": {"t": ctx.clip.t.isoformat(), "water_level_m": ctx.level},
            "policy_limits": state.limits.to_dict(),
            "perception": prefix,
        }
        content = [{"type": "text", "text": "New clip. Perception results:\n" + json.dumps(intro, default=str)}]
        for key in ("frame_overlay", "ortho"):
            part = _image_part(ctx.sink.path(f"{key}.jpg"))
            if part:
                content.append({"type": "text", "text": f"evidence: {key}"})
                content.append(part)
        self.messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": content}]

    def feedback(self, call: ToolCall, result: dict) -> None:
        self._pending.append({"call": call, "result": result})

    def _flush(self, ctx) -> None:
        images = []
        for p in self._pending:
            call, res = p["call"], p["result"]
            if call.call_id:
                self.messages.append({"role": "tool", "tool_call_id": call.call_id,
                                      "content": json.dumps(_compact(res), default=str)})
            for key in (res.get("evidence") or []) if isinstance(res, dict) else []:
                images.append(key)
        self._pending = []
        if images:
            content = [{"type": "text", "text": "Evidence from the last step(s):"}]
            for key in images[-self.max_images:]:
                part = _image_part(ctx.sink.path(key))
                if part:
                    content.append({"type": "text", "text": Path(key).stem})
                    content.append(part)
            if len(content) > 1:
                self.messages.append({"role": "user", "content": content})

    def next(self, state) -> ToolCall | None:
        if self._queue:
            return self._queue.pop(0)
        self._flush(state.ctx)
        if not self.budget.allow():
            raise BrainError("budget", "daily LLM budget exhausted")
        resp = self.client.chat(self.messages, tool_schemas(), self.model)
        usage = resp.get("usage") or {}
        cost = price_usd(self.model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        self.cost_usd += cost
        self.budget.charge(cost)
        try:
            msg = resp["choices"][0]["message"]
        except (KeyError, IndexError) as e:
            raise BrainError("malformed_response") from e
        calls = msg.get("tool_calls") or []
        self.messages.append({"role": "assistant", "content": msg.get("content") or "",
                              **({"tool_calls": calls} if calls else {})})
        if not calls:
            # A text-only answer is not a decision. Ask once for a tool call; the loop's step budget bounds this.
            self.messages.append({"role": "user", "content": "Respond with a tool call."})
            return ToolCall("__no_tool_call__", {}, (msg.get("content") or "")[:200])
        out = []
        for c in calls:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"__malformed__": fn.get("arguments", "")[:200]}
            out.append(ToolCall(fn.get("name", ""), args, args.get("reason", "") if isinstance(args, dict) else "",
                                call_id=c.get("id")))
        self._queue = out[1:]
        return out[0]
