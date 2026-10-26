"""The investigation loop: the model chooses tools, we run them, and the final
report must pass validation before it is accepted.

The model is pluggable. Any client with `next(messages, tools) -> dict` works:
  - returns {"tool_calls": [{"id": str, "name": str, "arguments": dict}, ...]}
  - or      {"final": <report dict>}
`baseline.RuleBasedClient` follows fixed rules (the comparison baseline);
`clients.py` is where the Muse Spark and open-weights clients plug in.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .report import validate_report
from .tools import TOOL_SCHEMAS

SYSTEM_PROMPT = """You are LabWatch, an investigator for hospital room air safety on unit {unit}.
You investigate equipment and environment only. You are not a clinician: never give
medical advice and never mention patients or diagnoses.

How to investigate:
1. Find what the flagged rooms have in common (get_layout).
2. Form a hypothesis about the cause.
3. Predict what else must be true if it is right, and check it with tools.
4. Rule out the alternatives: air handler, doors, and faulty sensors.
5. Call get_protocol for every situation that applies.
6. Reply with the final report as JSON only.

Rules:
- Every claim must cite evidence IDs (E1, E2, ...) returned by your tool calls.
- Recommend actions ONLY by id from get_protocol results, with their params.
- If the evidence does not support a conclusion, use status "inconclusive".

Report JSON:
{{"status": "diagnosed"|"inconclusive",
  "root_cause": {{"category": "air_handler_failure"|"sensor_fault"|"door_not_latching"|"none"|"unknown",
                 "asset": "<room or air handler id or null>", "summary": "<one sentence>", "evidence": ["E.."]}},
  "affected_rooms": ["..."], "confidence": "high"|"medium"|"low",
  "findings": [{{"claim": "...", "evidence": ["E.."]}}],
  "ruled_out": [{{"cause": "...", "evidence": ["E.."]}}],
  "actions": [{{"id": "ACTION_ID", "params": {{...}}}}]}}
"""


@dataclass
class AgentResult:
    status: str                       # accepted | rejected | max_steps
    report: dict | None
    actions: list[dict]               # rendered runbook actions
    errors: list[str]
    trace: list[dict] = field(default_factory=list)
    steps: int = 0


def incident_message(incident: dict) -> str:
    lines = [f"Incident {incident['incident_id']} opened at {incident['opened_at']}.",
             "Rooms flagged by the detector:"]
    for f in incident["flags"]:
        lines.append(f"- room {f['room']} at {f['ts']}: {f['reason']}, reading {f['dp_pa']} Pa")
    lines.append("Investigate and find the root cause.")
    return "\n".join(lines)


def run_investigation(client, inv, incident: dict, max_steps: int = 12,
                      max_repairs: int = 2) -> AgentResult:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(unit=inv.layout["unit_name"])},
        {"role": "user", "content": incident_message(incident)},
    ]
    trace, repairs = [], 0
    for step in range(1, max_steps + 1):
        resp = client.next(messages, TOOL_SCHEMAS)
        if "final" in resp:
            report = resp["final"]
            actions, errors = validate_report(report, inv)
            trace.append({"type": "report", "errors": errors})
            if not errors:
                return AgentResult("accepted", report, actions, [], trace, step)
            if repairs >= max_repairs:
                return AgentResult("rejected", report, [], errors, trace, step)
            repairs += 1
            messages.append({"role": "assistant", "content": report})
            messages.append({"role": "user", "content":
                             "Your report failed validation. Fix these and resend:\n- " + "\n- ".join(errors)})
            continue

        calls = resp.get("tool_calls") or []
        messages.append({"role": "assistant", "tool_calls": calls, "content": resp.get("thought", "")})
        if resp.get("thought"):
            trace.append({"type": "thought", "text": resp["thought"]})
        for c in calls:
            result = inv.call(c["name"], c.get("arguments") or {})
            messages.append({"role": "tool", "tool_call_id": c.get("id"), "name": c["name"], "content": result})
            trace.append({"type": "tool", "name": c["name"], "arguments": c.get("arguments"),
                          "evidence_id": result.get("evidence_id"), "error": result.get("error")})
    return AgentResult("max_steps", None, [], ["agent did not finish within the step limit"], trace, max_steps)
