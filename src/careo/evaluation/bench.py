"""Seeded-regression evaluation of four testing methods on the same fixtures.

Methods
  A  example unit tests  - the example project's own happy-path checks, run
                           against the candidate alone (deterministic, 1 run/case)
  B  single-operation     - random operations, each from a clean state (max_steps=1)
  C  stateful             - generated multi-step sequences, no shrinking
  D  stateful + shrinking - C plus Hypothesis shrinking and delta debugging

B, C and D share one operation budget (``budget.operations``), enforced by
counting executed operations. Each (case, seed) run starts both services from
scratch. Raw per-run records are written alongside the summary.

These are seeded fixtures. Results say nothing about effectiveness on real
migrations.
"""

from __future__ import annotations

import importlib.util
import json
import statistics
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from careo import __version__
from careo.adapters.sqlite import SqliteState
from careo.config import ConfigError, _check_keys, _int, _str
from careo.engine import environment_info
from careo.execution.service import ManagedService, ServiceError, free_port
from careo.scenarios.runner import DIFFERENCE_CONFIRMED, NO_DIFFERENCES_OBSERVED, run
from careo.scenarios.spec import Generation, ScenarioSpec, load_spec

METHODS = ("A_unit_tests", "B_single_operation", "C_stateful", "D_stateful_shrinking")
INVALID_OUTCOMES = {"RESET_FAILED", "EXECUTION_ERROR"}


def load_suite(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read suite {path}: {exc}") from None
    d = _check_keys(
        raw, "suite", {"base_spec", "unit_tests", "candidate_script", "budget", "cases"}, set()
    )
    b = _check_keys(
        d["budget"], "suite.budget", {"operations", "max_steps", "replay_checks"}, set()
    )
    base = path.resolve().parent
    cases = []
    for i, c in enumerate(d["cases"]):
        cd = _check_keys(c, f"suite.cases[{i}]", {"name", "defect", "seeded"}, set())
        if not isinstance(cd["seeded"], bool):
            raise ConfigError(f"suite.cases[{i}].seeded must be true or false")
        defect = cd["defect"]
        if defect is not None:
            _str(defect, f"suite.cases[{i}].defect")
        cases.append({"name": _str(cd["name"], "name"), "defect": defect, "seeded": cd["seeded"]})
    return {
        "path": path.resolve(),
        "base_spec": (base / _str(d["base_spec"], "base_spec")).resolve(),
        "unit_tests": (base / _str(d["unit_tests"], "unit_tests")).resolve(),
        "candidate_script": (base / _str(d["candidate_script"], "candidate_script")).resolve(),
        "operations": _int(b["operations"], "budget.operations", 1),
        "max_steps": _int(b["max_steps"], "budget.max_steps", 1),
        "replay_checks": _int(b["replay_checks"], "budget.replay_checks", 1, 50),
        "cases": cases,
    }


def variant_spec(base: ScenarioSpec, suite: dict[str, Any], defect: str | None) -> ScenarioSpec:
    if defect is None:  # the reference compared with a second copy of itself
        cand = replace(
            base.candidate,
            name="reference-copy",
            command=base.reference.command,
            cwd=base.reference.cwd,
        )
    else:
        cmd = (
            *(base.candidate.command or ())[:1],
            str(suite["candidate_script"]),
            "--port",
            "{port}",
            "--db",
            "{db}",
            "--defect",
            defect,
        )
        cand = replace(base.candidate, name=f"rewrite[{defect}]", command=cmd)
    return replace(base, candidate=cand)


def _load_checks(path: Path) -> tuple[list[Any], Any]:
    spec = importlib.util.spec_from_file_location("careo_bench_unit_tests", path)
    if spec is None or spec.loader is None:
        raise ConfigError(f"cannot import unit tests from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.CHECKS), module


def method_a(spec: ScenarioSpec, suite: dict[str, Any], work: Path) -> dict[str, Any]:
    """Run the example's happy-path checks against the candidate service alone."""
    checks, module = _load_checks(suite["unit_tests"])
    calls = 0
    original_call = module.call

    def counting_call(*a: Any, **k: Any) -> Any:
        nonlocal calls
        calls += 1
        return original_call(*a, **k)

    module.call = counting_call
    st = spec.state
    assert st is not None
    db = (work / "unit.db").resolve()
    SqliteState(db, st.schema_sql, st.fixture_sql, st.tables).prepare()
    port = free_port()
    from careo.scenarios.session import resolve_argv

    cand = spec.candidate
    svc = ManagedService(
        cand.name,
        resolve_argv(cand, spec.base_dir, {"port": str(port), "db": str(db)}),
        cand.cwd,
        cand.child_env({}),
        cand.endpoint(port),
        cand.ready_path,
        cand.ready_timeout,
        work / "unit.log",
    )
    t0 = time.monotonic()
    failures = []
    try:
        svc.start()
        for check in checks:
            try:
                check(svc.endpoint.url)
            except Exception as exc:  # an erroring test is a failing test
                failures.append(f"{check.__name__}: {type(exc).__name__}: {exc!s:.200}")
    except ServiceError as exc:
        return {"outcome": "EXECUTION_ERROR", "error": str(exc)}
    finally:
        svc.stop()
    return {
        "outcome": DIFFERENCE_CONFIRMED if failures else NO_DIFFERENCES_OBSERVED,
        "detected": bool(failures),
        "tests_run": len(checks),
        "failures": failures,
        "operations_executed": calls,
        "runtime_seconds": round(time.monotonic() - t0, 3),
    }


def method_generated(spec: ScenarioSpec, gen: Generation, work: Path) -> dict[str, Any]:
    report = run(spec.with_generation(gen), work)
    (work / "scenario-report.json").write_text(json.dumps(report, indent=2) + "\n")
    st = report["stats"]
    ver = report["verification"] or {}
    return {
        "outcome": report["outcome"],
        "detected": report["outcome"] == DIFFERENCE_CONFIRMED,
        "operations_executed": st["operations_executed"],
        "search_operations": st["search_operations"],
        "scenarios_generated": st["scenarios_generated"],
        "operations_to_first_difference": st["operations_to_first_difference"],
        "time_to_first_difference_seconds": st["time_to_first_difference_seconds"],
        "sequence_length_before_shrink": (report["original_sequence"] or {}).get("length"),
        "sequence_length_after_shrink": (report["minimized_sequence"] or {}).get("length"),
        "verification_reproduced": ver.get("reproduced"),
        "verification_checks": ver.get("checks"),
        "failure_signature": (ver.get("signatures") or [None])[0],
        "minimized_steps": (report["minimized_sequence"] or {}).get("steps"),
        "runtime_seconds": st["runtime_seconds"],
        "notes": report["notes"],
    }


def _rate(n: int, d: int) -> dict[str, Any]:
    return {"numerator": n, "denominator": d, "rate": round(n / d, 3) if d else None}


def _med(values: list[Any]) -> Any:
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 3) if vals else None


def summarize(records: list[dict[str, Any]], suite: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in METHODS:
        rs = [r for r in records if r["method"] == m]
        seeded = [r for r in rs if r["seeded"]]
        clean = [r for r in rs if not r["seeded"]]
        valid_seeded = [r for r in seeded if r["outcome"] not in INVALID_OUTCOMES]
        detected = [r for r in valid_seeded if r.get("detected")]
        per_case = {}
        for c in suite["cases"]:
            cr = [r for r in rs if r["case"] == c["name"]]
            per_case[c["name"]] = _rate(sum(1 for r in cr if r.get("detected")), len(cr))
        replayed = [r for r in detected if r.get("verification_checks")]
        out[m] = {
            "runs": len(rs),
            "invalid_runs": sum(1 for r in rs if r["outcome"] in INVALID_OUTCOMES),
            "seeded_detection": _rate(len(detected), len(seeded)),
            "seeded_cases_detected_at_least_once": _rate(
                len({r["case"] for r in detected}), len({r["case"] for r in seeded})
            ),
            "false_alarms_on_clean": _rate(
                sum(1 for r in clean if r["outcome"] != NO_DIFFERENCES_OBSERVED), len(clean)
            ),
            "per_case_detection": per_case,
            "median_operations_to_first_difference": _med(
                [r.get("operations_to_first_difference") for r in detected]
            ),
            "median_time_to_first_difference_seconds": _med(
                [r.get("time_to_first_difference_seconds") for r in detected]
            ),
            "total_operations_executed": sum(r.get("operations_executed") or 0 for r in rs),
            "invalid_run_ids": [
                f"{r['case']}/{r['method']}/seed{r['seed']}"
                for r in rs
                if r["outcome"] in INVALID_OUTCOMES
            ],
            "total_search_operations": sum(
                r.get("search_operations", r.get("operations_executed")) or 0 for r in rs
            ),
            "median_sequence_length_before_shrink": _med(
                [r.get("sequence_length_before_shrink") for r in detected]
            ),
            "median_sequence_length_after_shrink": _med(
                [r.get("sequence_length_after_shrink") for r in detected]
            ),
            "replay_success": _rate(
                sum(
                    1 for r in replayed if r["verification_reproduced"] == r["verification_checks"]
                ),
                len(replayed),
            ),
            "total_runtime_seconds": round(sum(r.get("runtime_seconds") or 0 for r in rs), 3),
        }
    return out


def render_markdown(summary: dict[str, Any], meta: dict[str, Any]) -> str:
    lines = [
        "# Careo benchmark results",
        "",
        "All regressions are **deliberately seeded** into fixtures. These numbers describe "
        "Careo on these fixtures only and do not predict effectiveness on real migrations.",
        "",
        f"- Careo {meta['careo_version']} · commit `{meta.get('git_commit')}` · "
        f"{meta['finished_at']}",
        f"- Seeds per case: {meta['seeds']} (method A is deterministic: 1 run per case) · "
        f"operation budget per run (B, C, D): {meta['budget_operations']} · "
        f"max steps (C, D): {meta['max_steps']}",
        "",
        "| Method | Seeded detection | Cases found | False alarms (clean) | Median ops to 1st | "
        "Median len before → after shrink | Replay success | Search ops (budgeted) | "
        "Total ops incl. shrink+verify | Runtime s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    def r(x: dict[str, Any]) -> str:
        return f"{x['numerator']}/{x['denominator']}"

    for m, s in summary.items():
        lines.append(
            f"| {m} | {r(s['seeded_detection'])} | {r(s['seeded_cases_detected_at_least_once'])} | "
            f"{r(s['false_alarms_on_clean'])} | {s['median_operations_to_first_difference']} | "
            f"{s['median_sequence_length_before_shrink']} → "
            f"{s['median_sequence_length_after_shrink']} | "
            f"{r(s['replay_success'])} | {s['total_search_operations']} | "
            f"{s['total_operations_executed']} | {s['total_runtime_seconds']} |"
        )
    lines += [
        "",
        "## Detection per case (detected runs / runs)",
        "",
        "| Case | " + " | ".join(summary) + " |",
        "|---|" + "---|" * len(summary),
    ]
    cases = next(iter(summary.values()))["per_case_detection"]
    for c in cases:
        lines.append(
            f"| {c} | " + " | ".join(r(summary[m]["per_case_detection"][c]) for m in summary) + " |"
        )
    lines += [
        "",
        "How to read this:",
        "",
        "- B, C and D share the same budget of *search* operations (operations spent "
        "looking for the first difference). Shrinking and verification replays are extra "
        "work, shown in the total column.",
        "- A is NOT budget-equal: it is the example project's fixed happy-path suite "
        "(its HTTP calls are counted as operations).",
        "- D runs the same seeded search as C, so it detects exactly what C detects by "
        "construction. What D adds is shorter, verified counterexamples, at extra cost.",
        "- Runs that could not reset or execute are counted in denominators as not "
        "detected and are listed by id in summary.json (`invalid_run_ids`).",
        "",
    ]
    return "\n".join(lines)


def _git_commit(cwd: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - optional provenance only
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError:
        return None
    return out.stdout.strip() or None


def run_suite(path: Path, out: Path, seeds: int, quick: bool) -> int:
    suite = load_suite(path)
    if quick:
        suite["operations"] = min(suite["operations"], 150)
        seeds = min(seeds, 1)
    base = load_spec(suite["base_spec"])
    out.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    raw_path = out / "raw-records.jsonl"
    raw_path.write_text("")
    started = datetime.now(UTC)
    for case in suite["cases"]:
        spec = variant_spec(base, suite, case["defect"])
        jobs: list[tuple[str, int, Any]] = [("A_unit_tests", 0, None)]
        for s in range(seeds):
            seed = 1000 + s
            common = {
                "seed": seed,
                "replay_checks": suite["replay_checks"],
                "max_operations": suite["operations"],
                "max_scenarios": suite["operations"],
            }
            jobs += [
                ("B_single_operation", seed, Generation(max_steps=1, shrink=True, **common)),
                (
                    "C_stateful",
                    seed,
                    Generation(max_steps=suite["max_steps"], shrink=False, **common),
                ),
                (
                    "D_stateful_shrinking",
                    seed,
                    Generation(max_steps=suite["max_steps"], shrink=True, **common),
                ),
            ]
        for method, seed, gen in jobs:
            work = out / "runs" / case["name"] / f"{method}-seed{seed}"
            work.mkdir(parents=True, exist_ok=True)
            result = (
                method_a(spec, suite, work) if gen is None else method_generated(spec, gen, work)
            )
            rec = {
                "case": case["name"],
                "defect": case["defect"],
                "seeded": case["seeded"],
                "method": method,
                "seed": seed if gen else None,
                **result,
            }
            records.append(rec)
            with raw_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(
                f"{case['name']:<18} {method:<21} seed={rec['seed']!s:<5} {rec['outcome']:<24} "
                f"ops={rec.get('operations_executed')}",
                flush=True,
            )
    summary = summarize(records, suite)
    meta = {
        "careo_version": __version__,
        "git_commit": _git_commit(path.resolve().parent),
        "environment": environment_info(),
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "seeds": seeds,
        "quick": quick,
        "budget_operations": suite["operations"],
        "max_steps": suite["max_steps"],
        "replay_checks": suite["replay_checks"],
        "suite": str(path),
        "spec_fingerprints": {
            c["name"]: variant_spec(base, suite, c["defect"]).fingerprint() for c in suite["cases"]
        },
        "python": sys.version.split()[0],
    }
    (out / "summary.json").write_text(
        json.dumps({"meta": meta, "summary": summary}, indent=2) + "\n"
    )
    (out / "summary.md").write_text(render_markdown(summary, meta))
    print(render_markdown(summary, meta))
    invalid = sum(s["invalid_runs"] for s in summary.values())
    if invalid:
        print(f"benchmark INVALID: {invalid} run(s) failed to reset or execute", file=sys.stderr)
        return 3
    return 0
