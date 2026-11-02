"""Shrink correctness: minimize_sequence only keeps reductions that reproduce."""

from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from careo.scenarios.runner import minimize_sequence
from careo.scenarios.session import STEP_MATCH, STEP_MISMATCH, SequenceResult, StepResult


@dataclass
class FakeSession:
    """Fails iff the sequence contains the subsequence `needle` (in order)."""

    needle: list[str]
    replays: int = 0

    def run_sequence(self, steps):
        self.replays += 1
        it = iter(s["op"] for s in steps)
        hit = all(any(op == n for op in it) for n in self.needle)
        res = [StepResult(i, s, STEP_MATCH, {}, {}, [], []) for i, s in enumerate(steps)]
        if hit and res:
            res[-1].verdict = STEP_MISMATCH
            res[-1].step = {"op": "FAIL"}
            return SequenceResult(STEP_MISMATCH, res)
        return SequenceResult(STEP_MATCH, res)


def sig():
    return StepResult(0, {"op": "FAIL"}, STEP_MISMATCH, {}, {}, [], []).signature()


def test_reduces_to_needle():
    steps = [{"op": x} for x in "xaxxbxxxcx"]
    out, _ = minimize_sequence(FakeSession(list("abc")), steps, sig())
    assert [s["op"] for s in out] == list("abc")


@settings(max_examples=150, deadline=None)
@given(
    st.lists(st.sampled_from("abcxy"), min_size=1, max_size=25),
    st.lists(st.sampled_from("abc"), min_size=1, max_size=3),
)
def test_result_still_fails_and_is_a_subsequence(ops, needle):
    fs = FakeSession(needle)
    steps = [{"op": o, "i": i} for i, o in enumerate(ops)]
    if fs.run_sequence(steps).verdict == STEP_MATCH:
        return
    out, _ = minimize_sequence(fs, steps, sig())
    assert fs.run_sequence(out).verdict == STEP_MISMATCH  # still reproduces
    idx = [s["i"] for s in out]
    assert idx == sorted(idx) and len(out) <= len(steps)  # an order-preserving subsequence
    assert len(out) == len(needle)  # 1-minimal for this failure model


def test_budget_is_respected():
    fs = FakeSession(list("ab"))
    steps = [{"op": x} for x in "a" + "x" * 50 + "b"]
    _, replays = minimize_sequence(fs, steps, sig(), max_replays=5)
    assert replays <= 5
