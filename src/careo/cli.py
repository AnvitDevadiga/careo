"""Command-line interface: compare, validate, inspect, replay."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from careo import __version__
from careo.config import Config, ConfigError, load_config
from careo.engine import EXIT_EXECUTION_ERROR, EXIT_USAGE, MATCH, CaseResult, run_comparison
from careo.replay import replay
from careo.report import ReportError, load_report, write_reports

EXIT_REPLAY_DIVERGED = 1


def _err(msg: str) -> None:
    print(f"careo: error: {msg}", file=sys.stderr)


def _progress(result: CaseResult) -> None:
    print(f"  {result.outcome:<17} {result.case.id}", flush=True)


def _print_config(config: Config) -> None:
    for spec in (config.reference, config.candidate):
        print(f"{spec.role:<10} {spec.name}: {' '.join(spec.argv)}  (cwd: {spec.cwd_rel})")
    print(f"cases      {len(config.cases)} from {config.cases_source}")
    print(f"policy     {config.policy.to_dict()}")
    print(f"normalize  {len(config.rules)} rule(s)")
    for r in config.rules:
        print(f"  - {r.id}: {r.action} {r.path} ({r.reason})")


def cmd_validate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(f"config OK: {config.path}")
    _print_config(config)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(f"careo {__version__}: comparing {len(config.cases)} case(s)")
    report = run_comparison(config, None if args.quiet else _progress)
    json_path, md_path = write_reports(report, Path(args.output_dir))
    _print_summary(report)
    print(f"reports: {json_path}  {md_path}")
    return int(report["result"]["exit_code"])


def _print_summary(report: dict[str, Any]) -> None:
    res = report["result"]
    counts = ", ".join(f"{k}={v}" for k, v in res["outcomes"].items() if v)
    print(f"{res['conclusion']}: {counts}")
    print(res["summary"])
    for w in report.get("warnings", []):
        print(f"warning: {w}")


def cmd_inspect(args: argparse.Namespace) -> int:
    report = load_report(args.report)
    print(f"reference: {report['reference']['name']}  candidate: {report['candidate']['name']}")
    print(f"policy fingerprint: {report['policy_fingerprint']}")
    _print_summary(report)
    for case in report["cases"]:
        if args.case and case["id"] != args.case:
            continue
        if case["outcome"] == MATCH and not args.all and not args.case:
            continue
        print(f"\n[{case['outcome']}] {case['id']}  input={_short(case['input'])}")
        for note in case.get("notes", []):
            print(f"    note: {note}")
        for d in case.get("differences", []):
            ref = _short(d["reference"]) if "reference" in d else "(absent)"
            cand = _short(d["candidate"]) if "candidate" in d else "(absent)"
            print(f"    {d['path'] or '(root)'}: {d['kind']}: {ref} -> {cand}")
    return int(report["result"]["exit_code"])


def _short(value: Any, limit: int = 120) -> str:
    import json

    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


def cmd_replay(args: argparse.Namespace) -> int:
    report = load_report(args.report)
    result = replay(report, args.config)
    if result.drift:
        print("drift detected (results may legitimately differ):")
        for d in result.drift:
            print(f"  - {d}")
    else:
        print(
            "no drift detected in tracked identity (config, inputs, executables, argument "
            "files, identity_files, Careo/Python/platform); untracked code is not checked"
        )
    if args.output_dir:
        json_path, md_path = write_reports(result.new_report, Path(args.output_dir))
        print(f"replay reports: {json_path}  {md_path}")
    if result.reproduced:
        print(f"REPRODUCED: all {len(report['cases'])} case outcomes and differences match")
        return 0
    print("NOT REPRODUCED:")
    for change in result.case_changes:
        print(f"  - {change}")
    return EXIT_REPLAY_DIVERGED


# --------------------------------------------------------------------------- stateful


def _gen_overrides(spec: Any, args: argparse.Namespace) -> Any:
    from dataclasses import replace

    g = spec.generation
    changes = {}
    for name in ("seed", "max_scenarios", "max_steps", "max_operations"):
        v = getattr(args, name, None)
        if v is not None:
            changes[name] = v
    if getattr(args, "no_shrink", False):
        changes["shrink"] = False
    return spec.with_generation(replace(g, **changes)) if changes else spec


def cmd_scenario_validate(args: argparse.Namespace) -> int:
    from careo.scenarios.spec import load_spec

    spec = load_spec(args.spec)
    print(f"spec OK: {spec.path}")
    for svc in (spec.reference, spec.candidate):
        how = "managed: " + " ".join(svc.command) if svc.command else "external"
        print(f"{svc.role:<10} {svc.name}: {svc.base_url} ({how})")
    if spec.state:
        print(
            f"state      sqlite, reset={spec.state.reset}, compare={spec.state.compare}, "
            f"tables={[t.name for t in spec.state.tables]}"
        )
    print(f"operations {len(spec.operations)} from {spec.operations_source}:")
    for op in spec.operations:
        extra = f" -> produces {op.produces.entity}" if op.produces else ""
        needs = f" (needs {', '.join(op.bundle_slots())})" if op.bundle_slots() else ""
        print(f"  - {op.name}: {op.method} {op.path}{needs}{extra}")
    print(f"generation {spec.generation.to_dict()}")
    return 0


def cmd_scenario_run(args: argparse.Namespace) -> int:
    from careo.reporting.scenario import write
    from careo.scenarios.runner import export_regression, run
    from careo.scenarios.spec import load_spec

    spec = _gen_overrides(load_spec(args.spec), args)
    out = Path(args.output_dir)
    print(
        f"careo {__version__}: stateful run, seed {spec.generation.seed}, up to "
        f"{spec.generation.max_scenarios} scenarios x {spec.generation.max_steps} steps"
    )
    report = run(spec, out)
    jp, mp = write(report, out)
    st = report["stats"]
    print(f"{report['outcome']}: {report['summary']}")
    print(
        f"scenarios={st['scenarios_generated']} operations={st['operations_executed']} "
        f"resets={st['resets']} runtime={st['runtime_seconds']}s"
    )
    for n in report["notes"]:
        print(f"note: {n}")
    for w in report["warnings"]:
        print(f"warning: {w}")
    if report["minimized_sequence"]:
        o, m = report["original_sequence"], report["minimized_sequence"]
        v = report["verification"] or {}
        print(
            f"original sequence: {o['length']} operations; minimized: {m['length']} operations; "
            f"verified {v.get('reproduced', 0)}/{v.get('checks', 0)}"
        )
        for i, step in enumerate(m["steps"]):
            print(f"  {i}. {_short(step)}")
        if report["evidence"]:
            last = report["evidence"][-1]
            for d in last["differences"]:
                ref = _short(d["reference"]) if "reference" in d else "(absent)"
                cand = _short(d["candidate"]) if "candidate" in d else "(absent)"
                print(f"     {d['path']}: {d['kind']}: {ref} -> {cand}")
    for path in export_regression(report, spec, out):
        print(f"exported: {path}")
    print(f"reports: {jp}  {mp}")
    return int(report["exit_code"])


def cmd_scenario_replay(args: argparse.Namespace) -> int:
    from careo.scenarios.runner import replay_file

    res = replay_file(Path(args.scenario), args.spec)
    print(res.describe())
    if res.reproduced:
        print("REPRODUCED: replay matches the recorded result")
        return 0
    print(f"NOT REPRODUCED: recorded {res.expected!r}, now {res.signature!r}")
    return EXIT_REPLAY_DIVERGED


def cmd_scenario_minimize(args: argparse.Namespace) -> int:
    from careo.scenarios.runner import minimize_file

    result = minimize_file(
        Path(args.scenario), Path(args.output), args.spec, args.max_replays, args.checks
    )
    for k, v in result.items():
        print(f"{k}: {v}")
    return 0 if result.get("minimized") else 1


def cmd_bench(args: argparse.Namespace) -> int:
    from careo.evaluation.bench import run_suite

    return run_suite(Path(args.suite), Path(args.output_dir), args.seeds, args.quick)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="careo",
        description="Catch what the AI rewrite broke: differential testing of two programs.",
        epilog="Exit codes (compare/inspect): 0 all cases matched, 1 differences or failures, "
        "2 invalid config/usage, 3 execution error.",
    )
    parser.add_argument("--version", action="version", version=f"careo {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("compare", help="run both programs on every case and compare")
    p.add_argument("--config", default="careo.yaml")
    p.add_argument("--output-dir", default="careo-report")
    p.add_argument("--quiet", action="store_true", help="do not print per-case progress")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("validate", help="check a config without running anything")
    p.add_argument("--config", default="careo.yaml")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("inspect", help="summarize a report.json")
    p.add_argument("report")
    p.add_argument("--all", action="store_true", help="also list matching cases")
    p.add_argument("--case", help="show only this case id")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("replay", help="re-run a recorded comparison and check it reproduces")
    p.add_argument("--report", required=True)
    p.add_argument("--config", help="config to use instead of the recorded config_path")
    p.add_argument("--output-dir", help="also write the replay's report here")
    p.set_defaults(func=cmd_replay)
    sc = sub.add_parser("scenario", help="stateful differential testing of HTTP services")
    ssub = sc.add_subparsers(dest="scenario_command", required=True)
    p = ssub.add_parser("validate", help="check a scenario spec without running anything")
    p.add_argument("--spec", default="careo-scenarios.yaml")
    p.set_defaults(func=cmd_scenario_validate)
    p = ssub.add_parser("run", help="generate, compare, shrink, verify and export")
    p.add_argument("--spec", default="careo-scenarios.yaml")
    p.add_argument("--output-dir", default="careo-scenario-report")
    p.add_argument("--seed", type=int)
    p.add_argument("--max-scenarios", type=int)
    p.add_argument("--max-steps", type=int)
    p.add_argument("--max-operations", type=int)
    p.add_argument("--no-shrink", action="store_true")
    p.set_defaults(func=cmd_scenario_run)
    p = ssub.add_parser("replay", help="replay a scenario file from a clean verified state")
    p.add_argument("--scenario", required=True)
    p.add_argument("--spec")
    p.set_defaults(func=cmd_scenario_replay)
    p = ssub.add_parser("minimize", help="bounded reduction of a recorded failing scenario")
    p.add_argument("--scenario", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--spec")
    p.add_argument("--max-replays", type=int, default=200)
    p.add_argument("--checks", type=int, default=3)
    p.set_defaults(func=cmd_scenario_minimize)

    p = sub.add_parser("bench", help="run the seeded-regression evaluation suite")
    p.add_argument("--suite", required=True)
    p.add_argument("--output-dir", default="careo-bench")
    p.add_argument("--seeds", type=int, default=5)
    p.add_argument("--quick", action="store_true", help="small budgets (CI validation)")
    p.set_defaults(func=cmd_bench)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, ReportError) as exc:
        _err(str(exc))
        return EXIT_USAGE
    except OSError as exc:
        _err(f"{type(exc).__name__}: {exc}")
        return EXIT_USAGE
    except Exception as exc:  # never let an internal failure look like a result
        _err(f"internal error: {type(exc).__name__}: {exc}")
        return EXIT_EXECUTION_ERROR
    except KeyboardInterrupt:
        _err("interrupted")
        return 130
