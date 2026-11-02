"""Scenario specification (``careo-scenarios.yaml``): services, state, operations.

Validation follows Phase 1 rules: unknown keys are errors, every path must stay
inside the spec directory, commands are argument lists, and endpoints must be
loopback unless ``allow_remote: true`` is set.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from careo.adapters.http import Endpoint, EndpointError, parse_endpoint
from careo.adapters.sqlite import StateError, TableSpec, check_ident
from careo.comparison import ComparisonPolicy
from careo.config import (
    DEFAULT_INHERIT_ENV,
    ConfigError,
    _check_keys,
    _choice,
    _int,
    _load_rules,
    _positive_number,
    _str,
    contained_path,
)
from careo.jsonvalue import canonical, ensure_json_value
from careo.normalize import NormalizationRule, parse_pointer

SPEC_VERSION = 1
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# --------------------------------------------------------------------------- values


@dataclass(frozen=True)
class ValueSpec:
    """A declarative value generator. ``kind`` is one of: const, sampled_from,
    integers, text, booleans, bundle, one_of, maybe_absent."""

    kind: str
    arg: Any = None

    def bundles(self) -> list[str]:
        if self.kind == "bundle":
            return [self.arg]
        if self.kind == "one_of":
            return [b for v in self.arg for b in v.bundles()]
        if self.kind == "maybe_absent":
            return list(self.arg.bundles())
        return []

    def to_dict(self) -> Any:
        if self.kind in ("one_of",):
            return {"one_of": [v.to_dict() for v in self.arg]}
        if self.kind == "maybe_absent":
            return {"maybe_absent": self.arg.to_dict()}
        return {self.kind: self.arg}


def parse_value(raw: Any, where: str) -> ValueSpec:
    if not isinstance(raw, dict):
        try:
            ensure_json_value(raw, where)
        except ValueError as exc:
            raise ConfigError(str(exc)) from None
        return ValueSpec("const", raw)
    if len(raw) != 1:
        raise ConfigError(f"{where}: a value generator must have exactly one key")
    ((kind, arg),) = raw.items()
    if kind == "const":
        try:
            ensure_json_value(arg, where)
        except ValueError as exc:
            raise ConfigError(str(exc)) from None
        return ValueSpec("const", arg)
    if kind == "sampled_from":
        if not isinstance(arg, list) or not arg:
            raise ConfigError(f"{where}.sampled_from must be a non-empty list")
        try:
            ensure_json_value(arg, where)
        except ValueError as exc:
            raise ConfigError(str(exc)) from None
        return ValueSpec("sampled_from", tuple(arg))
    if kind == "integers":
        d = _check_keys(arg, f"{where}.integers", {"min", "max"}, set())
        lo, hi = d["min"], d["max"]
        for v in (lo, hi):
            if isinstance(v, bool) or not isinstance(v, int):
                raise ConfigError(f"{where}.integers bounds must be integers")
        if lo > hi:
            raise ConfigError(f"{where}.integers: min > max")
        return ValueSpec("integers", (lo, hi))
    if kind == "text":
        d = _check_keys(arg, f"{where}.text", set(), {"min_size", "max_size", "alphabet"})
        lo = _int(d.get("min_size", 0), f"{where}.text.min_size", 0, 10000)
        hi = _int(d.get("max_size", 20), f"{where}.text.max_size", lo, 10000)
        alphabet = d.get("alphabet")
        if alphabet is not None and (not isinstance(alphabet, str) or not alphabet):
            raise ConfigError(f"{where}.text.alphabet must be a non-empty string")
        return ValueSpec("text", (lo, hi, alphabet))
    if kind == "booleans":
        return ValueSpec("booleans", True)
    if kind == "bundle":
        return ValueSpec("bundle", _str(arg, f"{where}.bundle"))
    if kind == "one_of":
        if not isinstance(arg, list) or not arg:
            raise ConfigError(f"{where}.one_of must be a non-empty list")
        return ValueSpec(
            "one_of", tuple(parse_value(v, f"{where}.one_of[{i}]") for i, v in enumerate(arg))
        )
    if kind == "maybe_absent":
        return ValueSpec("maybe_absent", parse_value(arg, f"{where}.maybe_absent"))
    raise ConfigError(f"{where}: unknown value generator {kind!r}")


# --------------------------------------------------------------------------- operations


@dataclass(frozen=True)
class IdPath:
    pointer: str
    entity: str

    def to_dict(self) -> dict[str, str]:
        return {"pointer": self.pointer, "entity": self.entity}


@dataclass(frozen=True)
class Operation:
    name: str
    method: str
    path: str
    params: dict[str, ValueSpec] = field(default_factory=dict)
    query: dict[str, ValueSpec] = field(default_factory=dict)
    body: dict[str, ValueSpec] | ValueSpec | None = None
    produces: IdPath | None = None
    ids: tuple[IdPath, ...] = ()
    expect_status: tuple[int, ...] = ()

    def bundle_slots(self) -> list[str]:
        out: list[str] = []
        for v in self._values():
            out += v.bundles()
        return out

    def _values(self) -> list[ValueSpec]:
        vals = list(self.params.values()) + list(self.query.values())
        if isinstance(self.body, dict):
            vals += list(self.body.values())
        elif isinstance(self.body, ValueSpec):
            vals.append(self.body)
        return vals

    def all_id_paths(self) -> tuple[IdPath, ...]:
        return ((self.produces,) if self.produces else ()) + self.ids

    def to_dict(self) -> dict[str, Any]:
        body: Any
        if isinstance(self.body, dict):
            body = {k: v.to_dict() for k, v in self.body.items()}
        elif isinstance(self.body, ValueSpec):
            body = self.body.to_dict()
        else:
            body = None
        return {
            "name": self.name,
            "method": self.method,
            "path": self.path,
            "params": {k: v.to_dict() for k, v in self.params.items()},
            "query": {k: v.to_dict() for k, v in self.query.items()},
            "body": body,
            "produces": self.produces.to_dict() if self.produces else None,
            "ids": [i.to_dict() for i in self.ids],
            "expect_status": list(self.expect_status),
        }


def _id_path(raw: Any, where: str, entities: set[str]) -> IdPath:
    d = _check_keys(raw, where, {"pointer", "entity"}, set())
    pointer = _str(d["pointer"], f"{where}.pointer")
    try:
        parse_pointer(pointer)
    except ValueError as exc:
        raise ConfigError(f"{where}.pointer: {exc}") from None
    entity = _str(d["entity"], f"{where}.entity")
    if entity not in entities:
        raise ConfigError(f"{where}.entity {entity!r} is not declared in entities")
    return IdPath(pointer, entity)


def parse_operation(raw: Any, where: str, entities: set[str]) -> Operation:
    d = _check_keys(
        raw,
        where,
        {"name", "method", "path"},
        {"params", "query", "body", "produces", "ids", "expect_status"},
    )
    name = _str(d["name"], f"{where}.name")
    if not _NAME.match(name):
        raise ConfigError(f"{where}.name {name!r} must match {_NAME.pattern}")
    method = _choice(str(d["method"]).upper(), f"{where}.method", METHODS)
    path = _str(d["path"], f"{where}.path")
    if not path.startswith("/") or "://" in path or ".." in path.split("/"):
        raise ConfigError(f"{where}.path must be an absolute path like /items/{{id}}")

    def mapping(key: str) -> dict[str, ValueSpec]:
        m = d.get(key, {}) or {}
        if not isinstance(m, dict):
            raise ConfigError(f"{where}.{key} must be a mapping")
        return {str(k): parse_value(v, f"{where}.{key}.{k}") for k, v in m.items()}

    params = mapping("params")
    placeholders = set(re.findall(r"\{([^{}]+)\}", path))
    if placeholders != set(params):
        raise ConfigError(
            f"{where}: path placeholders {sorted(placeholders)} must match params {sorted(params)}"
        )
    for k, v in params.items():
        if v.kind == "maybe_absent":
            raise ConfigError(f"{where}.params.{k}: path parameters cannot be absent")
    body: dict[str, ValueSpec] | ValueSpec | None = None
    if "body" in d:
        raw_body = d["body"]
        if isinstance(raw_body, dict) and not (
            len(raw_body) == 1 and next(iter(raw_body)) in _GENERATOR_KEYS
        ):
            body = {str(k): parse_value(v, f"{where}.body.{k}") for k, v in raw_body.items()}
        else:
            body = parse_value(raw_body, f"{where}.body")
    if body is not None and method in ("GET", "DELETE"):
        raise ConfigError(f"{where}: {method} operations cannot have a body")
    produces = _id_path(d["produces"], f"{where}.produces", entities) if "produces" in d else None
    ids_raw = d.get("ids", [])
    if not isinstance(ids_raw, list):
        raise ConfigError(f"{where}.ids must be a list")
    ids = tuple(_id_path(x, f"{where}.ids[{i}]", entities) for i, x in enumerate(ids_raw))
    exp = d.get("expect_status", [])
    if not isinstance(exp, list):
        raise ConfigError(f"{where}.expect_status must be a list of status codes")
    expect = tuple(_int(c, f"{where}.expect_status[]", 100, 599) for c in exp)
    op = Operation(name, method, path, params, mapping("query"), body, produces, ids, expect)
    for b in op.bundle_slots():
        if b not in entities:
            raise ConfigError(f"{where}: bundle {b!r} is not declared in entities")
    return op


_GENERATOR_KEYS = {
    "const",
    "sampled_from",
    "integers",
    "text",
    "booleans",
    "bundle",
    "one_of",
    "maybe_absent",
}


# --------------------------------------------------------------------------- services/state


@dataclass(frozen=True)
class ServiceSpec:
    role: str
    name: str
    base_url: str  # may contain {port}
    command: tuple[str, ...] | None
    cwd: Path
    env: tuple[tuple[str, str], ...]
    inherit_env: tuple[str, ...]
    ready_path: str
    ready_timeout: float
    allow_remote: bool
    db: Path | None  # absolute; only for unmanaged services
    identity_files: tuple[str, ...]

    @property
    def managed(self) -> bool:
        return self.command is not None

    def endpoint(self, port: int | None) -> Endpoint:
        url = self.base_url.replace("{port}", str(port)) if port is not None else self.base_url
        try:
            return parse_endpoint(url, allow_remote=self.allow_remote)
        except EndpointError as exc:
            raise ConfigError(f"services.{self.role}.base_url: {exc}") from None

    def child_env(self, extra: dict[str, str]) -> dict[str, str]:
        env = {k: os.environ[k] for k in self.inherit_env if k in os.environ}
        env.update(dict(self.env))
        env.update(extra)
        return env

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "command": list(self.command) if self.command else None,
            "cwd": str(self.cwd),
            "env_names": sorted(k for k, _ in self.env),
            "inherit_env": list(self.inherit_env),
            "ready_path": self.ready_path,
            "allow_remote": self.allow_remote,
            "identity_files": list(self.identity_files),
        }


@dataclass(frozen=True)
class StateSpec:
    schema_sql: str
    fixture_sql: str
    schema_file: str
    fixture_file: str
    reset: str  # "sqlite" | "restart"
    reset_request: tuple[str, str] | None
    tables: tuple[TableSpec, ...]
    compare: str  # each_step | end | never

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema_file,
            "fixture": self.fixture_file,
            "reset": self.reset,
            "reset_request": list(self.reset_request) if self.reset_request else None,
            "tables": [t.to_dict() for t in self.tables],
            "compare": self.compare,
        }


@dataclass(frozen=True)
class Generation:
    seed: int = 0
    max_scenarios: int = 100
    max_steps: int = 20
    shrink: bool = True
    replay_checks: int = 3
    max_operations: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "max_scenarios": self.max_scenarios,
            "max_steps": self.max_steps,
            "shrink": self.shrink,
            "replay_checks": self.replay_checks,
            "max_operations": self.max_operations,
        }


@dataclass(frozen=True)
class ScenarioSpec:
    path: Path
    base_dir: Path
    reference: ServiceSpec
    candidate: ServiceSpec
    state: StateSpec | None
    entities: tuple[str, ...]
    operations: tuple[Operation, ...]
    operations_source: str
    timeout_seconds: float
    max_response_bytes: int
    compare_headers: tuple[str, ...]
    policy: ComparisonPolicy
    rules: tuple[NormalizationRule, ...]
    generation: Generation

    def op(self, name: str) -> Operation:
        for o in self.operations:
            if o.name == name:
                return o
        raise KeyError(name)

    def public_dict(self) -> dict[str, Any]:
        return {
            "version": SPEC_VERSION,
            "reference": self.reference.to_public_dict(),
            "candidate": self.candidate.to_public_dict(),
            "state": self.state.to_dict() if self.state else None,
            "state_sql": ([self.state.schema_sql, self.state.fixture_sql] if self.state else None),
            "entities": list(self.entities),
            "operations": [o.to_dict() for o in self.operations],
            "http": {
                "timeout_seconds": self.timeout_seconds,
                "max_response_bytes": self.max_response_bytes,
                "compare_headers": list(self.compare_headers),
            },
            "comparison": self.policy.to_dict(),
            "normalize": [r.to_dict() for r in self.rules],
        }

    def fingerprint(self) -> str:
        """Everything that defines behavior comparison (generation settings excluded)."""
        import hashlib

        return "sha256:" + hashlib.sha256(canonical(self.public_dict()).encode()).hexdigest()

    def with_generation(self, gen: Generation) -> ScenarioSpec:
        from dataclasses import replace

        return replace(self, generation=gen)


def _service(raw: Any, role: str, base: Path) -> ServiceSpec:
    where = f"services.{role}"
    d = _check_keys(
        raw,
        where,
        {"base_url"},
        {
            "name",
            "command",
            "cwd",
            "env",
            "inherit_env",
            "ready_path",
            "ready_timeout_seconds",
            "allow_remote",
            "db",
            "reset_wipes_database",
            "identity_files",
        },
    )
    base_url = _str(d["base_url"], f"{where}.base_url")
    command: tuple[str, ...] | None = None
    if "command" in d:
        c = d["command"]
        if isinstance(c, str) or not isinstance(c, list) or not c:
            raise ConfigError(f"{where}.command must be a non-empty list of arguments (no shell)")
        command = tuple(_str(a, f"{where}.command[{i}]") for i, a in enumerate(c))
    cwd = contained_path(base, _str(d.get("cwd", "."), f"{where}.cwd"), f"{where}.cwd")
    if not cwd.is_dir():
        raise ConfigError(f"{where}.cwd is not a directory")
    env_raw = d.get("env", {}) or {}
    if not isinstance(env_raw, dict):
        raise ConfigError(f"{where}.env must be a mapping")
    env = []
    for k, v in env_raw.items():
        if not isinstance(k, str) or not _ENV.match(k):
            raise ConfigError(f"{where}.env: invalid variable name {k!r}")
        env.append((k, _str(v, f"{where}.env.{k}", allow_empty=True)))
    inherit = d.get("inherit_env", list(DEFAULT_INHERIT_ENV))
    if not isinstance(inherit, list) or not all(
        isinstance(k, str) and _ENV.match(k) for k in inherit
    ):
        raise ConfigError(f"{where}.inherit_env must be a list of variable names")
    allow_remote = d.get("allow_remote", False)
    if not isinstance(allow_remote, bool):
        raise ConfigError(f"{where}.allow_remote must be true or false")
    if command is None and "{port}" in base_url:
        raise ConfigError(f"{where}.base_url may only use {{port}} for managed services")
    if command is not None and "{port}" not in base_url:
        raise ConfigError(
            f"{where}: managed services must use {{port}} in base_url (Careo picks a free port)"
        )
    ready_path = _str(d.get("ready_path", "/"), f"{where}.ready_path")
    if not ready_path.startswith("/"):
        raise ConfigError(f"{where}.ready_path must start with '/'")
    db: Path | None = None
    if "db" in d:
        if command is not None:
            raise ConfigError(f"{where}.db: managed services get a Careo-owned database via {{db}}")
        if d.get("reset_wipes_database") is not True:
            raise ConfigError(
                f"{where}: Careo deletes and reloads every table of {d['db']!r} before each "
                "scenario; set reset_wipes_database: true to confirm it is a disposable "
                "test database"
            )
        db = contained_path(base, _str(d["db"], f"{where}.db"), f"{where}.db")
    ident = d.get("identity_files", [])
    if not isinstance(ident, list):
        raise ConfigError(f"{where}.identity_files must be a list")
    idents = []
    for i, rel in enumerate(ident):
        p = contained_path(base, _str(rel, f"{where}.identity_files[{i}]"), where)
        if not p.exists():
            raise ConfigError(f"{where}.identity_files[{i}]: {rel!r} does not exist")
        idents.append(str(p.relative_to(base)))
    spec = ServiceSpec(
        role,
        _str(d.get("name", role), f"{where}.name"),
        base_url,
        command,
        cwd,
        tuple(env),
        tuple(inherit),
        ready_path,
        _positive_number(d.get("ready_timeout_seconds", 15), f"{where}.ready_timeout_seconds"),
        allow_remote,
        db,
        tuple(idents),
    )
    spec.endpoint(1 if command else None)  # validate host policy early
    return spec


def _state(raw: Any, base: Path, entities: set[str]) -> StateSpec:
    d = _check_keys(
        raw,
        "state",
        {"kind", "schema", "tables"},
        {"fixture", "reset", "reset_request", "compare"},
    )
    if d["kind"] != "sqlite":
        raise ConfigError("state.kind must be 'sqlite' (the only supported state adapter)")
    schema_file = _str(d["schema"], "state.schema")
    schema_sql = contained_path(base, schema_file, "state.schema").read_text(encoding="utf-8")
    fixture_file = d.get("fixture", "")
    fixture_sql = (
        contained_path(base, _str(fixture_file, "state.fixture"), "state.fixture").read_text(
            encoding="utf-8"
        )
        if fixture_file
        else ""
    )
    if re.search(r"\b(BEGIN|COMMIT|ROLLBACK|ATTACH)\b", fixture_sql, re.I):
        raise ConfigError("state.fixture must not contain BEGIN/COMMIT/ROLLBACK/ATTACH")
    reset = _choice(d.get("reset", "sqlite"), "state.reset", ("sqlite", "restart"))
    rr = d.get("reset_request")
    reset_request = None
    if rr is not None:
        r = _check_keys(rr, "state.reset_request", {"method", "path"}, set())
        reset_request = (
            _choice(str(r["method"]).upper(), "state.reset_request.method", METHODS),
            _str(r["path"], "state.reset_request.path"),
        )
    if not isinstance(d["tables"], list) or not d["tables"]:
        raise ConfigError("state.tables must list at least one table to compare")
    tables = []
    try:
        for i, t in enumerate(d["tables"]):
            w = f"state.tables[{i}]"
            td = _check_keys(
                t, w, {"name"}, {"order_by", "unordered", "ignore_columns", "id_columns"}
            )
            idc = td.get("id_columns", {}) or {}
            if not isinstance(idc, dict):
                raise ConfigError(f"{w}.id_columns must map column -> entity")
            for col, ent in idc.items():
                check_ident(col, f"{w}.id_columns")
                if ent not in entities:
                    raise ConfigError(f"{w}.id_columns.{col}: entity {ent!r} is not declared")
            unordered = td.get("unordered", False)
            if not isinstance(unordered, bool):
                raise ConfigError(f"{w}.unordered must be true or false")
            tables.append(
                TableSpec(
                    check_ident(td["name"], f"{w}.name"),
                    tuple(check_ident(c, f"{w}.order_by") for c in td.get("order_by", [])),
                    unordered,
                    tuple(
                        check_ident(c, f"{w}.ignore_columns") for c in td.get("ignore_columns", [])
                    ),
                    {str(k): str(v) for k, v in idc.items()},
                )
            )
    except StateError as exc:
        raise ConfigError(str(exc)) from None
    compare = _choice(d.get("compare", "each_step"), "state.compare", ("each_step", "end", "never"))
    return StateSpec(
        schema_sql,
        fixture_sql,
        schema_file,
        fixture_file or "",
        reset,
        reset_request,
        tuple(tables),
        compare,
    )


def load_spec(path: str | os.PathLike[str]) -> ScenarioSpec:
    p = Path(path)
    try:
        p = p.resolve(strict=True)
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"scenario spec not found: {path}") from None
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read scenario spec {path}: {exc}") from None
    base = p.parent
    d = _check_keys(
        raw,
        "spec",
        {"version", "services", "entities"},
        {"operations", "openapi", "state", "http", "comparison", "normalize", "generation"},
    )
    if d["version"] != SPEC_VERSION or isinstance(d["version"], bool):
        raise ConfigError(f"spec.version must be {SPEC_VERSION}")
    svc = _check_keys(d["services"], "services", {"reference", "candidate"}, set())
    reference = _service(svc["reference"], "reference", base)
    candidate = _service(svc["candidate"], "candidate", base)

    ents = d["entities"]
    if not isinstance(ents, list) or not all(isinstance(e, str) and _NAME.match(e) for e in ents):
        raise ConfigError("entities must be a list of names")
    entities = set(ents)

    state = _state(d["state"], base, entities) if "state" in d else None
    if state and state.reset == "restart" and not (reference.managed and candidate.managed):
        raise ConfigError("state.reset: restart requires managed services (command)")
    for s in (reference, candidate):
        if state and not s.managed and s.db is None:
            raise ConfigError(
                f"services.{s.role}.db is required for an unmanaged service with state"
            )
    if reference.db is not None and reference.db == candidate.db:
        raise ConfigError("reference and candidate must not share one database file")

    if ("operations" in d) == ("openapi" in d):
        raise ConfigError("give exactly one of operations or openapi")
    if "operations" in d:
        if not isinstance(d["operations"], list) or not d["operations"]:
            raise ConfigError("operations must be a non-empty list")
        ops = tuple(
            parse_operation(o, f"operations[{i}]", entities) for i, o in enumerate(d["operations"])
        )
        source = "inline"
    else:
        from careo.scenarios.openapi import load_openapi

        o = _check_keys(d["openapi"], "openapi", {"file"}, {"include"})
        f = contained_path(base, _str(o["file"], "openapi.file"), "openapi.file")
        include = o.get("include")
        if include is not None and not isinstance(include, list):
            raise ConfigError("openapi.include must be a list of operationIds")
        ops = load_openapi(f, entities, include)
        source = str(f.relative_to(base))
    names = [o.name for o in ops]
    if len(set(names)) != len(names):
        raise ConfigError("operation names must be unique")
    produced = {op.produces.entity for op in ops if op.produces}
    for op in ops:
        for b in op.bundle_slots():
            if b not in produced:
                raise ConfigError(
                    f"operation {op.name!r} depends on entity {b!r}, but no operation produces it "
                    "(unsupported operation dependency)"
                )

    h = _check_keys(
        d.get("http", {}),
        "http",
        set(),
        {"timeout_seconds", "max_response_bytes", "compare_headers"},
    )
    headers = h.get("compare_headers", [])
    if not isinstance(headers, list) or not all(isinstance(x, str) for x in headers):
        raise ConfigError("http.compare_headers must be a list of header names")
    c = _check_keys(
        d.get("comparison", {}),
        "comparison",
        set(),
        {"array_order", "numbers", "float_abs_tolerance"},
    )
    tol = c.get("float_abs_tolerance", 0.0)
    if isinstance(tol, bool) or not isinstance(tol, (int, float)) or not (0 <= tol < float("inf")):
        raise ConfigError("comparison.float_abs_tolerance must be a finite number >= 0")
    policy = ComparisonPolicy(
        array_order=_choice(
            c.get("array_order", "strict"), "comparison.array_order", ("strict", "ignore")
        ),
        numbers=_choice(c.get("numbers", "strict"), "comparison.numbers", ("strict", "numeric")),
        float_abs_tolerance=float(tol),
    )
    g = _check_keys(
        d.get("generation", {}),
        "generation",
        set(),
        {"seed", "max_scenarios", "max_steps", "shrink", "replay_checks", "max_operations"},
    )
    shrink = g.get("shrink", True)
    if not isinstance(shrink, bool):
        raise ConfigError("generation.shrink must be true or false")
    max_ops = g.get("max_operations")
    gen = Generation(
        seed=_int(g.get("seed", 0), "generation.seed", 0, 2**32 - 1),
        max_scenarios=_int(g.get("max_scenarios", 100), "generation.max_scenarios", 1, 100000),
        max_steps=_int(g.get("max_steps", 20), "generation.max_steps", 1, 1000),
        shrink=shrink,
        replay_checks=_int(g.get("replay_checks", 3), "generation.replay_checks", 1, 50),
        max_operations=None if max_ops is None else _int(max_ops, "generation.max_operations", 1),
    )
    return ScenarioSpec(
        p,
        base,
        reference,
        candidate,
        state,
        tuple(ents),
        ops,
        source,
        _positive_number(h.get("timeout_seconds", 5), "http.timeout_seconds"),
        _int(h.get("max_response_bytes", 1_048_576), "http.max_response_bytes", 1, 1 << 30),
        tuple(x.lower() for x in headers),
        policy,
        _load_rules(d.get("normalize", [])),
        gen,
    )
