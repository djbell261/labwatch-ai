"""Validates the agent's final report before any human sees it.

Report shape:
{
  "status": "diagnosed" | "inconclusive",
  "root_cause": {"category": str, "asset": str|null, "summary": str, "evidence": ["E2", ...]},
  "affected_rooms": ["412", ...],
  "confidence": "high" | "medium" | "low",
  "findings":  [{"claim": str, "evidence": ["E1"]}],
  "ruled_out": [{"cause": str, "evidence": ["E3"]}],
  "actions":   [{"id": "DOORS_CLOSED", "params": {"rooms": ["412"]}}]
}

Any shape, however malformed, comes back as validation errors, never an exception.
"""
from __future__ import annotations

from .actions import render_actions

CATEGORIES = {"air_handler_failure", "sensor_fault", "door_not_latching", "none", "unknown"}
CONFIDENCE = {"high", "medium", "low"}


def _one_of(value, options) -> bool:
    return isinstance(value, str) and value in options


def _list(report: dict, key: str, errors: list) -> list:
    value = report.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{key} must be a list")
        return []
    return value


def _check_cites(where: str, cites, ledger: dict, errors: list):
    if not cites:
        errors.append(f"{where}: no evidence cited")
        return
    if not isinstance(cites, list):
        errors.append(f"{where}: evidence must be a list of IDs like ['E1']")
        return
    for c in cites:
        if not isinstance(c, str) or c not in ledger:
            errors.append(f"{where}: cites '{c}', which is not a real tool result")


def _check_entries(key: str, report: dict, ledger: dict, errors: list):
    for i, f in enumerate(_list(report, key, errors)):
        if not isinstance(f, dict):
            errors.append(f"{key}[{i}] must be an object with 'evidence'")
            continue
        _check_cites(f"{key}[{i}]", f.get("evidence"), ledger, errors)


def validate_report(report, inv, flagged_rooms: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """Returns (rendered_actions, errors). Empty errors means the report passed.

    `flagged_rooms` are the rooms the detector flagged for this incident; when given,
    affected_rooms must be a subset of them.
    """
    errors: list[str] = []
    layout, ledger = inv.layout, inv.ledger
    if not isinstance(report, dict):
        return [], ["report must be a JSON object"]

    if not _one_of(report.get("status"), {"diagnosed", "inconclusive"}):
        errors.append("status must be 'diagnosed' or 'inconclusive'")
    if not _one_of(report.get("confidence"), CONFIDENCE):
        errors.append(f"confidence must be one of {sorted(CONFIDENCE)}")

    rc = report.get("root_cause")
    if not isinstance(rc, dict):
        errors.append("root_cause must be an object")
        rc = {}
    if not _one_of(rc.get("category"), CATEGORIES):
        errors.append(f"root_cause.category must be one of {sorted(CATEGORIES)}")
    asset = rc.get("asset")
    if asset is not None and not (_one_of(asset, layout["rooms"]) or _one_of(asset, layout["air_handlers"])):
        errors.append(f"root_cause.asset '{asset}' does not exist")
    if report.get("status") == "diagnosed":
        _check_cites("root_cause", rc.get("evidence"), ledger, errors)

    affected = [str(r) for r in _list(report, "affected_rooms", errors)]
    for r in affected:
        if r not in layout["rooms"]:
            errors.append(f"affected_rooms: unknown room '{r}'")
        elif flagged_rooms is not None and r not in flagged_rooms:
            errors.append(f"affected_rooms: room '{r}' was not flagged by the detector")
    _check_entries("findings", report, ledger, errors)
    _check_entries("ruled_out", report, ledger, errors)

    history = inv.sim.history
    latest_dp = {r: history[r][-1][1] for r in layout["rooms"] if history.get(r)}
    rendered, action_errors = render_actions(_list(report, "actions", errors), layout, inv.runbook,
                                             affected=set(affected), latest_dp=latest_dp)
    errors.extend(action_errors)
    return rendered, errors


def staff_summary(report: dict) -> str:
    """The only summary staff see: built from validated fields, never the model's own words."""
    rc = report["root_cause"]
    cause = rc["category"].replace("_", " ")
    if rc.get("asset"):
        cause += f" at {rc['asset']}"
    rooms = ", ".join(str(r) for r in report.get("affected_rooms") or []) or "none"
    return f"{cause}; rooms {rooms} affected"
