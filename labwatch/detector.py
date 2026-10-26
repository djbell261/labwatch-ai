"""Simple statistical detector (day-1 baseline; the PyTorch model replaces it later).

A room is flagged when its pressure is out of its required range, or drifts far
from its own recent normal, for several readings in a row. Flags that arrive
close together are grouped into one incident, so one failure is one alert.

Only readings close to a room's normal join its baseline, so slow drift can't drag
the baseline along with it. After a repair, `reset_room()` starts the room fresh.
"""
from __future__ import annotations

import statistics
from collections import deque
from datetime import datetime

from .config import in_range


class Detector:
    def __init__(self, layout: dict, baseline_ticks: int = 60, z_limit: float = 4.0,
                 sustain: int = 3, std_floor: float = 0.2, admit_z: float = 2.0,
                 settle_ticks: int = 24):
        self.layout = layout
        self.z_limit, self.sustain, self.std_floor = z_limit, sustain, std_floor
        self.admit_z, self.settle_ticks = admit_z, settle_ticks
        self.baseline = {r: deque(maxlen=baseline_ticks) for r in layout["rooms"]}
        self.streak = {r: 0 for r in layout["rooms"]}
        self.settling = {r: 0 for r in layout["rooms"]}
        self.flagged: set[str] = set()

    def reset_room(self, room: str):
        """Start a room fresh after repair. It isn't watched for `settle_ticks` so normal
        recovery doesn't raise an alarm; if it's still out of range after that, it is
        flagged again as a new problem."""
        self.baseline[room].clear()
        self.streak[room] = 0
        self.flagged.discard(room)
        self.settling[room] = self.settle_ticks

    def _stats(self, room: str) -> tuple[float, float] | None:
        base = self.baseline[room]
        if len(base) < 20:
            return None
        return statistics.fmean(base), max(statistics.pstdev(base), self.std_floor)

    def _anomalous(self, room: str, dp: float) -> tuple[bool, str]:
        kind = self.layout["rooms"][room]["type"]
        if not in_range(self.layout, kind, dp):
            return True, "out_of_range"
        stats = self._stats(room)
        if stats and abs(dp - stats[0]) / stats[1] > self.z_limit:
            return True, "drift"
        return False, ""

    def update(self, readings: list[dict]) -> list[dict]:
        """Feed one tick of readings. Returns rooms newly flagged this tick."""
        new_flags = []
        for r in readings:
            if r["kind"] != "room":
                continue
            room, dp = r["asset"], r["dp_pa"]
            if self.settling[room]:
                self.settling[room] -= 1
                continue
            bad, reason = self._anomalous(room, dp)
            if bad:
                self.streak[room] += 1
                if self.streak[room] >= self.sustain and room not in self.flagged:
                    self.flagged.add(room)
                    new_flags.append({"room": room, "ts": r["ts"], "reason": reason, "dp_pa": dp})
            else:
                self.streak[room] = 0
                self.flagged.discard(room)
                stats = self._stats(room)
                if stats is None or abs(dp - stats[0]) <= self.admit_z * stats[1]:
                    self.baseline[room].append(dp)
        return new_flags


class IncidentTracker:
    """Groups flags within `window_s` seconds of the first one into one incident."""

    def __init__(self, window_s: int = 60):
        self.window_s = window_s
        self.open: dict | None = None
        self.count = 0

    def add(self, flags: list[dict]) -> dict | None:
        for f in flags:
            ts = datetime.fromisoformat(f["ts"])
            if self.open and (ts - self.open["_start"]).total_seconds() <= self.window_s:
                if f["room"] not in self.open["rooms"]:
                    self.open["rooms"].append(f["room"])
                self.open["flags"].append(f)
            else:
                self.count += 1
                self.open = {"incident_id": f"INC-{self.count:04d}", "_start": ts,
                             "opened_at": f["ts"], "rooms": [f["room"]], "flags": [f]}
        return self.open

    def ready(self, now_iso: str) -> bool:
        """True once the grouping window has passed and the incident can go to the agent."""
        if not self.open:
            return False
        return (datetime.fromisoformat(now_iso) - self.open["_start"]).total_seconds() >= self.window_s
