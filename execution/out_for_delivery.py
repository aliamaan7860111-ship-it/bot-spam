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

# Orders we've already tried-and-failed or can't route this process run. Without this,
# a permanent error (rejected template, unknown brand, missing phone) would be re-attempted
# every poll tick forever — hammering WhatChimp and churning subscriber custom fields.
# Keyed by Notion page_id. Cleared on process restart (e.g. after a config redeploy), so a
# fixed template / transient failure gets one fresh attempt per restart.
_skip_this_run: set[str] = set()


def send_out_for_delivery(order: dict) -> bool:
    """Idempotently send the OFD template for one parsed order.

    Returns True only if a message was sent on this call. Skips (returns False)
    when already sent, brand unknown/pending, phone missing, or already failed
    this run. On a confirmed send it sets the 'Out For Delivery Sent' checkbox.
    """
    order_id = order.get("order_id", "")
    page_id = order.get("page_id", "")

    if order.get("out_for_delivery_sent"):
        return False

    # Already handled (and logged) once this run — don't re-hit the API every tick.
    if page_id in _skip_this_run:
        return False

    cfg = wc.get_ofd_config(order_id)
    if cfg is None:
        log.warning("OFD: unknown brand prefix for order %r — skipping (no retry this run)", order_id)
        _skip_this_run.add(page_id)
        return False

    if not cfg.get("ofd_template_id") or cfg.get("pending"):
        # No approved template yet (or brand disabled): no-op, leave checkbox unset so it
        # sends automatically once the template is filled/enabled in OFD_CONFIG (next restart).
        log.info("OFD: template pending for brand of %r — skipping", order_id)
        _skip_this_run.add(page_id)
        return False

    phone = order.get("phone", "")
    if not phone:
        log.warning("OFD: no phone on order %r — skipping (no retry this run)", order_id)
        _skip_this_run.add(page_id)
        return False

    sent = wc.send_out_for_delivery_template(phone, order_id, cfg)
    if sent:
        nc.mark_out_for_delivery_sent(page_id)
        log.info("OFD: sent + marked %r", order_id)
        return True

    # Rejected by WhatChimp (template/locale error, bad number, etc.). The client already
    # logged the reason — give up on this order until the next service restart rather than
    # re-attempting every 30s.
    log.error("OFD: send failed for %r — no retry until service restart", order_id)
    _skip_this_run.add(page_id)
    return False


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
    import error_reporter
    error_reporter.install("grq-ofd", host="gcp-vm")
    main()
