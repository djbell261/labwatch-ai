"""Runs one scenario and prints what the investigator did, step by step.

  python scripts/demo.py ahu_failure
  python scripts/demo.py sensor_fault
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labwatch.baseline import RuleBasedClient  # noqa: E402
from labwatch.pipeline import run_scenario, score  # noqa: E402
from labwatch.scenarios import SCENARIOS  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else "ahu_failure"
if name not in SCENARIOS:
    sys.exit(f"pick one of: {', '.join(SCENARIOS)}")

run = run_scenario(name, lambda layout, inc: RuleBasedClient(layout, inc))
if run["incident"] is None:
    print("No incident detected. All rooms in range.")
    sys.exit()

inc = run["incident"]
print(f"\n{inc['incident_id']}  rooms flagged: {', '.join(inc['rooms'])}\n")
for t in run["result"].trace:
    if t["type"] == "thought":
        print(f"  thinking: {t['text']}")
    elif t["type"] == "tool":
        tag = t["evidence_id"] or f"ERROR {t['error']}"
        print(f"  [{tag}] {t['name']}({t['arguments']})")
    elif t["type"] == "report":
        print(f"  report check: {'passed' if not t['errors'] else t['errors']}")
    else:
        print(f"  format error: {t['errors']}")

r = run["result"]
print(f"\nstatus: {r.status}")
if r.status == "accepted":
    rc = r.report["root_cause"]
    print(f"root cause: {rc['category']} ({rc['asset']}) — {rc['summary']}")
    for a in r.actions:
        print(f"  -> {a['audience']}: {a['text']}")
print(f"\nscore vs ground truth: {score(run)}")
