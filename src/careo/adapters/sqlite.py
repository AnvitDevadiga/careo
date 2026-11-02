"""SQLite state adapter: prepare -> reset -> inspect -> teardown.

Careo owns the database file. It resets it itself, then *independently verifies*
the reset by comparing a full snapshot (schema plus every row of every table)
against the state produced by loading the same schema and fixture into a
private in-memory database. A reset that does not produce exactly that state
is a RESET_FAILED, never a verified scenario.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from careo.jsonvalue import canonical

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
Snapshot = dict[str, list[dict[str, Any]]]


class StateError(Exception):
    pass


def check_ident(name: str, where: str) -> str:
    if not isinstance(name, str) or not _IDENT.match(name):
        raise StateError(f"{where}: {name!r} is not a plain SQL identifier")
    return name


@dataclass(frozen=True)
class TableSpec:
    name: str
    order_by: tuple[str, ...] = ()  # empty = rowid (insertion order)
    unordered: bool = False
    ignore_columns: tuple[str, ...] = ()
    id_columns: dict[str, str] = field(default_factory=dict)  # column -> entity

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "order_by": list(self.order_by),
            "unordered": self.unordered,
            "ignore_columns": list(self.ignore_columns),
            "id_columns": dict(self.id_columns),
        }


def _value(v: Any) -> Any:
    if isinstance(v, bytes):
        return {"$bytes": v.hex()}
    return v


def _user_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def _schema(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name"
    ).fetchall()
    return [f"{t}:{n}:{s}" for t, n, s in rows]


def _rows(conn: sqlite3.Connection, table: str, order: str) -> list[dict[str, Any]]:
    cur = conn.execute(f'SELECT * FROM "{table}" {order}')  # noqa: S608 - validated identifier
    cols = [d[0] for d in cur.description]
    return [{c: _value(v) for c, v in zip(cols, row, strict=True)} for row in cur.fetchall()]


def full_state(conn: sqlite3.Connection) -> dict[str, Any]:
    """Order-insensitive, complete description used for reset verification."""
    tables = {}
    for t in _user_tables(conn):
        rows = _rows(conn, t, "")
        tables[t] = sorted(canonical(r) for r in rows)
    seq: list[Any] = []
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
        seq = sorted(canonical(list(r)) for r in conn.execute("SELECT * FROM sqlite_sequence"))
    return {"schema": _schema(conn), "tables": tables, "sqlite_sequence": seq}


class SqliteState:
    def __init__(
        self, db_path: Path, schema_sql: str, fixture_sql: str, tables: tuple[TableSpec, ...]
    ) -> None:
        self.db_path = db_path
        self.schema_sql = schema_sql
        self.fixture_sql = fixture_sql
        self.tables = tables
        self._expected: dict[str, Any] | None = None

    # -- lifecycle ---------------------------------------------------------
    def prepare(self, recreate: bool = True) -> None:
        """Create the database from schema+fixture. With ``recreate=False`` (an
        external service that may already hold the file open) an existing file is
        reset in place instead of being deleted, and must already have the schema."""
        if not recreate and self.db_path.exists():
            self.reset()
            problem = self.verify_reset()
            if problem:
                raise StateError(f"{self.db_path.name}: {problem}")
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(str(self.db_path) + suffix).unlink(missing_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(self.schema_sql)
            conn.executescript(self.fixture_sql)
            conn.commit()
        except sqlite3.Error as exc:
            raise StateError(f"cannot prepare {self.db_path.name}: {exc}") from None
        finally:
            conn.close()

    def reset(self) -> None:
        """Delete every row of every user table and reload the fixture, atomically."""
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        try:
            tables = _user_tables(conn)
            has_seq = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'"
            ).fetchone()
            script = ["PRAGMA foreign_keys=OFF;", "BEGIN IMMEDIATE;"]
            script += [f'DELETE FROM "{t}";' for t in tables]  # noqa: S608 - names from sqlite_master
            if has_seq:
                script.append("DELETE FROM sqlite_sequence;")
            script += [self.fixture_sql, "COMMIT;"]
            try:
                conn.executescript("\n".join(script))
            except sqlite3.Error as exc:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise StateError(f"reset of {self.db_path.name} failed: {exc}") from None
        finally:
            conn.close()

    def expected_state(self) -> dict[str, Any]:
        if self._expected is None:
            mem = sqlite3.connect(":memory:")
            try:
                mem.executescript(self.schema_sql)
                mem.executescript(self.fixture_sql)
                self._expected = full_state(mem)
            finally:
                mem.close()
        return self._expected

    def verify_reset(self) -> str | None:
        """None if the live database equals the fixture state, else a reason."""
        try:
            conn = self._ro()
            try:
                actual = full_state(conn)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            return f"cannot inspect {self.db_path.name}: {exc}"
        expected = self.expected_state()
        if actual["schema"] != expected["schema"]:
            return "schema differs from schema.sql after reset"
        for name in sorted(set(actual["tables"]) | set(expected["tables"])):
            if actual["tables"].get(name) != expected["tables"].get(name):
                got = len(actual["tables"].get(name, []))
                want = len(expected["tables"].get(name, []))
                return (
                    f"table {name!r} does not match the fixture after reset "
                    f"({got} rows, expected {want})"
                )
        if actual["sqlite_sequence"] != expected["sqlite_sequence"]:
            return "AUTOINCREMENT counters were not reset"
        return None

    def snapshot(self) -> Snapshot:
        """Rows of the configured tables, for behavioral comparison."""
        conn = self._ro()
        try:
            out: Snapshot = {}
            for spec in self.tables:
                order = (
                    "ORDER BY " + ", ".join(f'"{c}"' for c in spec.order_by)
                    if spec.order_by
                    else "ORDER BY rowid"
                )
                rows = _rows(conn, spec.name, order)
                for r in rows:
                    for col in spec.ignore_columns:
                        r.pop(col, None)
                out[spec.name] = rows
            return out
        finally:
            conn.close()

    def teardown(self) -> None:
        pass  # files are left in the run directory as evidence

    def _ro(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=10)
