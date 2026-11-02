"""Writing, loading and rendering reports."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from careo.engine import MATCH, OUTCOMES, REPORT_SCHEMA_VERSION


class ReportError(Exception):
    pass


_REQUIRED = {
    "schema_version",
    "config_path",
    "policy_fingerprint",
    "cases_fingerprint",
    "reference",
    "candidate",
    "comparison_policy",
    "normalization_rules",
    "result",
    "cases",
}


def write_reports(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"
    _atomic_write(json_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    _atomic_write(md_path, render_markdown(report))
    return json_path, md_path


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_report(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ReportError(f"report not found: {path}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportError(f"cannot read report {path}: {exc}") from None
    if not isinstance(data, dict):
        raise ReportError("report must be a JSON object")
    if data.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ReportError(
            f"unsupported report schema_version {data.get('schema_version')!r} "
            f"(this Careo reads version {REPORT_SCHEMA_VERSION})"
        )
    missing = _REQUIRED - set(data)
    if missing:
        raise ReportError(f"report is missing keys: {', '.join(sorted(missing))}")
    cases = data["cases"]
    if not isinstance(cases, list) or not all(
        isinstance(c, dict)
        and isinstance(c.get("id"), str)
        and "input" in c
        and c.get("outcome") in OUTCOMES
        for c in cases
    ):
        raise ReportError("report.cases is malformed")
    return data


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _code(value: Any, limit: int = 2000) -> str:
    text = json.dumps(value, indent=2, ensure_ascii=False)
    if len(text) > limit:
        text = text[:limit] + "\n… (truncated; see report.json)"
    return "```json\n" + text.replace("```", "`\u200b``") + "\n```\n"


def render_markdown(report: dict[str, Any]) -> str:
    res = report["result"]
    ref, cand = report["reference"], report["candidate"]
    lines = [
        "# Careo report",
        "",
        f"**Conclusion: {res['conclusion']}** — {res['summary']}",
        "",
        "| | Reference | Candidate |",
        "|---|---|---|",
        f"| Name | {_md_escape(ref['name'])} | {_md_escape(cand['name'])} |",
        f"| Command | `{_md_escape(' '.join(ref['command']))}` | "
        f"`{_md_escape(' '.join(cand['command']))}` |",
        f"| Executable | `{ref['resolved_executable']}` | `{cand['resolved_executable']}` |",
        "",
        f"- Policy fingerprint: `{report['policy_fingerprint']}`",
        f"- Cases fingerprint: `{report['cases_fingerprint']}` ({report.get('cases_source')})",
        f"- Comparison policy: `{json.dumps(report['comparison_policy'])}`",
        f"- Environment: `{json.dumps(report.get('environment', {}))}`",
        f"- Started: {report.get('started_at')} · Finished: {report.get('finished_at')}",
        "",
        "## Normalization rules",
        "",
    ]
    rules = report["normalization_rules"]
    if not rules:
        lines.append("None. All output fields were compared exactly.")
    else:
        lines += ["| id | path | action | reason | values normalized |", "|---|---|---|---|---|"]
        totals = report.get("normalization_totals", {})
        for r in rules:
            lines.append(
                f"| {r['id']} | `{_md_escape(r['path'])}` | {r['action']} | "
                f"{_md_escape(r['reason'])} | {totals.get(r['id'], '?')} |"
            )
    if report.get("warnings"):
        lines += ["", "## Warnings", ""] + [f"- {_md_escape(w)}" for w in report["warnings"]]

    lines += ["", "## Outcomes", "", "| Outcome | Count |", "|---|---|"]
    for name, count in res["outcomes"].items():
        if count:
            lines.append(f"| {name} | {count} |")
    lines += ["", "| Case | Outcome | Differences | Ref ms | Cand ms |", "|---|---|---|---|---|"]
    for c in report["cases"]:
        rms = _ms(c.get("reference_runs"))
        cms = _ms(c.get("candidate_runs"))
        lines.append(
            f"| {_md_escape(c['id'])} | {c['outcome']} | {len(c.get('differences', []))} "
            f"| {rms} | {cms} |"
        )

    failing = [c for c in report["cases"] if c["outcome"] != MATCH]
    if failing:
        lines += ["", "## Details of non-matching cases", ""]
    for c in failing:
        lines += [f"### `{_md_escape(c['id'])}` — {c['outcome']}", ""]
        if c.get("description"):
            lines += [_md_escape(c["description"]), ""]
        lines += ["Input:", "", _code(c["input"])]
        for note in c.get("notes", []):
            lines.append(f"- {_md_escape(note)}")
        if c.get("notes"):
            lines.append("")
        if c.get("differences"):
            lines += ["| Path | Kind | Reference | Candidate |", "|---|---|---|---|"]
            for d in c["differences"]:
                lines.append(
                    f"| `{_md_escape(d['path']) or '(root)'}` | {d['kind']} | "
                    f"{_cell(d, 'reference')} | {_cell(d, 'candidate')} |"
                )
            lines.append("")
        for side in ("reference", "candidate"):
            runs = c.get(f"{side}_runs") or []
            if runs:
                r = runs[0]
                lines.append(
                    f"<details><summary>{side} run: status={r['status']}, "
                    f"exit_code={r['exit_code']}</summary>\n"
                )
                lines += ["stdout:", "", _code(r.get("stdout", ""))]
                if r.get("stderr"):
                    lines += ["stderr:", "", _code(r["stderr"])]
                lines += ["</details>", ""]
    lines += [
        "",
        "---",
        "Careo compares observable behavior on the supplied inputs only. "
        "A clean report is evidence, not proof, of equivalence.",
        "",
    ]
    return "\n".join(lines)


def _ms(runs: Any) -> str:
    if not runs:
        return "-"
    return f"{runs[0].get('duration_seconds', 0) * 1000:.1f}"


def _cell(d: dict[str, Any], key: str) -> str:
    if key not in d:
        return "*(absent)*"
    text = json.dumps(d[key], ensure_ascii=False)
    if len(text) > 200:
        text = text[:200] + "…"
    return "`" + _md_escape(text).replace("`", "'") + "`"
