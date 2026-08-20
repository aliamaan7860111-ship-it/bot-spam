"""
stripe_pay.py — create a Stripe Checkout Session payment link for an order.

Used by the confirmation bot when a customer picks the "Pay By Link" payment
method at checkout. We send Stripe ONLY the amount + a neutral line-item name
(never product/item titles) and stamp the CRM order_id into metadata +
client_reference_id so the Stripe webhook (payment_bridge) can match the
payment back to the Notion order.

Currency: AED. Uses the restricted key (STRIPE_API_KEY, rk_live_...), which has
Checkout Sessions write but NOT Prices write — so we pass price_data inline
(no Price object needed).
"""
from __future__ import annotations

import os
import logging
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

log = logging.getLogger("stripe_pay")

STRIPE_API_BASE = "https://api.stripe.com/v1"
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "")
# Where Stripe sends the customer after a successful payment. A simple hosted
# thank-you page (not order-specific). Overridable per deploy.
STRIPE_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "https://amarasroom.store")


class StripeError(Exception):
    """Raised when a Stripe API call fails or inputs are invalid."""


def _to_fils(amount) -> int:
    """AED major units -> integer fils (minor units), rounded to 2dp."""
    try:
        val = float(str(amount).replace(",", "").strip())
    except (TypeError, ValueError):
        raise StripeError(f"invalid amount: {amount!r}")
    if val <= 0:
        raise StripeError(f"non-positive amount: {amount!r}")
    return int(round(val * 100))


def create_payment_link(order_id: str, amount, customer_name: str = "",
                        currency: str = "aed") -> str:
    """Create a Stripe Checkout Session and return its hosted payment URL.

    Sends NO product/item data — the line item is a neutral "Order <id>" label.
    order_id is stamped into metadata + client_reference_id for reconciliation.
    """
    if not STRIPE_API_KEY:
        raise StripeError("STRIPE_API_KEY not set in .env")
    if not order_id:
        raise StripeError("order_id required")

    unit_amount = _to_fils(amount)

    data = {
        "mode": "payment",
        "success_url": STRIPE_SUCCESS_URL,
        "client_reference_id": order_id,
        "metadata[order_id]": order_id,
        "metadata[customer_name]": customer_name or "",
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": currency,
        "line_items[0][price_data][unit_amount]": str(unit_amount),
        "line_items[0][price_data][product_data][name]": f"Order {order_id}",
        # Stamp the PaymentIntent too, so order_id is present on the charge object.
        "payment_intent_data[metadata][order_id]": order_id,
    }

    try:
        resp = requests.post(
            f"{STRIPE_API_BASE}/checkout/sessions",
            data=data,
            auth=(STRIPE_API_KEY, ""),
            timeout=20,
        )
        body = resp.json()
    except Exception as e:
        raise StripeError(f"Stripe request failed: {e}")

    if resp.status_code != 200 or not isinstance(body, dict) or "url" not in body:
        err = (body.get("error", {}) or {}).get("message") if isinstance(body, dict) else body
        raise StripeError(f"Stripe session create failed ({resp.status_code}): {err}")

    log.info(f"Stripe link for {order_id}: {body['id']} amount={unit_amount} {currency}")
    return body["url"]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    oid = sys.argv[1] if len(sys.argv) > 1 else "TEST_LOCAL"
    amt = sys.argv[2] if len(sys.argv) > 2 else "1.00"
    print(create_payment_link(oid, amt, "Test Customer"))
