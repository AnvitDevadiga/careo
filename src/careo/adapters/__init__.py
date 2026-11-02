"""Adapters between Careo and the systems under test.

``StateAdapter`` is the fixture lifecycle contract: prepare (once per run),
reset (before every scenario), verify_reset (independent check), snapshot
(inspect persistent state) and teardown. ``SqliteState`` is the only
implementation in this release.
"""

from __future__ import annotations

from typing import Any, Protocol


class StateAdapter(Protocol):
    def prepare(self) -> None: ...
    def reset(self) -> None: ...
    def verify_reset(self) -> str | None: ...
    def snapshot(self) -> dict[str, list[dict[str, Any]]]: ...
    def teardown(self) -> None: ...
