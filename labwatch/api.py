"""Live server: runs the simulator in real time, detects incidents, investigates them,
and exposes everything the console and nurse page need.

  uvicorn labwatch.api:app --reload
  then open http://127.0.0.1:8000/docs

Demo flow: POST /chaos/ahu_failure -> GET /incidents (wait ~1 min) ->
POST /incidents/{id}/approve -> nurse page reads GET /notifications -> POST /notifications/{id}/ack

Investigations run on a worker thread, so the simulator keeps ticking while the model
thinks. An error in a tick is logged and the next tick runs anyway.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .agent import run_investigation
from .clients import make_client as build_client
from .config import load_layout, load_runbook
from .detector import Detector, IncidentTracker
from .report import staff_summary
from .simulator import TICK_SECONDS, Simulator
from .tools import Investigation

log = logging.getLogger("labwatch")

SPEED = float(os.environ.get("LABWATCH_SPEED", "5"))  # 5 = simulation runs 5x faster than real time
CLIENT = os.environ.get("LABWATCH_CLIENT", "baseline")  # baseline | muse | open | replay:<path>
AI_SUMMARY_LABEL = "AI summary (for supervisor review)"

LAYOUT, RUNBOOK = load_layout(), load_runbook()
# Ticks and tool calls both touch the simulator, and they run on different threads.
LOCK = threading.Lock()
# A single worker: investigations run one at a time, off the event loop.
INVESTIGATOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="investigator")


def new_state() -> dict:
    return {
        "sim": Simulator(LAYOUT, seed=42),
        "detector": Detector(LAYOUT),
        "tracker": IncidentTracker(),
        "latest": [],
        "incidents": {},       # id -> incident dict (with report, actions, trace, status)
        "notifications": [],   # nurse/facilities notifications after approval
    }


state = new_state()


class LockedInvestigation(Investigation):
    """Tool calls read the live simulator, so they wait for the current tick to finish."""

    def call(self, name, args):
        with LOCK:
            return super().call(name, args)


def make_client(layout, incident):
    return build_client(CLIENT, layout, incident)


def _outcome(result) -> dict:
    accepted = result.status == "accepted"
    out = {"status": "awaiting_approval" if accepted else "needs_human_review",
           "agent_status": result.status, "report": result.report, "actions": result.actions,
           "errors": result.errors, "trace": result.trace}
    if accepted:
        # Staff only ever get code-built text. The model's own words stay on the console.
        out["staff_summary"] = staff_summary(result.report)
        out["ai_summary"] = {"label": AI_SUMMARY_LABEL, "text": result.report["root_cause"].get("summary")}
    return out


def _investigate(incidents: dict, sim, incident: dict):
    inv = LockedInvestigation(sim, LAYOUT, RUNBOOK)
    try:
        outcome = _outcome(run_investigation(make_client(LAYOUT, incident), inv, incident))
    except Exception as e:
        log.exception("investigation of %s failed", incident["incident_id"])
        outcome = {"status": "needs_human_review", "agent_status": "error",
                   "errors": [f"investigation failed: {type(e).__name__}: {e}"]}
    # Replace the record in one step so readers never see it half-updated.
    incidents[incident["incident_id"]] = {**incident, "report": None, "actions": [], "trace": [],
                                          **outcome, "ledger": inv.ledger}


def _tick():
    with LOCK:
        sim = state["sim"]
        readings = sim.step()
        state["latest"] = readings
        tracker = state["tracker"]
        tracker.add(state["detector"].update(readings))
        inc = tracker.open
        if not inc or inc["incident_id"] in state["incidents"] or not tracker.ready(sim.now.isoformat()):
            return
        # The record exists before the worker starts, so an incident is never investigated twice.
        public = copy.deepcopy({k: v for k, v in inc.items() if not k.startswith("_")})
        state["incidents"][inc["incident_id"]] = {**public, "status": "investigating", "report": None,
                                                  "actions": [], "errors": [], "trace": [], "ledger": {}}
    INVESTIGATOR.submit(_investigate, state["incidents"], sim, public)


def _safe_tick():
    try:
        _tick()
    except Exception:
        log.exception("tick failed; the simulator keeps running")


async def _loop():
    while True:
        _safe_tick()
        await asyncio.sleep(TICK_SECONDS / SPEED)


@asynccontextmanager
async def lifespan(app):
    make_client(LAYOUT, {"rooms": []})  # fail fast on a bad LABWATCH_CLIENT or missing keys
    log.info("investigator client: %s", CLIENT)
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
    inv = LockedInvestigation(state["sim"], LAYOUT, RUNBOOK)
    result = inv.call("query_metrics", {"asset": asset, "minutes": minutes})
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.post("/chaos/{fault}")
def chaos(fault: str, target: str | None = None):
    defaults = {"ahu_failure": ("ahu", "AHU-4"), "sensor_fault": ("room", "412"),
                "door_ajar": ("room", "414")}
    if fault == "repair":
        with LOCK:
            state["sim"].repair()
            for room in LAYOUT["rooms"]:
                state["detector"].reset_room(room)
        return {"ok": True, "repaired": True}
    if fault not in defaults:
        raise HTTPException(400, f"unknown fault. Use one of {list(defaults)} or 'repair'")
    key, default = defaults[fault]
    target = target or default
    valid = LAYOUT["air_handlers"] if key == "ahu" else LAYOUT["rooms"]
    if target not in valid:
        raise HTTPException(400, f"unknown {key} '{target}'. Use one of {sorted(valid)}")
    with LOCK:
        state["sim"].inject(fault, **{key: target})
    return {"ok": True, "injected": fault, key: target}


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
        # Only code-built text goes to staff: the validated summary and fixed runbook wording.
        state["notifications"].append({
            "id": f"N{len(state['notifications']) + 1}", "incident_id": incident_id,
            "audience": audience, "status": "sent", "sent_at": inc["approved_at"],
            "summary": inc["staff_summary"],
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
