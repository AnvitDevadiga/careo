"""Loading and validating ``careo.yaml``.

Validation is strict: unknown keys are errors (a typo such as ``normalise``
must not silently disable a rule), paths must stay inside the directory that
contains the config file, and executables are resolved to absolute paths
before anything runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from careo.comparison import ComparisonPolicy
from careo.jsonvalue import JSONParseError, canonical, ensure_json_value, parse_strict
from careo.normalize import ACTIONS, NormalizationRule, parse_pointer

CONFIG_VERSION = 1
DEFAULT_INHERIT_ENV = ("PATH", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigError(Exception):
    """The configuration is invalid; nothing was executed."""


@dataclass(frozen=True)
class Limits:
    timeout_seconds: float = 10.0
    max_output_bytes: int = 1_048_576
    max_input_bytes: int = 1_048_576
    max_cases_file_bytes: int = 16_777_216

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "max_input_bytes": self.max_input_bytes,
            "max_cases_file_bytes": self.max_cases_file_bytes,
        }


@dataclass(frozen=True)
class ProgramSpec:
    role: str
    name: str
    command: tuple[str, ...]  # as configured
    argv: tuple[str, ...]  # argv[0] replaced by the resolved absolute executable
    cwd: Path  # absolute, inside base_dir
    cwd_rel: str
    env: tuple[tuple[str, str], ...]  # explicit values from config
    inherit_env: tuple[str, ...]

    def child_env(self) -> dict[str, str]:
        env = {k: os.environ[k] for k in self.inherit_env if k in os.environ}
        env.update(dict(self.env))
        return env

    def to_public_dict(self) -> dict[str, Any]:
        # Environment *values* are deliberately excluded: they may be secrets.
        return {
            "name": self.name,
            "command": list(self.command),
            "cwd": self.cwd_rel,
            "env_names": sorted(k for k, _ in self.env),
            "inherit_env": list(self.inherit_env),
        }


@dataclass(frozen=True)
class Case:
    id: str
    input: Any
    description: str = ""


@dataclass(frozen=True)
class Config:
    path: Path
    base_dir: Path
    reference: ProgramSpec
    candidate: ProgramSpec
    cases: tuple[Case, ...]
    cases_source: str
    limits: Limits
    policy: ComparisonPolicy
    rules: tuple[NormalizationRule, ...]

    def policy_dict(self) -> dict[str, Any]:
        return {
            "version": CONFIG_VERSION,
            "reference": self.reference.to_public_dict(),
            "candidate": self.candidate.to_public_dict(),
            "limits": self.limits.to_dict(),
            "comparison": self.policy.to_dict(),
            "normalize": [r.to_dict() for r in self.rules],
        }

    def policy_fingerprint(self) -> str:
        return _sha256_text(canonical(self.policy_dict()))

    def cases_fingerprint(self) -> str:
        return cases_fingerprint(self.cases)

    def with_cases(self, cases: tuple[Case, ...], source: str) -> Config:
        return replace(self, cases=cases, cases_source=source)


def cases_fingerprint(cases: tuple[Case, ...]) -> str:
    return _sha256_text(canonical([[c.id, c.input] for c in cases]))


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- helpers


def _check_keys(obj: Any, where: str, required: set[str], optional: set[str]) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ConfigError(f"{where} must be a mapping")
    for key in obj:
        if not isinstance(key, str):
            raise ConfigError(f"{where}: key {key!r} must be a string")
    unknown = set(obj) - required - optional
    if unknown:
        raise ConfigError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")
    missing = required - set(obj)
    if missing:
        raise ConfigError(f"{where}: missing required key(s): {', '.join(sorted(missing))}")
    return obj


def _str(value: Any, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ConfigError(f"{where} must be a non-empty string")
    if "\x00" in value:
        raise ConfigError(f"{where} must not contain NUL bytes")
    return value


def _int(value: Any, where: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        raise ConfigError(f"{where} must be between {minimum} and {maximum or 'unbounded'}")
    return value


def _positive_number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where} must be a number")
    if not (value > 0) or value != value or value == float("inf"):
        raise ConfigError(f"{where} must be a finite number > 0")
    return float(value)


def _choice(value: Any, where: str, choices: tuple[str, ...]) -> str:
    if value not in choices:
        raise ConfigError(f"{where} must be one of: {', '.join(choices)}")
    return str(value)


def contained_path(base: Path, rel: str, where: str) -> Path:
    """Resolve ``rel`` against ``base`` and refuse anything that escapes ``base``
    (including through ``..`` or symlinks)."""
    _str(rel, where)
    resolved = (base / rel).resolve()
    if not resolved.is_relative_to(base):
        raise ConfigError(f"{where}: {rel!r} resolves outside the config directory {base}")
    return resolved


# --------------------------------------------------------------------------- loading


def load_config(path: str | os.PathLike[str]) -> Config:
    config_path = Path(path)
    try:
        config_path = config_path.resolve(strict=True)
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from None
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"config is not valid YAML: {exc}") from None
    base = config_path.parent
    top = _check_keys(
        raw,
        "config",
        {"version", "reference", "candidate", "cases"},
        {"limits", "comparison", "normalize"},
    )
    if top["version"] != CONFIG_VERSION or isinstance(top["version"], bool):
        raise ConfigError(f"config.version must be {CONFIG_VERSION}")

    limits = _load_limits(top.get("limits", {}))
    policy = _load_policy(top.get("comparison", {}))
    rules = _load_rules(top.get("normalize", []))
    reference = _load_program(top["reference"], "reference", base)
    candidate = _load_program(top["candidate"], "candidate", base)
    cases, source = _load_cases(top["cases"], base, limits)
    return Config(config_path, base, reference, candidate, cases, source, limits, policy, rules)


def _load_limits(raw: Any) -> Limits:
    d = _check_keys(
        raw,
        "limits",
        set(),
        {"timeout_seconds", "max_output_bytes", "max_input_bytes", "max_cases_file_bytes"},
    )
    defaults = Limits()
    timeout = _positive_number(d.get("timeout_seconds", defaults.timeout_seconds), "timeout")
    if timeout > 3600:
        raise ConfigError("limits.timeout_seconds must be <= 3600")
    gib = 1 << 30
    return Limits(
        timeout_seconds=timeout,
        max_output_bytes=_int(
            d.get("max_output_bytes", defaults.max_output_bytes),
            "limits.max_output_bytes",
            1,
            gib,
        ),
        max_input_bytes=_int(
            d.get("max_input_bytes", defaults.max_input_bytes), "limits.max_input_bytes", 1, gib
        ),
        max_cases_file_bytes=_int(
            d.get("max_cases_file_bytes", defaults.max_cases_file_bytes),
            "limits.max_cases_file_bytes",
            1,
            gib,
        ),
    )


def _load_policy(raw: Any) -> ComparisonPolicy:
    d = _check_keys(
        raw,
        "comparison",
        set(),
        {
            "accepted_exit_codes",
            "stderr",
            "array_order",
            "numbers",
            "float_abs_tolerance",
            "repeat",
        },
    )
    codes_raw = d.get("accepted_exit_codes", [0])
    if not isinstance(codes_raw, list) or not codes_raw:
        raise ConfigError("comparison.accepted_exit_codes must be a non-empty list of integers")
    codes = tuple(sorted({_int(c, "comparison.accepted_exit_codes[]", 0, 255) for c in codes_raw}))
    tol_raw = d.get("float_abs_tolerance", 0.0)
    if isinstance(tol_raw, bool) or not isinstance(tol_raw, (int, float)):
        raise ConfigError("comparison.float_abs_tolerance must be a number")
    if not (0 <= tol_raw < float("inf")):
        raise ConfigError("comparison.float_abs_tolerance must be finite and >= 0")
    return ComparisonPolicy(
        accepted_exit_codes=codes,
        stderr=_choice(d.get("stderr", "ignore"), "comparison.stderr", ("ignore", "exact")),
        array_order=_choice(
            d.get("array_order", "strict"), "comparison.array_order", ("strict", "ignore")
        ),
        numbers=_choice(d.get("numbers", "strict"), "comparison.numbers", ("strict", "numeric")),
        float_abs_tolerance=float(tol_raw),
        repeat=_int(d.get("repeat", 1), "comparison.repeat", 1, 100),
    )


def _load_rules(raw: Any) -> tuple[NormalizationRule, ...]:
    if not isinstance(raw, list):
        raise ConfigError("normalize must be a list of rules")
    rules: list[NormalizationRule] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        where = f"normalize[{i}]"
        d = _check_keys(item, where, {"id", "path", "action", "reason"}, {"pattern", "replacement"})
        rule_id = _str(d["id"], f"{where}.id")
        if not _ID_RE.match(rule_id):
            raise ConfigError(f"{where}.id must match {_ID_RE.pattern}")
        if rule_id in seen:
            raise ConfigError(f"{where}.id {rule_id!r} is duplicated")
        seen.add(rule_id)
        path = _str(d["path"], f"{where}.path")
        try:
            parse_pointer(path)
        except ValueError as exc:
            raise ConfigError(f"{where}.path: {exc}") from None
        action = _choice(d["action"], f"{where}.action", ACTIONS)
        reason = _str(d["reason"], f"{where}.reason")
        pattern: str | None = None
        replacement = ""
        if action == "regex":
            pattern = _str(d.get("pattern"), f"{where}.pattern")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ConfigError(f"{where}.pattern is not a valid regex: {exc}") from None
            replacement = _str(d.get("replacement", ""), f"{where}.replacement", allow_empty=True)
            try:
                re.compile(pattern).sub(replacement, "")
            except (re.error, IndexError) as exc:
                raise ConfigError(f"{where}.replacement is invalid: {exc}") from None
        elif "pattern" in d or "replacement" in d:
            raise ConfigError(f"{where}: pattern/replacement are only valid with action 'regex'")
        rules.append(NormalizationRule(rule_id, path, action, reason, pattern, replacement))
    return tuple(rules)


def _load_program(raw: Any, role: str, base: Path) -> ProgramSpec:
    d = _check_keys(raw, role, {"command", "cwd"}, {"name", "env", "inherit_env"})
    name = _str(d.get("name", role), f"{role}.name")
    cmd_raw = d["command"]
    if isinstance(cmd_raw, str):
        raise ConfigError(
            f"{role}.command must be a list of arguments, not a shell string "
            "(Careo never invokes a shell)"
        )
    if not isinstance(cmd_raw, list) or not cmd_raw:
        raise ConfigError(f"{role}.command must be a non-empty list of strings")
    command = tuple(_str(a, f"{role}.command[{i}]") for i, a in enumerate(cmd_raw))
    cwd = contained_path(base, _str(d["cwd"], f"{role}.cwd"), f"{role}.cwd")
    if not cwd.is_dir():
        raise ConfigError(f"{role}.cwd: {cwd} is not a directory")

    env_raw = d.get("env", {})
    if not isinstance(env_raw, dict):
        raise ConfigError(f"{role}.env must be a mapping of strings")
    env: list[tuple[str, str]] = []
    for k, v in env_raw.items():
        if not isinstance(k, str) or not _ENV_NAME_RE.match(k):
            raise ConfigError(f"{role}.env: invalid variable name {k!r}")
        env.append((k, _str(v, f"{role}.env.{k}", allow_empty=True)))
    inherit_raw = d.get("inherit_env", list(DEFAULT_INHERIT_ENV))
    if not isinstance(inherit_raw, list):
        raise ConfigError(f"{role}.inherit_env must be a list of variable names")
    inherit = []
    for k in inherit_raw:
        if not isinstance(k, str) or not _ENV_NAME_RE.match(k):
            raise ConfigError(f"{role}.inherit_env: invalid variable name {k!r}")
        inherit.append(k)

    spec = ProgramSpec(
        role,
        name,
        command,
        command,
        cwd,
        str(cwd.relative_to(base)) or ".",
        tuple(env),
        tuple(inherit),
    )
    executable = resolve_executable(command[0], spec, base)
    return replace(spec, argv=(str(executable), *command[1:]))


def resolve_executable(argv0: str, spec: ProgramSpec, base: Path) -> Path:
    where = f"{spec.role}.command[0]"
    if os.sep in argv0 or (os.altsep and os.altsep in argv0):
        # Execute the path as written (made absolute) without following symlinks:
        # a venv's bin/python is a symlink whose identity matters. Containment is
        # checked on the fully resolved path.
        candidate = Path(
            os.path.abspath(Path(argv0) if Path(argv0).is_absolute() else spec.cwd / argv0)
        )
        if not Path(argv0).is_absolute() and not candidate.resolve().is_relative_to(base):
            raise ConfigError(f"{where}: {argv0!r} resolves outside the config directory")
        if not candidate.is_file():
            raise ConfigError(f"{where}: executable not found: {candidate}")
        if not os.access(candidate, os.X_OK):
            raise ConfigError(f"{where}: {candidate} is not executable")
        return candidate
    found = shutil.which(argv0, path=spec.child_env().get("PATH", ""))
    if found is None:
        raise ConfigError(f"{where}: executable {argv0!r} not found on the child PATH")
    return Path(found).absolute()


def _load_cases(raw: Any, base: Path, limits: Limits) -> tuple[tuple[Case, ...], str]:
    if isinstance(raw, list):
        items, source = raw, "inline"
    else:
        d = _check_keys(raw, "cases", {"file"}, set())
        rel = _str(d["file"], "cases.file")
        path = contained_path(base, rel, "cases.file")
        if not path.is_file():
            raise ConfigError(f"cases.file: {rel!r} not found")
        size = path.stat().st_size
        if size > limits.max_cases_file_bytes:
            raise ConfigError(
                f"cases.file is {size} bytes, above limits.max_cases_file_bytes "
                f"({limits.max_cases_file_bytes})"
            )
        items = _read_cases_file(path)
        source = str(path.relative_to(base))
    return _validate_cases(items, limits), source


def _read_cases_file(path: Path) -> list[Any]:
    data = path.read_bytes()
    if path.suffix == ".jsonl":
        items = []
        for lineno, line in enumerate(data.splitlines(), 1):
            if not line.strip():
                continue
            try:
                items.append(parse_strict(line))
            except JSONParseError as exc:
                raise ConfigError(f"{path.name}:{lineno}: {exc}") from None
        return items
    if path.suffix == ".json":
        try:
            value = parse_strict(data)
        except JSONParseError as exc:
            raise ConfigError(f"{path.name}: {exc}") from None
        if not isinstance(value, list):
            raise ConfigError(f"{path.name}: must contain a JSON array of cases")
        return value
    raise ConfigError("cases.file must end in .json or .jsonl")


def _validate_cases(items: list[Any], limits: Limits) -> tuple[Case, ...]:
    if not items:
        raise ConfigError("at least one test case is required")
    cases: list[Case] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        where = f"cases[{i}]"
        d = _check_keys(item, where, {"id", "input"}, {"description"})
        case_id = _str(d["id"], f"{where}.id")
        if not _ID_RE.match(case_id):
            raise ConfigError(f"{where}.id {case_id!r} must match {_ID_RE.pattern}")
        if case_id in seen:
            raise ConfigError(f"{where}.id {case_id!r} is duplicated")
        seen.add(case_id)
        try:
            ensure_json_value(d["input"], f"{where}.input")
        except ValueError as exc:
            raise ConfigError(str(exc)) from None
        size = len(encode_input(d["input"]))
        if size > limits.max_input_bytes:
            raise ConfigError(
                f"{where}.input is {size} bytes, above limits.max_input_bytes "
                f"({limits.max_input_bytes})"
            )
        desc = d.get("description", "")
        if not isinstance(desc, str):
            raise ConfigError(f"{where}.description must be a string")
        cases.append(Case(case_id, d["input"], desc))
    return tuple(cases)


def encode_input(value: Any) -> bytes:
    """The exact bytes written to each program's stdin for a case."""
    return (json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
