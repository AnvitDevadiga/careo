"""Capture (or verify) the booking demo evidence used by the website.

    python scripts/capture_demo.py            # run the demo, write benchmarks/results/demo/
    python scripts/capture_demo.py --verify   # re-run and check it still reproduces

The run is real: both booking services are started and exercised by Careo.
Local absolute paths are replaced with <repo> and <out> before saving, so the
evidence carries no machine-specific paths. Nothing else is altered.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from careo.scenarios.runner import export_regression, run
from careo.scenarios.spec import load_spec

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "examples" / "booking-api" / "careo-scenarios.yaml"
OUT = REPO / "benchmarks" / "results" / "demo"


def _redact(text: str, out: Path) -> str:
    for real, label in (
        (str(out.resolve()), "<out>"),
        (str(out), "<out>"),
        (str(REPO), "<repo>"),
        (str(Path.home()), "<home>"),
    ):
        text = text.replace(real, label)
    return text


def capture(out: Path) -> dict:
    report = run(load_spec(SPEC), out)
    export_regression(report, load_spec(SPEC), out)
    return report


def key(report: dict) -> dict:
    """What must stay identical for the published demo to remain truthful."""
    return {
        "outcome": report["outcome"],
        "minimized_steps": report["minimized_sequence"]["steps"],
        "original_length": report["original_sequence"]["length"],
        "signature": report["verification"]["signatures"][0],
        "reproduced": report["verification"]["reproduced"],
        "checks": report["verification"]["checks"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "demo"
        report = capture(out)
        if a.verify:
            saved = json.loads((OUT / "scenario-report.json").read_text())
            if key(report) != key(saved):
                print(
                    "demo evidence no longer reproduces with the current engine:", file=sys.stderr
                )
                print(
                    json.dumps({"saved": key(saved), "now": key(report)}, indent=1), file=sys.stderr
                )
                return 1
            print(
                f"OK: demo reproduces ({report['minimized_sequence']['length']} steps, "
                f"{report['verification']['reproduced']}/{report['verification']['checks']})"
            )
            return 0
        OUT.mkdir(parents=True, exist_ok=True)
        for name in ("scenario-report.json", "regression.json", "test_careo_regression.py"):
            text = (
                (out / name).read_text()
                if name != "scenario-report.json"
                else (json.dumps(report, indent=2, ensure_ascii=False) + "\n")
            )
            (OUT / name).write_text(_redact(text, out))
    print(f"captured demo evidence in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
