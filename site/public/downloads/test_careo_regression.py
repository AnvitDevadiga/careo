"""Regression test exported by Careo.

It replays a minimized scenario against the configured reference and candidate
from a clean, verified state. It FAILS while the behavioral difference exists
and passes once the candidate matches the reference again.
"""

from pathlib import Path

from careo.scenarios.runner import replay_file


def test_careo_regression() -> None:
    result = replay_file(Path(__file__).with_name("regression.json"))
    assert result.verdict == "MATCH", result.describe()
