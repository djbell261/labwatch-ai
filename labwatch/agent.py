"""The investigation loop: the model chooses tools, we run them, and the final
report must pass validation before it is accepted.

The model is pluggable. Any client with `next(messages, tools) -> dict` works:
  - returns {"tool_calls": [{"id": str, "name": str, "arguments": dict}, ...]}
  - or      {"final": <report dict>}
`baseline.RuleBasedClient` follows fixed rules (the comparison baseline);
`clients.py` is where the Muse Spark and open-weights clients plug in.

Malformed model output never crashes the loop: it goes back to the model as a tool
error or a validation error and counts against `max_repairs`. If the client itself
raises (timeout, HTTP error), the investigation ends with status "model_error".
"""
from __future__ import annotations

import json
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
    status: str                       # accepted | rejected | max_steps | model_error
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


def _parse_call(call) -> tuple[str | None, dict | None, str | None]:
    """Returns (name, arguments, error). Arguments may arrive as a JSON string."""
    if not isinstance(call, dict):
        return None, None, "each tool call must be an object with 'name' and 'arguments'"
    name = call.get("name")
    if not isinstance(name, str) or not name:
        return None, None, "tool call is missing 'name'"
    args = call.get("arguments")
    if args is None:
        args = {}
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except ValueError:
            return name, None, f"arguments for {name} are not valid JSON"
    if not isinstance(args, dict):
        return name, None, f"arguments for {name} must be a JSON object"
    return name, args, None


def _run_tool_calls(resp, inv, messages: list, trace: list) -> list[str]:
    """Runs the response's tool calls. Returns format errors, already sent back to the model."""
    calls = resp.get("tool_calls") if isinstance(resp, dict) else None
    if not isinstance(calls, list) or not calls:
        error = "Reply with tool calls or with the final report as a JSON object."
        messages.append({"role": "user", "content": error})
        trace.append({"type": "format_error", "errors": [error]})
        return [error]

    thought = resp.get("thought") or ""
    messages.append({"role": "assistant", "tool_calls": calls, "content": thought})
    if thought:
        trace.append({"type": "thought", "text": thought})
    errors = []
    for c in calls:
        name, args, error = _parse_call(c)
        if error:
            errors.append(error)
            result = {"error": error}
        else:
            result = inv.call(name, args)
        raw = c if isinstance(c, dict) else {}
        messages.append({"role": "tool", "tool_call_id": raw.get("id"), "name": name, "content": result})
        trace.append({"type": "tool", "name": name, "arguments": raw.get("arguments", c),
                      "evidence_id": result.get("evidence_id"), "error": result.get("error")})
    return errors


def run_investigation(client, inv, incident: dict, max_steps: int = 12,
                      max_repairs: int = 2) -> AgentResult:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(unit=inv.layout["unit_name"])},
        {"role": "user", "content": incident_message(incident)},
    ]
    trace, repairs = [], 0
    for step in range(1, max_steps + 1):
        try:
            resp = client.next(messages, TOOL_SCHEMAS)
        except Exception as e:  # timeouts, HTTP errors, client bugs
            return AgentResult("model_error", None, [], [f"model call failed: {type(e).__name__}: {e}"],
                               trace, step)

        report = None
        if isinstance(resp, dict) and "final" in resp:
            report = resp["final"]
            actions, errors = validate_report(report, inv, incident["rooms"])
            trace.append({"type": "report", "errors": errors})
            if not errors:
                return AgentResult("accepted", report, actions, [], trace, step)
            messages.append({"role": "assistant", "content": report})
            messages.append({"role": "user", "content":
                             "Your report failed validation. Fix these and resend:\n- " + "\n- ".join(errors)})
        else:
            errors = _run_tool_calls(resp, inv, messages, trace)
            if not errors:
                continue

        # Failed reports and malformed output both count against max_repairs.
        if repairs >= max_repairs:
            return AgentResult("rejected", report if isinstance(report, dict) else None, [], errors, trace, step)
        repairs += 1
    return AgentResult("max_steps", None, [], ["agent did not finish within the step limit"], trace, max_steps)
