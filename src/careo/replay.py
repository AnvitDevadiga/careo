"""Re-running a recorded comparison and reporting whether it reproduces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from careo.config import Case, Config, ConfigError, load_config
from careo.engine import run_comparison
from careo.jsonvalue import canonical


@dataclass
class ReplayResult:
    drift: list[str]
    case_changes: list[str]
    new_report: dict[str, Any]

    @property
    def reproduced(self) -> bool:
        return not self.case_changes


def _identity_drift(role: str, old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    drift = []
    if old.get("resolved_executable") != new.get("resolved_executable"):
        drift.append(
            f"{role}: executable path changed {old.get('resolved_executable')} -> "
            f"{new.get('resolved_executable')}"
        )
    elif old.get("executable_sha256") != new.get("executable_sha256"):
        drift.append(f"{role}: executable {new.get('resolved_executable')} contents changed")
    old_files = old.get("argument_files_sha256", {}) or {}
    new_files = new.get("argument_files_sha256", {}) or {}
    for name in sorted(set(old_files) | set(new_files)):
        if name not in new_files:
            drift.append(f"{role}: file {name} no longer present")
        elif name not in old_files:
            drift.append(f"{role}: new argument file {name}")
        elif old_files[name] != new_files[name]:
            drift.append(f"{role}: file {name} contents changed")
    return drift


def _diff_signature(case: dict[str, Any]) -> str:
    return canonical(
        sorted(
            canonical([d.get("path"), d.get("kind"), d.get("reference"), d.get("candidate")])
            for d in case.get("differences", [])
        )
    )


def replay(report: dict[str, Any], config_path: str | Path | None = None) -> ReplayResult:
    path = Path(config_path) if config_path else Path(report["config_path"])
    config: Config = load_config(path)
    drift: list[str] = []

    try:
        cases = tuple(Case(c["id"], c["input"], c.get("description", "")) for c in report["cases"])
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"report cases are malformed: {exc}") from None
    if config.cases_fingerprint() != report["cases_fingerprint"]:
        drift.append(
            "current cases file differs from the recorded cases; replaying the recorded inputs"
        )
    config = config.with_cases(cases, "replayed from report")
    if config.policy_fingerprint() != report["policy_fingerprint"]:
        drift.append("comparison configuration (policy, programs, limits or rules) changed")
    if config.cases_fingerprint() != report["cases_fingerprint"]:
        drift.append("recorded cases do not match the recorded cases fingerprint (report edited?)")

    new = run_comparison(config)
    drift += _identity_drift("reference", report["reference"], new["reference"])
    drift += _identity_drift("candidate", report["candidate"], new["candidate"])
    old_env, new_env = report.get("environment", {}), new["environment"]
    for key in sorted(set(old_env) | set(new_env)):
        if old_env.get(key) != new_env.get(key):
            drift.append(f"environment {key}: {old_env.get(key)!r} -> {new_env.get(key)!r}")

    changes = []
    for old_case, new_case in zip(report["cases"], new["cases"], strict=True):
        if old_case["outcome"] != new_case["outcome"]:
            changes.append(f"{old_case['id']}: {old_case['outcome']} -> {new_case['outcome']}")
        elif _diff_signature(old_case) != _diff_signature(new_case):
            changes.append(f"{old_case['id']}: {old_case['outcome']} with different differences")
    return ReplayResult(drift, changes, new)
