"""
whatsapp_confirmation_bot.py
===========================
Dedicated bot for sending WhatsApp order confirmations across all 5 brands.
Separated from the main order_bridge to allow independent lifecycle management.

Usage:
    python execution/whatsapp_confirmation_bot.py
"""

import json
import os
import sys
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# Resolve paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

# Load .env
load_dotenv(PROJECT_ROOT / ".env")

# Only process orders created after this date (from .env)
# Using specific key for WhatsApp
ORDER_CUTOFF_DATE_STR = os.getenv("WHATSAPP_ORDER_CUTOFF_DATE", os.getenv("ORDER_CUTOFF_DATE", "2026-03-24T18:58:00+05:00"))
ORDER_CUTOFF_DATE = datetime.fromisoformat(ORDER_CUTOFF_DATE_STR)

# Never send a confirmation for an order older than this. Prevents stale blasts
# when a backlog of previously-failed orders becomes sendable after a fix.
MAX_CONFIRM_AGE_HOURS = int(os.getenv("WHATSAPP_MAX_CONFIRM_AGE_HOURS", "24"))

# Where the confirmation bot looks for new orders. Notion until this is
# switched on, GRQ OS after. One environment variable, so the cutover can be
# undone by restarting a service.
CONFIRM_FROM_GRQ_OS = os.getenv("CONFIRM_FROM_GRQ_OS", "").strip() in ("1", "true", "yes")


def _parse_floor(v):
    """Parse an absolute ISO cutoff (e.g. '2026-08-03T20:08:00+00:00'). None = disabled."""
    v = (v or "").strip()
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        return None


# Hard absolute floor: never confirm an order created before this timestamp.
# Belt-and-suspenders on top of the rolling MAX_CONFIRM_AGE_HOURS window, to stop
# a backlog blast. Set via WHATSAPP_CONFIRM_NOT_BEFORE (ISO). Empty = disabled.
CONFIRM_NOT_BEFORE = _parse_floor(os.getenv("WHATSAPP_CONFIRM_NOT_BEFORE"))


def _created_after_floor(created, floor):
    """True if the order's created time is at/after floor; False if before or unparseable."""
    try:
        cdt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        cdt = cdt.replace(tzinfo=timezone.utc) if cdt.tzinfo is None else cdt
        return cdt >= floor
    except Exception:
        return False


# Local imports
import notion_client as notion
import grq_os_work as grq
import whatchimp_client as wc
import stripe_pay
from order_bridge import BRAND_MAP, get_brand_from_order_id

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("whatsapp_bot")

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))

# Pilot scope for the "Pay By Link" flow: order-id prefixes whose Shopify
# stores have the "Pay By Link" manual payment method enabled. Amara only for now.
PAY_LINK_BRANDS = {b.strip() for b in os.getenv("PAY_LINK_BRANDS", "AM").split(",") if b.strip()}


# Orders whose pay-by-link can never succeed, so the poller stops reconsidering
# them. An order sits in NEW until it is confirmed, so without this a permanently
# unsendable one is retried every cycle for as long as it stays inside the
# freshness window -- which is how AM4996 (a South African number on a UAE-only
# flow) was attempted 1,499 times across five hours.
PAYLINK_BLOCKED = PROJECT_ROOT / ".tmp" / "paylink_blocked.json"


def _blocked_read() -> dict:
    try:
        with open(PAYLINK_BLOCKED, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _block(order_id: str, reason: str, context: dict) -> None:
    """Record a permanent failure, and raise it once in the Telegram error group.

    Once per order: the file is checked before the work is attempted, so the
    alert does not repeat every poll the way the failure itself used to.
    """
    log.error(f"Pay-by-link {order_id}: {reason} - will not retry")
    try:
        import error_reporter
        error_reporter.report(
            f"Pay-by-link permanently blocked for {order_id}: {reason}",
            error_type="paylink_blocked",
            context=context,
        )
    except Exception:
        pass
    try:
        PAYLINK_BLOCKED.parent.mkdir(parents=True, exist_ok=True)
        data = _blocked_read()
        data[order_id] = {"reason": reason, "at": datetime.now(timezone.utc).isoformat(), **context}
        tmp = PAYLINK_BLOCKED.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, PAYLINK_BLOCKED)
    except Exception as e:
        log.warning(f"could not persist paylink block for {order_id}: {e}")


def _is_pay_by_link(method: str) -> bool:
    """True if the CRM PAYMENT value is the 'Pay By Link' checkout method."""
    return "pay by link" in (method or "").strip().lower()


def _send_pay_link(order: dict) -> bool:
    """Generate a Stripe payment link for a 'Pay By Link' order and send the
    payment template instead of the COD confirmation. Returns True on success.

    Everything that can rule the order out is checked BEFORE the Stripe link is
    minted, because minting first meant every rejected send still left a live
    payment link behind. A send that fails for a reason that might clear -- a
    WhatChimp outage, a template not yet published -- is left to retry, and the
    retry now reuses the link already minted rather than making another.
    """
    order_id = order.get("order_id", "")
    phone = order.get("phone", "")
    name = order.get("customer_name", "Customer")

    if order_id in _blocked_read():
        return False

    try:
        amount = f"{float(str(order.get('total_aed')).replace(',', '').strip()):.2f}"
    except (TypeError, ValueError):
        _block(order_id, f"unparseable amount {order.get('total_aed')!r}",
               {"order_id": order_id, "total_aed": str(order.get("total_aed"))})
        return False

    # Full order_id, not a slice: the resolver handles one-char brands (O, R).
    if wc.get_pay_link_config(order_id) is None:
        _block(order_id, f"no pay-link routing for order id '{order_id}'",
               {"order_id": order_id})
        return False

    if wc.paylink_phone_or_none(phone) is None:
        _block(order_id, f"phone {phone!r} is not a usable number",
               {"order_id": order_id, "phone": phone})
        return False

    try:
        pay_url = stripe_pay.create_payment_link(order_id, amount, name)
    except Exception as e:
        log.error(f"Pay-by-link {order_id}: Stripe link failed: {e}")
        return False
    return wc.send_payment_link_template(
        phone_number=phone,
        customer_name=name,
        order_id=order_id,
        amount=amount,
        pay_url=pay_url,
        brand_prefix=order_id[:2],
    )



def _grq_new_orders() -> list[dict]:
    """
    New orders GRQ OS says nobody has messaged yet.

    The three age guards live on the database side now - the rolling window,
    the absolute floor and the automation line - so they cannot drift apart
    from the ones applied here. The two checks that are about THIS bot rather
    than about the order stay local: which brands it serves, and that organic
    orders are not part of the confirmation flow at all.
    """
    out = []
    for o in grq.claim_confirmation(max_age_hours=MAX_CONFIRM_AGE_HOURS):
        order_id = str(o.get("order_code") or "")
        prefix = order_id[:2] if order_id[:2] in BRAND_MAP else order_id[:1]
        if prefix not in BRAND_MAP:
            continue
        if notion.is_organic_order(order_id):
            continue
        out.append({
            "order_id": order_id,
            "page_id": o.get("order_id"),
            "grq_os_order_id": o.get("order_id"),
            "notion_page_id": o.get("notion_page_id"),
            "phone": o.get("phone") or "",
            "customer_name": o.get("customer") or "Customer",
            "total_aed": "" if o.get("total") is None else str(o.get("total")),
            "payment": o.get("payment_method") or "",
            "order_status": "NEW",
            "whatsapp_sent": False,
            "brand_name": get_brand_from_order_id(order_id),
        })
    return out


def _mark_sent(order: dict) -> None:
    """
    Record the send in whichever systems are live.

    Writing only to GRQ OS while the mirror runs would be undone: the mirror
    reads NEW back off Notion a minute later and the customer is messaged
    twice. Both are written until Notion is retired.
    """
    grq_id = order.get("grq_os_order_id")
    if grq_id and not grq.mark_confirmation_sent(grq_id, template="confirmation"):
        log.error("confirmation sent for %s but GRQ OS would not record it", order.get("order_id"))
    page_id = order.get("notion_page_id") if CONFIRM_FROM_GRQ_OS else order.get("page_id")
    if page_id:
        try:
            notion.mark_whatsapp_sent(page_id)
        except Exception as e:
            log.warning("could not mark Notion for %s (%s)", order.get("order_id"), e)


async def poll_whatsapp_once() -> int:
    """
    Poll Notion for NEW orders, trigger WhatChimp template delivery.
    """
    if CONFIRM_FROM_GRQ_OS:
        return await _send_all(_grq_new_orders())

    # Rolling window: only consider orders from the last MAX_CONFIRM_AGE_HOURS,
    # so an old backlog is never re-blasted after a fix.
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=MAX_CONFIRM_AGE_HOURS)).isoformat()
    orders = notion.query_new_orders(cutoff)
    if not orders:
        return 0

    # Filter for all supported brands
    new_orders = []
    for o in orders:
        order_id = str(o.get("order_id", ""))
        # 2-char prefix first, then 1-char (e.g. Rimal "R" from "R1271")
        prefix = order_id[:2] if order_id[:2] in BRAND_MAP else order_id[:1]

        # Only confirm orders still in NEW status. Without this, an order that
        # was processed while its "Confirmation Sent" checkbox was still False
        # (e.g. during a confirmation outage) gets confirmed late, and
        # mark_whatsapp_sent() clobbers ORDER STATUS back to "Confirmation Sent"
        # — reverting a Processed/labeled order.
        if prefix in BRAND_MAP and o.get("order_status") == "NEW" and not o.get("whatsapp_sent"):
            # Organic orders (e.g. "LU 231", space between initials and number)
            # are not part of the confirmation flow — skip them.
            if notion.is_organic_order(order_id):
                continue
            # Freshness guard: never send a confirmation for a stale/backlogged order.
            if not notion.is_order_fresh(o.get("created"), MAX_CONFIRM_AGE_HOURS):
                continue
            # Hard absolute floor: never confirm orders created before the cutoff.
            if CONFIRM_NOT_BEFORE is not None and not _created_after_floor(o.get("created"), CONFIRM_NOT_BEFORE):
                continue
            o["brand_name"] = get_brand_from_order_id(order_id)
            new_orders.append(o)
    
    return await _send_all(new_orders)


async def _send_all(new_orders: list[dict]) -> int:
    """Send the confirmation for each order. Shared by both work lists."""
    if not new_orders:
        return 0

    log.info(f"🚀 Found {len(new_orders)} new order(s) for WhatsApp confirmation")
    
    processed = 0
    for order in new_orders:
        phone = order.get("phone", "")
        if not phone:
            continue
            
        order_id = order.get("order_id", "")
        prefix = order_id[:2] if order_id[:2] in BRAND_MAP else order_id[:1]

        # "Pay By Link" orders (pilot: PAY_LINK_BRANDS) get a Stripe payment
        # link instead of the COD confirmation template.
        if _is_pay_by_link(order.get("payment")) and prefix in PAY_LINK_BRANDS:
            success = _send_pay_link(order)
        else:
            success = wc.send_template_message(
                phone_number=phone,
                customer_name=order.get("customer_name", "Customer"),
                order_id=order_id,
                total=str(order.get("total_aed") or "0"),
                brand_name=order.get("brand_name", ""),
                brand_prefix=order_id[:2],
            )
        if success:
            _mark_sent(order)
            log.info(f"✅ WhatsApp ({order.get('brand_name')}) sent for {order.get('order_id')}")
            processed += 1
        
        await asyncio.sleep(1.5) # Breath between triggers
        
    return processed

async def main_loop():
    """Continuously poll Notion for new orders to send WhatsApp."""
    log.info("=" * 60)
    log.info("  WhatChimp Multi-Brand Confirmation Bot")
    log.info(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"  Polling every {POLL_INTERVAL_SECONDS * 2}s")
    log.info("=" * 60)

    # Initial connection test
    if not notion.test_connection():
        log.error("❌ Notion connection failed. Check your .env settings.")
        return

    while True:
        try:
            count = await poll_whatsapp_once()
            if count > 0:
                log.info(f"  Processed {count} confirmations this cycle.")
        except Exception as e:
            log.error(f"WhatsApp poller error: {e}")
            
        await asyncio.sleep(POLL_INTERVAL_SECONDS * 2)

if __name__ == "__main__":
    import error_reporter
    error_reporter.install("whatsapp-bot", host="gcp-vm")
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        log.info("WhatsApp bot stopped by user.")
