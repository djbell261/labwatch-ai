"""Turns the agent's chosen action IDs into fixed runbook wording, with checks.

The model never writes instructions. It picks IDs and parameters; this code
rejects unknown actions, unknown rooms or equipment, and missing or extra parameters.
Given the incident context, it also rejects actions on rooms outside the incident
and backup rooms that are affected or out of range.
"""
from __future__ import annotations

from string import Formatter

from .config import in_range


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(text) if name}


def render_actions(actions: list, layout: dict, runbook: dict, affected: set[str] | None = None,
                   latest_dp: dict[str, float] | None = None) -> tuple[list[dict], list[str]]:
    """`affected` (the report's affected rooms) and `latest_dp` (latest reading per room)
    turn on the incident checks. Leave them out to check against the runbook only."""
    rendered, errors = [], []
    rooms, ahus = layout["rooms"], layout["air_handlers"]
    for i, a in enumerate(actions or []):
        if not isinstance(a, dict):
            errors.append(f"action[{i}] must be an object with 'id' and 'params'")
            continue
        aid = a.get("id")
        spec = runbook["actions"].get(aid) if isinstance(aid, str) else None
        if spec is None:
            errors.append(f"action[{i}]: unknown action id '{aid}'")
            continue
        params = a.get("params") or {}
        if not isinstance(params, dict):
            errors.append(f"action[{i}] {aid}: params must be an object")
            continue
        needed = _placeholders(spec["text"])
        if set(params) != needed:
            errors.append(f"action[{i}] {aid}: needs params {sorted(needed)}, got {sorted(params)}")
            continue
        values, ok = {}, True
        if "rooms" in params:
            rl = params["rooms"] if isinstance(params["rooms"], list) else [params["rooms"]]
            names = [str(r) for r in rl]
            bad = [r for r in names if r not in rooms]
            if bad or not names:
                errors.append(f"action[{i}] {aid}: unknown rooms {bad}")
                ok = False
            elif affected is not None and not set(names) <= affected:
                errors.append(f"action[{i}] {aid}: rooms {sorted(set(names) - affected)} are not in affected_rooms")
                ok = False
            values["rooms"] = ", ".join(names)
        if "asset" in params:
            if not isinstance(params["asset"], str) or params["asset"] not in ahus:
                errors.append(f"action[{i}] {aid}: unknown air handler '{params['asset']}'")
                ok = False
            values["asset"] = params["asset"]
        if "backup_room" in params:
            b = str(params["backup_room"])
            if b not in rooms or rooms[b]["type"] != "isolation":
                errors.append(f"action[{i}] {aid}: backup_room '{b}' is not an isolation room")
                ok = False
            elif affected is not None and b in affected:
                errors.append(f"action[{i}] {aid}: backup_room '{b}' is one of the affected rooms")
                ok = False
            elif latest_dp is not None and (b not in latest_dp or not in_range(layout, "isolation", latest_dp[b])):
                errors.append(f"action[{i}] {aid}: backup_room '{b}' is not in range right now")
                ok = False
            values["backup_room"] = b
        if ok:
            rendered.append({"id": aid, "audience": spec["audience"],
                             "text": spec["text"].format(**values)})
    return rendered, errors
