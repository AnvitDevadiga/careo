"""Running every case against both programs and classifying the outcome."""

from __future__ import annotations

import hashlib
import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from careo import __version__
from careo.comparison import EXIT_CODE, STDERR, Difference, diff
from careo.config import Case, Config, ProgramSpec, encode_input
from careo.execution import OUTPUT_LIMIT, SPAWN_ERROR, TIMEOUT, RunResult, run_program
from careo.jsonvalue import JSONParseError, canonical, parse_strict
from careo.normalize import apply_rules

REPORT_SCHEMA_VERSION = 1

MATCH = "MATCH"
MISMATCH = "MISMATCH"
REFERENCE_FAILED = "REFERENCE_FAILED"
CANDIDATE_FAILED = "CANDIDATE_FAILED"
BOTH_FAILED = "BOTH_FAILED"
TIMEOUT_OUTCOME = "TIMEOUT"
INCONCLUSIVE = "INCONCLUSIVE"
EXECUTION_ERROR = "EXECUTION_ERROR"
OUTCOMES = (
    MATCH,
    MISMATCH,
    REFERENCE_FAILED,
    CANDIDATE_FAILED,
    BOTH_FAILED,
    TIMEOUT_OUTCOME,
    INCONCLUSIVE,
    EXECUTION_ERROR,
)

# Side statuses
OK = "ok"
FAILED = "failed"

# Exit codes of `careo compare`
EXIT_ALL_MATCH = 0
EXIT_DIFFERENCES = 1
EXIT_USAGE = 2
EXIT_EXECUTION_ERROR = 3

REPORT_TEXT_LIMIT = 65536
_HASH_FILE_LIMIT = 256 * 1024 * 1024


@dataclass
class Observation:
    run: RunResult
    status: str  # ok | failed | timeout | spawn_error
    reason: str = ""
    output: Any = None
    normalized: Any = None
    normalizations: dict[str, int] = field(default_factory=dict)

    def signature(self, compare_stderr: bool) -> str:
        """What must be identical across repeated runs of the same input."""
        return canonical(
            [
                self.status,
                self.run.exit_code,
                self.normalized if self.status == OK else self.reason,
                self.run.stderr.decode("utf-8", "replace") if compare_stderr else None,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "exit_code": self.run.exit_code,
            "duration_seconds": round(self.run.duration_seconds, 6),
        }
        if self.reason:
            out["failure_reason"] = self.reason
        out.update(_text("stdout", self.run.stdout, self.run.stdout_truncated))
        out.update(_text("stderr", self.run.stderr, self.run.stderr_truncated))
        if self.status == OK:
            out["output"] = self.output
            if self.normalizations:
                out["normalized_output"] = self.normalized
                out["normalizations_applied"] = self.normalizations
        return out


def _text(name: str, data: bytes, truncated_by_limit: bool) -> dict[str, Any]:
    out: dict[str, Any] = {name: data[:REPORT_TEXT_LIMIT].decode("utf-8", "replace")}
    if len(data) > REPORT_TEXT_LIMIT:
        out[f"{name}_report_truncated"] = True
    if truncated_by_limit:
        out[f"{name}_exceeded_limit"] = True
    return out


def observe(spec: ProgramSpec, case: Case, config: Config) -> Observation:
    run = run_program(
        spec.argv,
        spec.cwd,
        spec.child_env(),
        encode_input(case.input),
        config.limits.timeout_seconds,
        config.limits.max_output_bytes,
    )
    if run.status == SPAWN_ERROR:
        return Observation(run, SPAWN_ERROR, run.error or "could not start process")
    if run.status == TIMEOUT:
        return Observation(run, TIMEOUT, f"exceeded timeout of {config.limits.timeout_seconds}s")
    if run.status == OUTPUT_LIMIT:
        return Observation(
            run, FAILED, f"output exceeded limit of {config.limits.max_output_bytes} bytes"
        )
    if run.error:
        return Observation(run, FAILED, run.error)
    code = run.exit_code
    assert code is not None
    if code < 0:
        return Observation(run, FAILED, f"killed by signal {-code}")
    if code not in config.policy.accepted_exit_codes:
        return Observation(run, FAILED, f"exit code {code} is not an accepted exit code")
    try:
        value = parse_strict(run.stdout)
    except JSONParseError as exc:
        return Observation(run, FAILED, f"stdout is not a valid JSON value: {exc}")
    normalized, counts = apply_rules(value, config.rules)
    return Observation(run, OK, "", value, normalized, counts)


@dataclass
class CaseResult:
    case: Case
    outcome: str
    reference: list[Observation]
    candidate: list[Observation]
    differences: list[Difference]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case.id,
            "description": self.case.description,
            "input": self.case.input,
            "outcome": self.outcome,
            "notes": self.notes,
            "differences": [d.to_dict() for d in self.differences],
            "reference_runs": [o.to_dict() for o in self.reference],
            "candidate_runs": [o.to_dict() for o in self.candidate],
        }


def classify(
    ref: list[Observation], cand: list[Observation], config: Config
) -> tuple[str, list[Difference], list[str]]:
    notes: list[str] = []
    policy = config.policy
    exact_stderr = policy.stderr == "exact"
    all_obs = [("reference", o) for o in ref] + [("candidate", o) for o in cand]

    spawn = [f"{side}: {o.reason}" for side, o in all_obs if o.status == SPAWN_ERROR]
    if spawn:
        return EXECUTION_ERROR, [], spawn
    timeouts = [f"{side}: {o.reason}" for side, o in all_obs if o.status == TIMEOUT]
    if timeouts:
        return TIMEOUT_OUTCOME, [], timeouts

    for side, obs in (("reference", ref), ("candidate", cand)):
        if len({o.signature(exact_stderr) for o in obs}) > 1:
            notes.append(
                f"{side} produced different results across {len(obs)} repeated runs of the "
                "same input (nondeterministic or normalization incomplete)"
            )
    if notes:
        return INCONCLUSIVE, [], notes

    r, c = ref[0], cand[0]
    if r.status == OK and c.status == OK:
        diffs: list[Difference] = []
        if r.run.exit_code != c.run.exit_code:
            diffs.append(Difference("<exit_code>", EXIT_CODE, r.run.exit_code, c.run.exit_code))
        diffs.extend(diff(r.normalized, c.normalized, policy))
        if exact_stderr and r.run.stderr != c.run.stderr:
            diffs.append(
                Difference(
                    "<stderr>",
                    STDERR,
                    r.run.stderr.decode("utf-8", "replace"),
                    c.run.stderr.decode("utf-8", "replace"),
                )
            )
        return (MATCH if not diffs else MISMATCH), diffs, notes
    if r.status == OK:
        return CANDIDATE_FAILED, [], [f"candidate: {c.reason}"]
    if c.status == OK:
        return REFERENCE_FAILED, [], [f"reference: {r.reason}"]
    same = r.reason == c.reason and r.run.stdout == c.run.stdout
    notes = [
        f"reference: {r.reason}",
        f"candidate: {c.reason}",
        "both programs failed identically; this is NOT evidence of correct behavior"
        if same
        else "both programs failed, differently",
    ]
    return BOTH_FAILED, [], notes


def run_case(case: Case, config: Config) -> CaseResult:
    ref: list[Observation] = []
    cand: list[Observation] = []
    for _ in range(config.policy.repeat):
        ref.append(observe(config.reference, case, config))
        cand.append(observe(config.candidate, case, config))
    outcome, diffs, notes = classify(ref, cand, config)
    return CaseResult(case, outcome, ref, cand, diffs, notes)


# --------------------------------------------------------------------------- identity


def sha256_file(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > _HASH_FILE_LIMIT:
            return None
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return "sha256:" + h.hexdigest()
    except OSError:
        return None


def program_identity(spec: ProgramSpec, base: Path) -> dict[str, Any]:
    """Hashes of the executable and of every argument that names an existing
    file, so replay can detect when the programs under test changed."""
    exe = Path(spec.argv[0])
    files: dict[str, str | None] = {}
    for arg in spec.argv[1:]:
        candidate = (spec.cwd / arg).resolve()
        if candidate.is_file():
            try:
                label = str(candidate.relative_to(base))
            except ValueError:
                label = str(candidate)
            files[label] = sha256_file(candidate)
    return {
        **spec.to_public_dict(),
        "resolved_executable": str(exe),
        "executable_sha256": sha256_file(exe),
        "argument_files_sha256": files,
    }


def environment_info() -> dict[str, Any]:
    return {
        "careo_version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


# --------------------------------------------------------------------------- report


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    counts = dict.fromkeys(OUTCOMES, 0)
    for r in results:
        counts[r.outcome] += 1
    total = len(results)
    if counts[EXECUTION_ERROR]:
        conclusion = "EXECUTION_ERRORS"
        text = "Some cases could not be executed; no conclusion about equivalence is possible."
    elif counts[MATCH] == total:
        conclusion = "NO_DIFFERENCES_OBSERVED"
        text = (
            f"All {total} cases matched under the configured comparison policy. "
            "This is bounded testing over the supplied inputs, not a proof of equivalence."
        )
    else:
        conclusion = "DIFFERENCES_FOUND"
        text = f"{total - counts[MATCH]} of {total} cases did not match."
    return {"total_cases": total, "outcomes": counts, "conclusion": conclusion, "summary": text}


def exit_code_for(summary: dict[str, Any]) -> int:
    if summary["conclusion"] == "EXECUTION_ERRORS":
        return EXIT_EXECUTION_ERROR
    if summary["conclusion"] == "DIFFERENCES_FOUND":
        return EXIT_DIFFERENCES
    return EXIT_ALL_MATCH


def run_comparison(
    config: Config, progress: Callable[[CaseResult], None] | None = None
) -> dict[str, Any]:
    started = datetime.now(UTC)
    results = []
    for case in config.cases:
        result = run_case(case, config)
        results.append(result)
        if progress:
            progress(result)
    finished = datetime.now(UTC)

    totals = {r.id: 0 for r in config.rules}
    for res in results:
        for obs in res.reference + res.candidate:
            for rule_id, n in obs.normalizations.items():
                totals[rule_id] += n
    warnings = [
        f"normalization rule {rule_id!r} never matched any output value; check its path"
        for rule_id, n in totals.items()
        if n == 0
    ]
    summary = summarize(results)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": "careo",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "config_path": str(config.path),
        "policy_fingerprint": config.policy_fingerprint(),
        "cases_fingerprint": config.cases_fingerprint(),
        "cases_source": config.cases_source,
        "environment": environment_info(),
        "reference": program_identity(config.reference, config.base_dir),
        "candidate": program_identity(config.candidate, config.base_dir),
        "limits": config.limits.to_dict(),
        "comparison_policy": config.policy.to_dict(),
        "normalization_rules": [r.to_dict() for r in config.rules],
        "normalization_totals": totals,
        "warnings": warnings,
        "result": {**summary, "exit_code": exit_code_for(summary)},
        "cases": [r.to_dict() for r in results],
    }
