"""
Nightly Filex reconciliation.

Two jobs in one run:
  1. Stuck-order alert: orders sitting at 'Label Created' for >24h get
     reported to the fulfillment Telegram group.
  2. Status reconciliation: for every active (non-RTO, dispatched within
     14 days) order, query Filex's ShipmentLastStatus and update Notion
     if the status has changed. Catches webhooks missed during downtime.

Run via systemd timer (filex-reconcile.timer) at 23:00 daily.
"""

import os
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
import requests

load_dotenv()

import notion_client as nc
import filex_status_mapper
import out_for_delivery as ofd
from filex_client import FilexClient

FILEX_USERNAME       = os.getenv("FILEX_USERNAME")
FILEX_PASSWORD       = os.getenv("FILEX_PASSWORD")
FILEX_ACCOUNT_NUMBER = os.getenv("FILEX_ACCOUNT_NUMBER")
FILEX_API_BASE       = os.getenv("FILEX_API_BASE", "https://filex-shipperapi.dispatchex.com")

TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
FULFILLMENT_GROUP_ID = os.getenv("TELEGRAM_FULFILLMENT_GROUP_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("filex_reconcile")


def send_telegram(text: str) -> None:
    """Plain HTTP POST to Telegram Bot API. Avoids importing the full bot."""
    if not (TELEGRAM_BOT_TOKEN and FULFILLMENT_GROUP_ID):
        log.warning("Telegram not configured; skipping alert.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": FULFILLMENT_GROUP_ID,
        "text": text,
        "parse_mode": "Markdown",
    }, timeout=20)


def alert_stuck_orders():
    """Find orders stuck at 'Label Created' for 24h+ and alert."""
    stuck = nc.query_filex_stuck(hours=24)
    if not stuck:
        log.info("No stuck orders.")
        return
    lines = [f"⚠️ *{len(stuck)} order(s) stuck at 'Label Created' for 24h+:*"]
    for order in stuck:
        lines.append(
            f"- `{order['order_id']}` (`{order.get('tracking_number') or '?'}`) "
            f"— {order.get('customer_name') or '?'}"
        )
    lines.append("\nInvestigate with Filex / fulfillment.")
    send_telegram("\n".join(lines))
    log.info("Sent stuck-order alert for %d orders.", len(stuck))


def reconcile_active_orders(cutoff_iso: str | None = None):
    """Poll Filex for status drift and update Notion when found."""
    if cutoff_iso:
        active = nc.query_filex_active_since(cutoff_iso)
    else:
        active = nc.query_filex_active(within_days=14)
    if not active:
        log.info("No active orders to reconcile.")
        return

    client = FilexClient(FILEX_USERNAME, FILEX_PASSWORD, FILEX_ACCOUNT_NUMBER, FILEX_API_BASE)

    # Build tracking_no -> page lookup
    by_tn = {o["tracking_number"]: o for o in active if o.get("tracking_number")}
    if not by_tn:
        log.info("No tracking numbers in active orders.")
        return

    # Batch in groups of 50
    tracking_numbers = list(by_tn.keys())
    for i in range(0, len(tracking_numbers), 50):
        chunk = tracking_numbers[i : i + 50]
        try:
            results = client.get_status(chunk)
        except Exception as e:
            log.error("get_status batch failed: %s", e)
            continue
        for r in results:
            tn = r["tracking_No"]
            order = by_tn.get(tn)
            if not order:
                continue
            mapped = filex_status_mapper.map_status(
                r.get("trackingStatus", ""), order.get("filex_status"),
            )
            if mapped != order.get("filex_status"):
                log.info(
                    "Reconcile drift: %s %s -> %s",
                    order["order_id"], order.get("filex_status"), mapped,
                )
                nc.set_filex_status(order["page_id"], mapped)
                # Also promote to the main ORDER STATUS for Shipped/Delivered/RTO.
                # Pass current ORDER STATUS so we don't stomp downstream manual moves
                # (e.g. ops marked the order as ↩️ RETURNED after verifying the return).
                promoted = filex_status_mapper.order_status_from_filex(
                    mapped, order.get("order_status"),
                )
                if promoted:
                    nc.update_order_status(order["page_id"], promoted)
                    log.info(
                        "  ↳ ORDER STATUS promoted to %r for %s",
                        promoted, order["order_id"],
                    )
                    if promoted == nc.STATUS_SHIPPED:
                        # Fire the out-for-delivery WhatsApp the instant we auto-promote
                        # to SHIPPED. `order` was queried while not-yet-shipped, so its
                        # out_for_delivery_sent is False; send_out_for_delivery's own guard
                        # + the success-set checkbox prevent any double-send vs. the poller.
                        try:
                            ofd.send_out_for_delivery(order)
                        except Exception as e:
                            log.error(
                                "OFD at-source send failed for %s: %s",
                                order["order_id"], e,
                            )
                event_iso = r.get("eventTime")
                if event_iso:
                    # Filex eventTime is naive PKT; tag as +05:00 so stored UTC matches reality.
                    if "T" in event_iso and "+" not in event_iso and "Z" not in event_iso:
                        event_iso = event_iso + "+05:00"
                    nc.set_last_update(order["page_id"], event_iso)


def main():
    parser = argparse.ArgumentParser(description="Filex reconciliation runner.")
    parser.add_argument("--status-only", action="store_true",
                        help="Skip stuck-order alerts; only reconcile statuses (used by polling timer).")
    parser.add_argument("--cutoff-iso", default=None,
                        help="ISO timestamp; only poll orders dispatched at or after this. "
                             "Default: 14 days back.")
    args = parser.parse_args()

    log.info("=== Filex reconcile starting (status_only=%s, cutoff=%s) ===",
             args.status_only, args.cutoff_iso)

    if not args.status_only:
        try:
            alert_stuck_orders()
        except Exception:
            log.exception("alert_stuck_orders crashed")

    try:
        reconcile_active_orders(cutoff_iso=args.cutoff_iso)
    except Exception:
        log.exception("reconcile_active_orders crashed")

    log.info("=== Filex reconcile run done ===")


if __name__ == "__main__":
    main()
