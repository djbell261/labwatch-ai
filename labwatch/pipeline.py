"""Runs one scenario end to end: simulate -> detect -> investigate -> validate."""
from __future__ import annotations

import time

from .agent import run_investigation
from .config import load_layout, load_runbook
from .detector import Detector, IncidentTracker
from .scenarios import SCENARIOS
from .simulator import Simulator
from .tools import Investigation

WARMUP_TICKS = 120   # 10 simulated minutes of normal operation
WATCH_TICKS = 60     # 5 simulated minutes after the fault


def run_scenario(name: str, client_factory, seed: int = 0, layout=None, runbook=None) -> dict:
    layout = layout or load_layout()
    runbook = runbook or load_runbook()
    sc = SCENARIOS[name]
    sim = Simulator(layout, seed=seed)
    det, tracker = Detector(layout), IncidentTracker()

    for _ in range(WARMUP_TICKS):
        det.update(sim.step())
    if sc["inject"]:
        fault, kw = sc["inject"]
        sim.inject(fault, **kw)

    incident = None
    for _ in range(WATCH_TICKS):
        tracker.add(det.update(sim.step()))
        if tracker.ready(sim.now.isoformat()):
            incident = tracker.open
            break

    out = {"scenario": name, "seed": seed, "incident": None, "result": None}
    if incident is None:
        return out
    out["incident"] = {k: v for k, v in incident.items() if not k.startswith("_")}
    inv = Investigation(sim, layout, runbook)
    t0 = time.perf_counter()
    result = run_investigation(client_factory(layout, incident), inv, incident)
    out["result"] = result
    out["seconds"] = round(time.perf_counter() - t0, 2)
    out["ledger"] = inv.ledger
    return out


def score(run: dict) -> dict:
    truth = SCENARIOS[run["scenario"]]["truth"]
    if truth["category"] == "none":
        ok = run["incident"] is None
        return {"detected_correctly": ok, "root_cause_correct": ok, "actions_f1": 1.0 if ok else 0.0}
    res = run["result"]
    if run["incident"] is None or res is None or res.status != "accepted":
        return {"detected_correctly": run["incident"] is not None,
                "root_cause_correct": False, "actions_f1": 0.0}
    rc = res.report["root_cause"]
    rc_ok = rc.get("category") == truth["category"] and rc.get("asset") == truth["asset"]
    got = {a["id"] for a in res.actions}
    want = set(truth["actions"])
    tp = len(got & want)
    f1 = 0.0 if tp == 0 else 2 * tp / (len(got) + len(want))
    return {"detected_correctly": True, "root_cause_correct": rc_ok, "actions_f1": round(f1, 2)}
