"""Command-line interface: compare, validate, inspect, replay."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from careo import __version__
from careo.config import Config, ConfigError, load_config
from careo.engine import EXIT_USAGE, MATCH, CaseResult, run_comparison
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
        print("no drift: configuration, inputs, program files and environment match")
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
    except KeyboardInterrupt:
        _err("interrupted")
        return 130
