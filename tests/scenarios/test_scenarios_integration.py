"""End-to-end stateful runs against the scriptable item service."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from conftest import item_spec

from careo.scenarios.runner import (
    DIFFERENCE_CONFIRMED,
    DIFFERENCE_FLAKY,
    EXECUTION_ERROR,
    NO_DIFFERENCES_OBSERVED,
    RESET_FAILED,
    TIMEOUT,
    export_regression,
    replay_file,
    run,
)
from careo.scenarios.session import Session
from careo.scenarios.spec import load_spec


def go(spec_path: Path, out: Path) -> dict:
    return run(load_spec(spec_path), out)


def kinds(report: dict) -> set[tuple[str, str]]:
    return {(d["path"], d["kind"]) for d in report["evidence"][-1]["differences"]}


def test_identical_services_match(tmp_path):
    r = go(item_spec(tmp_path / "p"), tmp_path / "o")
    assert r["outcome"] == NO_DIFFERENCES_OBSERVED and r["exit_code"] == 0
    assert r["stats"]["operations_executed"] > 50
    assert "not a proof" in r["summary"]


def test_different_id_schemes_are_mapped_not_reported(tmp_path):
    r = go(item_spec(tmp_path / "p", [], ["--idbase", "8000"]), tmp_path / "o")
    assert r["outcome"] == NO_DIFFERENCES_OBSERVED, r["evidence"]
    assert r["stats"]["id_translations"] > 0


def test_ids_are_not_ignored_without_a_mapping(tmp_path):
    p = item_spec(tmp_path / "p", [], ["--idbase", "8000"])
    data = yaml.safe_load(p.read_text())
    del data["operations"][0]["produces"]
    data["operations"] = [data["operations"][0]]
    p.write_text(yaml.safe_dump(data))
    r = go(p, tmp_path / "o")
    assert r["outcome"] == DIFFERENCE_CONFIRMED
    assert ("<body>/id", "value_changed") in kinds(r)


def test_wrong_id_is_detected_even_with_mapping(tmp_path):
    r = go(item_spec(tmp_path / "p", [], ["--idbase", "8000", "--wrong-get-id"]), tmp_path / "o")
    assert r["outcome"] == DIFFERENCE_CONFIRMED
    diffs = r["evidence"][-1]["differences"]
    # the reference id maps to <item#1>; the candidate's wrong id maps to nothing
    assert any(d["path"] == "<body>/id" and d["reference"] == "<item#1>" for d in diffs)


def test_stateful_defect_found_minimized_verified_exported(tmp_path):
    spec_path = item_spec(tmp_path / "p", [], ["--double-delete-ok"])
    out = tmp_path / "o"
    r = go(spec_path, out)
    assert r["outcome"] == DIFFERENCE_CONFIRMED and r["exit_code"] == 1
    steps = [s["op"] for s in r["minimized_sequence"]["steps"]]
    assert steps == ["create_item", "delete_item", "delete_item"]
    assert r["minimized_sequence"]["length"] <= r["original_sequence"]["length"]
    assert r["verification"] == {**r["verification"], "checks": 3, "reproduced": 3}
    assert ("<status>", "status_changed") in kinds(r)

    files = export_regression(r, load_spec(spec_path), out)
    assert [f.name for f in files] == ["regression.json", "test_careo_regression.py"]
    rep = replay_file(out / "regression.json")
    assert rep.verdict == "MISMATCH" and rep.reproduced and not rep.drift
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            str(out / "test_careo_regression.py"),
        ],
        capture_output=True,
        text=True,
        cwd=out,
        timeout=120,
    )
    assert proc.returncode == 1 and "MISMATCH" in proc.stdout  # fails while the bug exists


def test_exported_regression_passes_once_fixed(tmp_path):
    spec_path = item_spec(tmp_path / "p", [], ["--double-delete-ok"])
    out = tmp_path / "o"
    r = go(spec_path, out)
    export_regression(r, load_spec(spec_path), out)
    data = yaml.safe_load(spec_path.read_text())
    data["services"]["candidate"]["command"].remove("--double-delete-ok")
    spec_path.write_text(yaml.safe_dump(data))
    rep = replay_file(out / "regression.json")
    assert rep.verdict == "MATCH"
    assert any("spec" in d for d in rep.drift)  # honest about the changed configuration


def test_database_side_effect_difference(tmp_path):
    r = go(item_spec(tmp_path / "p", [], ["--audit-extra"]), tmp_path / "o")
    assert r["outcome"] == DIFFERENCE_CONFIRMED
    assert any(p.startswith("<db>/audit") for p, _ in kinds(r))
    assert r["minimized_sequence"]["length"] == 1


def test_candidate_timeout_is_a_failure(tmp_path):
    r = go(
        item_spec(
            tmp_path / "p",
            [],
            ["--sleep-on", "GET /items"],
            http={"timeout_seconds": 0.5},
            generation={"max_scenarios": 10, "max_steps": 3, "replay_checks": 1},
        ),
        tmp_path / "o",
    )
    assert r["outcome"] == DIFFERENCE_CONFIRMED
    assert r["evidence"][-1]["verdict"] == "CANDIDATE_FAILED"
    assert r["evidence"][-1]["candidate"]["status"] == "timeout"


def test_reference_timeout_is_reported_as_reference_failure(tmp_path):
    r = go(
        item_spec(
            tmp_path / "p",
            ["--sleep-on", "GET /items"],
            [],
            http={"timeout_seconds": 0.5},
            generation={"max_scenarios": 10, "max_steps": 3, "replay_checks": 1},
        ),
        tmp_path / "o",
    )
    assert r["evidence"][-1]["verdict"] == "REFERENCE_FAILED"
    assert r["outcome"] != NO_DIFFERENCES_OBSERVED


def test_both_timeouts_are_never_equivalence(tmp_path):
    flags = ["--sleep-on", "GET /items"]
    r = go(
        item_spec(
            tmp_path / "p",
            flags,
            flags,
            http={"timeout_seconds": 0.5},
            generation={"max_scenarios": 10, "max_steps": 3},
        ),
        tmp_path / "o",
    )
    assert r["outcome"] == TIMEOUT and r["exit_code"] == 1


def test_candidate_crash_and_recovery(tmp_path):
    r = go(
        item_spec(
            tmp_path / "p",
            [],
            ["--crash-after", "5"],
            generation={"max_scenarios": 20, "max_steps": 8, "replay_checks": 2},
        ),
        tmp_path / "o",
    )
    assert r["outcome"] in (DIFFERENCE_CONFIRMED, DIFFERENCE_FLAKY)
    assert r["outcome"] != NO_DIFFERENCES_OBSERVED
    assert any("connection" in json.dumps(s) or "protocol" in json.dumps(s) for s in r["evidence"])


def test_partial_response_is_a_failure(tmp_path):
    r = go(
        item_spec(tmp_path / "p", [], ["--partial"], http={"timeout_seconds": 1}), tmp_path / "o"
    )
    assert r["outcome"] == DIFFERENCE_CONFIRMED
    last = r["evidence"][-1]
    assert last["verdict"] == "CANDIDATE_FAILED"
    assert last["candidate"]["status"] in ("protocol_error", "timeout")


def test_broken_reset_cannot_produce_a_verdict(tmp_path):
    r = go(
        item_spec(
            tmp_path / "p",
            [],
            ["--bad-reset-hook"],
            state={"reset_request": {"method": "POST", "path": "/__reset"}},
        ),
        tmp_path / "o",
    )
    assert r["outcome"] == RESET_FAILED and r["exit_code"] == 3
    assert "candidate" in r["notes"][0] and "does not match the fixture" in r["notes"][0]


def test_nondeterministic_candidate_is_flaky_not_confirmed(tmp_path):
    r = go(
        item_spec(tmp_path / "p", [], ["--alternate"], generation={"replay_checks": 4}),
        tmp_path / "o",
    )
    assert r["outcome"] in (DIFFERENCE_FLAKY, DIFFERENCE_CONFIRMED)
    if r["outcome"] == DIFFERENCE_CONFIRMED:
        pytest.fail(f"alternating behavior must not verify reliably: {r['verification']}")


def test_no_state_leaks_between_scenarios(tmp_path):
    spec = load_spec(item_spec(tmp_path / "p"))
    s = Session(spec, tmp_path / "run")
    s.prepare()
    try:
        create = {"op": "create_item", "params": {}, "query": {}, "body": {"name": "a"}}
        listing = {"op": "list_items", "params": {}, "query": {}}
        first = s.run_sequence([create, create, listing])
        assert len(first.steps[-1].reference["body"]["items"]) == 2
        second = s.run_sequence([listing])
        assert second.steps[0].reference["body"] == {"items": []}
        assert second.steps[0].candidate["body"] == {"items": []}
        assert s.ref.forward == {} and s.cand.forward == {}
    finally:
        s.teardown()


def test_restart_reset_clears_in_memory_state(tmp_path):
    spec = load_spec(
        item_spec(tmp_path / "p", ["--memory"], ["--memory"], state={"reset": "restart"})
    )
    s = Session(spec, tmp_path / "run")
    s.prepare()
    try:
        create = {"op": "create_item", "params": {}, "query": {}, "body": {"name": "a"}}
        s.run_sequence([create, create])
        again = s.run_sequence([create])
        assert again.steps[0].reference["body"]["seq"] == 1  # counter did not leak
    finally:
        s.teardown()


def test_sqlite_reset_does_not_hide_in_memory_leaks(tmp_path):
    """With reset: sqlite, in-memory state DOES survive (documented limitation)."""
    spec = load_spec(item_spec(tmp_path / "p", ["--memory"], ["--memory"]))
    s = Session(spec, tmp_path / "run")
    s.prepare()
    try:
        create = {"op": "create_item", "params": {}, "query": {}, "body": {"name": "a"}}
        s.run_sequence([create])
        again = s.run_sequence([create])
        assert again.steps[0].reference["body"]["seq"] == 2
    finally:
        s.teardown()


def test_service_that_fails_to_start(tmp_path):
    p = item_spec(tmp_path / "p")
    data = yaml.safe_load(p.read_text())
    data["services"]["candidate"]["command"] = [
        sys.executable,
        "-c",
        "raise SystemExit(4)",
        "{port}",
    ]
    p.write_text(yaml.safe_dump(data))
    r = go(p, tmp_path / "o")
    assert r["outcome"] == EXECUTION_ERROR and r["exit_code"] == 3


def test_external_service_connection_refused(tmp_path):
    from careo.execution.service import free_port

    p = item_spec(tmp_path / "p")
    data = yaml.safe_load(p.read_text())
    port = free_port()
    for role in ("reference", "candidate"):
        data["services"][role] = {"base_url": f"http://127.0.0.1:{port}"}
    del data["state"]
    p.write_text(yaml.safe_dump(data))
    r = go(p, tmp_path / "o")
    assert r["outcome"] == EXECUTION_ERROR
    assert "never evidence of equivalence" in json.dumps(r["notes"])


def test_replay_of_unknown_operation_is_an_error(tmp_path):
    from careo.config import ConfigError

    spec_path = item_spec(tmp_path / "p", [], ["--double-delete-ok"])
    out = tmp_path / "o"
    r = go(spec_path, out)
    export_regression(r, load_spec(spec_path), out)
    data = json.loads((out / "regression.json").read_text())
    data["steps"][0]["op"] = "no_such_op"
    (out / "bad.json").write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="no longer defines"):
        replay_file(out / "bad.json")
    data["steps"] = [{"op": "delete_item", "params": {"id": {"$ref": "item#9"}}, "query": {}}]
    (out / "bad2.json").write_text(json.dumps(data))
    assert replay_file(out / "bad2.json").verdict == "INVALID_SEQUENCE"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_services_are_cleaned_up_after_interruption(tmp_path, sig):
    p = item_spec(
        tmp_path / "p",
        ["--pidfile", "ref.pid"],
        ["--pidfile", "cand.pid"],
        generation={"max_scenarios": 100000, "max_steps": 20},
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "careo",
            "scenario",
            "run",
            "--spec",
            str(p),
            "--output-dir",
            str(tmp_path / "o"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pidfiles = [p.parent / "ref.pid", p.parent / "cand.pid"]
    deadline = time.monotonic() + 30
    while not all(f.exists() and f.read_text() for f in pidfiles) and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.5)
    pids = [int(f.read_text()) for f in pidfiles]
    assert all(_alive(x) for x in pids)
    proc.send_signal(sig)
    proc.wait(timeout=30)
    deadline = time.monotonic() + 10
    while any(_alive(x) for x in pids) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not any(_alive(x) for x in pids)
