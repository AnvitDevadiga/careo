"""Benchmark candidate: an independent rewrite of the booking API reference
(examples/booking-api/app_reference.py) with ONE selectable, deliberately
seeded regression. `--defect none` is the clean rewrite used to measure false
alarms; it also numbers bookings differently (5000 + 11n), so a correct
logical-id mapping is required to avoid false alarms.

Defects (all seeded on purpose; none are real-world findings):
  retry_cancel       cancelling twice returns 200 and records a duplicate event
  slot_not_released  a cancelled booking still blocks its slot
  lost_update        PATCH returns the new slot but never persists it
  pagination         offset > 0 skips one extra row
  missing_error      unknown booking id returns 400 instead of 404
  status_transition  rescheduling a cancelled booking reactivates it (200, not 409)
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


DEFECTS = {
    "none",
    "retry_cancel",
    "slot_not_released",
    "lost_update",
    "pagination",
    "missing_error",
    "status_transition",
}
VALID_SLOT = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


class Service:
    def __init__(self, db, defect):
        self.defect = defect
        self.c = sqlite3.connect(db, isolation_level=None, check_same_thread=False)
        self.c.row_factory = sqlite3.Row

    def row(self, sql, *a):
        r = self.c.execute(sql, a).fetchone()
        return dict(r) if r else None

    def find(self, bid):
        return self.row("SELECT * FROM bookings WHERE id = ?", bid)

    def emit(self, bid, kind):
        self.c.execute("INSERT INTO events (booking_id, kind) VALUES (?, ?)", (bid, kind))

    def missing(self):
        return (
            (400, {"error": "bad_id"})
            if self.defect == "missing_error"
            else (404, {"error": "not_found"})
        )

    def occupied(self, slot, skip=None):
        status_filter = "" if self.defect == "slot_not_released" else " AND status = 'active'"
        return (
            self.row(
                f"SELECT id FROM bookings WHERE slot = ?{status_filter} AND id IS NOT ?", slot, skip
            )
            is not None
        )

    def create(self, body):
        if not isinstance(body, dict):
            return 400, {"error": "invalid"}
        who, slot = body.get("customer"), body.get("slot")
        if not isinstance(who, str) or not (1 <= len(who) <= 40):
            return 400, {"error": "invalid_customer"}
        if not isinstance(slot, str) or not VALID_SLOT.match(slot):
            return 400, {"error": "invalid_slot"}
        if self.occupied(slot):
            return 409, {"error": "slot_taken"}
        n = self.row("SELECT COUNT(*) AS n FROM bookings WHERE id >= 5000")["n"]
        bid = 5000 + 11 * n
        self.c.execute("INSERT INTO bookings VALUES (?, ?, ?, 'active')", (bid, who, slot))
        self.emit(bid, "created")
        return 201, self.find(bid)

    def cancel(self, bid):
        b = self.find(bid)
        if b is None:
            return self.missing()
        if b["status"] == "cancelled" and self.defect != "retry_cancel":
            return 409, {"error": "already_cancelled"}
        self.c.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (bid,))
        self.emit(bid, "cancelled")
        return 200, self.find(bid)

    def reschedule(self, bid, body):
        b = self.find(bid)
        if b is None:
            return self.missing()
        slot = body.get("slot") if isinstance(body, dict) else None
        if not isinstance(slot, str) or not VALID_SLOT.match(slot):
            return 400, {"error": "invalid_slot"}
        if b["status"] != "active" and self.defect != "status_transition":
            return 409, {"error": "not_active"}
        if self.occupied(slot, skip=bid):
            return 409, {"error": "slot_taken"}
        if self.defect == "lost_update":
            self.emit(bid, "rescheduled")
            return 200, {**b, "slot": slot}
        self.c.execute("UPDATE bookings SET slot = ?, status = 'active' WHERE id = ?", (slot, bid))
        self.emit(bid, "rescheduled")
        return 200, self.find(bid)

    def page(self, q):
        try:
            limit, offset = int(q.get("limit", ["10"])[0]), int(q.get("offset", ["0"])[0])
        except ValueError:
            return 400, {"error": "invalid_paging"}
        if not (1 <= limit <= 50) or offset < 0:
            return 400, {"error": "invalid_paging"}
        if self.defect == "pagination" and offset > 0:
            offset += 1
        items = [
            dict(r)
            for r in self.c.execute(
                "SELECT * FROM bookings ORDER BY id LIMIT ? OFFSET ?", (limit, offset)
            )
        ]
        total = self.row("SELECT COUNT(*) AS n FROM bookings")["n"]
        return 200, {"items": items, "total": total}


def handler_for(svc):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def out(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def json_body(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                return json.loads(raw or b"null")
            except ValueError:
                return None

        def go(self, m):
            u = urlsplit(self.path)
            p = [x for x in u.path.split("/") if x]
            if m == "GET" and p == ["health"]:
                return self.out(200, {"ok": True})
            if not p or p[0] != "bookings":
                return self.out(404, {"error": "no_route"})
            if len(p) == 1:
                if m == "POST":
                    return self.out(*svc.create(self.json_body()))
                if m == "GET":
                    return self.out(*svc.page(parse_qs(u.query)))
            elif not p[1].isdigit():
                return self.out(404, {"error": "not_found"})
            elif len(p) == 2 and m == "GET":
                b = svc.find(int(p[1]))
                return self.out(200, b) if b else self.out(*svc.missing())
            elif len(p) == 2 and m == "PATCH":
                return self.out(*svc.reschedule(int(p[1]), self.json_body()))
            elif p[2:] == ["cancel"] and m == "POST":
                return self.out(*svc.cancel(int(p[1])))
            return self.out(405, {"error": "method_not_allowed"})

        def do_GET(self):
            self.go("GET")

        def do_POST(self):
            self.go("POST")

        def do_PATCH(self):
            self.go("PATCH")

    return H


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--defect", choices=sorted(DEFECTS), default="none")
    a = ap.parse_args()
    LocalHTTPServer(("127.0.0.1", a.port), handler_for(Service(a.db, a.defect))).serve_forever()
