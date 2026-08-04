"""
RPGRQ Leads Bot — orders-CRM two-way sync.

Closes the loop between the sales side (leads DB) and the fulfilment side
(orders CRM). A sales agent enters the "Order ID" on a lead ticket once they
close the customer; this worker polls the orders CRM by that Order ID and
writes the real delivery truth back onto the ticket:

    Delivery Status  <- CRM FILEX STATUS (falls back to ORDER STATUS)
    Delivered        <- FILEX STATUS == "Delivered"
    Order Value      <- CRM TOTAL
    Tracking Number  <- CRM Tracking Number
    Synced At        <- now

That lets a manager group the leads DB by "Agent Assigned" and see, per agent,
how many closes actually DELIVERED (real revenue) vs bounced (RTO/cancelled) —
i.e. which agent is closing and for which it is actually delivering.

Runs as a background asyncio task inside the leads webhook server (no separate
service). Read-side (CRM) uses the synchronous notion_client via a thread; the
write-side (leads DB) uses the async rpgrq_notion client.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from execution import rpgrq_notion as notion
from execution import notion_client as crm

log = logging.getLogger("rpgrq.sync")

# Poll cadence + politeness gap between per-order CRM lookups.
SYNC_INTERVAL_SECONDS = 300
PER_ORDER_DELAY = 0.2

# FILEX STATUS strings that mean the parcel physically arrived. The CRM uses a
# free-form select, so keep this permissive; raw status is always surfaced too.
DELIVERED_MARKERS = {"delivered", "delivered to customer", "completed"}


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_delivery(order: Optional[dict]) -> tuple[Optional[str], bool]:
    """
    Map a CRM order dict to (delivery_status, delivered).
    FILEX STATUS is the courier truth when present; else fall back to ORDER
    STATUS. A missing order (bad/blank Order ID) surfaces as 'Not Found'.
    """
    if not order:
        return "Not Found", False
    filex = (order.get("filex_status") or "").strip()
    order_status = (order.get("order_status") or "").strip()
    delivery_status = filex or order_status or None
    delivered = filex.lower() in DELIVERED_MARKERS
    return delivery_status, delivered


async def sync_once(client: httpx.AsyncClient) -> int:
    """One pass: enrich every ticket that has an Order ID. Returns #updated."""
    tickets = await notion.find_tickets_with_order_id(client)
    if not tickets:
        return 0

    updated = 0
    for ticket in tickets:
        order_id = notion.ticket_order_id(ticket)
        if not order_id:
            continue

        # CRM lookup is synchronous (its own Notion version/headers) — offload.
        try:
            order = await asyncio.to_thread(crm.find_order_by_id, order_id)
        except Exception as e:
            log.warning(f"sync: CRM lookup failed for {order_id!r}: {e}")
            continue

        delivery_status, delivered = _derive_delivery(order)

        # Skip redundant writes: only touch Notion when the status actually moved.
        if delivery_status == notion.ticket_delivery_status(ticket):
            continue

        order_value = order.get("total_aed") if order else None
        if isinstance(order_value, str):
            try:
                order_value = float(order_value.replace(",", "").strip())
            except ValueError:
                order_value = None
        tracking = (order.get("tracking_number") if order else "") or ""

        ok = await notion.set_order_sync(
            client, ticket["id"], delivery_status, delivered,
            order_value, tracking, _utc_iso_now(),
        )
        if ok:
            updated += 1
            log.info(
                f"🔗 sync {order_id}: status={delivery_status!r} "
                f"delivered={delivered} value={order_value}"
            )
        await asyncio.sleep(PER_ORDER_DELAY)

    return updated


async def run_order_sync_loop(
    client: httpx.AsyncClient,
    interval: int = SYNC_INTERVAL_SECONDS,
) -> None:
    """Background loop. Never raises — a CRM/Notion hiccup must not kill the bot."""
    log.info(f"🔗 order-CRM sync loop started (every {interval}s)")
    while True:
        try:
            n = await sync_once(client)
            if n:
                log.info(f"🔗 sync pass complete: {n} ticket(s) updated")
        except Exception as e:
            log.error(f"sync loop pass failed (continuing): {e}")
        await asyncio.sleep(interval)
