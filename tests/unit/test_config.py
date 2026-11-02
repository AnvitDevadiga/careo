import os
import sys
from pathlib import Path

import pytest
import yaml

from careo.config import ConfigError, load_config

PY = sys.executable


def write(tmp_path: Path, cfg: dict, cases: str | None = '{"id": "a", "input": 1}\n') -> Path:
    if cases is not None:
        (tmp_path / "cases.jsonl").write_text(cases)
    p = tmp_path / "careo.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def base(**over):
    cfg = {
        "version": 1,
        "reference": {"command": [PY, "-c", "pass"], "cwd": "."},
        "candidate": {"command": [PY, "-c", "pass"], "cwd": "."},
        "cases": {"file": "cases.jsonl"},
    }
    cfg.update(over)
    return cfg


def err(tmp_path, cfg, match, cases='{"id": "a", "input": 1}\n'):
    with pytest.raises(ConfigError, match=match):
        load_config(write(tmp_path, cfg, cases))


def test_valid_minimal(tmp_path):
    cfg = load_config(write(tmp_path, base()))
    assert cfg.reference.argv[0] == str(Path(PY).absolute())
    assert len(cfg.cases) == 1
    assert cfg.policy.accepted_exit_codes == (0,)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_invalid_yaml(tmp_path):
    p = tmp_path / "careo.yaml"
    p.write_text("version: [1\n")
    with pytest.raises(ConfigError, match="YAML"):
        load_config(p)


def test_unknown_keys_are_errors(tmp_path):
    err(tmp_path, base(normalise=[]), "unknown key")
    c = base()
    c["comparison"] = {"stdErr": "exact"}
    err(tmp_path, c, "unknown key")


def test_wrong_version(tmp_path):
    err(tmp_path, base(version=2), "version")
    err(tmp_path, base(version=True), "version")


def test_shell_string_command_rejected(tmp_path):
    c = base()
    c["reference"]["command"] = "python -c pass"
    err(tmp_path, c, "not a shell string")


def test_missing_executable(tmp_path):
    c = base()
    c["candidate"]["command"] = ["definitely-not-a-real-program-xyz"]
    err(tmp_path, c, "not found")
    c["candidate"]["command"] = ["./missing.sh"]
    err(tmp_path, c, "not found")


def test_non_executable_file(tmp_path):
    (tmp_path / "tool.sh").write_text("#!/bin/sh\n")
    os.chmod(tmp_path / "tool.sh", 0o644)
    c = base()
    c["candidate"]["command"] = ["./tool.sh"]
    err(tmp_path, c, "not executable")


def test_cwd_traversal_rejected(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    c = base()
    c["reference"]["cwd"] = "../"
    err(proj, c, "outside the config directory")
    c["reference"]["cwd"] = "/"
    err(proj, c, "outside the config directory")


def test_symlink_escape_rejected(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (tmp_path / "secret.jsonl").write_text('{"id": "a", "input": 1}\n')
    (proj / "link.jsonl").symlink_to(tmp_path / "secret.jsonl")
    c = base(cases={"file": "link.jsonl"})
    err(proj, c, "outside the config directory", cases=None)


def test_cases_traversal_rejected(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    err(proj, base(cases={"file": "../cases.jsonl"}), "outside the config directory")


def test_relative_executable_escape_rejected(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    c = base()
    c["reference"]["command"] = ["../../bin/sh"]
    err(proj, c, "outside the config directory")


def test_duplicate_case_ids(tmp_path):
    err(tmp_path, base(), "duplicated", cases='{"id":"a","input":1}\n{"id":"a","input":2}\n')


def test_invalid_case_json(tmp_path):
    err(tmp_path, base(), "cases.jsonl:2", cases='{"id":"a","input":1}\n{bad\n')
    err(tmp_path, base(), "NaN", cases='{"id":"a","input":NaN}\n')


def test_empty_cases(tmp_path):
    err(tmp_path, base(), "at least one", cases="\n")


def test_input_size_limit(tmp_path):
    c = base(limits={"max_input_bytes": 10})
    err(tmp_path, c, "max_input_bytes", cases='{"id":"a","input":"' + "x" * 50 + '"}\n')


def test_cases_file_size_limit(tmp_path):
    err(tmp_path, base(limits={"max_cases_file_bytes": 5}), "max_cases_file_bytes")


def test_inline_cases_must_be_json(tmp_path):
    p = tmp_path / "careo.yaml"
    p.write_text(
        f"version: 1\nreference: {{command: [{PY!r}], cwd: .}}\n"
        f"candidate: {{command: [{PY!r}], cwd: .}}\n"
        "cases:\n  - id: a\n    input: 2026-01-01\n"
    )
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(p)


def test_normalization_rule_validation(tmp_path):
    rule = {"id": "t", "path": "no-slash", "action": "mask", "reason": "x"}
    err(tmp_path, base(normalize=[rule]), "JSON Pointer")
    rule = {"id": "t", "path": "/a", "action": "drop", "reason": "x"}
    err(tmp_path, base(normalize=[rule]), "action")
    rule = {"id": "t", "path": "/a", "action": "mask"}
    err(tmp_path, base(normalize=[rule]), "reason")
    rule = {"id": "t", "path": "/a", "action": "regex", "reason": "x", "pattern": "("}
    err(tmp_path, base(normalize=[rule]), "regex")
    rule = {
        "id": "t",
        "path": "/a",
        "action": "regex",
        "reason": "x",
        "pattern": "a",
        "replacement": "\\9",
    }
    err(tmp_path, base(normalize=[rule]), "replacement")
    rule = {"id": "t", "path": "/a", "action": "mask", "reason": "x", "pattern": "a"}
    err(tmp_path, base(normalize=[rule]), "only valid")
    r = {"id": "t", "path": "/a", "action": "mask", "reason": "x"}
    err(tmp_path, base(normalize=[r, r]), "duplicated")


@pytest.mark.parametrize(
    "limits,match",
    [
        ({"timeout_seconds": 0}, "> 0"),
        ({"timeout_seconds": "5"}, "number"),
        ({"max_output_bytes": 0}, "between"),
        ({"timeout_seconds": float("nan")}, "> 0"),
    ],
)
def test_limits_validation(tmp_path, limits, match):
    err(tmp_path, base(limits=limits), match)


def test_comparison_validation(tmp_path):
    err(tmp_path, base(comparison={"accepted_exit_codes": []}), "non-empty")
    err(tmp_path, base(comparison={"accepted_exit_codes": [256]}), "between")
    err(tmp_path, base(comparison={"repeat": 0}), "between")
    err(tmp_path, base(comparison={"array_order": "sorted"}), "one of")
    err(tmp_path, base(comparison={"float_abs_tolerance": -1}), ">= 0")


def test_env_validation(tmp_path):
    c = base()
    c["reference"]["env"] = {"BAD-NAME": "x"}
    err(tmp_path, c, "invalid variable name")
    c["reference"]["env"] = {"OK": 5}
    err(tmp_path, c, "string")


def test_fingerprint_excludes_env_values(tmp_path):
    c = base()
    c["reference"]["env"] = {"TOKEN": "secret-1"}
    f1 = load_config(write(tmp_path, c)).policy_fingerprint()
    c["reference"]["env"] = {"TOKEN": "secret-2"}
    cfg = load_config(write(tmp_path, c))
    assert cfg.policy_fingerprint() == f1
    assert "secret" not in str(cfg.policy_dict())
