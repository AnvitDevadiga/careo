import pytest

from careo.jsonvalue import JSONParseError, canonical, ensure_json_value, parse_strict


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"   \n",
        b"NaN",
        b'{"a": Infinity}',
        b'{"a":1,"a":2}',
        b"\xff\xfe",
        b"[1] [2]",
        b"\xef\xbb\xbf{}",
        b"{'a': 1}",
        b"[1,]",
    ],
)
def test_rejects_invalid_or_ambiguous(data: bytes) -> None:
    with pytest.raises(JSONParseError):
        parse_strict(data)


def test_parses_unicode_and_big_ints() -> None:
    assert parse_strict('{"k": "ünï😀"}\n'.encode()) == {"k": "ünï😀"}
    assert parse_strict(b"123456789012345678901234567890") == 123456789012345678901234567890


def test_deep_nesting_is_parse_error() -> None:
    with pytest.raises(JSONParseError):
        parse_strict(b"[" * 100000 + b"]" * 100000)


def test_canonical_sorts_keys() -> None:
    assert canonical({"b": 1, "a": [True, None]}) == '{"a":[true,null],"b":1}'


@pytest.mark.parametrize("bad", [float("nan"), {1: 2}, {"a": object()}, [float("inf")]])
def test_ensure_json_value_rejects(bad: object) -> None:
    with pytest.raises(ValueError):
        ensure_json_value(bad, "x")


def test_exact_floats_accepted():
    assert parse_strict(b"0.1") == 0.1
    assert parse_strict(b"0.30000000000000004") == 0.1 + 0.2
    assert parse_strict(b"1.5e3") == 1500.0
    assert parse_strict(b"-0.0") == 0.0


def test_seventeen_digit_floats_accepted():
    # %.17g output from C/Go/Java must not be rejected
    assert parse_strict(b"0.10000000000000001") == 0.1
    assert parse_strict(b"2.2250738585072014e-308") > 0


def test_underflow_rejected():
    for bad in (b"1e-400", b"-5e-500", b"[0.0, 1e-400]"):
        with pytest.raises(JSONParseError):
            parse_strict(bad)
