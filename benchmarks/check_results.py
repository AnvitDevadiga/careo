"""Check that committed benchmark results are internally consistent.

Recomputes the summary from raw-records.jsonl and compares it with
summary.json, so published numbers cannot drift from the raw data.
"""

import json
import sys
from pathlib import Path

from careo.evaluation.bench import load_suite, summarize


def main(result_dir: str) -> int:
    d = Path(result_dir)
    records = [json.loads(x) for x in (d / "raw-records.jsonl").read_text().splitlines() if x]
    saved = json.loads((d / "summary.json").read_text())
    suite = load_suite(Path(__file__).parent / "booking" / "suite.yaml")
    recomputed = summarize(records, suite)
    if recomputed != saved["summary"]:
        print("summary.json does not match raw-records.jsonl", file=sys.stderr)
        return 1
    expected_runs = len(suite["cases"]) * (1 + 3 * saved["meta"]["seeds"])
    if len(records) != expected_runs:
        print(f"expected {expected_runs} raw records, found {len(records)}", file=sys.stderr)
        return 1
    print(f"OK: {len(records)} records consistent with summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
