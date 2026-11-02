"""Orchestrates a stateful differential run, verification, replay and export."""

from __future__ import annotations

import json
import signal
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from careo.config import ConfigError
from careo.engine import _hash_tree, environment_info, sha256_file
from careo.scenarios.machine import Recorder, search
from careo.scenarios.session import (
    FINDINGS,
    STEP_MATCH,
    SequenceResult,
    Session,
    SetupError,
)
from careo.scenarios.spec import ScenarioSpec, ServiceSpec, load_spec

SCENARIO_REPORT_SCHEMA = 1
SCENARIO_FILE_SCHEMA = 1

# run outcomes
NO_DIFFERENCES_OBSERVED = "NO_DIFFERENCES_OBSERVED"
DIFFERENCE_CONFIRMED = "DIFFERENCE_CONFIRMED"
DIFFERENCE_FLAKY = "DIFFERENCE_FLAKY"
RESET_FAILED = "RESET_FAILED"
TIMEOUT = "TIMEOUT"
EXECUTION_ERROR = "EXECUTION_ERROR"
INCONCLUSIVE = "INCONCLUSIVE"

MAX_DDMIN_REPLAYS = 150

EXIT_CODES = {
    NO_DIFFERENCES_OBSERVED: 0,
    DIFFERENCE_CONFIRMED: 1,
    DIFFERENCE_FLAKY: 1,
    INCONCLUSIVE: 1,
    TIMEOUT: 1,
    RESET_FAILED: 3,
    EXECUTION_ERROR: 3,
}


@contextmanager
def interruptible() -> Iterator[None]:
    """Turn SIGTERM into SystemExit so ``finally`` blocks stop managed services."""

    def handler(signum: int, frame: Any) -> None:
        raise SystemExit(128 + signum)

    try:
        previous = signal.signal(signal.SIGTERM, handler)
    except ValueError:  # not in the main thread
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def failure_signature(seq: SequenceResult) -> str | None:
    """Index-independent signature: operation, verdict and difference locations."""
    f = seq.failing
    return f.signature() if f else None


def service_identity(svc: ServiceSpec, base: Path) -> dict[str, Any]:
    files: dict[str, str | None] = {}
    for arg in (svc.command or ())[1:]:
        p = (svc.cwd / arg).resolve()
        if p.is_file():
            try:
                label = str(p.relative_to(base))
            except ValueError:
                label = str(p)
            files[label] = sha256_file(p)
    for rel in svc.identity_files:
        files[rel] = _hash_tree(base / rel)
    return {**svc.to_public_dict(), "files_sha256": files}


@dataclass
class Verification:
    checks: int
    reproduced: int
    signatures: list[str | None] = field(default_factory=list)
    final: SequenceResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"checks": self.checks, "reproduced": self.reproduced, "signatures": self.signatures}


def verify(
    session: Session, steps: list[dict[str, Any]], expected: str | None, checks: int
) -> Verification:
    """Replay ``steps`` from clean verified resets and count exact reproductions."""
    v = Verification(checks, 0)
    for _ in range(checks):
        res = session.run_sequence(steps)
        sig = failure_signature(res) if res.verdict in FINDINGS else None
        v.signatures.append(sig)
        if expected is not None and sig == expected:
            v.reproduced += 1
        v.final = res
    return v


def minimize_sequence(
    session: Session, steps: list[dict[str, Any]], expected: str, max_replays: int = 200
) -> tuple[list[dict[str, Any]], int]:
    """Bounded delta-debugging style reduction for recorded sequences.

    A candidate reduction is kept only if replaying it from a clean, verified
    reset reproduces the same failure signature. Result is 1-minimal only if the
    replay budget was not exhausted; it is never claimed to be globally smallest.
    """
    current = list(steps)
    replays = 0
    chunk = max(1, len(current) // 2)
    while chunk >= 1 and replays < max_replays:
        changed = False
        i = 0
        while i < len(current) and replays < max_replays:
            trial = current[:i] + current[i + chunk :]
            if trial:
                replays += 1
                res = session.run_sequence(trial)
                if res.verdict in FINDINGS and failure_signature(res) == expected:
                    current = trial
                    changed = True
                    continue
            i += chunk
        if not changed:
            chunk //= 2
    return current, replays


def _steps_dicts(results: SequenceResult | None) -> list[dict[str, Any]]:
    return [r.to_dict() for r in results.steps] if results else []


def _id_mapping(session: Session) -> dict[str, Any]:
    return {
        "reference": {k: v for k, v in session.ref.forward.items()},
        "candidate": {k: v for k, v in session.cand.forward.items()},
    }


def run(spec: ScenarioSpec, out_dir: Path) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    t0 = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    session = Session(spec, out_dir / "run")
    rec = Recorder()
    flaky = None
    verification: Verification | None = None
    minimized: list[dict[str, Any]] | None = None
    ddmin_replays = 0
    notes: list[str] = []
    with interruptible():
        try:
            session.prepare()
        except (SetupError, ConfigError) as exc:
            return _report(
                spec,
                session,
                rec,
                EXECUTION_ERROR,
                [f"setup failed: {exc}"],
                None,
                None,
                started_at,
                t0,
                None,
                0,
            )
        try:
            flaky = search(spec, session, rec)
            if rec.last_failure is not None and rec.abort_kind != "RESET_FAILED":
                expected_sig = None
                if rec.last_failure_results:
                    expected_sig = rec.last_failure_results[-1].signature()
                minimized = rec.last_failure
                if spec.generation.shrink and expected_sig is not None and len(minimized) > 1:
                    minimized, ddmin_replays = minimize_sequence(
                        session, minimized, expected_sig, max_replays=MAX_DDMIN_REPLAYS
                    )
                verification = verify(
                    session, minimized, expected_sig, spec.generation.replay_checks
                )
        finally:
            session.teardown()

    if rec.abort_kind == "RESET_FAILED":
        outcome = RESET_FAILED
        notes.append(f"reset failed: {rec.abort}. No behavioral verdict is possible.")
        if rec.last_failure:
            notes.append("a difference was observed before the reset failure; it is NOT verified")
    elif verification is not None:
        if verification.reproduced == verification.checks and flaky is None:
            outcome = DIFFERENCE_CONFIRMED
        else:
            outcome = DIFFERENCE_FLAKY
            notes.append(
                f"minimized sequence reproduced {verification.reproduced}/{verification.checks} "
                "times from clean resets; treat as nondeterministic"
            )
        if rec.abort:
            notes.append(f"search also stopped on: {rec.abort}")
    elif rec.abort_kind == "BOTH_TIMEOUT":
        outcome = TIMEOUT
        notes.append(f"both services timed out: {rec.abort}. This is not equivalence.")
    elif rec.abort:
        outcome = EXECUTION_ERROR
        notes.append(rec.abort)
    elif flaky and rec.first_failure is not None:
        outcome = DIFFERENCE_FLAKY
        notes.append("Hypothesis could not reproduce a failure while shrinking (flaky)")
    elif flaky:
        # Hypothesis-internal flakiness (e.g. generation changed once the operation budget
        # ran out) with no observed behavioral difference is not a finding.
        outcome = NO_DIFFERENCES_OBSERVED if session.operations_executed else INCONCLUSIVE
        notes.append(
            "Hypothesis reported inconsistent generation without any observed "
            "behavioral difference (typically caused by the operation budget)"
        )
    elif session.operations_executed == 0:
        outcome = INCONCLUSIVE
        notes.append("no operations were executed")
    else:
        outcome = NO_DIFFERENCES_OBSERVED
    return _report(
        spec,
        session,
        rec,
        outcome,
        notes,
        verification,
        flaky,
        started_at,
        t0,
        minimized,
        ddmin_replays,
    )


def _report(
    spec: ScenarioSpec,
    session: Session,
    rec: Recorder,
    outcome: str,
    notes: list[str],
    verification: Verification | None,
    flaky: str | None,
    started_at: datetime,
    t0: float,
    minimized: list[dict[str, Any]] | None,
    ddmin_replays: int,
) -> dict[str, Any]:
    iso = session.isolation() if session.sides else {}
    warnings = []
    if (
        iso
        and not iso["database_reset_and_verified"]
        and not iso["processes_restarted_each_scenario"]
    ):
        warnings.append(
            "initial state was NOT reset or verified (unverified_initial_state); "
            "scenarios may not be independent"
        )
    elif iso and not iso["in_memory_state_reset"]:
        warnings.append(
            "database state was reset and verified, but service processes were not "
            "restarted: in-memory state can leak across scenarios (use reset: restart)"
        )
    warnings += [
        f"normalization rule {k!r} never changed a value; check its path"
        for k, v in session.normalization_totals.items()
        if v == 0 and session.operations_executed
    ]
    if rec.budget_exhausted:
        notes.append(f"operation budget of {spec.generation.max_operations} reached")
    summary = {
        NO_DIFFERENCES_OBSERVED: (
            f"No behavioral difference in {session.operations_executed} operations over "
            f"{rec.runs} generated scenarios. Bounded testing, not a proof of equivalence."
        ),
        DIFFERENCE_CONFIRMED: "A behavioral difference was found and its minimized sequence "
        "reproduced on every verification replay after a reset (see isolation).",
        DIFFERENCE_FLAKY: "A difference was observed but did not reproduce reliably.",
        RESET_FAILED: "Equivalent initial state could not be established; results are void.",
        TIMEOUT: "Both services timed out; no conclusion.",
        EXECUTION_ERROR: "Services could not be exercised; no conclusion.",
        INCONCLUSIVE: "Nothing was executed; no conclusion.",
    }[outcome]
    return {
        "schema": "careo.scenario-report",
        "schema_version": SCENARIO_REPORT_SCHEMA,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "spec_path": str(spec.path),
        "spec_fingerprint": spec.fingerprint(),
        "generation": spec.generation.to_dict(),
        "environment": environment_info(),
        "reference": service_identity(spec.reference, spec.base_dir),
        "candidate": service_identity(spec.candidate, spec.base_dir),
        "state": spec.state.to_dict() if spec.state else None,
        "comparison_policy": spec.policy.to_dict()
        | {"compare_headers": list(spec.compare_headers)},
        "normalization_rules": [r.to_dict() for r in spec.rules],
        "normalization_totals": session.normalization_totals,
        "isolation": iso,
        "outcome": outcome,
        "exit_code": EXIT_CODES[outcome],
        "summary": summary,
        "notes": notes,
        "warnings": warnings,
        "stats": {
            "scenarios_generated": rec.runs,
            "operations_executed": session.operations_executed,
            "search_operations": (
                rec.search_operations
                if rec.search_operations is not None
                else session.operations_executed
            ),
            "requests_sent": session.requests_sent,
            "resets": session.resets,
            "id_translations": session.id_translations,
            "time_to_first_difference_seconds": rec.time_to_first_failure,
            "operations_to_first_difference": rec.ops_to_first_failure,
            "runtime_seconds": round(time.monotonic() - t0, 3),
            "hypothesis_flaky": bool(flaky),
        },
        "original_sequence": (
            {"length": len(rec.first_failure), "steps": rec.first_failure}
            if rec.first_failure is not None
            else None
        ),
        "minimized_sequence": (
            {
                "length": len(minimized),
                "steps": minimized,
                "method": (
                    "hypothesis-shrink + bounded delta debugging"
                    if spec.generation.shrink
                    else "none (shrinking disabled)"
                ),
                "hypothesis_shrunk_length": len(rec.last_failure or []),
                "ddmin_replays": ddmin_replays,
                "claim": "smaller reproducing sequence; not proven globally minimal",
            }
            if minimized is not None
            else None
        ),
        "verification": verification.to_dict() if verification else None,
        "evidence": _steps_dicts(verification.final if verification else None),
        "id_mapping": _id_mapping(session),
    }


# --------------------------------------------------------------------------- files


def scenario_file(report: dict[str, Any], spec: ScenarioSpec, out: Path) -> dict[str, Any]:
    seq = report["minimized_sequence"]
    ver = report["verification"] or {}
    expected = (ver.get("signatures") or [None])[0]
    try:
        rel = str(Path(spec.path).relative_to(out.resolve().parent))
    except ValueError:
        rel = None
    return {
        "schema": "careo.scenario",
        "schema_version": SCENARIO_FILE_SCHEMA,
        "spec_path": str(spec.path),
        "spec_relative": rel,
        "spec_fingerprint": spec.fingerprint(),
        "steps": seq["steps"],
        "recorded_outcome": report["outcome"],
        "expected_failure_signature": expected,
    }


_TEST_TEMPLATE = '''"""Regression test exported by Careo.

It replays a minimized scenario against the configured reference and candidate
from a clean, verified state. It FAILS while the behavioral difference exists
and passes once the candidate matches the reference again.
"""

from pathlib import Path

from careo.scenarios.runner import replay_file


def test_careo_regression() -> None:
    result = replay_file(Path(__file__).with_name("{name}"))
    assert result.verdict == "MATCH", result.describe()
'''


def export_regression(report: dict[str, Any], spec: ScenarioSpec, out_dir: Path) -> list[Path]:
    if report["minimized_sequence"] is None or report["outcome"] != DIFFERENCE_CONFIRMED:
        return []
    data = scenario_file(report, spec, out_dir / "regression.json")
    jp = out_dir / "regression.json"
    jp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tp = out_dir / "test_careo_regression.py"
    tp.write_text(_TEST_TEMPLATE.replace("{name}", jp.name), encoding="utf-8")
    return [jp, tp]


def load_scenario_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read scenario file {path}: {exc}") from None
    if (
        not isinstance(data, dict)
        or data.get("schema") != "careo.scenario"
        or data.get("schema_version") != SCENARIO_FILE_SCHEMA
    ):
        raise ConfigError(f"{path} is not a Careo scenario file (schema version 1)")
    steps = data.get("steps")
    if not isinstance(steps, list) or not all(isinstance(s, dict) and "op" in s for s in steps):
        raise ConfigError(f"{path}: steps are malformed")
    return data


@dataclass
class ReplayOutcome:
    verdict: str
    signature: str | None
    expected: str | None
    drift: list[str]
    sequence: SequenceResult

    @property
    def reproduced(self) -> bool:
        return self.signature == self.expected

    def describe(self) -> str:
        lines = [f"verdict={self.verdict}"]
        lines += [f"drift: {d}" for d in self.drift]
        f = self.sequence.failing
        if f:
            lines.append(f"step {f.index} {f.step['op']}: {f.verdict}")
            lines += [
                f"  {d.path}: {d.kind} {d.to_dict().get('reference')!r} -> "
                f"{d.to_dict().get('candidate')!r}"
                for d in f.differences
            ]
            lines += [f"  note: {n}" for n in f.notes]
        if self.sequence.reset_error:
            lines.append(f"reset: {self.sequence.reset_error}")
        return "\n".join(lines)


def _spec_for(
    data: dict[str, Any], scenario_path: Path, spec_path: str | Path | None
) -> ScenarioSpec:
    if spec_path is not None:
        return load_spec(spec_path)
    rel = data.get("spec_relative")
    if rel:
        p = scenario_path.resolve().parent / rel
        if p.exists():
            return load_spec(p)
    return load_spec(data["spec_path"])


def replay_file(
    path: Path, spec_path: str | Path | None = None, out_dir: Path | None = None
) -> ReplayOutcome:
    data = load_scenario_file(path)
    spec = _spec_for(data, path, spec_path)
    drift = []
    if spec.fingerprint() != data.get("spec_fingerprint"):
        drift.append(
            "scenario spec (services, state, operations or policy) changed since recording"
        )
    for op in {s["op"] for s in data["steps"]}:
        try:
            spec.op(op)
        except KeyError:
            raise ConfigError(
                f"scenario uses operation {op!r}, which the spec no longer defines"
            ) from None
    session = Session(spec, (out_dir or path.resolve().parent / ".careo-replay") / "run")
    with interruptible():
        try:
            session.prepare()
        except SetupError as exc:
            raise ConfigError(f"setup failed: {exc}") from None
        try:
            seq = session.run_sequence(data["steps"])
        finally:
            session.teardown()
    sig = failure_signature(seq) if seq.verdict in FINDINGS else None
    verdict = seq.verdict if seq.verdict != STEP_MATCH else "MATCH"
    return ReplayOutcome(verdict, sig, data.get("expected_failure_signature"), drift, seq)


def minimize_file(
    path: Path,
    out: Path,
    spec_path: str | Path | None = None,
    max_replays: int = 200,
    checks: int = 3,
) -> dict[str, Any]:
    data = load_scenario_file(path)
    spec = _spec_for(data, path, spec_path)
    session = Session(spec, out.resolve().parent / ".careo-minimize" / "run")
    with interruptible():
        try:
            session.prepare()
        except SetupError as exc:
            raise ConfigError(f"setup failed: {exc}") from None
        try:
            first = session.run_sequence(data["steps"])
            expected = failure_signature(first) if first.verdict in FINDINGS else None
            if expected is None:
                return {
                    "minimized": False,
                    "reason": f"sequence does not fail (verdict {first.verdict})",
                    "original_length": len(data["steps"]),
                }
            steps, replays = minimize_sequence(session, data["steps"], expected, max_replays)
            v = verify(session, steps, expected, checks)
        finally:
            session.teardown()
    result = {
        "minimized": v.reproduced == checks,
        "original_length": len(data["steps"]),
        "minimized_length": len(steps),
        "replays_used": replays,
        "budget_exhausted": replays >= max_replays,
        "verification": v.to_dict(),
    }
    if v.reproduced == checks:
        new = dict(data)
        new["steps"] = steps
        new["expected_failure_signature"] = expected
        out.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        result["output"] = str(out)
    return result
