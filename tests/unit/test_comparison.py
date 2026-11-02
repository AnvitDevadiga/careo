from hypothesis import given, settings
from hypothesis import strategies as st

from careo.comparison import ComparisonPolicy, diff
from careo.jsonvalue import canonical

STRICT = ComparisonPolicy()


def kinds(a, b, policy=STRICT):
    return [(d.path, d.kind) for d in diff(a, b, policy)]


def test_identical_values_have_no_differences():
    v = {"a": [1, 2, {"b": None}], "c": "ü"}
    assert diff(v, v, STRICT) == []


def test_changed_value_and_type():
    assert kinds({"a": 1}, {"a": 2}) == [("/a", "value_changed")]
    assert kinds({"a": 1}, {"a": "1"}) == [("/a", "type_changed")]


def test_bool_is_not_int():
    assert kinds(1, True) == [("", "type_changed")]
    assert kinds(0, False) == [("", "type_changed")]


def test_int_vs_float_strict_and_numeric():
    assert kinds(1, 1.0) == [("", "type_changed")]
    assert kinds(1, 1.0, ComparisonPolicy(numbers="numeric")) == []
    assert kinds(1, 1.5, ComparisonPolicy(numbers="numeric")) == [("", "value_changed")]


def test_negative_zero_is_distinct_under_strict():
    assert kinds(0.0, -0.0) == [("", "value_changed")]


def test_float_tolerance():
    p = ComparisonPolicy(float_abs_tolerance=1e-9)
    assert kinds(0.1 + 0.2, 0.3, p) == []
    assert kinds(0.1 + 0.2, 0.3) == [("", "value_changed")]
    assert kinds(1.0, 1.1, p) == [("", "value_changed")]


def test_large_int_boundaries():
    big = 2**64
    assert kinds(big, big + 1) == [("", "value_changed")]
    assert kinds(2**53 + 1, float(2**53), ComparisonPolicy(numbers="numeric")) == [
        ("", "value_changed")
    ]
    huge = 10**400
    assert kinds(huge, 1.0, ComparisonPolicy(numbers="numeric", float_abs_tolerance=0.5)) == [
        ("", "value_changed")
    ]


def test_missing_extra_and_null_vs_absent():
    assert kinds({"a": 1, "b": 2}, {"a": 1}) == [("/b", "missing_in_candidate")]
    assert kinds({"a": 1}, {"a": 1, "z": 0}) == [("/z", "extra_in_candidate")]
    assert kinds({"a": None}, {}) == [("/a", "null_vs_absent")]
    assert kinds({}, {"a": None}) == [("/a", "null_vs_absent")]
    assert kinds({"a": None}, {"a": 0}) == [("/a", "type_changed")]


def test_array_order_strict_and_ignored():
    assert kinds([1, 2, 3], [3, 2, 1]) == [("", "array_order")]
    assert kinds([1, 2, 3], [3, 2, 1], ComparisonPolicy(array_order="ignore")) == []
    assert kinds([1, 1, 2], [1, 2, 2], ComparisonPolicy(array_order="ignore")) == [
        ("", "array_elements")
    ]


def test_array_length_differences():
    assert kinds([1, 2], [1]) == [("/1", "missing_in_candidate")]
    assert kinds([1], [1, 2]) == [("/1", "extra_in_candidate")]


def test_pointer_escaping_in_paths():
    assert kinds({"a/b": {"~x": 1}}, {"a/b": {"~x": 2}}) == [("/a~1b/~0x", "value_changed")]


def test_unicode_normalization_forms_are_different():
    # NFC "é" vs NFD "é" are observably different strings.
    assert kinds("é", "é") == [("", "value_changed")]


json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(),
    lambda children: (
        st.lists(children, max_size=5) | st.dictionaries(st.text(max_size=5), children, max_size=5)
    ),
    max_leaves=25,
)

MIRROR = {
    "missing_in_candidate": "extra_in_candidate",
    "extra_in_candidate": "missing_in_candidate",
}


@settings(max_examples=300)
@given(json_values)
def test_reflexive(v):
    assert diff(v, v, STRICT) == []


@settings(max_examples=300)
@given(json_values, json_values)
def test_no_differences_iff_canonically_equal(a, b):
    assert (diff(a, b, STRICT) == []) == (canonical(a) == canonical(b))


@settings(max_examples=300)
@given(json_values, json_values)
def test_symmetric(a, b):
    forward = sorted((d.path, MIRROR.get(d.kind, d.kind)) for d in diff(a, b, STRICT))
    backward = sorted((d.path, d.kind) for d in diff(b, a, STRICT))
    assert forward == backward


@settings(max_examples=200)
@given(st.lists(json_values, max_size=6), st.randoms())
def test_ignore_order_accepts_any_permutation(items, rnd):
    shuffled = list(items)
    rnd.shuffle(shuffled)
    assert diff(items, shuffled, ComparisonPolicy(array_order="ignore")) == []
