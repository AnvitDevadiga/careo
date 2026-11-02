from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).parent.parent


def write_project(
    root: Path,
    cases: list[dict[str, Any]],
    *,
    comparison: dict[str, Any] | None = None,
    normalize: list[dict[str, Any]] | None = None,
    limits: dict[str, Any] | None = None,
    reference: dict[str, Any] | None = None,
    candidate: dict[str, Any] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "prog.py", root / "prog.py")
    with (root / "cases.jsonl").open("w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(case, ensure_ascii=False) + "\n")
    config: dict[str, Any] = {
        "version": 1,
        "reference": reference or {"command": [sys.executable, "prog.py", "ref"], "cwd": "."},
        "candidate": candidate or {"command": [sys.executable, "prog.py", "cand"], "cwd": "."},
        "cases": {"file": "cases.jsonl"},
        "limits": {"timeout_seconds": 5, **(limits or {})},
    }
    if comparison:
        config["comparison"] = comparison
    if normalize:
        config["normalize"] = normalize
    path = root / "careo.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path: Path):
    def make(cases: list[dict[str, Any]], **kw: Any) -> Path:
        return write_project(tmp_path / "proj", cases, **kw)

    return make
