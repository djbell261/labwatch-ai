"""Live server: runs the simulator in real time, detects incidents, investigates them,
and exposes everything the console and nurse page need.

  uvicorn labwatch.api:app --reload
  then open http://127.0.0.1:8000/docs

Demo flow: POST /chaos/ahu_failure -> GET /incidents (wait ~1 min) ->
POST /incidents/{id}/approve -> nurse page reads GET /notifications -> POST /notifications/{id}/ack
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .agent import run_investigation
from .baseline import RuleBasedClient
from .config import load_layout, load_runbook
from .detector import Detector, IncidentTracker
from .simulator import TICK_SECONDS, Simulator
from .tools import Investigation

SPEED = float(os.environ.get("LABWATCH_SPEED", "5"))  # 5 = simulation runs 5x faster than real time

LAYOUT, RUNBOOK = load_layout(), load_runbook()
state = {
    "sim": Simulator(LAYOUT, seed=42),
    "detector": Detector(LAYOUT),
    "tracker": IncidentTracker(),
    "latest": [],
    "incidents": {},       # id -> incident dict (with report, actions, trace, status)
    "notifications": [],   # nurse/facilities notifications after approval
}


def make_client(layout, incident):
    # Swap for MuseSparkClient() / OpenWeightsClient() once wired up.
    return RuleBasedClient(layout, incident)


def _tick():
    sim = state["sim"]
    readings = sim.step()
    state["latest"] = readings
    tracker = state["tracker"]
    tracker.add(state["detector"].update(readings))
    inc = tracker.open
    if inc and inc["incident_id"] not in state["incidents"] and tracker.ready(sim.now.isoformat()):
        public = {k: v for k, v in inc.items() if not k.startswith("_")}
        inv = Investigation(sim, LAYOUT, RUNBOOK)
        result = run_investigation(make_client(LAYOUT, inc), inv, inc)
        state["incidents"][inc["incident_id"]] = {
            **public, "status": "awaiting_approval" if result.status == "accepted" else "needs_human_review",
            "report": result.report, "actions": result.actions, "errors": result.errors,
            "trace": result.trace, "ledger": inv.ledger,
        }


async def _loop():
    while True:
        _tick()
        await asyncio.sleep(TICK_SECONDS / SPEED)


@asynccontextmanager
async def lifespan(app):
    for _ in range(120):  # 10 simulated minutes of history before going live
        _tick()
    task = asyncio.create_task(_loop())
    yield
    task.cancel()


app = FastAPI(title="LabWatch-AI", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/rooms")
def rooms():
    out = {}
    for r in state["latest"]:
        if r["kind"] == "room":
            spec = LAYOUT["rooms"][r["asset"]]
            out[r["asset"]] = {**r, "type": spec["type"], "ahu": spec["ahu"],
                               "flagged": r["asset"] in state["detector"].flagged}
        else:
            out[r["asset"]] = r
    return {"time": state["sim"].now.isoformat(), "assets": out}


@app.get("/metrics/{asset}")
def metrics(asset: str, minutes: int = 20):
    inv = Investigation(state["sim"], LAYOUT, RUNBOOK)
    result = inv.call("query_metrics", {"asset": asset, "minutes": minutes})
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.post("/chaos/{fault}")
def chaos(fault: str, target: str | None = None):
    defaults = {"ahu_failure": ("ahu", "AHU-4"), "sensor_fault": ("room", "412"),
                "door_ajar": ("room", "414")}
    if fault == "repair":
        state["sim"].repair()
        return {"ok": True, "repaired": True}
    if fault not in defaults:
        raise HTTPException(400, f"unknown fault. Use one of {list(defaults)} or 'repair'")
    key, default = defaults[fault]
    state["sim"].inject(fault, **{key: target or default})
    return {"ok": True, "injected": fault, key: target or default}


@app.get("/incidents")
def incidents():
    return list(state["incidents"].values())


@app.post("/incidents/{incident_id}/approve")
def approve(incident_id: str, approved_by: str = "demo-supervisor"):
    inc = state["incidents"].get(incident_id)
    if not inc:
        raise HTTPException(404, "no such incident")
    if inc["status"] != "awaiting_approval":
        raise HTTPException(409, f"incident is {inc['status']}")
    inc["status"] = "approved"
    inc["approved_by"], inc["approved_at"] = approved_by, datetime.now().isoformat(timespec="seconds")
    for audience in sorted({a["audience"] for a in inc["actions"]}):
        state["notifications"].append({
            "id": f"N{len(state['notifications']) + 1}", "incident_id": incident_id,
            "audience": audience, "status": "sent", "sent_at": inc["approved_at"],
            "summary": inc["report"]["root_cause"]["summary"],
            "rooms": inc["report"]["affected_rooms"],
            "actions": [a["text"] for a in inc["actions"] if a["audience"] == audience],
        })
    return inc


@app.get("/notifications")
def notifications(audience: str | None = None):
    return [n for n in state["notifications"] if audience in (None, n["audience"])]


@app.post("/notifications/{nid}/ack")
def ack(nid: str):
    for n in state["notifications"]:
        if n["id"] == nid:
            n["status"], n["acked_at"] = "acknowledged", datetime.now().isoformat(timespec="seconds")
            return n
    raise HTTPException(404, "no such notification")
