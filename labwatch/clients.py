"""Model clients for the investigation loop.

Each client translates between the generic message format used in agent.py and
the provider's API:
  - messages: {"role": "system"|"user"|"assistant"|"tool", "content": str|dict, ...}
  - tool results arrive as dicts in "content"; they are sent to the API as JSON strings
  - return {"tool_calls": [...], "thought": str} or {"final": <parsed report JSON>}

ChatToolsClient speaks any OpenAI-compatible chat-completions endpoint with function
calling. MuseSparkClient and OpenWeightsClient only differ in where their config comes
from. ReplayClient records a session to JSONL or replays one, for deterministic demos.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

import httpx

from .baseline import RuleBasedClient

try:  # optional: pick up a local .env
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = logging.getLogger("labwatch")

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class ModelClientError(Exception):
    def __init__(self, status: int | None, message: str):
        self.status = status
        super().__init__(f"HTTP {status}: {message}" if status else message)


# ------------------------------------------------------------ translation
def _as_json(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def to_api_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        role = m["role"]
        if role == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id"), "content": _as_json(m["content"])})
        elif role == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": [
                {"id": c.get("id"), "type": "function",
                 "function": {"name": c.get("name"), "arguments": _as_json(c.get("arguments") or {})}}
                for c in m["tool_calls"]]})
        elif role == "assistant":   # e.g. a rejected report dict being sent back for repair
            out.append({"role": "assistant", "content": _as_json(m.get("content"))})
        else:
            out.append({"role": role, "content": m["content"]})
    return out


def to_api_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": t} for t in tools]


def _parse_arguments(raw):
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw or "{}")
    except ValueError:
        return raw   # agent.py reports invalid JSON back to the model


def parse_final(text: str):
    """The report as parsed JSON, or the raw text if it isn't JSON (validation asks for a repair)."""
    fenced = _FENCE.search(text)
    body = fenced.group(1) if fenced else text
    try:
        return json.loads(body.strip())
    except ValueError:
        return text


def from_api(data: dict) -> dict:
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise ModelClientError(None, "response has no choices[0].message") from None
    text = msg.get("content") or ""
    if isinstance(text, list):   # some servers return content parts
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    calls = msg.get("tool_calls") or []
    if not calls:
        return {"final": parse_final(text)}
    out = {"tool_calls": [{"id": c.get("id"), "name": (c.get("function") or {}).get("name"),
                           "arguments": _parse_arguments((c.get("function") or {}).get("arguments"))}
                          for c in calls]}
    if text.strip():
        out["thought"] = text.strip()
    return out


# ------------------------------------------------------------ HTTP client
class ChatToolsClient:
    """Any OpenAI-compatible chat-completions endpoint with function calling."""

    def __init__(self, base_url: str, api_key: str | None, model: str, timeout: float = 60.0,
                 max_retries: int = 2, backoff: float = 1.0, transport: httpx.BaseTransport | None = None):
        self.model, self.timeout = model, timeout
        self.max_retries, self.backoff = max_retries, backoff
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(base_url=base_url.rstrip("/"), headers=headers,
                                  timeout=timeout, transport=transport)

    def __repr__(self):   # never show the key
        return f"{type(self).__name__}(base_url={str(self._http.base_url)!r}, model={self.model!r})"

    def next(self, messages, tools):
        body = {"model": self.model, "messages": to_api_messages(messages), "tools": to_api_tools(tools)}
        return from_api(self._post(body))

    def _post(self, body: dict) -> dict:
        """POSTs with retries on 429, 5xx, and timeouts. Anything else fails at once."""
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            try:
                r = self._http.post("/chat/completions", json=body)
            except httpx.TimeoutException:
                status, message = None, f"model call timed out after {self.timeout}s"
            except httpx.HTTPError as e:
                raise ModelClientError(None, f"could not reach model endpoint: {type(e).__name__}") from None
            else:
                if r.status_code < 400:
                    try:
                        return r.json()
                    except ValueError:
                        raise ModelClientError(r.status_code, "response is not JSON") from None
                status, message = r.status_code, _error_message(r)
                if status != 429 and status < 500:
                    raise ModelClientError(status, message)
            if attempt < attempts - 1:
                delay = self.backoff * 2 ** attempt
                log.warning("model call failed (%s), retrying in %.1fs", status or "timeout", delay)
                time.sleep(delay)
        raise ModelClientError(status, f"{message} (gave up after {attempts} attempts)")


def _error_message(r: httpx.Response) -> str:
    try:
        return str(r.json()["error"]["message"])[:200]
    except (ValueError, KeyError, TypeError):
        return r.text[:200] or r.reason_phrase


def _env(required: dict[str, str], optional: dict[str, str] | None = None) -> dict:
    """Reads constructor args from env vars, with one clear error listing what's missing."""
    missing = [var for var in required.values() if not os.environ.get(var)]
    if missing:
        raise ModelClientError(None, f"missing {', '.join(missing)}. Set them in .env "
                                     "(see .env.example), or use the baseline client "
                                     "(--client baseline / LABWATCH_CLIENT=baseline).")
    names = {**required, **(optional or {})}
    return {arg: os.environ.get(var) or None for arg, var in names.items()}


class MuseSparkClient(ChatToolsClient):
    """Muse Spark via the Meta Model API (hosted)."""

    def __init__(self, **kw):
        super().__init__(**_env({"base_url": "META_BASE_URL", "api_key": "META_API_KEY",
                                 "model": "META_MODEL"}), **kw)


class OpenWeightsClient(ChatToolsClient):
    """Muse Spark Open / Muse Glimmer on Baseten or Modal (private mode)."""

    def __init__(self, **kw):
        super().__init__(**_env({"base_url": "OPEN_WEIGHTS_BASE_URL", "model": "OPEN_WEIGHTS_MODEL"},
                                {"api_key": "OPEN_WEIGHTS_API_KEY"}), **kw)


# ------------------------------------------------------------ record / replay
def _request_summary(messages: list[dict]) -> dict:
    last = messages[-1] if messages else {}
    return {"n_messages": len(messages), "last_role": last.get("role"), "last_tool": last.get("name")}


class ReplayClient:
    """With `inner`: forwards to it and appends each (request summary, response) to a
    JSONL file. Without: replays the file's responses in order."""

    def __init__(self, path: str | Path, inner=None):
        self.path, self.inner = Path(path), inner
        if inner is None:
            if not self.path.exists():
                raise ModelClientError(None, f"replay file {self.path} not found")
            lines = self.path.read_text().splitlines()
            self._responses = [json.loads(line)["response"] for line in lines if line.strip()]

    def next(self, messages, tools):
        if self.inner is None:
            if not self._responses:
                raise ModelClientError(None, f"replay file {self.path} has no more responses")
            return self._responses.pop(0)
        resp = self.inner.next(messages, tools)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps({"request": _request_summary(messages), "response": resp}, default=str) + "\n")
        return resp


# ------------------------------------------------------------ selection
CLIENT_NAMES = "baseline, muse, open, or replay:<path>"


def make_client(name: str, layout: dict, incident: dict):
    if name == "baseline":
        return RuleBasedClient(layout, incident)
    if name == "muse":
        return MuseSparkClient()
    if name == "open":
        return OpenWeightsClient()
    if name.startswith("replay:"):
        return ReplayClient(name.removeprefix("replay:"))
    raise ModelClientError(None, f"unknown client '{name}'. Use {CLIENT_NAMES}.")
