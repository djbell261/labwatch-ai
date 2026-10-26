"""Runs one scenario and prints what the investigator did, step by step.

  python scripts/demo.py ahu_failure
  python scripts/demo.py sensor_fault --client muse
  python scripts/demo.py ahu_failure --client muse --record evals/replays/ahu.jsonl
  python scripts/demo.py ahu_failure --client replay:evals/replays/ahu.jsonl
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labwatch.clients import CLIENT_NAMES, ModelClientError, ReplayClient, make_client  # noqa: E402
from labwatch.pipeline import run_scenario, score  # noqa: E402
from labwatch.scenarios import SCENARIOS  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("scenario", nargs="?", default="ahu_failure", choices=SCENARIOS)
ap.add_argument("--client", default="baseline", help=CLIENT_NAMES)
ap.add_argument("--record", metavar="PATH", help="save the model's responses for replay:PATH")
a = ap.parse_args()


def factory(layout, incident):
    client = make_client(a.client, layout, incident)
    return ReplayClient(a.record, inner=client) if a.record else client


try:
    make_client(a.client, None, {"rooms": []})   # fail fast on bad config
except ModelClientError as e:
    sys.exit(f"error: {e}")
if a.record:
    Path(a.record).unlink(missing_ok=True)   # one recording per file
run = run_scenario(a.scenario, factory)
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
if r.errors:
    print(f"errors: {r.errors}")
if r.status == "accepted":
    rc = r.report["root_cause"]
    print(f"root cause: {rc['category']} ({rc['asset']}) — {rc['summary']}")
    for a in r.actions:
        print(f"  -> {a['audience']}: {a['text']}")
print(f"\nscore vs ground truth: {score(run)}")
