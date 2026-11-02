"""Reference implementation of the bulk-pricing contract (demo fixture).

Contract: read one JSON object from stdin:
    {"quantity": int >= 0, "unit_price_cents": int >= 0}
Write one JSON object to stdout and exit 0:
    quantity, subtotal_cents, discount_percent, total_cents,
    generated_at (UTC timestamp), request_id (random)
Discounts: 10% at quantity >= 100, 20% at quantity >= 1000 (floor to cents).
Invalid input: write {"error": "..."} and exit 2.
"""

import json
import sys
import uuid
from datetime import UTC, datetime


def fail(message):
    print(json.dumps({"error": message}))
    sys.exit(2)


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def main():
    try:
        request = json.load(sys.stdin)
    except ValueError:
        fail("input is not JSON")
    if not isinstance(request, dict):
        fail("input must be an object")
    quantity = request.get("quantity")
    price = request.get("unit_price_cents")
    if not is_int(quantity) or not is_int(price):
        fail("quantity and unit_price_cents must be integers")
    if quantity < 0 or price < 0:
        fail("quantity and unit_price_cents must be >= 0")

    if quantity >= 1000:
        discount = 20
    elif quantity >= 100:
        discount = 10
    else:
        discount = 0
    subtotal = quantity * price
    total = subtotal - (subtotal * discount) // 100
    print(
        json.dumps(
            {
                "quantity": quantity,
                "subtotal_cents": subtotal,
                "discount_percent": discount,
                "total_cents": total,
                "generated_at": datetime.now(UTC).isoformat(),
                "request_id": str(uuid.uuid4()),
            }
        )
    )


if __name__ == "__main__":
    main()
