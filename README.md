# LabWatch-AI

**An AI investigator for hospital isolation-room air safety.**
Built for the Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026).

> Everything here runs on a **simulated** hospital unit. No real hospital data, no patient data.

> **Current status:** the investigator runs on the **rule-based baseline**
> ([`labwatch/baseline.py`](labwatch/baseline.py)). The Muse Spark and open-weights model
> clients are in progress ([`labwatch/clients.py`](labwatch/clients.py)); the investigation
> loop, tools, and safety checks are model-agnostic and ready for them.

## The problem

Isolation rooms keep airborne diseases like measles, TB, and COVID contained by holding
the room at negative air pressure. Protective rooms do the reverse for patients with weak
immune systems. These rooms only work when the pressure is right, and often it isn't: a
CDC-archived study of 678 isolation rooms found only 32% met the recommended pressure.

When something goes wrong, today's alarms say "pressure low," and nothing else. Someone has
to work out *why* (a failed fan? a door that didn't latch? a broken sensor?) across
separate systems, often at night, while the room may be leaking.

## What LabWatch does

1. **Watches** room pressure, door sensors, and air-handler fan power.
2. **Detects** rooms drifting out of range and groups related alarms into one incident.
3. **Investigates** like an engineer: finds what the rooms share, tests hypotheses with
   tools, and rules out alternatives.
4. **Recommends** actions **only from the unit's own isolation protocol**
   ([`config/runbook.yaml`](config/runbook.yaml)). The model picks action IDs; the wording is fixed.
5. **Waits for a human to approve**, then notifies facilities and the charge nurse.

### Safety design
- Every claim in a report must cite real tool results (`E1`, `E2`, ...). Made-up evidence is rejected.
- Invented actions, rooms, or equipment are rejected before anyone sees them. Actions may only
  target the incident's affected rooms, and a backup room must be an unaffected isolation room
  that is in range right now.
- **Nothing the model writes reaches staff.** Notifications contain a summary built by code from
  validated fields plus fixed runbook wording. The model's own summary appears on the console
  only, labeled "AI summary (for supervisor review)".
- Malformed model output is sent back to the model to fix; a failing model call sends the
  incident to human review instead of crashing the server.
- Room and equipment data only. The system never handles patient information or gives medical advice.
- Nothing is sent without human approval.

## Architecture

```
simulator ──► detector ──► incident ──► investigator (pluggable model client) ──► report validation ──► approval ──► notifications
 (physics)    (grouping)               tools: get_layout,                         (evidence + runbook)              (facilities,
                                       query_metrics,                                                                  charge nurse)
                                       get_door_events, get_protocol
```

| Path | What it is |
|---|---|
| `config/layout.yaml` | Fictional unit 4 West: rooms, types, air handlers |
| `config/runbook.yaml` | The only actions LabWatch may recommend |
| `labwatch/simulator.py` | Physics-based room pressure simulator with fault injection |
| `labwatch/scenarios.py` | Failure scenarios + ground truth for evals |
| `labwatch/detector.py` | Day-1 statistical detector (PyTorch model replaces it) |
| `labwatch/tools.py` | Agent tools and the evidence ledger |
| `labwatch/agent.py` | Investigation loop with report validation and self-repair |
| `labwatch/report.py`, `actions.py` | Evidence and runbook checks |
| `labwatch/baseline.py` | Rule-based investigator (comparison baseline) |
| `labwatch/clients.py` | Model clients (OpenAI-compatible), record/replay, client selection |
| `labwatch/api.py` | Live server: chaos controls, incidents, approval, notifications |
| `scripts/eval.py` | Scores investigators against ground truth |

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/demo.py ahu_failure        # watch one investigation
python scripts/eval.py --runs 5           # score every scenario
python -m unittest discover tests         # run tests

uvicorn labwatch.api:app --reload         # live server, docs at /docs
curl -X POST localhost:8000/chaos/ahu_failure
curl localhost:8000/incidents
```

## Connecting a model

The investigator takes any client with `next(messages, tools)`. Pick one with `--client`
(scripts) or `LABWATCH_CLIENT` (live server):

| Client | What it uses | Config |
|---|---|---|
| `baseline` (default) | Rule-based investigator, no model | none |
| `muse` | Muse Spark via the Meta Model API | `META_API_KEY`, `META_BASE_URL`, `META_MODEL` |
| `open` | Self-hosted open weights (private mode) | `OPEN_WEIGHTS_BASE_URL`, `OPEN_WEIGHTS_MODEL`, optional `OPEN_WEIGHTS_API_KEY` |
| `replay:<path>` | Replays a recorded session, no network | a JSONL file from `--record` |

Both model clients speak the OpenAI-compatible chat-completions format with function calling
([`labwatch/clients.py`](labwatch/clients.py)), retrying on 429, 5xx, and timeouts.

```bash
cp .env.example .env                       # fill in the keys (python-dotenv loads it if installed)
python scripts/demo.py ahu_failure --client muse
python scripts/demo.py ahu_failure --client muse --record evals/replays/ahu.jsonl   # demo fallback
python scripts/demo.py ahu_failure --client replay:evals/replays/ahu.jsonl
python scripts/eval.py --client muse --runs 5
LABWATCH_CLIENT=muse uvicorn labwatch.api:app
```

## Scenarios

| Scenario | What happens | Correct diagnosis |
|---|---|---|
| `normal` | Nothing | No incident |
| `ahu_failure` | AHU-4 fans fail; rooms 412, 414, 418 lose pressure | Air handler AHU-4 |
| `sensor_fault` | Room 412's probe sticks at +1.2 Pa; the room is actually fine | Sensor fault, not a real leak |
| `door_ajar` | Door 414 doesn't latch; the door sensor still reads closed | Door not latching |

## Status

- [x] Room simulator, scenarios, ground truth
- [x] Detector with incident grouping
- [x] Agent tools, evidence ledger, report and runbook validation
- [x] Rule-based baseline + eval runner
- [x] Live API: chaos, incidents, approval, notifications
- [x] Hardening: non-blocking investigations, malformed-output handling, code-built notifications
- [x] Model client adapter with record/replay, tested offline
- [ ] Muse Spark investigator: client ready, waiting on API access
- [ ] Harder scenarios (decoys, simultaneous faults)
- [ ] Console and nurse phone page
- [ ] PyTorch detector
- [ ] Private mode on Meta open weights (Muse Spark Open / Glimmer)
- [ ] Reopen-and-ask-the-nurse loop with SAM 3.1 door check

## Built during the event

All code in this repository was written during the hackathon, starting October 26, 2026.
See [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md).

## License

MIT
