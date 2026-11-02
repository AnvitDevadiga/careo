import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from careo.cli import main

REPO = Path(__file__).resolve().parents[2]


def compare(config: Path, out: Path, *extra: str) -> tuple[int, dict]:
    code = main(["compare", "--config", str(config), "--output-dir", str(out), "--quiet", *extra])
    report = json.loads((out / "report.json").read_text()) if (out / "report.json").exists() else {}
    return code, report


def outcomes(report: dict) -> dict[str, str]:
    return {c["id"]: c["outcome"] for c in report["cases"]}


def test_identical_programs_match(project, tmp_path):
    cfg = project(
        [
            {"id": "obj", "input": {"value": {"a": [1, 2], "b": None}}},
            {"id": "unicode", "input": {"value": "héllo 😀 ‮"}},
            {"id": "big-int", "input": {"value": 2**70}},
        ]
    )
    code, report = compare(cfg, tmp_path / "out")
    assert code == 0
    assert set(outcomes(report).values()) == {"MATCH"}
    assert report["result"]["conclusion"] == "NO_DIFFERENCES_OBSERVED"
    assert "not a proof" in report["result"]["summary"]


def test_differences_are_reported(project, tmp_path):
    cfg = project(
        [
            {"id": "value", "input": {"ref": {"value": {"n": 1}}, "cand": {"value": {"n": 2}}}},
            {
                "id": "missing",
                "input": {"ref": {"value": {"n": 1, "m": 2}}, "cand": {"value": {"n": 1}}},
            },
            {"id": "type", "input": {"ref": {"value": {"n": 1}}, "cand": {"value": {"n": "1"}}}},
            {"id": "order", "input": {"ref": {"value": [1, 2]}, "cand": {"value": [2, 1]}}},
            {"id": "null", "input": {"ref": {"value": {"n": None}}, "cand": {"value": {}}}},
        ]
    )
    code, report = compare(cfg, tmp_path / "out")
    assert code == 1
    assert set(outcomes(report).values()) == {"MISMATCH"}
    kinds = {c["id"]: [d["kind"] for d in c["differences"]] for c in report["cases"]}
    assert kinds == {
        "value": ["value_changed"],
        "missing": ["missing_in_candidate"],
        "type": ["type_changed"],
        "order": ["array_order"],
        "null": ["null_vs_absent"],
    }


def test_failure_classification(project, tmp_path):
    cfg = project(
        [
            {"id": "cand-crash", "input": {"cand": {"signal": "SIGABRT"}, "value": 1}},
            {"id": "ref-exit", "input": {"ref": {"exit": 1}, "value": 1}},
            {"id": "both-same", "input": {"both": {"exit": 1, "raw": "boom"}}},
            {"id": "both-diff", "input": {"ref": {"exit": 1}, "cand": {"raw": "not json"}}},
            {"id": "invalid-json", "input": {"ref": {"value": 1}, "cand": {"raw": "{bad"}}},
            {"id": "timeout", "input": {"cand": {"sleep": 30}, "value": 1}},
            {"id": "big-output", "input": {"cand": {"big": 200000}, "value": 1}},
            {"id": "exit-code", "input": {"ref": {"exit": 2, "value": 1}, "cand": {"value": 1}}},
        ],
        limits={"timeout_seconds": 1, "max_output_bytes": 100000},
        comparison={"accepted_exit_codes": [0, 2]},
    )
    code, report = compare(cfg, tmp_path / "out")
    assert code == 1
    assert outcomes(report) == {
        "cand-crash": "CANDIDATE_FAILED",
        "ref-exit": "REFERENCE_FAILED",
        "both-same": "BOTH_FAILED",
        "both-diff": "BOTH_FAILED",
        "invalid-json": "CANDIDATE_FAILED",
        "timeout": "TIMEOUT",
        "big-output": "CANDIDATE_FAILED",
        "exit-code": "MISMATCH",
    }
    by_id = {c["id"]: c for c in report["cases"]}
    assert any("NOT evidence" in n for n in by_id["both-same"]["notes"])
    assert by_id["big-output"]["candidate_runs"][0]["stdout_exceeded_limit"] is True
    assert by_id["exit-code"]["differences"][0]["kind"] == "exit_code"
    assert report["result"]["outcomes"]["MATCH"] == 0


def test_stderr_policy(project, tmp_path):
    cases = [{"id": "s", "input": {"ref": {"stderr": "a", "value": 1}, "cand": {"value": 1}}}]
    code, _ = compare(project(cases), tmp_path / "o1")
    assert code == 0
    code, report = compare(project(cases, comparison={"stderr": "exact"}), tmp_path / "o2")
    assert code == 1 and report["cases"][0]["differences"][0]["kind"] == "stderr"


def test_repeat_detects_nondeterminism(project, tmp_path):
    cfg = project([{"id": "r", "input": {"both": {"random": True}}}], comparison={"repeat": 3})
    code, report = compare(cfg, tmp_path / "out")
    assert code == 1 and outcomes(report) == {"r": "INCONCLUSIVE"}
    assert len(report["cases"][0]["reference_runs"]) == 3


def test_normalization_visible_and_unmatched_rule_warned(project, tmp_path):
    cfg = project(
        [{"id": "r", "input": {"both": {"random": True}}}],
        normalize=[
            {"id": "rid", "path": "/id", "action": "mask", "reason": "uuid"},
            {"id": "typo", "path": "/idd", "action": "mask", "reason": "typo"},
        ],
    )
    code, report = compare(cfg, tmp_path / "out")
    assert code == 0
    assert report["normalization_totals"] == {"rid": 2, "typo": 0}
    assert any("typo" in w for w in report["warnings"])
    run = report["cases"][0]["reference_runs"][0]
    assert run["output"]["id"] != run["normalized_output"]["id"]
    md = (tmp_path / "out" / "report.md").read_text()
    assert "rid" in md and "Warnings" in md


def test_without_normalization_random_ids_mismatch(project, tmp_path):
    code, _ = compare(project([{"id": "r", "input": {"both": {"random": True}}}]), tmp_path / "o")
    assert code == 1


def test_execution_error_exit_code(project, tmp_path):
    from careo.config import load_config
    from careo.engine import run_comparison

    cfg = project([{"id": "a", "input": {"value": 1}}])
    victim = tmp_path / "victim.sh"
    victim.write_text("#!/bin/sh\necho 1\n")
    victim.chmod(0o755)
    cfg.write_text(cfg.read_text().replace(sys.executable, str(victim), 1))
    config = load_config(cfg)  # valid at load time
    victim.unlink()  # disappears between validation and execution
    report = run_comparison(config)
    assert report["result"]["exit_code"] == 3
    assert outcomes(report) == {"a": "EXECUTION_ERROR"}


def test_config_error_exit_code(tmp_path, capsys):
    assert main(["compare", "--config", str(tmp_path / "missing.yaml")]) == 2
    assert "not found" in capsys.readouterr().err


def test_non_python_candidate(project, tmp_path):
    cfg = project([{"id": "a", "input": {"value": {"ok": True}}}])
    script = cfg.parent / "cand.sh"
    script.write_text("#!/bin/sh\ncat > /dev/null\nprintf '{\"ok\": true}'\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    text = cfg.read_text()
    import yaml

    data = yaml.safe_load(text)
    data["candidate"] = {"command": ["./cand.sh"], "cwd": "."}
    cfg.write_text(yaml.safe_dump(data))
    code, report = compare(cfg, tmp_path / "out")
    assert code == 0, report["cases"]


def test_report_consistency(project, tmp_path):
    cfg = project(
        [
            {"id": "m", "input": {"value": 1}},
            {"id": "x", "input": {"ref": {"value": 1}, "cand": {"value": 2}}},
        ]
    )
    _, report = compare(cfg, tmp_path / "out")
    counts = report["result"]["outcomes"]
    assert sum(counts.values()) == report["result"]["total_cases"] == len(report["cases"])
    for c in report["cases"]:
        assert (c["outcome"] == "MATCH") == (not c["differences"] and not c["notes"])
    md = (tmp_path / "out" / "report.md").read_text()
    assert "DIFFERENCES_FOUND" in md and "`x` — MISMATCH" in md
    for key in (
        "policy_fingerprint",
        "cases_fingerprint",
        "environment",
        "reference",
        "candidate",
        "comparison_policy",
        "started_at",
        "finished_at",
    ):
        assert key in report
    assert report["reference"]["executable_sha256"].startswith("sha256:")
    assert "prog.py" in report["reference"]["argument_files_sha256"]


def test_report_has_no_env_values(project, tmp_path):
    cfg = project(
        [{"id": "a", "input": {"value": 1}}],
        reference={
            "command": [sys.executable, "prog.py", "ref"],
            "cwd": ".",
            "env": {"API_TOKEN": "hunter2-secret"},
        },
    )
    compare(cfg, tmp_path / "out")
    for name in ("report.json", "report.md"):
        assert "hunter2-secret" not in (tmp_path / "out" / name).read_text()


def test_replay_reproduces_and_detects_drift(project, tmp_path, capsys):
    cfg = project(
        [
            {"id": "m", "input": {"value": 1}},
            {"id": "x", "input": {"ref": {"value": 1}, "cand": {"value": 2}}},
        ]
    )
    compare(cfg, tmp_path / "out")
    report = tmp_path / "out" / "report.json"
    assert main(["replay", "--report", str(report)]) == 0
    out = capsys.readouterr().out
    assert "REPRODUCED" in out and "no drift" in out

    prog = cfg.parent / "prog.py"
    prog.write_text(prog.read_text() + "\n# edited\n")
    assert main(["replay", "--report", str(report)]) == 0
    assert "prog.py contents changed" in capsys.readouterr().out

    prog.write_text(
        prog.read_text().replace(
            'print(json.dumps(spec.get("value"', 'print(json.dumps(spec.get("nope"'
        )
    )
    assert main(["replay", "--report", str(report), "--output-dir", str(tmp_path / "r")]) == 1
    out = capsys.readouterr().out
    assert "NOT REPRODUCED" in out
    assert (tmp_path / "r" / "report.json").exists()


def test_replay_uses_recorded_inputs(project, tmp_path, capsys):
    cfg = project([{"id": "a", "input": {"value": 1}}])
    compare(cfg, tmp_path / "out")
    (cfg.parent / "cases.jsonl").write_text('{"id": "a", "input": {"value": 99}}\n')
    assert main(["replay", "--report", str(tmp_path / "out" / "report.json")]) == 0
    assert "replaying the recorded inputs" in capsys.readouterr().out


def test_replay_rejects_tampered_report(project, tmp_path, capsys):
    cfg = project([{"id": "a", "input": {"value": 1}}])
    compare(cfg, tmp_path / "out")
    path = tmp_path / "out" / "report.json"
    data = json.loads(path.read_text())
    data["cases"][0]["input"] = {"value": 2}
    path.write_text(json.dumps(data))
    main(["replay", "--report", str(path)])
    assert "report edited" in capsys.readouterr().out
    data["schema_version"] = 99
    path.write_text(json.dumps(data))
    assert main(["replay", "--report", str(path)]) == 2
    assert main(["inspect", str(path)]) == 2


def test_inspect(project, tmp_path, capsys):
    cfg = project([{"id": "x", "input": {"ref": {"value": {"a": 1}}, "cand": {"value": {}}}}])
    compare(cfg, tmp_path / "out")
    capsys.readouterr()
    assert main(["inspect", str(tmp_path / "out" / "report.json")]) == 1
    out = capsys.readouterr().out
    assert "[MISMATCH] x" in out and "/a: missing_in_candidate: 1 -> (absent)" in out


def test_validate_command(project, capsys):
    cfg = project([{"id": "a", "input": 1}])
    assert main(["validate", "--config", str(cfg)]) == 0
    assert "config OK" in capsys.readouterr().out


def test_demo_finds_seeded_boundary_bug(tmp_path):
    src = REPO / "examples" / "bulk-pricing"
    code, report = compare(src / "careo.yaml", tmp_path / "out")
    assert code == 1
    failing = {c["id"] for c in report["cases"] if c["outcome"] != "MATCH"}
    assert failing == {"at-tier-1", "at-tier-2"}
    diffs = {d["path"] for c in report["cases"] for d in c["differences"]}
    assert diffs == {"/discount_percent", "/total_cents"}


@pytest.mark.skipif(shutil.which("careo") is None, reason="console script not installed")
def test_console_script_entry_point(project, tmp_path):
    cfg = project([{"id": "a", "input": {"value": 1}}])
    r = subprocess.run(
        ["careo", "validate", "--config", str(cfg)],
        capture_output=True,
        text=True,
        env={**os.environ},
    )
    assert r.returncode == 0, r.stderr
