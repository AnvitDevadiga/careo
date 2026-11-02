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


ITEM_SCHEMA = """
CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE tombstones (id INTEGER PRIMARY KEY);
CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER, action TEXT);
"""


def item_spec(
    root: Path,
    ref_flags: list[str] | None = None,
    cand_flags: list[str] | None = None,
    *,
    generation: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    operations: list[dict[str, Any]] | None = None,
    http: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "itemsvc.py", root / "itemsvc.py")
    (root / "schema.sql").write_text(ITEM_SCHEMA)

    def svc(flags: list[str]) -> dict[str, Any]:
        return {
            "command": [sys.executable, "itemsvc.py", "--port", "{port}", "--db", "{db}", *flags],
            "base_url": "http://127.0.0.1:{port}",
            "ready_path": "/health",
        }

    spec: dict[str, Any] = {
        "version": 1,
        "services": {"reference": svc(ref_flags or []), "candidate": svc(cand_flags or [])},
        "state": {
            "kind": "sqlite",
            "schema": "schema.sql",
            "tables": [
                {"name": "items", "id_columns": {"id": "item"}},
                {"name": "tombstones", "id_columns": {"id": "item"}},
                {"name": "audit", "ignore_columns": ["id"], "id_columns": {"item_id": "item"}},
            ],
            **(state or {}),
        },
        "entities": ["item"],
        "operations": operations
        or [
            {
                "name": "create_item",
                "method": "POST",
                "path": "/items",
                "body": {"name": {"sampled_from": ["a", "b"]}},
                "produces": {"entity": "item", "pointer": "/id"},
            },
            {
                "name": "get_item",
                "method": "GET",
                "path": "/items/{id}",
                "params": {"id": {"bundle": "item"}},
                "ids": [{"entity": "item", "pointer": "/id"}],
            },
            {
                "name": "delete_item",
                "method": "DELETE",
                "path": "/items/{id}",
                "params": {"id": {"bundle": "item"}},
            },
            {
                "name": "list_items",
                "method": "GET",
                "path": "/items",
                "ids": [{"entity": "item", "pointer": "/items/*/id"}],
            },
        ],
        "http": {"timeout_seconds": 2, **(http or {})},
        "generation": {
            "seed": 7,
            "max_scenarios": 60,
            "max_steps": 12,
            "replay_checks": 3,
            **(generation or {}),
        },
        **(extra or {}),
    }
    path = root / "careo-scenarios.yaml"
    path.write_text(yaml.safe_dump(spec, sort_keys=False))
    return path
