"""Markdown and JSON output for stateful scenario runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _j(value: Any, limit: int = 160) -> str:
    text = json.dumps(value, ensure_ascii=False)
    text = text if len(text) <= limit else text[:limit] + "…"
    return "`" + text.replace("|", "\\|").replace("`", "'") + "`"


def _step_line(i: int, step: dict[str, Any]) -> str:
    args = {k: v for k, v in step.items() if k != "op" and v not in ({}, None)}
    return f"{i}. `{step['op']}` {_j(args) if args else ''}".rstrip()


def render(report: dict[str, Any]) -> str:
    st = report["stats"]
    lines = [
        "# Careo stateful report",
        "",
        f"**Outcome: {report['outcome']}**. {report['summary']}",
        "",
        f"- Reference: `{report['reference']['name']}` · "
        f"Candidate: `{report['candidate']['name']}`",
        f"- Spec fingerprint: `{report['spec_fingerprint']}`",
        f"- Generation: `{json.dumps(report['generation'])}`",
        f"- Scenarios generated: {st['scenarios_generated']} · operations executed: "
        f"{st['operations_executed']} · verified resets: {st['resets']} · runtime: "
        f"{st['runtime_seconds']}s",
    ]
    if st.get("operations_to_first_difference") is not None:
        lines.append(
            f"- First difference after {st['operations_to_first_difference']} operations "
            f"({st['time_to_first_difference_seconds']:.2f}s)"
        )
    for n in report["notes"]:
        lines.append(f"- Note: {n}")
    for w in report["warnings"]:
        lines.append(f"- Warning: {w}")
    orig, mini = report["original_sequence"], report["minimized_sequence"]
    if orig and mini:
        ver = report["verification"] or {}
        lines += [
            "",
            "## Counterexample",
            "",
            f"Original failing sequence: **{orig['length']} operations**. "
            f"Minimized: **{mini['length']} operations** ({mini['method']}; {mini['claim']}).",
            f"Verification: reproduced **{ver.get('reproduced', 0)}/{ver.get('checks', 0)}** times "
            "from clean, verified initial states.",
            "",
        ]
        lines += [_step_line(i, s) for i, s in enumerate(mini["steps"])]
    if report["evidence"]:
        lines += ["", "## Evidence (last verification replay)", ""]
        for s in report["evidence"]:
            lines.append(f"### Step {s['index']}: `{s['step']['op']}`: {s['verdict']}")
            lines.append("")
            r, c = s["reference"], s["candidate"]
            for label, obs in (("reference", r), ("candidate", c)):
                shown = obs.get("body", obs.get("body_text", obs.get("error")))
                lines.append(f"- {label}: HTTP {obs.get('code', obs.get('status'))} {_j(shown)}")
            for n in s["notes"]:
                lines.append(f"- note: {n}")
            if s["differences"]:
                lines += ["", "| Path | Kind | Reference | Candidate |", "|---|---|---|---|"]
                for d in s["differences"]:
                    lines.append(
                        f"| `{d['path']}` | {d['kind']} | "
                        f"{_j(d['reference']) if 'reference' in d else '*(absent)*'} | "
                        f"{_j(d['candidate']) if 'candidate' in d else '*(absent)*'} |"
                    )
            lines.append("")
    m = report["id_mapping"]
    if m["reference"]:
        lines += [
            "## Logical id mapping (final replay)",
            "",
            "| Logical | Reference | Candidate |",
            "|---|---|---|",
        ]
        for k in m["reference"]:
            lines.append(f"| {k} | {_j(m['reference'][k])} | {_j(m['candidate'].get(k))} |")
        lines.append("")
    if report["normalization_rules"]:
        lines += ["## Normalization rules", ""]
        for r in report["normalization_rules"]:
            lines.append(
                f"- `{r['id']}` {r['action']} `{r['path']}` ({r['reason']}): "
                f"{report['normalization_totals'].get(r['id'], 0)} values"
            )
        lines.append("")
    lines += [
        "---",
        "Seeded fixtures and bounded generation demonstrate differences; a clean "
        "report is evidence, not proof, of equivalence.",
        "",
    ]
    return "\n".join(lines)


def write(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jp, mp = out_dir / "scenario-report.json", out_dir / "scenario-report.md"
    for path, text in (
        (jp, json.dumps(report, indent=2, ensure_ascii=False) + "\n"),
        (mp, render(report)),
    ):
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    return jp, mp
