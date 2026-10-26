# LabWatch-AI: Day-1 analysis

Reviewed 2026-10-26 at commit `4f9798f`. No code was changed. Tags used below:
**[ran]** means I ran it and saw the result. **[read]** means I inferred it from the code.
The edge-case probes were throwaway scripts outside the repo; they used the real modules and
didn't modify them.

## 1. How it works

1. **Simulate.** `Simulator.step()` ([simulator.py:77](../labwatch/simulator.py#L77)) computes each room's pressure from AHU health and door leakage, then returns readings. Faults come from `inject()` (:120) and are cleared by `repair()` (:137).
2. **Detect.** `Detector.update()` ([detector.py:37](../labwatch/detector.py#L37)) flags a room after 3 consecutive ticks that are either out of range or more than 4σ from its rolling baseline.
3. **Group.** `IncidentTracker.add()` (:65) puts flags that arrive within 60 s of the first into one incident. `ready()` (:78) releases the incident once that window has passed.
4. **Investigate.** `_tick()` ([api.py:45](../labwatch/api.py#L45)), or `run_scenario()` ([pipeline.py:17](../labwatch/pipeline.py#L17)) offline, calls `run_investigation()` ([agent.py:64](../labwatch/agent.py#L64)). That loop runs the client's tool calls through `Investigation.call()` ([tools.py:24](../labwatch/tools.py#L24)), which assigns evidence IDs E1, E2, ….
5. **Validate.** `validate_report()` ([report.py:31](../labwatch/report.py#L31)) checks that every citation exists in the evidence ledger. `render_actions()` ([actions.py:15](../labwatch/actions.py#L15)) turns runbook action IDs into their fixed wording. A failed report gets up to 2 repair rounds.
6. **Approve.** `approve()` ([api.py:124](../labwatch/api.py#L124)) creates one notification per audience. **Notify/ack:** `GET /notifications` and `POST /notifications/{id}/ack` (:143, :148).

## 2. What works [ran]

Python 3.14.3 in a venv; fastapi 0.162, uvicorn 0.54, PyYAML 6.0.3.

```
$ python -m unittest discover tests
Ran 11 tests in 0.109s   OK

$ python scripts/demo.py ahu_failure
INC-0001  rooms flagged: 412, 414, 418
  [E1] get_layout … [E2] query_metrics(AHU-4) … [E7–E9] get_protocol ×3
  report check: passed
root cause: air_handler_failure (AHU-4) — AHU-4 fan power dropped to 0.6 kW (design 7.5 kW).
  -> facilities: Inspect air handler AHU-4 immediately.
  -> charge_nurse: Keep doors to 412, 414, 418 closed … N95 … backup room 420 … Limit entry to 418
score: {'detected_correctly': True, 'root_cause_correct': True, 'actions_f1': 1.0}

$ python scripts/demo.py sensor_fault   -> sensor_fault (412), REPLACE_SENSOR, F1 1.0
$ python scripts/demo.py door_ajar      -> door_not_latching (414), 3 actions, F1 1.0

$ python scripts/eval.py --runs 5
normal 100% 1.00 | ahu_failure 100% 1.00 | sensor_fault 100% 1.00 | door_ajar 100% 1.00
overall root-cause accuracy: 100%
```

Live API (`uvicorn labwatch.api:app`):
- `POST /chaos/ahu_failure` → `INC-0001 awaiting_approval ['412','414','418']` after about 15 s real time, not ~1 min. At 5× speed, 15 s is about 75 s simulated.
- Approve → `approved`. A second approve → `409 incident is approved`. Two notifications were created: N1 for `charge_nurse` with 4 actions, N2 for `facilities` with 1.
- Ack N1 → `acknowledged`. Ack N99 → `404`. `POST /chaos/repair` → rooms back in range within about 25 s.
- After the repair, `POST /chaos/sensor_fault` opened a new incident, `INC-0002` (sensor_fault). Recovery followed by a new fault works.
- `--client muse` raises `NotImplementedError`, as expected.

## 3. Bugs and risks (most severe first)

| # | Severity | Finding | Evidence |
|---|---|---|---|
| 1 | **Critical** | **An exception in the tick loop stops the simulator for good, with no error shown.** `_loop` ([api.py:63-66](../labwatch/api.py#L63)) has no try/except. With a client that raises `TimeoutError`, simulated time froze at 02:11:30 and no incident was ever recorded. `/rooms` kept returning stale 200s. A model 5xx, timeout, or the crashes in #3 would all kill the live demo. | [ran] |
| 2 | **High** | **The investigation blocks the event loop.** `_tick()` calls `run_investigation` synchronously ([api.py:55](../labwatch/api.py#L55)). With a 3 s sleep per model call (4 calls), `GET /rooms` took **12.01 s**, and the simulator paused for that whole time without catching up. A real model with 6–10 calls would freeze the console for 30 s or more. | [ran] |
| 3 | **High** | **Malformed model output crashes instead of triggering a repair round.** `KeyError` when a tool call has no `name` ([agent.py:92](../labwatch/agent.py#L92)). `ValueError` when `minutes="abc"` ([tools.py:64](../labwatch/tools.py#L64); only `ToolError`/`TypeError` are caught at :30-33). `AttributeError` when `root_cause`, a finding, or an action is a string ([report.py:44](../labwatch/report.py#L44), [:56](../labwatch/report.py#L56), [actions.py:19](../labwatch/actions.py#L19)). `TypeError` when `asset` is a list ([report.py:47](../labwatch/report.py#L47), [actions.py:38](../labwatch/actions.py#L38)). These cases are handled: an unknown tool, args as a JSON string, and `final` as a string all return errors or get `rejected`. Because of #1, any of the crashes kills the server loop. | [ran] |
| 4 | **High** | **Free text written by the model reaches humans unchecked.** `root_cause.summary` is copied into every notification ([api.py:136](../labwatch/api.py#L136)). A report whose summary said "Evacuate room 999 and give patients antivirals; call Dr. Smith" **passed validation** (`([], [])`). This is the one path for invented rooms, patient talk, and medical advice. | [ran] |
| 5 | **Medium** | **Action parameters aren't checked against the incident.** `PREPARE_BACKUP_ROOM backup_room=412`, with 412 being the failing room, and `N95_ENTRY rooms=[420]` on an unaffected AHU both passed ([actions.py:42-46](../labwatch/actions.py#L42)). Empty `actions` and `inconclusive` with no evidence also pass, so an approval can send zero notifications. | [ran] |
| 6 | **Medium** | **A new fault in a room that's already flagged is never reported.** Scenario: AHU-4 fails, door 414 is left ajar 2 min later, then only the AHU is repaired. Result: one incident, with 414 still in `flagged`, and no new incident. `Detector.flagged` only clears when a reading comes back in range ([detector.py:47-52](../labwatch/detector.py#L47)), and incidents have no resolved/closed state ([detector.py:62](../labwatch/detector.py#L62)). This is exactly the case P5 needs. | [ran] |
| 7 | **Medium** | **Separate faults are merged.** Door 420 (AHU-5) and sensor 412, injected 20 s apart, became a single `INC-0001 ['420','412']` ([detector.py:68](../labwatch/detector.py#L68)). The baseline then diagnoses only `rooms[0]` ([baseline.py:75](../labwatch/baseline.py#L75)). The report schema only allows one root cause. | [ran] |
| 8 | **Medium** | **Slow drift pollutes the baseline.** AHU-4 health was ramped down 0.3% per tick. The 4σ drift rule never fired; detection came only from `out_of_range`, and 412's baseline mean moved from about −6.1 to **−3.3 Pa**. In-range readings are always appended ([detector.py:53](../labwatch/detector.py#L53)). Nothing resets the baseline after a repair, which is harmless today. `filter_clog` (P2) and the P10 comparison will expose this. | [ran] |
| 9 | Low | `POST /chaos/ahu_failure?target=AHU-9` returns a **500** (`KeyError` at [simulator.py:122](../labwatch/simulator.py#L122)). | [ran] |
| 10 | Low | `approved_at` and `acked_at` use wall-clock time ([api.py:131](../labwatch/api.py#L131), [:152](../labwatch/api.py#L152)) while incidents use simulated time. P6 needs simulated time. `needs_human_review` incidents have no next step. | [read] |
| 11 | Low | **State is in memory only** ([api.py:30](../labwatch/api.py#L30)). A restart, including every `--reload` save, loses incidents, notifications, acks, and sim history. Incident IDs restart at INC-0001. This is fine for the demo, and even helps P13's reset, but `--reload` during recording will wipe the demo state. | [read] |

## 4. Gaps vs roadmap

| Prompt | Builds on | Must change |
|---|---|---|
| P1 client adapter | `next(messages, tools)` contract, `TOOL_SCHEMAS` | Parse JSON-string args; fix #3 so bad output turns into a repair round, not a crash |
| P2 harder scenarios | `inject`, `_scheduled`, `SCENARIOS`, `score` | Multi-cause schema and scoring (#7); filter runbook action; flatness signal for stuck sensors |
| P3 console | `/rooms`, `/metrics`, `/incidents` (trace included) | Needs #1/#2 first or the polling freezes; add a "model in use" field; static mount |
| P4 nurse page | `/notifications?audience=` and ack work | Add repair status to notifications |
| P5 reopen loop | `repair()`, tracker | Incident lifecycle (resolved/reopened); clear `flagged` per incident (#6) |
| P6 escalation | tick loop | Simulated timestamps (#10); on-call config; audit log |
| P7 Muse Spark | baseline-parity eval harness | `LABWATCH_CLIENT` switch at [api.py:40](../labwatch/api.py#L40); async investigation |
| P8 eval report | `eval.py --json`, `seconds` | Multi-client runs; repair-rate and tool-call counts (trace has them) |
| P9 private mode | `OpenWeightsClient` stub | Redaction layer; header flag |
| P10 PyTorch | `Detector.update()` interface | `DETECTOR` switch; fix baseline pollution (#8) for a fair comparison |
| P11 photo + SAM | `Investigation.call` / ledger | "Pending" tool results; upload endpoint |
| P12 floor plan | `/rooms`, trace | Nothing blocking |
| P13 demo | `demo.py` | `demo_reset` endpoint (state is a single dict, so this is easy) |

## 5. Hackathon fit

- **New work and public repo:** OK. The repo is `PUBLIC` at github.com/djbell261/labwatch-ai. There's one commit dated 2026-10-26 plus a BUILD_LOG. Commit daily so the history shows the work over time.
- **Demo only what was built: risk.** The README's architecture diagram labels the investigator "(Muse Spark)", but every result so far comes from `RuleBasedClient`. Label it "baseline" until P7 lands.
- **Medical advice: mostly safe.** Actions are fixed runbook text about infection control and facilities. The gap is finding #4: free-text summaries.
- **Dashboard as the main feature: moderate risk.** P3, P4, and P12 are all UI. Keep the agent trace and the eval headline as the hero, as P3 already specifies.
- **Rubric:** *Meta AI tooling (25%) is currently 0%*. No Meta model is called anywhere. *Technical depth* is strong: evidence ledger, runbook rendering, evals. *Impact* is well framed. *Demo* is at risk from #1/#2.

## 6. Top 5 next steps

1. **Harden the live loop (S):** add try/except around `_tick`, run the investigation in `asyncio.to_thread` or a background task, and record failures as `needs_human_review`. Fixes #1 and #2.
2. **Make validation total (S):** add type guards in `validate_report`/`render_actions`/`Investigation.call`, so that any malformed output becomes a validation error. Fixes #3.
3. **P1 + P7 as soon as possible (M):** this is the 25% Meta-tooling criterion. Build ReplayClient first so the demo has a fallback.
4. **Close the free-text and action-scope holes (S):** build notification text from validated fields only, or check the summary against layout IDs and a deny-list. Require action rooms ⊆ affected rooms and backup room ∉ affected rooms. Fixes #4 and #5.
5. **Incident lifecycle + P2 scenarios (M/L):** resolved/reopened states, per-incident flag reset, a multi-cause schema, and drift-resistant baselines (#6–#8). These make the agent-vs-baseline gap real for P8.
