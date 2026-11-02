"""Scriptable HTTP item service used by Careo's scenario tests (stdlib only).

API: GET /health, POST /items {"name"}, GET /items, GET /items/{id},
DELETE /items/{id}, POST /__reset. State lives in the SQLite file given by --db.
Flags switch on specific (mis)behaviors.
"""

import argparse
import json
import os
import socketserver
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class LocalHTTPServer(HTTPServer):
    """HTTPServer without the reverse-DNS lookup in server_bind (socket.getfqdn),
    which can stall for many seconds on some hosts (seen on macOS CI runners)."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, required=True)
ap.add_argument("--db", required=True)
ap.add_argument("--idbase", type=int, default=0)  # first id = idbase + 1
ap.add_argument("--double-delete-ok", action="store_true")  # 2nd delete -> 204 not 404
ap.add_argument("--sleep-on", default="")  # "GET /items" etc. -> sleep 30
ap.add_argument("--crash-after", type=int, default=0)  # os._exit after N item requests
ap.add_argument("--partial", action="store_true")  # lie about Content-Length on list
ap.add_argument("--memory", action="store_true")  # in-memory counter in responses
ap.add_argument("--alternate", action="store_true")  # every other DELETE answers 500
ap.add_argument("--bad-reset-hook", action="store_true")  # /__reset inserts a junk row
ap.add_argument("--audit-extra", action="store_true")  # extra audit row on create
ap.add_argument("--wrong-get-id", action="store_true")  # GET returns id + 1
ap.add_argument("--pidfile", default="")
ap.add_argument("--string-ids", action="store_true")  # ids serialized as strings
ap.add_argument("--blob", default="ff")  # hex bytes served by GET /blob
args = ap.parse_args()
if args.pidfile:
    with open(args.pidfile, "w") as fh:
        fh.write(str(os.getpid()))

db = sqlite3.connect(args.db, isolation_level=None, check_same_thread=False)
counter = {"requests": 0, "memory": 0, "deletes": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def out(self, code, obj, lie=False):
        data = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data) + (50 if lie else 0)))
        self.end_headers()
        self.wfile.write(data)

    def handle_any(self, method):
        path = self.path.split("?")[0]
        if path == "/health":
            return self.out(200, {"ok": True})
        if path == "/blob":
            data = bytes.fromhex(args.blob)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return None
        if path == "/__reset":
            if args.bad_reset_hook:
                db.execute("INSERT INTO items (id, name) VALUES (424242, 'ghost')")
            return self.out(200, {"reset": True})
        counter["requests"] += 1
        if args.crash_after and counter["requests"] > args.crash_after:
            os._exit(1)
        key = f"{method} {'/items' if path == '/items' else '/items/{id}'}"
        if args.sleep_on and args.sleep_on == key:
            time.sleep(30)
        parts = [p for p in path.split("/") if p]
        if method == "POST" and parts == ["items"]:
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}"
            )
            n = db.execute(
                "SELECT (SELECT COUNT(*) FROM items) + (SELECT COUNT(*) FROM tombstones)"
            ).fetchone()[0]
            new_id = args.idbase + n + 1
            while db.execute("SELECT 1 FROM items WHERE id = ?", (new_id,)).fetchone():
                new_id += 1
            db.execute("INSERT INTO items (id, name) VALUES (?, ?)", (new_id, body.get("name")))
            if args.audit_extra:
                db.execute("INSERT INTO audit (item_id, action) VALUES (?, 'created')", (new_id,))
            obj = {"id": str(new_id) if args.string_ids else new_id, "name": body.get("name")}
            if args.memory:
                counter["memory"] += 1
                obj["seq"] = counter["memory"]
            return self.out(201, obj)
        if method == "GET" and parts == ["items"]:
            rows = db.execute("SELECT id, name FROM items ORDER BY id").fetchall()
            return self.out(
                200, {"items": [{"id": i, "name": n} for i, n in rows]}, lie=args.partial
            )
        if len(parts) == 2 and parts[0] == "items" and parts[1].isdigit():
            iid = int(parts[1])
            row = db.execute("SELECT id, name FROM items WHERE id = ?", (iid,)).fetchone()
            if method == "GET":
                if not row:
                    return self.out(404, {"error": "not_found"})
                return self.out(
                    200, {"id": row[0] + (1 if args.wrong_get_id else 0), "name": row[1]}
                )
            if method == "DELETE":
                counter["deletes"] += 1
                if args.alternate and counter["deletes"] % 2 == 0:
                    return self.out(500, {"error": "boom"})
                if not row:
                    if (
                        args.double_delete_ok
                        and db.execute("SELECT 1 FROM tombstones WHERE id = ?", (iid,)).fetchone()
                    ):
                        return self.out(204, None)
                    return self.out(404, {"error": "not_found"})
                db.execute("DELETE FROM items WHERE id = ?", (iid,))
                db.execute("INSERT INTO tombstones (id) VALUES (?)", (iid,))
                return self.out(204, None)
        return self.out(404, {"error": "no_route"})

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_DELETE(self):
        self.handle_any("DELETE")


LocalHTTPServer(("127.0.0.1", args.port), H).serve_forever()
