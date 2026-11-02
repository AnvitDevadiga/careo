import contextlib
import socket
import sqlite3
import threading

import pytest

from careo.adapters.http import (
    CONNECTION_ERROR,
    OK,
    PROTOCOL_ERROR,
    RESPONSE_TOO_LARGE,
    TIMEOUT,
    EndpointError,
    build_path,
    parse_endpoint,
    request,
)
from careo.adapters.sqlite import SqliteState, TableSpec
from careo.execution.service import free_port


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:8000", "http://localhost:1", "http://[::1]:80/api"]
)
def test_loopback_endpoints_allowed(url):
    parse_endpoint(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "http://10.0.0.5:80",
        "ftp://127.0.0.1",
        "http://user:pw@127.0.0.1",
        "http://127.0.0.1/?q=1",
    ],
)
def test_non_local_or_malformed_endpoints_rejected(url):
    with pytest.raises(EndpointError):
        parse_endpoint(url)


def test_remote_requires_explicit_opt_in():
    assert parse_endpoint("http://staging.internal:8080", allow_remote=True).port == 8080


def test_build_path_quotes_and_checks():
    ep = parse_endpoint("http://127.0.0.1:1/api")
    assert build_path(ep, "/items/{id}", {"id": "a/b c"}) == "/api/items/a%2Fb%20c"
    with pytest.raises(ValueError):
        build_path(ep, "/items/{id}", {})


def _raw_server(payload: bytes, delay: float = 0.0):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            conn.recv(65536)
            if delay:
                import time

                time.sleep(delay)
            with contextlib.suppress(OSError):
                conn.sendall(payload)
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv, parse_endpoint(f"http://127.0.0.1:{port}")


def _req(ep, **kw):
    return request(
        ep,
        "GET",
        "/",
        timeout_seconds=kw.get("timeout", 2),
        max_response_bytes=kw.get("limit", 1 << 20),
    )


def test_ok_json_response():
    srv, ep = _raw_server(b'HTTP/1.1 200 OK\r\nContent-Length: 8\r\nX-A: 1\r\n\r\n{"a": 1}')
    r = _req(ep)
    srv.close()
    assert r.status == OK and r.code == 200 and r.body_json == {"a": 1} and r.headers["x-a"] == "1"


def test_partial_body_is_protocol_error():
    srv, ep = _raw_server(b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{"a": 1}')
    r = _req(ep)
    srv.close()
    assert r.status == PROTOCOL_ERROR


def test_garbage_status_line_is_protocol_error():
    srv, ep = _raw_server(b"NOT HTTP\r\n\r\n")
    r = _req(ep)
    srv.close()
    assert r.status in (PROTOCOL_ERROR, CONNECTION_ERROR)


def test_too_large_response():
    srv, ep = _raw_server(b"HTTP/1.1 200 OK\r\nContent-Length: 2000\r\n\r\n" + b"x" * 2000)
    r = _req(ep, limit=100)
    srv.close()
    assert r.status == RESPONSE_TOO_LARGE


def test_timeout():
    srv, ep = _raw_server(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}", delay=2)
    r = _req(ep, timeout=0.3)
    srv.close()
    assert r.status == TIMEOUT


def test_connection_refused():
    r = _req(parse_endpoint(f"http://127.0.0.1:{free_port()}"))
    assert r.status == CONNECTION_ERROR


def test_non_json_and_empty_bodies_are_not_null():
    srv, ep = _raw_server(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
    r = _req(ep)
    srv.close()
    assert r.status == OK and not r.body_is_json and r.body_text == ""


SCHEMA = "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT, ts TEXT);"


def make_state(tmp_path, fixture="INSERT INTO t (v, ts) VALUES ('seed', 'x');", **table):
    s = SqliteState(tmp_path / "s.db", SCHEMA, fixture, (TableSpec("t", **table),))
    s.prepare()
    return s


def test_reset_restores_fixture_and_verifies(tmp_path):
    s = make_state(tmp_path)
    assert s.verify_reset() is None
    c = sqlite3.connect(s.db_path)
    c.execute("INSERT INTO t (v, ts) VALUES ('new', 'y')")
    c.commit()
    assert "does not match the fixture" in s.verify_reset()
    s.reset()
    assert s.verify_reset() is None
    assert s.snapshot() == {"t": [{"id": 1, "v": "seed", "ts": "x"}]}


def test_verify_detects_counter_and_schema_drift(tmp_path):
    s = make_state(tmp_path)
    c = sqlite3.connect(s.db_path)
    c.execute("INSERT INTO t (v) VALUES ('a')")
    c.execute("DELETE FROM t WHERE v = 'a'")
    c.commit()
    assert s.verify_reset() == "AUTOINCREMENT counters were not reset"
    c.execute("CREATE TABLE extra (x)")
    c.commit()
    assert "schema differs" in s.verify_reset()
    c.close()


def test_snapshot_ignores_columns_and_orders(tmp_path):
    s = make_state(
        tmp_path,
        fixture="INSERT INTO t (v, ts) VALUES ('b', '1'), ('a', '2');",
        order_by=("v",),
        ignore_columns=("ts",),
    )
    assert s.snapshot() == {"t": [{"id": 2, "v": "a"}, {"id": 1, "v": "b"}]}


def test_bad_fixture_fails_prepare(tmp_path):
    from careo.adapters.sqlite import StateError

    with pytest.raises(StateError):
        make_state(tmp_path, fixture="INSERT INTO nope VALUES (1);")
