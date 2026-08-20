"""
stripe_pay.py - create a short Stripe Payment Link (buy.stripe.com) for an order.

Used by the confirmation bot when a customer picks "Pay By Link" at checkout.
We send Stripe ONLY the amount + a neutral reusable product (never item titles),
and carry the CRM order_id on the link via client_reference_id (+ metadata) so
the Stripe webhook (payment_bridge) matches the payment back to the Notion order.

Currency: AED. The restricted key (STRIPE_API_KEY) needs Products / Prices /
PaymentLinks Write. Per order we mint one Price (the amount) against a single
reusable Product (STRIPE_PRODUCT_ID) and one Payment Link -> short buy.stripe.com
URL (matches the links the team sends manually).
"""
from __future__ import annotations

import os
import logging
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

log = logging.getLogger("stripe_pay")

STRIPE_API_BASE = "https://api.stripe.com/v1"
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "")
# Reusable neutral product every order Price points at - keeps the Stripe
# dashboard clean and never leaks item names. Created once; id lives in .env.
# If unset, we fall back to an inline product_data (a product per order).
STRIPE_PRODUCT_ID = os.getenv("STRIPE_PRODUCT_ID", "")
STRIPE_PRODUCT_NAME = os.getenv("STRIPE_PRODUCT_NAME", "Order Payment")


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


def _post(endpoint: str, data: dict) -> dict:
    """POST form-encoded to Stripe; raise StripeError on any non-200/error."""
    if not STRIPE_API_KEY:
        raise StripeError("STRIPE_API_KEY not set in .env")
    try:
        resp = requests.post(
            f"{STRIPE_API_BASE}/{endpoint}", data=data,
            auth=(STRIPE_API_KEY, ""), timeout=20,
        )
        body = resp.json()
    except Exception as e:
        raise StripeError(f"Stripe {endpoint} request failed: {e}")
    if resp.status_code != 200 or not isinstance(body, dict) or body.get("error"):
        err = (body.get("error", {}) or {}).get("message") if isinstance(body, dict) else body
        raise StripeError(f"Stripe {endpoint} failed ({resp.status_code}): {err}")
    return body


def create_payment_link(order_id: str, amount, customer_name: str = "",
                        currency: str = "aed") -> str:
    """Create a short buy.stripe.com Payment Link for an order; return its URL.

    Mints a Price for the amount (against the reusable neutral product), then a
    Payment Link. order_id rides on client_reference_id (+ metadata) so
    payment_bridge can reconcile. No product/item names are ever sent.
    """
    if not order_id:
        raise StripeError("order_id required")
    unit_amount = _to_fils(amount)

    price_data = {"currency": currency, "unit_amount": str(unit_amount)}
    if STRIPE_PRODUCT_ID:
        price_data["product"] = STRIPE_PRODUCT_ID
    else:
        price_data["product_data[name]"] = STRIPE_PRODUCT_NAME
    price = _post("prices", price_data)

    link = _post("payment_links", {
        "line_items[0][price]": price["id"],
        "line_items[0][quantity]": "1",
        "metadata[order_id]": order_id,
        "metadata[customer_name]": customer_name or "",
        "payment_intent_data[metadata][order_id]": order_id,
    })
    url = link.get("url")
    if not url:
        raise StripeError(f"payment_link had no url: {link.get('id')}")
    log.info(f"Stripe payment link for {order_id}: {link['id']} {url} amount={unit_amount} {currency}")
    # Carry order_id so the paid checkout.session has client_reference_id set,
    # which payment_bridge reads to find the Notion order.
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}client_reference_id={quote(order_id, safe='')}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    oid = sys.argv[1] if len(sys.argv) > 1 else "TEST_LOCAL"
    amt = sys.argv[2] if len(sys.argv) > 2 else "210.00"
    print(create_payment_link(oid, amt, "Test Customer"))
