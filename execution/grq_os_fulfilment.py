"""
grq_os_fulfilment.py
====================
The fulfilment bridge's client for GRQ OS.

order_bridge.py has always found its work by asking Notion which orders are
`CONFIRMED | PROCESSING`. This asks GRQ OS instead, through the same signed
ingest endpoint every other automation here uses.

Four calls, matching the four things the bridge has to say:

    claim(n)                  give me up to n orders, and tick them so a
                              second poller cannot take the same ones
    albums_sent(id, k, msg)   album k landed
    finish(id, total)         all of them landed; the order is Processed
    release(id, why)          the send failed; untick it for the next cycle

Unlike grq_os_ingest, a failure here is NOT silent. That client is
fire-and-forget because a GRQ OS problem must never become a Notion capture
outage. This one is the other way round: if the claim fails the bridge simply
has no work, and if a checkpoint fails the bridge must know, because the
alternative is sending the same album twice.

Inert unless GRQ_OS_URL and GRQ_OS_INGEST_SECRET are both set.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os

import httpx

log = logging.getLogger("order_bridge.grq_os")

GRQ_OS_URL = os.getenv("GRQ_OS_URL", "").strip().rstrip("/")
INGEST_SECRET = os.getenv("GRQ_OS_INGEST_SECRET", "").strip()
BYPASS = os.getenv("GRQ_OS_BYPASS", "").strip()

TIMEOUT = float(os.getenv("GRQ_OS_TIMEOUT", "30"))


def configured() -> bool:
    return bool(GRQ_OS_URL and INGEST_SECRET)


def _post(payload: dict) -> dict | None:
    """Signed POST. Returns the parsed body, or None if anything went wrong."""
    if not configured():
        return None
    raw = json.dumps(payload, ensure_ascii=False)
    # Sign the exact bytes sent: an Arabic customer name 401s otherwise.
    sig = hmac.new(INGEST_SECRET.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "x-grq-signature": sig}
    if BYPASS:
        headers["x-vercel-protection-bypass"] = BYPASS
    try:
        res = httpx.post(
            f"{GRQ_OS_URL}/api/ingest/fulfilment",
            content=raw.encode("utf-8"),
            headers=headers,
            timeout=TIMEOUT,
        )
    except Exception as e:
        log.error("GRQ OS fulfilment %s failed: %s", payload.get("action"), e)
        return None
    if res.status_code != 200:
        log.error(
            "GRQ OS fulfilment %s returned %s: %s",
            payload.get("action"), res.status_code, res.text[:200],
        )
        return None
    return res.json()


def claim(limit: int = 20, worker: str = "order-bridge", stale_minutes: int = 10) -> list[dict]:
    """Orders to send, already ticked so nobody else takes them."""
    body = _post({"action": "claim", "limit": limit, "worker": worker, "stale_minutes": stale_minutes})
    return (body or {}).get("orders") or []


def albums_sent(order_id: str, sent: int, message_id: int | None = None) -> bool:
    return _post({"action": "albums", "order_id": order_id, "sent": sent, "message_id": message_id}) is not None


def finish(order_id: str, total_albums: int | None = None) -> bool:
    return _post({"action": "finish", "order_id": order_id, "total_albums": total_albums}) is not None


def release(order_id: str, reason: str | None = None) -> bool:
    return _post({"action": "release", "order_id": order_id, "reason": reason}) is not None


def to_bridge_order(claimed: dict) -> dict:
    """
    A claimed GRQ OS order in the shape telegram_client.send_order_to_group
    already expects, so the sender itself does not change.

    Images are deliberately left to the sender: it fetches the *variant*
    picture and the size from the Shopify order-status page, which is what
    somebody packing needs to see. GRQ OS's own per-product picture goes in as
    `image_urls`, which the sender uses only when that fetch comes back empty
    - the hand-typed orders, where it is the only picture there is.
    """
    items = claimed.get("items") or []

    parts = []
    for it in items:
        title = (it.get("product_title") or it.get("sku") or "Item").strip()
        variant = (it.get("variant") or "").strip()
        qty = it.get("quantity") or 1
        parts.append(f"{title}{f' ({variant})' if variant else ''} x{qty}")

    # One picture per unit, the same convention the GraphQL path uses.
    fallback_images: list[str] = []
    for it in items:
        url = (it.get("image_url") or "").strip()
        if url:
            fallback_images.extend([url] * int(it.get("quantity") or 1))

    total = claimed.get("total")
    return {
        # The bridge logs and captions by order code.
        "order_id": claimed.get("order_code"),
        # GRQ OS's id. Named page_id because that is what the sender's resume
        # protocol calls its opaque key.
        "page_id": claimed.get("order_id"),
        "grq_os_order_id": claimed.get("order_id"),
        "notion_page_id": claimed.get("notion_page_id"),
        "customer_name": claimed.get("customer") or "",
        "phone": claimed.get("phone") or "",
        "full_address": claimed.get("address") or "",
        "total_aed": "" if total is None or not claimed.get("total_known", True) else str(total),
        "internal_note": claimed.get("internal_note") or "",
        "order_source_url": claimed.get("order_source_url") or "",
        "item_qty": ", ".join(parts),
        "image_urls": fallback_images,
        "albums_sent": int(claimed.get("albums_sent") or 0),
    }
