"""Physics-based simulator for room pressure on a hospital unit.

Each room's pressure comes from a simple airflow balance:
  - the air handler (AHU) pushes supply air in and pulls exhaust air out
  - isolation rooms exhaust more than they supply  -> negative pressure
  - protective rooms supply more than they exhaust -> positive pressure
  - leakage (gaps, an ajar or open door) pulls pressure back toward zero

Faults change the physics, so each one leaves a realistic signature across
several signals. Nothing here is real hospital data.
"""
from __future__ import annotations

import random
from collections import deque
from datetime import datetime, timedelta

TICK_SECONDS = 5
HISTORY_TICKS = 2 * 3600 // TICK_SECONDS  # keep 2 hours

K_PRESSURE = 0.24
BASE_LEAK = 0.012
DOOR_LEAK = {"closed": 0.0, "ajar": 0.03, "open": 0.5}
# (supply, exhaust) design airflow per room type
DESIGN_FLOWS = {
    "isolation": (0.30, 0.36),
    "protective": (0.36, 0.30),
    "standard": (0.30, 0.30),
}
SENSOR_NOISE_PA = 0.12


class Room:
    def __init__(self, room_id: str, kind: str, ahu: str):
        self.room_id = room_id
        self.kind = kind
        self.ahu = ahu
        self.dp = 0.0
        self.door = "closed"          # physical state: closed | ajar | open
        self.door_sensor_ok = True    # False: sensor reads "closed" when ajar
        self.sensor_stuck_at = None   # broken pressure probe
        self.reported_door_open = False


class AirHandler:
    def __init__(self, ahu_id: str, design_kw: float):
        self.ahu_id = ahu_id
        self.design_kw = design_kw
        self.health = 1.0
        self.target_health = 1.0


class Simulator:
    def __init__(self, layout: dict, seed: int = 0, start: datetime | None = None):
        self.layout = layout
        self.rng = random.Random(seed)
        self.now = start or datetime(2026, 10, 27, 2, 0, 0)
        self.tick_count = 0
        self.ahus = {a: AirHandler(a, spec["design_fan_kw"])
                     for a, spec in layout["air_handlers"].items()}
        self.rooms = {r: Room(r, spec["type"], spec["ahu"])
                      for r, spec in layout["rooms"].items()}
        for room in self.rooms.values():
            room.dp = self._target_dp(room)
        self.history: dict[str, deque] = {}
        self.door_events: list[dict] = []
        self.active_faults: list[dict] = []
        self._scheduled: list[tuple[int, callable]] = []

    # ------------------------------------------------------------ physics
    def _target_dp(self, room: Room) -> float:
        supply, exhaust = DESIGN_FLOWS[room.kind]
        offset = (exhaust - supply) * self.ahus[room.ahu].health
        leak = BASE_LEAK + DOOR_LEAK[room.door]
        return -K_PRESSURE * offset * abs(offset) / leak ** 2

    def step(self) -> list[dict]:
        """Advance one tick and return the sensor readings for it."""
        self.tick_count += 1
        self.now += timedelta(seconds=TICK_SECONDS)
        for due, fn in [s for s in self._scheduled if s[0] <= self.tick_count]:
            fn()
        self._scheduled = [s for s in self._scheduled if s[0] > self.tick_count]

        ts = self.now.isoformat()
        readings = []
        for ahu in self.ahus.values():
            ahu.health += (ahu.target_health - ahu.health) * 0.4
            kw = max(0.0, ahu.design_kw * ahu.health * self.rng.gauss(1, 0.01))
            readings.append({"ts": ts, "kind": "ahu", "asset": ahu.ahu_id,
                             "fan_power_kw": round(kw, 2)})
            self._record(ahu.ahu_id, ts, kw)

        for room in self.rooms.values():
            room.dp += (self._target_dp(room) - room.dp) * 0.5
            if room.sensor_stuck_at is not None:
                reported = room.sensor_stuck_at + self.rng.gauss(0, 0.03)
            else:
                reported = room.dp + self.rng.gauss(0, SENSOR_NOISE_PA)
            door_open = room.door == "open" or (room.door == "ajar" and room.door_sensor_ok)
            if door_open != room.reported_door_open:
                self.door_events.append({"ts": ts, "room": room.room_id,
                                         "event": "opened" if door_open else "closed"})
                room.reported_door_open = door_open
            readings.append({"ts": ts, "kind": "room", "asset": room.room_id,
                             "dp_pa": round(reported, 2), "door_open": door_open})
            self._record(room.room_id, ts, reported)
        return readings

    def run(self, ticks: int) -> list[dict]:
        out = []
        for _ in range(ticks):
            out.extend(self.step())
        return out

    def _record(self, key: str, ts: str, value: float):
        self.history.setdefault(key, deque(maxlen=HISTORY_TICKS)).append((ts, round(value, 2)))

    # ------------------------------------------------------------ faults
    def inject(self, fault: str, **kw):
        if fault == "ahu_failure":
            self.ahus[kw.get("ahu", "AHU-4")].target_health = 0.08
        elif fault == "sensor_fault":
            self.rooms[kw.get("room", "412")].sensor_stuck_at = 1.2
        elif fault == "door_ajar":
            room = self.rooms[kw.get("room", "414")]
            room.door = "open"   # someone walks in...

            def bounce_back():   # ...door swings back but doesn't latch
                room.door = "ajar"
                room.door_sensor_ok = False
            self._scheduled.append((self.tick_count + 3, bounce_back))
        else:
            raise ValueError(f"unknown fault {fault}")
        self.active_faults.append({"fault": fault, "at": self.now.isoformat(), **kw})

    def repair(self):
        for ahu in self.ahus.values():
            ahu.target_health = 1.0
        for room in self.rooms.values():
            room.sensor_stuck_at = None
            room.door = "closed"
            room.door_sensor_ok = True
        self._scheduled.clear()
        self.active_faults.clear()
