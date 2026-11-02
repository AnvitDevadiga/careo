"""AI-rewrite-style candidate of app_reference.py (demo fixture, stdlib only).

The rewrite restructures the code and numbers new bookings differently
(8000 + 3n instead of 1, 2, 3...). Careo must treat those ids as the same
logical entities instead of reporting them as differences.

SEEDED REGRESSION (deliberate, documented; not a real-world finding):
cancelling an already-cancelled booking is treated as an idempotent success.
It returns 200 instead of 409 already_cancelled AND appends a second
"cancelled" event (a duplicate side effect, e.g. a second refund). The
happy-path tests in test_happy_path.py never cancel twice, so they pass.
"""

import argparse
import json
import re
import socketserver
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit


class LocalHTTPServer(HTTPServer):
    """HTTPServer without the reverse-DNS lookup in server_bind (socket.getfqdn),
    which can stall for many seconds on some hosts (seen on macOS CI runners)."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


SLOT_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def valid_slot(value):
    return isinstance(value, str) and bool(SLOT_RE.match(value))


class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def one(self, sql, *args):
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def get(self, booking_id):
        return self.one("SELECT * FROM bookings WHERE id = ?", booking_id)

    def record(self, booking_id, kind):
        self.conn.execute("INSERT INTO events (booking_id, kind) VALUES (?, ?)", (booking_id, kind))

    def slot_in_use(self, slot, ignore_id=None):
        return (
            self.one(
                "SELECT id FROM bookings WHERE slot = ? AND status = 'active' AND id IS NOT ?",
                slot,
                ignore_id,
            )
            is not None
        )

    def next_id(self):
        n = self.one("SELECT COUNT(*) AS n FROM bookings WHERE id >= 8000")["n"]
        return 8000 + 3 * n


def handle_create(store, payload):
    if not isinstance(payload, dict):
        return 400, {"error": "invalid"}
    customer = payload.get("customer")
    if not (isinstance(customer, str) and 0 < len(customer) <= 40):
        return 400, {"error": "invalid_customer"}
    if not valid_slot(payload.get("slot")):
        return 400, {"error": "invalid_slot"}
    if store.slot_in_use(payload["slot"]):
        return 409, {"error": "slot_taken"}
    new_id = store.next_id()
    store.conn.execute(
        "INSERT INTO bookings (id, customer, slot, status) VALUES (?, ?, ?, 'active')",
        (new_id, customer, payload["slot"]),
    )
    store.record(new_id, "created")
    return 201, store.get(new_id)


def handle_cancel(store, booking_id):
    booking = store.get(booking_id)
    if not booking:
        return 404, {"error": "not_found"}
    # "Make cancellation idempotent" -- SEEDED BUG: the retry still emits an event
    # and returns success instead of 409 already_cancelled.
    store.conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
    store.record(booking_id, "cancelled")
    return 200, store.get(booking_id)


def handle_reschedule(store, booking_id, payload):
    booking = store.get(booking_id)
    if not booking:
        return 404, {"error": "not_found"}
    slot = payload.get("slot") if isinstance(payload, dict) else None
    if not valid_slot(slot):
        return 400, {"error": "invalid_slot"}
    if booking["status"] != "active":
        return 409, {"error": "not_active"}
    if store.slot_in_use(slot, ignore_id=booking_id):
        return 409, {"error": "slot_taken"}
    store.conn.execute("UPDATE bookings SET slot = ? WHERE id = ?", (slot, booking_id))
    store.record(booking_id, "rescheduled")
    return 200, store.get(booking_id)


def handle_list(store, query):
    try:
        limit = int(query.get("limit", ["10"])[0])
        offset = int(query.get("offset", ["0"])[0])
    except ValueError:
        return 400, {"error": "invalid_paging"}
    if limit < 1 or limit > 50 or offset < 0:
        return 400, {"error": "invalid_paging"}
    rows = store.conn.execute(
        "SELECT * FROM bookings ORDER BY id LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
    total = store.one("SELECT COUNT(*) AS n FROM bookings")["n"]
    return 200, {"items": [dict(r) for r in rows], "total": total}


def build(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def payload(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(length) or b"null")
            except ValueError:
                return None

        def dispatch(self, verb):
            url = urlsplit(self.path)
            segs = [s for s in url.path.split("/") if s]
            if verb == "GET" and segs == ["health"]:
                return self.reply(200, {"ok": True})
            if not segs or segs[0] != "bookings":
                return self.reply(404, {"error": "no_route"})
            if len(segs) == 1 and verb == "POST":
                return self.reply(*handle_create(store, self.payload()))
            if len(segs) == 1 and verb == "GET":
                return self.reply(*handle_list(store, parse_qs(url.query)))
            if len(segs) >= 2 and not segs[1].isdigit():
                return self.reply(404, {"error": "not_found"})
            if len(segs) == 2 and verb == "GET":
                found = store.get(int(segs[1]))
                return self.reply(200, found) if found else self.reply(404, {"error": "not_found"})
            if len(segs) == 2 and verb == "PATCH":
                return self.reply(*handle_reschedule(store, int(segs[1]), self.payload()))
            if len(segs) == 3 and segs[2] == "cancel" and verb == "POST":
                return self.reply(*handle_cancel(store, int(segs[1])))
            return self.reply(405, {"error": "method_not_allowed"})

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

        def do_PATCH(self):
            self.dispatch("PATCH")

    return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    LocalHTTPServer(("127.0.0.1", args.port), build(Store(args.db))).serve_forever()
