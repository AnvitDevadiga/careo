"""Reference appointment-booking API (demo fixture, stdlib only).

Contract
  GET  /health
  POST  /bookings {"customer": str 1-40, "slot": "HH:MM"}  201 | 400 | 409 slot_taken
  GET   /bookings/{id}                                    200 | 404
  POST  /bookings/{id}/cancel                             200 | 404 | 409 already_cancelled
  PATCH /bookings/{id} {"slot": "HH:MM"}                  200 | 400 | 404 | 409
  GET   /bookings?limit=&offset=                          200 {"items", "total"}
Every state change appends one row to the events table (the declared side effect).
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


SLOT = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


class Api:
    def __init__(self, db):
        self.db = sqlite3.connect(db, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row

    def booking(self, bid):
        row = self.db.execute("SELECT * FROM bookings WHERE id = ?", (bid,)).fetchone()
        return dict(row) if row else None

    def event(self, bid, kind):
        self.db.execute("INSERT INTO events (booking_id, kind) VALUES (?, ?)", (bid, kind))

    def slot_taken(self, slot, exclude=None):
        row = self.db.execute(
            "SELECT 1 FROM bookings WHERE slot = ? AND status = 'active' AND id IS NOT ?",
            (slot, exclude),
        ).fetchone()
        return row is not None

    def create(self, body):
        if not isinstance(body, dict):
            return 400, {"error": "invalid"}
        customer, slot = body.get("customer"), body.get("slot")
        if not isinstance(customer, str) or not 1 <= len(customer) <= 40:
            return 400, {"error": "invalid_customer"}
        if not isinstance(slot, str) or not SLOT.match(slot):
            return 400, {"error": "invalid_slot"}
        if self.slot_taken(slot):
            return 409, {"error": "slot_taken"}
        cur = self.db.execute(
            "INSERT INTO bookings (customer, slot, status) VALUES (?, ?, 'active')",
            (customer, slot),
        )
        self.event(cur.lastrowid, "created")
        return 201, self.booking(cur.lastrowid)

    def cancel(self, bid):
        b = self.booking(bid)
        if b is None:
            return 404, {"error": "not_found"}
        if b["status"] == "cancelled":
            return 409, {"error": "already_cancelled"}
        self.db.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (bid,))
        self.event(bid, "cancelled")
        return 200, self.booking(bid)

    def reschedule(self, bid, body):
        b = self.booking(bid)
        if b is None:
            return 404, {"error": "not_found"}
        slot = body.get("slot") if isinstance(body, dict) else None
        if not isinstance(slot, str) or not SLOT.match(slot):
            return 400, {"error": "invalid_slot"}
        if b["status"] != "active":
            return 409, {"error": "not_active"}
        if self.slot_taken(slot, exclude=bid):
            return 409, {"error": "slot_taken"}
        self.db.execute("UPDATE bookings SET slot = ? WHERE id = ?", (slot, bid))
        self.event(bid, "rescheduled")
        return 200, self.booking(bid)

    def list(self, query):
        try:
            limit = int(query.get("limit", ["10"])[0])
            offset = int(query.get("offset", ["0"])[0])
        except ValueError:
            return 400, {"error": "invalid_paging"}
        if not 1 <= limit <= 50 or offset < 0:
            return 400, {"error": "invalid_paging"}
        rows = self.db.execute(
            "SELECT * FROM bookings ORDER BY id LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        total = self.db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
        return 200, {"items": [dict(r) for r in rows], "total": total}


def make_handler(api):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, payload):
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"null")
            except ValueError:
                return None

        def route(self, method):
            url = urlsplit(self.path)
            parts = [p for p in url.path.split("/") if p]
            if parts == ["health"] and method == "GET":
                return self.send(200, {"ok": True})
            if parts[:1] != ["bookings"]:
                return self.send(404, {"error": "no_route"})
            if len(parts) == 1:
                if method == "POST":
                    return self.send(*api.create(self.body()))
                if method == "GET":
                    return self.send(*api.list(parse_qs(url.query)))
            if len(parts) >= 2:
                if not parts[1].isdigit():
                    return self.send(404, {"error": "not_found"})
                bid = int(parts[1])
                if len(parts) == 2 and method == "GET":
                    b = api.booking(bid)
                    return self.send(200, b) if b else self.send(404, {"error": "not_found"})
                if len(parts) == 2 and method == "PATCH":
                    return self.send(*api.reschedule(bid, self.body()))
                if parts[2:] == ["cancel"] and method == "POST":
                    return self.send(*api.cancel(bid))
            return self.send(405, {"error": "method_not_allowed"})

        def do_GET(self):
            self.route("GET")

        def do_POST(self):
            self.route("POST")

        def do_PATCH(self):
            self.route("PATCH")

    return Handler


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--db", required=True)
    a = ap.parse_args()
    LocalHTTPServer(("127.0.0.1", a.port), make_handler(Api(a.db))).serve_forever()
