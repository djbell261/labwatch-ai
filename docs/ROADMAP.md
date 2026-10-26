# LabWatch-AI roadmap and build prompts

Deadline: **Wed Nov 4, 9:00 PM ET**. Aim to submit **Tue Nov 3**.
Judging: Impact 30% · Meta AI tooling 25% · Technical depth 20% · Demo video 25%.
Bonus targets: Best Agentic Use-Case, Best Build on Meta Open Weights, Most Viral (X).

## Strategy while the Meta API key is pending

Build everything that doesn't need the model. The agent loop already takes any client
with `next(messages, tools)`, so the Muse Spark client is a drop-in when access arrives.
The rule-based baseline keeps the whole pipeline runnable in the meantime.

## Schedule

| Day | Date | Build | Prompt |
|---|---|---|---|
| 1 | Mon 10/26 | Core loop, safety checks, evals, API ✅ | done |
| 2 | Tue 10/27 | Model client adapter + replay client | P1 |
| 2 | Tue 10/27 | Harder scenarios the baseline gets wrong | P2 |
| 3 | Wed 10/28 | Incident console page | P3 |
| 3 | Wed 10/28 | Nurse phone page | P4 |
| 4 | Thu 10/29 | Recovery check + reopen loop | P5 |
| 4 | Thu 10/29 | Escalation timer | P6 |
| ★ | when key arrives | Wire Muse Spark, tune prompt, run evals | P7 |
| 5 | Fri 10/30 | Eval report: agent vs baseline | P8 |
| — | **Fri 10/30** | **Milestone: full loop works end to end** | |
| 6 | Sat 10/31 | Private mode on Meta open weights | P9 |
| 6 | Sat 10/31 | PyTorch detector | P10 |
| 7 | Sun 11/1 | Ask-the-nurse photo + SAM 3.1 door check | P11 |
| 7 | Sun 11/1 | Live floor-plan visualization | P12 |
| 8 | Mon 11/2 | Demo script, record video, README | P13 |
| 9 | Tue 11/3 | Final video, submit, post on X | |
| 10 | Wed 11/4 | Buffer only | |

**Cut order if behind:** P12 → P10 → P6 → P11. Never cut P7, P8, P9, or the video.

## How to use the prompts

Paste one at a time into your coding agent (Muse Code, Claude Code, or similar) from the repo root.
After each one: run `python -m unittest discover tests`, try it, then commit.
Every prompt starts with the same context block, so the agent doesn't need this chat.

**Shared context (prepend to every prompt):**
> This repo is LabWatch-AI, a hackathon project: an AI investigator for hospital
> isolation-room air safety on a simulated unit. Read README.md first. Key files:
> labwatch/simulator.py (physics sim + faults), scenarios.py (ground truth), detector.py,
> tools.py (agent tools + evidence ledger), agent.py (investigation loop), report.py and
> actions.py (validation), baseline.py (rule-based client), api.py (FastAPI server),
> config/layout.yaml, config/runbook.yaml. Rules: all code must be new work; keep existing
> tests passing and add tests for new behavior; no patient data and no medical advice;
> the model may only recommend runbook action IDs; every claim must cite evidence IDs.
> Keep it simple and readable. Python 3.11+, FastAPI, plain HTML/JS for frontend (no build step).

---

### P1: Model client adapter (Day 2)
> In labwatch/clients.py, implement a reusable `ChatToolsClient` for any OpenAI-compatible
> chat-completions endpoint with function calling, configured by base_url, api_key, model
> (from env vars in .env.example). It must translate our generic messages (see agent.py
> docstring: tool results are dicts in "content", assistant tool_calls have id/name/arguments)
> into the API format and back, returning {"tool_calls": [...]} or {"final": <parsed JSON report>}.
> If the final text isn't valid JSON, return {"final": {}} so validation asks for a repair.
> Make MuseSparkClient and OpenWeightsClient thin subclasses with their own env vars.
> Add `ReplayClient` that records every response to a JSONL file and can replay it later
> (for deterministic demos and tests). Test the translation with a fake HTTP transport;
> do not call any real API in tests. Add `--client muse|open|replay` handling where needed.
> Done when: tests pass offline and README explains how to set the env vars.

Note: confirm the Meta Model API request format from the kickoff docs. If it isn't
OpenAI-compatible, keep the same class interface and change only the translation layer.

### P2: Harder scenarios (Day 2)
> Add scenarios to labwatch/simulator.py and scenarios.py that a fixed-rules investigator
> gets wrong, each with ground truth: (1) `sensor_fault_after_door`: someone opens and closes
> 412's door normally, and a minute later 412's pressure sensor sticks; the real cause is the
> sensor (tell: reading is suspiciously flat, fan and neighbors normal). (2) `filter_clog`:
> 418's supply filter slowly clogs over ~20 simulated minutes; AHU-4 power rises slightly
> while 418's positive pressure decays; add a filter-related runbook action and playbook.
> (3) `two_at_once`: AHU-5 is fine, 414's door doesn't latch while 412's sensor fails at the
> same time; both causes must be reported. Extend the report schema to allow multiple root
> causes if needed, and update validation and scoring. Run scripts/eval.py and confirm the
> baseline now fails at least two scenarios. Do not change the baseline to fix them.

### P3: Incident console (Day 3)
> Build labwatch/static/console.html (plain HTML/CSS/JS, served by FastAPI at "/"). Layout,
> top to bottom: header (unit, sim time, model in use, "Simulated environment" badge);
> the open incident with root cause, confidence, and rendered actions with an Approve button
> (POST /incidents/{id}/approve); the agent's investigation trace (thoughts and tool calls with
> evidence IDs, newest last) as the main panel; a room grid grouped by air handler showing
> pressure vs requirement in green/out-of-range colors; a pressure chart for a selected room
> using GET /metrics/{asset} (inline SVG, no chart library); demo controls calling
> POST /chaos/{fault}. Poll every 1s. The agent trace must be the visual focus, not the grid.
> Must work at phone width and in dark mode. Done when the full ahu_failure demo can be run
> from the browser alone.

### P4: Nurse phone page (Day 3)
> Build labwatch/static/nurse.html served at "/nurse". It polls GET /notifications?audience=charge_nurse
> and shows the newest alert: a red header with the summary, the action list as checkboxes,
> repair status, and a large Acknowledge button (POST /notifications/{id}/ack). Show
> "Acknowledged HH:MM" after. Design for a 390px-wide phone, 44px+ touch targets, readable at
> arm's length. No patient information anywhere.

### P5: Recovery check and reopen loop (Day 4)
> After an incident is approved, keep watching its rooms. When every affected room has been
> in range for 5 simulated minutes, mark it "resolved" and send a resolution notification on
> the same incident thread. If the root-cause asset recovers (for example AHU fan power back
> to design) but any affected room is still out of range for 2 minutes, set status
> "reopened" and run a new investigation scoped to the remaining rooms, passing the previous
> report as context in the first user message. Add a scenario `ahu_then_door` (AHU-4 fails;
> after repair, 414's door is found not latching) to test this. Show status changes on the console.

### P6: Escalation timer (Day 4)
> Add a simple on-call table in config/oncall.yaml (fictional roles per unit: charge_nurse,
> house_supervisor, facilities). Notifications not acknowledged within N simulated minutes
> (default 5) are escalated: status "escalated", a new notification to the next role, and an
> audit log entry. Implement as a check inside the API tick loop. Add GET /audit/{incident_id}.
> Test with simulated time, not real sleeps.

### P7: Wire up Muse Spark (when the API key arrives)
> Using ChatToolsClient from P1, wire MuseSparkClient to the Meta Model API with the model id
> from the docs. Make api.py choose the client from env LABWATCH_CLIENT (baseline|muse|open).
> Run `python scripts/eval.py --client muse --runs 5 --json evals/results/muse.json`. For every
> failed run, read the trace and fix the cause (prompt wording, tool descriptions, or tool
> output clarity), not the scoring. Record a ReplayClient session of a clean ahu_failure run
> for the demo fallback. Report the before/after accuracy in docs/BUILD_LOG.md.

Tuning checklist: ask the model to state a hypothesis before each tool call; require it to
check at least one alternative cause; make tool errors explain the fix; keep the system
prompt under ~400 words.

### P8: Eval report (Day 5)
> Extend scripts/eval.py to run multiple clients and write evals/REPORT.md: a table per
> scenario with root-cause accuracy, actions F1, validation-repair rate, average tool calls,
> and average time, for baseline vs muse (and open, if configured). Add a one-line headline
> ("Muse Spark found the correct root cause in X of Y runs vs Z for fixed rules"). This
> headline goes on the final video slide.

### P9: Private mode on Meta open weights (Day 6)
> Deploy Muse Spark Open or Muse Glimmer on Baseten or Modal (hackathon credits), then point
> OpenWeightsClient at it. Add a PRIVATE_MODE flag: when on, every model call goes to the
> self-hosted endpoint and the console header shows "Private mode · on-site model". Add a
> redaction step before any hosted call that strips anything that looks like a name, email,
> phone number, or MRN from tool outputs (there shouldn't be any; this is defense in depth),
> with tests. Run the eval with --client open and add it to the report.

### P10: PyTorch detector (Day 6)
> Add labwatch/ml/ with a small LSTM autoencoder trained on normal simulator data
> (multiple seeds, per-room pressure plus AHU power windows). Flag by reconstruction error
> above a threshold learned on held-out normal data. Keep the statistical detector as a
> fallback behind DETECTOR=stats|torch. Add scripts/train_detector.py and a comparison of
> time-to-detect and false alarms on all scenarios (especially filter_clog, which drifts slowly).
> Commit the trained weights only if under 5 MB.

### P11: Ask the nurse + SAM 3.1 (Day 7)
> Add an agent tool `request_photo(room, question)` that creates a nurse task (shown on
> nurse.html with a photo upload) and returns "pending" until a photo arrives. Add
> `check_door_photo(photo_id)` that calls SAM 3.1 via the Meta Model API to segment the door
> and frame, returning whether the door looks fully closed, with the mask as evidence.
> Use staged photos of an ordinary door (not in a hospital). The agent uses this only when
> sensors disagree (door sensor "closed" but pressure says otherwise). Demo with the
> door_ajar and ahu_then_door scenarios.

### P12: Live floor-plan view (Day 7, optional)
> Turn the floor-plan particle mockup into labwatch/static/floor.js, embedded at the top of
> the console. Drive it entirely from API data: room pressure sets particle flow direction and
> speed, AHU fan power sets duct flow, flagged rooms pulse, and each agent tool call in the
> trace highlights its target on the map (get_layout draws room→AHU links, query_metrics
> highlights the asset, get_door_events marks doors). Canvas 2D only.

### P13: Demo prep (Day 8)
> Write docs/DEMO_SCRIPT.md for a 3-minute video: hook (measles + isolation-room failure stat),
> live ahu_failure run with the agent's reasoning visible, approval and nurse page, sensor_fault
> false alarm, recovery, eval headline, Meta models used and why, private mode. Add a
> scripts/demo_reset.py that restarts the sim into a clean state. Update README with
> screenshots, setup, and the eval headline. Verify the repo opens logged out.

## Video checklist
- [ ] 3:00 or less; show only what was built during the event
- [ ] Hook in the first 10 seconds
- [ ] Agent reasoning visible on screen, not described
- [ ] Name each Meta model and what it does
- [ ] Eval number on screen
- [ ] Say "simulated hospital, no patient data"
- [ ] Recorded fallback of every live segment
