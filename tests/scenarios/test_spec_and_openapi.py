import sys
from pathlib import Path

import pytest
import yaml
from conftest import item_spec

from careo.config import ConfigError
from careo.scenarios.spec import load_spec


def mutate(tmp_path, fn, **kw) -> Path:
    p = item_spec(tmp_path / "p", **kw)
    data = yaml.safe_load(p.read_text())
    fn(data)
    p.write_text(yaml.safe_dump(data))
    return p


def err(tmp_path, fn, match, **kw):
    with pytest.raises(ConfigError, match=match):
        load_spec(mutate(tmp_path, fn, **kw))


def test_valid_spec(tmp_path):
    spec = load_spec(item_spec(tmp_path / "p"))
    assert [o.name for o in spec.operations][:2] == ["create_item", "get_item"]
    assert spec.fingerprint().startswith("sha256:")


def test_unknown_key(tmp_path):
    err(tmp_path, lambda d: d.update(genration={}), "unknown key")


def test_shell_string_command(tmp_path):
    def f(d):
        d["services"]["candidate"]["command"] = "python itemsvc.py"

    err(tmp_path, f, "no shell")


def test_remote_endpoint_rejected_by_default(tmp_path):
    def f(d):
        d["services"]["reference"] = {"base_url": "https://api.example.com"}

    err(tmp_path, f, "not a loopback")


def test_unmanaged_db_requires_wipe_confirmation(tmp_path):
    def f(d):
        d["services"]["reference"] = {"base_url": "http://127.0.0.1:9", "db": "real.db"}

    err(tmp_path, f, "reset_wipes_database")


def test_path_traversal_in_state_files(tmp_path):
    err(tmp_path, lambda d: d["state"].update(schema="../schema.sql"), "outside")
    err(tmp_path, lambda d: d["state"].update(fixture="/etc/hosts"), "outside")


def test_symlinked_schema_outside_rejected(tmp_path):
    outside = tmp_path / "outside.sql"
    outside.write_text("CREATE TABLE x (a);")
    p = item_spec(tmp_path / "p")
    (p.parent / "link.sql").symlink_to(outside)
    data = yaml.safe_load(p.read_text())
    data["state"]["schema"] = "link.sql"
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="outside"):
        load_spec(p)


def test_fixture_may_not_control_transactions(tmp_path):
    p = item_spec(tmp_path / "p")
    (p.parent / "seed.sql").write_text("COMMIT; INSERT INTO items VALUES (1, 'a');")
    data = yaml.safe_load(p.read_text())
    data["state"]["fixture"] = "seed.sql"
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="BEGIN/COMMIT"):
        load_spec(p)


def test_undeclared_and_unproduced_dependencies(tmp_path):
    err(
        tmp_path,
        lambda d: d["operations"][1]["params"].update(id={"bundle": "ghost"}),
        "not declared",
    )

    def f(d):
        d["entities"].append("order")
        d["operations"][1]["params"]["id"] = {"bundle": "order"}

    err(tmp_path, f, "unsupported operation dependency")


def test_path_placeholder_mismatch(tmp_path):
    err(tmp_path, lambda d: d["operations"][1].update(path="/items/{item_id}"), "placeholders")


def test_restart_requires_managed_services(tmp_path):
    def f(d):
        for r in ("reference", "candidate"):
            d["services"][r] = {
                "base_url": "http://127.0.0.1:9",
                "db": f"{r}.db",
                "reset_wipes_database": True,
            }
        d["state"]["reset"] = "restart"

    err(tmp_path, f, "restart requires managed")


def test_shared_database_rejected(tmp_path):
    def f(d):
        for r in ("reference", "candidate"):
            d["services"][r] = {
                "base_url": "http://127.0.0.1:9",
                "db": "same.db",
                "reset_wipes_database": True,
            }

    err(tmp_path, f, "must not share")


OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "items", "version": "1"},
    "paths": {
        "/items": {
            "post": {
                "operationId": "create_item",
                "x-careo-produces": {"entity": "item", "pointer": "/id"},
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/NewItem"}}
                    }
                },
                "responses": {"201": {"description": "ok"}},
            },
            "get": {
                "operationId": "list_items",
                "x-careo-ids": [{"entity": "item", "pointer": "/items/*/id"}],
                "parameters": [
                    {
                        "in": "query",
                        "name": "limit",
                        "schema": {"type": "integer", "minimum": 1, "maximum": 5},
                    }
                ],
                "responses": {"200": {"description": "ok"}},
            },
        },
        "/items/{id}": {
            "parameters": [
                {
                    "in": "path",
                    "name": "id",
                    "required": True,
                    "schema": {"type": "integer"},
                    "x-careo-bundle": "item",
                }
            ],
            "get": {"operationId": "get_item", "responses": {"200": {}, "404": {}}},
            "delete": {"operationId": "delete_item", "responses": {"204": {}, "404": {}}},
        },
    },
    "components": {
        "schemas": {
            "NewItem": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "enum": ["a", "b"]},
                    "note": {"type": "string", "maxLength": 5},
                },
            }
        }
    },
}


def openapi_spec(tmp_path, doc, include=None):
    def f(d):
        del d["operations"]
        d["openapi"] = {"file": "openapi.yaml", **({"include": include} if include else {})}

    p = mutate(tmp_path, f)
    (p.parent / "openapi.yaml").write_text(yaml.safe_dump(doc))
    return p


def test_openapi_subset_discovery(tmp_path):
    spec = load_spec(openapi_spec(tmp_path, OPENAPI))
    ops = {o.name: o for o in spec.operations}
    assert set(ops) == {"create_item", "list_items", "get_item", "delete_item"}
    assert ops["get_item"].bundle_slots() == ["item"]
    assert ops["create_item"].produces.entity == "item"
    assert ops["create_item"].body["note"].kind == "maybe_absent"
    assert ops["list_items"].query["limit"].kind == "maybe_absent"
    assert ops["delete_item"].expect_status == (204, 404)


def test_openapi_runs_end_to_end(tmp_path):
    from careo.scenarios.runner import DIFFERENCE_CONFIRMED, run

    p = openapi_spec(tmp_path, OPENAPI)
    data = yaml.safe_load(p.read_text())
    data["services"]["candidate"]["command"].append("--double-delete-ok")
    p.write_text(yaml.safe_dump(data))
    r = run(load_spec(p), tmp_path / "o")
    assert r["outcome"] == DIFFERENCE_CONFIRMED
    assert [s["op"] for s in r["minimized_sequence"]["steps"]] == [
        "create_item",
        "delete_item",
        "delete_item",
    ]


@pytest.mark.parametrize(
    "patch,match",
    [
        (lambda d: d.update(openapi="2.0"), "only OpenAPI 3"),
        (
            lambda d: d["components"]["schemas"]["NewItem"]["properties"].update(
                tags={"type": "array", "items": {"type": "string"}}
            ),
            "unsupported",
        ),
        (
            lambda d: d["components"]["schemas"]["NewItem"]["properties"].update(
                x={"oneOf": [{"type": "string"}]}
            ),
            "unsupported schema keyword",
        ),
        (
            lambda d: d["paths"]["/items"]["post"]["requestBody"]["content"][
                "application/json"
            ].update(schema={"$ref": "other.yaml#/X"}),
            "unsupported \\$ref",
        ),
        (
            lambda d: d["components"]["schemas"].update(
                NewItem={"$ref": "#/components/schemas/NewItem"}
            ),
            "too deep or cyclic",
        ),
        (
            lambda d: d["paths"]["/items"]["get"]["parameters"].append(
                {"in": "header", "name": "X"}
            ),
            "path/query",
        ),
        (lambda d: d["paths"]["/items"]["post"].pop("operationId"), "operationId is required"),
        (
            lambda d: d["paths"]["/items/{id}"]["parameters"][0].update(
                {"x-careo-bundle": "order"}
            ),
            "not declared",
        ),
    ],
)
def test_invalid_or_unsupported_openapi(tmp_path, patch, match):
    import copy

    doc = copy.deepcopy(OPENAPI)
    patch(doc)
    with pytest.raises(ConfigError, match=match):
        load_spec(openapi_spec(tmp_path, doc))


def test_openapi_include_unknown(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_spec(openapi_spec(tmp_path, OPENAPI, include=["create_item", "nope"]))


def test_python_executable_is_used():
    assert sys.executable
