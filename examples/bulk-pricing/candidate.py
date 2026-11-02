"""AI-rewrite-style candidate of original.py (demo fixture).

SEEDED REGRESSION (deliberate, documented, not a real-world finding):
the tier checks use `>` instead of `>=`, so exactly 100 and exactly 1000
units miss their discount. Happy-path inputs such as 5 or 250 units are
unaffected, which is why ordinary example-based tests miss it.
"""

import json
import sys
import uuid
from datetime import UTC, datetime

TIERS = [(1000, 20), (100, 10)]


def respond(payload, code=0):
    print(json.dumps(payload))
    sys.exit(code)


def parse_request(raw):
    try:
        req = json.loads(raw)
    except ValueError:
        respond({"error": "input is not JSON"}, 2)
    if not isinstance(req, dict):
        respond({"error": "input must be an object"}, 2)
    q, p = req.get("quantity"), req.get("unit_price_cents")
    if type(q) is not int or type(p) is not int:
        respond({"error": "quantity and unit_price_cents must be integers"}, 2)
    if min(q, p) < 0:
        respond({"error": "quantity and unit_price_cents must be >= 0"}, 2)
    return q, p


def discount_for(quantity):
    # BUG (seeded): should be `quantity >= threshold`.
    return next((pct for threshold, pct in TIERS if quantity > threshold), 0)


def main():
    quantity, price = parse_request(sys.stdin.read())
    pct = discount_for(quantity)
    subtotal = quantity * price
    respond(
        {
            "quantity": quantity,
            "subtotal_cents": subtotal,
            "discount_percent": pct,
            "total_cents": subtotal - subtotal * pct // 100,
            "generated_at": datetime.now(UTC).isoformat(),
            "request_id": str(uuid.uuid4()),
        }
    )


if __name__ == "__main__":
    main()
