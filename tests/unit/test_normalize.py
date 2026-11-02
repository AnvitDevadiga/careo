import pytest
from hypothesis import given
from hypothesis import strategies as st

from careo.comparison import ComparisonPolicy, diff
from careo.normalize import NormalizationRule, apply_rules, parse_pointer


def mask(path, rid="r"):
    return NormalizationRule(rid, path, "mask", "test")


def test_mask_existing_field_only():
    value, counts = apply_rules({"t": "2026", "x": 1}, (mask("/t"),))
    assert value == {"t": "<careo:normalized:r:string>", "x": 1}
    assert counts == {"r": 1}
    value, counts = apply_rules({"x": 1}, (mask("/t"),))
    assert value == {"x": 1} and counts == {"r": 0}


def test_wildcards_over_arrays_and_objects():
    v = {"items": [{"id": "a", "n": 1}, {"id": "b", "n": 2}]}
    out, counts = apply_rules(v, (mask("/items/*/id"),))
    assert [i["id"] for i in out["items"]] == ["<careo:normalized:r:string>"] * 2
    assert [i["n"] for i in out["items"]] == [1, 2]
    assert counts["r"] == 2


def test_array_index_segments():
    out, _ = apply_rules([1, 2, 3], (mask("/1"),))
    assert out[0] == 1 and out[2] == 3 and out[1] != 2
    out, counts = apply_rules([1, 2], (mask("/01"),))  # leading zero is not an index
    assert out == [1, 2] and counts["r"] == 0


def test_mask_does_not_hide_presence_or_type_changes():
    rules = (mask("/t"),)
    a, _ = apply_rules({"t": "2026-01-01"}, rules)
    b, _ = apply_rules({}, rules)
    assert [d.kind for d in diff(a, b, ComparisonPolicy())] == ["missing_in_candidate"]
    c, _ = apply_rules({"t": None}, rules)
    assert [d.kind for d in diff(a, c, ComparisonPolicy())] == ["value_changed"]


def test_regex_only_rewrites_strings():
    rule = NormalizationRule("p", "/path", "regex", "tmp", r"/tmp/[^/]+", "<tmp>")
    out, counts = apply_rules({"path": "/tmp/abc123/file"}, (rule,))
    assert out == {"path": "<tmp>/file"} and counts == {"p": 1}
    out, counts = apply_rules({"path": 5}, (rule,))
    assert out == {"path": 5} and counts == {"p": 0}


def test_input_is_not_mutated():
    v = {"a": {"b": 1}}
    apply_rules(v, (mask("/a/b"),))
    assert v == {"a": {"b": 1}}


@pytest.mark.parametrize("bad", ["a", "", "/a~2"])
def test_invalid_pointers(bad):
    with pytest.raises(ValueError):
        parse_pointer(bad)


def test_pointer_unescape():
    assert parse_pointer("/a~1b/~0c") == ["a/b", "~c"]


@given(st.dictionaries(st.text(max_size=3), st.integers(), max_size=5))
def test_idempotent(v):
    rules = (mask("/*"),)
    once, _ = apply_rules(v, rules)
    twice, _ = apply_rules(once, rules)
    assert once == twice
