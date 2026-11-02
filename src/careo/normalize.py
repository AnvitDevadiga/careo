"""Explicit, path-scoped normalization of known nondeterministic fields.

Paths use JSON Pointer syntax (RFC 6901) with one extension: a segment of
exactly ``*`` matches every key of an object or every index of an array.
Normalization never adds or removes fields, so presence/absence is still
compared; it only rewrites values that already exist at a matching path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from careo.jsonvalue import JSONValue

WILDCARD = "*"
ACTIONS = ("mask", "regex")


@dataclass(frozen=True)
class NormalizationRule:
    id: str
    path: str
    action: str
    reason: str
    pattern: str | None = None
    replacement: str = ""

    def segments(self) -> list[str]:
        return parse_pointer(self.path)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "path": self.path,
            "action": self.action,
            "reason": self.reason,
        }
        if self.action == "regex":
            out["pattern"] = self.pattern
            out["replacement"] = self.replacement
        return out


def parse_pointer(path: str) -> list[str]:
    if not path.startswith("/"):
        raise ValueError(f"path {path!r} must be a JSON Pointer starting with '/'")
    segments = []
    for raw in path[1:].split("/"):
        if re.search(r"~(?![01])", raw):
            raise ValueError(f"path {path!r} contains an invalid '~' escape")
        segments.append(raw.replace("~1", "/").replace("~0", "~"))
    return segments


def escape_segment(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def json_type(value: JSONValue) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def mask_marker(rule_id: str, original: JSONValue) -> str:
    """Masked values keep their JSON type in the marker, so a masked field that
    changes type (e.g. string -> null) is still reported as a difference."""
    return f"<careo:normalized:{rule_id}:{json_type(original)}>"


def apply_rules(
    value: JSONValue, rules: tuple[NormalizationRule, ...]
) -> tuple[JSONValue, dict[str, int]]:
    """Return a normalized copy of ``value`` and how many values each rule changed."""
    counts: dict[str, int] = {}
    for rule in rules:
        compiled = re.compile(rule.pattern) if rule.action == "regex" and rule.pattern else None
        value, n = _apply(value, rule.segments(), rule, compiled)
        counts[rule.id] = n
    return value, counts


def _apply(
    value: JSONValue,
    segments: list[str],
    rule: NormalizationRule,
    compiled: re.Pattern[str] | None,
) -> tuple[JSONValue, int]:
    if not segments:
        return _transform(value, rule, compiled)
    head, rest = segments[0], segments[1:]
    if isinstance(value, dict):
        keys = list(value) if head == WILDCARD else ([head] if head in value else [])
        if not keys:
            return value, 0
        new = dict(value)
        total = 0
        for key in keys:
            new[key], n = _apply(value[key], rest, rule, compiled)
            total += n
        return new, total
    if isinstance(value, list):
        if head == WILDCARD:
            indices = list(range(len(value)))
        elif (
            head.isdigit() and (head == "0" or not head.startswith("0")) and int(head) < len(value)
        ):
            indices = [int(head)]
        else:
            return value, 0
        new_list = list(value)
        total = 0
        for i in indices:
            new_list[i], n = _apply(value[i], rest, rule, compiled)
            total += n
        return new_list, total
    return value, 0


def _transform(
    value: JSONValue, rule: NormalizationRule, compiled: re.Pattern[str] | None
) -> tuple[JSONValue, int]:
    if rule.action == "mask":
        return mask_marker(rule.id, value), 1
    # regex: only string values are rewritten; other types are left untouched so a
    # type change at a normalized path is still reported.
    if isinstance(value, str) and compiled is not None:
        new, n = compiled.subn(rule.replacement, value)
        return new, 1 if n else 0
    return value, 0
