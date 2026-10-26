"""Model clients. Wire these up once Meta Model API access is active.

Each client translates between the generic message format used in agent.py and
the provider's API:
  - messages: {"role": "system"|"user"|"assistant"|"tool", "content": str|dict, ...}
  - tool results arrive as dicts in "content"; serialize them to JSON for the API
  - return {"tool_calls": [...]} or {"final": <parsed report JSON>}
"""
from __future__ import annotations

import os


class MuseSparkClient:
    """Muse Spark via the Meta Model API (hosted). TODO: implement on day 2-3."""

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or os.environ.get("META_MODEL")
        self.api_key = api_key or os.environ.get("META_API_KEY")

    def next(self, messages, tools):
        raise NotImplementedError("Wire up the Meta Model API here using the docs from kickoff.")


class OpenWeightsClient:
    """Muse Spark Open / Muse Glimmer on Baseten or Modal (private mode). TODO: day 6."""

    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = base_url or os.environ.get("OPEN_WEIGHTS_BASE_URL")
        self.model = model or os.environ.get("OPEN_WEIGHTS_MODEL")

    def next(self, messages, tools):
        raise NotImplementedError("Wire up the self-hosted open-weights endpoint here.")
