"""
whatsapp_confirmation_bot.py
===========================
Dedicated bot for sending WhatsApp order confirmations across all 5 brands.
Separated from the main order_bridge to allow independent lifecycle management.

Usage:
    python execution/whatsapp_confirmation_bot.py
"""

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


def _is_pay_by_link(method: str) -> bool:
    """True if the CRM PAYMENT value is the 'Pay By Link' checkout method."""
    return "pay by link" in (method or "").strip().lower()


def _send_pay_link(order: dict) -> bool:
    """Generate a Stripe payment link for a 'Pay By Link' order and send the
    payment template instead of the COD confirmation. Returns True on success."""
    order_id = order.get("order_id", "")
    phone = order.get("phone", "")
    name = order.get("customer_name", "Customer")
    try:
        amount = f"{float(str(order.get('total_aed')).replace(',', '').strip()):.2f}"
    except (TypeError, ValueError):
        log.error(f"Pay-by-link {order_id}: unparseable amount {order.get('total_aed')!r} - skipping")
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


async def poll_whatsapp_once() -> int:
    """
    Poll Notion for NEW orders, trigger WhatChimp template delivery.
    """
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
            notion.mark_whatsapp_sent(order["page_id"])
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
