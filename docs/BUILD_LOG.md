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

## Day 2: Tue Oct 27
- Model client adapter (`labwatch/clients.py`): `ChatToolsClient` for any OpenAI-compatible
  chat-completions endpoint with function calling, built on httpx. Translates our messages,
  tool schemas, and responses; parses JSON reports, including fenced ones, and returns non-JSON
  text as-is so validation asks for a repair. Retries 429/5xx/timeouts with exponential backoff;
  other errors raise `ModelClientError`, which the agent turns into `model_error`. The key is never logged.
- `MuseSparkClient` and `OpenWeightsClient` read their own env vars; `.env` is loaded if
  python-dotenv is installed.
- `ReplayClient` records sessions to JSONL and replays them, for deterministic demo fallbacks
  (`demo.py --record PATH`, then `--client replay:PATH`).
- One `make_client(name, ...)` factory for `baseline | muse | open | replay:<path>`, used by
  `eval.py --client`, `demo.py --client`, and `LABWATCH_CLIENT` in the API. Missing config
  fails fast with a one-line message.
- 18 new offline tests (54 total) using `httpx.MockTransport`, including a full ahu_failure
  investigation over a fake endpoint.
- Muse Spark is still waiting on the API key; the next step is config only (P7).
