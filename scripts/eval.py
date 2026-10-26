"""Runs every scenario several times and scores the investigator.

  python scripts/eval.py                   # rule-based baseline
  python scripts/eval.py --client muse     # Muse Spark (needs META_* in .env)
  python scripts/eval.py --client replay:evals/replays/ahu.jsonl
  python scripts/eval.py --runs 10 --json evals/results/latest.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labwatch.clients import CLIENT_NAMES, ModelClientError, make_client  # noqa: E402
from labwatch.pipeline import run_scenario, score  # noqa: E402
from labwatch.scenarios import SCENARIOS  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default="baseline", help=CLIENT_NAMES)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--json", help="write detailed results to this path")
    a = ap.parse_args()
    try:
        make_client(a.client, None, {"rooms": []})   # fail fast on bad config
    except ModelClientError as e:
        sys.exit(f"error: {e}")
    factory = lambda layout, incident: make_client(a.client, layout, incident)  # noqa: E731

    rows, details = [], []
    for name in SCENARIOS:
        scores = []
        for seed in range(a.runs):
            run = run_scenario(name, factory, seed=seed)
            s = score(run)
            scores.append(s)
            res = run["result"]
            details.append({"scenario": name, "seed": seed, **s,
                            "status": res.status if res else None,
                            "report": res.report if res else None,
                            "errors": res.errors if res else None,
                            "seconds": run.get("seconds")})
        n = len(scores)
        rows.append((name, sum(s["root_cause_correct"] for s in scores) / n,
                     sum(s["actions_f1"] for s in scores) / n))

    print(f"\nclient: {a.client}   runs per scenario: {a.runs}\n")
    print(f"{'scenario':<16}{'root cause':>12}{'actions F1':>12}")
    for name, rc, f1 in rows:
        print(f"{name:<16}{rc:>11.0%}{f1:>12.2f}")
    overall = sum(r[1] for r in rows) / len(rows)
    print(f"\noverall root-cause accuracy: {overall:.0%}")

    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(details, indent=2, default=str))
        print(f"details written to {a.json}")


if __name__ == "__main__":
    main()
