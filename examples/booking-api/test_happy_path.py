"""The example project's ordinary happy-path tests.

They pass against BOTH implementations: none of them cancels a booking twice.
Run them against a live service with:  BOOKING_URL=http://127.0.0.1:PORT pytest test_happy_path.py
The functions take a base URL so Careo's benchmark can reuse them unchanged.
"""

import json
import os
import urllib.error
import urllib.request

import pytest


def call(base, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        base + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def check_create_and_read(base):
    status, b = call(base, "POST", "/bookings", {"customer": "ana", "slot": "09:00"})
    assert status == 201 and b["status"] == "active" and b["slot"] == "09:00"
    status, got = call(base, "GET", f"/bookings/{b['id']}")
    assert status == 200 and got == b


def check_cancel(base):
    _, b = call(base, "POST", "/bookings", {"customer": "bo", "slot": "10:00"})
    status, c = call(base, "POST", f"/bookings/{b['id']}/cancel")
    assert status == 200 and c["status"] == "cancelled"


def check_slot_conflict(base):
    call(base, "POST", "/bookings", {"customer": "chen", "slot": "11:00"})
    status, err = call(base, "POST", "/bookings", {"customer": "dee", "slot": "11:00"})
    assert status == 409 and err == {"error": "slot_taken"}


def check_missing_is_404(base):
    status, _ = call(base, "GET", "/bookings/999999")
    assert status == 404


def check_list(base):
    status, page = call(base, "GET", "/bookings?limit=2&offset=0")
    assert status == 200 and len(page["items"]) <= 2 and page["total"] >= len(page["items"])


def check_invalid_slot(base):
    status, _ = call(base, "POST", "/bookings", {"customer": "eve", "slot": "25:00"})
    assert status == 400


CHECKS = [
    check_create_and_read,
    check_cancel,
    check_slot_conflict,
    check_missing_is_404,
    check_list,
    check_invalid_slot,
]


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_happy_path(check):
    base = os.environ.get("BOOKING_URL")
    if not base:
        pytest.skip("set BOOKING_URL to a running booking service")
    check(base)
