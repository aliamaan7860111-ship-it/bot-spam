"""grq-ac -> GRQ OS: the record of what recovery actually achieved.

Notion remains this bridge's working memory -- the scheduler reads rows back to
fire, re-read and cancel sends. What Notion was never good at is answering the
question the business asks: did the message bring the sale back? That answer
lives in GRQ OS now, in the Abandoned Checkouts section.

Two calls, at the only two moments that matter:

  record_sent()     a recovery message actually WENT OUT. Not at abandonment --
                    a customer who returns inside the thirty-minute delay is
                    cancelled before the send, and should never appear in a
                    report about messages we sent.

  record_outcome()  an order arrived. The discount code on it decides whether
                    the message earned the sale (recovered) or the customer was
                    coming back anyway (order_placed).

Both are best-effort by design: a GRQ OS outage must never stop a recovery from
being sent or an order from being processed. Failures are logged and swallowed.
Delivery is HMAC-signed with the same INGEST_SECRET the order/message/courier
ingests use.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os

import httpx

log = logging.getLogger("grq-ac.grq_os")

TIMEOUT = 10.0


def _endpoint() -> str | None:
    base = (os.getenv("GRQ_OS_URL") or "").strip().rstrip("/")
    return f"{base}/api/ingest/abandoned" if base else None


def _secret() -> str:
    return (os.getenv("GRQ_OS_INGEST_SECRET") or "").strip()


def _sign(raw: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()


async def _post(http: httpx.AsyncClient, payload: dict) -> dict | None:
    """Send one signed event. Returns the parsed response, or None on any failure."""
    url, secret = _endpoint(), _secret()
    if not url or not secret:
        log.debug("GRQ OS not configured (GRQ_OS_URL / GRQ_OS_INGEST_SECRET) - skipping")
        return None

    # The signature covers the exact bytes sent, so serialise once and post that
    # string rather than letting httpx re-encode the dict.
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    try:
        resp = await http.post(
            url,
            content=raw.encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-grq-signature": _sign(raw, secret),
            },
            timeout=TIMEOUT,
        )
    except Exception:
        log.exception("GRQ OS %s post failed (non-fatal)", payload.get("event"))
        return None

    if resp.status_code >= 400:
        log.error("GRQ OS %s rejected: HTTP %s %s",
                  payload.get("event"), resp.status_code, resp.text[:300])
        return None
    try:
        return resp.json()
    except Exception:
        return {}


async def record_sent(
    http: httpx.AsyncClient,
    *,
    brand_slug: str,
    phone: str,
    store_label: str | None = None,
    customer_name: str | None = None,
    cart_value: float | None = None,
    cart_items: str | None = None,
    checkout_id: str | None = None,
    checkout_url: str | None = None,
    abandoned_at: str | None = None,
    recovery_sent_at: str | None = None,
) -> None:
    """Record that a recovery message was sent to this customer for this store.

    Idempotent on the far side: one row per customer per store, ever, so a
    repeat returns the existing row instead of creating a second.
    """
    await _post(http, {
        "event": "sent",
        "brand_slug": brand_slug,
        "phone": phone,
        "store_label": store_label,
        "customer_name": customer_name,
        "cart_value": cart_value,
        "cart_items": cart_items,
        "checkout_id": checkout_id,
        "checkout_url": checkout_url,
        "abandoned_at": abandoned_at,
        "recovery_sent_at": recovery_sent_at,
    })


async def record_outcome(
    http: httpx.AsyncClient,
    *,
    brand_slug: str,
    phone: str,
    order_code: str | None = None,
    order_total: float | None = None,
    discount_codes: list[str] | None = None,
    recovery_code: str | None = None,
    at: str | None = None,
) -> str | None:
    """Resolve this customer's pending recovery against an order they placed.

    Returns 'recovered', 'order_placed', or None when no recovery was pending --
    which is the ordinary case, since most orders follow no recovery at all.
    """
    res = await _post(http, {
        "event": "outcome",
        "brand_slug": brand_slug,
        "phone": phone,
        "order_code": order_code,
        "order_total": order_total,
        "discount_codes": discount_codes or [],
        "recovery_code": recovery_code,
        "at": at,
    })
    return (res or {}).get("status")
