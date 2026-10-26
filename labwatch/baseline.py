"""Rule-based investigator: fixed steps, fixed rules, same tools as the agent.

Two jobs:
  1. Lets the whole pipeline (detector -> investigation -> validation -> eval)
     run end to end before the Muse Spark client is wired up.
  2. The comparison baseline for evals: "agent vs. hand-written rules".
"""
from __future__ import annotations


class RuleBasedClient:
    def __init__(self, layout: dict, incident: dict):
        self.layout = layout
        self.rooms = list(incident["rooms"])
        self.stage = 0
        self.decision = None

    @staticmethod
    def _results(messages):
        return [m for m in messages if m["role"] == "tool"]

    def _call(self, name, args):
        return {"id": f"c{self.stage}-{name}", "name": name, "arguments": args}

    def next(self, messages, tools):
        results = self._results(messages)
        if self.stage == 0:
            self.stage = 1
            return {"thought": "Check what the flagged rooms have in common.",
                    "tool_calls": [self._call("get_layout", {"rooms": self.rooms})]}

        if self.stage == 1:
            self.stage = 2
            ahus = sorted({self.layout["rooms"][r]["ahu"] for r in self.rooms})
            calls = [self._call("query_metrics", {"asset": a, "minutes": 15}) for a in ahus]
            calls.append(self._call("get_door_events", {"rooms": self.rooms, "minutes": 30}))
            calls += [self._call("query_metrics", {"asset": r, "minutes": 15}) for r in self.rooms]
            return {"thought": "Check air handler power, doors, and room pressure history.",
                    "tool_calls": calls}

        if self.stage == 2:
            self.stage = 3
            self.decision = self._decide(results)
            calls = [self._call("get_protocol", {"situation": s}) for s in self.decision["situations"]]
            return {"thought": f"Likely cause: {self.decision['category']}. Look up the protocol.",
                    "tool_calls": calls}

        return {"final": self._report(results)}

    # ------------------------------------------------------------ rules
    def _decide(self, results):
        by_tool = {}
        for m in results:
            by_tool.setdefault(m["name"], []).append(m["content"])
        layout_eid = by_tool["get_layout"][0]["evidence_id"]
        door = by_tool["get_door_events"][0]
        metrics = {r["asset"]: r for r in by_tool["query_metrics"] if "asset" in r}

        for ahu, spec in self.layout["air_handlers"].items():
            m = metrics.get(ahu)
            if m and m["current"] < 0.5 * spec["design_fan_kw"]:
                affected = [r for r in self.rooms if self.layout["rooms"][r]["ahu"] == ahu]
                kinds = {self.layout["rooms"][r]["type"] for r in affected}
                situations = ["air_handler_failure"]
                if "isolation" in kinds:
                    situations.append("isolation_pressure_lost")
                if "protective" in kinds:
                    situations.append("protective_pressure_lost")
                return {"category": "air_handler_failure", "asset": ahu, "affected": affected,
                        "situations": situations, "cause_eid": m["evidence_id"],
                        "layout_eid": layout_eid, "door_eid": door["evidence_id"],
                        "summary": f"{ahu} fan power dropped to {m['current']} kW "
                                   f"(design {spec['design_fan_kw']} kW)."}

        room = self.rooms[0]
        opened = [e for e in door["events"] if e["room"] == room and e["event"] == "opened"]
        if opened:
            return {"category": "door_not_latching", "asset": room, "affected": [room],
                    "situations": ["door_not_latching"], "cause_eid": door["evidence_id"],
                    "layout_eid": layout_eid, "door_eid": door["evidence_id"],
                    "summary": f"Door {room} was opened at {opened[-1]['ts'][11:19]} and pressure "
                               "has not recovered, though the sensor reads closed."}
        return {"category": "sensor_fault", "asset": room, "affected": [room],
                "situations": ["sensor_fault"], "cause_eid": metrics[room]["evidence_id"],
                "layout_eid": layout_eid, "door_eid": door["evidence_id"],
                "summary": f"Room {room} pressure reading jumped while its air handler and door are normal."}

    def _backup_room(self, exclude_ahu):
        for r, s in self.layout["rooms"].items():
            if s["type"] == "isolation" and s["ahu"] != exclude_ahu and r not in self.rooms:
                return r
        return None

    def _report(self, results):
        d = self.decision
        rooms = self.layout["rooms"]
        iso = [r for r in d["affected"] if rooms[r]["type"] == "isolation"]
        prot = [r for r in d["affected"] if rooms[r]["type"] == "protective"]
        actions = []
        if d["category"] == "air_handler_failure":
            actions.append({"id": "INSPECT_AHU", "params": {"asset": d["asset"]}})
            if iso or prot:
                actions.append({"id": "DOORS_CLOSED", "params": {"rooms": iso + prot}})
            if iso:
                actions.append({"id": "N95_ENTRY", "params": {"rooms": iso}})
                backup = self._backup_room(d["asset"])
                if backup:
                    actions.append({"id": "PREPARE_BACKUP_ROOM", "params": {"backup_room": backup}})
            if prot:
                actions.append({"id": "LIMIT_ENTRY_PROTECTIVE", "params": {"rooms": prot}})
        elif d["category"] == "door_not_latching":
            actions += [{"id": "CHECK_DOOR_LATCH", "params": {"rooms": d["affected"]}},
                        {"id": "DOORS_CLOSED", "params": {"rooms": d["affected"]}}]
            if iso:
                actions.append({"id": "N95_ENTRY", "params": {"rooms": iso}})
        else:
            actions.append({"id": "REPLACE_SENSOR", "params": {"rooms": d["affected"]}})

        return {
            "status": "diagnosed",
            "root_cause": {"category": d["category"], "asset": d["asset"],
                           "summary": d["summary"], "evidence": [d["cause_eid"]]},
            "affected_rooms": d["affected"],
            "confidence": "medium",
            "findings": [{"claim": "Flagged rooms and their air handlers identified.",
                          "evidence": [d["layout_eid"]]}],
            "ruled_out": [],
            "actions": actions,
        }
