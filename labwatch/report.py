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
"""
from __future__ import annotations

from .actions import render_actions

CATEGORIES = {"air_handler_failure", "sensor_fault", "door_not_latching", "none", "unknown"}
CONFIDENCE = {"high", "medium", "low"}


def _check_cites(where: str, cites, ledger: dict, errors: list):
    if not cites:
        errors.append(f"{where}: no evidence cited")
        return
    for c in cites:
        if c not in ledger:
            errors.append(f"{where}: cites '{c}', which is not a real tool result")


def validate_report(report: dict, inv) -> tuple[list[dict], list[str]]:
    """Returns (rendered_actions, errors). Empty errors means the report passed."""
    errors: list[str] = []
    layout, ledger = inv.layout, inv.ledger
    if not isinstance(report, dict):
        return [], ["report is not a JSON object"]

    if report.get("status") not in {"diagnosed", "inconclusive"}:
        errors.append("status must be 'diagnosed' or 'inconclusive'")
    if report.get("confidence") not in CONFIDENCE:
        errors.append(f"confidence must be one of {sorted(CONFIDENCE)}")

    rc = report.get("root_cause") or {}
    if rc.get("category") not in CATEGORIES:
        errors.append(f"root_cause.category must be one of {sorted(CATEGORIES)}")
    asset = rc.get("asset")
    if asset is not None and asset not in layout["rooms"] and asset not in layout["air_handlers"]:
        errors.append(f"root_cause.asset '{asset}' does not exist")
    if report.get("status") == "diagnosed":
        _check_cites("root_cause", rc.get("evidence"), ledger, errors)

    for r in report.get("affected_rooms") or []:
        if str(r) not in layout["rooms"]:
            errors.append(f"affected_rooms: unknown room '{r}'")
    for i, f in enumerate(report.get("findings") or []):
        _check_cites(f"findings[{i}]", f.get("evidence"), ledger, errors)
    for i, f in enumerate(report.get("ruled_out") or []):
        _check_cites(f"ruled_out[{i}]", f.get("evidence"), ledger, errors)

    rendered, action_errors = render_actions(report.get("actions") or [], layout, inv.runbook)
    errors.extend(action_errors)
    return rendered, errors
