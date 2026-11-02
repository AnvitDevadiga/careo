"""Operation discovery from a documented subset of OpenAPI 3.0 / 3.1.

Supported:
  * paths with get/post/put/patch/delete operations that have an operationId
  * path and query parameters, inline or via #/components/parameters/*
  * parameter / property schemas of type integer (minimum, maximum),
    string (enum, minLength, maxLength), boolean, or with an enum
  * application/json request bodies whose schema is an object of such
    properties (``required`` respected; others may be absent), inline or via
    #/components/schemas/*
  * numeric response codes become the operation's expect_status
  * dependencies via vendor extensions:
      x-careo-bundle: <entity>                 on a parameter or property
      x-careo-produces: {entity, pointer}      on an operation
      x-careo-ids: [{entity, pointer}, ...]    on an operation

Anything else used by an included operation (oneOf/anyOf/allOf/not, arrays,
nested objects, external $refs, header/cookie parameters, non-JSON bodies, ...)
is an error rather than being silently skipped. This is not full OpenAPI coverage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from careo.config import ConfigError
from careo.scenarios.spec import METHODS, Operation, ValueSpec, parse_operation

_UNSUPPORTED_SCHEMA_KEYS = {
    "oneOf",
    "anyOf",
    "allOf",
    "not",
    "items",
    "properties",
    "additionalProperties",
    "patternProperties",
    "if",
    "then",
    "else",
    "$defs",
    "dependentSchemas",
}
_MAX_REF_DEPTH = 32


def _load(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read OpenAPI document {path.name}: {exc}") from None


class _Doc:
    def __init__(self, doc: Any, name: str) -> None:
        if not isinstance(doc, dict):
            raise ConfigError(f"{name}: OpenAPI document must be a mapping")
        version = str(doc.get("openapi", ""))
        if not (version.startswith("3.0") or version.startswith("3.1")):
            raise ConfigError(f"{name}: only OpenAPI 3.0.x/3.1.x is supported (got {version!r})")
        if not isinstance(doc.get("paths"), dict):
            raise ConfigError(f"{name}: 'paths' must be a mapping")
        self.doc = doc
        self.name = name

    def deref(self, node: Any, where: str, depth: int = 0) -> Any:
        if not isinstance(node, dict) or "$ref" not in node:
            return node
        if depth > _MAX_REF_DEPTH:
            raise ConfigError(f"{where}: $ref chain too deep or cyclic")
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/components/"):
            raise ConfigError(f"{where}: unsupported $ref {ref!r} (only #/components/... refs)")
        target: Any = self.doc
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                raise ConfigError(f"{where}: $ref {ref!r} does not resolve")
            target = target[part]
        return self.deref(target, f"{where} -> {ref}", depth + 1)


def _value_from_schema(doc: _Doc, raw: Any, where: str, extra: dict[str, Any]) -> Any:
    """Translate a supported schema into a Careo value generator (raw dict form)."""
    if extra.get("x-careo-bundle"):
        return {"bundle": extra["x-careo-bundle"]}
    schema = doc.deref(raw, where)
    if not isinstance(schema, dict):
        raise ConfigError(f"{where}: schema must be a mapping")
    if schema.get("x-careo-bundle"):
        return {"bundle": schema["x-careo-bundle"]}
    bad = _UNSUPPORTED_SCHEMA_KEYS & set(schema)
    if bad:
        raise ConfigError(f"{where}: unsupported schema keyword(s) {sorted(bad)}")
    if "enum" in schema:
        if not isinstance(schema["enum"], list) or not schema["enum"]:
            raise ConfigError(f"{where}: enum must be a non-empty list")
        return {"sampled_from": schema["enum"]}
    typ = schema.get("type")
    if isinstance(typ, list):  # 3.1: ["integer", "null"] etc. are not supported
        raise ConfigError(f"{where}: type lists are not supported")
    if typ == "integer":
        lo = schema.get("minimum", 0)
        hi = schema.get("maximum", max(lo, 0) + 1000)
        if schema.get("exclusiveMinimum") is not None or schema.get("exclusiveMaximum") is not None:
            raise ConfigError(f"{where}: exclusiveMinimum/exclusiveMaximum are not supported")
        return {"integers": {"min": lo, "max": hi}}
    if typ == "string":
        if "pattern" in schema or "format" in schema:
            raise ConfigError(f"{where}: string pattern/format are not supported")
        return {
            "text": {
                "min_size": schema.get("minLength", 0),
                "max_size": schema.get("maxLength", 20),
            }
        }
    if typ == "boolean":
        return {"booleans": True}
    raise ConfigError(f"{where}: unsupported schema type {typ!r}")


def _operation(
    doc: _Doc, path: str, method: str, raw: Any, path_params: list[Any], entities: set[str]
) -> Operation:
    where = f"{doc.name}: {method.upper()} {path}"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: operation must be a mapping")
    op_id = raw.get("operationId")
    if not isinstance(op_id, str):
        raise ConfigError(f"{where}: operationId is required")
    params: dict[str, Any] = {}
    query: dict[str, Any] = {}
    for i, p in enumerate(path_params + list(raw.get("parameters", []) or [])):
        pw = f"{where} parameters[{i}]"
        p = doc.deref(p, pw)
        if not isinstance(p, dict):
            raise ConfigError(f"{pw}: parameter must be a mapping")
        loc, name = p.get("in"), p.get("name")
        if loc not in ("path", "query") or not isinstance(name, str):
            raise ConfigError(
                f"{pw}: only named path/query parameters are supported (got in={loc!r})"
            )
        value = _value_from_schema(doc, p.get("schema", {}), f"{pw} ({name})", p)
        if loc == "path":
            params[name] = value
        elif p.get("required"):
            query[name] = value
        else:
            query[name] = {"maybe_absent": value}
    spec: dict[str, Any] = {
        "name": op_id,
        "method": method.upper(),
        "path": path,
        "params": params,
        "query": query,
    }
    rb = raw.get("requestBody")
    if rb is not None:
        rb = doc.deref(rb, f"{where} requestBody")
        content = rb.get("content", {}) if isinstance(rb, dict) else {}
        if set(content) != {"application/json"}:
            raise ConfigError(f"{where}: only application/json request bodies are supported")
        schema = doc.deref(content["application/json"].get("schema", {}), f"{where} body")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ConfigError(f"{where}: request body schema must be an object")
        props = schema.get("properties", {})
        if not isinstance(props, dict):
            raise ConfigError(f"{where}: body properties must be a mapping")
        bad = (_UNSUPPORTED_SCHEMA_KEYS - {"properties"}) & set(schema)
        if bad:
            raise ConfigError(f"{where}: unsupported body schema keyword(s) {sorted(bad)}")
        required = set(schema.get("required", []))
        body = {}
        for k, ps in props.items():
            v = _value_from_schema(doc, ps, f"{where} body.{k}", {})
            body[k] = v if k in required else {"maybe_absent": v}
        spec["body"] = body
    if "x-careo-produces" in raw:
        spec["produces"] = raw["x-careo-produces"]
    if "x-careo-ids" in raw:
        spec["ids"] = raw["x-careo-ids"]
    codes = [int(c) for c in (raw.get("responses") or {}) if str(c).isdigit()]
    if codes:
        spec["expect_status"] = sorted(codes)
    return parse_operation(spec, where, entities)


def load_openapi(
    path: Path, entities: set[str], include: list[Any] | None
) -> tuple[Operation, ...]:
    doc = _Doc(_load(path), path.name)
    wanted = set(include) if include is not None else None
    ops: list[Operation] = []
    for p, item in doc.doc["paths"].items():
        if not isinstance(item, dict):
            raise ConfigError(f"{path.name}: path item {p!r} must be a mapping")
        shared = list(item.get("parameters", []) or [])
        for method, raw in item.items():
            if method.upper() not in METHODS:
                if method not in (
                    "parameters",
                    "summary",
                    "description",
                    "servers",
                ) and not method.startswith("x-"):
                    raise ConfigError(f"{path.name}: {p}: unsupported path item key {method!r}")
                continue
            op_id = raw.get("operationId") if isinstance(raw, dict) else None
            if wanted is not None and op_id not in wanted:
                continue
            ops.append(_operation(doc, p, method, raw, shared, entities))
    if wanted is not None:
        missing = wanted - {o.name for o in ops}
        if missing:
            raise ConfigError(f"openapi.include: operationId(s) not found: {sorted(missing)}")
    if not ops:
        raise ConfigError(f"{path.name}: no supported operations found")
    return tuple(ops)


__all__ = ["ValueSpec", "load_openapi"]
