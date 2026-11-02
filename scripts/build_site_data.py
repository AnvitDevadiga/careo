"""Derive the website's static data from committed evidence, with validation.

    raw records + demo evidence  ->  validation  ->  site/public/data/*.json
                                                 ->  site/public/downloads/*

    python scripts/build_site_data.py           # (re)generate
    python scripts/build_site_data.py --check   # fail if site data is stale or invalid

Every number shown on the site is recomputed here from
benchmarks/results/latest/raw-records.jsonl and must equal the committed
summary.json. Inputs are only read, never modified. Negative, missed and invalid
runs are kept.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from careo.evaluation.bench import INVALID_OUTCOMES, METHODS, load_suite, summarize

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "benchmarks" / "results" / "latest"
DEMO = REPO / "benchmarks" / "results" / "demo"
EXAMPLE = REPO / "examples" / "booking-api"
SUITE = REPO / "benchmarks" / "booking" / "suite.yaml"
SITE = REPO / "site" / "public"

DEFECT_DESCRIPTIONS = {
    "retry_cancel": "Cancelling twice returns 200 and records a duplicate event",
    "slot_not_released": "A cancelled booking still blocks its slot",
    "lost_update": "Rescheduling returns the new slot but never stores it",
    "pagination": "Offsets above zero skip one extra row",
    "missing_error": "Unknown booking id returns 400 instead of 404",
    "status_transition": "Rescheduling a cancelled booking reactivates it",
}
METHOD_LABELS = {
    "A_unit_tests": "Example unit tests",
    "B_single_operation": "Single-operation random",
    "C_stateful": "Stateful differential",
    "D_stateful_shrinking": "Stateful + shrinking",
}
RECORD_FIELDS = (
    "case",
    "defect",
    "seeded",
    "method",
    "seed",
    "outcome",
    "detected",
    "search_operations",
    "operations_executed",
    "operations_to_first_difference",
    "time_to_first_difference_seconds",
    "sequence_length_before_shrink",
    "sequence_length_after_shrink",
    "verification_reproduced",
    "verification_checks",
    "runtime_seconds",
)


class ValidationError(Exception):
    pass


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ValidationError(msg)


def _commit_exists(sha: str) -> bool | None:
    try:
        r = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],  # noqa: S607
            cwd=REPO,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except OSError:
        return None
    return r.returncode == 0


def benchmark_data() -> dict[str, Any]:
    raw_path = RESULTS / "raw-records.jsonl"
    records = [json.loads(line) for line in raw_path.read_text().splitlines() if line.strip()]
    saved = json.loads((RESULTS / "summary.json").read_text())
    suite = load_suite(SUITE)
    meta = saved["meta"]

    recomputed = summarize(records, suite)
    _check(recomputed == saved["summary"], "summary.json does not match raw records")
    seeds = meta["seeds"]
    _check(
        len(records) == len(suite["cases"]) * (1 + 3 * seeds),
        f"expected {len(suite['cases']) * (1 + 3 * seeds)} records, found {len(records)}",
    )
    for case in suite["cases"]:
        for m in METHODS:
            n = sum(1 for r in records if r["case"] == case["name"] and r["method"] == m)
            _check(
                n == (1 if m == "A_unit_tests" else seeds), f"{case['name']}/{m}: {n} runs recorded"
            )
    for r in records:
        _check(
            r["seeded"] == next(c["seeded"] for c in suite["cases"] if c["name"] == r["case"]),
            f"seeded flag mismatch for {r['case']}",
        )
        if r["method"] != "A_unit_tests":
            _check(
                r["search_operations"] <= meta["budget_operations"],
                f"{r['case']}/{r['method']}/{r['seed']} exceeded the search budget",
            )
        if r.get("detected"):
            _check(r["outcome"] == "DIFFERENCE_CONFIRMED", "detected run not confirmed")
    commit = meta.get("git_commit")
    _check(
        bool(commit) and re.fullmatch(r"[0-9a-f]{40}", commit) is not None,
        "summary.json has no source commit",
    )
    exists = _commit_exists(commit)
    _check(exists is not False, f"source commit {commit} is not in this repository")

    methods = []
    for m in METHODS:
        s = recomputed[m]
        detected = [r for r in records if r["method"] == m and r.get("detected")]
        times = sorted(
            r["time_to_first_difference_seconds"]
            for r in detected
            if r.get("time_to_first_difference_seconds") is not None
        )
        methods.append(
            {
                "id": m,
                "label": METHOD_LABELS[m],
                **s,
                "median_time_to_first_difference_seconds": s[
                    "median_time_to_first_difference_seconds"
                ],
                "time_to_first_difference_range_seconds": [round(times[0], 3), round(times[-1], 3)]
                if times
                else None,
                "inconclusive_runs": sum(
                    1
                    for r in records
                    if r["method"] == m
                    and r["outcome"] in ("DIFFERENCE_FLAKY", "INCONCLUSIVE", "TIMEOUT")
                ),
                "invalid_runs_total": sum(
                    1 for r in records if r["method"] == m and r["outcome"] in INVALID_OUTCOMES
                ),
            }
        )
    return {
        "generated_from": {
            "raw_records": "benchmarks/results/latest/raw-records.jsonl",
            "raw_records_sha256": sha256(raw_path),
            "summary_sha256": sha256(RESULTS / "summary.json"),
            "suite": "benchmarks/booking/suite.yaml",
        },
        "meta": {
            "careo_version": meta["careo_version"],
            "source_commit": commit,
            "finished_at": meta["finished_at"],
            "seeds": seeds,
            "budget_operations": meta["budget_operations"],
            "max_steps": meta["max_steps"],
            "replay_checks": meta["replay_checks"],
            "python": meta["python"],
            "platform": meta["environment"]["platform"],
        },
        "cases": [
            {
                **c,
                "description": DEFECT_DESCRIPTIONS.get(
                    c["defect"] or "",
                    (
                        "Independent rewrite with no seeded defect"
                        if c["defect"] == "none"
                        else "The reference compared with a second copy of itself"
                    ),
                ),
            }
            for c in suite["cases"]
        ],
        "methods": methods,
        "records": [{k: r.get(k) for k in RECORD_FIELDS} for r in records],
        "reproduce": "careo bench --suite benchmarks/booking/suite.yaml --seeds 5 "
        "--output-dir benchmarks/results/latest",
    }


def _function_source(path: Path, name: str) -> str:
    text = path.read_text()
    m = re.search(rf"^([ \t]*)def {name}\(.*?(?=^\1def |^\S|\Z)", text, re.S | re.M)
    _check(m is not None, f"{name} not found in {path.name}")
    lines = m.group(0).rstrip().splitlines()
    indent = len(m.group(1))
    return "\n".join(line[indent:] for line in lines)


def demo_data() -> dict[str, Any]:
    report = json.loads((DEMO / "scenario-report.json").read_text())
    regression = json.loads((DEMO / "regression.json").read_text())
    test_src = (DEMO / "test_careo_regression.py").read_text()
    _check(report["outcome"] == "DIFFERENCE_CONFIRMED", "demo outcome is not confirmed")
    v = report["verification"]
    _check(v["reproduced"] == v["checks"], "demo verification incomplete")
    _check(
        regression["steps"] == report["minimized_sequence"]["steps"],
        "regression.json differs from the minimized sequence",
    )
    _check(
        [e["step"] for e in report["evidence"]] == report["minimized_sequence"]["steps"],
        "evidence does not correspond to the minimized steps",
    )
    _check(all("state" in e for e in report["evidence"]), "evidence lacks state snapshots")
    _check(
        "/Users/" not in json.dumps(report) and "/home/" not in json.dumps(report),
        "demo evidence contains local paths",
    )
    spec_text = (EXAMPLE / "careo-scenarios.yaml").read_text()
    return {
        "generated_from": {
            "report": "benchmarks/results/demo/scenario-report.json",
            "report_sha256": sha256(DEMO / "scenario-report.json"),
            "precomputed": True,
            "note": "Recorded from a real run of `careo scenario run` against the two booking "
            "services. The browser replays this recording; it does not run Python.",
        },
        "careo_version": report["environment"]["careo_version"],
        "outcome": report["outcome"],
        "summary": report["summary"],
        "isolation": report["isolation"],
        "warnings": report["warnings"],
        "generation": report["generation"],
        "stats": report["stats"],
        "original_sequence": report["original_sequence"],
        "minimized_sequence": {
            k: report["minimized_sequence"][k] for k in ("length", "steps", "method", "claim")
        },
        "verification": {"checks": v["checks"], "reproduced": v["reproduced"]},
        "evidence": report["evidence"],
        "id_mapping": report["id_mapping"],
        "regression_json": regression,
        "regression_test": test_src,
        "spec": spec_text,
        "sources": {
            "reference": {
                "file": "examples/booking-api/app_reference.py",
                "function": _function_source(EXAMPLE / "app_reference.py", "cancel"),
            },
            "candidate": {
                "file": "examples/booking-api/app_candidate.py",
                "function": _function_source(EXAMPLE / "app_candidate.py", "handle_cancel"),
            },
        },
        "seeded_bug": "Deliberately seeded: retrying a cancellation returns 200 and records a "
        "second 'cancelled' event instead of 409 already_cancelled.",
    }


DOWNLOADS = {
    "careo-demo-report.json": DEMO / "scenario-report.json",
    "careo-regression.json": DEMO / "regression.json",
    "test_careo_regression.py": DEMO / "test_careo_regression.py",
    "careo-benchmark-raw-records.jsonl": RESULTS / "raw-records.jsonl",
    "careo-benchmark-summary.json": RESULTS / "summary.json",
}


def outputs() -> dict[Path, bytes]:
    out: dict[Path, bytes] = {}
    for name, data in (("benchmark.json", benchmark_data()), ("demo.json", demo_data())):
        out[SITE / "data" / name] = (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode()
    for name, src in DOWNLOADS.items():
        out[SITE / "downloads" / name] = src.read_bytes()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    try:
        files = outputs()
    except ValidationError as exc:
        print(f"site data validation failed: {exc}", file=sys.stderr)
        return 1
    if a.check:
        stale = [p for p, data in files.items() if not p.exists() or p.read_bytes() != data]
        if stale:
            for p in stale:
                print(f"stale: {p.relative_to(REPO)}", file=sys.stderr)
            print("run: python scripts/build_site_data.py", file=sys.stderr)
            return 1
        print(f"OK: {len(files)} site data files are current and validated")
        return 0
    for p, data in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    print(f"wrote {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
