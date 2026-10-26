# Build log

All code in this repository was written during the Meta Global AI Developer Hackathon
(October 26 – November 4, 2026). No code from earlier projects is included.

## Day 1: Mon Oct 26
- Repo created.
- Simulated unit "4 West": 6 rooms on 2 air handlers (`config/layout.yaml`).
- Isolation protocol runbook (`config/runbook.yaml`).
- Physics-based pressure simulator with 3 faults: air handler failure, stuck sensor, door not latching.
- Statistical detector and incident grouping.
- Agent tools with evidence ledger; report validation (evidence citations, runbook-only actions).
- Investigation loop with self-repair on failed validation.
- Rule-based baseline investigator and eval runner.
- Live API: chaos controls, incidents, approval, notifications.
- 11 unit tests.

## Day 1 (evening): hardening
Fixes for the issues in `docs/ANALYSIS.md`.
- Live server: a failing tick is logged and the simulator keeps going. Investigations run on a
  single worker thread, so the simulator and API stay responsive while the model thinks
  (verified: a 12 s stall with a slow client is now 0 s). Status is `investigating` while it
  runs and `needs_human_review` (with the error) if it fails. An incident is never investigated twice.
- Agent loop: malformed tool calls and reports become tool or validation errors sent back to
  the model and count against `max_repairs`. A client exception ends the run as `model_error`.
- Notifications carry only a code-built summary ("air handler failure at AHU-4; rooms 412, 414,
  418 affected") and runbook text. The model's summary is console-only, labeled for supervisor review.
- Validation checks actions against the incident: action rooms within `affected_rooms`, those
  within the detector's flagged rooms, and backup rooms unaffected, isolation, and in range.
- Detector: only readings within 2σ of normal join the baseline, so slow drift is caught as
  drift instead of dragging the baseline. `reset_room()` runs after repair, with a 2-minute
  settle period; a room still bad after that opens a new incident.
- `POST /chaos/...` with an unknown target returns 400 instead of 500.
- 36 unit tests (25 new).
