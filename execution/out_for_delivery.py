"""
out_for_delivery.py
===================
Send a one-time WhatsApp "out for delivery" notification when an order's
ORDER STATUS becomes 🚚 SHIPPED.

Two entry points share one idempotent sender:
  • send_out_for_delivery(order)  — called at-source by filex_reconcile and by the poller
  • main()                        — standalone poll loop (safety net) + --backfill one-shot

Dedup is the Notion checkbox 'Out For Delivery Sent', set only on confirmed send.
Brand routing (number / template / display name) comes from whatchimp_client.OFD_CONFIG.
"""
from __future__ import annotations

import argparse
import logging
import time

import notion_client as nc
import whatchimp_client as wc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("order_bridge.ofd")


def send_out_for_delivery(order: dict) -> bool:
    """Idempotently send the OFD template for one parsed order.

    Returns True only if a message was sent on this call. Skips (returns False)
    when already sent, brand unknown, template pending, or phone missing. On a
    confirmed send it sets the 'Out For Delivery Sent' checkbox.
    """
    order_id = order.get("order_id", "")

    if order.get("out_for_delivery_sent"):
        return False

    cfg = wc.get_ofd_config(order_id)
    if cfg is None:
        log.warning("OFD: unknown brand prefix for order %r — skipping", order_id)
        return False

    if not cfg.get("ofd_template_id"):
        # Future brand with no approved template yet: no-op, leave checkbox unset
        # so it sends automatically once the template_id is filled in OFD_CONFIG.
        log.info("OFD: template pending for %r — skipping", order_id)
        return False

    phone = order.get("phone", "")
    if not phone:
        log.warning("OFD: no phone on order %r — skipping", order_id)
        return False

    sent = wc.send_out_for_delivery_template(phone, order_id, cfg)
    if sent:
        nc.mark_out_for_delivery_sent(order["page_id"])
        log.info("OFD: sent + marked %r", order_id)
    return sent


def poll_once(grace_minutes: int = 2) -> int:
    """One poll pass: send to every shipped-but-unnotified order. Returns count sent."""
    orders = nc.query_shipped_unnotified(grace_minutes=grace_minutes)
    if not orders:
        return 0
    sent = 0
    for order in orders:
        try:
            if send_out_for_delivery(order):
                sent += 1
        except Exception as e:  # never let one bad row kill the loop
            log.error("OFD: error on %r: %s", order.get("order_id"), e)
    return sent


def backfill_mark_shipped() -> int:
    """One-shot: mark every currently-shipped order as already notified WITHOUT
    sending. Run once before first deploy so pre-existing shipped orders don't
    get blasted. Returns count marked."""
    orders = nc.query_shipped_unnotified(grace_minutes=0)
    marked = 0
    for order in orders:
        if nc.mark_out_for_delivery_sent(order["page_id"]):
            marked += 1
            log.info("OFD backfill: marked %r as already notified", order.get("order_id"))
    log.info("OFD backfill: %d orders marked.", marked)
    return marked


def main():
    parser = argparse.ArgumentParser(description="Out-for-delivery WhatsApp notifier.")
    parser.add_argument("--once", action="store_true", help="Run a single poll pass and exit.")
    parser.add_argument("--interval", type=int, default=30,
                        help="Poll interval seconds when looping (default: 30).")
    parser.add_argument("--grace-minutes", type=int, default=2,
                        help="Skip rows whose Last Update is newer than this (default: 2).")
    parser.add_argument("--backfill", action="store_true",
                        help="One-shot: mark all currently-shipped orders as notified, send nothing.")
    args = parser.parse_args()

    if args.backfill:
        backfill_mark_shipped()
        return

    if args.once:
        n = poll_once(grace_minutes=args.grace_minutes)
        log.info("OFD: single pass sent %d message(s).", n)
        return

    log.info("=== OFD notifier loop starting (interval=%ss, grace=%smin) ===",
             args.interval, args.grace_minutes)
    while True:
        try:
            poll_once(grace_minutes=args.grace_minutes)
        except Exception as e:
            log.error("OFD: poll pass failed: %s", e)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
