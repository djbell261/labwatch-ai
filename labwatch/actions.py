"""Turns the agent's chosen action IDs into fixed runbook wording, with checks.

The model never writes instructions. It picks IDs and parameters; this code
rejects unknown actions, unknown rooms or equipment, and missing or extra parameters.
"""
from __future__ import annotations

from string import Formatter


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(text) if name}


def render_actions(actions: list[dict], layout: dict, runbook: dict) -> tuple[list[dict], list[str]]:
    rendered, errors = [], []
    rooms, ahus = layout["rooms"], layout["air_handlers"]
    for i, a in enumerate(actions or []):
        aid = a.get("id")
        spec = runbook["actions"].get(aid)
        if spec is None:
            errors.append(f"action[{i}]: unknown action id '{aid}'")
            continue
        params = a.get("params") or {}
        needed = _placeholders(spec["text"])
        if set(params) != needed:
            errors.append(f"action[{i}] {aid}: needs params {sorted(needed)}, got {sorted(params)}")
            continue
        values, ok = {}, True
        if "rooms" in params:
            rl = params["rooms"] if isinstance(params["rooms"], list) else [params["rooms"]]
            bad = [r for r in rl if str(r) not in rooms]
            if bad or not rl:
                errors.append(f"action[{i}] {aid}: unknown rooms {bad}")
                ok = False
            values["rooms"] = ", ".join(str(r) for r in rl)
        if "asset" in params:
            if params["asset"] not in ahus:
                errors.append(f"action[{i}] {aid}: unknown air handler '{params['asset']}'")
                ok = False
            values["asset"] = params["asset"]
        if "backup_room" in params:
            b = str(params["backup_room"])
            if b not in rooms or rooms[b]["type"] != "isolation":
                errors.append(f"action[{i}] {aid}: backup_room '{b}' is not an isolation room")
                ok = False
            values["backup_room"] = b
        if ok:
            rendered.append({"id": aid, "audience": spec["audience"],
                             "text": spec["text"].format(**values)})
    return rendered, errors
