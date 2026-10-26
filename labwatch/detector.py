"""Simple statistical detector (day-1 baseline; the PyTorch model replaces it later).

A room is flagged when its pressure is out of its required range, or drifts far
from its own recent normal, for several readings in a row. Flags that arrive
close together are grouped into one incident, so one failure is one alert.
"""
from __future__ import annotations

import statistics
from collections import deque
from datetime import datetime

from .config import in_range


class Detector:
    def __init__(self, layout: dict, baseline_ticks: int = 60, z_limit: float = 4.0,
                 sustain: int = 3, std_floor: float = 0.2):
        self.layout = layout
        self.z_limit, self.sustain, self.std_floor = z_limit, sustain, std_floor
        self.baseline = {r: deque(maxlen=baseline_ticks) for r in layout["rooms"]}
        self.streak = {r: 0 for r in layout["rooms"]}
        self.flagged: set[str] = set()

    def _anomalous(self, room: str, dp: float) -> tuple[bool, str]:
        kind = self.layout["rooms"][room]["type"]
        if not in_range(self.layout, kind, dp):
            return True, "out_of_range"
        base = self.baseline[room]
        if len(base) >= 20:
            mean = statistics.fmean(base)
            std = max(statistics.pstdev(base), self.std_floor)
            if abs(dp - mean) / std > self.z_limit:
                return True, "drift"
        return False, ""

    def update(self, readings: list[dict]) -> list[dict]:
        """Feed one tick of readings. Returns rooms newly flagged this tick."""
        new_flags = []
        for r in readings:
            if r["kind"] != "room":
                continue
            room, dp = r["asset"], r["dp_pa"]
            bad, reason = self._anomalous(room, dp)
            if bad:
                self.streak[room] += 1
                if self.streak[room] >= self.sustain and room not in self.flagged:
                    self.flagged.add(room)
                    new_flags.append({"room": room, "ts": r["ts"], "reason": reason, "dp_pa": dp})
            else:
                self.streak[room] = 0
                self.flagged.discard(room)
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
