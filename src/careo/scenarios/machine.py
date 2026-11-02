"""Hypothesis ``RuleBasedStateMachine`` generated from a scenario spec.

* each operation becomes a rule; operations that ``produces`` an entity target
  a Bundle of logical ids, and ``{bundle: x}`` arguments draw from it, so an
  operation that needs an entity only runs after one was created;
* every Hypothesis example is one scenario, starting from a reset whose result
  is independently verified (``Session.reset``);
* a behavioral difference raises ``Finding`` so Hypothesis shrinks the
  sequence. Every shrink attempt re-runs from a fresh verified reset;
* reset failures, both-sides-timeout and both-sides-down raise ``Abort``. They
  are recorded and stop the search; they never become a verdict of equivalence.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from hypothesis import HealthCheck, Phase, Verbosity, seed, settings
from hypothesis import strategies as st
from hypothesis.errors import Flaky
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    multiple,
    rule,
    run_state_machine_as_test,
)

from careo.scenarios.session import (
    ABORTS,
    REF,
    STEP_MATCH,
    ResetFailed,
    Session,
    StepResult,
)
from careo.scenarios.spec import Operation, ScenarioSpec, ValueSpec


class Finding(AssertionError):
    pass


class Abort(Exception):
    pass


@dataclass
class Recorder:
    runs: int = 0
    current: list[dict[str, Any]] = field(default_factory=list)
    current_results: list[StepResult] = field(default_factory=list)
    first_failure: list[dict[str, Any]] | None = None
    first_failure_signature: str | None = None
    last_failure: list[dict[str, Any]] | None = None
    last_failure_results: list[StepResult] = field(default_factory=list)
    abort: str | None = None
    abort_kind: str | None = None
    time_to_first_failure: float | None = None
    ops_to_first_failure: int | None = None
    started: float = field(default_factory=time.monotonic)
    budget_exhausted: bool = False
    search_operations: int | None = None  # operations spent before the first difference


_SLOT = "$slot"


def _strategy(v: ValueSpec, slots: list[str]) -> Any:
    """Strategy for a value; bundle references become slot placeholders that are
    filled from the rule's Bundle arguments."""
    if v.kind == "const":
        return st.just(v.arg)
    if v.kind == "sampled_from":
        return st.sampled_from(list(v.arg))
    if v.kind == "integers":
        return st.integers(v.arg[0], v.arg[1])
    if v.kind == "text":
        lo, hi, alphabet = v.arg
        return (
            st.text(alphabet=alphabet, min_size=lo, max_size=hi)
            if alphabet
            else st.text(min_size=lo, max_size=hi)
        )
    if v.kind == "booleans":
        return st.booleans()
    if v.kind == "bundle":
        slots.append(v.arg)
        return st.just({_SLOT: len(slots) - 1})
    if v.kind == "one_of":
        return st.one_of(*[_strategy(x, slots) for x in v.arg])
    raise AssertionError(v.kind)


def _mapping(m: dict[str, ValueSpec], slots: list[str]) -> Any:
    required = {k: _strategy(v, slots) for k, v in m.items() if v.kind != "maybe_absent"}
    optional = {k: _strategy(v.arg, slots) for k, v in m.items() if v.kind == "maybe_absent"}
    return st.fixed_dictionaries(required, optional=optional)


def op_strategy(op: Operation) -> tuple[Any, list[str]]:
    slots: list[str] = []
    parts: dict[str, Any] = {
        "params": _mapping(op.params, slots),
        "query": _mapping(op.query, slots),
    }
    if isinstance(op.body, dict):
        parts["body"] = _mapping(op.body, slots)
    elif isinstance(op.body, ValueSpec):
        if op.body.kind == "maybe_absent":
            parts_strategy = st.fixed_dictionaries(
                {k: v for k, v in parts.items()}, optional={"body": _strategy(op.body.arg, slots)}
            )
            return parts_strategy, slots
        parts["body"] = _strategy(op.body, slots)
    return st.fixed_dictionaries(parts), slots


def _fill(value: Any, refs: list[str]) -> Any:
    if isinstance(value, dict):
        if set(value) == {_SLOT}:
            return {REF: refs[value[_SLOT]]}
        return {k: _fill(v, refs) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, refs) for v in value]
    return value


def build_machine(
    spec: ScenarioSpec, session: Session, rec: Recorder
) -> type[RuleBasedStateMachine]:
    bundles = {e: Bundle(e) for e in spec.entities}
    max_ops = spec.generation.max_operations

    def _over_budget() -> bool:
        # The budget limits the SEARCH for a first difference. Once one is found,
        # shrinking and verification must be able to replay it, so they are not
        # limited (their cost is reported separately).
        return (
            max_ops is not None
            and rec.first_failure is None
            and session.operations_executed >= max_ops
        )

    def __init__(self: Any) -> None:
        RuleBasedStateMachine.__init__(self)
        rec.runs += 1
        rec.current = []
        rec.current_results = []
        if rec.abort_kind == "RESET_FAILED":
            raise Abort(rec.abort)
        if rec.abort or _over_budget():
            rec.budget_exhausted = True
            return  # no requests will be made, so no reset is needed
        try:
            session.reset()
        except ResetFailed as exc:
            rec.abort = str(exc)
            rec.abort_kind = "RESET_FAILED"
            raise Abort(rec.abort) from None

    attrs: dict[str, Any] = {"__init__": __init__}
    for op in spec.operations:
        strategy, slots = op_strategy(op)
        kwargs: dict[str, Any] = {f"ref{i}": bundles[e] for i, e in enumerate(slots)}
        kwargs["parts"] = strategy

        def fn(self: Any, _op: Operation = op, _n: int = len(slots), **kw: Any) -> Any:
            if rec.abort:
                # Stop the search quickly, raising from the same place as the original abort.
                raise Abort(rec.abort)
            if _over_budget():
                rec.budget_exhausted = True
                return multiple() if _op.produces is not None else None
            refs = [kw[f"ref{i}"] for i in range(_n)]
            step: dict[str, Any] = {"op": _op.name, **_fill(kw["parts"], refs)}
            rec.current.append(step)
            result = session.execute(len(rec.current) - 1, step)
            rec.current_results.append(result)
            if result.verdict in ABORTS:
                rec.abort = "; ".join(result.notes) or result.verdict
                rec.abort_kind = result.verdict
                raise Abort(rec.abort)
            if result.verdict != STEP_MATCH:
                if rec.first_failure is None:
                    rec.first_failure = list(rec.current)
                    rec.first_failure_signature = result.signature()
                    rec.time_to_first_failure = time.monotonic() - rec.started
                    rec.ops_to_first_failure = session.operations_executed
                    rec.search_operations = session.operations_executed
                rec.last_failure = list(rec.current)
                rec.last_failure_results = list(rec.current_results)
                raise Finding(f"{result.verdict} at step {result.index} ({_op.name})")
            if _op.produces is not None:
                return result.produced if result.produced is not None else multiple()
            return None

        fn.__name__ = f"op_{op.name}"
        target = bundles[op.produces.entity] if op.produces else None
        make_rule: Any = rule
        decorated: Any = (
            make_rule(target=target, **kwargs)(fn)
            if target is not None
            else make_rule(**kwargs)(fn)
        )
        attrs[fn.__name__] = decorated
    return type("CareoMachine", (RuleBasedStateMachine,), attrs)


def search(spec: ScenarioSpec, session: Session, rec: Recorder) -> str | None:
    """Run Hypothesis. Returns 'flaky' if Hypothesis could not reproduce the
    failure while shrinking, else None."""
    g = spec.generation
    machine = build_machine(spec, session, rec)
    phases = [Phase.explicit, Phase.generate] + ([Phase.shrink] if g.shrink else [])
    max_examples = g.max_scenarios
    if g.max_operations is not None:
        # The operation budget is what stops the search; the example count is just a ceiling.
        max_examples = max(max_examples, g.max_operations)
    cfg = settings(
        max_examples=max_examples,
        stateful_step_count=g.max_steps,
        deadline=None,
        database=None,
        derandomize=False,
        phases=phases,
        suppress_health_check=list(HealthCheck),
        verbosity=Verbosity.quiet,
        report_multiple_bugs=False,
        print_blob=False,
    )
    try:
        run_state_machine_as_test(seed(g.seed)(machine), settings=cfg)
    except Flaky:
        return "flaky"
    except (Finding, Abort):
        return None
    except BaseExceptionGroup:
        return None
    return None
