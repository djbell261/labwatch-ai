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
