"""Strict JSON parsing and canonical serialization."""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any

JSONValue = Any  # None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]


class JSONParseError(ValueError):
    """Raised when bytes are not a single, standard, unambiguous JSON value."""


def _reject_constant(name: str) -> Any:
    raise JSONParseError(f"non-standard JSON constant {name!r} (NaN/Infinity are not JSON)")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JSONParseError(f"duplicate object key {key!r} (ambiguous JSON)")
        result[key] = value
    return result


def _parse_float(text: str) -> float:
    value = float(text)
    if math.isinf(value):
        raise JSONParseError(f"number {text} is out of range for a 64-bit float")
    # Careo compares numbers at double precision. Refuse literals whose exact value
    # a double cannot hold (e.g. 0.10000000000000000001), rather than silently
    # collapsing two different outputs to the same float and calling them equal.
    if Decimal(repr(value)) != Decimal(text):
        raise JSONParseError(
            f"number {text} cannot be represented exactly as a 64-bit float "
            "(Careo would lose precision comparing it)"
        )
    return value


_DECODER = json.JSONDecoder(
    parse_constant=_reject_constant,
    object_pairs_hook=_no_duplicate_keys,
    parse_float=_parse_float,
)


def parse_strict(data: bytes) -> JSONValue:
    """Parse UTF-8 bytes as exactly one standard JSON value.

    Rejects invalid UTF-8, empty input, NaN/Infinity, duplicate object keys and
    trailing garbage. Surrounding whitespace is allowed.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JSONParseError(f"output is not valid UTF-8: {exc}") from None
    if text.startswith("﻿"):
        raise JSONParseError("output starts with a byte-order mark")
    if not text.strip():
        raise JSONParseError("output is empty")
    try:
        return _DECODER.decode(text)
    except JSONParseError:
        raise
    except json.JSONDecodeError as exc:
        raise JSONParseError(f"invalid JSON: {exc}") from None
    except RecursionError:
        raise JSONParseError("JSON nesting too deep") from None


def canonical(value: JSONValue) -> str:
    """Deterministic JSON text: sorted keys, no whitespace, unicode preserved."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def ensure_json_value(value: Any, where: str) -> None:
    """Raise ValueError if ``value`` cannot be represented as standard JSON."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{where}: NaN/Infinity are not valid JSON")
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            ensure_json_value(item, f"{where}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{where}: object key {key!r} is not a string")
            ensure_json_value(item, f"{where}.{key}")
        return
    raise ValueError(f"{where}: value of type {type(value).__name__} is not valid JSON")
