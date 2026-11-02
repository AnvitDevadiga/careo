"""Structural comparison of JSON values under an explicit policy."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

from careo.jsonvalue import JSONValue, canonical
from careo.normalize import escape_segment, json_type

_MISSING: Any = object()

# Difference kinds
VALUE_CHANGED = "value_changed"
TYPE_CHANGED = "type_changed"
MISSING_IN_CANDIDATE = "missing_in_candidate"
EXTRA_IN_CANDIDATE = "extra_in_candidate"
NULL_VS_ABSENT = "null_vs_absent"
ARRAY_ORDER = "array_order"
ARRAY_ELEMENTS = "array_elements"
EXIT_CODE = "exit_code"
STDERR = "stderr"


@dataclass(frozen=True)
class ComparisonPolicy:
    accepted_exit_codes: tuple[int, ...] = (0,)
    stderr: str = "ignore"  # "ignore" | "exact"
    array_order: str = "strict"  # "strict" | "ignore"
    numbers: str = "strict"  # "strict": 1 != 1.0 | "numeric": compare by value
    float_abs_tolerance: float = 0.0
    repeat: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted_exit_codes": list(self.accepted_exit_codes),
            "stderr": self.stderr,
            "array_order": self.array_order,
            "numbers": self.numbers,
            "float_abs_tolerance": self.float_abs_tolerance,
            "repeat": self.repeat,
        }


@dataclass(frozen=True)
class Difference:
    path: str
    kind: str
    reference: Any = _MISSING
    candidate: Any = _MISSING
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"path": self.path, "kind": self.kind}
        if self.reference is not _MISSING:
            out["reference"] = self.reference
        if self.candidate is not _MISSING:
            out["candidate"] = self.candidate
        if self.detail:
            out["detail"] = self.detail
        return out


def _type_class(value: JSONValue, policy: ComparisonPolicy) -> str:
    t = json_type(value)
    if policy.numbers == "numeric" and t in ("integer", "float"):
        return "number"
    return t


def _numbers_equal(a: int | float, b: int | float, policy: ComparisonPolicy) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        tol = policy.float_abs_tolerance
        if tol > 0:
            try:
                return abs(float(a) - float(b)) <= tol
            except OverflowError:
                return a == b
        if isinstance(a, float) and isinstance(b, float) and a == 0.0 and b == 0.0:
            # Strict: -0.0 and 0.0 serialize differently and are observably distinct.
            return math.copysign(1.0, a) == math.copysign(1.0, b)
    return a == b


def diff(
    reference: JSONValue, candidate: JSONValue, policy: ComparisonPolicy, path: str = ""
) -> list[Difference]:
    """Return every difference between two JSON values (empty list == equal)."""
    out: list[Difference] = []
    _diff(reference, candidate, policy, path, out)
    return out


def _diff(
    ref: JSONValue, cand: JSONValue, policy: ComparisonPolicy, path: str, out: list[Difference]
) -> None:
    rt, ct = _type_class(ref, policy), _type_class(cand, policy)
    if rt != ct:
        out.append(
            Difference(path, TYPE_CHANGED, ref, cand, f"{json_type(ref)} -> {json_type(cand)}")
        )
        return
    if isinstance(ref, dict):
        _diff_objects(ref, cand, policy, path, out)
    elif isinstance(ref, list):
        _diff_arrays(ref, cand, policy, path, out)
    elif rt in ("integer", "float", "number"):
        if not _numbers_equal(ref, cand, policy):
            out.append(Difference(path, VALUE_CHANGED, ref, cand))
    elif ref != cand:
        out.append(Difference(path, VALUE_CHANGED, ref, cand))


def _diff_objects(
    ref: dict[str, Any],
    cand: dict[str, Any],
    policy: ComparisonPolicy,
    path: str,
    out: list[Difference],
) -> None:
    keys = list(ref) + [k for k in cand if k not in ref]
    for key in keys:
        sub = f"{path}/{escape_segment(key)}"
        if key not in cand:
            kind = NULL_VS_ABSENT if ref[key] is None else MISSING_IN_CANDIDATE
            out.append(Difference(sub, kind, reference=ref[key]))
        elif key not in ref:
            kind = NULL_VS_ABSENT if cand[key] is None else EXTRA_IN_CANDIDATE
            out.append(Difference(sub, kind, candidate=cand[key]))
        else:
            _diff(ref[key], cand[key], policy, sub, out)


def _diff_arrays(
    ref: list[Any], cand: list[Any], policy: ComparisonPolicy, path: str, out: list[Difference]
) -> None:
    ref_c = Counter(canonical(x) for x in ref)
    cand_c = Counter(canonical(x) for x in cand)
    if policy.array_order == "ignore":
        if ref_c != cand_c:
            out.append(
                Difference(
                    path,
                    ARRAY_ELEMENTS,
                    sorted((ref_c - cand_c).elements()),
                    sorted((cand_c - ref_c).elements()),
                    "multiset differs; values are canonical JSON of elements only in that side",
                )
            )
        return
    if ref_c == cand_c and len(ref) > 1:
        if [canonical(x) for x in ref] != [canonical(x) for x in cand]:
            out.append(Difference(path, ARRAY_ORDER, ref, cand, "same elements, different order"))
        return
    for i in range(min(len(ref), len(cand))):
        _diff(ref[i], cand[i], policy, f"{path}/{i}", out)
    for i in range(len(cand), len(ref)):
        out.append(Difference(f"{path}/{i}", MISSING_IN_CANDIDATE, reference=ref[i]))
    for i in range(len(ref), len(cand)):
        out.append(Difference(f"{path}/{i}", EXTRA_IN_CANDIDATE, candidate=cand[i]))
