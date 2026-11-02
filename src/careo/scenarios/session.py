"""Paired execution: one step goes to the reference, then the candidate, and the
two observations are compared after logical-ID translation and normalization.

Logical identity
----------------
An operation that ``produces`` an entity registers a logical id (``booking#1``)
mapped to each side's actual id (e.g. 101 and 8002). Later steps refer to the
logical id, and each side receives its own actual id. Before comparison, values
at declared id pointers (and declared id columns in database rows) are
translated actual -> logical. A value that maps to no logical id is left
untouched, so unmapped ids still differ visibly; ids are never ignored wholesale.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from careo.adapters.http import OK, TIMEOUT, Endpoint, HttpResponse, build_path, request
from careo.adapters.sqlite import SqliteState, StateError
from careo.comparison import ComparisonPolicy, Difference, diff
from careo.config import ConfigError
from careo.execution.service import ManagedService, ServiceError, free_port
from careo.jsonvalue import canonical
from careo.normalize import WILDCARD, apply_rules, parse_pointer
from careo.scenarios.spec import IdPath, Operation, ScenarioSpec, ServiceSpec

REF = "$ref"

# step-level verdicts
STEP_MATCH = "MATCH"
STEP_MISMATCH = "MISMATCH"
STEP_CANDIDATE_FAILED = "CANDIDATE_FAILED"
STEP_REFERENCE_FAILED = "REFERENCE_FAILED"
STEP_BOTH_TIMEOUT = "BOTH_TIMEOUT"
STEP_BOTH_FAILED = "BOTH_FAILED"

FINDINGS = (STEP_MISMATCH, STEP_CANDIDATE_FAILED, STEP_REFERENCE_FAILED)
ABORTS = (STEP_BOTH_TIMEOUT, STEP_BOTH_FAILED)


class ResetFailed(Exception):
    """Equivalent initial state could not be established or verified."""


class SetupError(Exception):
    """A service or state adapter could not be prepared."""


# --------------------------------------------------------------------------- pointers


def _get_all(value: Any, segments: list[str]) -> list[tuple[list[str | int], Any]]:
    """All (path, value) pairs matching a pointer with '*' wildcards."""
    if not segments:
        return [([], value)]
    head, rest = segments[0], segments[1:]
    out: list[tuple[list[str | int], Any]] = []
    if isinstance(value, dict):
        keys = list(value) if head == WILDCARD else ([head] if head in value else [])
        for k in keys:
            out += [([k, *p], v) for p, v in _get_all(value[k], rest)]
    elif isinstance(value, list):
        if head == WILDCARD:
            idx = list(range(len(value)))
        elif (
            head.isdigit() and (head == "0" or not head.startswith("0")) and int(head) < len(value)
        ):
            idx = [int(head)]
        else:
            idx = []
        for i in idx:
            out += [([i, *p], v) for p, v in _get_all(value[i], rest)]
    return out


def _set(value: Any, path: list[str | int], new: Any) -> Any:
    if not path:
        return new
    head, rest = path[0], path[1:]
    if isinstance(value, dict):
        copy = dict(value)
        copy[head] = _set(value[head], rest, new)
        return copy
    copy_list = list(value)
    copy_list[head] = _set(value[head], rest, new)  # type: ignore[index]
    return copy_list


def _id_key(v: Any) -> str:
    # ids are matched by JSON value, so 7 and "7" are different ids
    return canonical(v)


# --------------------------------------------------------------------------- sides


@dataclass
class Side:
    role: str
    spec: ServiceSpec
    endpoint: Endpoint
    state: SqliteState | None
    service: ManagedService | None
    forward: dict[str, Any] = field(default_factory=dict)  # logical -> actual
    reverse: dict[str, dict[str, str]] = field(default_factory=dict)  # entity -> key -> logical

    def clear_ids(self) -> None:
        self.forward.clear()
        self.reverse.clear()

    def logical_for(self, entity: str, actual: Any) -> str | None:
        return self.reverse.get(entity, {}).get(_id_key(actual))

    def bind(self, entity: str, logical: str, actual: Any) -> None:
        self.forward[logical] = actual
        self.reverse.setdefault(entity, {})[_id_key(actual)] = logical


def resolve_argv(spec: ServiceSpec, base: Path, substitutions: dict[str, str]) -> list[str]:
    argv = []
    for a in spec.command or ():
        for k, v in substitutions.items():
            a = a.replace("{" + k + "}", v)
        argv.append(a)
    exe = argv[0]
    if os.sep in exe:
        p = Path(exe) if Path(exe).is_absolute() else spec.cwd / exe
        p = Path(os.path.abspath(p))
        if not Path(exe).is_absolute() and not p.resolve().is_relative_to(base):
            raise ConfigError(
                f"services.{spec.role}.command[0] resolves outside the spec directory"
            )
        if not (p.is_file() and os.access(p, os.X_OK)):
            raise ConfigError(f"services.{spec.role}.command[0]: {p} is not an executable file")
        argv[0] = str(p)
    else:
        found = shutil.which(exe, path=spec.child_env({}).get("PATH", ""))
        if not found:
            raise ConfigError(f"services.{spec.role}.command[0]: {exe!r} not found on PATH")
        argv[0] = str(Path(found).absolute())
    return argv


# --------------------------------------------------------------------------- results


@dataclass
class StepResult:
    index: int
    step: dict[str, Any]
    verdict: str
    reference: dict[str, Any]
    candidate: dict[str, Any]
    differences: list[Difference]
    notes: list[str]
    produced: str | None = None

    def signature(self) -> str:
        return canonical(
            [
                self.step["op"],
                self.verdict,
                sorted({(d.path, d.kind) for d in self.differences}),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "step": self.step,
            "verdict": self.verdict,
            "differences": [d.to_dict() for d in self.differences],
            "notes": self.notes,
            "produced": self.produced,
            "reference": self.reference,
            "candidate": self.candidate,
        }


@dataclass
class SequenceResult:
    verdict: str  # MATCH | a FINDINGS verdict | an ABORTS verdict
    steps: list[StepResult]
    reset_error: str | None = None

    @property
    def failing(self) -> StepResult | None:
        return self.steps[-1] if self.steps and self.steps[-1].verdict != STEP_MATCH else None

    def signature(self) -> str | None:
        f = self.failing
        return f"{f.index}:{f.signature()}" if f else None


# --------------------------------------------------------------------------- session


class Session:
    def __init__(self, spec: ScenarioSpec, run_dir: Path) -> None:
        self.spec = spec
        self.run_dir = run_dir
        self.sides: list[Side] = []
        self.operations_executed = 0
        self.requests_sent = 0
        self.resets = 0
        self.started = False
        self.normalization_totals = {r.id: 0 for r in spec.rules}
        self.id_translations = 0

    # lifecycle -----------------------------------------------------------
    def prepare(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        for svc in (self.spec.reference, self.spec.candidate):
            state = None
            db = svc.db
            if self.spec.state:
                if db is None:
                    db = (self.run_dir / "state" / f"{svc.role}.db").resolve()
                st = self.spec.state
                state = SqliteState(db, st.schema_sql, st.fixture_sql, st.tables)
                try:
                    state.prepare()
                except StateError as exc:
                    raise SetupError(str(exc)) from None
            service = None
            if svc.managed:
                port = free_port()
                endpoint = svc.endpoint(port)
                subs = {"port": str(port), "db": str(db) if db else ""}
                argv = resolve_argv(svc, self.spec.base_dir, subs)
                env = svc.child_env({"CAREO_ROLE": svc.role})
                service = ManagedService(
                    svc.name,
                    argv,
                    svc.cwd,
                    env,
                    endpoint,
                    svc.ready_path,
                    svc.ready_timeout,
                    self.run_dir / "logs" / f"{svc.role}.log",
                )
            else:
                endpoint = svc.endpoint(None)
            self.sides.append(Side(svc.role, svc, endpoint, state, service))
        try:
            for side in self.sides:
                if side.service:
                    side.service.start()
        except ServiceError as exc:
            self.teardown()
            raise SetupError(str(exc)) from None
        self.started = True

    def teardown(self) -> None:
        for side in self.sides:
            if side.service:
                side.service.stop()
            if side.state:
                side.state.teardown()

    @property
    def ref(self) -> Side:
        return self.sides[0]

    @property
    def cand(self) -> Side:
        return self.sides[1]

    def reset(self) -> None:
        """Bring both sides to the verified fixture state, or raise ResetFailed."""
        self.resets += 1
        st = self.spec.state
        for side in self.sides:
            side.clear_ids()
            try:
                restart = side.service is not None and (
                    (st is not None and st.reset == "restart") or not side.service.alive()
                )
                if restart and side.service:
                    side.service.stop()
                if side.state:
                    side.state.reset()
                if restart and side.service:
                    side.service.start()
            except (StateError, ServiceError) as exc:
                raise ResetFailed(f"{side.role}: {exc}") from None
            if st and st.reset_request:
                method, path = st.reset_request
                r = request(
                    side.endpoint,
                    method,
                    side.endpoint.base_path + path,
                    send_body=method in ("POST", "PUT", "PATCH"),
                    body={},
                    timeout_seconds=self.spec.timeout_seconds,
                    max_response_bytes=self.spec.max_response_bytes,
                )
                if r.status != OK or r.code is None or not 200 <= r.code < 300:
                    raise ResetFailed(f"{side.role}: reset request failed: {r.error or r.code}")
            if side.state:
                problem = side.state.verify_reset()
                if problem:
                    raise ResetFailed(f"{side.role}: {problem}")

    # execution -----------------------------------------------------------
    def _resolve(self, side: Side, value: Any) -> Any:
        if isinstance(value, dict):
            if set(value) == {REF}:
                return side.forward[value[REF]]
            return {k: self._resolve(side, v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve(side, v) for v in value]
        return value

    def _send(self, side: Side, op: Operation, step: dict[str, Any]) -> HttpResponse:
        params = self._resolve(side, step.get("params", {}))
        query = self._resolve(side, step.get("query", {}))
        has_body = "body" in step
        body = self._resolve(side, step.get("body"))
        path = build_path(side.endpoint, op.path, params)
        self.requests_sent += 1
        return request(
            side.endpoint,
            op.method,
            path,
            query=query or None,
            body=body,
            send_body=has_body,
            timeout_seconds=self.spec.timeout_seconds,
            max_response_bytes=self.spec.max_response_bytes,
        )

    def _translate(self, side: Side, value: Any, id_paths: tuple[IdPath, ...]) -> tuple[Any, int]:
        n = 0
        for ip in id_paths:
            for path, actual in _get_all(value, parse_pointer(ip.pointer)):
                logical = side.logical_for(ip.entity, actual)
                if logical is not None:
                    value = _set(value, path, f"<{logical}>")
                    n += 1
        return value, n

    def _translate_rows(self, side: Side, snap: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        st = self.spec.state
        assert st is not None
        for t in st.tables:
            rows = []
            for row in snap.get(t.name, []):
                new = dict(row)
                for col, ent in t.id_columns.items():
                    if col in new:
                        logical = side.logical_for(ent, new[col])
                        if logical is not None:
                            new[col] = f"<{logical}>"
                            self.id_translations += 1
                rows.append(new)
            out[t.name] = rows
        return out

    def compare_state(self) -> list[Difference]:
        st = self.spec.state
        if not st:
            return []
        try:
            r = self._translate_rows(self.ref, self.ref.state.snapshot())  # type: ignore[union-attr]
            c = self._translate_rows(self.cand, self.cand.state.snapshot())  # type: ignore[union-attr]
        except Exception as exc:  # sqlite3.Error and friends
            return [Difference("<db>", "state_unreadable", detail=f"{type(exc).__name__}: {exc}")]
        diffs: list[Difference] = []
        for t in st.tables:
            policy = ComparisonPolicy(
                array_order="ignore" if t.unordered else "strict",
                numbers=self.spec.policy.numbers,
                float_abs_tolerance=self.spec.policy.float_abs_tolerance,
            )
            diffs += diff(r[t.name], c[t.name], policy, path=f"<db>/{t.name}")
        return diffs

    def _observe(self, side: Side, op: Operation, resp: HttpResponse) -> tuple[dict[str, Any], Any]:
        obs = resp.to_dict()
        if resp.status != OK:
            return obs, None
        if not resp.body_is_json:
            return obs, {"$text": resp.body_text}
        body, n = self._translate(side, resp.body_json, op.all_id_paths())
        self.id_translations += n
        body, counts = apply_rules(body, self.spec.rules)
        for k, v in counts.items():
            self.normalization_totals[k] += v
        if n or any(counts.values()):
            obs["compared_body"] = body
        if counts and any(counts.values()):
            obs["normalizations_applied"] = {k: v for k, v in counts.items() if v}
        return obs, body

    def execute(self, index: int, step: dict[str, Any]) -> StepResult:
        op = self.spec.op(step["op"])
        r_resp = self._send(self.ref, op, step)
        c_resp = self._send(self.cand, op, step)
        self.operations_executed += 1
        notes: list[str] = []
        diffs: list[Difference] = []

        # Register produced entities BEFORE translating, so the new ids map to the
        # same logical id on both sides (101 vs 8002 -> <booking#1>).
        produced = None
        if op.produces and r_resp.status == OK and c_resp.status == OK:
            produced, problem = self._produce(
                op.produces,
                r_resp.body_json if r_resp.body_is_json else None,
                c_resp.body_json if c_resp.body_is_json else None,
            )
            if problem:
                diffs.append(problem)
        r_obs, r_body = self._observe(self.ref, op, r_resp)
        c_obs, c_body = self._observe(self.cand, op, c_resp)

        if r_resp.status != OK or c_resp.status != OK:
            if r_resp.status != OK and c_resp.status != OK:
                verdict = (
                    STEP_BOTH_TIMEOUT
                    if TIMEOUT in (r_resp.status, c_resp.status)
                    else STEP_BOTH_FAILED
                )
                notes.append(
                    f"both sides failed (reference: {r_resp.error}; candidate: {c_resp.error}); "
                    "this is never evidence of equivalence"
                )
            elif r_resp.status != OK:
                verdict = STEP_REFERENCE_FAILED
                notes.append(f"reference {r_resp.status}: {r_resp.error}")
            else:
                verdict = STEP_CANDIDATE_FAILED
                notes.append(f"candidate {c_resp.status}: {c_resp.error}")
            return StepResult(index, step, verdict, r_obs, c_obs, [], notes)

        if r_resp.code != c_resp.code:
            diffs.append(Difference("<status>", "status_changed", r_resp.code, c_resp.code))
        for h in self.spec.compare_headers:
            rv, cv = r_resp.headers.get(h), c_resp.headers.get(h)
            if rv != cv:
                diffs.append(Difference(f"<header>/{h}", "header_changed", rv, cv))
        diffs += diff(r_body, c_body, self.spec.policy, path="<body>")
        if op.expect_status:
            if r_resp.code not in op.expect_status:
                notes.append(f"reference returned {r_resp.code}, outside declared expect_status")
            if c_resp.code not in op.expect_status:
                diffs.append(
                    Difference(
                        "<status>",
                        "contract_violation",
                        list(op.expect_status),
                        c_resp.code,
                        "candidate status outside expect_status",
                    )
                )

        st = self.spec.state
        if st and st.compare == "each_step":
            diffs += self.compare_state()
        verdict = STEP_MISMATCH if diffs else STEP_MATCH
        return StepResult(index, step, verdict, r_obs, c_obs, diffs, notes, produced)

    def _produce(
        self, ip: IdPath, r_body: Any, c_body: Any
    ) -> tuple[str | None, Difference | None]:
        seg = parse_pointer(ip.pointer)
        r_vals = _get_all(r_body, seg) if r_body is not None else []
        c_vals = _get_all(c_body, seg) if c_body is not None else []
        if not r_vals and not c_vals:
            return None, None  # nothing created on either side (e.g. both rejected)
        if len(r_vals) != 1 or len(c_vals) != 1:
            return None, Difference(
                f"<body>{ip.pointer}",
                "identity_missing",
                detail="produced id not found on one side",
            )
        r_actual, c_actual = r_vals[0][1], c_vals[0][1]
        r_log = self.ref.logical_for(ip.entity, r_actual)
        c_log = self.cand.logical_for(ip.entity, c_actual)
        if r_log is None and c_log is None:
            n = len(self.ref.reverse.get(ip.entity, {})) + 1
            logical = f"{ip.entity}#{n}"
            self.ref.bind(ip.entity, logical, r_actual)
            self.cand.bind(ip.entity, logical, c_actual)
            return logical, None
        if r_log == c_log:
            return r_log, None  # both returned the same existing entity (idempotent create)
        return None, Difference(
            f"<body>{ip.pointer}",
            "identity_divergence",
            r_log or f"new id {r_actual!r}",
            c_log or f"new id {c_actual!r}",
            "the two sides disagree about which logical entity this is",
        )

    # whole sequences ------------------------------------------------------
    def run_sequence(self, steps: list[dict[str, Any]]) -> SequenceResult:
        """Reset, verify, then execute ``steps`` until the first non-MATCH step."""
        try:
            self.reset()
        except ResetFailed as exc:
            return SequenceResult("RESET_FAILED", [], str(exc))
        results: list[StepResult] = []
        for i, step in enumerate(steps):
            try:
                resolved_ok = all(self._refs_known(step))
            except KeyError:
                resolved_ok = False
            if not resolved_ok:
                return SequenceResult(
                    "INVALID_SEQUENCE",
                    results,
                    f"step {i} refers to an entity that was never produced",
                )
            res = self.execute(i, step)
            results.append(res)
            if res.verdict != STEP_MATCH:
                return SequenceResult(res.verdict, results)
        st = self.spec.state
        if st and st.compare == "end" and results:
            d = self.compare_state()
            if d:
                last = results[-1]
                last.differences += d
                last.verdict = STEP_MISMATCH
                return SequenceResult(STEP_MISMATCH, results)
        return SequenceResult(STEP_MATCH, results)

    def _refs_known(self, step: dict[str, Any]) -> list[bool]:
        found: list[bool] = []

        def walk(v: Any) -> None:
            if isinstance(v, dict):
                if set(v) == {REF}:
                    found.append(v[REF] in self.ref.forward and v[REF] in self.cand.forward)
                else:
                    for x in v.values():
                        walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)

        for key in ("params", "query", "body"):
            walk(step.get(key))
        return found


def now() -> float:
    return time.monotonic()
