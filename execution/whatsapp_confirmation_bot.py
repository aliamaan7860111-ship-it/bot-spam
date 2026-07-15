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

# Local imports
import notion_client as notion
import whatchimp_client as wc
from order_bridge import BRAND_MAP, get_brand_from_order_id

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("whatsapp_bot")

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))

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
        prefix = order_id[:2]
        
        if prefix in BRAND_MAP and not o.get("whatsapp_sent"):
            # Organic orders (e.g. "LU 231", space between initials and number)
            # are not part of the confirmation flow — skip them.
            if notion.is_organic_order(order_id):
                continue
            # Freshness guard: never send a confirmation for a stale/backlogged order.
            if not notion.is_order_fresh(o.get("created"), MAX_CONFIRM_AGE_HOURS):
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
