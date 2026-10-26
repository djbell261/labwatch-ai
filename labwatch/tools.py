"""Tools the investigating agent can call, plus the evidence ledger.

Every successful tool call is recorded with an evidence ID (E1, E2, ...).
The agent must cite these IDs in its report, and the report validator
rejects any citation that doesn't exist. That is how made-up evidence is caught.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta

from .config import requirement_text


class ToolError(Exception):
    pass


class Investigation:
    def __init__(self, sim, layout: dict, runbook: dict):
        self.sim, self.layout, self.runbook = sim, layout, runbook
        self.ledger: dict[str, dict] = {}

    def call(self, name: str, args: dict) -> dict:
        fn = TOOLS.get(name)
        if fn is None:
            return {"error": f"unknown tool '{name}'. Available: {sorted(TOOLS)}"}
        try:
            result = fn(self, **(args or {}))
        except ToolError as e:
            return {"error": str(e)}
        except TypeError as e:
            return {"error": f"bad arguments for {name}: {e}"}
        eid = f"E{len(self.ledger) + 1}"
        self.ledger[eid] = {"tool": name, "args": args, "result": result}
        return {"evidence_id": eid, **result}

    # helpers
    def _check_room(self, room: str):
        if room not in self.layout["rooms"]:
            raise ToolError(f"unknown room '{room}'. Known rooms: {sorted(self.layout['rooms'])}")

    def _since(self, minutes: int) -> str:
        return (self.sim.now - timedelta(minutes=minutes)).isoformat()


def get_layout(inv: Investigation, rooms: list[str]) -> dict:
    out = {}
    for room in rooms:
        inv._check_room(room)
        spec = inv.layout["rooms"][room]
        same_ahu = [r for r, s in inv.layout["rooms"].items() if s["ahu"] == spec["ahu"] and r != room]
        out[room] = {"type": spec["type"], "air_handler": spec["ahu"],
                     "requirement": requirement_text(inv.layout, spec["type"]),
                     "other_rooms_on_same_air_handler": same_ahu}
    return {"rooms": out}


def query_metrics(inv: Investigation, asset: str, minutes: int = 15) -> dict:
    is_room = asset in inv.layout["rooms"]
    if not is_room and asset not in inv.layout["air_handlers"]:
        raise ToolError(f"unknown asset '{asset}'. Use a room number or an air handler id "
                        f"{sorted(inv.layout['air_handlers'])}")
    minutes = max(1, min(int(minutes), 120))
    since = inv._since(minutes)
    points = [(t, v) for t, v in inv.sim.history.get(asset, []) if t >= since]
    if not points:
        raise ToolError(f"no data for {asset} in the last {minutes} min")
    values = [v for _, v in points]
    per_minute = points[:: max(1, 60 // 5)]  # one point per minute
    summary = {
        "asset": asset,
        "metric": "pressure_pa_vs_corridor" if is_room else "fan_power_kw",
        "window_minutes": minutes,
        "current": values[-1],
        "start_of_window": values[0],
        "min": min(values), "max": max(values),
        "mean": round(statistics.fmean(values), 2),
        "series_per_minute": [[t[11:16], v] for t, v in per_minute],
    }
    if not is_room:
        summary["design_kw"] = inv.layout["air_handlers"][asset]["design_fan_kw"]
    return summary


def get_door_events(inv: Investigation, rooms: list[str], minutes: int = 30) -> dict:
    for room in rooms:
        inv._check_room(room)
    since = inv._since(minutes)
    events = [e for e in inv.sim.door_events if e["room"] in rooms and e["ts"] >= since]
    current = {r: ("open" if inv.sim.rooms[r].reported_door_open else "closed") for r in rooms}
    return {"events": events, "current_door_sensor": current, "window_minutes": minutes}


def get_protocol(inv: Investigation, situation: str) -> dict:
    playbooks = inv.runbook["playbooks"]
    if situation not in playbooks:
        raise ToolError(f"unknown situation '{situation}'. Valid: {sorted(playbooks)}")
    actions = inv.runbook["actions"]
    return {"situation": situation,
            "allowed_actions": {aid: actions[aid] for aid in playbooks[situation]}}


TOOLS = {
    "get_layout": get_layout,
    "query_metrics": query_metrics,
    "get_door_events": get_door_events,
    "get_protocol": get_protocol,
}

# Tool definitions in the common function-calling JSON format.
TOOL_SCHEMAS = [
    {"name": "get_layout",
     "description": "Room type, pressure requirement, serving air handler, and other rooms on that air handler.",
     "parameters": {"type": "object", "required": ["rooms"],
                    "properties": {"rooms": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "query_metrics",
     "description": "Recent history for a room (pressure in Pa vs corridor) or an air handler (fan power in kW).",
     "parameters": {"type": "object", "required": ["asset"],
                    "properties": {"asset": {"type": "string"},
                                   "minutes": {"type": "integer", "minimum": 1, "maximum": 120}}}},
    {"name": "get_door_events",
     "description": "Door sensor open/close events and current door sensor state for rooms.",
     "parameters": {"type": "object", "required": ["rooms"],
                    "properties": {"rooms": {"type": "array", "items": {"type": "string"}},
                                   "minutes": {"type": "integer", "minimum": 1, "maximum": 120}}}},
    {"name": "get_protocol",
     "description": "The unit's approved actions for a situation. Recommend actions ONLY from these.",
     "parameters": {"type": "object", "required": ["situation"],
                    "properties": {"situation": {"type": "string", "enum": [
                        "isolation_pressure_lost", "protective_pressure_lost",
                        "air_handler_failure", "sensor_fault", "door_not_latching"]}}}},
]
