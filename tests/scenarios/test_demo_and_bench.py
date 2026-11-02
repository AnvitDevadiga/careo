"""The booking demo and benchmark suite, executed for real."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from careo.cli import main
from careo.evaluation.bench import run_suite

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "examples" / "booking-api"


@pytest.mark.parametrize("app", ["app_reference.py", "app_candidate.py"])
def test_example_happy_path_tests_pass_on_both(app, tmp_path):
    from careo.adapters.http import parse_endpoint
    from careo.adapters.sqlite import SqliteState
    from careo.execution.service import ManagedService, free_port

    db = tmp_path / "db.sqlite"
    SqliteState(
        db, (DEMO / "schema.sql").read_text(), (DEMO / "seed.sql").read_text(), ()
    ).prepare()
    port = free_port()
    svc = ManagedService(
        app,
        [sys.executable, app, "--port", str(port), "--db", str(db)],
        DEMO,
        {},
        parse_endpoint(f"http://127.0.0.1:{port}"),
        "/health",
        15,
        tmp_path / "log",
    )
    svc.start()
    try:
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                str(DEMO / "test_happy_path.py"),
            ],
            env={"BOOKING_URL": svc.endpoint.url, "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        svc.stop()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "6 passed" in r.stdout


def test_demo_finds_and_minimizes_seeded_retry_bug(tmp_path, capsys):
    code = main(
        [
            "scenario",
            "run",
            "--spec",
            str(DEMO / "careo-scenarios.yaml"),
            "--output-dir",
            str(tmp_path),
        ]
    )
    out = capsys.readouterr().out
    assert code == 1, out
    report = json.loads((tmp_path / "scenario-report.json").read_text())
    assert report["outcome"] == "DIFFERENCE_CONFIRMED"
    ops = [s["op"] for s in report["minimized_sequence"]["steps"]]
    assert ops == ["create_booking", "cancel_booking", "cancel_booking"]
    assert report["verification"]["reproduced"] == 3
    diffs = {(d["path"], d["kind"]) for d in report["evidence"][-1]["differences"]}
    assert ("<status>", "status_changed") in diffs
    assert any(p.startswith("<db>/events") for p, _ in diffs)  # the duplicate side effect
    assert (tmp_path / "test_careo_regression.py").exists()
    assert main(["scenario", "replay", "--scenario", str(tmp_path / "regression.json")]) == 0


def test_scenario_validate_cli(capsys):
    assert main(["scenario", "validate", "--spec", str(DEMO / "careo-scenarios.yaml")]) == 0
    assert "cancel_booking" in capsys.readouterr().out


def test_bench_is_reproducible(tmp_path):
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        f"base_spec: {DEMO / 'careo-scenarios.yaml'}\n"
        f"unit_tests: {DEMO / 'test_happy_path.py'}\n"
        f"candidate_script: {REPO / 'benchmarks' / 'booking' / 'rewrite.py'}\n"
        "budget: {operations: 120, max_steps: 15, replay_checks: 2}\n"
        "cases:\n"
        "  - {name: retry, defect: retry_cancel, seeded: true}\n"
        "  - {name: clean, defect: none, seeded: false}\n"
    )
    assert run_suite(suite, tmp_path / "a", 1, False) == 0
    assert run_suite(suite, tmp_path / "b", 1, False) == 0

    def key(d):
        recs = [json.loads(x) for x in (d / "raw-records.jsonl").read_text().splitlines()]
        return [
            (
                r["case"],
                r["method"],
                r["outcome"],
                r.get("search_operations"),
                r.get("failure_signature"),
            )
            for r in recs
        ]

    assert key(tmp_path / "a") == key(tmp_path / "b")
    summary = json.loads((tmp_path / "a" / "summary.json").read_text())["summary"]
    assert summary["D_stateful_shrinking"]["false_alarms_on_clean"]["numerator"] == 0
    assert summary["D_stateful_shrinking"]["seeded_detection"] == {
        "numerator": 1,
        "denominator": 1,
        "rate": 1.0,
    }


def test_bench_unit_tests_that_error_count_as_failures(tmp_path):
    from careo.evaluation.bench import load_suite, method_a, variant_spec
    from careo.scenarios.spec import load_spec

    tests = tmp_path / "unit.py"
    tests.write_text(
        "def call(*a, **k):\n    return None\n"
        "def check_boom(base):\n    raise ConnectionResetError('crashed')\n"
        "CHECKS = [check_boom]\n"
    )
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        f"base_spec: {DEMO / 'careo-scenarios.yaml'}\nunit_tests: {tests}\n"
        f"candidate_script: {REPO / 'benchmarks' / 'booking' / 'rewrite.py'}\n"
        "budget: {operations: 10, max_steps: 2, replay_checks: 1}\n"
        "cases: [{name: c, defect: none, seeded: false}]\n"
    )
    suite = load_suite(suite_path)
    spec = variant_spec(load_spec(suite["base_spec"]), suite, "none")
    res = method_a(spec, suite, tmp_path)
    assert res["detected"] is True and "ConnectionResetError" in res["failures"][0]
